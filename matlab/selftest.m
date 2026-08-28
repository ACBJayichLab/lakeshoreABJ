function selftest(directory)
%SELFTEST  Check that MATLAB can see and command a running lschart recorder.
%
%   Run this once after installing, with a recorder already running:
%
%       >> selftest('C:\lschart\data')
%
%   It reads the status file, reads a temperature, and issues a `ping` -- the
%   one command that proves the whole command path works without touching an
%   instrument.  Nothing here changes a setpoint or moves a heater.
%
%   If it fails, the message says which half is wrong: reading status.json and
%   having commands applied are separate permissions in the recorder's config,
%   and they fail for different reasons.

    if nargin < 1 || isempty(directory)
        directory = fullfile('..', 'data');
    end
    ls = LakeShore(directory);
    fprintf('status file : %s\n', ls.StatusFile);

    % -- 1. is anything there at all --------------------------------------
    [alive, why] = ls.isAlive();
    if ~alive
        error('selftest:notAlive', ...
              ['the recorder is not alive: %s\n' ...
               'Start it with:  python -m lschart -c CONFIG run'], why);
    end
    s = ls.status();
    fprintf('recorder    : pid %d on %s, cycle %d, %.1f s old\n', ...
            s.pid, s.host, s.cycle, ls.ageOf(s));

    % -- 2. can we read temperatures --------------------------------------
    names = ls.channels();
    fprintf('channels    : %s\n', strjoin(names, ', '));
    temps = ls.temperature();
    fields = fieldnames(temps);
    for i = 1:numel(fields)
        fprintf('  %-24s %10.4f K\n', fields{i}, temps.(fields{i}));
    end
    if all(isnan(struct2array(temps)))
        error('selftest:noReadings', ...
              ['every channel reads NaN, so the recorder is running but its ' ...
               'instrument links are down. Check `links` in status.json.']);
    end

    % -- 3. what is each loop bound to ------------------------------------
    %
    % Schema 2 and later.  Empty against an older recorder, which is not a
    % failure: reading temperatures and commanding still work, and a script
    % that needs the loop table should say so itself.
    for link = ls.links(s)
        rows = ls.loops(link.name, s);
        if isempty(rows)
            fprintf('loops       : %s publishes none (recorder older than ' , link.name);
            fprintf('schema 2, or no loops)\n');
            continue
        end
        fprintf('loops       : %s\n', link.name);
        for i = 1:numel(rows)
            r = rows(i);
            if isempty(r.setpoint_k), sp = NaN; else, sp = r.setpoint_k; end
            fprintf('  loop %d  %-20s %-12s  SP %8.3f K   %s\n', ...
                    r.loop, r.sensor, r.mode, sp, gainsOf(r));
        end
    end

    % -- 4. what are we allowed to ask for --------------------------------
    %
    % Printed rather than checked.  None of these being open is a perfectly
    % good configuration for a recorder somebody only wants MATLAB to read, so
    % a selftest that failed on it would be wrong; what it must not do is let
    % you discover the answer from a refused command in the middle of a sweep.
    c = s.commands;
    fprintf('gates       : heater range %s, analog output %s, PID %s\n', ...
            onOff(c, 'allow_heater_range'), onOff(c, 'allow_analog_output'), ...
            onOff(c, 'allow_pid'));
    if isfield(c, 'source_policy') && c.source_policy
        allowed = true;
        entries = c.sources;
        if isstruct(entries)
            for i = 1:numel(entries)
                if strcmp(entries(i).name, 'matlab')
                    allowed = logical(entries(i).allowed);
                end
            end
        end
        fprintf('source      : this client is labelled "matlab" -- %s\n', ...
                ternary(allowed, 'PERMITTED', 'NOT PERMITTED by ipc.sources'));
    end

    % -- 5. can we command it ---------------------------------------------
    fprintf('ping        : ');
    [ok, message] = ls.ping();
    if ok
        fprintf('%s\n', message);
    else
        fprintf('REFUSED\n');
        error('selftest:noCommands', ...
              ['reading works, but commands do not: %s\n' ...
               'Set `ipc.accept_commands: true` in the recorder''s config ' ...
               'and restart it.'], message);
    end

    % -- 6. are the panic commands reachable -------------------------------
    %
    % Named rather than exercised.  hold() and heatersOff() both change what
    % the cryostat is doing, and a selftest that moved a setpoint or dropped a
    % heater range because somebody ran it to check their install would be a
    % worse failure than any it could detect.
    fprintf('panic       : heatersOff() and hold() are available here; ');
    fprintf('arm() is the way back from a hold\n');
    fprintf('              (not exercised -- both change what the cryostat ');
    fprintf('is doing)\n');

    fprintf('\nOK -- MATLAB can read this recorder and command it.\n');
end


function out = gainsOf(r)
%GAINSOF  One loop's gains, or a note saying why there are none.
    if isempty(r.p) || isnan(r.p)
        out = 'PID not polled (read_pid: false)';
    else
        out = sprintf('P %.1f  I %.1f  D %.1f', r.p, r.i, r.d);
    end
end


function out = onOff(c, field)
    out = 'refused';
    if isfield(c, field) && logical(c.(field))
        out = 'ALLOWED';
    end
end


function out = ternary(cond, a, b)
    if cond, out = a; else, out = b; end
end
