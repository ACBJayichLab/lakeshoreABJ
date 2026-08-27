# Handoff — 2026-08-27 (seventh session: the feature plan gets specified)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.

**No code changed. `FEATURE_PLAN.md` was rewritten from a draft into a specification
with stable IDs, phases and a file map. 408 tests passing, ruff clean — the next
session starts from a known-good tree.**

## Start here

Read [`FEATURE_PLAN.md`](FEATURE_PLAN.md), then its **Where the work lands** section,
which maps every ID onto files and entry points. **Phase 1 (V1, V2, C1, C2, E1) is
viewer-only** — no recorder change, nothing on the bus, independently shippable. Start
there unless told otherwise.

## What this session did

Took a feature draft written in a previous session and specified it with Jeff. Most of
the session was spent finding places where the draft had flattened an intent into
something simpler and wrong. Nine such places are listed in the plan's *What changed*
section; three are worth repeating here because they are the shape of the mistake:

- **Two features were one feature cut in half.** "Legend shows current value" and
  "hover tooltip" were the residue of *cursor region statistics* — two cursors and the
  mean between them. A live number in the legend answers a question the readouts panel
  already answers.
- **A loop-centric readouts table would have deleted thermometers.** "218: 1 row" turns
  an eight-input monitor into one channel, when recording every thermometer
  continuously is the recorder's whole job.
- **Three features sourced from config what the instrument already knows.**
  `loop_heater_map` and `loop_sensors` are `OUTMODE?` and the 33x command set. On that
  family the loop number *is* the output number by protocol.

### The decisions with teeth

**A1/A2 — a sixth interlock, on a new axis.** Per-source command policy: an immutable
`ipc.sources` ceiling plus a mutable runtime toggle that may only ever narrow it.
`Command.source` is already carried end to end and already populated by all three
clients; today it reaches `_execute` and is used only for a log line.

`allow_writes` is **not** repurposed for this, and the reason is worth carrying: it is
driver policy, it gates every caller equally, and `ltspm3`'s supervisor writes to the
instrument in-process without touching the spool — so it has no source for a source
policy to describe. The five existing gates ask *may this action happen*; these ask
*may this client ask for it*.

**A3 — the zero-exemptions are removed.** This is a reversal of a documented invariant.
`lschart` currently assumes zero is the safe direction in four places, including
`CLAUDE.md` invariant 3. Jeff's correction: cutting heater power is **not** automatically
safe on this cryostat — it stops heating and it can also crash the stage. `ltspm3`
already agreed, quietly: the supervisor commands a configured `safe_output_pct` on a
fault rather than zero.

After A3 the only exemptions anywhere are the two panic actions, and **the exemption
belongs to the command kind, not to the GUI** — MATLAB may send both. The fallout is
logged as S6 and is mostly documentation, including a rewrite of invariant 3.

**K3 — a new `hold` command.** Sets each loop's setpoint to its bound sensor's current
temperature, ramping disabled first so the instrument holds instead of ramping to it.
On the 218 it freezes the analog output and stops the software loop. The supervisor
already models this: `SupervisorState.HOLDING` is *"output frozen pending clarity"*, and
`abort_ramp()` / `set_mode(MANUAL)` / `set_manual_percent()` compose into it with the
clamp and rate limiter still in force. `arm()` is the way back.

## What is still open

One item: X1, the software loop's own state in the loop table, deferred deliberately.

Two others were settled late and are worth flagging because they arrived after the
register was written. The viewer **may** resume a software loop after a panic hold —
that is K4, a new `arm` command, and it is pointedly *not* a panic action: arming
starts the heater again, so it passes every gate. And W1's saturation thresholds are
**fixed** at 99% / 1%, not per loop; only W2's not-settled threshold is per loop.

## Unchanged

Windows deployment is still the outstanding risk, and CI proving the code runs on
Windows is still not the same as a recorder surviving a week on the cryostat's own
machine. `control/` was not touched.

