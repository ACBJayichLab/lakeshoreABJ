# Talking to the recorder from MATLAB

`LSChartRecorder.m` lets a MATLAB script read temperatures from a running `lschart`
recorder and command its instruments' setpoints.

## Why MATLAB does not open the instrument

It cannot. A Windows COM port has exactly one holder, and the recorder holds
it — so a MATLAB script that opened `COM10` would either fail, or take the port
and leave the recorder blind. There is no arrangement in which both talk to the
box.

So MATLAB talks to the **recorder** instead, through two files:

| | |
|---|---|
| `status.json` | rewritten by the recorder every cycle: temperatures, link health, and the outcome of recent commands |
| `commands/` | a drop-box; write a command here and the recorder applies it on its next cycle |

Nothing is connected to anything. There is no session to open, nothing to
close, and no state to get out of step: MATLAB can be started, stopped and
restarted at will while the recorder runs for months, and the recorder never
learns that MATLAB exists. That is the strongest available form of "do not
crash if the client does".

## Setup

1. Add this folder to the MATLAB path:

   ```matlab
   addpath('C:\lschart\matlab')
   ```

2. In the recorder's config file, switch the interface on:

   ```yaml
   ipc:
     enabled: true              # writes status.json — needed to READ
     accept_commands: true      # reads commands/ — needed to WRITE
   ```

   Reading and commanding are separate permissions, and `accept_commands` is
   off by default. Commanding *also* needs `allow_writes: true` on the
   instrument itself — see "What can be refused" below.

3. Start the recorder, then check MATLAB can see it:

   ```matlab
   selftest('C:\lschart\data')
   ```

   That reads the status file and issues a `ping`, which is the one command
   that proves the whole command path works without touching an instrument.

4. For a worked example rather than a pass/fail check, run the demo:

   ```matlab
   lschart_demo('C:\lschart\data')
   ```

   It is the shape a real experiment script takes — guard on `isAlive`, read
   by channel name, sample at the recorder's cadence, then pull the log as a
   table. It moves nothing. `selftest` answers "is this installed right?";
   `lschart_demo` answers "how do I write my own script?".

## Use

```matlab
ls = LSChartRecorder('C:\lschart\data');

ls.isAlive()                 % is the recorder actually running and current?
ls.channels()                % whatever the config names them, e.g. {'Sample', 'Coldplate'}
ls.temperature()             % all of them, as a struct
ls.temperature('Sample')     % one, in kelvin
ls.aux('ls336.setpoint1')    % setpoints, heater percents
ls.links()                   % per-instrument health: up, writable, capability
ls.loops('ls336')            % the loop table: sensor, mode, setpoint, output
ls.control()                 % the SOFTWARE loop's state, or [] if there is none
ls.plant()                   % the monitor's verdict on the cryostat
T = ls.readLog();            % the CSV as a table, safe to read mid-run

ls.setTemperature(120);      % the SOFTWARE loop — see "A sweep" below
ls.waitUntilSteady(120);     % ...and block until the cryostat has settled

ls.setSetpoint(1, 77.0);     % blocks until the recorder confirms
ls.setRamp(1, 2.5);          % K/min, run by the instrument's own firmware
ls.setRange(1, 0);           % heater range — 0 is off
ls.setAnalog(5.0);           % 218 analog output percent — see the warning below
ls.setPID(1, 50, 20, 0);     % the INSTRUMENT's own gains, all three together
ls.setSource('lschart-gui', false);   % have the recorder ignore the viewer
ls.heatersOff();             % everything the recorder may write to, to zero
ls.hold();                   % every loop stopped where it is
ls.ack();                    % clear a software loop's fault lockout
ls.arm();                    % close the software loop again
```

`heatersOff()` also **disarms a software loop**, before it zeroes anything:
writing zero once to an output something else drives every cycle does not turn
it off. `ack()` and `arm()` are the two halves of recovering from a fault
lockout, in that order — `ack()` clears the latch and leaves the loop
disarmed, deliberately, because the latch exists to make somebody look at the
cryostat.

`loops()` is the one worth knowing about if you are scripting a loop rather
than a channel. It answers **which sensor a loop reads** with the
*instrument's* own answer (`OUTMODE?`), so there is no map of loops to sensors
in your script to go stale — and `sensor` is the same string `temperature()`
uses, so the two join directly:

```matlab
r = ls.loops('ls336');
here = ls.temperature();
for i = 1:numel(r)
    fprintf('loop %d (%s): %.3f K, going to %.3f K\n', ...
            r(i).loop, r(i).sensor, ...
            here.(matlab.lang.makeValidName(r(i).sensor)), r(i).setpoint_k);
end
```

Every field is present on every entry, with `[]` where the recorder has
nothing to say — never a plausible zero. Against a recorder older than schema
2 it comes back empty, which is not an error: that recorder did not publish a
loop table at all.

