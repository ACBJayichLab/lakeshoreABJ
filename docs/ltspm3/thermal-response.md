# The measured thermal response

Everything here was extracted from `reference/logs/CD8,CD9,CD10/*.xls` —
**24 files, 1,510 h (63 days), ~1.1 M samples**. These numbers drive every
default in `ltspm3/control/`.

> An earlier version of this was calibrated on two files only, and was wrong in
> ways the wider set exposed. **Where a number here contradicts memory, the
> number won**: re-derive from the logs, don't trust the prose.

> **The Coldplate channel in every one of those logs is pre-calibration.**
> Input 2 was carrying another thermometer's curve until 2026-09-04 12:07, so
> the cold end read high by 12–13%. Nothing on *this* page is derived from
> Coldplate — the numbers here are Sample against heater output — so none of
> them move. `analysis/` is the part that does depend on it, as `T_c`, and its
> inputs have been remapped. Reprocessed logs live in `data/coldplate-recal/`;
> see
> [cryostat](cryostat.md#the-coldplate-was-reading-high-because-it-had-another-sensors-curve).

## τ is not a constant — 2026-09-05

The numbers below were the best available from the legacy logs, and the 43 h
sweep of 2026-09-02 → 09-04 has since superseded two of them. The ODE fitted to
that sweep in `analysis/` gives **τ = C(T)/Λ′(T)** as a function of temperature,
and it moves by three orders of magnitude:

| T | u% | dT/du | τ |
|---|---|---|---|
| 10 K | 24.6 | 0.3 K/% | < 1 s |
| 40 K | 56.4 | 3.9 K/% | 36 s |
| 70 K | 60.6 | 11.6 K/% | 246 s |
| 110 K | 63.7 | 13.2 K/% | 489 s |
| 137 K | 65.5 | 13.1 K/% | 586 s |

The 620 s below is the 137 K value and is right *there*. It is wrong everywhere
else, and the reason is physics rather than measurement: C falls steeply as the
cryostat cools while the link's conductance does not, so the cold end settles
almost instantly and the warm end takes ten minutes.

Two things follow.

**Below about 25 K the recorder cannot measure τ at all.** τ is a few seconds
against a 2 s cadence, so a dwell down there yields a steady state and nothing
else, however long it is held. That is not a defect in the dwell — it is
Nyquist, and the fix if τ(T) at the cold end ever matters is a faster cadence
for those rungs, not a longer hold.

### The programmed ladder measured it — 2026-09-05

`ltspm3.tools.sweep` ran 30 rungs from 7.19% to 63.70% against the live
recorder in **4 h 17 min**, and it settles both halves of this page.

**τ was already right.** Measured against the fitted model, from 77 K to 114 K:
1.03, 1.03, 1.03, 1.02, 1.01. At 50–57 K it is 0.88–0.90. The dynamics
generalise, as this page has always claimed they would.

> **PAID, 2026-09-13.** The refit below is done and the shipped table now
> reproduces these same 45 rungs to **0.139 K rms, 0.35 K max**, and predicts
> the three long holds it was not fitted to within 0.25 K. Everything in this
> section is the measurement that forced it, kept as it was written.
> REFIT_PLAN.md §1.

**The steady state was not.** The model came back **low by up to 4.5 K** across
the band it had been interpolating through, rising smoothly from +0.35 K at
28 K to a plateau of about +4.5 K from 53 K up — 25 times the fit's own 0.168 K
residual. Λ(T) between 40 K and 98 K had been an interpolation between anchors
twenty percentage points apart, and there was no measurement in it to argue.

| u% | model | measured |
|---|---|---|
| 51.708 | 28.24 | 28.59 |
| 57.108 | 42.90 | 45.28 |
| 60.798 | 72.37 | 77.10 |
| 63.699 | 109.97 | **114.28** |

That last row is the one to keep in mind when setting `--max-k`: the ceiling for
that run was 120 K, chosen off a model that turned out to be 4.3 K low, and it
finished with 5.7 K of headroom.

The run is versioned as the manifest window `trace-ladder-20260905` in
`reference/cooldown-10/`, so `python analysis/measure.py` on a fresh clone
rebuilds every anchor including these -- 149 of them now, 147 inside 4-200 K.

**The simulator has both versions.** `ltspm3/model/sim_response.py` is the two-pole
model these legacy numbers describe, and the control harness is still calibrated
against it. `ltspm3/model/fitted_response.py` integrates the fitted ODE from a frozen
table, needs no scipy, and is what `ltspm3.tools.sweep --simulate` rehearses
against — because a sweep is the one thing that crosses the whole range, and the
two models disagree by up to 17 K in the middle of it. Regenerate the table with
`python analysis/export_response.py` after any refit.

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
> about 3τ. The full table is
> [below](#how-long-to-hold-a-step--and-why-r-will-not-tell-you).
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

**For fitting, load the archive, not these logs.**
`reference/cooldown-10/` is three flattened tables carrying `Timestamp`, `t_s`,
`segment`, the thermometers, `u_pct` and `note` -- and nothing else -- plus
`segments.csv`, which names the windows inside them. This half of the cooldown
is `cd10_20260715_prepython.csv.gz`, 298,617 rows, 857.9 h, 5 segments.
**Fit each `segment` as its own trajectory**: they are split at the recording
gaps, and this table's are 65 h and 187 h long. The 08-08 ladder is its
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

Worse than 1/√N means the noise is correlated, and [noise.md](noise.md) takes
that apart: the jitter and the bands it lives in, how far averaging actually
gets (measured, not modelled), and why the temperature scaling cannot by itself
say whether the floor is thermal or instrumental — the sample is a Cernox, and
an NTC loses sensitivity as it warms.

**Those numbers are from the archive and predate the 2026-09-04/05 rework** —
new wiring, the Magnet on input 5, the 218's filter on, 4 Hz per input. They
describe fixed logs and remain true of them; they are not a description of the
cryostat as it now stands.

## How long to hold a step — and why R² will not tell you

Measuring `K` and `τ` is the highest-value hardware measurement there is, and the
window you fit over decides whether the answer means anything. **A short hold
does not give a noisy answer. It gives a confident wrong one.**

Fitting a single exponential over a window much shorter than τ cannot separate
"slow rise, large amplitude" from "fast rise, small amplitude" — the early part
of both is a straight line — so the fit trades τ against K and lands somewhere
plausible. Simulated against the calibrated two-pole response (τ_fast = 620 s
carrying 90 % of the step, τ_slow = 14400 s the rest), stepping 1.0 % at 65 %
output where the true steady-state K is 13.38 K/%:

| hold | K (K/%) | τ (s) | R² | K vs truth | τ/K |
|---|---|---|---|---|---|
| 5 min | 4.53 | 126 | **0.947** | 34 % | 27.7 |
| 10 min | 7.38 | 234 | **0.968** | 55 % | 31.7 |
| 15 min | 9.15 | 328 | **0.981** | 68 % | 35.9 |
| 20 min | 10.26 | 406 | 0.989 | 77 % | 39.6 |
| **30 min** | **11.42** | **520** | 0.997 | **85 %** | **45.5** |
| 45 min | 12.03 | 606 | 1.000 | 90 % | 50.4 |
| 60 min | 12.23 | 642 | 1.000 | 91 % | 52.5 |
| 90 min | 12.39 | 674 | 0.999 | 93 % | 54.4 |
| *truth* | *13.38* | *620* | | | *46.3* |

At five minutes the fit reports τ five times too small and K three times too
small, **at R² = 0.95** — which reads as a good fit and is not one. R² measures
how well an exponential describes the window you gave it, and a rising line is
described beautifully by the early part of any exponential you like.

Two things follow, and they pull in opposite directions:

- **Do not fit anything held for under ~20 minutes** at these temperatures, no
  matter what R² says. Below that the numbers are not merely imprecise, they are
  wrong by factors.
- **90 minutes is not necessary either.** IMC tuning uses `Kp = τ/(K·τ_cl)`, and
  the two biases run in the same direction, so they largely cancel in the ratio:
  τ/K passes through its true value at **around 30 minutes** and drifts *away*
  again by 45–90 min as the slow pole leaks in. A 30-minute hold gives a better
  `Kp` than a 90-minute one, while the individual K and τ it prints are each
  about 15 % low.

**So: hold ≈ 3τ, and record the window with every number.** Thirty minutes is
right where τ ≈ 620 s. Where τ is genuinely shorter the window shrinks with it,
which is the whole point of the section above — τ runs from under a second at
10 K to 489 s at 110 K, so one dwell is wrong at both ends. In practice: watch
the viewer, find the time to reach about two thirds of the move, and hold three
times that.

**Read the R², every time, before you believe a τ.** A 0.061 % step held for
21 hours in the archive "identifies" τ = 137,345 s — 38 hours — at **R² =
0.162**. There is no exponential in it; the fit is describing noise, and the
number it produces is confidently, catastrophically wrong. `analyse_step`
returns the R² in `OperatingPoint.note` and refuses a gain of the wrong sign,
but nothing stops a poor fit with a plausible-looking gain from being pasted
into a schedule.

Two more rules the existing hand data teaches:

- **Step big enough to be seen.** At ~14 K/% and a few tens of mK of noise, a
  0.06 % step is ~0.85 K of signal spread over an hour and the drift wins. The
  0.5 % step that identified cleanly moved 10.8 K. Below ~0.2 % is not worth
  the hour.
- **Step once and hold.** Several hand steps in the archive are up-down doublets
  held for tens of seconds, and every one fails identification with "the
  temperature moved against the step" — the segment begins after a *down* step
  while the cryostat is still rising from the preceding *up* step. Doublets test
  hysteresis only after each leg has settled.

**Any τ in these documents without a stated fit window should be read with that
table in hand**, including the τ = 709 s above.

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

`ltspm3/model/thermal_response.py` deliberately keeps **`P(pct)`** and **`T(P)`** apart, and both
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
| `ltspm3/model/thermal_response.py` | the one measured `P(pct)` / `T(P)` curve |
| `ltspm3/model/sim_response.py` | two-pole calibrated model + measured cross-channel coupling |

`sim.speedup` accelerates the thermal response but **not** the controller.
