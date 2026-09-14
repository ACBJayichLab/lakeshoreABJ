# PID Phase 2 — the monitor

Part of [PID_PLAN.md](../PID_PLAN.md). **Goal:** a judge outside the loop
that reads the recorder's files, applies the model's band, catches both
archive events as warnings, and warns about nothing else.

`ltspm3/monitor/`. A separate process like the viewer: no port, no
commands, ever. Runs whether or not the loop is armed — which is most of the
cryostat's life so far — and is the reference implementation the supervisor's
in-loop check (plan 3 §3.4) is tested against.

**Status: BUILT 2026-09-14.** `ltspm3/monitor/` — `source.py` (a live tail and
an archive replay, one `Sample` shape), `judge.py` (the residuals and the
verdicts), `report.py` (`plant.json` and the daily CSV), `__main__.py`.
27 tests, of which 11 are the replay on genuine data. **The 72 h live soak is
what is left**, and the replay table is green except for one row — see §2.4.

### The one design decision this phase added

**Jeff, 2026-09-14: recalibrate at most once per cooldown, and typical erroring
behaviour is so large as to be unmistakable from these variations.**

That makes the trailing baseline mandatory rather than optional, and it decides
what the baseline is kept in.

`missing_power_w`'s *level* carries two slow things: the calibration
(`bias_q_w`, 4.7 mW at 118 K) and the campaign drift (0.29 mW/day, unmodelled
and therefore unsubtracted). Over a months-long cooldown the full band reaches
52 mW — 31 K — and judges nothing. So the judge alarms on the **departure from
a slow baseline**, against `sigma_q_fast_w`, which does not grow with time at
all. One gauge per cooldown is then enough.

Two rules make that safe: the baseline **freezes** whenever the verdict is not
typical, or it learns the fault it is judging; and it is slow against a fault
and fast against the drift — 6 h, against a fault that declares itself in 30
minutes and a drift that takes days.

**The baseline is kept as a fraction of the delivered power**, and choosing
that variable took two wrong answers first:

| kept in | a 1 % calibration error is | why it fails |
|---|---|---|
| watts | 12 mW at 18 K, 20 mW at 94 K | the baseline chases the sweep |
| kelvin | 0.1 K at 18 K, 2.6 K at 94 K | a factor of **26** — Λ′ falls tenfold while P doubles |
| **fraction of P** | **the same number everywhere** | it is what a series resistance in a voltage-driven heater *is* |

Kelvin is the trap: the model's **shape** error is flat in kelvin (`band.py`
measured 0.135 K), so it looks right up until the thing being absorbed is the
**level** instead. Shape error and level error are different quantities with
different shapes.

## 2.1 Inputs and outputs

| | |
|---|---|
| reads | the recorder's CSV tail (`t` from the monotonic `Time` column, never the timestamp — AUDIT-2026-09-10 finding 4); `status.json` for `control` |
| writes | `plant.json` beside `status.json`: arrays not objects, `SCHEMA_VERSION`, `os.replace`; `plant_YYYY-MM-DD.csv`, one row per cycle |
| config | its own `monitor:` section: `warn_sigma: 3`, `warn_after_s`, `settle_taus: 3`, `window_s` |

## 2.2 Per cycle

| residual | computed | band | verdict |
|---|---|---|---|
| `δQ` | `model.missing_power_w` with `dT/dt` from a regressed slope | `model.sigma_q_w` | typical / warn / no opinion |
| `δT_c` | coldplate − locus, 175 s pole; 1st/2nd stage beside it | 27.6 mK rms | typical / warn |
| τ ratio | pole fit after a heater move holds, against `tau_s(T)` | 0.85–1.10 | typical / warn / no opinion below 25 K |
| noise | trailing rms vs `1.36e-6·T²`, floor 1.8 mK | < 2× | typical / warn |
| fault-level | `δQ` beyond `fault_mw` — **reported**, never acted on | seed 8 mW | flag only |

