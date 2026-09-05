# `analysis/` — fitting the LTSPM3 thermal model

Exploratory, not shipped. Nothing here is imported by `lschart` or `ltspm3`,
and **nothing in `control/` was changed** by any of it. These are the numbers
for a decision, not the decision.

The outputs are gitignored — CSVs and PNGs are regenerated in a few minutes.
**The inputs are not**: they live versioned in
[`reference/heater-calibration/`](../reference/heater-calibration), gzipped,
and everything here runs from a fresh clone with no setup beyond
`pip install -e ".[analysis]"`.

## Where the inputs live, and why they are in the repo

This repository gitignores derived data as a rule, and these three break it
deliberately, because of what they are derived *from*:

| | |
|---|---|
| `region_20260903-123832_complete_sweep.csv.gz` | the 8.8 h sweep, 5–187 K. **Irreplaceable** — a recorder export of a run that happened once. Nothing regenerates it. |
| `fit_recorder.csv.gz` | flattened from the recorder's own 2026-08/09 logs, which are *not* in the repo. Derived, but from a source a clone does not have, so primary in practice. |
| `fit_cd10.csv.gz` | the one genuinely regenerable file, from the versioned `reference/logs/CD10/*.xls`. Committed anyway, so step one of the pipeline does not fail until somebody finds a two-command dance. |

Also there: `..._even_larger.csv.gz`, the same export widened to
2026-09-02 → 09-04. Nothing uses it and it is **not** a drop-in for the sweep —
its tail crosses the 12:07 2026-09-04 Coldplate recalibration. It is kept as
the raw archive the sweep was cut from.

Gzipped because git stores the same compressed bytes either way, so plain CSV
would only buy 79 MB in every working tree instead of 13 — including for
coworkers who wanted the strip chart and nothing else. `analysis/_data.py`
opens either transparently, resolves names against the repository rather than
the working directory, and when something really is missing it says which file
and what to do.

## The model

Everything the sample touches sinks at the coldplate — structure, wiring and
radiation alike — so the paths are in parallel between the same two nodes and
add. Radiation to a common cold end has the same potential-difference form as
a conduction link, so it folds in rather than sitting outside as a source:

```
Λ(T) = Λ_struct(T) + Λ_w(T) + σ_r T⁴          the conductance integral
C(T) · dT/dt = Q(u) − [ Λ(T) − Λ(T_c(t)) ]     u(t) and T_c(t) driven from the log
Q(u) = (G · 10 V · u/100)² / R                 G ≈ 1.11 voltage gain, R = 75.5 Ω
```

At steady state `Λ(T_s) = Q + Λ(T_c)` with no heat capacity in it, so a settled
dwell measures `Λ` directly. The transients then measure `C`, and
`τ = C / (dΛ/dT)`.

## Order things must run in

```bash
# 1. dwells -> steady points and time constants        (~1 min)
#    no arguments: it defaults to the three versioned tables
.venv/Scripts/python.exe analysis/steps.py

# 2. the complexity ladder -> analysis/ladder.csv      (~15 min)
.venv/Scripts/python.exe analysis/fit_ode.py

# 3. the figures                                       (~5 min)
.venv/Scripts/python.exe analysis/diagram.py
.venv/Scripts/python.exe analysis/plot_gain.py
.venv/Scripts/python.exe analysis/plot_ode.py
.venv/Scripts/python.exe analysis/pid_tuning.py
.venv/Scripts/python.exe analysis/settling.py
```

`pip install -e ".[analysis]"` for scipy and matplotlib; the recorder itself
needs neither.

| | |
|---|---|
| `steps.py` | every constant-heater dwell fitted as `T = T∞ + A e^(−t/τ)`. Gives `T∞` extrapolated, `τ` measured, and the extrapolation distance as an error bar. **Read the `U_TOL_PCT` note**: the 218's readback flickers between adjacent codes, and an exact match shreds every dwell below 29 K. |
| `fit_ode.py` | integrates the ODE down the 8.8 h sweep and fits Λ and C as monotone cubics in (log T, log y). One curve's knots freed at a time. Writes `ladder.csv`. |
| `_data.py` | where the inputs live and how to open them; every reader here goes through it |
| `fit_lambda.py` | asks whether the settled points alone can separate `σ_r T⁴` from conduction. They cannot — see below. |
| `diagram.py` | the model, with each ODE term on its arrow |
| `plot_gain.py` | heater → steady temperature, on both a percent and a **power** axis. The watts one is the one to hand to somebody on a different cryostat with the same heater. |
| `plot_ode.py` | trajectory, residual, Λ, dΛ/dT, C, τ, and the ladder |
| `pid_tuning.py` | SIMC PI gains scheduled against T, on the loop *as configured* |
| `settling.py` | why a settle takes 20–30 min, and what a 10 K/min sweep needs |

