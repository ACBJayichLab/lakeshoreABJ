# Handoff — 2026-09-17 (the loop held for 19 h, and the hold is worse than open loop)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
route to a working loop is [PID_PLAN.md](PID_PLAN.md) and the commissioning
step-by-step is [plans/pid-4-commissioning.md](plans/pid-4-commissioning.md).
This goes stale.

Previous: [HANDOFF-2026-09-16.md](HANDOFF-2026-09-16.md) (the loop closed on the
cryostat, twice; the second time it held).

> ## THE LOOP IS HELD. THE HEATER IS FROZEN AT 63.989 %.
>
> `send hold` at 2026-09-17 09:33, at Jeff's instruction, after the overnight
> record was graded. `mode: off`, `state: idle`, reason *"held by an operator;
> `arm` to close the loop again"*. The recorder, the viewer and the monitor are
> all still up on `config-ltspm3-armed.yaml`; only the loop is disengaged.
>
> **Nothing is regulating the sample.** That is deliberate and it is the better
> of the two available states today — see below. Ask it, do not read it here:
>
> ```bash
> python -m lschart -c config-ltspm3-armed.yaml status
> ```
>
> **The AUDIT-2026-09-16 fixes ARE live.** Jeff restarted the recorder at
> **09:04:28 on 2026-09-17**, after the audit branch merged, so the 24-minute
> fault ramp-down, the one rate reaching the output and `arm` refusing on an
> armed loop are all on the cryostat now — for the first time.
>
> **The overnight window graded below is NOT that process.** It was recorded by
> the run that started on 09-16, on the pre-audit code. That does not touch the
> finding: none of the audit fixes moves a gain, and `Kp 0.02 / Ti 900` is the
> same either side of the restart.

## The finding: 19 h armed, and the loop made the hold 5.6x worse

Jeff noticed roughly two-hour oscillations overnight. They are real, they are
the loop's, and they are now measurable in one command.

**Two matched 15 h windows, same temperature, same length, adjacent nights:**

| τ | open loop 09-15 | armed 09-16 | ratio |
|---|---|---|---|
| 10 s (floor) | 8.69 mK | 8.57 mK | 0.99 |
| 72 s | 7.11 mK | 7.42 mK | 1.04 |
| 130 s | 5.70 mK | 6.91 mK | 1.21 |
| 416 s | 4.75 mK | 9.67 mK | 2.04 |
| **2368 s (39 min)** | **3.78 mK** | **21.19 mK** | **5.60** |

sd over the window: 16.3 mK open, 33.3 mK armed. **The loop is invisible below
about 2 minutes — 1.00x, 0.99x, 0.98x, 0.97x, 0.99x — and then diverges.** It
adds no noise anywhere a fast measurement would look, which is exactly why 4a's
one-hour gate passed it.

Band rms, by period, is what localises the mechanism:

| period | open loop | armed |
|---|---|---|
| 10–40 min | 4.87 mK | 7.93 mK |
| 40–90 min | 3.50 mK | 11.69 mK |
| 90–240 min | **2.60 mK** | **19.73 mK** |

### It is the loop, not the building

On the armed night **every disturbance channel was normal or quieter** than on
three open-loop nights, in the same 90–240 min band:

| channel | 09-13 | 09-14 | 09-15 | **armed 09-16** |
|---|---|---|---|---|
| RAD SHIELD | 28.0 | 13.7 | 6.4 | **6.6** |
| 1st Stage | 20.6 | 11.2 | 4.8 | **6.9** |
| Coldplate | 0.54 | 0.81 | 0.32 | **0.62** |
| Sample | 3.1 | 4.7 | 2.6 | **19.7** |

On 09-13 the shield swung 28 mK in that band and the sample moved 3.1 — the
sample is well isolated from it. The environment did not change; the loop did.

### The period is the loop's own number

The closed loop's own natural period, `2π·√(τ·Ti/(Kp·K))`, with the armed
gains — Kp 0.02 %/K, Ti 900 s, K 12.57 K/%, τ 516 s — is **142 min**. The
measured spectral peaks are 118–147 min.

(It reduces to `2π·τ/√(Kp·K)` only when `Ti = τ`, which is what SIMC picks and
is NOT what the armed file runs — so use the general form.)

Heater-against-sample phase in that band is **+108°, coherence 0.77**, which is
what a PI with these gains produces (predicted +128°, integral-dominated). After
the plant's own 24° lag the heater's 11.8 mK-equivalent of forcing lands in near
quadrature with the sample — which sustains a mode instead of correcting one.
**The controller is doing exactly what it was told; the gain pair is what is
wrong.**

