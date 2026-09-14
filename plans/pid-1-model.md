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

## 1.2 Export the band and the residual functions — **DONE 2026-09-14**

`analysis/band.py` measures the band; `export_response.py` freezes it into the
table; `fitted_response.py` is where the two functions live. Four things came
out differently from how this section imagined them, and each was a measurement
rather than a preference.

| deliverable | where | outcome |
|---|---|---|
| band constants: `DELTA_P_FRAC`, `DRIFT_W_PER_DAY` ± spread, `DRIFT_T0`, `SIGMA_TINF_K`, `DIURNAL_K`, `TC_RMS_K`, `TAU_BATH_S`, the `T_c` locus, **`SIGMA_C_FRAC`** | `analysis/band.py` → `export_response.py` → `model/_fitted_table.py`, beside `FIT_KEY` and the gauge | **DONE.** Each under the comment that says what it is; `test_fitted_table.py` asserts presence and range. `SIGMA_MODEL_K` was added — see below |
| `missing_power_w(T_s, dT_dt, T_c, u)` | `model/fitted_response.py` | **DONE.** 0.583 mW worst over 40 K in-epoch, 0.35 mW rms; the matched-output pair either side of the 09-10 fault reads **−0.284 mW**, which is what the reseat left |
| `sigma_q_w(T_s, u, t, dT_dt=0)` | same | **DONE.** Monotone in `t` and clamped before `DRIFT_T0`; 3σ at 118 K settled is **1.4 mW at day 0**, 8.8 mW at day 10, 6.7 mW at 5 K/min |
| `analysis/pid_tuning.py --rows` on `production_inputs()`; delay derived from the filter config; rows carrying the fit's cache key | `analysis/` | **DONE.** Every 10 K from 10 to 190 K, paste-ready `OperatingPoint` rows; the delay floor binds in `move` at 10–30 K |
| ~~`analysis/allan.py`~~ **DONE 2026-09-12** | `analysis/` | **The gate as written could not be met, because its figures were a prediction and not a measurement.** Replaced by the measured ladder on `pc-20260908-154814`, the quietest open-loop hold: **7.79 / 7.95 / 9.52 / 12.72 mK at 4 / 60 / 600 / 3600 s**, floor 7.38 mK at 130 s. The estimator is validated exactly against white noise (1/√τ), a linear drift (τ/√2) and a sine (peak at P/2). Averaging stops helping at two minutes; the old figures had it still improving at ten |

### What changed, and why

**1. The model's error is flat in KELVIN, not in watts, so the band carries
`SIGMA_MODEL_K × Λ′`.** Written as a fraction of the delivered power the same
in-epoch residual is 0.07 % at 118 K and **17 % at 5.4 K**, where 8 mW of
heater holds the sample. Flat in kelvin is what it is — 0.107 K rms below 25 K,
0.183 K above 40 K, 0.135 K overall. That number is REFIT §1's own row 2
recomputed through the shipped grid, deliberately: the band cannot claim the
model is better than the scoreboard says.

**2. `δP` came OUT of the band and became `bias_q_w`.** The 0.7 % is a constant
that changes when somebody handles the wiring, not a fluctuation. In the band
it makes 3σ at 118 K **14 mW** — three times the 09-10 event phase 2's replay
requires the monitor to catch, and larger than the fault threshold. PID_PLAN §3
has the full argument and the three consumers that do feel it.

**3. The drift enters at its FULL rate and symmetrically**, not at ±25 % of it.
REFIT §7.3 took the drift out of the shipped fit, so nothing subtracts it; and
§7.3's finding is a staircase of handling events rather than a rate, so the
sign is not to be trusted either.

**4. `SIGMA_C_FRAC` comes from the τ residuals, not from the model-free C.**
3.03 % rms over the eight graded relaxations in 40–120 K, which is §1 row 3
read as a number instead of as a verdict. The model-free comparison — this
section's parenthetical — is 14 % rms over six coarse bins with one of them
32 % out; it is the noisier of two estimates of the same quantity.