# Handoff — 2026-08-26 (sixth session: the tests get audited, and CI arrives)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.

**395 tests, all passing, on Linux, Windows and macOS — CI now runs them.
No production behaviour changed except one CLI error path. `control/` untouched
apart from two dead lines the linter found.**

## What this session did

Audited the suite rather than adding to it: read every test file, measured
coverage, then ran targeted mutations against the safety logic to see which
tests actually bite. Ten of twelve mutations were caught. The two that survived
are the interesting part.

### The one real hole

`test_per_step_rate_limit_is_respected_while_tracking` **passed with the rate
limiter deleted**. Without the limiter the output jumps to the band ceiling in
one cycle and then sits there, so every step it measured was exactly zero and
its ceiling assertion was satisfied trivially. Its own `assert steps` guard was
written to catch this and only checked that steps were *recorded*. One line
fixes it, and the mutant now fails.

The other survivor was not a hole: the authority band is enforced at three
independent sites, so removing one is invisible. The observable invariant holds
and the test asserts the right thing. **Left alone deliberately** — pinning
each layer separately would be testing the implementation, not the behaviour.

### Bugs the audit turned up

- **`cmd_set` could not report its most important failure.** `InstrumentError`
  is a `RuntimeError`, so it fell through `except (ValueError, OSError)` and the
  "the write was NOT applied — do not assume" message arrived as a traceback.
  Found by covering the CLI, which had been at 17%.
- **A control test passed the supervisor the whole readings dict** where it
  takes one `Reading`, hidden behind an always-taken `except Exception`.
- **Seven tests silently skipped outside the repo root**, including all of
  `test_replay_reference.py` — the only tests that run on genuine data — while
  reporting "reference logs not present". Relative paths.
- **`Poller(sleeper=)` did nothing.** `run` blocks on `_stop.wait`, so anything
  injected through that seam was ignored. Removed.
- **`test_the_backfill_stops_once_its_coverage_is_met`** — the "pre-existing
  failure" the previous handoff recorded. It is not pre-existing and not
  persistent: it built its logs at "today 12:00" and asserted against a budget
  `_backfill` measures from `now()`, so it passed 00:00–17:59 and failed
  18:00–23:59, every day, in every timezone. Verified at all 24 hourly offsets.
  Fixed by anchoring the fixture to `now`. `_backfill` itself is correct —
  measuring from now is the promise it makes.

### Coverage, where it was thin

| | before | after |
|---|---|---|
| `lschart/__main__.py` (the CLI) | 17% | 77% |
| `poller.py` (the threaded loop) | 70% | 95% |
| `ipc/service.py` | 88% | 93% |
| overall | 81% | 86% |

New: `tests/test_cli.py` (31 tests, driven through `main()` with real argv);
the poller's cadence, thread lifecycle and overrun reset; the `ramp` file
command, which MATLAB's `setRamp` drives and which carries the "rate 0 turns
ramping off" subtlety; and the recorder-only refusal of an ltspm3 config —
which needs a subprocess, because `register_section` is process-wide and any
`tests_ltspm3` import makes the refusal unobservable in-process.

### CI

`.github/workflows/tests.yml` — Linux, Windows, macOS × py3.11, py3.13. Lints
first. **A skipped test fails the build**, because a skip is exactly how the
real-data tests went missing. It found the backfill bug on its first run.

The repo is public, so Actions minutes are free and unmetered. If it ever goes
private the org is on the Free plan (2,000 min/month) and a run costs ~42
billable minutes, ~30 of them macOS at its 10× multiplier — dropping macOS from
the matrix is the lever, not dropping Windows.

## What is still not verified

Unchanged from the last three sessions, and CI does not change it: **a recorder
has not run for a week on the cryostat's own Windows machine.** CI proves the
code runs on Windows. It says nothing about that box's COM port, its drivers,
or its power management.

Also still open, and not this session's business: no ramp or step limit on the
manual path, in the CLI or the viewer.

