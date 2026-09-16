# Handoff — 2026-09-15b (the monitor soaked; two gates shrank; a document retired)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
route to a working loop is [PID_PLAN.md](PID_PLAN.md) and the commissioning
step-by-step is [plans/pid-4-commissioning.md](plans/pid-4-commissioning.md).
This goes stale.

Previous: [HANDOFF-2026-09-15a.md](HANDOFF-2026-09-15a.md) (the phase 3 review
fixes).

> ## NOTHING WAS COMMANDED, AND THE HEATER IS WHERE IT WAS
>
> The 218's analog output has been at **64.0100 %** since 2026-09-10 14:49 and
> the sample flat at **118.3 K**. **No `control:` section has ever been armed on
> this cryostat.** The monitor ran, and it holds no port and sends no commands.
> **1210 tests passing, `ruff` clean** — and one pre-existing failure, below.

## What this session did

No code changed. Three conclusions were reached by reading the code against the
documents, and the documents lost.

### 1. Phase 2's live soak is done, and the gate is a day rather than 72 h

Jeff ran the monitor on 2026-09-15. **The 72 h was a round number with nothing
behind it** — plan 2 stated it and never derived it. What establishes the
false-alarm rate is the replay across 63 days of archive, green in `pytest`.
What the live run adds is that the tail path works on that machine, that
`plant.json` stays current, and that it survives.

The defensible length is **one diurnal cycle**: a 16 mK diurnal term is in the
band and `measure.py` fits a 24 h harmonic, so a day is the longest measured
timescale short of the campaign drift — which no soak of any length covers.

**And the judge's state is reconstructed from the recorder's log, not from the
monitor's own uptime.** Started on 09-15 it read the 09-14 file from the top and
caught up in seconds. The evidence is in the CSV the recorder has been writing
all along, which is what makes the shorter gate defensible rather than merely
convenient.

### 2. W1 is three commands, not a characterisation, and nothing is blocked

`write_settle_s` is **not a command cadence**. It is the transport's pacing gap
between a write and the next transaction on that link — `_pace()` applies it
only when the last transaction was a write — so a write and its confirming
readback sit back to back inside one 2 s cycle. Nothing is issued at sub-cycle
intervals, and the old W1's 0/25/50/80/150/300 ms sweep was asking for timing
the architecture does not use.

What the check is worth is narrower than invariant 5 claimed, and the narrowing
is the useful half:

- the comparison is against the value **just commanded**, so a stale readback
  returns the old value, misses by the whole step, and is caught rather than
  confirmed. "Both wrong regimes look like success" predates that;
- **below `readback_tol_pct` the check is undecidable at any settle time**,
  because stale and fresh are the same number. That is every hold write under
  `dither: true` (one 0.01 % code, against a 0.015 % tolerance) and every ramp
  write at 118 K (5 K/min ÷ 13.8 K/% = 0.012 % per cycle);
- **it bites at the cold end**, where the gain falls toward 0.35 K/% and the
  same rate is ~0.48 % per cycle.

So `verify_readback` will be silent through the whole of 4a, and silent by
arithmetic rather than by passing. Three `send analog` steps above the tolerance
answer "is 100 ms enough", with the recorder running and no downtime.

### 3. `docs/ltspm3/commissioning.md` is retired

964 lines, written 2026-09-03 and overtaken three times. A dated status block
describing the sample at 180.57 K on 69.027 %; a two-rate ramp-down with a knee
at 40 % that phase 3 step 5 replaced with one rate in kelvin; the W1 above; and
gates met months ago. The durable half went to four homes — the step-test
method and the R² table to [thermal-response](docs/ltspm3/thermal-response.md),
the `AOUT?` flicker to [cryostat](docs/ltspm3/cryostat.md), the band principle
was already in [running](docs/ltspm3/running.md), and the plan half plus C1–C7
to [plans/pid-4-commissioning.md](plans/pid-4-commissioning.md).

**Archived `HANDOFF-*` and `AUDIT-*` references still point at the deleted
file, on purpose**: they are records of what was true when written.

## One finding, not diagnosed

**Two residuals go blind at the daily file roll.** At exactly
`2026-09-15T00:00:00`, `cold_head` → `no opinion (no cold-head reading)` and
`noise` → `no opinion (not enough samples)`. `noise` recovers in 15 s, which is
`RollingFit(noise_window_s = 60)` refilling and is expected. **`cold_head`
takes 616 s**, which is not explained by that and lines up suspiciously with
`stage_baseline_tau_s = 600`.

It fails safe — `no opinion` is the honest answer and never reads as green — so
it bears on nothing about arming. It bears on **stage 5**: seven unattended days
is seven midnights. Two candidates, neither checked: the 336's aux columns
absent from the new file's opening rows, or the stage fits/baselines resetting
across the rollover. It wants a test that rolls a file under the judge.
plans/pid-2-monitor.md carries the row.

## Then, in order

1. **Phase 4 stage 3 close-out.** The write check (three commands, recorder
   running) and W2's ceilings — `send analog 70.5` refused, `analog 0` refused
   with the gate closed, `heaters_off` reaching the box. **`heaters_off` takes
   the sample off 118 K**, so pick the day for that one.
2. **4a — the first armed hour**, `authority_pct` narrowed to 0.1, tuning and
   feedforward off, armed with no `--setpoint` from a hold that has settled.
   **The first `arm` is Jeff's to type.**
3. **4b** the fault drill, **4c** widen one thing per watched hour, **4d** the
   5 K/min sweep.
4. Before stage 5: the midnight blind spot above.
5. `lschart status` and MATLAB `plant()` still do not read `plant.json`.
6. The 09-10 mask still goes in with the next archive export.

## One pre-existing test failure, and it is not from this session

`tests/test_gui_window.py::test_a_burst_of_view_changes_costs_one_redraw` fails
on this Mac with `assert 6 == 3` — a second redraw pass after `processEvents()`.
**1210 passed, 1 failed.** It failed identically on a branch three weeks older
whose GUI history is entirely different, and nothing in this session touches a
file under `lschart/gui/`. CI is green on Linux/Windows, so it reads as a
platform difference rather than a regression — but it is failing, it is not
written down anywhere current, and it should be either fixed or recorded as
known before it becomes normal.

## Traps this session added

- **A round number in a plan is not a measurement.** The 72 h had been quoted
  forward through three documents and derived in none of them. Ask what
  timescale a gate is supposed to cover before spending days on it.
- **"Both wrong regimes look like success" stopped being true when the
  comparison changed**, and the sentence outlived the code by weeks in an
  invariant. A claim about a failure mode has to name the code that produces
  it, or it cannot be rechecked when that code moves.
- **A check can be silent by arithmetic.** `verify_readback` passes at a hold
  because every write is below its own tolerance, not because the write landed.
  A green check whose inputs cannot differ is not evidence.
- **A dated current-state block in a long-lived document has now been wrong
  three times** — twice in the config headers, once in the document retired
  here. Current state goes in `HANDOFF.md`, which is archived under its date.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ltspm3.monitor -c config-ltspm3-heater.yaml
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ltspm3 -c config-ltspm3-armed.yaml check
```

`analysis/` still needs `measure.py` run once (11 s) before anything that fits.
