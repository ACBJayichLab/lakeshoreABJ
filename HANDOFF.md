# Handoff — 2026-08-28 (tenth session: X1, the bench 336, and the viewer)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.

**Everything in [`FEATURE_PLAN.md`](FEATURE_PLAN.md) is now implemented and
tested, X1 included. 694 tests passing (from 584), ruff clean.** Verified
against a live armed sim recorder driving a real `ltspm3` software loop through
a hold and back. **No hardware was touched this session** — the bench 336 was
not connected.

## Start here

The feature plan is no longer a to-do list; it is a record of why things are
shaped the way they are. Three sections at the end are worth reading before you
touch any of it: **Where phase 3 differed from the plan**, **X1, and the half of
its question that was wrong**, and **The two tables became one**.

**Most of this session was the viewer, and none of it was planned** — it came
from Jeff opening the thing on a real screen. If you are picking the viewer up,
read [Themes](docs/recorder/gui.md#themes) and the reading-table section of
[gui.md](docs/recorder/gui.md) first: both encode rules that are easy to break
by accident and were broken by accident here.

**Windows deployment is nearly closed out.** Of the three unknowns
`windows.md` listed, two are now settled: the clock-resolution worry is pinned
by tests that run on Windows in CI, and `config-ltspm3-heater.yaml` has since
been run *and commanded* on the cryostat's own machine (Jeff, 2026-08-28),
which also largely retires the `movefile` question. **What is left is
`os.replace` over an open `status.json` under a real reader**, plus the
unattended-running choice (Task Scheduler vs NSSM), which is a decision rather
than a defect.

## What landed this session

### The viewer, after real use on a real screen (`d5309a7`..`af2f6be`)

Eight commits, all of them from Jeff looking at the thing rather than from a
plan. Worth reading as a group, because several are the same lesson.

**Dark mode ate the text** (`3358b7a`). Reported from macOS. The viewer was
written on a light desktop and wrote its foregrounds down as constants, so the
tables forced `#000000` onto a `#171717` base — a contrast ratio of **1.17**,
which is not "hard to read", it is not there. Never a macOS bug: a dark Windows
or KDE theme would have done the same.

New `lschart/gui/theme.py`, and two rules worth keeping when you add a widget:

- **Never paint the normal case.** Ordinary text has no colour of its own; it
  is whatever the palette says. A hardcoded black is a bug on a dark theme and
  a hardcoded white is the same bug on a light one, so the fix is not a better
  constant, it is *no* constant.
- **Paint the exceptional case from a measured pair.** `tests/test_gui_theme.py`
  computes contrast ratios against the grounds Qt actually reports, 4.5:1
  floor. That caught the existing warning orange failing at 3.79 on white —
  wrong on light mode all along.

Colours resolve at call time, so a desktop that switches theme under a running
viewer is followed on the spot.

**The chart itself stays white on both themes, deliberately.** The ten curve
colours are chosen to separate on white and the stat panel is drawn to sit on
it. Say so before changing it; it is a design decision, not an oversight.

**The panel did not fit and did not scroll** (`f8fa8a9`, `4114986`). It wanted
**1404 px** against the ~795 a 949 px screen leaves, and a bare `QVBoxLayout`
answers that by squeezing children below their minimums — which is how
Setpoint, PID gains and Heater range came to be three titles with nothing under
them. It is a `QScrollArea` now, and the trace list is both what takes spare
height and the first to give it back (Jeff's call: it has its own scrollbar and
loses nothing by being short).

Then: one table instead of two, a status strip across the bottom, denser button
rows, P/I/D on one line, shorter notes. **1404 → 706 px**, no scrollbar.

**One table, not two** (`f8fa8a9`). See
[FEATURE_PLAN.md](FEATURE_PLAN.md)'s "The two tables became one" — this
reverses an L1 decision, and the reason that decision existed is what shapes
the merge. The row is the channel; the loop is columns on it.

**The status strip** carries Panic, "Listen to" and link health across the
window. "Listen to" gained MATLAB and **Other clients** beside this viewer;
*Other* is not a client, it is the overlay's own `default`, and it is the only
way to shut out a label nobody knew in advance. `sources.py` learned to honour
a `default` in the overlay for it — narrowing only, like every overlay entry.

**Panic is red, twice as wide, and opens a modal** (`f22c96b`). A popup is a
small target beside the pointer and the two things in it are "stop heating this
cryostat" and "freeze it where it is". Still three interactions — open, choose,
confirm.

**The instrument selector took three attempts** (`d51b4b9`, `3db3f92`,
`af2f6be`), and the reason is worth remembering: **a titled `QGroupBox` draws
its title above its frame**, so the widget rectangle and the box anyone sees
differ by the whole title band. Flush against the widget rect is not flush
against the box. The fix is that the first *visible* group gives up its title
to the selector's row. Watch for the two traps it hides: the group titles must
be **stored** (two change at runtime) and the stack needs a **trailing stretch**
or the slack lands above the first visible group.

**One defect found by the hardware run** (`d5309a7`): the *Send PID* button
stayed live while `ipc.allow_pid` was false, so it could only ever produce a
refusal. The spin boxes stay readable — the gains are worth seeing where they
cannot be written — and only the button is disabled.

### X1 — the software loop finally has a row (`9a5754c`)

A viewer pointed at a running `ltspm3` used to draw the heater percent as a
trace and say **nothing whatever** about the loop driving it — not its
setpoint, not its health, and not that it had locked itself out after a fault.
The loop that most needed watching was the one loop with no row. On a 218-only
cryostat the loop table was hidden entirely.

The plan asked "what should its `sensor` and `range` columns say for a loop
that has neither". **Half of that was wrong:** it does have a sensor, the
recorder's `control_channel`, which was simply never published — a fact that
never changes does not end up in a per-cycle struct. Published, the `K` column
fills itself by the same lookup every other row uses. `range` really is `n/a`,
the word a 336's loops 3 and 4 already get, for the stronger reason in
invariant 4.

Three things beyond that, none in the plan:

- **A `State` column, on every row.** Instrument rows show what `OUTMODE?`
  says; the software row shows the supervisor's state. It is what decides
  whether either W1 mark applies and it used to be reachable only by hover, so
  a loop that had quietly stopped trying was invisible without a mouse.
- **The software loop rails at its own authority band, not at 99%.** That band
  is about a percent wide, so the fixed rails could never light the mark on the
  one loop whose authority is genuinely scarce. Not a per-loop knob by the back
  door: no instrument row passes one, and this is the clamp the supervisor
  actually enforces, published as `rail_low_pct`/`rail_high_pct`.
- **The mark is judged on `demand_pct`, not `output_pct`.** A saturated
  software loop writes *below* its own rail — quantised to a DAC code, then the
  band re-applied by stepping down one — so testing what it wrote would never
  fire.

**The row is read, not clicked.** It takes no setpoint, range or PID command,
only `arm` and the panic `hold`, so it is not selectable: a row that could be
clicked into a selection the command panel cannot honour would be a row that
lies.

Two things to expect on the real cryostat. On the shipped numbers a *tracking*
software loop cannot rail at all — `max_error_k` is 1.0 K against about ±7 K of
authority, so the anomaly hold fires first and you see `holding`. And when
health goes bad both marks go **quiet**, because the loop has stopped trying;
the row is coloured instead.

`tests_ltspm3/test_status_projection.py` is new and is the one that matters:
`_control` reads every field by name off whatever the poller holds, so a rename
in `ltspm3` would leave a status file that still parses and is quietly full of
nulls. It pins the names against a real supervisor.

**The config decision from last session is settled** — `a11dfe9` says the 336
is writable because it is.

### The bench 336 (LTSPM2), on real hardware

**First hardware run since phase 2.** Everything from phase 3 had only ever
seen the simulator. `LSA26E0` over USB, cryo off, all four inputs ~295-297 K,
both heater ranges 0 throughout — **no power was applied at any point**, and
the box was left exactly as found.

What it settled, in order:

- `probe` — forces read-only regardless of config. `LSCI,MODEL336,LSA26E0`,
  firmware 3.1, four inputs, all four loops `OUTMODE` closed.
- `check` — 27 transactions, 1.35 s inside the 2.0 s cadence, exactly what the
  config's own comment predicted.
- **`read_pid` against real hardware for the first time.** P/I/D came back per
  loop (100/5/0, 124/10/1, 325/10/0, 350/10/0) and reached the viewer's boxes.
  P1 had only ever been exercised against sim and MATLAB.
- **The W1 marks, confirmed on hardware in the case `gui.md` says to expect.**
  Loops 1-2 are silent at range 0. Loops 3-4 are analog-only, so they have no
  range to be switched off by, sit ~20 K above a 275 K setpoint with the output
  at 0 %, and light *both* marks and keep them lit. That is the documented
  behaviour, seen for real.
- **A3, on hardware.** `send range 0` refused, with the message naming the
  panic path; `send pid` refused (`allow_pid` defaults off); `send heaters_off`
  **applied through the same gate that had just refused `range 0`**. That is
  the exemption working outside the simulator.
- A setpoint written and verified by readback (`SETP 1,280.0000 (verified)`),
  then restored. Inert throughout, because the range was 0 — invariant 4 doing
  its job on a real box.
- 73 cycles, **0 dropped, 0 status-write failures**, clean SIGINT shutdown
  leaving `running: false`.

**One defect found, fixed, and pinned.** The viewer's *Send PID* button stayed
live while `ipc.allow_pid` was false, so it could only ever produce a refusal —
the same shape A3 removed from the range control. The spin boxes stay readable
(the gains are worth seeing where they cannot be written); the button no longer
does. Three tests now pin all three states, including that a recorder with
`read_pid: false` may still be *sent* gains — a missing capability is not a
withheld permission.

### Windows: the command ordering test was passing for the wrong reason

`docs/recorder/windows.md` listed the ~15 ms clock resolution as unverified.
There *was* a test — twenty commands queued in a tight loop, asserted to come
back in order — but on a machine whose clock resolves finely it never touches
the sequence tie-break at all, and would go green on a spool that had no
sequence number. It was passing for a reason that had nothing to do with
Windows.

Two tests replace that hope with arithmetic, and both fail when the behaviour
they pin is removed (checked by mutation, not assumed):

- **the clock frozen**, so *every* command shares a millisecond — the worst
  case of a coarse one, and nothing but the sequence can order them;
- **the clock stepped backwards** mid-run, which must not let a later command
  sort first. That is what clamps the filename prefix monotonic, and nothing
  covered it before.

Deterministic on every platform, which is worth more than hoping the CI
runner's clock is coarse that day. What it does not settle is the end-to-end
path on the cryostat's own machine, which still has `accept_commands: false`.

## What landed in the session before this one (phase 3)

Four commits, in the order the plan's priority section gives.

### A1, A2 — a sixth gate, asking *who* is asking (`56fdf2e`)

The five interlocks all answer "may this action happen"; none can say "the
operator at this terminal may drive the cryostat, the analysis script may not".
`Command.source` had been carried end to end since the spool was written and
used for nothing but a log line. This is what it was for.

- `ipc.sources` is the ceiling, fixed for the process. `sources.json` beside the
  status file is a runtime overlay, re-read every cycle, **may only ever
  narrow**. A restart always returns to the audited config.
- **Two ways to write the overlay** — a text editor, or the `source` command
  (CLI `send source NAME on|off`, MATLAB `setSource`, and a checkbox in the
  viewer beside the Panic menu). The command is **exempt from the policy it
  edits**, which is what stops muting being a one-way door. See
  [A2 gained a command](FEATURE_PLAN.md) — this reverses an argument the
  original plan made, at Jeff's call.
- **Muted is about listening, never about reading.** `status.json` is a file
  anyone may open, so a muted client keeps every "getting" operation it had:
  temperatures, the loop table, the marks, the chart.
- Written non-empty, `default:` is **false** unless it says otherwise. A typo in
  a source name has to fail closed.
- Matched on the part before the first `/`, because the CLI stamps its pid in.
- New module `lschart/ipc/sources.py`; read its docstring first.

### P1, S3, S4 — the loop's gains (`5db2d9a`)

- `PID?` per loop on the same slow cadence as `OUTMODE?`, published in
  `links[].loops` as `p`/`i`/`d` and in aux as `{inst}.p{loop}`.
- **`read_pid` is off by default and that is arithmetic, not caution** — see the
  plan's "Where phase 3 differed". The examples turn it on at 2 s.
- New `pid` command behind `ipc.allow_pid`, which is *not* a power gate: a loop
  with range 0 stays inert however it is tuned. All three gains go together.
- CLI `send pid P I D --loop N`, MATLAB `setPID`, and `selftest.m` prints the
  gains, the gate states and the source policy.

### K1–K4 — two ways to stop, one way back (`16a90e0`)

- New `hold`: per closed 33x loop, **ramping off first** (rate kept), then the
  setpoint moved to that loop's own bound sensor's temperature. Order matters.
  A loop with no binding, not in closed loop, or whose sensor did not read is
  skipped and named.
- On the software loop, `HeaterSupervisor.panic_hold()` — `abort_ramp()` plus
  `set_mode(MANUAL)` under one name. **The one seam `lschart` reaches into
  `ltspm3` by**, duck-typed, so invariant 1 holds.
- New `arm`, the way back, and deliberately **not** a panic action: it starts
  the loop driving, so it passes every gate.
- The viewer's Panic menu is three clicks and lives **outside** the command
  group — in Qt a child of a disabled parent is disabled however firmly you
  enable it, and these kinds are exempt from the source policy at the recorder.

### A3, S6 — zero is not a permission (`ada3413`)

`range 0` and `analog 0` are gated like every other value. Cutting a heater is
not automatically the safe direction: it stops heating and can also crash the
stage, and `ltspm3` always agreed — its supervisor commands a configured
`safe_output_pct` on a fault, never zero.

**The panic kinds are now the only exemptions anywhere in the system.** Both
refusal messages name them, so a shut gate is a signpost rather than a wall.

Consequence in the viewer: a shut gate now **disables** its control instead of
just annotating it, which inverts a decision made when the exemption existed.
Both halves of the old reasoning are gone.

`CLAUDE.md` invariant 3 is rewritten: seven interlocks, both directions, and the
exemption named properly.

### After phase 3

Two follow-on commits, neither in the plan.

**`961bf96` — a failed status write is no longer silent.** `windows.md` named
this as the weak spot behind the first of its three unverified Windows
behaviours: `os.replace` over a status file another process has open can fail
with a sharing violation, and the handling was a `DEBUG` line plus a counter
nobody could read. A gap in the feed was indistinguishable from a hung recorder.
Now the **edges** are WARNING (first failure, and recovery — not every cycle,
which is how a signal gets buried) and the next file that *is* written carries
`status_file.failures` and `status_file.last_error`. Still unverified on
Windows; that needs the cryostat's own machine.

**The `source` command** — see the A1/A2 section above and
`FEATURE_PLAN.md`'s "A2 gained a command".

## A config that contradicts itself — SETTLED

**Resolved in `a11dfe9` after this was written: the comments were rewritten to
say the 336 is writable, because it is. Kept here for the reasoning.**

`config-ltspm3-heater.yaml` says one thing in its header and does another:

| Its own comments say | The file actually sets |
|---|---|
| "the whole 336: `read_only` AND `allow_writes: false` … **Nothing here may touch it**" (lines 29–31) | `ls336.transport.read_only: false`, `ls336.allow_writes: true` |
| "`ipc.allow_heater_range: false` — moot while the 336 is read-only" (line 32), and "LEFT OFF" again at line 224 | `ipc.allow_heater_range: true` |

That is the config for the cryostat where **loop 2 holds THE CHONKE and heater 2
is railed at 100%**. As written, a file command can raise a heater range on it,
which is exactly what the comments promise cannot happen.

Two readings, and only Jeff can say which:

- the comments are the intent and the values drifted (the `LTSPM3 Actual
  Heating` commit is the likely culprit) → set both to `false`;
- a real heating run needed the 336 opened and the comments were never
  updated → rewrite the comments and say why the 336 is writable.

Do not guess. Nothing in this session changed those values.

## State of the tree

- `694 passed`, `ruff` clean, on `main`. Nothing is running.
- Example configs: all three validate, all three now poll `PID?`.
  **`examples/config-336-usb.yaml` moved to a 2 s cadence** — it did not
  validate before this session, because `read_analog_outputs: true` had been
  added without raising the poll to match. `tests/test_config.py` now checks
  every shipped example loads and fits its own cadence.
- Bring a recorder back with:

  ```
  .venv/bin/python -m lschart -c examples/config-336-usb-writable.yaml run
  ```

## What was verified, and how

Nothing here touched hardware. Everything below is a live process.

**This session (X1).** An armed sim recorder with the software loop closed:
the table drew `sw | Sample | 96.209 | 95.997 | 63.1 | n/a | tracking` beneath
the 336's four loops. `send hold` moved it to `idle`, mode `manual`, with both
marks dark and the frozen 63.070% still in the output column; `send arm`
returned it to `tracking` at the temperature it had drifted to. The row refused
selection throughout while the instrument rows accepted it.

**Earlier sessions, phase 3.**

- **A1/A2**: a sim recorder refused the CLI by config with the remedy that needs
  a restart, applied MATLAB, then refused MATLAB via `sources.json` with the
  remedy that does not.
- **P1**: `send pid 123 45 6` applied and verified; the loop table carried the
  new gains; MATLAB's `setPID` landed and read back.
- **K3/K4**: against a plain recorder, `hold` moved all four loops to their own
  sensors and kept a 3 K/min rate while switching ramping off. Against a real
  `ltspm3` software loop it froze the heater at 63.070% in `idle`/`manual`, and
  `arm` returned it to `tracking`/`pid` at the temperature it had drifted to.
- **A3**: `range 0` refused with the gate shut, the reply naming the way out;
  `heaters_off` and `hold` both applied through that same shut gate.
- **MATLAB R2025b** ran `selftest.m` end to end against a live recorder, and
  separately muted and un-muted *itself* with `setSource` — reading
  temperatures and the loop table throughout, which is the point of the
  listening/reading split.
