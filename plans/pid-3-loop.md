# PID Phase 3 — the loop

Part of [PID_PLAN.md](../PID_PLAN.md). **Goal:** the controller rebuilt on
the model — one rate, two speed ratios, premise checks in watts, a filter
chain with no low-pass, graceful failure — proved on the fitted plant at six
temperatures before it touches the cryostat.

**Every step is one commit, names the safety rule it touches, and runs the
bench (§3.7) before it lands.** Order matters: each step's tests assume the
ones before it.

> **REVISED 2026-09-14.** The first draft was written 2026-09-11, before the
> 09-13 refit and the 09-14 band. Read against today's code it was missing
> seven things, and one of them — §3.0.A — is the change that actually unblocks
> 4 to 300 K. The section numbers are unchanged because three other documents
> point at them; what is new is §3.0 and the step order at the end.

## 3.0 What the first draft did not know

Seven findings, each verified against the code or the production fit rather
than reasoned from the plan. Four of them add a step; three change one.

**A. The authority band cannot span 4–300 K, and nothing recentres it.**
`HeaterSupervisor.band` is recomputed every cycle but from two config
constants, and `_apply_band_to_pid()` is called exactly once, in `__init__` —
not in `arm()`, not in `set_setpoint()`, not in `step()`. So the PID's rails
are wherever the config file put them and stay there for the life of the
process. At `operating_point_pct: 63.076` and `authority_pct: 1.0` they are
62.08–64.08 %. From the production fit, 10 K is **24.22 %**, 118 K is 63.96 %
and 180 K is **68.73 %**: a sweep of more than ~15 K anywhere, and any sweep at
all below 60 K, is arithmetically impossible. The first draft retires eight
rate fields and never touches rule 5. **This is step 4, and it is the one
rule-scoped change that needs signing off before it lands.**

**B. Velocity feedforward is capped an order of magnitude too low**, which
follows from A and was hidden by it. Sustaining a ramp needs `rate·τ/K`:

| T K | u % | K K/% | τ s | 5 K/min in %/min | ff needed % | 3σ sweeping mW |
|---|---|---|---|---|---|---|
| 10 | 24.22 | 0.342 | 0.1 | 14.60 | 0.02 | 9.72 |
| 30 | 52.41 | 1.958 | 9.0 | 2.55 | 0.38 | 6.59 |
| 60 | 59.15 | 9.067 | 166.5 | 0.55 | 1.53 | 7.39 |
| 100 | 62.59 | 13.044 | 441.3 | 0.38 | 2.82 | 9.36 |
| 118 | 63.96 | 13.190 | 519.4 | 0.38 | 3.28 | 10.26 |
| 140 | 65.63 | 13.221 | 589.8 | 0.38 | 3.72 | 11.24 |
| 180 | 68.73 | 12.414 | 611.2 | 0.40 | 4.10 | 12.56 |

`max_velocity_ff_pct` is **1.00**. Above 60 K the ramp cannot be sustained, the
integral supplies it instead, and it overshoots when the ramp ends — which is
the exact failure that field's own docstring was written to prevent, one rate
change later. It becomes derived: the one rate through the gain, with headroom.

**C. `control/` still reads the OLD curve.** `feedforward.py` imports
`model/thermal_response.py`, not `fitted_response.py`. That is the curve the
09-13 refit found **4–5 K cold** at a given output — 60.597 % read 70.0 K and
reads 75.09. Both the positional feedforward and §3.3's sensor-free open-loop
ramp-down stand on it. Repointing is a prerequisite for §3.3, not a tidy-up,
and it is why it is step 2 rather than part of step 5.

**D. The tuning clamps bind on the new schedule, and `Ti = τ` is wrong at the
cold end.** `min_ti_s: 60` against `ti = τ` of 0.1 s at 10 K, 9.0 s at 30 K and
36.9 s at 40 K; `max_kp_pct_per_k: 0.50` against 0.519 at 40 K in `move`. Both
would clamp silently. More substantially: where τ(T) is shorter than the loop's
3 s delay, pole cancellation cancels a pole that is not the dominant lag.
`analysis/pid_tuning.py`'s own docstring derives the right form — Skogestad's
half rule and SIMC, `Ti = min(τ_eff, 4(τ_c + θ))` — and §3.2's flat `Ti = τ(T)`
drops it. The floor binds in `move` from **10 to 30 K**, which `--rows` prints.

**E. The monitor goes blind the moment the loop is armed.**
`MonitorConfig.move_pct` is 0.005 %, which is *half a DAC code*. A closed loop
with dither on moves the output by a code most cycles, so `_track_move`
refreshes `_move_t` continuously, `in_transient` never expires, and the judge
that exists to watch the loop reports `no opinion` forever. The gate wants to
key on a jump larger than the loop's own rate limit — an *uncommanded* move —
not on any change at all. Phase 2's replay cannot see this: nothing in the
archive is closed loop.