Persistence: a verdict changes only after `warn_after_s` out of band. No
opinion within `settle_taus × τ(T)` of a heater move, inside a mask, outside
the table, below 28 % output, or while `δT_c` is atypical (one cause, one
alarm).

## 2.3 The replay — the test on genuine data

`python -m ltspm3.monitor --replay reference/cooldown-10/` prints every
verdict change with its time. Pinned in `tests_ltspm3/test_monitor.py`:

| window | must |
|---|---|
| 2026-09-09 18:06 | `δT_c` warn within 30 min; `δQ` typical or no opinion |
| 2026-09-10 11:33 | `δQ` warn within 30 min, −5 ± 2 mW; **not** fault-level; `δT_c` typical |
| three post-recal holds | typical; `δQ` within ±1 mW of the drift line |
| `trace-ladder-20260905` | no warn; ≥ 27 of 30 rungs return a τ ratio in 0.85–1.10 above 40 K |
| `trace-sweep-20260902` | no warn outside masked windows |
| 2026-09-04 12:07 recalibration | no verdict change |
| **whole archive** | **zero** fault-level flags — this is what sets `fault_mw` |

**False-alarm budget: < 1 warning per week on a settled hold.** Every verdict
change in the replay is read by a person once; that is this phase's pause.

## 2.4 What the replay actually says — 2026-09-14

`python -m ltspm3.monitor --replay reference/cooldown-10/`, 1,063,448 samples,
about 30 s. Pinned in `tests_ltspm3/test_monitor.py`.

| the table's row | result |
|---|---|
| 2026-09-09 18:06: `δT_c` warn within 30 min | **NOT MET, and deliberately** — see below |
| …and `δQ` typical or no opinion | **PASS** |
| 2026-09-10 11:33: `δQ` warn within 30 min, −5 ± 2 mW | **PASS** — warns at **11:44**, eleven minutes, **−5.01 mW** (−0.75 % of delivered) |
| …and **not** fault-level | **PASS** |
| …and `δT_c` typical | **PASS** — the sink did not move, the heater did |
| 2026-09-04 12:07 recalibration: no verdict change | **PASS** — a *reading* changed and the cryostat did not |
| three post-recal holds: typical | **PASS** — 106 h of settled hold, no warning |
| `trace-sweep-20260902`: no warn | **PASS** — the fit's own training data reads as typical |
| `trace-ladder-20260905`: ≥ 27 of 30 rungs with τ in 0.85–1.10 above 40 K | **NOT MET** — see below |
| whole archive: **zero** fault-level flags | **NOT MET** — two, and both are real |

**The 2026-09-09 row, and it is the most interesting thing this phase found.**
The row asks `δT_c` to warn within 30 minutes. It cannot, and the reason is not
a shortcoming of the check — it is that **the event is not visible in the
channels the monitor has.**

The manifest measured it: at a fixed output the cold-head channels stepped at
18:06:04 by **Coldplate −7.6 mK, 1st Stage −80 mK, 2nd Stage −19 mK**, and the
sample then fell **0.37 K over three hours**. The coldplate step is a quarter
of that channel's own rms and a tenth of its band, so `δT_c` cannot see it and
should not: a bar that saw 7.6 mK would warn continuously.

So the stages were added beside it, as §2.2 asked — `cold_head`, a step
detector on the 1st and 2nd Stage against their own recent scatter. It **does**
catch the event, at 18:17, eleven minutes. And then the replay answered the
question the row had not asked: **a 70–170 mK step on the 1st Stage happens
five times in the four settled days before the event and six times in the
disturbed day and a half after it.** The 09-09 step is 90 mK. It is not
exceptional in the only channel that shows it.

That is Jeff's ruling of 2026-09-14 arriving from the data rather than from the
instruction: *typical erroring behaviour is so large as to be unmistakable from
these kinds of variation.* A cold-head step of a tenth of a kelvin, several
times a week, **is** these kinds of variation. Tuned to catch 09-09 the check
costs nine warnings a week against a budget of one; set where ordinary steps
are quiet it is silent across the whole archive, and it still catches the
failure it exists for — a compressor degrading moves these channels by
**kelvins**, not by a tenth of one.