## What came out

| | |
|---|---|
| fit quality | 0.284 K rms over 8.8 h and 5–187 K; 0.256 K rms / 2.12 K max outside one 9-minute slew |
| `dΛ/dT` | peaks ~25 mW/K near 13 K, falls to 1.8 by 150 K — the link's conductivity maximum |
| `C(T)` | a 4.7 g Cu/sapphire/diamond Debye mix |
| `τ(137 K)` | 536 s fitted, against 620 s and 709 s measured independently |
| local gain | 0.58 → 13.4 K/%, or 40 → 650 K/W; nearly all the change between 50% and 60% |

## Every `T_c` here is pre-calibration — 2026-09-04

`Coldplate` is not a passenger column in this directory. It is `T_c`, and it
enters the model directly:

```
Λ(T_s) = Q + Λ(T_c)          at steady state
```

On **2026-09-04 at 12:07** a transposed digit was corrected in the Coldplate's
calibration curve — a 6 where a 9 belonged — which had the cold end reading
high for as long as that curve was loaded. **Every input to every fit here
predates that**: `fit_lambda`, `fit_ode`, `plot_gain`, `plot_ode` and `steps`
all read `Coldplate` from `reference/heater-calibration/`, which is built from
pre-cutover logs. See [cryostat.md](../docs/ltspm3/cryostat.md).

**This very likely explains the second caveat below.** A sample settling 0.79 K
*beneath* its own heat sink is not physics; it is the sink reading high. That
the correction is of the same order as the anomaly is suggestive, not proof —
neither number has been measured against the other yet.

How much the fitted `Λ` moves is *not* known and is not guessed here. The
expectation is "little": `T_c` enters only as `Λ(T_c)`, evaluated at the very
bottom of the conductance curve where `Λ` is smallest, against an `Λ(T_s)` at
100–200 K. But an expectation is not a result, and the anomaly above is the
standing reminder that the cold end is where this model is weakest.

> **TODO, in this order** — reprocess the pre-cutover logs onto the corrected
> curve; rebuild `reference/heater-calibration/`; re-run the ladder in the
> order given below; then check whether the 0.79 K caveat survives. If it does
> not, the "model undefined below ~12 K" restriction may be liftable, and the
> stray magnet-side load it was blamed on may not exist.

Nothing in `control/` depends on any of this — see the top of this file — so
none of it is urgent, and none of it is a safety matter.

## Caveats that outlive the numbers

- **The two cooldowns differ by ~3.2 K at matched power.** Absorbed by
  per-anchor margins, not modelled. `Λ` below ~20 K rests on this cooldown's
  dynamics alone.
- **At zero power the sample settles 0.79 K *below* the coldplate reading.**
  Thermometry plus stray magnet-side load. No increasing `Λ` can represent it,
  so the model is undefined below ~12 K and the plots say so. **Read this
  against the recalibration note above: the leading suspect is now the
  coldplate thermometer, not a stray load.**
- **`C(T)` is the weakly constrained half.** The ladder buys 24× from Λ knots
  and ~10% from C knots; the 20 measured τ pin it to about ±30%. Anything
  sized on `C` — the velocity feedforward gain especially — inherits that.
- **Radiation is not separable.** Fitted with and without a free `σ_r T⁴` the
  residuals are identical, because it is a pure `T⁴` addition to an already
  free-form `Λ`. Bounded at `εA ≤ 2.4 cm²` (ε = 1) rather than fitted.
- **A second node was tested and refused.** Split so the steady state is
  provably unchanged, the fit drove the split to 0.999 — the middle node
  collapsing onto the coldplate — and rms got worse.