**F. The watt premise check is dead below ~30 K.** `min_output_pct: 28` and
10 K sits at 24.22 %, so `δQ` has no opinion there — correctly, it is the
difference of two large numbers. In `hold` the kelvin check still covers it; in
`move` the first draft turns the kelvin check off, so the cold end of a sweep
would have no premise check at all. **Settled 2026-09-14 (Jeff): the kelvin
check stays on in `move` below `min_output_pct`** — it is the one regime where
the gain is small enough that a kelvin threshold is not wrong. 5 K at 10 K is
14.6 % of output; at 118 K it is 0.38 %.

**G. `config-ltspm3-armed.yaml` does not exist** and §3.7 loads the bench's
tables from it. It is written in step 0.

## 3.1 Filter chain and derived delay — rule 3

| change | value |
|---|---|
| `filter.median_window` | 5 → **3** |
| `filter.tau` | 60 → **0**: pass-through. `ExponentialFilter` stays; the median's output becomes the spike reference, the reseed value, and what `primed` / `is_stale` test |
| `TuningConfig.delay_s` | **derived**: `median_window // 2 × cadence + cadence / 2 + tau / 2` = **3.0 s** at the 2 s cadence `config-ltspm3-heater.yaml` actually runs. Never a constant |

Test: a single-sample glitch is still rejected; a two-sample one now reaches
the guard, which rejects it by slew; `delay_s` equals the measured group
delay of the chain on a step.

## 3.2 Loop speed from the plant's τ — rule 4

Replace `hold_tau_cl_s: 1800` and `move_tau_cl_s: 300` with **ratios**:

```
tau_cl = max(speed × tau(T), 4 × delay_s)
Kp     = tau_eff / (K(T) × (tau_cl + theta))     = 1 / (speed × K(T)) above the floor
Ti     = min(tau_eff, 4 × (tau_cl + theta))      -- NOT tau(T) flat; finding D
```

`hold_speed: 3` (slower than the plant; noise never amplified),
`move_speed: 0.5`. `tau(T)` and `K(T)` from the schedule, which
`pid_tuning.py --rows` prints from the fitted table every 10 K with the fit's
cache key in `note` — today `24e2736fe6c28ba503e53546b19acd92`, which must
equal `_fitted_table.FIT_KEY`. `PROVISIONAL_SCHEDULE` deleted.
`min_ti_s` and `max_kp_pct_per_k` become derived bounds, not the constants that
would silently clamp rows 10–40 K.

Test: `Kp` within 1 % of `1/(speed·K)` at 60–180 K; the floor binds in `move`
at 10–30 K; `Ti` never below the loop's own delay; a schedule row that
disagrees with a fresh export by more than the fit's error fails.

## 3.3 One rate — rules 1, 5, 8

| retired | replaced by |
|---|---|
| `max_step_pct`, `max_rate_pct_per_min`, `rate_k_per_min`, `max_rate_k_per_min`, `approach_rate_k_per_min`, `rampdown_pct_per_min`, `rampdown_knee_pct`, `rampdown_below_knee_pct_per_min` | **`max_rate_k_per_min: 5.0`** and **`min_rate_pct_per_min: 0.20`** |

- Heater percent rate = `max_rate_k_per_min / K(T)`, floored at
  `min_rate_pct_per_min` where the model has no opinion. 0.38 %/min at
  118 K, 14.6 %/min at 10 K.
- Setpoint ramps, the post-fault approach and the fault ramp-down all use
  the one rate.
- **Ramp-down is open loop through the model's inverse curve**:
  `u(t) = percent_for(T_target(t))`, `T_target` falling at the rate from the
  last trusted temperature. It needs no sensor (rule 3). 118 K → base in
  ~23 min. Only ever lowers the heater (rule 1). **Through
  `fitted_response`, not `thermal_response`** — finding C, and step 2 is what
  makes that true.
- **Velocity feedforward from the model**: `du/dt = (dT/dt)/K(T)`;
  `max_velocity_ff_pct` **derived** from the one rate through the gain rather
  than the 1.00 constant — finding B.

Test: `check` prints one rate; a 5 K/min ramp at 118 K commands 0.38 ± 0.02
%/min; a ramp-down with the sensor faulted still descends at 5 K/min by the
model's reckoning and reaches `safe_output_pct`.

## 3.4 Premise checks in watts — rule 4, rewritten

`max_error_k`, `anomaly_hold_s`, `max_ramp_error_k`, `model_trust_k` retired.
So is `response_lag_s: 620` and the whole `_ramp_allowance_k` decay with it:
the allowance existed because one kelvin threshold had to cover a hold and a
sweep, and once the phase decides which check applies there is nothing left for
it to do. That is a deletion, not a rewrite, and it is the largest single
simplification in this phase.

| field | value | in `hold` | in `move` |
|---|---|---|---|
| `warn_error_k` | 1.0 | alarm, keep tracking | off, **except below `min_output_pct`** — finding F |
| `warn_sigma` | 3 | `δQ` ≥ 3 σ_Q → alarm | same |
| `fault_mw` | **10, as a STEP within `fault_window_s` = 1800 s** | → **frozen → ramp down → locked out** | same |
| `fault_error_k` | 5.0 | error beyond it **and railed** at the band for `fault_after_s` → the same (authority exhausted) | off |
| `fault_after_s` | 180 | | |