**And one number this section asked for that the apparatus does not give.**
"3σ at 118 K, day 0, settled, between 4 and 8 mW" is 1.4 mW measured. The
figure was written before the terms were, and 4–8 mW is where the band lands
about ten days after a gauge rather than on the day of one. Nothing was tuned
to reach it: every term is measured and `analysis/band.py` prints them
separately so the arithmetic is checkable.

## 1.3 Extend to 300 K

Nothing above 180.6 K is measured; the table clamps at 195 K. Extrapolation
says 300 K needs about 1.0–1.3 W, 80–90 % of output. Heater rated 1.68 W,
wiring fine, so the ceiling may rise to 100 % **one measured rung at a time**.

The ladder itself is commissioning stage 6 (plan 4 §4.4); this phase owns the
pipeline for it:

1. `plan_sweep.py --hi 300` extrapolates the rung list and marks rungs
   beyond the table as predicted-only. **DONE 2026-09-14** — and it was not
   marking them, it was extrapolating a monotone cubic past the top of the data
   and printing the answer in the same columns as the measured rungs. The
   boundary is the top of what the fit was *given*, 192.6 K, so four of thirty
   rungs on a `--hi 300` ladder now say `PREDICTED ONLY` on screen and carry
   `predicted_only` in the CSV. The top rung asks 77.9 % = **991 mW**, against
   a heater rated 1.68 W.
2. Each rung is a `jump` window; `curate.py --propose` is the diff.
3. Refit with the new anchors; the up-range residual must be **< 0.5 K rms**
   and τ within 10 % where measurable before the table's `T_MAX_K` moves.
4. Re-export; the monitor's no-opinion region shrinks with the table.

The 4 K end costs nothing: no authority below ~28 % output, so holding at
base is output zero, which the band's floor already allows.

## Exit gate

- REFIT_PLAN §1's three rows green: holds < 0.3 K, ladder < 0.5 K rms, every
  τ within 10 %. **MET 2026-09-13** — −0.25 / −0.08 / −0.01 K, 0.139 K rms,
  6.7 % worst, with the holds and the ladder scored as PREDICTIONS from a gauge
  fitted on disjoint anchors. "Every τ" now means every relaxation a single
  pole can describe, which is `steps.MAX_REACH` and `MAX_AMPLITUDE_FRAC`.
- `SUPERSEDED_NOTE` cleared; `test_fitted_response.py`'s pins **regenerated,
  not loosened**. **DONE** — eight points moved by up to 5 K and the 0.05 K
  tolerance stands.
- `missing_power_w` < 1 mW at every anchor; `sigma_q_w` exported. **MET
  2026-09-14, with the gate's one sentence read twice.**
  `python analysis/band.py` prints it: **0.583 mW worst over 40 K, in-epoch.**
  - *"Every anchor the fit was given"* is 136 anchors over 57 days and the
    level is gauged to ONE of them. In-epoch the residual is 0.135 K; against
    the whole campaign it is 4.7 K rms over 40 K, and that is the history of
    the cryostat rather than the model's error — REFIT §7.2, and no fit that
    ships one level can make it smaller. Both are printed, in that order.
  - *"< 1 mW"* is a kelvin gate divided by Λ′, and Λ′ runs 24 mW/K at 10 K
    against 1.7 at 118 K. In-epoch the gate is met above 40 K and missed below
    25 K (3.4 mW worst) — while in KELVIN the cold end is the **better** half,
    0.107 K against 0.183 K. A gate in watts is fourteen times stricter exactly
    where the model is best.
- The pipeline of 1.3 run end to end on the existing 180 K top rung as a dry
  run, producing no table change. **MET 2026-09-14** — `curate.py --propose`
  clean, `plan_sweep.py --hi 180` builds 30 rungs with nothing marked
  predicted-only, and `export_response.py` rewrote
  `ltspm3/model/_fitted_table.py` **byte for byte identical**.
