# The measured thermal response

Everything here was extracted from `reference/logs/CD8,CD9,CD10/*.xls` —
**24 files, 1,510 h (63 days), ~1.1 M samples**. These numbers drive every
default in `ltspm3/control/`.

> An earlier version of this was calibrated on two files only, and was wrong in
> ways the wider set exposed. **Where a number here contradicts memory, the
> number won**: re-derive from the logs, don't trust the prose.

> **The Coldplate channel in every one of those logs is pre-calibration.** A
> transposed digit in its curve was corrected at the box on 2026-09-04 12:07;
> the cold end read high before that. Nothing on *this* page is derived from
> Coldplate — the numbers here are Sample against heater output — so none of
> them move. `analysis/` is the part that does depend on it, as `T_c`. See
> [cryostat](cryostat.md#the-coldplate-was-reading-high-and-every-old-number-carries-it).

## The measurements

| Property | Value |
|---|---|
| Sensor noise, sample channel | **quadratic in T**: `rms ≈ 1.36e-6 · T² K`, floored ~1.8 mK. Measured 1.8 mK @ 18 K, 13.6 mK @ 96 K, 45 mK @ 190 K, **109 mK @ 290 K** |
| Fast thermal time constant | ~5–10 min |
| Slow thermal tail | hours (3–12 h; poorly constrained) |
| Actuator | the analog output is a **voltage** into a stable 75.5 Ω heater, so **`P ∝ pct²` exactly** and temperature-independently |
| Thermal response | `T − T_bath = A·P^m`, **m ≈ 3.16** (lumped `pct^6.32`, R² = 0.9962) from **24 settled heater steps** in `cd10 monitor4/5` |
| Steady state | 43% → 18.2 K; 63.076% → **99.60 K**; 66.95% → 151.05 K |
| **Local gain at the 63% operating point** | **~10.0 K/%** |
| **Local gain at 66.6% / ~149 K** | **~13.8 K/%** — measured 2026-08-31 from seven settled points, 66.235% → 66.598%, on the live recorder rather than the legacy logs |
| **Local gain at 67–69% / 155–181 K** | **~13.0 K/%** — measured 2026-09-03 from four settled points, 66.998% → 69.027%, holds of 10.7–25.7 h. Easing gently with temperature: 13.3 → 12.7 K/% across the span |
| Fast pole, re-measured | **709 s, R² = 0.9973** — the +0.500% step of 2026-08-24 17:31. Independently confirms the 620 s below. **Fit window not recorded** — see the caveat below |
| Time constant | **~620 s** @ 137 K — but from the *one* clean step response in the logs. Provisional |
| Largest *legitimate* one-sample ΔT | **6.5 K** (−1.63 K/s, `cd8_…_monitor7`, corroborated on all three inputs); ~2.97 K/s just after a heater cut |
| Normal-operation ΔT, p99 | 0.26 K |
| Practical stability floor | ~2.5–4 mK near 96 K; **~100 mK near 290 K** |
| Sensor noise character | **correlated, not white** — lag-1 autocorrelation **+0.51** |

> **Every τ in this table is a fitted number, and a fitted τ is only as good as
> the window it was fitted over.** Simulated against the calibrated two-pole
> response, a 5-minute window returns τ five times too small and K three times
> too small *at R² = 0.947* — a fit that reads as healthy and is not. The
> reliable region starts around 20 minutes and the working rule is to hold
> about 3τ. The full table is in
> [commissioning.md](commissioning.md#how-long-to-hold--and-why-r-will-not-tell-you).
> Record the fit window with any τ added here.

**Heater resistance: 75.5 Ω, measured (2026-09-03).** This supersedes the 50 Ω
that the prose carried in seven places since the model was written. **No fitted
number in this document changes**, and that is worth understanding rather than
just noting: R never enters any calculation here. Every fit is against
*percent*, and `dT = A·P^m` with `P = V²/R` absorbs the whole of R into the
coefficient `A`. So `P ∝ pct²` is untouched, `m = 3.16` is untouched, the local
gains are untouched, and so is the simulator.

R matters at exactly one boundary: **converting to absolute watts.** Anything
quoted in W against the old value is high by 75.5/50 = **1.51×**. If the
energy-balance form `C(T)·dT/dt = Q(u) − G(T)·(T − T_bath)` is ever fitted for
physical `C` and `G`, R sets their absolute scale — the shapes and the ratio
`τ = C/G` do not care, the magnitudes do.

**What CD10 actually contains (converted to recorder CSV, 2026-09-03).**
`python -m lschart.tools.xls_to_csv "reference/logs/CD10/*.xls" -o "data/heater calibration steps"`
reproduces this; `data/` is gitignored, so the CSVs are derived, not stored.

| log | span | heater cmds | 336? | what it is |
|---|---|---|---|---|
| `sample_cold` | 07-15 → 07-17 | 31 | yes | **mid-cooldown, not valid** for calibration |
| `sample_monitor1` | 07-17 → 07-20, 72 h | 1 | yes | stages **at base** (1st 28.49 K @ +0.56 mK/h, 2nd 3.94 K @ +0.12 mK/h). A 72 h *constant-heater* hold at 63.07% / 96 K — a drift and noise dataset, **not** a step dataset |
| `sample_monitor3`, `st2_monitor3` | 07-23 → 07-31 | 0 | no | constant 63.072%, ~98–100 K |
| **`sample_monitor4`+`5`** | **08-08 → 08-20, 287 h** | **200** | **no** | the ladder: 60–70%, 99.6–170.8 K, 120 steps, **21 dwells > 3 h totalling 279 h** |

**For fitting, load the flattened tables, not these logs.**
`data/heater calibration steps/fit_cd10.csv` (298,617 rows, 857.9 h, 5 segments)
and `fit_recorder.csv` (435,300 rows, 244.2 h, 4 segments) carry `Timestamp`,
`t_s`, `segment`, the thermometers, `u_pct` and `note` -- and nothing else.
**Fit each `segment` as its own trajectory**: they are split at the recording
gaps, and CD10's are 65 h and 187 h long. The ladder is `fit_cd10.csv`
segment 4 -- 286.9 h, 103,282 rows, 60-70%, 99.6-170.8 K.

`data/cd10/` holds the same data as 28 recorder-shaped daily files; that set is
for the viewer (`--csv`), not for fitting.

**The 336 stopped logging on 2026-07-23**, so the two files carrying 200 of the
232 heater commands have no `RAD SHIELD` / `THE CHONKE` / stage data at all.
The converter reports the match rate per file rather than leaving a blank to be
mistaken for a cold shield. This is why the room-temperature covariate that
works on the 2026-08/09 recorder data cannot be applied to monitor4/5.

Every large step in monitor4/5 is preceded by a *brief* excursion, so the
pre-step state is unsettled and the net ΔT across a step is small — a −4.500%
step at 08-08 16:45 held 30 h shows ΔT of only +3.94 K. **Per-segment fitting
cannot use these; a whole-record fit that propagates state through can.**

**Noise, confirmed at the top of the range (2026-09-03).** Over a settled 25.7 h
hold at 180.56 K the sample's rms is **44.1 mK** against the model's predicted
**44.3 mK**. That is the quadratic fit confirmed independently of the data it
was fitted to.

A linear noise fit from 96 K understates room temperature by ~4×. Millikelvin
control is a **low-temperature capability, not a global one**.

Allan deviation: 6.1 mK @ 4 s, 4.1 mK @ 60 s, 2.5 mK @ 600 s — about 2× worse
than 1/√N. **The measurement, not the DAC, is what limits mK stability**, and
it is why sampling faster than 1 Hz buys much less than it looks like it should.

## The consequence that shapes the whole design

At ~10.0 K/%, one 0.01% DAC code is **~100 mK** — roughly forty times the sensor
noise floor at 96 K, and far coarser than the few-mK goal. **Rounding to the
nearest code would make millikelvin control impossible regardless of PID
tuning.**

So the output is **sigma-delta dithered** (`control/dither.py`): the rounding
error is carried forward so the *sequence* of codes averages to the request, and
the response's ~620 s pole low-passes the dither to sub-mK ripple.

### One subtlety the quadratic actuator introduces

The dither averages *voltage*, but the sample responds to *power*, and
`⟨V²⟩ = ⟨V⟩² + Var(V)`. So the mean power delivered sits slightly **above** the
power at the mean voltage.

Measured at the operating point that bias is **~2 μK** — three orders below the
noise floor, so it is ignorable. But it is a real systematic, and it is tested
for (`tests_ltspm3/test_plant.py`) so nobody has to rediscover it while chasing
an offset.

## Why the model is in two stages

`ltspm3/thermal_response.py` deliberately keeps **`P(pct)`** and **`T(P)`** apart, and both
the simulator and the feedforward import that one curve so they cannot drift.

Lumping them into a single `T ∝ pct^n` fit — the previous model, n = 5 from two
points — hid the fact that **only one factor is uncertain**, and invited
re-fitting the exponent to absorb error belonging to the fixed quadratic.

**No single exponent spans the range.** The local lumped exponent runs from ~5.0
near 43% to ~7.8 near 64%, which is what changing conductances imply.
Extrapolating the high-temperature fit down to 43% predicts 12.8 K where 18.2 K
was measured.

So measured points are **interpolated (log-log) where they exist**, and the
power law only extrapolates beyond them.

| Module | |
|---|---|
| `ltspm3/thermal_response.py` | the one measured `P(pct)` / `T(P)` curve |
| `ltspm3/sim_response.py` | two-pole calibrated model + measured cross-channel coupling |

`sim.speedup` accelerates the thermal response but **not** the controller.