So the bar is set where the archive is quiet, and 09-09 is not caught. What
stays on the record instead is the manifest's own conclusion, which this
replay independently confirms: *something moves the sample's steady state at
constant power that `Λ(T_s) − Λ(T_c)` cannot see.* It is REFIT_PLAN §2.5's
open diagnostic, not a monitor bug, and no threshold in this file closes it.

**One thing the cold-head check corroborates on its way past.** Five steps in
four settled days before the fault; six in the one and a half disturbed days
after the reseat — four times the rate. HANDOFF-2026-09-13 measured the same
period as carrying **twice the wander over 300–1200 s**, by a completely
different method. Two independent signatures, same conclusion: reseated is not
repaired.

**The τ row.** Sixteen rungs above 40 K return a ratio at all; twelve are in
band. The shape is not noise and it is worth reading:

```
45-48 K 0.84   57-58 K 0.90   63-64 K 0.97   70-71 K 0.98   77-103 K 1.01
```

Above 60 K it is 0.97–1.01 — better than the row asks for. Between 45 and 58 K
it is systematically **low by 10–16 %**, and waiting longer makes it worse, not
better: at a reach of 5 the same rungs read the same and three of them stop
returning a ratio at all, and at 8 the ladder's dwells are too short to reach it
and **nothing** is measured. So this is not a settling problem. It is the bottom
of where a pole is measurable against a 2–4 s cadence and a dwell sized by
`plan_sweep` for the *steady state* rather than for τ.

It is left as it is rather than tuned into passing. The refit's own graded τ
agree to 6.7 % worst over 40–120 K (`analysis/plot_tau.py`), so the model is not
what is 16 % out here — the monitor's one-pass pole fit on a short dwell is. And
τ is a **secondary** indicator: it never faults, and it is not what catches a
heater event. Widening the band to 0.80 would make the row pass and would mean
nothing.

**The fault-level row, and this one is a finding rather than a defect.** The two
flags are **2026-09-04 11:34 (−21.9 mW)** and **2026-09-10 14:42 (−11.6 mW)** —
the wire reseat and the post-fault reseat. Both are real events on the heater
circuit, both are larger than `fault_mw` = 8 mW, and a monitor that stayed quiet
through them would be the broken one. The row was written expecting the archive
to contain no fault-sized excursions; it contains two, and REFIT_PLAN §7.2
measured them independently at a few tenths of an ohm each.

So **the archive does not set `fault_mw` by being clean.** What it says is that
8 mW is below the size of a connector being reseated, which is the number plan 3
needs before it turns `fault_mw` into a ramp-down: a threshold there would ramp
the cryostat down every time somebody touched the wiring. That is a decision for
Jeff at plan 3 §3.4, with the two measured events in front of it, and it is the
one thing this phase hands forward rather than settles.

## Exit gate

- The replay table green in `pytest`; `fault_mw` and `warn_after_s` written
  into config from what the replay required. **PARTLY MET 2026-09-14** -- six of
  eight rows green, and the two that are not are §2.4's, one of them a finding
  rather than a defect. `warn_after_s` is 600 s, which is what puts the 09-10
  event at eleven minutes rather than at two; `fault_mw` is **handed to plan 3
  unset**, because the archive says 8 mW is smaller than a reseated connector.
- 72 h beside the live recorder, `plant.json` read by `lschart status` and
  MATLAB `plant()`, the post-repair residual inside the band throughout.
  **NOT STARTED.** It needs the recorder restarted with the monitor beside it,
  which is Jeff's to schedule; nothing about it is blocked on code.
- `lschart status` and MATLAB `plant()` do not read `plant.json` yet. That is
  phase 5's work in the viewer and a ten-line reader in `LakeShore.m`; neither
  blocks the soak, which only needs the file to exist and be current.
