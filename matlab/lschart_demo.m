function lschart_demo(directory)
%LSCHART_DEMO  A worked example of driving a running lschart recorder.
%
%   lschart_demo                      % ../data, relative to this folder
%   lschart_demo('C:\lschart\data')
%
%   This is a DEMONSTRATION -- the shape a real experiment script takes.  It
%   is not a test: for a pass/fail check that MATLAB can see and command a
%   recorder, run SELFTEST, which is that file's whole job.  This one
%   deliberately does not repeat it.
%
%   Everything here goes through the LakeShore class, and that is the point.
%   The file protocol -- the command spool, atomic writes, the
%   acknowledgement handshake, staleness -- is implemented once, in
%   LakeShore.m.  A user script that reimplements any of it has bought
%   itself a second copy to keep in step with the recorder, and the failures
%   are quiet ones: read the wrong field of status.json and nothing errors,
%   you simply never match, and a command that WAS applied looks like a
%   timeout.
%
%   Nothing below moves a heater.  See "5." for why that is a property of
%   the recorder's config and not of this script's good manners.

    if nargin < 1 || isempty(directory)
        directory = fullfile(fileparts(mfilename('fullpath')), '..', 'data');
    end
    ls = LakeShore(directory);   % shadows the `ls` builtin, as selftest does

    % -- 0. Never trust a status file you have not checked ------------------
    % status.json outlives the process that wrote it, so a recorder killed an
    % hour ago still leaves a file full of entirely plausible temperatures.
    % isAlive is what separates "current" from "merely present"; every read
    % below is guarded by it.
    [alive, why] = ls.isAlive();
    if ~alive
        fprintf('Recorder not usable: %s\n', why);
        fprintf('Start one with:  python -m lschart -c CONFIG run\n');
        return
    end
    s = ls.status();
    fprintf('Recorder pid %d, cycle %d, polling every %g s.\n\n', ...
            s.pid, s.cycle, s.interval_s);

    % -- 1. Read every channel at once -------------------------------------
    % The no-argument form returns a struct keyed by channel name, so the
    % names are pushed through makeValidName: "Stage 2" becomes Stage2.
    temps = ls.temperature();
    disp('All channels:');
    disp(temps);

    % -- 2. Read one channel by its real name ------------------------------
    % Prefer this in a script you intend to keep.  It uses the name exactly
    % as the recorder logs it, so it does not quietly depend on how MATLAB
    % happened to mangle a label with a space in it.
    names = ls.channels();
    fprintf('Reading "%s" by name: %.4f K\n\n', names{1}, ls.temperature(names{1}));

    % -- 3. Sample over time -----------------------------------------------
    % The shape of an actual measurement loop.  Poll no faster than the
    % recorder does -- it republishes status.json once per cycle, so a
    % tighter loop just re-reads the same numbers and calls them data.
    n = 5;
    fprintf('Sampling %d points at the recorder''s own %g s cadence:\n', n, s.interval_s);
    t = NaN(n, 1);
    k = NaN(n, 1);
    t0 = tic;
    for i = 1:n
        k(i) = ls.temperature(names{1});
        t(i) = toc(t0);
        fprintf('  t=%5.1f s   %s = %8.4f K\n', t(i), names{1}, k(i));
        if i < n, pause(s.interval_s); end
    end
    fprintf('  spread over %.1f s: %.4f K\n\n', t(end), max(k) - min(k));

    % -- 4. Setpoints and heater outputs -----------------------------------
    % Auxiliary values are whatever the driver was configured to read back.
    % aux() with no argument lists the names; a NaN means the recorder has
    % the name but no current value for it.
    disp('Auxiliary values:');
    for name = string(ls.aux())
        fprintf('  %-24s %g\n', name, ls.aux(char(name)));
    end
    fprintf('\n');

    % -- 5. What this recorder would let you do ----------------------------
    % Read this before assuming a command will land.  Commanding is gated
    % independently of reading, and a heater range is gated independently of
    % a setpoint, because they are different kinds of mistake.  A refusal
    % here is a correctly configured recorder, not a fault.
    c = s.commands;
    fprintf('Commands accepted      : %s\n', mat2str(logical(c.accepted)));
    fprintf('Heater range permitted : %s\n', mat2str(logical(c.allow_heater_range)));
    fprintf('Analog out permitted   : %s\n', mat2str(logical(c.allow_analog_output)));
    L = ls.links(s);
    for i = 1:numel(L)
        fprintf('  %s: up=%s writable=%s, loops [%s]\n', L(i).name, ...
                mat2str(logical(L(i).up)), mat2str(logical(L(i).writable)), ...
                strjoin(cellstr(string(L(i).loops(:))), ' '));
    end
    fprintf('\n');

    % A setpoint is inert while its heater range is 0, so the write below is
    % safe on a cold, idle cryostat and is NOT safe on one whose range has been
    % raised.  It stays commented out on purpose: uncomment it when you have
    % read docs/recorder/file-interface.md and mean it.
    %
    %   [ok, msg] = ls.setSetpoint(1, 77.0);
    %   fprintf('setpoint: ok=%d  %s\n', ok, msg);

    % -- 6. The recorded data ----------------------------------------------
    % The CSV is flushed every sample, so it can be read mid-run.  This is
    % where analysis actually starts -- status.json is a snapshot, the log is
    % the record.
    T = ls.readLog();
    fprintf('Log %s: %d rows x %d columns\n', s.recorder.path, height(T), width(T));
    disp(T(max(1, height(T) - 2):end, 1:min(5, width(T))));
end