# Handoff — 2026-08-25 (fifth session: command fields learn where the cryostat is)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.

**343 tests: 342 passing, plus one pre-existing failure
(`test_the_backfill_stops_once_its_coverage_is_met`, which fails on a clean
tree too). Small GUI session; nothing else touched.**

## What this session did

The viewer's command fields now **fill with what the recorder's readback says
the box is at** instead of opening at zero: setpoint from the selected loop,
analog output from the 218's current percentage (so swapping to it finds the
power it is already driving rather than presenting 0% as if that were neutral),
range combo from the box's current range. All read from the `aux` block of
`status.json` — no new protocol, no link, same file MATLAB reads.

Three decisions in there worth not undoing:

- **fields track the readback every tick until edited**, so a value changed
  from MATLAB or another viewer shows up here too; an edit stops tracking
  until the selection changes or the pending command settles;
- **a queued command holds its field at the commanded value until the readback
  confirms it.** Without this guard the stale pre-command aux value snapped the
  field back — showing 0% again in the seconds after someone asked for 43%;
- **no aux entry means no fill.** An older recorder without the name in its
  status file leaves the widget alone rather than guessing.

Documented in [gui.md](docs/recorder/gui.md). Also corrected there: the drag
section still described the *flat drag picks time alone* behaviour that an
earlier session replaced with always-the-whole-rectangle; the docs now match
`ZoomViewBox` as it is.

---

# Handoff — 2026-08-24 (fourth session: the sample heater becomes drivable)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.


Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.

**311 tests passing (was 257). Nothing has been written to any heater.** The
read-only recorder from the third session is still running on the cryostat — sample
4.742 K, `ls218.aout1` 0.0%, 336 loop 2 still railed.

## What this session did

Made manual control of the **218's analog output 1** — the sample heater —
possible through the running recorder's file spool, at Jeff's request, and
explicitly *not* by enabling the software PID. `ltspm3/control/` was not touched.

The 218 previously had **no write gate at all**. `set_analog_percent` was
reachable only from `HeaterSupervisor`, so it had never needed one, and none of
the CLI, the spool or MATLAB could reach it. Adding a manual path meant adding
the gate first.

### The design point worth carrying forward

**A 218 has no inert half.** Every safety story in this repo up to now leaned on
the 33x split: `SETP` says where to go and does nothing, `RANGE` applies power,
and you gate them separately. A 218 has no loop, no range and no setpoint —
one `ANALOG` command, and the percentage *is* the power. Nothing about `40`
looks more dangerous than `4`, and on this cryostat (~10 K/%) the difference
between them is about 350 K.

So the ceiling does the work the `RANGE` split used to do:

| | |
|---|---|
| `allow_writes` on the 218 | new, off by default, same shape as the 33x gate |
| `max_output_pct` | new. **70.0** in the cryostat config — the supervisor's own `hard_max_pct`, just above the hottest step in the reference logs |
| `verify_writes` / `readback_tol_pct` | new. Confirms by `AOUT?`; tolerance must clear the 0.01% DAC step *and* the two-decimal readback, or a good write reads as a failure |
| `ipc.allow_analog_output` | new, fifth interlock. Deliberately **not** folded into `allow_heater_range` — this cryostat wants exactly one of them open |

`heaters_off` now covers **every** writable instrument rather than one, 33x
ranges and 218 analog outputs alike, skipping read-only boxes and naming them.
A panic button that leaves the sample heater running is worse than none,
because it will be believed.

A driver limit refusing a command (`max_output_pct`, `max_setpoint_k`, a
missing loop) now comes back as `refused: …` at WARNING instead of an ERROR
with a traceback. An operator's typo must not look like a fault in a live run's
log.

### `config-ltspm3-heater.yaml`

