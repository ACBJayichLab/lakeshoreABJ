# Handoff — 2026-09-17, afternoon (the goal was written down, and the loop retuned to it)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; **the
loop's requirements are [docs/ltspm3/requirements.md](docs/ltspm3/requirements.md)**,
the route is [PID_PLAN.md](PID_PLAN.md), and the commissioning step-by-step is
[plans/pid-4-commissioning.md](plans/pid-4-commissioning.md). This goes stale.

Previous: [archive/HANDOFF-2026-09-17a.md](archive/HANDOFF-2026-09-17a.md)
(the morning: the hold graded 5.6× worse than open loop, the first setpoint
move, "a rate is a ceiling"). Every older dated document is under
[archive/](archive/) now.

> ## STATE: the cryostat is ARMED on the MORNING's numbers. This tree is not running on it.
>
> Recorder started 11:13, armed at 116.88 K, commanded to 119.0 K at ~11:15.
> It runs `hold_speed: 12`, `move_speed: 0.5`, a band of ±0.25 % **pinned at
> 63.96 %** — the file as it was this morning. Everything below is in `main`
> and in this file, and reaches the cryostat only when the recorder is
> restarted and re-armed. **Ask it, do not read it here:**
>
> ```bash
> python -m lschart -c config-ltspm3-armed.yaml status
> ```
>
> **The first move on the new numbers is Jeff's to command**, viewer open:
>
> ```bash
> python -m lschart -c config-ltspm3-armed.yaml send hold
> ```
> stop the recorder, `git pull` in the main checkout, then
> ```bash
> python -m ltspm3 -c config-ltspm3-armed.yaml check
> ```
> ```bash
> python -m ltspm3 -c config-ltspm3-armed.yaml run --arm
> ```
> ```bash
> python -m lschart -c config-ltspm3-armed.yaml send setpoint 120 --software
> ```
> The bench says 4.6 minutes to within 100 mK. The recorder's CSV says what
> the cryostat did.

## What this session established

### 1. The goal had drifted, and the fix was to ask

Jeff noticed the 2 K test ramp was taking 25 minutes and that this was the
opposite of the point. Eight questions, eight answers, recorded verbatim in
[requirements.md](docs/ltspm3/requirements.md). The two that changed the design:

* **2 K at 118 K in 5 minutes.** The morning's "a rate is a ceiling, not a
  promise" was a true statement about the loop as shipped, not a requirement.
  `move_speed: 0.5` put a 260 s trajectory corner on a 260 s closed loop, and
  the two stacked into a move as slow as a hand step.
* **The band follows the setpoint, and is not the "is the power wrong" check.**
  With the feedforward term off — the armed configuration — the band's centre
  was pinned at `operating_point_pct`, and a 2 K move at 140 K **faulted on the
  bench** (`authority exhausted`, heater ramped down; at 180 K the output went to
  zero). `target_band_centre_pct` asks `has_curve` now, the same seam the
  ramp-down and the rate limiter use since the 09-16 audit.

Also: strong hold, not weak; 5 K/min is a safety ceiling; filter unchanged;
his day-to-day use is typed setpoints and programmed ladders with a measurement
at each rung.

### 2. What the bench says about the retune

`tests_ltspm3/test_stage_4d_fast_move.py`, all on the fitted plant with the
heater delivering 0.336 % less than the model claims:

| | morning's file | this file |
|---|---|---|
| 2 K at 118 K, to 95 % | > 30 min (cryostat: 86 % at 19 min) | **4.6 min**, 7 mK over |
| 2 K at 100 / 140 / 180 K | 140 and 180 **faulted** | 3.8 / 5.0 / 4.9 min |
| 10 K at 118 K | — | 16 min, never railed |
| hold, Allan ratio to open loop, 15 s / 60 s / 300 s / 900 s | 0.98 / 0.97 / 1.10 / **2.15** | 1.00 / 0.97 / 0.81 / **0.71** |

Three config numbers: `authority_pct` 0.25 → **1.0**, `move_speed` 0.5 →
**0.15**, `hold_speed` 12 → **0.25**. Raising the 5 K/min ceiling to 10 or 20
was measured **worse** (tripped `anomaly_demand_pct` at 60 K, slower at 118 K),
so it stays. The full tables are in the config's comments.

### 3. What the bench cannot say

Its plant has white sensor noise and no slow disturbance, so it grades the move
and the short-tau half of the hold, and **cannot grade the slow wander that is
the hold's whole point**. That is `analysis/hold_quality.py` against the
2026-09-15 open-loop night, after a night armed on the new numbers.

## What is in the tree that was not this morning

