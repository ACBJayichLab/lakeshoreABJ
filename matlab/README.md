# Talking to the recorder from MATLAB

`LakeShore.m` lets a MATLAB script read temperatures from a running `lschart`
recorder and command its instruments' setpoints.

## Why MATLAB does not open the instrument

It cannot. A Windows COM port has exactly one holder, and the recorder holds
it — so a MATLAB script that opened `COM10` would either fail, or take the port
and leave the recorder blind. There is no arrangement in which both talk to the
box.

So MATLAB talks to the **recorder** instead, through two files:

| | |
|---|---|
| `status.json` | rewritten by the recorder every poll cycle: temperatures, link health, and the outcome of recent commands |
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
ls = LakeShore('C:\lschart\data');

ls.isAlive()                 % is the recorder actually running and current?
ls.channels()                % {'Sample', 'Cold Head', 'Shield'}
ls.temperature()             % all of them, as a struct
ls.temperature('Sample')     % one, in kelvin
ls.aux('ls336.setpoint1')    % setpoints, heater percents
ls.links()                   % per-instrument health: up, writable, loops
T = ls.readLog();            % the CSV as a table, safe to read mid-run

ls.setSetpoint(1, 77.0);     % blocks until the recorder confirms
ls.setRamp(1, 2.5);          % K/min, run by the instrument's own firmware
ls.setRange(1, 0);           % heater range — 0 is off
ls.setAnalog(5.0);           % 218 analog output percent — see the warning below
ls.heatersOff();             % everything the recorder may write to, to zero
```

Every command method blocks until the recorder acknowledges it, and **raises
if the command was refused**. That is deliberate: a setpoint that was silently
rejected must not look to a sweep script like one that was applied. Ask for
outputs instead when you want to inspect the outcome yourself:

```matlab
[ok, message] = ls.setSetpoint(1, 77.0);
```

Use `submit` and `await` to queue without blocking.

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
true` — for anything above zero. `setAnalog(0)` is always allowed.

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

| Refusal | Fix |
|---|---|
| `this recorder is not accepting commands` | `ipc.accept_commands: true` in the recorder's config |
| `... is configured read-only` | `allow_writes: true` on that instrument |
| `raising a heater range applies power ...` | `ipc.allow_heater_range: true`, if a remote client really should be able to turn a heater on. Turning one **off** is always allowed |
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
