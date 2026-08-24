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

    % -- 3. can we command it ---------------------------------------------
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

    fprintf('\nOK -- MATLAB can read this recorder and command it.\n');
end
