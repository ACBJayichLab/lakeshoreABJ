# Handoff — 2026-09-17, evening (five minutes was too generous, and going fast found three bugs)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; **the
loop's requirements are [docs/ltspm3/requirements.md](docs/ltspm3/requirements.md)**,
the route is [PID_PLAN.md](PID_PLAN.md), and the commissioning step-by-step is
[plans/pid-4-commissioning.md](plans/pid-4-commissioning.md). This goes stale.

Previous: [archive/HANDOFF-2026-09-17b.md](archive/HANDOFF-2026-09-17b.md)
(the afternoon: the goal written down, the loop retuned to five minutes).

> ## STATE: the cryostat is ARMED on the MORNING's numbers. This tree is not running on it.
>
> Unchanged from the afternoon handoff, and now two retunes behind: the
> recorder still runs `hold_speed: 12`, `move_speed: 0.5` and a band of
> ±0.25 % pinned at 63.96 %. **A running recorder never re-reads its config**,
> so nothing in this tree reaches the cryostat until it is restarted.
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
> python -m ltspm3 -c config-ltspm3-armed.yaml send setpoint 122 --software
> ```
> The bench says 86 s to within 100 mK and 114 s to within 50. The recorder's
> CSV says what the cryostat did.

## What this session established

### 1. The oscillation in the viewer was the morning's tuning, not a new fault

Jeff saw the sample approach 119 K and then wander ±0.1 K with a repeating
shape, and asked whether the ramp-to-hold handover caused it. It did not. The
running recorder was started at 11:33, before the 12:24 retune, so it was on
`hold_speed: 12` — which the afternoon's own Allan table grades at **2.15×
open loop at 900 s**. At 119 K that tuning gives `kp` = 0.0066 %/K, so a 0.1 K
error commands 0.0007 % — a fifteenth of one DAC code. The loop could not have
caused the wander and could not have corrected it, which is exactly what a flat
output trace next to a moving temperature looks like.

### 2. Five minutes was excessively generous — §1b

Jeff's words, recorded verbatim in
[requirements.md](docs/ltspm3/requirements.md) §1b, which **supersedes answer
1**: 2 K at 120 K with a fast approach and a slight adjustment, overshoot up to
250 mK, within 50 mK and staying inside 2–5 min. He also chose, asked:

* **the 2 s cadence stays**, and with it the 3.0 s dead time;
* **`delay_floor` stays 4** — the stability margin is not traded for the last
  15–20 s;
* **5 K/min stays** as a *trajectory* ceiling, with the heater's own slew
  configured separately.

Those four choices are what set the 86 s the bench now measures. It is not a
tuning number: arrival is `span / rate + corner + ~2 tau_cl`, and at 5 K/min,
`delay_floor` 4 and a corner of 8 dead times that is 24 + 24 + ~38 s.

### 3. One number was doing two jobs

`max_rate_k_per_min`, divided by the gain, was **also** the heater's own slew
limit: 0.40 %/min at 120 K, against the 3.5 % of overdrive a 5 K/min ramp
needs. Every move spent 8.8 minutes creeping toward a drive the ramp had long
finished asking for. That is the soft approach with the long tail, and it is
why the afternoon measured moves getting *worse* when the ceiling was raised —
raising it steepens the trajectory by the same factor it loosens the actuator.

`supervisor.max_output_rate_pct_per_min` is now its own number. The fault
ramp-down keeps the kelvin conversion, because a descent that has to work with
no sensor is a trajectory and kelvin is its unit.

### 4. Going fast found three defects, none of them about speed

All three were live on the cryostat. Written up with what each cost in
[requirements.md](docs/ltspm3/requirements.md) §3b.

* **The spike test could not follow the file's own 5 K/min ceiling.**
  `predict()` added back the low pass's lag but not the median's, biasing it by
  `rate × 2 s`. Past ~3 K/min that passes the 8-sigma threshold, and a rejected
  sample never refreshes the reference it was rejected against — so it ran
  away. On a noise-free 5 K/min ramp, **38 of 40 honest samples were thrown
  away** and the loop froze for 30 s until staleness forced a reseed.
* **One rejection rejected everything after it.** The only escape was a 30 s
  clock. It is now also a sample count, and reaching it *reseeds* — accepting
  one sample is not enough, because the guard needs `recover_samples` good ones
  in a row and a filter limping at one in three never supplies them.
* **The write decision compared against memory, not against the heater.**
  Whenever the rate limiter handed back the target unchanged — most cycles, now
  that the heater may travel at its own rate — a loop whose output somebody
  else had moved decided it was already there and wrote nothing, indefinitely.
  This is the blindness `test_the_next_move_is_computed_from_where_the_heater_is`
  was written for; the old creeping limiter hid it.

### 5. The premise check was reading a measurement lag as a power fault

A fast move swings `dQ` by 47 mW against a 10 mW fault threshold, and **none of
it is delivered power**: `dQ` carries `C(T) dT/dt`, the slope arrives from a
regression 15 s late, and mid-move the estimator read 0.021 K/s against a true
0.055. Times 0.88 J/K that is 30 mW of a 32 mW excursion.

Jeff chose to put it where the other known uncertainties live, so the band
gained a `slope_lag` term: zero at a hold, zero during a constant sweep however
fast, nonzero only while the rate is *changing*. Two consequences:

* 3 sigma at a settled 118 K hold went **1.44 → 1.52 mW** — rectification bias
  on an estimate whose true value is zero, not a real widening.
* The step test now asks the band **at the two samples that make the step**
  rather than the widest anywhere in the window. The old form let one transient
  raise the threshold for a full half hour: a 3 % power loss at 100 K, an
  unmistakable 20 mW fault, went undetected for 1976 s — exactly when the
  window rolled past the transient the loss itself had caused. **374 s now.**

### 6. What the bench says

`tests_ltspm3/test_stage_4e_fast_move.py`, fitted plant, heater delivering
0.336 % less than the model claims. Full table in
[requirements.md](docs/ltspm3/requirements.md) §3b.

| | afternoon's file | this file |
|---|---|---|
| 2 K at 120 K, to 95 % | 4.6 min | **86 s** |
| …and to within 50 mK | — | **114 s** |
| overshoot | 7 mK | 11 mK (budget 250) |
| 10 K at 120 K | 15.9 min | **2.6 min** |
| 2 K at 60 K | **387 mK over** | **2.8 mK** |
| hold, Allan ratio at 15 / 60 / 300 / 900 s | 1.00 / 0.97 / 0.81 / 0.71 | unchanged — `hold_speed` untouched |

**What the bench still cannot say** is the slow wander that is the hold's whole
point: its plant has white sensor noise and no slow disturbance. That is
`analysis/hold_quality.py` against the 2026-09-15 open-loop night, after a
night armed on the new numbers.

## What is in the tree that was not this afternoon

| | |
|---|---|
| `docs/ltspm3/requirements.md` | **§1b, Jeff's revised move requirement**, and §3b, what the bench measured against it. Change §1 and §1b only by asking him |
| `ltspm3/control/supervisor.py` | `max_output_rate_pct_per_min`, the heater's own slew; the step test asks the band at the step's own extremes; the write decision compares against the heater |
| `ltspm3/control/filters.py` | `predict()` adds back the whole chain's lag; `max_consecutive_spikes` reseeds; `slope_delay_s`, `acceleration_excess` and a spike threshold that widens while the cryostat accelerates |
| `ltspm3/model/fitted_response.py` | the `slope_lag` band term, in `FAST_TERMS` |
| `ltspm3/control/tuning.py` | `max_kp_pct_per_k` 1.0 → 5.0, and a clamp that says so when it binds |
| `config-ltspm3-armed.yaml` | `max_output_rate_pct_per_min: 20`, `move_speed: 0.03`, `move_error_k: 0.40`, `hold_error_k: 0.05`, `max_kp_pct_per_k: 5.0` |
| `tests_ltspm3/test_stage_4e_fast_move.py` | was `4d`; Jeff's §1b benchmarks, 180 K's ceiling pinned, 60 K's old open item closed |
| `PID_PLAN.md` §1, `plans/pid-4-commissioning.md` 4e | the route |

## What needs doing, in the order I would take it

### A. Arm on the new numbers and move 2 K — Jeff

The box at the top. Nothing here has run on the cryostat; the bench is a
rehearsal and the heater is real. Watch the viewer, then read the CSV: time to
95 %, peak overshoot, and whether it is inside 50 mK by 5 minutes.

**Probe the box's own filter while the port is free** — between stopping the
recorder and re-arming, because the recorder owns the port exclusively
(invariant 2) and `probe` cannot run alongside it:

```bash
python -m lschart -c config-ltspm3-read-only.yaml probe
```

`17341e4` landed on main during this session and makes `probe` print the 218's
*own* reading filter and the per-input reading rate. It matters here:
everything in this handoff is tuned against a dead time of **3.0 s derived from
the SOFTWARE chain alone** — the median of three and the 2 s cadence — and the
loop knows nothing about a filter running inside the box. If one is on, the
real dead time is larger, and `delay_floor × delay_s` is an underestimate —
which would move `tau_cl`, the 86 s, the trajectory corner and the `slope_lag`
band term together. That is precisely the stability margin Jeff chose to keep,
so it is worth the one command.

### B. A night armed, then `hold_quality.py`

`hold_speed` was not touched this session, so the afternoon's Allan table
should still hold — but it has never been checked on the cryostat, and the
long-averaging half needs a night against 2026-09-15.

### C. The two open items that did not move

* the positional feedforward still waits on a delivered-power gauge, and when
  it comes it needs `move_speed` re-graded against §3b rather than §3a;
* 10 K still takes 16 min for a 2 K move, below `min_output_pct`, in a regime
  nobody has looked at. Not on Jeff's path.
