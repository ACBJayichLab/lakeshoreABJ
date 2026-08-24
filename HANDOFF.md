# Handoff — 2026-08-24

Point-in-time status. Durable context lives in `CLAUDE.md`; this goes stale.

**Branch `split/generic-lschart`, 5 commits ahead of `main`. 194 tests passing.**

## The priority changed

Jeff, this session: **the GUI and the MATLAB interface are the priority; the
software PID is not.**

That is a reordering, not a cancellation. `ltspm` is finished and tested and
should be left alone. Everything below is organised around getting `lschart`
into a coworker's hands and onto a screen.

## What happened this session

### The project split in two

`lschart` is now a generic Lake Shore recorder that a coworker can install and
point at their own rig; `ltspm` is the LTSPM3-specific software PID that
depends on it. Three couplings had to be cut (config imports, the supervisor
wiring in `app.py`, and a simulator fused to the calibrated plant); the rest was
file moves. `lschart` contains no reference to `ltspm` beyond a doc comment.

The join that makes it work: a config file carrying a `control:` section is
**refused** by a recorder-only install, with a message saying to run
`python -m ltspm`. It is not silently ignored, because silently recording when
someone asked for a closed heater loop is the wrong failure.

### A real Lake Shore 336 got connected

A spare 336 on USB — the first hardware this project has ever talked to.
Read-only first, then every write path, with the instrument's full state
captured before and confirmed identical after.

**It found four defects that no amount of simulation would have.** All four are
now in `CLAUDE.md` under "Talking to a Lake Shore box: four things that will
bite". The one that matters most:

> **Lake Shore boxes apply commands asynchronously.** A query issued too soon
> after a write overtakes it and answers with the *previous* value. At 0 ms
> every readback was stale; at 50 ms readbacks lagged by exactly one write.
> Both of those *look like success*.

`LakeshoreTransport` had `inter_command_delay=0.0`, so `set --setpoint 77` would
have printed a confident confirmation of the old value. Fixed twice over: a
100 ms post-write settle makes the race unlikely, and readback verification in
`LS33x` makes it detectable. Only the second is worth staking a cryostat on.

### The 33x family, and the write path

One driver for 335 and 336 with a capability table per model. The write surface
a hardware-PID rig actually needs — `SETP`, `RANGE`, `PID`, `RAMP` — behind
`allow_writes` plus a `max_setpoint_k` ceiling, plus a lower-level `read_only`
interlock that refuses at the point where bytes leave.

The rule that shapes it: **a setpoint does nothing while the heater range is 0,
and raising the range is what applies power.** So nothing raises a range as a
side effect, and `set` applies `--range` last.

### Robustness

Reconnection lives in the `Transport` base class: lazy opening, retries backing
off 1→30 s, and a link only torn down after 3 consecutive failures because one
GPIB timeout is usually a slow instrument rather than a dead bus. Plus an
OS-level single-instance lock, since a COM port has exactly one holder and two
processes on one GPIB board interleave into garbled replies.

## Current state

| Area | State |
|---|---|
| `lschart` transport / instruments | **Exercised against real hardware.** 218 GPIB path is *not* — no 218 has ever been connected. |
| `lschart` config / app / CLI | Complete. `run` / `probe` / `set` / `check` / `init`. |
| `lschart` acquisition | Complete. 120 cycles at 1 Hz off the real 336, 0 dropped. |
| `lschart.ipc` | Only `lock.py`. **Status file and command spool not started.** |
| **GUI** | **Not started.** The priority. |
| **MATLAB interface** | **Not started.** The priority. |
| `ltspm` (software PID) | Complete, 100+ tests, replay over 63 days of real logs. Parked. |
| Windows deployment | Untested. Development is macOS. |

## Next steps, in priority order

### 1. The MATLAB file interface (`lschart/ipc/`)

Decided this session: **files, not a socket.** A socket puts a connection state
machine inside the process that must never die, and its failure is quiet — a
dead server thread keeps recording perfectly while silently ignoring every
setpoint. The file version has no connection state at all: Python never learns
MATLAB exists, which is the strongest form of "don't crash if MATLAB does".

This is *mandatory*, not merely preferable: a Windows COM port has exactly one
holder, so MATLAB cannot open COM10 while the recorder has it. Talking through
files is the only shape that works.

