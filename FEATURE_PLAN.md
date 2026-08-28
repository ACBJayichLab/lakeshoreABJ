# Feature Plan — Desired Behavior

**Date:** 2026-08-27
**Status:** **All three phases are done.** Everything in the register below is
implemented, tested and exercised against a live recorder.
**Supersedes:** the 2026-08-26 draft (see [What changed](#what-changed-from-the-first-draft))

| Phase | | |
|---|---|---|
| 1 | V1 V2 C1 C2 E1 | **done** -- viewer only, nothing on the bus |
| 2 | S1 S2 S5 · L1 L2 L3 R1 W1 W2 | **done** -- verified against the bench 336, read path only |
| 3 | A1 A2 P1 (S3 S4) K1-K4 A3 (S6) | **done** -- sim recorder, real software loop, MATLAB R2025b |

Phase 3 landed in four commits, in the order the priority section gives:
`56fdf2e` (A1, A2), `5db2d9a` (P1, S3, S4), `16a90e0` (K1-K4), `ada3413`
(A3, S6). What is left is X1 under [Open](#open), and the Windows deployment
that was never part of this plan.

The viewer and the MATLAB interface are the priority; the software PID is not.
Nothing here asks for work in `control/` except one seam, named where it comes up.

---

## The register

Stable IDs. Use them in commits and in conversation; the numbering of the first
draft is gone and is not worth reviving.

### Viewer — chart and axes

| ID | Feature | Behavior |
|---|---|---|
| **V1** | Live view windows | Buttons 6 h / 12 h / 24 h / 48 h, **24 h default**. No "All": scrolling back to find a run is acceptable. A button means a sliding window that rides with the newest sample. Dragging or zooming means a fixed window served from disk; no button is checked and the status bar says *not following*. Any button returns to live. |
| **V2** | Value-axis comfort stop | Zoom and pan on the value axis stop at 0–450 K and 0–100% — **unless the data goes outside them**, in which case the limit widens to the data. A miswired sensor reading 1400 K must stay reachable. Hardcoded in the viewer with a CLI override, in the shape of the existing `--max-points` / `--gap-factor`. No config key. X axis unaffected. |
| **C1** | Cursor region statistics | Two vertical cursors, brought into existence by a toggle button and moved by left-click. Between them, an in-plot stat panel reports **per trace: mean, standard deviation, Δvalue**, and once for the region **Δtime**. Statistics come from **full-resolution samples**, never from the decimated draw — `CsvTail.prepare_span` already re-reads a span from disk for this reason. With no cursors set, the legend shows the **live value**. |
| **C2** | Hover identify | Hovering a trace names it and gives its interpolated value at the cursor. Both panels, 3 dp. Independent of C1. |
| **E1** | Export the region | The cursor region, written out as CSV. |

### Viewer — readouts and command panel

| ID | Feature | Behavior |
|---|---|---|
| **L1** | Loop table | A loop-centric table **beneath** the existing per-channel readouts, which stay. One row per loop: loop, sensor, kelvin, setpoint, output, range. **Clicking a row selects that loop**, replacing the loop spin box for setpoint, range and PID alike. |
| **L2** | Loop → sensor and heater binding | From the instrument, via `OUTMODE? <output>` — which input a loop reads, and whether it is in closed loop, zone, open loop, monitor or off. **No `loop_heater_map` and no `loop_sensors` in config.** On the 33x family the loop number *is* the output number by protocol, so the heater binding is derived from `caps`, not configured. `OUTMODE` changes approximately never: read at startup and on a slow cadence, not every cycle. |
| **L3** | 336 loops 3–4 | Shown with their AOUT percent in the output column, range N/A. In the command panel, selecting such a loop reveals the analog grouping and hides the heater-range grouping. Only the relevant grouping is ever shown. |
| **R1** | Range follows the selected loop | No separate Output combo. The range control applies to the heater output the selected loop drives, pre-filled with that output's current range. |
| **W1** | Two warning marks | On the loop table, not the trace list. **Saturated** (output at rail, **fixed** at ≥99% / ≤1% — not per loop and not configurable) and **not settled** (\|T − SP\| over W2's per-loop threshold) are *separate marks*, never OR'd into one — "not at setpoint" is the normal state of a cooldown and an icon that is always lit is an icon nobody reads. Both are suppressed while the loop is not trying: range 0, `OUTMODE` mode other than closed loop, or `RAMPST?` reporting a deliberate traversal. |
| **W2** | Thresholds | A property of the loop, per loop — not global and not a viewer setting. 0.5 K is a tight tolerance at 4 K and a loose one at 300 K. Published in the status file so the viewer never parses config semantics. |
| **P1** | PID boxes | P, I, D per loop, tied to the same row selection as everything else. **Read by aux polling** (`{inst}.p{loop}`, `.i{loop}`, `.d{loop}`) on a slow cadence — the viewer holds no port and cannot query an instrument, and a command that returns data would be a new pattern for the spool, the CLI and MATLAB all at once. Written through a new `pid` command gated by **`ipc.allow_pid`**. `LS33x.set_pid()` already exists and verifies by readback. |

### Command interlocks

| ID | Feature | Behavior |
|---|---|---|
| **A1** | Per-source command policy | New `ipc.sources` section: per-source bool plus a `default` for unlisted clients. Immutable config. `Command.source` is **already carried end to end** and already populated (`matlab`, `lschart-gui`, `lschart-cli/<pid>`) — today it reaches `_execute` and is used only for a log line. Matched on the part before the first `/`, because the CLI stamps its pid into the string and no fixed key could ever match it. |
| **A2** | Runtime source toggle | Mutable per-source enable/disable, so an operator can say "programmatic control only" or "this terminal only" on the fly. A small `sources.json` in the IPC directory, re-read each cycle, torn reads tolerated — **not a command kind**, because a spool command that disables the viewer would leave the viewer no way to re-enable itself. **It may only ever narrow A1, never widen it**, and a restart clears it back to the config ceiling. Hand-editable, so a lockout never requires stopping the recorder and dropping the port. |
| **A3** | No exemption outside panic | The zero-exemptions are **removed**. Today `range 0` bypasses `ipc.allow_heater_range` and `analog 0` bypasses `ipc.allow_analog_output`, on the reasoning that the direction removing heat never needs another permission. That reasoning is retired: cutting heater power is not automatically safe on this cryostat — it stops heating and it can also crash the stage. After this change both gates apply in both directions, and the only exemptions anywhere are the panic actions in K1. |

`allow_writes` is **not** touched. It is driver policy on the instrument, it gates
every caller equally — the CLI, the IPC service, and `ltspm3`'s supervisor writing
in-process — and the software PID never passes through the spool at all, so it has
no source for a source policy to describe. A1/A2 are a sixth gate on a new axis:
the existing five ask *may this action happen*, these ask *may this client ask for it*.

**This is an interlock against habit and mistake, not against malice.** `source` is
self-declared in the command file; anything that can write to the spool can write any
label. That is the accepted trade.

### Panic actions

| ID | Feature | Behavior |
|---|---|---|
| **K1** | Panic menu | A "Panic" button opens a menu holding the two actions below; each then takes its own confirmation. **Three clicks by design** — these are needed almost never and must not be reachable by accident. The menu states plainly that these bypass **the source policy only**. |
| **K2** | All heaters OFF | As today, moved under K1. Bypasses A1/A2. |
| **K3** | All Temps Hold | **New `hold` command.** Per 33x loop: disable ramping first, preserving the configured rate, *then* set the setpoint to the loop's bound sensor temperature. Order matters — set the setpoint while ramping is still on and the instrument ramps to it instead of holding. Ramping is left off; silently restoring it would surprise whoever pressed the button. On the 218: freeze the analog output where it is and stop the software loop. Bypasses A1/A2. |

| **K4** | Resume after a hold | **New `arm` command**, and the way back from K3. Re-arms the software loop at a setpoint, defaulting to the temperature it is currently holding — resuming at the held temperature is what avoids handing the PID a step to chase. **Not a panic action and not exempt from anything**: arming a loop starts it driving the heater again, which is the power-applying direction, so it passes the source policy, `ipc.allow_analog_output` and `allow_writes` like any other write. On a recorder with no software loop it is a no-op that says so by name. Reached by the same duck-typed seam as K3; the supervisor's `arm()` is the method. |

**What the bypass does and does not cover.** The two panic kinds bypass the A1/A2
source policy **and** the per-kind power gates (`ipc.allow_heater_range`,
`ipc.allow_analog_output`). They do **not** bypass `ipc.accept_commands`,
`instrument.allow_writes` or `transport.read_only` — a box configured read-only stays
read-only and is named in the reply, as `_do_heaters_off` already does. The menu text
must say this rather than "bypasses interlocks", which would be a promise it does not
keep.

With A3, these are the **only** exemptions in the system. Everything else passes every
gate in both directions.

**The exemption belongs to the command kind, not to the GUI.** The recorder cannot tell
a menu press from a script — it sees the kind `heaters_off`, and MATLAB's `heatersOff()`
sends exactly that. So **MATLAB may send both panic commands** and gets the same bypass.
That is deliberate: an automated abort is a large part of why a panic command exists at
all. The panic menu is how the *viewer* makes these hard to hit by accident; it is not
what makes them privileged.

**Two honest things for K3's confirmation dialog.** Hold is not a synonym for less
power: while a ramp is heading *down*, its setpoint sits below the temperature the
cryostat has actually reached, so holding — which sets the setpoint to that reached
temperature — demands more heat than the ramp was demanding. It never raises a range, so it stays bounded by the power
already permitted. And Hold means two different things on the two boxes — a 33x loop
holds a *temperature* and keeps regulating; the 218 holds a *power* and nothing
regulates the sample afterward, so it will drift with the cryostat.

**Re-arming is bounded but not free.** If the cryostat has drifted during a hold, K4
hands the PID whatever error has accumulated. The supervisor's clamp and rate limiter
still apply, so the output cannot jump — which is exactly why K4 goes through `arm()`
rather than around it.

**The `ltspm3` seam.** The supervisor already models this state:
`SupervisorState.HOLDING` is *"output frozen pending clarity"*, and `abort_ramp()`,
`set_mode(MANUAL)` and `set_manual_percent()` compose into exactly a panic hold with the
clamp and rate limiter still in force. `arm()` is the way back. `lschart` reaches it by
duck-typed method on the controller object, read by name the way `status.py::_control`
already reads supervisor fields — so `lschart` still never imports `ltspm3`.

### Recorder-side changes these require

| ID | Change |
|---|---|
| **S1** | A `loops` array per link in `status.json`: `{loop, sensor, mode, heater_output, setpoint_k, output_pct, range, threshold_k}`. **An array of uniform objects, not an object keyed by loop number** — MATLAB's `jsondecode` runs object keys through `makeValidName`, so `{1: "Sample"}` arrives as `x1` and the name is mangled. `SCHEMA_VERSION` → 2; the viewer degrades for older recorders the way `capabilities()` already demonstrates. |
| **S2** | `OUTMODE?` polling on the 33x, slow cadence. Adds one transaction per output; `transactions_per_frame()` is the budget to update. |
| **S3** | P/I/D into the aux block, slow cadence (P1). |
| **S4** | New command kinds `pid`, `hold` and `arm`; new gate `ipc.allow_pid`; new section `ipc.sources`; the `sources.json` watcher. Each new kind needs a CLI verb and a `LakeShore.m` method, and `selftest.m` extended to cover them. |
| **S5** | Per-loop `threshold_k` in the instrument's config block, republished in S1. |
| **S6** | A3's fallout, which is mostly not code. **`CLAUDE.md` invariant 3 must be rewritten** — its last sentence, "Turning a heater **off** needs neither of them", becomes false. The two `CommandError` messages in `_do_range` and `_do_analog` both end by promising that zero is always allowed. The viewer's gate note says "Setting 0 still works." `docs/recorder/instruments.md`, `docs/recorder/file-interface.md` and the MATLAB README repeat the claim and need checking. `tests/test_ipc_service.py` carries ~33 references to these gates, several of which assert the exemption and will invert. |

---

## Priority

**Phase 1 — viewer only, no recorder change, nothing on the bus.**
V1, V2, C1, C2, E1. Self-contained and independently shippable.

**Phase 2 — status and `OUTMODE`.**
S1, S2, S5 → then L2, L1, L3, R1, W1, W2. L2 is the keystone: L1, L3, R1, W1 and K3
all need to know which sensor a loop reads, and nothing else can tell them correctly.

**Phase 3 — the write path.**
A1 → A2 → P1 (with S3, S4) → K1, K2, K3, K4 → A3 (with S6). Ordered so the source policy
exists before the panic actions that bypass it, and K3 before A3 because A3 removes the
last exemption that is not a panic action — the panic path should exist and work before
the fallback it replaces is taken away.

Phases 1 and 2 touch no write path. Phase 3 does, and every item in it is invariant
territory: read invariants 3, 4 and 5 in `CLAUDE.md` before starting it.

---

## Where the work lands

Written for a session that has not read the tree. Line numbers drift; the names do not.

### Phase 1 — viewer only

| ID | Files and entry points |
|---|---|
| **V1** | `gui/window.py`: `VIEW_WINDOWS`, `_left_panel()` (drop `live_button`), `_follow_live`, `_set_follow`, `_sync_view_buttons`, `_update_statusbar`. Note `BACKFILL_COVERAGE_S` is derived from `VIEW_WINDOWS[-1]` and should stay derived. `_redraw()`'s `_follow_span_s is None` branch is the "All" path and goes with it. |
| **V2** | `gui/window.py`: `_plots()` — `ViewBox.setLimits()` per panel, which constrains pan as well as zoom. Interacts with `_y_range_changed` and `_zoom_y`. New flags in `gui/__main__.py` beside `--max-points` / `--gap-factor`. |
| **C1** | Cursor items in `_plots()`. **Read `ZoomViewBox` first**: left-drag is already the zoom-rectangle gesture and left-click already reaches `mouseClickEvent`. The toggle button has to reassign the gesture while cursors are live, without breaking the drag when they are not. Statistics go in `gui/source.py`, not `window.py` — that module has no Qt and is what the tests cover. Full-resolution samples come from `CsvTail.prepare_span` then `between`; the one-tick arm-then-load dance in `refresh()` is the existing pattern for that. |
| **C2** | `pg.SignalProxy` on the scene's `sigMouseMoved` in `_plots()`; nearest-sample lookup in `source.py` for the same reason as C1. |
| **E1** | Writer in `source.py`; the button in `window.py`. |

### Phase 2 — status and `OUTMODE`

| ID | Files and entry points |
|---|---|
| **S2** | `instruments/ls33x.py`: an `outmode(output)` reader beside `heater_range()`, a slow-cadence path into aux, and `transactions_per_frame()` updated. **`instruments/sim.py` must learn to answer `OUTMODE?`** or none of the sim-backed tests will cover the new path. |
| **S1** | `ipc/status.py`: a `_loops()` beside `_capabilities()`, `SCHEMA_VERSION` → 2. Arrays of uniform objects — re-read the module docstring on why. |
| **S5** | `config.py`: `LS33xConfig`, per-loop threshold. Unknown keys are an error, so the schema has to accept it before a config can carry it. |
| **L1, L3, R1, W1, W2** | `gui/window.py`: `_left_panel()` for the table, `_setpoint_group()` (drop `loop_spin`), `_range_group()` (drop `heater_combo`), `_instrument_changed()`, `_sync_command_values()`. `gui/source.py`: extend `capabilities()`, which already carries the degrade-for-old-recorders pattern to copy. |

### Phase 3 — the write path

| ID | Files and entry points |
|---|---|
| **A1** | `config.py`: `IpcConfig`. `ipc/service.py`: the check goes in `_execute` before the `_do_*` dispatch. |
| **A2** | `ipc/`: the `sources.json` watcher, re-read per cycle, torn reads tolerated — `read_status()` is the model. |
| **P1** | `ipc/service.py` `_do_pid`; `ls33x.py` P/I/D into aux; `config.py` `allow_pid`; `__main__.py` send subparser; `matlab/LakeShore.m` `setPID` **and `selftest.m`**. |
| **K1–K4** | `gui/window.py`: `_command_box()` for the menu; K4 belongs outside it, being the power-applying direction. `ipc/service.py` `_do_hold`, `_do_arm`. The `ltspm3` seam is duck-typed on the controller object `app.py` already holds. |
| **A3** | Two conditions in `_do_range` / `_do_analog`, then all of S6. |

### Landmines

- **`lschart` never imports `ltspm3`.** The K3 seam is duck-typed for this reason.
- **A skipped test fails the build.** Every new test must run in CI or be conditional on something CI provides.
- **No test may depend on the working directory or the time of day.** Both have already bitten; see the note in `CLAUDE.md`.
- **`sim.py` is cryostat-agnostic.** New queries need fakes there, and the calibrated LTSPM3 response stays injected from `ltspm3/`.
- **`ruff check .` gates CI** — `F`, `E9`, `E501` at 100 columns.
- Every new command kind needs four things, not one: the handler, a CLI verb, a `LakeShore.m` method, and `selftest.m` coverage.

### Baseline as of 2026-08-27

`408 passed` when this plan was written; `560 passed` with all three phases in.
`ruff` clean throughout.

---

## What changed from the first draft

Recorded because the concern was that intent had been flattened, and most of it had.

1. **"Legend shows current value" and "hover tooltip" were one feature, cut in half.**
   The intent was cursor region statistics (C1); a live number in the legend answers a
   question the readouts panel already answers. Hover identify (C2) is genuinely
   separate and survives on its own.
2. **The loop table would have deleted thermometers.** "218: 1 row" turns an
   eight-input monitor into one channel, when recording every thermometer continuously
   is the recorder's stated job. L1 adds a table; it does not replace one.
3. **The warning icon would have been lit permanently.** OR-ing "not at setpoint" into
   it guaranteed it. W1 splits the marks and suppresses them when the loop is not trying.
4. **Three features sourced from config what the instrument knows.** `loop_heater_map`
   and `loop_sensors` are `OUTMODE?` and the 33x command set. Config here could only go
   stale or lie.
5. **The status shape would have been mangled by MATLAB.** Corrected to arrays.
6. **A hard axis clamp could hide a bad reading**, against the rule that the measured
   number wins. Corrected to a comfort stop.
7. **"Get PID" cannot be a button.** The viewer holds no port. Corrected to aux polling.
8. **"Sensor channel label under the loop selector" is now redundant** — the loop
   selector is gone, replaced by row selection in L1, and the row carries the sensor.
9. **Not in the first draft at all:** A1, A2, E1, K1, K3, and the correction that
   turning heaters off is not automatically the safe direction.

The last one is worth its own line, and A3 resolves it. `lschart` currently assumes
zero is safe in four places — invariant 3's wording, the zero-exemptions in `_do_range`
and `_do_analog`, and the panic button's "never gated" comment. `ltspm3` already
disagreed: the supervisor commands a configured `safe_output_pct` on a fault rather
than zero. A3 brings `lschart` into line, and the fallout is listed as S6 because it
reaches further than the code.

---

## Open

1. **X1 — the software loop's own state in the loop table.** Wanted eventually,
   deferred deliberately. `status.json` already carries `control` beside
   `links[].loops`; what is missing is a row for it in the viewer's table, and
   a decision about what its "sensor" and "range" columns should say for a loop
   that has neither.

## Where phase 3 differed from the plan

Three things were decided at the keyboard and are worth recording, because the
plan's text does not describe what was built.

1. **`read_pid` defaults to *off*.** The plan does not name a default. It cannot
   be on: the shipped 218 + 336 config sits at 19 transactions against a 1 s
   cadence at 50 ms pacing, and one `PID?` per loop does not fit — `check`
   refuses a cadence a cycle cannot fit. The example configs turn it on at 2 s.
   Where it is off the viewer's boxes are blank and name the key that fills them.
2. **The `ltspm3` seam is `panic_hold()`, and the state it leaves is
   `IDLE`/`MANUAL`, not `HOLDING`.** The plan quoted `SupervisorState.HOLDING`
   ("output frozen pending clarity"), but in the code that state is a transient
   inside the PID branch and `update()`'s manual branch overwrites it every
   cycle. `abort_ramp()` + `set_mode(MANUAL)` is the mechanism the plan
   actually described, and one name for the pair is what was added.
3. **The panic menu had to leave the command group.** In Qt a child of a
   disabled parent is disabled however firmly it is enabled, so a panic button
   inside the group the source policy switches off is a button that lies. It
   also had to leave `_buttons()`, because no pending command can make it wrong
   to stop.

And one defect found on the way, unrelated to the plan: `examples/config-336-usb.yaml`
did not validate. `read_analog_outputs: true` had been added without raising its
cadence, so the file a coworker installs was refused by `check`. It now runs at
2 s, and `tests/test_config.py` checks every shipped example loads and fits its
own cadence — nothing did before.