Every command method blocks until the recorder acknowledges it, and **raises
if the command was refused**. That is deliberate: a setpoint that was silently
rejected must not look to a sweep script like one that was applied. Ask for
outputs instead when you want to inspect the outcome yourself:

```matlab
[ok, message] = ls.setSetpoint(1, 77.0);
```

Use `submit` and `await` to queue without blocking.

## A sweep: command a temperature, wait, measure, repeat

This is the shape of the thing, and it is meant to be this short:

```matlab
ls = LSChartRecorder('C:\lschart\data');
for T = [110 115 120]
    ls.setTemperature(T);        % the software loop, not a 33x setpoint
    ls.waitUntilSteady(T);       % blocks; raises if it never settles
    myExperiment(T);             % the hold is yours
end
```

`lschart_sweep_demo.m` is that with the guards around it — it refuses to start
on a loop that is not armed, brackets the run in the log's Notes column, asks
the monitor for a verdict at each point, and stops rather than measuring at a
temperature that never arrived.

**`setTemperature` is not `setSetpoint`.** `setSetpoint` moves an *instrument*
loop's setpoint, and on a 33x that is inert until somebody raises a heater
range. `setTemperature` moves the *software* loop's, and an armed software loop
is already driving the 218's analog output — where the percentage **is** the
power and there is no inert half. So it applies power on the recorder's next
cycle, and the recorder gates it exactly as it gates `arm()`:
`ipc.allow_analog_output: true`. It does not arm anything: a setpoint handed to
a loop that is not armed is remembered and drives nothing.

