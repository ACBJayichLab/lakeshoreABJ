# PID Phase 1 — the model

Part of [PID_PLAN.md](../PID_PLAN.md). **Goal:** a thermal model that is
right from 4 to 300 K, with its error band exported beside it, so the
controller and the monitor read one source.

## 1.1 Finish the refit

[REFIT_PLAN.md](../REFIT_PLAN.md) §7 steps 7–10, unchanged. Its gate stands:
**leave-one-epoch-out predicts the three post-recal holds inside 0.5 K having
never seen them, or nothing proceeds.** Settle first, before regenerating:

- ~~the below-10 K basin (`T_lo` sits where there is no data).~~ **DONE
  2026-09-12**, and the answer is that the knots stay. Below 7 K nothing
  identifies the conductance — the sweep's 140 cold samples are 0.3 % of the
  weight, the roughness prior reaches no lower than 5.14 K, and the four
  zero-output anchors are missed by less than their own bar, so dropping all
  four moves the curve in the sixth figure. Four bottom-knot placements spread
  dΛ/dT by 10× at 4.55 K and 1.02× at 7 K; one *extra* knot below the data
  costs 83 % of the objective. What the question did turn up is that Λ is
  evaluated at the **coldplate**, 2.7 % below the bottom knot, which
  `knot_range` never looked at — now bounded by `KNOT_EXTRAP_TOL`. Numbers in
  [REFIT_PLAN.md](../REFIT_PLAN.md) 6c.
- ~~`δP` as a power-side error bar on every anchor (REFIT T10).~~ **DONE
  2026-09-12**, `DELTA_P_FRAC = 0.007` in quadrature with the kelvin bar, in
  watts. It binds over 40–120 K — where it is 1.7–2.6× the kelvin bar — and
  nowhere below 20 K, which is what carrying it in watts was for. The
  trajectory comes back 13.6 % better for 0.7 % on the anchors, the steady
  state moves up to 0.51 K at 122 K, and quadrupling the bar moves 77 K by
  0.10 K, so the answer does not turn on the one fault that sized it. Numbers
  in [REFIT_PLAN.md](../REFIT_PLAN.md) T10.

## 1.2 Export the band and the residual functions

| deliverable | where | test |
|---|---|---|
| band constants: `DELTA_P_FRAC`, `DRIFT_W_PER_DAY` ± spread, `DRIFT_T0`, `SIGMA_TINF_K`, `DIURNAL_K`, `TC_RMS_K`, `TAU_BATH_S`, the `T_c` locus, **`SIGMA_C_FRAC`** (from the τ residuals; the model-free and fitted C differ by 3.5 %) | `export_response.py` → `model/_fitted_table.py`, in the cache key | `test_fitted_table.py` asserts each is present and in range |
| `missing_power_w(T_s, dT_dt, T_c, u, t)` | `model/fitted_response.py` | < 1 mW at every settled anchor the fit was given; −4.9 ± 2 mW across the 09-10 window |
| `sigma_q_w(T_s, u, t, dT_dt=0)` | same | monotone in `t` after `DRIFT_T0`; 3σ at 118 K, day 0, settled, between 4 and 8 mW; at 5 K/min it grows by `SIGMA_C_FRAC × C × 0.083 K/s` |
| `analysis/pid_tuning.py` on `production_inputs()`; delay from the filter config; rows with the fit's cache key in `note` | `analysis/` | prints rows every 10 K over the table's range |
| ~~`analysis/allan.py`~~ **DONE 2026-09-12** | `analysis/` | **The gate as written could not be met, because its figures were a prediction and not a measurement.** Replaced by the measured ladder on `pc-20260908-154814`, the quietest open-loop hold: **7.79 / 7.95 / 9.52 / 12.72 mK at 4 / 60 / 600 / 3600 s**, floor 7.38 mK at 130 s. The estimator is validated exactly against white noise (1/√τ), a linear drift (τ/√2) and a sine (peak at P/2). Averaging stops helping at two minutes; the old figures had it still improving at ten |

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