New sibling to `config-ltspm3.yaml`, following the `-writable` pattern from
`examples/`. Opens the 218 only — the 336 stays `read_only: true` *and*
`allow_writes: false`, because loop 2 is holding THE CHONKE at 100% and has no
headroom. Same lock file, so the two recorders cannot both run. Different
`filename_prefix`, because a run where the heater could move is a different
kind of record from one where it could not, and in six months the filename is
the only thing that will still say which.

### What was verified, and what was not

- **Verified against the simulator, end to end through the real CLI**: queue →
  apply → readback → acknowledge, the ceiling refusing 400%, `heaters_off`
  zeroing the output, and the audit line
  `ls218: ANALOG 1, 0, 2, 1, 1,1,1,43.000  (5.000% -> 43.000%)` at WARNING.
- **Not verified against hardware.** Nothing was sent to the real 218. The
  GPIB board was left alone entirely — the read-only recorder holds it, and a
  second opener is the garbled-reply hazard. **The first real write is Jeff's,
  with him watching.**
- `ltspm3` was left parked. One consequence: `LS218Config.allow_writes` defaults
  false, so **an armed `ltspm3` run now needs `allow_writes: true` and
  `verify_writes: false` on its 218**. `controller_factory` raises at startup
  saying exactly that, rather than letting the poll thread hit a
  `PermissionError` on the first output.

### The GUI got both controls, at Jeff's request

The third session listed "no heater range control" as a deliberate omission.
That is now reversed on purpose: refusing to offer it just meant the operator
walked to another terminal and applied power *without* the chart in front of
them, which is worse. The viewer now has a four-part control panel — setpoint,
heater range, analog output, and an always-live **All heaters OFF**.

Which controls appear is decided by capability data the **recorder** now
publishes in `status.json` (`links[].loops`, `heater_outputs`,
`analog_output`, `max_output_pct`), not by a model-number table in the viewer.
A viewer newer than the recorder it is watching degrades to the old assumption
(loops 1–4, no analog control) rather than to an empty panel.

Four decisions in there worth not undoing:

- **the analog spin box is capped at the recorder's `max_output_pct`**, so the
  widget cannot express a value that will be refused, and the ceiling is
  visible in the group title;
- **a shut gate is announced, not greyed out.** 0 is always permitted, so
  disabling the control would remove the button at exactly the moment somebody
  wants to make the cryostat safe;
- **the range dialog quotes the setpoint with its age.** This was a real defect
  caught by driving the GUI against a live recorder, not by a test: the cycle
  order is read → apply → write status, so a setpoint set seconds ago is *not*
  in the file yet, and the first version showed the stale number as current;
- **one unacknowledged command locks every button**, so a range cannot be
  queued against a setpoint that turned out to be refused.

Verified headless against a live simulated recorder, both boxes: the controls
switch with the instrument, a click reaches the spool, and the acknowledgement
comes back — including `✓ ls218: analog output 0%; ls336: all heater ranges 0`
from the panic button while the 336 was selected.

### Still not done, deliberately

- **No direct CLI path to the 218.** `lschart set` remains 33x-only; the 218 is
  reachable only through a running recorder's spool. That was the requested
  shape (keep the log unbroken), not an oversight.
- **No ramp or step limit on the manual path**, in the CLI or the GUI.
  `analog 60` from 0 is one step. Rate limiting is control policy and belongs
  to the supervisor; duplicating it would give the cryostat two sets of limits that
  can disagree.

---

# Handoff — 2026-08-24 (third session: first live deployment)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.

**On the LTSPM3 cryostat itself, on `main`. 257 tests passing on the cryostat's own
interpreter. Recording a cold cryostat, read-only, at 2 s.**

## What this session did

Took a fresh clone onto the LTSPM3 machine and made it record the real
instruments. Scope was deliberately narrow: **monitoring only**. No control
section, no writes, nothing in `ltspm3/` touched.

`config-ltspm3.yaml` is the new file — a plain `lschart` config with
`read_only: true` on both transports, `allow_writes: false` on the 336, and
`accept_commands: false`. `check` reports `writable: nothing (read-only)`.

