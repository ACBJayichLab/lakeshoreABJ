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
| `sweep_decimated.csv.gz` | the sweep again, adaptively thinned by `decimate.py` — 4,968 rows and 46 kB against 77,375 and 1.4 MB. Regenerable in twenty seconds, committed because it is what the production fit actually reads. |

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

# 2. the complexity ladder -> analysis/ladder.csv      (~15 min, 4 workers)
.venv/Scripts/python.exe analysis/fit_ode.py

# 2b. the coldplate's own pole                        (~30 s)
.venv/Scripts/python.exe analysis/bath.py

# 2c. re-thin the sweep, if the sweep ever changes     (~20 s)
#     the thinned table is versioned, so this is not part of a normal run
.venv/Scripts/python.exe analysis/decimate.py --write

# 3. the figures                                       (~5 min)
.venv/Scripts/python.exe analysis/diagram.py
.venv/Scripts/python.exe analysis/plot_gain.py
.venv/Scripts/python.exe analysis/plot_ode.py
.venv/Scripts/python.exe analysis/pid_tuning.py
.venv/Scripts/python.exe analysis/settling.py
```

`pip install -e ".[analysis]"` for scipy and matplotlib; the recorder itself
needs neither.

**Converged fits are cached** under the gitignored `analysis/.fit_cache/`,
keyed on a digest of the sweep, the anchors, the taus and the knot counts.
Step 2 is the only expensive one -- on the 43 h sweep a single 9-knot fit is a
quarter of an hour -- and steps 3 to 6 all wanted the same (9, 4) model, so
each of them used to pay for it again. Now the ladder pays once and the figures
take seconds. The key contains the data, so remapping `T_c` invalidates every
entry by itself; it cannot see a change to the objective in `fit_ode.py`, which
is what `FIT_CACHE_VERSION` is for. Delete the directory to force a refit.

| | |
|---|---|
| `steps.py` | every constant-heater dwell fitted as `T = T∞ + A e^(−t/τ)`. Gives `T∞` extrapolated, `τ` measured, and the extrapolation distance as an error bar. **Read the `U_TOL_PCT` note**: the 218's readback flickers between adjacent codes, and an exact match shreds every dwell below 29 K. |
| `fit_ode.py` | integrates the ODE down the 8.8 h sweep and fits Λ and C as monotone cubics in (log T, log y). One curve's knots freed at a time. Writes `ladder.csv`. |
| `decimate.py` | the sweep, thinned where nothing is happening and kept where it is. **16x fewer samples, 26x faster to fit, 0.8% different.** Writes `sweep_decimated.csv.gz` |
| `bath.py` | the coldplate as a first-order lag driven by the heater, not as a bath. **tau = 175 s, 27.6 mK rms over a 2.30 K swing.** What makes the plant self-contained |
| `_data.py` | where the inputs live and how to open them; every reader here goes through it |
| `fit_lambda.py` | asks whether the settled points alone can separate `σ_r T⁴` from conduction. They cannot — see below. |
| `diagram.py` | the model, with each ODE term on its arrow |
| `plot_gain.py` | heater → steady temperature, on both a percent and a **power** axis. The watts one is the one to hand to somebody on a different cryostat with the same heater. |
| `plot_ode.py` | trajectory, residual, Λ, dΛ/dT, C, τ, and the ladder |
| `pid_tuning.py` | SIMC PI gains scheduled against T, on the loop *as configured* |
| `settling.py` | why a settle takes 20–30 min, and what a 10 K/min sweep needs |

## What came out

Re-run 2026-09-05: 43 h sweep, corrected `T_c`, current dwell grader. Every
number below came out of that run. The full ladder is `analysis/ladder.csv`.

| | |
|---|---|
| fit quality | **0.2024 K rms** over 43 h and 4.9–192.6 K — Λ 12 knots, C 4, 3 drift knots, fitted on the decimated sweep and scored on the full one. 10.9 K max, all of it inside one 9-minute recovery slew. Without the drift term, 0.404 K at Λ 10 |
| what the residual is made of | **bias, not noise.** Inside the 22.8 h hold at 180.5 K the scatter is **49 mK** and the level is **−0.39 K**; inside the 13.9 h hold at 192.4 K, 48 mK and **+0.55 K**. The model reproduces each hold thirty times better than it places the pair |
| `dΛ/dT` | peaks ~25 mW/K near 10–13 K, 1.60 mW/K at 100 K, 1.79 at 180 K — the link's conductivity maximum |
| `C(T)` | a **4.89 g** Cu/sapphire/diamond Debye mix |
| `τ(137 K)` | **572 s** fitted, against 620 s and 709 s measured independently; ×1.22 in log against all 17 |
| local gain | 0.24 → 13.7 K/%, or 41 → 665 K/W. Nearly all the change is between 50% and 60% |
| how much complexity buys | Λ knots 3→10: rms 5.23 → 0.404 K, flattening after 8. **C knots 2→7: 0.625 → 0.438 K, and 3 knots already gets 0.456.** C is the weakly constrained half and the ladder says so |

**Two things the re-run settled, both negative.** Dropping the 34 CD10 anchors
— a different cooldown, which disagrees with this one by ~3.2 K at matched
power and which the fit misses by −2.62 K on average — moves the sweep rms from
0.4467 to 0.4383 K. They are not what limits the fit. And **quantile knot
placement is much worse, not better** (27 K rms): the sweep spends 36 of its
43 hours above 175 K, so quantiles collapse to three knots and starve the cold
end. Geomspace stays.

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

## The coldplate needed a pole — 2026-09-05

`fit_ode` takes `T_c(t)` as an exogenous input, read from the log. For fitting
that is exact and free. For **control** it is silently useless: raising the
sample heater warms the coldplate too, and that comes back as a change in the
sample's own sink temperature. A plant that takes `T_c` as given cannot see
that loop, and there is no log to read from when you are predicting.

So `bath.py` fits it — one pole on a monotone steady-state curve in heater
power, over the whole 43 h sweep:

| | |
|---|---|
| `tau_bath` | **175 s** (2.9 min) |
| whole record | **27.6 mK rms**, 617 mK max, over a **2.30 K** swing — 1.2% of it |
| the 22.8 h opening hold | 1.5 mK rms |
| the 13.9 h closing hold | 3.8 mK rms |
| the 7.5 h excursion | 65.8 mK rms |
| `T_c` at u = 0 / u = 70% | 4.80 K / 6.93 K |

**It is not a small effect for tuning.** `tau_bath / tau_sample(137 K) = 0.31`:
the sink moves at a third of the sample's rate, which is neither of the two
things a fixed-sink plant can assume. The pole helps — warming the sink cuts
the gradient the heater has to hold — so gains sized on the fixed-sink plant
are conservative rather than unsafe, but they leave performance on the table.
**`pid_tuning.py` and `settling.py` do not yet use it.**

**It does not explain the steady-state bias.** Both long holds are settled to
0.1 mK/h in `T_c`, and a lag cannot produce a steady-state error by
construction. That one is Λ shape at the top of the range: 12 knots takes the
sweep to **0.3225 K** rms and the hold biases to −0.28 / +0.35 K.

## The sweep is 16x smaller now, and the fit did not notice

52% of the sweep is one hold: 22.8 h at 69.027% over which the sample moves
87 mK in total. Every fit integrated all of it, and `least_squares` did it
again per parameter per iteration to difference the Jacobian — so a
14-parameter step integrated a day and a half of a cryostat sitting still,
fourteen times.

`decimate.py` keeps a sample when the temperature has moved by 50 mK, when the
heater changed, or when 300 s have passed, and weights each kept sample by
`sqrt(span)` so the objective stays the *time* integral it was:

| | |
|---|---|
| samples | 77,375 → **4,968** (16x), steps 2–300 s |
| the 22.8 h opening hold | 40,500 → 271 (**149x**) |
| the 7.5 h excursion | 13,500 → 4,538 (3x) — the informative part is barely touched |
| (9, 4) fit time | 190 s → **7.3 s** |
| (9, 4) rms, **both scored on the full 77,375-sample grid** | 0.4467 → 0.4504 K (**0.8%**) |
| `tau(137 K)`, implied mass | identical to three figures |

Two things make that free rather than merely cheap. `integrate` reads its step
from `t[k+1] - t[k]` and exponential Euler is *exact* for relaxation towards a
constant target, which is what a hold is — a 300 s step across constant `u` and
`T_c` is not an approximation of 150 two-second steps, it is the same answer.
And the decision to keep is made on a **60 s-smoothed** copy, because comparing
raw samples asks "has the temperature plus 28 mK of noise changed", which on a
dead-flat hold is yes, constantly. That one detail is the difference between 5x
and 16x.

## The steady state drifts; the dynamics do not — 2026-09-05

The sweep's two settled holds were missed in opposite directions, −0.39 K at
180.5 K and +0.55 K at 192.4 K, with only 49 mK of scatter inside each. And
during the 22.8 h opening hold, at a heater that never moves, the sample drifts
**−3.8 mK/h** while every other channel in the cryostat is flat to ±1.5 mK/h.
Something slow was changing the steady state and it is not in the log.

So `fit(..., n_drift=k)` adds a slow unmeasured load, a few knots in **time**:

```
C(T) dT/dt = Q(u) + P_drift(t) − [ Λ(T) − Λ(T_c(t)) ]
```

| model | rms K | hold bias K | `tau(137 K)` | mass g |
|---|---|---|---|---|
| Λ 9, no drift | 0.4504 | −0.400 / +0.552 | 572 s | 4.89 |
| Λ 9, drift 3 | 0.2516 | −0.134 / +0.217 | 566 s | 4.85 |
| Λ 12, no drift | 0.3223 | −0.283 / +0.342 | 595 s | 4.89 |
| **Λ 12, drift 3** | **0.2024** | **−0.103 / +0.137** | 589 s | 4.85 |
| Λ 12, drift 6 | 0.2090 | −0.096 / +0.112 | 590 s | 4.84 |

All scored on the full grid. **The rms halves for three parameters, and
`tau(137 K)` and the implied mass do not move.** That separation is the whole
point: with three knots over 43 h the drift cannot move faster than about 11 h,
against `tau = 572 s` for the sample — four orders of magnitude too slow to
stand in for a relaxation, so Λ and C still have to earn every transient.

The fitted drift is **2 mW peak to peak on an 800 mW heater**, 0.25%, and
`DRIFT_SIGMA_W` holds it there. What it *is* remains unidentified: no logged
channel moves with it, and the aux thermometers are flat to tens of mK across
the whole 43 h.

`plot_gain.py` now draws this model — Λ 12, C 4, drift 3, on the decimated
sweep. `plot_ode.py` deliberately does not: it is the complexity study, and it
compares knot counts on the full grid with no drift so the fan-out means what
it has always meant.

## Method, and what is wrong with it

**The ladder is parallel now.** The rungs are independent least-squares
problems and were being run one at a time; `LADDER_WORKERS` (default 4, set it
to 1 for a readable traceback) runs them in processes. Processes rather than
threads because the cost is `integrate`, a Python loop over 77,375 samples
holding the GIL throughout.

**Three rungs in the shipped ladder did not converge.** Λ at 6, 7 and 8 knots
all stopped at `nfev = 300 = max_nfev`; their rms figures — 0.877, 0.594,
0.497 K — are *upper bounds*, not fits. Λ at 9 and 10 knots converged in 88 and
78 evaluations, which is the odd part: more parameters, a quarter of the work.
So "the ladder flattens after 8 knots" is partly an artefact of the cap, and
the flattening should not be quoted as evidence about the cryostat until those
three are re-run. Raising `max_nfev` changes the cache key and invalidates
every stored fit, which is why it has not been done in passing.

**The Jacobian is the cost.** `least_squares` differences it, one extra
77,375-sample integration per parameter per iteration, so a 14-parameter fit
integrates the sweep fourteen times to take one step. The two ways out are an
adjoint gradient and a coarser residual grid; `STEP_S` is already at the log's
own 2 s cadence for a documented reason (coarsening to 4 s triples the worst
residual), so the grid is not free to move.

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
