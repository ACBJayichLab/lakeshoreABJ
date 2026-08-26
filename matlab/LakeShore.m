classdef LakeShore < handle
%LAKESHORE  Read and command a running lschart recorder, from MATLAB.
%
%   MATLAB cannot open the instrument itself.  A Windows COM port has exactly
%   one holder, and the recorder holds it -- so if this class opened COM10 the
%   recorder would go blind, and if the recorder started first this would
%   simply fail.  There is no arrangement in which both talk to the box.
%
%   So this talks to the *recorder*, through two files in one directory:
%
%     status.json   the recorder rewrites it every poll cycle: temperatures,
%                   link health, and the outcome of recent commands.
%     commands/     a drop-box.  Write a command here and the recorder picks
%                   it up on its next cycle and reports what happened.
%
%   Nothing is connected to anything.  There is no session to open, nothing to
%   close, and no state to get out of step: MATLAB can be started, stopped and
%   restarted at will while the recorder runs for months, and the recorder
%   never learns that MATLAB exists.
%
%   Example
%   -------
%       ls = LakeShore('C:\lschart\data');
%       ls.isAlive()                    % is the recorder actually running?
%       ls.temperature()                % every channel, as a struct
%       ls.temperature('Sample')        % one channel, in kelvin
%       ls.links()                      % per-instrument health and capability
%       ls.setSetpoint(1, 77.0);        % blocks until the recorder confirms
%
%   Every command method blocks until the recorder acknowledges it, and raises
%   if the command was refused.  That is deliberate: a setpoint that was
%   silently rejected -- because the instrument is configured read-only, say --
%   must not look to a sweep script like a setpoint that was applied.  Use
%   submit() instead if you want to queue one without waiting.
%
%   The recorder must have `ipc.accept_commands: true` in its config for any
%   command to be applied; without it the commands are read and refused, with
%   a message saying so.  Reading status.json never requires that.

    properties (SetAccess = immutable)
        Directory           char    % the recorder's data directory
        StatusFile          char
        CommandDirectory    char
    end

    properties
        %TIMEOUT  Seconds to wait for a command to be acknowledged.
        %   Must comfortably exceed the recorder's poll interval: a command is
        %   applied between cycles, so the wait is at least one cycle long.
        Timeout (1,1) double = 10

        %MAXAGE  Seconds after which status.json is treated as stale.
        %   The recorder rewrites it every cycle, so anything older than a few
        %   cycles means it has stopped, hung, or lost its disk.
        MaxAge (1,1) double = 5
    end

    properties (Access = private)
        Seq (1,1) double = 0    % tie-breaks commands issued in the same ms
    end

    methods
        function obj = LakeShore(directory)
            %LAKESHORE  Point at the directory holding status.json.
            if nargin < 1 || isempty(directory)
                directory = 'data';
            end
            obj.Directory        = char(directory);
            obj.StatusFile       = fullfile(obj.Directory, 'status.json');
            obj.CommandDirectory = fullfile(obj.Directory, 'commands');
        end

        % -- reading -------------------------------------------------------

        function s = status(obj)
            %STATUS  The whole status file, decoded.  Raises if unreadable.
            %
            %   A file being rewritten can briefly be unreadable, which is not
            %   an error worth propagating on the first try -- so a failed read
            %   is retried a few times before giving up.
            for attempt = 1:5
                try
                    s = jsondecode(fileread(obj.StatusFile));
                    return
                catch err
                    last = err;
                    pause(0.05);
                end
            end
            error('LakeShore:noStatus', ...
                  ['cannot read %s (%s).\n' ...
                   'Is the recorder running, and is `ipc.enabled: true` in ' ...
                   'its config file?'], obj.StatusFile, last.message);
        end

        function [tf, why] = isAlive(obj)
            %ISALIVE  True if the recorder is running and its status is current.
            %
            %   Checks freshness rather than the mere existence of the file: a
            %   recorder that died an hour ago leaves a perfectly readable
            %   status file full of hour-old temperatures.
            why = '';
            try
                s = obj.status();
            catch err
                tf = false; why = err.message; return
            end
            age = obj.ageOf(s);
            if isfield(s, 'running') && ~s.running
                tf = false;
                why = 'the recorder stopped cleanly (its last status says so)';
            elseif age > obj.MaxAge
                tf = false;
                why = sprintf(['status.json is %.1f s old (limit %.1f s) -- ' ...
                               'the recorder is not updating it'], age, obj.MaxAge);
            else
                tf = true;
            end
        end

        function age = ageOf(~, s)
            %AGEOF  Seconds since the recorder wrote this status struct.
            %   Wall clock, the only clock the two processes share.
            age = posixtime(datetime('now', 'TimeZone', 'UTC')) - s.t_wall;
            age = max(0, age);
        end

        function out = temperature(obj, channel)
            %TEMPERATURE  Kelvin for one channel, or a struct of all of them.
            %
            %   A channel the recorder marked unusable -- a sensor glitch, a
            %   dead link -- comes back as NaN rather than as a number, so it
            %   cannot be mistaken for a measurement.  With no argument, the
            %   field names are the channel names made valid for MATLAB; use
            %   the one-argument form with the real name to avoid that.
            s = obj.status();
            obj.assertFresh(s);
            chans = obj.channelStruct(s);
            if nargin < 2
                out = struct();
                for i = 1:numel(chans)
                    out.(matlab.lang.makeValidName(chans(i).name)) = ...
                        obj.kelvinOf(chans(i));
                end
                return
            end
            for i = 1:numel(chans)
                if strcmp(chans(i).name, channel)
                    out = obj.kelvinOf(chans(i));
                    return
                end
            end
            error('LakeShore:noChannel', ...
                  'no channel named "%s". This recorder has: %s', ...
                  channel, strjoin({chans.name}, ', '));
        end

        function names = channels(obj)
            %CHANNELS  The channel names, exactly as the recorder logs them.
            chans = obj.channelStruct(obj.status());
            names = {chans.name};
        end

        function items = links(obj, s)
            %LINKS  Per-instrument health and capability, as a struct array.
            %
            %   Fields: name, model, up, consecutive_failures, reconnects,
            %   last_error, writable, loops, heater_outputs, analog_output,
            %   max_output_pct.
            %
            %   `writable` is the driver's own allow_writes policy, and it is
            %   separate from whether the recorder accepts commands at all --
            %   a command needs both.  Reach for this before assuming a write
            %   will land, and to tell a refusal apart from a dead link.
            %
            %   Pass a status struct to read it from a snapshot you already
            %   have, rather than making a second read that could disagree
            %   with the first.
            if nargin < 2, s = obj.status(); end
            items = obj.asStructArray(s, 'links');
        end

        function out = aux(obj, key)
            %AUX  An auxiliary value (setpoints, heater percents) by name.
            %   Names look like "ls336.setpoint1"; aux() lists them all.
            s = obj.status();
            items = obj.asStructArray(s, 'aux');
            if nargin < 2
                out = {items.name}; return
            end
            for i = 1:numel(items)
                if strcmp(items(i).name, key)
                    out = items(i).value;
                    if isempty(out), out = NaN; end
                    return
                end
            end
            error('LakeShore:noAux', 'no auxiliary value named "%s". Have: %s', ...
                  key, strjoin({items.name}, ', '));
        end

        function T = readLog(obj, filename)
            %READLOG  The recorder's CSV as a table, for analysis after a run.
            %
            %   With no argument, reads whichever file the recorder says it is
            %   currently writing.  That file is flushed every sample, so it is
            %   safe to read while the run is still going.
            if nargin < 2 || isempty(filename)
                s = obj.status();
                filename = s.recorder.path;
                if isempty(filename)
                    error('LakeShore:noLog', 'the recorder is not writing a log');
                end
            end
            T = readtable(filename, 'VariableNamingRule', 'preserve');
        end

        % -- commanding ----------------------------------------------------

        function [ok, message, id] = setSetpoint(obj, loop, kelvin)
            %SETSETPOINT  Move an instrument loop's setpoint, in kelvin.
            %
            %   This does NOT turn a heater on.  On a Lake Shore box a setpoint
            %   does nothing at all while the heater range is 0 -- raising the
            %   range is the act that applies power.  See setRange.
            [ok, message, id] = obj.run('setpoint', ...
                struct('loop', loop, 'kelvin', kelvin), nargout);
        end

        function [ok, message, id] = setRamp(obj, loop, rateKPerMin)
            %SETRAMP  Use the instrument's own setpoint ramp, in K/min.
            %
            %   Ramping in the instrument's firmware rather than from MATLAB is
            %   deliberate: the ramp carries on if MATLAB stops, if this laptop
            %   sleeps, or if the recorder is restarted.  A rate of 0 turns
            %   ramping off (it does NOT mean "infinitely fast").
            [ok, message, id] = obj.run('ramp', ...
                struct('loop', loop, 'rate_k_per_min', rateKPerMin), nargout);
        end

        function [ok, message, id] = setRange(obj, output, value)
            %SETRANGE  Heater range: 0 off, 1 low, 2 medium, 3 high.
            %
            %   THIS IS THE COMMAND THAT APPLIES POWER.  The recorder refuses
            %   to raise a range from a file unless its config says
            %   `ipc.allow_heater_range: true`.  Lowering one to 0 is always
            %   allowed -- the safe direction never needs permission.
            [ok, message, id] = obj.run('range', ...
                struct('output', output, 'value', value), nargout);
        end

        function [ok, message, id] = setAnalog(obj, percent)
            %SETANALOG  Drive a 218 analog output, in percent of full scale.
            %
            %   Manual control of a heater that has no loop behind it.  A 218
            %   has no range to raise and no setpoint to be inert: one number
            %   goes out and the heater dissipates accordingly, so THIS IS THE
            %   COMMAND THAT APPLIES POWER on such a box.  The recorder refuses
            %   anything above 0 unless its config says
            %   `ipc.allow_analog_output: true`, and refuses anything above its
            %   own `max_output_pct` regardless.  Commanding 0 is always
            %   allowed.
            %
            %   Know the gain before you type a number.  On the LTSPM3 sample
            %   heater it is about 10 KELVIN PER PERCENT near the operating
            %   point, so a misplaced decimal is worth tens of kelvin.
            %
            %   There is no ramp here.  setAnalog(60) from 0 is one step and
            %   the sample goes there as fast as it can; walk it up yourself if
            %   that matters.
            [ok, message, id] = obj.run('analog', ...
                struct('percent', percent), nargout);
        end

        function [ok, message, id] = heatersOff(obj)
            %HEATERSOFF  Stop heating: every heater this recorder may write to.
            %
            %   33x heater ranges to 0 AND 218 analog outputs to 0%, on every
            %   writable instrument rather than on one.  Boxes the recorder is
            %   configured read-only for are left alone and named in the
            %   message -- on a shared cryostat those are somebody else's.
            [ok, message, id] = obj.run('heaters_off', struct(), nargout);
        end

        function [ok, message, id] = ping(obj)
            %PING  Prove the command path works, without touching an instrument.
            %
            %   isAlive() says the recorder is writing status.  This says it is
            %   also *reading commands*, which is a different question and the
            %   one that matters before a sweep script starts.
            [ok, message, id] = obj.run('ping', struct(), nargout);
        end

        function [id, issuedAt] = submit(obj, kind, args, instrument)
            %SUBMIT  Queue a command without waiting for the acknowledgement.
            %   Returns the id, and when it was issued; pass both to await()
            %   when you want the answer.
            if nargin < 4, instrument = ''; end
            if nargin < 3 || isempty(args), args = struct(); end

            if ~isfolder(obj.CommandDirectory)
                mkdir(obj.CommandDirectory);
            end
            now_s = posixtime(datetime('now', 'TimeZone', 'UTC'));
            issuedAt = now_s;
            % NOT randi.  MATLAB reseeds the default generator identically
            % at every startup, so `randi` hands the first command of every
            % session the same id -- and await() would then match an
            % acknowledgement left in the recorder's ring by the *previous*
            % session and report its outcome as this command's.  That is the
            % precise failure this whole interface exists to avoid: a
            % confident confirmation of something that never happened.
            % (Measured, not theorised: a setSetpoint reported "pong".)
            %
            % tempname is documented to be unique per call and does not touch
            % the user's RNG state, which `rng('shuffle')` would.
            [~, unique_part] = fileparts(tempname);
            unique_part = lower(regexprep(unique_part, '[^0-9a-z]', ''));
            id = unique_part(max(1, numel(unique_part) - 11):end);

            payload = args;
            payload.id         = id;
            payload.kind       = kind;
            payload.issued_at  = now_s;
            payload.instrument = instrument;
            payload.source     = 'matlab';

            % The filename orders the spool: milliseconds, then a per-client
            % sequence.  The sequence is not decoration -- Windows resolves the
            % clock to about 15 ms, so two commands issued back to back share a
            % millisecond, and without the tie-break they would be applied in
            % whichever order their random ids happened to sort.
            obj.Seq = mod(obj.Seq + 1, 10000);
            stem = sprintf('%013.0f-%04d-%s', floor(now_s * 1000), obj.Seq, id);
            tmp   = fullfile(obj.CommandDirectory, [stem '.json.tmp']);
            final = fullfile(obj.CommandDirectory, [stem '.json']);

            fid = fopen(tmp, 'w');
            if fid < 0
                error('LakeShore:cannotQueue', ...
                      'cannot write to %s. Does the directory exist and is it writable?', ...
                      obj.CommandDirectory);
            end
            closer = onCleanup(@() fclose(fid));   % also closes if fwrite throws
            fwrite(fid, jsonencode(payload), 'char');
            clear closer;

            % The recorder only ever looks at *.json, so it cannot see this
            % file until the rename completes.  movefile within one directory
            % is a rename on both platforms -- and if it ever were not, the
            % worst case is still safe rather than wrong: a partially written
            % JSON object has lost its closing brace, so it can only fail to
            % parse, and the recorder answers "unreadable command file".  It
            % can never parse into a different command.
            %
            % Deliberately no java.io.File here.  That would be a guaranteed
            % atomic rename, but it drags the JVM in -- which prints warnings
            % under `matlab -batch` and does not exist at all under `-nojvm`,
            % for a guarantee the paragraph above says is not needed.
            movefile(tmp, final, 'f');
        end

        function [ok, message] = await(obj, id, since)
            %AWAIT  Wait for one command's acknowledgement.
            %
            %   Waits for the *id*, not merely for something to happen: several
            %   clients may be commanding this recorder, and "a command was
            %   applied" is not the same statement as "yours was".
            %
            %   `since` is the time the command was issued.  An acknowledgement
            %   stamped before that cannot be the answer to it, whatever its id
            %   says -- which makes a repeated id harmless rather than
            %   silently wrong.  Belt to the id's braces.
            if nargin < 3 || isempty(since), since = -Inf; end
            deadline = tic;
            while toc(deadline) < obj.Timeout
                try
                    s = obj.status();
                catch
                    pause(0.1); continue
                end
                acks = obj.asStructArray(s, 'recent', 'commands');
                for i = 1:numel(acks)
                    if strcmp(acks(i).id, id) && acks(i).t_wall >= since - 1
                        ok = logical(acks(i).ok);
                        message = acks(i).message;
                        return
                    end
                end
                pause(0.1);
            end
            ok = false;
            message = sprintf(['no acknowledgement within %.1f s. The recorder ' ...
                'may be stopped, or may not be reading commands ' ...
                '(ipc.accept_commands). The command may still be applied.'], ...
                obj.Timeout);
        end
    end

    methods (Access = private)
        function [ok, message, id] = run(obj, kind, args, nout)
            %RUN  Queue a command, wait for it, and decide how to report it.
            %
            %   Called for its effect -- `ls.setSetpoint(1, 77)` -- a refusal
            %   raises, because a sweep script must not carry on believing a
            %   setpoint was applied when it was not.  Called for its value --
            %   `[ok, msg] = ls.setSetpoint(1, 77)` -- the caller has said it
            %   intends to inspect the outcome, so it is returned instead.
            [id, issuedAt] = obj.submit(kind, args);
            [ok, message] = obj.await(id, issuedAt);
            if nout == 0 && ~ok
                error('LakeShore:refused', '%s was refused: %s', kind, message);
            end
        end

        function assertFresh(obj, s)
            age = obj.ageOf(s);
            if age > obj.MaxAge
                error('LakeShore:stale', ...
                      ['status.json is %.1f s old (limit %.1f s): these ' ...
                       'temperatures are not current. The recorder has ' ...
                       'stopped or hung.'], age, obj.MaxAge);
            end
        end

        function k = kelvinOf(~, chan)
            % jsondecode turns JSON null into [].  An unusable reading must not
            % come back as a number a script would happily average.
            if ~chan.usable || isempty(chan.kelvin)
                k = NaN;
            else
                k = chan.kelvin;
            end
        end

        function chans = channelStruct(obj, s)
            chans = obj.asStructArray(s, 'channels');
            if isempty(chans)
                error('LakeShore:noChannels', ...
                      ['the recorder reports no channels at all -- every ' ...
                       'instrument link is probably down. Check `errors` and ' ...
                       '`links` in %s'], obj.StatusFile);
            end
        end

        function items = asStructArray(~, s, field, parent)
            % jsondecode gives a struct array for a uniform JSON array, a cell
            % array if the elements ever differ, and [] for an empty one.  The
            % recorder writes uniform elements deliberately, but normalising
            % here means a future field cannot turn every caller into a bug.
            if nargin >= 4
                if ~isfield(s, parent), items = struct([]); return, end
                s = s.(parent);
            end
            if ~isfield(s, field) || isempty(s.(field))
                items = struct([]); return
            end
            raw = s.(field);
            if iscell(raw)
                items = [raw{:}];
            else
                items = raw;
            end
        end
    end
end
