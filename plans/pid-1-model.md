# PID Phase 1 — the model

Part of [PID_PLAN.md](../PID_PLAN.md). **Goal:** a thermal model that is
right from 4 to 300 K, with its error band exported beside it, so the
controller and the monitor read one source.

## 1.1 Finish the refit

[REFIT_PLAN.md](../REFIT_PLAN.md) §7 steps 7–10, unchanged. Its gate stands:
**leave-one-epoch-out predicts the three post-recal holds inside 0.5 K having
never seen them, or nothing proceeds.** Settle first, before regenerating:

- the below-10 K basin (`T_lo` sits where there is no data);
- `δP` as a power-side error bar on every anchor (REFIT T10).

## 1.2 Export the band and the residual functions

| deliverable | where | test |
|---|---|---|
| band constants: `DELTA_P_FRAC`, `DRIFT_W_PER_DAY` ± spread, `DRIFT_T0`, `SIGMA_TINF_K`, `DIURNAL_K`, `TC_RMS_K`, `TAU_BATH_S`, the `T_c` locus | `export_response.py` → `model/_fitted_table.py`, in the cache key | `test_fitted_table.py` asserts each is present and in range |
| `missing_power_w(T_s, dT_dt, T_c, u, t)` | `model/fitted_response.py` | < 1 mW at every settled anchor the fit was given; −4.9 ± 2 mW across the 09-10 window |
| `sigma_q_w(T_s, u, t)` | same | monotone in `t` after `DRIFT_T0`; 3σ at 118 K, day 0, between 4 and 8 mW |
| `analysis/pid_tuning.py` on `production_inputs()`; delay from the filter config; rows with the fit's cache key in `note` | `analysis/` | prints rows every 10 K over the table's range |
| `analysis/allan.py` | `analysis/` | reproduces the 2026-09-10/11 open-loop hold: 8.3 / 7.6 / 4.3 / 6.3 mK at 4 / 60 / 600 / 3600 s |

## 1.3 Extend to 300 K

Nothing above 180.6 K is measured; the table clamps at 195 K. Extrapolation
says 300 K needs about 1.0–1.3 W, 80–90 % of output. Heater rated 1.68 W,
wiring fine, so the ceiling may rise to 100 % **one measured rung at a time**.

The ladder itself is commissioning stage 6 (plan 4 §4.4); this phase owns the
pipeline for it:

1. `plan_sweep.py --hi 300` extrapolates the rung list and marks rungs
   beyond the table as predicted-only.
2. Each rung is a `jump` window; `curate.py --propose` is the diff.
3. Refit with the new anchors; the up-range residual must be **< 0.5 K rms**
   and τ within 10 % where measurable before the table's `T_MAX_K` moves.
4. Re-export; the monitor's no-opinion region shrinks with the table.

The 4 K end costs nothing: no authority below ~28 % output, so holding at
base is output zero, which the band's floor already allows.

## Exit gate

- REFIT_PLAN §1's three rows green: holds < 0.3 K, ladder < 0.5 K rms, every
  τ within 10 %.
- `SUPERSEDED_NOTE` cleared; `test_fitted_response.py`'s pins **regenerated,
  not loosened**.
- `missing_power_w` < 1 mW at every anchor; `sigma_q_w` exported.
- The pipeline of 1.3 run end to end on the existing 180 K top rung as a dry
  run, producing no table change.