- `status.json`, rewritten atomically each cycle (temp file + `os.replace`).
  Carries temperatures, link state, a heartbeat, and `last_applied_id`.
  A failed replace is harmless — the next cycle rewrites it.
- A maildir-style command spool: MATLAB writes `cmd.tmp`, renames it to
  `<counter>.json`; the poller scans, applies, deletes. No locking, no
  contention, and a crash mid-write leaves a `.tmp` nobody picks up.
- **Commands carry a timestamp and are ignored beyond ~30 s.** Without this, a
  recorder that was down for an hour comes back and replays a backlog of stale
  setpoints into a live cryostat.
- **Commands carry an id, echoed in `status.json`** — the acknowledgement a
  naive file scheme lacks.
- `matlab/LakeShore.m`: `temperature()`, `setSetpoint(loop, K)`, `isAlive()`.

### 2. The GUI

**A separate process** reading `status.json` and tailing the CSV — not a thread
in the recorder. A Qt bug then cannot take down logging, the viewer can be
closed and reopened mid-run, and two people can watch at once. It becomes just
another file-IPC client, same contract as MATLAB, so it costs little extra.

pyqtgraph strip chart; `pip install "lschart[gui]"`. The GUI dependencies were
deliberately moved out of the base install this session — the recorder is what
must stay up for months, and it should not need Qt to do it.

### 3. Windows deployment

The real target. Needs: the vendor USB driver, a Task Scheduler recipe (or a
service wrapper), and a check that the CP210x/COM-port path behaves as it does
on macOS. `serial_number` matching already handles re-enumeration, which is the
common failure for a USB instrument left running for weeks.

### 4. Hand the coworker their build

`examples/config-335-usb.yaml` is ready and annotated. Their 335 is on COM10
with heaters on its own outputs, so `driver: lakeshore` applies and **no VISA
runtime is needed**. Wants a short README rather than this file.

## Not the priority, but do not lose

- **`verify_readback` on the 218 may be reading stale values.** The async-write
  behaviour measured on the 336 very likely applies to the 218 on GPIB too.
  `SupervisorConfig.verify_readback` reads `AOUT?` after `ANALOG`; it passes in
  simulation only because the fake applies writes synchronously. **Check this
  before the LTSPM rig ever runs armed.** This is the highest-value item on the
  parked list.
- **Sweep scheduler** — `sweep_to()` exists and is tested; a sequence of
  setpoints with dwell times does not.
- **A deliberate step test at two or three temperatures** remains the
  highest-value LTSPM hardware measurement. `tools/steptest.py` is the protocol;
  only the 137 K row is real.

## Two open questions worth resolving

**Is the LTSPM noise model right?** The bench 336 reads 0.44–3.03 mK rms
(3-point detrended) at ~296 K. `CLAUDE.md` says the 218 sample channel does
**109 mK at 290 K**. That is 30–200× worse for the same temperature.

Three things differ at once — different instrument, different sensors, and a
completely quiet rig here (cryo off, nothing moving) versus 218 logs taken
during active cooldowns. Any could dominate, so **neither number is wrong yet**.
The clean resolution is to record the 218 under the same quiet conditions and
compare. It matters because "millikelvin is a low-temperature capability" is
built on the 218 figure.

**Does `read_status: true` earn its cost?** On the bench 336 the only cycles
exceeding 1 s were exactly the every-15th ones adding four `RDGST?` queries
(~1.25 s vs ~1.00 s). Nothing was dropped — the fixed-deadline schedule absorbs
it — but the margin is gone. Raising `status_every_n_cycles`, or dropping status
polling, buys it back.

## Things worth knowing

- **`reference/logs` is ~110 MB and deliberately not gitignored** — the only
  empirical record of the LTSPM plant.
- **Filenames in `reference/logs` lie.** `import_xls` sniffs row 0.
- **`sim.speedup` accelerates the plant but not the controller.** Fine for
  exercising the recorder; meaningless for closed-loop behaviour. Use the
  virtual-clock harness in `tests_ltspm/conftest.py`.
- **The bench 336's loops are benign by value, not by configuration.** All four
  are in closed-loop mode with `powerup_enable=1` on loops 3/4; nothing runs
  only because every setpoint (275 K) sits below ambient (296 K).
