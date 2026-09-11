# PID Phase 4 — commissioning

Part of [PID_PLAN.md](../PID_PLAN.md). **Goal:** the loop armed on the
cryostat, attended and then not, then walked to 300 K.
[commissioning.md](../docs/ltspm3/commissioning.md) has the procedure and is
not restated; this is what changes and what each gate measures. **The monitor
runs from stage 3 on and `plant.json` is part of every gate.**

## 4.1 Stage 3 close-out

| item | do | gate |
|---|---|---|
| W1 | twenty distinct writes, `AOUT?` at 0/25/50/80/150/300 ms | `write_settle_s` in the config with margin; the twenty readbacks in `HANDOFF.md` |
| W2 | `send analog 70.5`; `analog 0` with the gate closed; `heaters_off` | two refusals logged, one zero reaching the box, output restored |
| circuit | the 09-10 repair on the manifest (Phase 0) | `δQ` inside 3 σ_Q for 72 h; weak evidence, but the evidence there is |

## 4.2 Stage 4, attended

A **third config**, `config-ltspm3-armed.yaml`: `operating_point_pct` on the
output holding the chosen temperature today (64.010 % holds 118.3 K),
`authority_pct` narrowed per commissioning 0.3, Phase 3's tables loaded.
Arm only when `plant.json` has read typical on all residuals for the past
hour.

| step | gate |
|---|---|
| 4a — arm, `authority_pct` 0.1, no tuning, no feedforward | ≥ 1 h `tracking`, readback agreeing every cycle, monitor and supervisor verdicts agreeing |
| 4b — provoked fault at low temperature | ramps down at 5 K/min through the inverse curve, latches, locks out, `ack` the only way out |
| 4c — widen to 1.0 %, then tuning, then feedforward, one per watched hour | no `frozen` without a named cause |
| 4d — **5 K/min sweep ≥ 10 K** | lag < 2 K, no warning at either end, `δQ` quiet |

## 4.3 Stage 5, unattended

Seven days at the operating point. **Gate:** the hold criterion
`σ_y(τ) ≤ σ_y(10 s)` over the run; every warning in `plant.json` explained;
no fault; no `frozen` longer than `warn_after_s` without a cause. Any of
those failing drops back a stage. Indefinite is the design; seven days is the
proof.

## 4.4 Stage 6 — the ladder to 300 K

Plan 1 §1.3's pipeline, run: rungs upward from 64 % by `plan_sweep.py`,
`max_output_pct` raised **one graded rung at a time** toward 100 %, each
rung a `jump` window and a refit input, `δT_c` and the stage channels the
watch on the cooler. Stops where Jeff says or where `δT_c` says the cooler is
losing.

**Gate:** every rung graded `steady`; the refit's up-range residual < 0.5 K
rms; the table's `T_MAX_K` at the highest graded rung; the ceiling in config
equal to it.