**What "steady" means is the recorder's verdict, `control.settled`.** Steady is
[Jeff's settle gate](../docs/ltspm3/requirements.md) — "within 50 mK and
staying" — and the recorder decides it, once: the loop is tracking, the
trajectory has arrived, and the error has been inside `hold_error_k` for
`hold_settle_s`, and once true it stays true through anything shorter than
`unsettle_s` outside the band — so a measurement of many minutes started on it
can rely on it. MATLAB counts nothing of its own, and the viewer shows the
same field, so the two cannot disagree. It is also the moment the loop's gains
switch to hold. The rule behind it comes back in `info`, so a script can log
the rule it waited for rather than a rule it believed:

```matlab
[steady, info] = ls.waitUntilSteady(120);
fprintf('%.4f K after %.0f s (waited for within %.3f K for %.0f s)\n', ...
        info.temperature_k, info.waited_s, info.hold_error_k, info.hold_settle_s);
```

**It does not wait out a loop that will never arrive.** A fault ramp-down, a
lockout, a crash, a loop left idle or disengaged, a recorder that stopped
writing, or a loop aimed at a different temperature each return at once and say
which — waiting half an hour to be told nothing happened is how a night gets
wasted. A `frozen` loop is the one exception and is waited through: the
supervisor declining to act on a reading it does not believe is usually seconds
long and clears itself, and it is reported in `info.frozen_s`.

Called with no output it **raises** if the cryostat did not settle, which is
what a sweep wants — measuring at a temperature that never arrived is worse
than stopping. Ask for `[steady, info]` to handle it yourself.

**Pass the temperature you commanded.** The status file written on the cycle
a command lands still carries the verdict from before it, and checking the loop
is aimed at that temperature is what keeps a stale `settled` from answering for
the new setpoint. `control.phase` is reported in `info.phase` and gates
nothing: it goes to hold with `settled`, and then stays there through small
excursions that `settled` does not.

A recorder started before 2026-09-23 does not publish `settled`, and
`waitUntilSteady` raises `LSChartRecorder:noSettledVerdict` rather than
re-deriving it. Restart the recorder.

`control()` is the whole loop state behind all of this, and `plant()` is the
monitor's verdict on the cryostat — a separate process, so it can be absent or
stale while the recorder is perfectly healthy, and both are reported rather
than hidden. The loop can be holding its setpoint beautifully while the
cryostat underneath it is not the one the model describes; that is what
`plant()` judges and `control()` does not.

## Things worth knowing

**A setpoint does not turn a heater on.** On a Lake Shore box a setpoint does
nothing at all while that output's heater range is 0. Raising the range is the
act that applies power, and nothing does it as a side effect of anything else.

**`setAnalog` is the exception, and it is a big one.** A 218 has no loop, no
range and no setpoint — its analog output is driven in manual mode, so the
percentage *is* the power and there is no inert half to the command. Two
consequences:

- **Know the gain before you type a number.** On the LTSPM3 sample heater it is
  about **10 kelvin per percent** near the operating point. A misplaced decimal
  is worth tens of kelvin, which is why the recorder carries a `max_output_pct`
  ceiling and refuses anything above it.
- **There is no ramp.** `setAnalog(60)` from 0 is a single step and the sample
  goes there as fast as it can. Walking up in stages is your discipline, not
  the software's.

Like `setRange`, it needs the recorder's own opt-in — `ipc.allow_analog_output:
true` — for **any** value, `setAnalog(0)` included. To stop the heater without
that permission, use `heatersOff()`, which is exempt.

**Ramp in the instrument, not in MATLAB.** `setRamp` uses the box's own
firmware ramp, which carries on if MATLAB stops, if the laptop sleeps, or if
the recorder is restarted. A loop of `setSetpoint` calls from MATLAB does not.

**Commands expire after ~30 seconds.** If the recorder is not running, a
command sits in the spool until it goes stale and is then refused rather than
applied. Without that, a recorder that was down for an hour would come back
and replay an hour of queued setpoints into a live cryostat — and the last one
would even be correct, which is what makes it dangerous. The hazard is the
traversal, not the destination.

**An unusable reading comes back as `NaN`, not as a number.** If the recorder
rejected a sample — a sensor glitch, a dead link — `temperature()` gives NaN so
it cannot be quietly averaged into a result.

**Channel names with spaces.** `ls.temperature()` returns a struct, and MATLAB
struct fields cannot contain spaces, so "Rad Shield" becomes `RadShield` there.
`ls.temperature('Rad Shield')` takes the real name and is the form to prefer.

## What can be refused, and why

A command from MATLAB passes exactly the same interlocks as one typed at the
recorder's own command line. The file interface is not a back door.

The one exception runs the other way: `heatersOff()` and `hold()` are panic
commands, and panic commands are exempt from the per-client source policy and
from the two power gates. `arm()` and `ack()` are **not** among them — they are
the two steps back toward driving the heater, and the exemption is for stopping
only. MATLAB gets that exemption for the same reason the viewer does —
the recorder sees the command *kind*, not who sent it, and an automated abort
is a large part of why the command exists. It is still refused by
`ipc.accept_commands`, by `allow_writes` and by `transport.read_only`.

`note()` is the one command that writes to the **log** rather than to a box.
It needs no power gate, but it is not a panic kind either: a client the source
policy has muted stays muted for it. Use it to bracket a script —
`ls.note('starting the 40 K -> 120 K ladder')` — as well as to record what was
done at the cryostat. Nothing else in the recorder records that, and the two
events of September 2026 both had to be reconstructed from the curves because
the column was empty.

| Refusal | Fix |
|---|---|
| `this recorder is not accepting commands` | `ipc.accept_commands: true` in the recorder's config |
| `... is configured read-only` | `allow_writes: true` on that instrument |
| `changing a heater range is not accepted ...` | `ipc.allow_heater_range: true`, if a remote client really should be able to move a heater. Needed for 0 as well — use `heatersOff()` or `hold()` to stop without it |
| `retuning a loop is not accepted from a file ...` | `ipc.allow_pid: true`. Gains apply no power, but they change how the loop behaves for the rest of the run |
| `commands from 'matlab' are not accepted by this recorder's configuration` | `ipc.sources` names which clients may ask at all. Needs a config edit and a restart |
| `commands from 'matlab' are currently switched off in ... sources.json` | somebody muted this client. `ls.setSource('matlab', true)` undoes it — that command is exempt from the policy it edits, so you are never locked out of your own un-mute. Reading was never affected |
| `issued N s ago, older than the 30 s limit` | the recorder was not running when the command was queued |
| `several controllers are configured` | say which: `ls.submit('setpoint', struct('loop',1,'kelvin',77), 'ls336')` |

## Testing without MATLAB

The recorder's own CLI speaks the same protocol, which is the way to check the
path is working before involving MATLAB at all:

```bash
python -m lschart -c CONFIG status          # read status.json
python -m lschart -c CONFIG send ping       # round-trip a command
python -m lschart -c CONFIG send setpoint 77 --loop 1
```

## Rehearsing a sweep without the cryostat

A sweep script is worth running once before it is trusted with a night, and it
does not need the cryostat to do it. Copy the config, point it at a **simulated**
cryostat and a data directory that is not the real one, and run a second
recorder beside the first — it holds no port, so the two do not collide:

```yaml
instruments:
  - name: ls218
    driver: sim                     # was: visa
  # ...and the same for every other instrument
recorder:
  directory: C:\temp\simdata        # NOT the real data directory
ipc:
  directory: C:\temp\simdata
runtime:
  lock_path: C:\temp\simdata\sim.lock
sim:
  start_k: 120.0
```

```bash
python -m ltspm3 -c config-sim.yaml run --arm
```

Then point MATLAB at `C:\temp\simdata` instead. Every command, every refusal
and every wait behaves as it does on the cryostat, on a plant that runs in real
time — a 2 K move takes the minutes it takes. What it is **not** is a test of
the cryostat: the simulated plant is the calibrated model, so a sweep that works
here has been shown to be correctly *written*, not that the cryostat will
oblige.