### The bug worth knowing about: the Windows lock did not work

`runtime.single_instance` was **ineffective on Windows**, and had been all
along. `msvcrt.locking` locks a byte range from the *current file position*,
and the lock file is opened `"a+"` — so every holder locked a different byte
and no second instance ever collided with the first.

It looked like it worked only because of an accident further down: the second
process truncated the file and wrote its own record, and that write failed with
a `PermissionError` against the first holder's lock on byte 0. A second
recorder was refused — by the wrong error, after erasing the running recorder's
diagnostics.

Now taken on a fixed byte past the record. Verified live: a second `run` exits
2 naming the holder, before opening any transport. `tests/test_lock.py` no
longer skips the killed-holder case on Windows — that skip is what hid this.

Un-skipping that test then exposed a second Windows fact: the kernel releases
the lock during process *teardown*, which lags `wait()` returning, so an
immediate reacquire is refused about one attempt in three. That is not a test
artefact — **a supervisor that restarts the recorder the instant it dies can be
refused its own lock**, and `run` exits 2 without retrying. Relevant to the
still-undecided Task Scheduler / NSSM question.

Full reasoning in [`docs/recorder/windows.md`](docs/recorder/windows.md).

### Two smaller things the hardware settled

- **The 218 ends a GPIB reply with `LF`, the 336 with `CR LF`.** The default is
  `CR LF`, so the 218 needs `read_termination` set. A wrong terminator is not a
  visible failure — EOI ends the read anyway — it just costs a warning per read
  and a hidden dependence on EOI.
- **The machine had only Python 3.10.0** and `pyproject.toml` asks for 3.11.
  Nothing in the codebase needs 3.11; the full suite passes on 3.10.0, so it
  was installed with `--ignore-requires-python` rather than installing a second
  Python onto a machine running a live experiment. The metadata was left alone.

### The cryostat, as measured 2026-08-24 16:10

Cold, and someone else's. Recorded here because the numbers, not memory, are
what a later session should trust.

| 218 | | 336 | |
|---|---|---|---|
| Sample | 4.742 K | A RAD SHIELD | 39.27 K |
| Cold Head | 5.494 K | B THE CHONKE | 289.182 K |
| Shield | 4.810 K | C 1st Stage | 28.32 K |
| | | D 2nd Stage | 3.36 K |

Loop 1: setpoint 295.0 K, heater 1 at 0%, range 0. Loop 2: setpoint **289.2 K**
(memory said 290.6), heater 2 at **100%**, range 3.

> **Heater 2 is railed.** It sat between 90.8% and 100% for three days and is
> at 100% now, holding THE CHONKE 18 mK *below* a setpoint it is supposed to
> reach from underneath. Loop 2 has no headroom left. Nothing here caused that
> and nothing here can fix it, but anything that adds heat to THE CHONKE will
> now simply lose, and that is worth knowing before the next run.

The 218's analog output 1 — the sample heater — reads 0.0% and is logged every
cycle as `ls218.aout1`.

## What is still not verified on Windows

The first deployment records only, so the whole command path is untested here:
the ~15 ms clock resolution behind command sequencing, and MATLAB's `movefile`
rename into the spool. `os.replace` over an open `status.json` did not fail in
this run, but note `_write_status` discards the write result and logs failures
at `DEBUG`, so at `INFO` that failure mode is silent. All three are in
`docs/recorder/windows.md`.

## Priorities are unchanged

`ltspm3/` was not touched and should not be. Next up is the same list
`CLAUDE.md` gives: running the recorder unattended (Task Scheduler vs a service
wrapper is still undecided), and exercising the MATLAB half against this
machine's own MATLAB.

---

# Handoff — 2026-08-24 (second session)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.

**Branch `split/generic-lschart`. 246 tests passing (was 194).**

## What this session did

The two things `CLAUDE.md` names as the priority — **the MATLAB interface and
the GUI** — now exist, and both were exercised against something real rather
than only against tests.