### Ruled out, with the evidence

* **Not output quantisation.** `SigmaDeltaDither` works: the code toggles every
  1–2 cycles, median run 2 s, three codes used over 15 h. The 218's 0.01 % step
  is ~126 mK here and the modulator is burying it, as designed.
* **Not sensor quantisation either**, though the Sample channel really does
  report only **10 mK** — the coarsest of any channel on the cryostat. The
  bench's simulated thermometer is `quantum_k: 0.001` (1 mK,
  `ltspm3/model/fitted_response.py:139`), so the bench has been running a sensor
  ten times finer than the real one. Raising it to 10 mK changes the bench's
  answer by nothing: `noise_quadratic·T²` is 18.9 mK at this temperature, which
  dithers a 10 mK code completely.
* **The model says this cannot happen.** PI on a single pole is unconditionally
  stable; |S| ≤ 1 at every period, phase margin 96°, ζ = 1.65. Bolting on a
  fabricated 2 h second pole still only reaches |S| = 1.63. The cryostat shows
  5.6x. **So the plant's hour-scale dynamics are not what the model says**, and
  nothing has ever measured them — τ came from steps. **Gains cannot be chosen
  from the model here; they have to be graded on the cryostat.**

## What is new in the tree

### `analysis/hold_quality.py` — the comparison nobody could make before

PID_PLAN §1's criterion compares a window to its own 10 s floor, which is
self-referential: a loop that doubles the wander everywhere can pass it. This
puts two windows side by side and answers the question that actually gates an
experiment.

```bash
python -m analysis.hold_quality \
  --csv "data/ltspm3-armed_2026-09-1[67].csv" \
  --from 2026-09-16T18:00 --to 2026-09-17T09:00 \
  --vs "data/ltspm3-heater_2026-09-1[56].csv" \
  --vs-from 2026-09-15T18:00 --vs-to 2026-09-16T09:00
```

It reuses `allan.adev`/`report` rather than reimplementing σ_y, adds the band
table, and prints a per-band and per-τ ratio with a verdict. **Run it on every
night from here on** — that is what turns "I think it looked worse" into a
number by breakfast.

### `tests_ltspm3/test_stage_4c_tuning.py` — can this loop move a setpoint?

`test_stage_4a.py` already pinned that a 3 K move is not delivered at 5 K/min
and already named the switch conflation. This is the other half of that
sentence: **what flipping `tuning.enabled` actually buys.**

Bench, +2 K from a settled 116.88 K, forty minutes:

| rate | 4a, short by | tuner on, short by |
|---|---|---|
| 0.2 K/min | 1.045 K | 0.065 K |
| 1.0 K/min | 0.982 K | 0.041 K |
| 5.0 K/min | 0.970 K | 0.037 K |

**The rate is not what binds** — all three land within 0.1 K of each other, so a
test that only ran 5 K/min would have concluded "the ramp is too fast" and been
wrong. Three things are gated on `tuner.enabled` and all of them are off at 4a:
the scheduled gains (`move_speed: 0.5` is an 8x kp), the velocity feedforward,
and `ramp_lead_pct`, which is the authority to use it. The loop drags the sample
with P+I inside a fixed ±0.25 % window. It never rails and never faults; it
arrives about a kelvin late with a `warn_error_k` warning — graceful, and
useless for an experiment.

## What needs doing, in the order I would take it

### A. ~~The tuning step of 4c~~ — APPLIED 2026-09-17, and never armed

`plans/pid-4-commissioning.md` already has this: 4c is *"widen to 1.0 %, then
tuning, then feedforward, one per watched hour"*. **The tuning step does not
need the gauge**, and that is the point — `feedforward` commands the model's
stale LEVEL, the tuner reads only `K(T)` and `τ(T)`, the SHAPE. Same distinction
`has_curve` already draws for the ramp-down and the rate limiter
(AUDIT-2026-09-16 findings 2 and 3), and `test_stage_4c_tuning.py` pins that the
two switches come apart.

**APPLIED to `config-ltspm3-armed.yaml`, and it reaches the cryostat only at the
next `run --arm` — which has not happened. The running loop is HELD and its
gains are still the 4a ones.**

```yaml
  tuning:
    enabled: true          # was false
    hold_speed: 12.0       # was 3.0 -- see below
```

`check` now prints it, which it did not before:

```
  gain schedule  : ON -- kp/ti from the model at hold_speed 12.0, move_speed
                   0.5; a ramp also gets velocity feedforward and the band
                   widens while it runs
```

