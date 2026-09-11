# PID Phase 3 — the loop

Part of [PID_PLAN.md](../PID_PLAN.md). **Goal:** the controller rebuilt on
the model — one rate, two speed ratios, premise checks in watts, a filter
chain with no low-pass, graceful failure — proved on the fitted plant at six
temperatures before it touches the cryostat.

**Every step is one commit, names the safety rule it touches, and runs the
bench (§3.7) before it lands.** Order matters: each step's tests assume the
ones before it.

## 3.1 Filter chain and derived delay — rule 3

| change | value |
|---|---|
| `filter.median_window` | 5 → **3** |
| `filter.tau` | 60 → **0**: pass-through. `ExponentialFilter` stays; the median's output becomes the spike reference, the reseed value, and what `primed` / `is_stale` test |
| `TuningConfig.delay_s` | **derived**: `median_window // 2 × cadence + cadence / 2 + tau / 2` ≈ **3 s** at 2 s cadence. Never a constant |

Test: a single-sample glitch is still rejected; a two-sample one now reaches
the guard, which rejects it by slew; `delay_s` equals the measured group
delay of the chain on a step.

## 3.2 Loop speed from the plant's τ — rule 4

Replace `hold_tau_cl_s: 1800` and `move_tau_cl_s: 300` with **ratios**:

```
tau_cl = max(speed × tau(T), 4 × delay_s)
Kp     = tau(T) / (K(T) × tau_cl)        = 1 / (speed × K(T)) above the floor
Ti     = tau(T)
```

`hold_speed: 3` (slower than the plant; noise never amplified),
`move_speed: 0.5`. `tau(T)` and `K(T)` from the schedule, which
`pid_tuning.py` now prints from the fitted table every 10 K with the fit's
cache key in `note`. `PROVISIONAL_SCHEDULE` deleted.

Test: `Kp` within 1 % of `1/(speed·K)` at 60–180 K; the floor binds below
30 K; a schedule row that disagrees with a fresh export by more than the
fit's error fails.

## 3.3 One rate — rules 1, 5, 8

| retired | replaced by |
|---|---|
| `max_step_pct`, `max_rate_pct_per_min`, `rate_k_per_min`, `max_rate_k_per_min`, `approach_rate_k_per_min`, `rampdown_pct_per_min`, `rampdown_knee_pct`, `rampdown_below_knee_pct_per_min` | **`max_rate_k_per_min: 5.0`** and **`min_rate_pct_per_min: 0.20`** |

- Heater percent rate = `max_rate_k_per_min / K(T)`, floored at
  `min_rate_pct_per_min` where the model has no opinion. 0.38 %/min at
  118 K, 14 %/min at 10 K.
- Setpoint ramps, the post-fault approach and the fault ramp-down all use
  the one rate.
- **Ramp-down is open loop through the model's inverse curve**:
  `u(t) = percent_for(T_target(t))`, `T_target` falling at the rate from the
  last trusted temperature. It needs no sensor (rule 3). 118 K → base in
  ~23 min. Only ever lowers the heater (rule 1).
- **Velocity feedforward from the model**: `du/dt = (dT/dt)/K(T)`;
  `max_velocity_ff_pct` set from the one rate through the gain. This, not a
  faster loop, is what closes the lag on a 5 K/min ramp.

Test: `check` prints one rate; a 5 K/min ramp at 118 K commands 0.38 ± 0.02
%/min; a ramp-down with the sensor faulted still descends at 5 K/min by the
model's reckoning and reaches `safe_output_pct`.

## 3.4 Premise checks in watts — rule 4, rewritten

`max_error_k`, `anomaly_hold_s`, `max_ramp_error_k`, `model_trust_k` retired.

| field | value | in `hold` | in `move` |
|---|---|---|---|
| `warn_error_k` | 1.0 | alarm, keep tracking | off |
| `warn_sigma` | 3 | `δQ` ≥ 3 σ_Q → alarm | same |
| `fault_mw` | from plan 2's replay (seed 8) | `δQ` beyond it for `fault_after_s` → **frozen → ramp down → locked out** | same |
| `fault_error_k` | 5.0 | error beyond it **and railed** at the band for `fault_after_s` → the same (authority exhausted) | off |
| `fault_after_s` | 180 | | |

`_check_model` calls `model.missing_power_w` and `model.sigma_q_w` — the
monitor's two functions. `δT_c` never faults. `safety.md` rule 4 is rewritten
in the same commit: the premise is "the watts add up".

Test: the 09-10 event replayed through the supervisor on a virtual clock
**warns and does not fault**; a simulated compressor failure holds the
setpoint until the output rails at the floor, then faults; a +30 mW step in
delivered power faults within `fault_after_s`.

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
loaded from `config-ltspm3-armed.yaml`, not built in the test:

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

## Exit gate

- All seven scenarios green at all six temperatures.
- `SupervisorConfig` has 2 rate fields where it had 8; `check` prints them.
- Plan 2's replay still green with the new tables.
- `safety.md` rules 3 and 4 reworded; `control.md` and `running.md` state
  the two ratios, the one rate and the state names.