### The file interface (`lschart/ipc/`)

Built to the shape the previous handoff decided: files, not a socket. Three
new modules — `status.py`, `commands.py`, `service.py` — plus an `ipc:` config
section and two new CLI verbs. The design and the reasoning are in
`docs/recorder/file-interface.md`; that is the durable copy and this does not
repeat it.

What is worth knowing here is what it cost to get right:

- **Command ordering needed a sequence number, not just a timestamp.** The
  first version prefixed filenames with the issuing millisecond. Three
  commands submitted in one smoke test were applied in *uuid* order, because
  they shared a millisecond. On Windows this is much worse than it sounds —
  `time.time()` resolves to about 15 ms there, so a script queueing a setpoint
  and then a heater range would routinely have them applied backwards.
- **`lschart check` crashed on `examples/config-335-usb.yaml`** — the very file
  meant to be handed to the coworker. A recorder-only cryostat declares no
  `control_input`, and `cfg.control_channel` raises. Fixed, and `check` now
  also reports the status file, the command permissions and which instruments
  are writable.

### MATLAB (`matlab/`)

`LakeShore.m`, `selftest.m`, and a README. **Tested against the real MATLAB
R2025b on this machine**, driving a live recorder: reads, every command, the
refusal path, and ordering.

That testing was worth it, because it found a bug no amount of Python testing
could have:

> **MATLAB reseeds its default RNG identically at every session start.** So
> ids built from `randi` repeat across sessions, `await()` matched an
> acknowledgement left in the recorder's ring by the *previous* session, and a
> `setSetpoint` reported `pong`.

Fixed twice over, in the house style: ids now come from `tempname`, *and*
`await` ignores any acknowledgement stamped before the command was issued. The
second is what makes it safe rather than merely unlikely.

Also dropped `java.io.File` for the rename — it warns under `matlab -batch` and
does not exist under `-nojvm`, and the atomicity it bought is not needed: a
partially written JSON object has lost its closing brace, so it can only fail
to parse, never parse into a *different* command.

### The GUI (`lschart/gui/`)

A separate process, as decided. pyqtgraph strip chart with two x-linked panels
— kelvin above, output percent below — because 63% and 63 K are different
quantities and one axis invites reading a trend across them. Live readouts,
link health, a time-window selector, per-trace toggles, and a setpoint control
that writes into the same spool MATLAB uses, behind a confirmation dialog.

**Dragging a rectangle on a panel zooms to exactly it** (`ZoomViewBox`), which
is the gesture the preset combo cannot express: "what happened between there
and there, at this magnification". Both axes, at precisely the edges dragged.
The time axis is shared over the x-link; the value axis belongs to the panel
dragged, so a kelvin rectangle leaves the percent panel autoscaling. A drag
that is flat sets time alone and one that is tall and thin sets value alone —
reaching for a time window with a level hand must not crop the temperature
axis to a hair. `Drag zooms: X | Y` beside the combo takes an axis out of the
gesture entirely, and they cannot both be off. A hand-picked view stops
following the recorder, says so in the status bar, lights the `Live` button,
and is left by that button, a double-click, or any preset — all axes at once.
The time window refeeds the curves with exactly the samples in the span rather
than only moving the view, so a panel still autoscaling fits what is on screen.
Shift-drag pans; `Shift` and not `Ctrl` because macOS turns Ctrl-click into a
right-click before Qt sees it. `tests/test_gui_window.py` drives it headless.

Verified headless (`QT_QPA_PLATFORM=offscreen`) against a live recorder,
including the send path and the acknowledgement round trip.

`source.py` holds everything that is not Qt and is what the tests cover; Qt is
imported by exactly one module in the repo.

## Current state