Read that line before arming. Three behaviours hang off the switch and nothing
used to print any of them, which is how "a move arrives a kelvin late at any
rate" was read as "the ramp is too fast".

Flipping it broke seven tests, every one of them the pinning working:
`test_stage_4a.py` is now two things (`armed()` follows the file, `at_4a()`
forces the tuner off for the three scenarios that reproduce 2026-09-16), the
rate limiter is asserted at both stages, and **`BENCH_HOLD_SPEED = 3.0` is a
fourth envelope switch** — `enabled` was pinned but `hold_speed` was still read
from the file, so raising it handed every envelope scenario a loop four times
weaker and the monitor's dither test failed honestly.

**`hold_speed` is a guess and is the one number here that is not measured.** The
loop's authority in the 90–240 min band scales as `1/hold_speed`, and the
tuner's default of 3 would give |L| ≈ 0.73 there against the 0.37 that produced
the overnight oscillation — i.e. **the tuner's default hold gains would probably
make the hold worse, not better.** 12 gives ≈ 0.18. It costs a ramp nothing,
because a ramp runs on `move_speed`; `test_hold_speed_does_not_touch_the_move_gains`
pins that. One night with `hold_quality.py` settles it.

### B. Ramping on the cryostat, which is what 4c buys

Untested on hardware. Within the present fixed band the reach is −3.5/+2.8 K,
and the largest rate the band can sustain at all is **0.36 K/min** against the
configured `max_rate_k_per_min: 5.0`. With the tuner on the band widens during a
ramp and that ceiling lifts. A ±2 K move at a few rates, graded, is an afternoon.

### C. The gauge — still the critical path for anything past ±3 K

Unchanged from the last handoff, item A. `~2–3 h`, rehearsed and green:

```bash
python -m ltspm3.tools.sweep -c config-ltspm3-heater.yaml --percents "63.5,62.8,62.0,61.2" --order down
```

**Do not run it without asking** — Jeff declined it on 2026-09-16 and it ends
the hold.

### D. The bench cannot grade a hold, and that is why none of this was caught

Two gaps, both measured today:

* the simulated sensor resolves 1 mK against the cryostat's 10 mK;
* **the bench plant has no slow disturbance at all.** A 15 h simulated hold is
  3.0 mK sd and flat whatever the tuning is. No test in `tests_ltspm3/` can fail
  on hold quality, which is why 497 of them were green while the cryostat was
  doing this.

Giving the bench plant the measured open-loop spectrum would let the harness
reproduce and grade a hold. Until then the cryostat is the only instrument that
can answer a tuning question, and `hold_quality.py` is how it answers.

## Corrections to the record

* **The open-loop bar has moved, and improved.** PID_PLAN §1's hold row quotes
  the archive reference `pc-20260908-154814`: 7.4 mK at 130 s rising to 12.7 at
  1 h and 24.5 at 6.6 h, "2.9x outside the criterion". That still reproduces.
  But the **2026-09-15 open-loop night falls instead of rising** — 3.78 mK at
  39 min, worst 1.04x its floor at τ = 12 s, i.e. **essentially MEETING the
  criterion open loop.** Two different windows of the same cryostat, five weeks
  and a coldplate recalibration apart. The bar a software PID has to beat is now
  the recent one, and it is a much harder bar.
* **4a's gate could not have caught this.** One hour, and the Allan criterion
  runs to `L/4` = 15 min. The mode is at 120–145 min. The gate was not run
  badly; it was structurally blind to this, and that is worth writing into the
  stage-5 criteria rather than discovering twice.

## Traps this session added

* **"The loop adds no noise" is true and is not the question.** It is 1.00x out
  to 72 s and 5.6x at 39 min. Any grading that stops before an hour will keep
  saying the loop is fine.
* **A quiet hour is not a quiet night**, and a quiet night is not a quiet
  window: always grade against a matched open-loop window of the *same length*,
  which is what `--vs` is for.
* **Do not turn the tuner on at its default `hold_speed`** expecting the
  oscillation to go away. The arithmetic says it doubles the loop's authority in
  exactly the band where the problem is.
* **The premise warning is still the stale gauge**, unchanged: `missing power
  ≈ −2.4 mW past 3σ at 1.44 mW` appears in every bench run above and on the
  cryostat. It is a WARNING, it keeps tracking, and raising `warn_sigma` to
  silence it is the one thing not to do.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m lschart -c config-ltspm3-armed.yaml status
```

`analysis/` still needs `measure.py` run once (11 s) before anything that fits.
`hold_quality.py` needs nothing but the recorder's own CSVs.
