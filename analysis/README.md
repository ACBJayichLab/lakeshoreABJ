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
| `region_..._complete_sweep_even_larger.csv.gz` | the sweep, 2026-09-02 16:01 → 09-04 11:00. 43 h, 4.9–192.6 K, 2 s cadence, no gap over a minute. **Irreplaceable** — a run that happened once. |
| `fit_recorder.csv.gz` | flattened from the recorder's own 2026-08/09 logs, which are *not* in the repo. Derived, but from a source a clone does not have, so primary in practice. |
| `fit_cd10.csv.gz` | the one genuinely regenerable file, from the versioned `reference/logs/CD10/*.xls`. Committed anyway, so step one of the pipeline does not fail until somebody finds a two-command dance. |

**The 8.8 h cut of the same run that used to be here is gone** (2026-09-04,
Jeff). It saw only the middle: 2.2 h of the 22.8 h hold at 180 K and 0.4 h of
the 13.9 h hold at 192 K, and those long holds are what pin the slow bath
behaviour. It survives in git history and in the gitignored `data/`; it is not
on the remote because the wide export contains it.

The wide export ends **67 minutes before** the 12:07:16 2026-09-04 Coldplate
recalibration, so it is entirely pre-cutover and internally consistent. (An
earlier revision of this file claimed its tail crossed the cutover. That was
wrong — 11:00 is before 12:07.)

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

> **STALE — re-run before quoting. 2026-09-04.** Every number in this section
> was computed on the 8.8 h sweep and on the pre-2026-09-04 dwell grader. Both
> have since changed: the sweep is now the 43 h wide export, and `steps.py`
> decides a dwell on how fast it was still moving at its end rather than on how
> far it moved in total. `steps.py` has been re-run (65 graded dwells, 17 with
> a usable τ, 5.1–192.4 K, now including holds of 144.4 h, 74.9 h and 22.8 h
> that the old rule discarded). **`fit_ode.py` and the figures have not.**
> Re-run them in the order below, then replace this table and delete this note.

| | |
|---|---|
| fit quality | 0.284 K rms over 8.8 h and 5–187 K; 0.256 K rms / 2.12 K max outside one 9-minute slew |
| `dΛ/dT` | peaks ~25 mW/K near 13 K, falls to 1.8 by 150 K — the link's conductivity maximum |
| `C(T)` | a 4.7 g Cu/sapphire/diamond Debye mix |
| `τ(137 K)` | 536 s fitted, against 620 s and 709 s measured independently |
| local gain | 0.58 → 13.4 K/%, or 40 → 650 K/W; nearly all the change between 50% and 60% |

## `T_c` has been remapped — 2026-09-05

`Coldplate` is not a passenger column in this directory. It is `T_c`, and it
enters the model directly:

```
Λ(T_s) = Q + Λ(T_c)          at steady state
```

Until **2026-09-04 12:07** the 218 was carrying another thermometer's
calibration on input 2 — X186276's, where the Coldplate is X186279 — so the
cold end read high by 12–13% of absolute temperature. Every fit in this
directory was originally computed from those values.

**The three tables in `reference/heater-calibration/` have since been remapped
in place**, kelvin → resistance → kelvin, by

```bash
python -m lschart.tools.recalibrate --column Coldplate     --from reference/sensor-curves/X186276.340     --to   reference/sensor-curves/X186279.340     "reference/heater-calibration/*.gz" -o data/coldplate-recal/fit-inputs
```

so `fit_lambda`, `fit_ode`, `plot_gain`, `plot_ode` and `steps` now read the
corrected `T_c` with no argument and no code change. The as-logged tables are
in git history at `72c3f32`. Only `Coldplate` moved; every other column is
byte-identical. See [cryostat.md](../docs/ltspm3/cryostat.md).

**908 rows of `fit_cd10` came back blank rather than converted.** They were
clamped at the top of the loaded table — the wrong curve rails at 330.324 K —
and a clamped reading has no resistance behind it to convert. They are the
first ninety minutes of the CD10 cooldown, at room temperature, and every
loader here already drops a NaN `T_c`.

### It settles the 0.79 K anomaly

The caveat below used to read "at zero power the sample settles 0.79 K *below*
the coldplate reading — thermometry plus stray magnet-side load". A sample
cannot rest colder than its own heat sink, so something in that pair had to be
wrong. It was the thermometry, and the remap does not merely shrink the
anomaly, it removes it:

| | rows where `Sample` < `Coldplate` |
|---|---|
| as logged | 13,290 of 811,292 across the three tables, worst −30.19 K |
| remapped | **0**, in all three, at every power |

At zero power the sample now sits **+0.29 K above** the coldplate (96 settled
rows, min +0.21 K), which is an ordinary small parasitic load. That the count
goes to exactly zero — not "mostly" — is the strongest evidence available that
X186279 is the right curve, and it is independent of anything in the curve
files themselves.

**The consequence for the model has not been re-derived yet.** The "model
undefined below ~12 K" restriction was a consequence of the anomaly, so it is
now a candidate for lifting, and the stray magnet-side load it was blamed on
may not exist. That is the next thing to check, not something this note claims.

Nothing in `control/` depends on any of this — see the top of this file — so
none of it is urgent, and none of it is a safety matter.

## Caveats that outlive the numbers

- **The two cooldowns differ by ~3.2 K at matched power.** Absorbed by
  per-anchor margins, not modelled. `Λ` below ~20 K rests on this cooldown's
  dynamics alone.
- **~~At zero power the sample settles 0.79 K *below* the coldplate
  reading.~~ RESOLVED 2026-09-05** — it was the coldplate thermometer, not a
  stray magnet-side load, and the remap above puts the sample +0.29 K *above*
  its sink where it belongs. The "model undefined below ~12 K" restriction was
  a consequence of this caveat and the plots still say so; both need re-deriving
  on the corrected `T_c`.
- **`C(T)` is the weakly constrained half.** The ladder buys 24× from Λ knots
  and ~10% from C knots; the 20 measured τ pin it to about ±30%. Anything
  sized on `C` — the velocity feedforward gain especially — inherits that.
- **Radiation is not separable.** Fitted with and without a free `σ_r T⁴` the
  residuals are identical, because it is a pure `T⁴` addition to an already
  free-form `Λ`. Bounded at `εA ≤ 2.4 cm²` (ε = 1) rather than fitted.
- **A second node was tested and refused.** Split so the steady state is
  provably unchanged, the fit drove the split to 0.999 — the middle node
  collapsing onto the coldplate — and rms got worse.