`_check_model` calls `model.missing_power_w` and `model.sigma_q_w` — the
monitor's two functions, so the two cannot disagree about what typical means.
It needs the coldplate, so the channel is a `SupervisorConfig` field with the
model's own locus as the fallback, and it inherits phase 2's no-opinion rules
verbatim: outside the table, below `min_output_pct`, within a transient, or
while `δT_c` is atypical means **no opinion, which is not typical and never
faults**.

**The fault is a step, not a level**, inherited from phase 2 rather than
decided here: `fault_mw` is 10 mW *as a range inside 30 minutes*. Slow
degradation is authority exhausted's to catch and that is a different
condition, gated by no window.

Test: the 09-10 event replayed through the supervisor on a virtual clock
**warns and does not fault**; a simulated compressor failure holds the
setpoint until the output rails at the floor, then faults; a +30 mW step in
delivered power faults within `fault_after_s`; a 10 K hold at 24 % output
still has a kelvin premise check.

## 3.5 `FROZEN` and `CRASHED` — rules 6, 7

- `SupervisorState.HOLDING` → **`FROZEN`**. Status schema bump; viewer and
  `LakeShore.m` mappings updated in the same commit.
- On an exception in `step()`: `panic_hold()`, state **`CRASHED`** with the
  exception's first line, recorder keeps writing, `arm` refused until `ack`.

Test: raise inside `step()`; assert the next frame is written, the output
unchanged, state `crashed`, `arm` refused, `ack` then `arm` resumes.

## 3.6 Quiet hold — rule 4

Not a limit, a test: on the fitted plant at each bench temperature, a
`hold`-phase loop fed the measured noise (`1.36e-6·T²` rms, lag-1 0.51)
commands **≤ 0.02 %/min** over one hour. If tuning cannot meet it,
`hold_max_rate_k_per_min` is added and this document says so.

## 3.7 The bench

A second harness fixture on `FittedResponse` beside the existing
`sim_response` one. Scenarios at **10, 30, 60, 100, 140, 180 K**, tables
loaded from `config-ltspm3-armed.yaml`, not built in the test, at the **2 s
cadence the cryostat runs** rather than the existing harness's 4 s:

| scenario | pass |
|---|---|
| 3 K setpoint move | overshoot < 5 % of the move; settles within 4 τ_cl |
| 5 K/min sweep ≥ 10 K | lag < 2 K with velocity feedforward; no fault; `δQ` quiet |
| glitch (single sample, 7 K/s) | frozen, recovers, no output change |
| sensor fault | frozen → ramp down at 5 K/min via the inverse curve → locked out; `ack` the only way out |
| compressor (T_c +2 K over 1 h) | tracks until railed at the floor, then faults |
| crash | §3.5 |
| quiet hold, 1 h | §3.6 |
| **model wrong on purpose**: plant K ×0.8 and ×1.2, τ ×0.7 and ×1.3, Λ offset ±0.3 K, C ×0.95 — controller unchanged | every row above still passes; **no false fault**; a 5 K/min sweep with C ×0.95 stays under `fault_mw` |

## The step order

The bench goes first because every later gate is "the bench is green", and a
grader written after the thing it grades is not one. Step 4 goes before step 5
because a one-rate sweep the band forbids cannot be tested.

| # | commit | rule | gate |
|---|---|---|---|
| 0 | the bench: `config-ltspm3-armed.yaml` + the `FittedResponse` fixture, six temperatures | — | runs; today's failures recorded as the baseline the later steps close |
| 1 | §3.1 filter chain and derived delay | 3 | §3.1 |
| 2 | finding C: `control/` onto `fitted_response` | 4 | `percent_for(T)` matches the shipped table |
| 3 | §3.2 ratios, schedule, SIMC floor (finding D) | 4 | §3.2 |
| 4 | finding A+B: the band follows the setpoint; ff cap derived | **5** | a 4→300 K traverse stays inside the band throughout; `hard_max_pct` never crossed |
| 5 | §3.3 one rate, open-loop ramp-down | 1, 5, 8 | §3.3 |
| 6 | §3.4 premise in watts, incl. finding F | 4 | §3.4 |
| 7 | §3.5 `FROZEN` / `CRASHED`, schema bump | 6, 7 | §3.5 |
| 8 | §3.6 quiet hold, the full matrix, finding E, the docs | 4 | the exit gate below |

## Exit gate

- All eight scenarios green at all six temperatures.
- `SupervisorConfig` has 2 rate fields where it had 8; `check` prints them.
- Plan 2's replay still green with the new tables, and the monitor **keeps an
  opinion while the loop is armed** (finding E).
- `safety.md` rules 3, 4 and 5 reworded; `control.md` and `running.md` state
  the two ratios, the one rate, the moving band and the state names.