| | |
|---|---|
| `docs/ltspm3/requirements.md` | **the requirements, in Jeff's words.** Change §1 only by asking him |
| `ltspm3/control/supervisor.py` | `target_band_centre_pct` follows the setpoint whenever a curve exists (rule 5) |
| `config-ltspm3-armed.yaml` | the three numbers above, comments rewritten to the measurements |
| `tests_ltspm3/test_stage_4d_fast_move.py` | Jeff's benchmarks on the bench, the 60 K overshoot pinned as an open item |
| `tests_ltspm3/test_stage_4a.py`, `test_stage_4c_tuning.py` | re-graded for a band that follows; a new test for the band that follows with the term off; a new test for a band narrower than the level error cutting the heater at arming |
| `lschart/__main__.py` | `check` no longer calls the band FIXED when the feedforward term is off |
| `archive/` | 13 handoffs, 5 audits and reviews, the finished feature plan. Links fixed |
| `PID_PLAN.md` §1, `CLAUDE.md`, `docs/ltspm3/README.md`, `control.md`, `safety.md` rule 5, `running.md`, `plans/pid-4-commissioning.md` 4d, `docs/recorder/cli.md` | the sweep |

## What needs doing, in the order I would take it

### A. Arm on the new numbers and move 2 K — Jeff

The block at the top. Compare the CSV with 4.6 min. If it is much slower, the
first suspect is the band's lead not widening (`ramp_lead_pct` is gated on
`tuner.enabled`, which is on) and the second is the output rate limiter —
`check` prints both. Do not retune from one move.

### B. A night armed on the new hold, graded

```bash
python -m analysis.hold_quality --csv "data/ltspm3-armed_2026-09-17.csv" "data/ltspm3-armed_2026-09-18.csv" --from 2026-09-17T18:00 --to 2026-09-18T09:00 --vs "data/ltspm3-heater_2026-09-15.csv" "data/ltspm3-heater_2026-09-16.csv" --vs-from 2026-09-15T18:00 --vs-to 2026-09-16T09:00
```

Target, from requirements §2: ratio ≤ 1 at 15 s, 1 min and 5 min, and well
under 1 at 39 min. **Keep the 09-15 night as `--vs` forever.**

### C. The ladder tool — Jeff's use case 6

A sequence of setpoints with a dwell at each, through the command spool the way
`ltspm3/tools/sweep.py` drives heater percents. It is a move followed by a hold,
repeated, and both halves are now graded. Belongs in `ltspm3/tools/`, with the
table at the end; MATLAB gets the same via `setpoint --software` once
`LakeShore.m` has a method for it (item E of the morning handoff, still open).

### D. 60 K overshoots 0.39 K

Rate-limiter windup: the 5 K/min output limit is 0.8 %/min there, the corner
is 22 s, and the integral winds up behind the limiter. Back-calculation
anti-windup against the rate-limited output is the fix — a `control/` change
under rule 8, with the pinned test flipping from "overshoots" to "does not".

### E. Give the bench plant the measured open-loop spectrum

Unchanged from the morning. Until then no test can fail on slow wander.

### F. The gauge sweep — now only for the feedforward TERM

The band no longer waits on it. The positional feedforward (`feedforward.enabled`)
still does, and stays off until somebody re-gauges. **Do not run the sweep
without asking** — it ends the hold. And when the term does come on,
`move_speed` has to be re-graded with it: on the bench the term stacked on the
fast loop overshoots a 3 K move by 10.9 % at 118 K (3.1 % with it off), which
is why the bench envelope pins `BENCH_MOVE_SPEED = 0.5` while the file ships
0.15.

## Traps

* **The running recorder has the old file.** A `check` in this tree prints the
  new band; the cryostat is on the old one until restarted.
* **`hold_speed` under 1 is the STRONG regime.** The morning's arithmetic
  (stirring ≈ 2.2 / hold_speed) described the weak regime and does not
  extrapolate; the bench table in the config is where that regime ends. Do not
  "fix" a quiet hour by weakening.
* **±1 % is ±13 K of authority at 118 K.** A setpoint typo is bounded by rule 8
  (ramped at the one rate), the output rate limiter, and `hard_max_pct: 70`,
  none of which moved.
* **The "missing power −2.4 mW past 3σ" warning** on every bench run is the
  stale gauge, unchanged, and is not a fault.
* **Do not grade the hold criterion on anything short.** 1.00× to 72 s and 5.6×
  at 39 min was the morning's lesson, and it still applies to the new numbers.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m lschart -c config-ltspm3-armed.yaml status
```

`python -m ltspm3 -c config-ltspm3-armed.yaml check` prints the band **and**
the gain schedule; read both before arming.
