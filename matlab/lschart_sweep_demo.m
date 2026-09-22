function results = lschart_sweep_demo(directory, temperatures, measureFcn)
%LSCHART_SWEEP_DEMO  Measure at a series of temperatures, holding at each.
%
%   THIS ONE MOVES THE CRYOSTAT.  `lschart_demo` reads and touches nothing;
%   this commands the software loop to each temperature in turn, waits for it
%   to settle, and calls your measurement in the hold.  It is the shape of
%   answer 6 in docs/ltspm3/requirements.md -- "programmatic sweeps where I am
%   going to measure at a series of temperatures (with holds during
%   measurement)" -- and it is meant to be copied and edited, not called.
%
%       results = lschart_sweep_demo('C:\lschart\data', [110 115 120], ...
%                                    @(ls, T) myExperiment(ls, T));
%
%   `measureFcn` is handed the LSChartRecorder object and the temperature that was
%   asked for, and whatever it returns is collected into `results`.  Omit it
%   and the sweep records the cryostat's own state at each point, which is
%   enough to see that the sweep worked and nothing else.
%
%   WHAT IT REFUSES TO START ON.  A loop that is not armed, a recorder that is
%   not running, a recorder with no software loop: each is reported by name
%   rather than discovered three temperatures in.  Arming is deliberately not
%   done here -- arm() applies power, and the decision to close the loop
%   belongs to whoever is at the cryostat, not to a script that was run.
%
%   WHAT IT DOES ON A POINT THAT WILL NOT SETTLE.  It stops, writes why into
%   the log's Notes column, and returns what it has.  It does NOT call
%   heatersOff() or hold(): the supervisor is what stops a cryostat that is in
%   trouble, and a sweep script second-guessing it would be one more thing
%   that can move the heater for a reason nobody can reconstruct afterwards.
%   The loop is left holding its last setpoint, which is where it already was.
%
%   See also LSCHARTRECORDER/SETTEMPERATURE, LSCHARTRECORDER/WAITUNTILSTEADY.

if nargin < 2 || isempty(temperatures)
    error('lschart_sweep_demo:noTemperatures', ...
          'give a list of temperatures, e.g. [110 115 120]');
end
if nargin < 3 || isempty(measureFcn)
    measureFcn = @defaultMeasurement;
end

ls = LSChartRecorder(directory);

% -- the guards, all before anything moves --------------------------------
[alive, why] = ls.isAlive();
if ~alive
    error('lschart_sweep_demo:notRunning', ...
          'the recorder is not running: %s', why);
end
c = ls.control();
if isempty(c)
    error('lschart_sweep_demo:noSoftwareLoop', ...
          ['this recorder has no software loop -- it records and does not ' ...
           'steer. A sweep needs `python -m ltspm3 -c CONFIG run --arm`.']);
end
if ~strcmp(c.mode, 'pid')
    error('lschart_sweep_demo:notArmed', ...
          ['the software loop is %s, not driving the heater. arm() closes ' ...
           'it -- and that is the command that applies power, so somebody ' ...
           'should mean it.'], c.mode);
end
if isempty(c.hold_error_k)
    error('lschart_sweep_demo:noSettleRule', ...
          ['this recorder publishes no settle rule, so there is no way to ' ...
           'tell when a point has arrived. A recorder started before ' ...
           '2026-09-22 does not publish it and has to be restarted to.']);
end
fprintf('loop is %s at %.4f K, settle rule: within %.3f K for %.0f s\n', ...
        c.state, c.setpoint_k, c.hold_error_k, c.hold_settle_s);

% Bracket the run in the log.  If this script is interrupted -- Ctrl-C, an
% error in the measurement, MATLAB closing -- the closing note is still
% written, so the log says the sweep stopped rather than going quiet.
ls.note(sprintf('MATLAB sweep starting: %s', mat2str(temperatures, 6)));
closing = onCleanup(@() ls.note('MATLAB sweep ended'));

results = struct('requested_k', {}, 'settled', {}, 'temperature_k', {}, ...
                 'error_k', {}, 'output_pct', {}, 'waited_s', {}, ...
                 'verdict', {}, 'measurement', {});

for k = 1:numel(temperatures)
    T = temperatures(k);
    fprintf('\n[%d/%d] -> %.4f K\n', k, numel(temperatures), T);
    ls.setTemperature(T);

    [steady, info] = ls.waitUntilSteady(T);
    fprintf('  %s after %.0f s: %.4f K, error %.4f K, output %.3f %%\n', ...
            statusWord(steady), info.waited_s, info.temperature_k, ...
            info.error_k, info.output_pct);
    if ~steady
        ls.note(sprintf('MATLAB sweep STOPPED at %.4f K: %s', T, info.why));
        warning('lschart_sweep_demo:stopped', ...
                'stopping at %.4f K: %s', T, info.why);
        return
    end

    % The loop can be holding its setpoint perfectly while the cryostat
    % underneath it is not the one the model describes.  That is the
    % monitor's question and not the loop's, so it is asked separately --
    % and it is recorded beside the measurement rather than acted on, because
    % a warn is a thing to know about a data point, not a reason to stop.
    verdict = 'no monitor';
    p = ls.plant();
    if ~isempty(p) && ~p.stale
        verdict = p.verdict;
    end
    if ~strcmp(verdict, 'typical')
        fprintf('  monitor says: %s\n', verdict);
    end

    ls.note(sprintf('MATLAB measuring at %.4f K', T));
    measurement = measureFcn(ls, T);

    results(end+1) = struct('requested_k', T, 'settled', steady, ...
        'temperature_k', info.temperature_k, 'error_k', info.error_k, ...
        'output_pct', info.output_pct, 'waited_s', info.waited_s, ...
        'verdict', verdict, 'measurement', {measurement}); %#ok<AGROW>
end

fprintf('\nsweep finished: %d of %d points\n', numel(results), numel(temperatures));
end


function out = defaultMeasurement(ls, ~)
%DEFAULTMEASUREMENT  What a sweep records when you have not said what to.
%   Every thermometer at the moment of the hold.  Replace this with the
%   experiment; the sweep does not care what it returns.
out = ls.temperature();
end


function word = statusWord(steady)
if steady, word = 'settled'; else, word = 'DID NOT SETTLE'; end
end
