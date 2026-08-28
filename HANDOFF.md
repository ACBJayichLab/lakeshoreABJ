# Handoff — 2026-08-27 (ninth session: phase 3, the write path)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.

**All three phases of [`FEATURE_PLAN.md`](FEATURE_PLAN.md) are implemented and
tested. 560 tests passing (from 472), ruff clean.** Verified against a live sim
recorder, a real `ltspm3` software loop, and MATLAB R2025b. **No hardware was
touched this session** — the bench 336 was not connected.

## Start here

Phase 3 is done, so the feature plan is no longer a to-do list; it is a record
of why things are shaped the way they are, plus a short
**Where phase 3 differed from the plan** section at the end that is worth
reading before you touch any of it.

**What is left is Windows deployment**, which was never part of that plan, and
X1 (the software loop's own row in the loop table), which was deferred
deliberately.

**One thing needs a decision from Jeff before the LTSPM3 cryostat runs armed —
see [A config that contradicts itself](#a-config-that-contradicts-itself).**

## What landed

Four commits, in the order the plan's priority section gives.

### A1, A2 — a sixth gate, asking *who* is asking (`56fdf2e`)

The five interlocks all answer "may this action happen"; none can say "the
operator at this terminal may drive the cryostat, the analysis script may not".
`Command.source` had been carried end to end since the spool was written and
used for nothing but a log line. This is what it was for.

- `ipc.sources` is the ceiling, fixed for the process. `sources.json` beside the
  status file is a runtime overlay, re-read every cycle, **may only ever
  narrow**. A restart always returns to the audited config.
- A file and not a command kind, deliberately: a *command* that disabled the
  viewer would leave the viewer no way to re-enable itself.
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

## A config that contradicts itself

**Found while auditing S6, not caused by this session, and not fixed — because
fixing it either way is a decision about a real cryostat.**

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

- `560 passed`, `ruff` clean, on `main`. Nothing is running.
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
- **MATLAB R2025b** ran `selftest.m` end to end against a live recorder.