| Area | State |
|---|---|
| `lschart` transport / instruments | Exercised against a real 336. The 218 GPIB path is still **untouched by hardware**. |
| `lschart` config / app / CLI | Complete. `run` / `probe` / `set` / `check` / `status` / `send` / `init`. |
| `lschart` acquisition | Complete. |
| **`lschart.ipc`** | **Complete and tested** — status file, command spool, four interlocks. |
| **MATLAB** | **Complete and tested against MATLAB R2025b.** |
| **GUI** | **Complete** for a first cut; see "What the GUI does not do yet". |
| `ltspm3` (software PID) | Complete, parked, untouched this session. |
| **Windows deployment** | **Untested. Now the priority.** |

## Next steps, in priority order

### 1. Windows deployment

The real target, and now the only unstarted item on the original list. Needs:

- the Silicon Labs CP210x VCP driver (or the vendor's, for a 335 on a COM port);
- a Task Scheduler recipe or a service wrapper for the recorder;
- a check that `os.replace` over an open `status.json` behaves. On Windows,
  replacing a file another process has open can fail with a sharing violation.
  This is *handled* — the write is counted and logged, and the next cycle
  rewrites it a second later — but it has never been *observed*, and it is
  worth knowing whether it happens once an hour or never;
- confirmation that the ~15 ms clock resolution assumption behind the command
  sequence number is right, and that `movefile` from MATLAB is a rename there.

### 2. Hand the coworker their build

`examples/config-335-usb.yaml` is annotated and now carries an `ipc:` section
with `accept_commands: true`. `matlab/README.md` is written for them.

The documentation gap this called out is closed: `README.md` covers install →
run → view → MATLAB, and the design document has been split into
`docs/recorder/` (generic, any cryostat) and `docs/ltspm3/` (the software PID, one
cryostat), so nobody has to read the cryostat's calibration to start a recorder.
`CLAUDE.md` is now orientation and invariants pointing into those.

### 3. What the GUI does not do yet

Deliberate omissions, not oversights:

- **no heater range control.** It applies power; doing it from a chart is a
  different decision from typing it. The spool supports it, gated;
- **no ramp control** — same file protocol, just no widget yet;
- **no annotation of the log** from the viewer;
- **no y-axis autoscale lock** or cursor readout. Both are pyqtgraph one-liners
  if they turn out to be wanted;
- **no export of a hand-picked span.** Picking a window is a way to look, not a
  way to cut the log.

## Not the priority, but do not lose

- **`verify_readback` on the 218 may be reading stale values.** Unchanged from
  the last handoff and still the highest-value parked item. The async-write
  behaviour measured on the 336 very likely applies to the 218 on GPIB too;
  `SupervisorConfig.verify_readback` reads `AOUT?` after `ANALOG` and may be
  confirming a stale value. It passes in simulation only because the fake
  applies writes synchronously. **Check this before the LTSPM3 cryostat runs armed.**
- **Sweep scheduler** — `sweep_to()` exists and is tested; a sequence of
  setpoints with dwell times does not. Note that the file interface now makes
  this reasonable to write *in MATLAB* instead, which may be the better home
  for it: it is an experiment protocol, not a safety mechanism.
- **A deliberate step test at two or three temperatures** remains the
  highest-value LTSPM3 hardware measurement.
- **Is the LTSPM3 noise model right?** The bench 336 reads 0.44–3.03 mK rms at
  ~296 K where `docs/ltspm3/thermal-response.md` claims 109 mK at 290 K for the 218 sample channel.
  Three things differ at once, so neither number is wrong yet. The clean
  resolution is to record the 218 under the same quiet conditions.
- **Does `read_status: true` earn its cost?** Unchanged.

## Things worth knowing

- **`reference/logs` is ~110 MB and deliberately not gitignored.**
- **Filenames in `reference/logs` lie.** `import_xls` sniffs row 0.
- **`sim.speedup` accelerates the thermal response but not the controller.**
- **The bench 336's loops are benign by value, not by configuration.**
- **A first `matlab -batch` on a fresh machine can take many minutes** doing
  first-run setup, with no output at all. It is not hung. Subsequent runs take
  about 20 s.
