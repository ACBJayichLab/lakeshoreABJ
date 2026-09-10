# Thermal model refit — plan

**Status: PHASE 0 DONE. Awaiting the manifest review — the first hard pause.**
Prerequisite work is at `da295af`; Phase 0 is at `6432128` (the archive and the
manifest) and the commit after it (the rewiring and the deletion).
**Shape:** three phases, two hard pauses. Phase 0 → *pause* → Phase A → *pause* → Phase B.
Update this Status line as phases land.

### Where a new session picks up

| | |
|---|---|
| **done** | §5.1 the archive — `reference/cooldown-10/`, three non-overlapping tables covering the whole cooldown, with a README carrying provenance, segment boundaries and caveats |
| **done** | §5.4 — the fresh export past 2026-09-09 18:06. The transient is in `cd10_20260904_recorder.csv.gz` segment 0, recording continuous across it |
| **done** | §5.2 the manifest — `reference/cooldown-10/segments.csv`, 239 windows. §5.3 `analysis/segments.py` + `analysis/curate.py` |
| **done** | the rewiring. `_data.py`, `steps.py`, `plot_ladder.py`, `decimate.py` and `fit_ode.load_sweep` read the archive; the five old tables are deleted |
| **next** | **a human reads the manifest and agrees with it.** Then Phase A (§6) |

Everything in `analysis/` reads the archive. The five overlapping tables in
`reference/heater-calibration/` are gone; only `sweep_decimated.csv.gz`
remains there, regenerated, which makes that directory a candidate for
retirement — deliberately not done in passing.

**Every fit number in this document below was produced with the OLD tables**,
and they still hold: the rewiring was measured against them and moves the
Λ12/C4/drift-3 fit from `rms_k` 0.2113 to 0.2129, τ(137 K) from 567.8 to
568.2 s, and the implied mass from 4.7965 to 4.7956 g. What moved is `T_c`, by
1.2 mK rms — the region export had been rounded before the Coldplate remap —
and two anchor boundaries the exports had cut. See `analysis/README.md`,
"Curate, do not discover".

Also worth knowing before Phase B: the pre-rewire fit stopped at
`nfev = 300 = max_nfev` and the post-rewire one converged in 182. The
production preset was hitting the cap, so the "before" rms above was an upper
bound rather than a fit — the same problem the README records for three rungs
of the ladder.

---

## 1. The goal

Produce a thermal model of the LTSPM3 cryostat that reproduces, **at the same
time and without retuning between them**:

| | target | where it stands today |
|---|---|---|
| the three long settled holds | < 0.3 K | **2.36 / 2.65 / 2.81 K low** |
| the 2026-09-05 ladder, 31 rungs | < 0.5 K rms | **1.36 K rms**, 2.66 K max |
| every measured τ, 40–120 K | < 10 % | **passes** (see §2.4) |

and to do it from data that is **organised, named and frozen** rather than
rediscovered by a heuristic each time a fit runs.

That last clause is half the work and it is not decoration. The fit **used to**
scan five overlapping tables and *discover* 234 dwells with a rule keyed on a
tolerance constant; change the constant and the dataset silently changed, with
nothing in review to show it. **Phase 0 fixed this** — the dataset is
`reference/cooldown-10/segments.csv` and a change to the finder is a diff. The
rest of §2 is the reasoning that motivated the work and is left as it was
written.

**Definition of done.** All three rows above green; `ltspm3/_fitted_table.py`
regenerated; `SUPERSEDED_NOTE` cleared; full suite and `ruff` clean; the docs
corrected. Nothing in `lschart/` or `ltspm3/control/` touched.

---

## 2. Why — the reasoning, with the numbers

A session picking this up cold should be able to re-derive every number here.
All of them came from `analysis/steps.csv` and the recorder logs in `data/`.

### 2.1 The shipped model is 4.4 K low, and three independent holds say so

The post-recalibration recorder log holds three stretches at a fixed output,
each settled to under 1.4 mK/h, that **no fit input contained** until `da295af`:

| hold | output | measured | shipped table | miss |
|---|---|---|---|---|
| 11.52 h | 62.347 % | 96.516 K | 92.17 K | +4.35 K |
| 69.94 h | 63.699 % | 114.390 K | 109.97 K | +4.42 K |
| 24.47 h | 64.015 % | 118.609 K | 114.16 K | +4.44 K |

Three consistent misses at three outputs. They also validate the ladder: the
114.28 K rung, which the old grader threw away, differs from the 70-hour hold at
the same output by **0.11 K**.

### 2.2 A first refit closed 40 % of it and stopped

Re-running the production fit on the new anchors: holds `+2.36 / +2.65 / +2.81`,
ladder rms 2.54 → 1.36 K. Then a weight study, over a 20× range:

| anchor share | trajectory rms | the three holds |
|---|---|---|
| 0.10 (current) | 0.170 K | +2.46, +2.83, +2.88 |
| 0.25 | 0.222 | +1.65, +2.08, +2.21 |
| 0.50 | 0.276 | +1.08, +1.54, +1.75 |
| 1.00 | 0.329 | +0.67, +1.12, +1.40 |
| 2.00 | 0.376 | +0.45, +0.82, +1.15 |

Twenty times the weight, the trajectory residual more than doubled, and three
holds settled to 1.4 mK/h were **still** missed by ~1 K. **This is not a
weighting problem.** The model as posed cannot satisfy both bodies of evidence.

### 2.3 The reason: the cryostat drifts, and the fit assumes it does not

Regressing 109 graded anchors' `T_inf` jointly on heater output **and date**,
over the 23 anchors in one narrow output band spanning 16 days:

- local gain **+13.20 K/%** — an independent check; the fit says 13–15 there
- drift **+0.167 K/day at fixed output**, residual **0.32 K rms**

In the fit's own currency that is **+0.27 mW/day of equivalent parasitic load**.
Over the ~20 days separating the July–August anchors from the September ones it
comes to 5.5 mW, against the **4.8–5.4 mW** the fit independently measures as a
per-era step offset. The same number, two ways.

Jeff's reading, which the sign agrees with: reduced cooling power over time.

**The anchors span 54 days. That is ~9 K of drift, and the fit treats them as
simultaneous apart from one two-group step.** The resulting smear is the size of
the residual that would not close. Everything in Phase B follows from this.

Corollary that matters for scope: **`fit_cd10` is not a different cooldown.**
Cooldown 10 began 2026-07-15 and is still running; the `.xls` logs are its
pre-Python half. The `ANCHOR_SIGMA_K["fit_cd10"] = 3.0` bar exists *because* of
the July/September disagreement — which is to say the drift is already being
forgiven, once, at three kelvin. See trap T3.

### 2.4 The dynamics are not in dispute

τ from the first refit against τ measured, across the band that matters:

```
  measured   185  241  302  361  414  467  513  s
  fitted     193  250  302  360  400  448  484  s
```

This is why the work splits into two stages. τ is a strong measurement and it
already travels. The steady state is the weak one, and it is weak for a reason
that is now identified.

### 2.5 There is also a load path the model does not have

At 18:06 on 2026-09-09 a power transient stepped the cold-head channels at a
fixed 64.0154 % output:

| channel | change | σ |
|---|---|---|
| Sample | **−0.306 K** | 887 |
| Coldplate | −0.0076 K | 270 |
| 1st Stage | −0.080 K | 92 |
| 2nd Stage | −0.019 K | 65 |
| RAD SHIELD | −0.13 K, still falling | ramp |

The cold-head channels **stepped**; the sample and shield were still **ramping**
2.7 h later. Propagated through the fitted Λ′ ratio the coldplate step accounts
for only ~0.07 K of the sample's 0.31 K. Something moves the sample's steady
state at constant power that `Λ(T_s) − Λ(T_c)` cannot see.

**This is a Phase B diagnostic, not a Phase B term** — see trap T6.

---

## 3. Principles for this work

1. **A settled hold measures Λ directly; a trajectory measures it by inference.**
   At steady state `Λ(T_s) − Λ(T_c) = Q`, with no heat capacity in it. The
   current fit lets ~5,000 weighted trajectory samples outvote a 70-hour hold on
   a question the hold answers exactly. Stage A exists to stop that.
2. **Curate, do not discover.** A window is named, dated and committed; a
   heuristic change shows up as a manifest diff in review.
3. **Bytes live once.** Duplication is allowed only where an organised reason is
   written down next to it.
4. **Prove a refactor inert before changing any physics.** Phase B steps 1–4
   must reproduce today's numbers to the last digit.
5. **Invariant 1 holds throughout.** `analysis/` imports neither `lschart` nor
   `ltspm3` and is imported by neither. It reads CSVs and nothing else.
6. `ltspm3/control/` is not touched. `ltspm3/_fitted_table.py` is *generated*,
   never hand-edited.

---

## 4. What is already committed — `da295af`

Read this before starting; it is the foundation the phases assume.

- **The grader rule changed.** `MAX_END_RATE_K_PER_H` is a rate, and a rate
  cannot decide "is this relaxation over" on a plant whose τ runs 4 s to 500 s.
  A dwell is now settled if *either* the rate is under the bar *or* it ran
  `MIN_REACH` time constants with under `SETTLED_REMAINDER_K = 0.15` K left —
  measured on the **fitted pole**, not the last sample, which carries 30 mK of
  noise at 114 K. 0.15 K is half `ANCHOR_FLOOR_K`, so an admitted dwell's
  extrapolation lands at the smallest error bar any anchor gets.
  **92 → 109 anchors in range, 26 → 34 with a usable τ; the 40–98 K band 7 → 13.**
  Mirrored into `ltspm3/tools/sweep.py`, where it is also the dwell stop rule.
- **`fit_recorder_postcal.csv.gz` added** — the log carrying the three holds.
- **The CD10 framing corrected** in `_data.py`, `fit_ode.py`, `anchor_groups`.
- **`export_response.py` and `plan_sweep.py` now pass `groups=`**, as
  `plot_gain.py` already did.
- **A cache bug fixed:** `cache_key` covered the data and two objective
  constants and not the rest, so a study varying `ANCHOR_SHARE` got the first
  fit's answer back five times and the knob looked dead. Every objective
  constant is in the key now.

Not done: the refit itself. `ltspm3/_fitted_table.py` is untouched and still
carries its `SUPERSEDED_NOTE`.

---

## 5. Phase 0 — the data library

**Goal:** every number the fit uses comes from a named, committed window.
**Est:** 1 session. **Exit gate: a human reads the manifest and agrees with it.**

### 0.1 The archive — three non-overlapping tables

`reference/cooldown-10/`, one flattened table per continuous recording run,
built with the existing `lschart.tools.fit_table` (it keeps every thermometer
and drops only the dotted aux readbacks — and the thermometers are wanted now,
per §2.5). All three sources exist on this machine:

| file | span | built from |
|---|---|---|
| `cd10_20260715_prepython.csv.gz` | Jul 15 – Aug 20, 5 segments | `data/coldplate-recal/cd10/cd10_*.csv` |
| `cd10_20260824_recorder.csv.gz` | Aug 24 – Sep 04 12:07, ~5 segments | `data/coldplate-recal/recorder/ltspm3-heater_*.csv` |
| `cd10_20260904_recorder.csv.gz` | Sep 04 23:38 – present, 1 segment | `data/ltspm3-heater_2026-09-0[4-9].csv` |

Both Coldplate-corrected sets already exist under `data/coldplate-recal/`, so no
remap is re-run. Boundaries fall on real recording gaps; the middle one is also
the calibration cutover.

> **Trap:** glob `ltspm3-heater_*.csv`, **not** `ltspm3_*.csv`. The latter is a
> different recorder instance overlapping in time.

**Delete** both `region_2026*.csv.gz` and all three `fit_*.csv.gz`, and the
`steps.py` dedup they exist to defeat. Roughly size-neutral (~12 MB for
~12.5 MB) with the overlap gone. The rebuild also recovers **Sep 03 21:22 →
Sep 04 11:00**, which today only the sweep region export holds.

### 0.2 The manifest

`reference/cooldown-10/segments.csv` — one row per curated window:

```
id, kind, file, t_start, t_end, u_pct, T_lo, T_hi, use, quality, note
```

- `kind` — `jump` (driven step held a few τ) · `hold` (long settled) · `trace`
  (long trajectory for the ODE)
- `use` — `tau` · `steady` · `ode` · `excluded`
- `note` — why, in words: *"power transient 18:06, excluded"*

### 0.3 Loader and curator

- `analysis/segments.py` — `load(id)`, `by_kind()`, `by_use()`, through the
  existing `_data.open_table`. Validates on load: every window inside its file,
  none spanning a recording gap, no two of a kind overlapping.
- `analysis/curate.py --propose` — runs the dwell finder over the archive and
  prints a **diff against the committed manifest**. Nothing written without
  `--write`.

### 0.4 Also in this phase

Export a fresh window covering **2026-09-09 18:06**. Nothing versioned reaches
past 16:16 that day, so the transient of §2.5 is currently unfittable and
untestable. Cheap now, impossible later.

### Exit criteria — met, except the review

- ✅ `curate.py --propose` on a clean tree reports **no diff**.
- ✅ Anchor count and every graded `T_inf` reproduce `da295af`'s within 1 mK:
  **111 usable anchors, 109 in 4–200 K, 36 with a believable τ**, all unchanged,
  and 103 of the 105 shared graded anchors agree to **3 nK**. The other two, and
  the six rows that exist on one side only, are boundary effects — the archive
  has more of the same dwell where a region export cut it — and each has a
  `note` on its row. They are enumerated in `analysis/README.md`.
- ⏸ **The manifest has not been read by a human yet.** That is the pause.

Two things turned up doing it that were not in the plan.

**The middle archive table had been built without `--rename`.** The 218's
inputs 2 and 3 were relabelled at the 2026-08-26 part-roll, so 83,215 of its
461,849 rows carried the cold end as `Cold Head` with `Coldplate` blank — four
half-empty columns, and any fit reading `Coldplate` silently lost the first two
days. Rebuilt; the diff against the old bytes is exactly the fold. The
prepython table reproduces byte-identically, which says the README's commands
are otherwise right.

**`ANCHOR_SIGMA_K` and the per-era offset were keyed on a filename.** Five
places tested `source.startswith("fit_cd10")`, whose failure mode is silent: an
input rename makes every anchor `recent`, the offset fits nothing, and the curve
comes out about a kelvin wrong for both halves with no symptom. `steps.csv`
carries an `era` column now, `segments.ERAS` is the map, and an era with no
`ANCHOR_SIGMA_K` entry raises. The `prepython` set is 37 anchors, which is the
old `fit_cd10` set exactly — so the rewiring is verified, not assumed.

> ### ⏸ PAUSE — do not start Phase A until the manifest is approved.
> Phases A and B are cheap to redo and expensive to redo *against the wrong
> data*. This is the artefact worth a human's time.

---

## 6. Phase A — measure each segment

**Goal:** every segment reduced to numbers with honest uncertainties, seeding B.
**Est:** 1 session. **Exit gate: the measurement table is reviewed.**

`analysis/measure.py`, taking over `steps.py`'s `__main__`. `fit_pole` and the
grading constants stay — `tests_ltspm3/test_sweep_tool.py` reads them as text
and mirrors them into `ltspm3/tools/sweep.py`.

**Per `jump`:** `T_inf`, `τ`, **and an uncertainty on each** from the residual
covariance, rather than a pass/fail grade. Keep `reach` and `remainder_K`.

**Per `hold`:** mean, linear drift, and a 24 h harmonic — amplitude and phase.
Then regress the sample against Coldplate, 1st Stage, 2nd Stage and RAD SHIELD.
This is where the two things long holds are *for* get measured: diurnal
movement, and how bath temperatures reach the sample.

**Every steady-state uncertainty gets the long-term fluctuation added in
quadrature**, sized from the holds' own measured diurnal amplitude rather than
assumed. Every row carries its absolute timestamp.

Output `analysis/measured.csv`, superseding `analysis/steps.csv`.

> Fifteen call sites read that file; six open it with a bare `open()` rather
> than `_data.open_table` (`plot_gain.py:215`, `export_response.py:73`,
> `plan_sweep.py:82`, `pid_tuning.py:108`, `plot_ode.py:49`,
> `fit_lambda.py:46`). Route them through the loader while they are being
> touched.

### Exit criteria

- Reproduces τ at 114 K and 118 K (513 s, 525 s) and the three hold
  temperatures (96.516, 114.390, 118.609 K).
- The diurnal amplitude is a **measured number with a phase**, not an assumption.
- Uncertainties are populated for every row and are not all equal.

> ### ⏸ PAUSE — review the measurement table before fitting anything to it.
> Stage A is the direct measurement. If it is wrong, Stage B will fit it
> beautifully and be wrong with it.

---

## 7. Phase B — the ODE over all good traces

**Goal:** hit all three targets in §1 at once.
**Est:** 2–3 sessions. This is the long phase; the ordering below is what makes
it bisectable.

**Steps 1–4 are refactors and must be bit-identical. Prove it before step 5.**

1. **Types and loaders.** A `Record` (its own clock, `T0`, weights, absolute
   `t0`, name) and an `Anchors` carrying `t_abs` and `source`. `steps.csv`
   already has `t_end` for every graded row — the anchors have a clock on disk;
   `load_anchors` simply never reads it. Add `production_inputs()` so the ladder
   cannot compute different things on its two paths. Bump `FIT_CACHE_VERSION`.
   *Prove inert:* `rms_k`, `tau_137_s`, `mass_g`, `anchor_k` identical.
2. **Multi-record plumbing at R = 1.** `N_eff` replacing the scalar `len(t)` at
   all five prior-weight sites; per-record `integrate`, `opening_hold` and
   `np.gradient`; `knot_range()` over all records *and* anchors. Still identical.
3. **`_worker`/`ladder` unification.** `_worker` re-loads `load_sweep()` itself
   and ignores its caller, and `ladder()` uses `data` only on the serial path —
   so with records they would silently fit different things. Assert the two
   paths produce the same cache key for one rung.
4. ~~**`decimate.py` emits an absolute timestamp.**~~ **DONE in Phase 0's
   rewiring** — the table had to be regenerated anyway for the corrected `T_c`,
   and doing it twice would have been silly. `Timestamp` is beside `Time`;
   it cost 16 kB, 46 → 62 kB.
5. **`analysis/drift.py` — the gate.** Records the +0.167 K/day regression
   (currently written down nowhere in the repository) and runs the **coverage
   report**: for each proposed drift-knot interval, the anchor count and
   temperature ratio inside it. A knot interval whose anchors do not span
   temperature cannot tell time from Λ — they are the same parameter there.
   Current coverage, which decides the knot count:

   ```
   days  0-30   n=20   22.5-148.8 K   ratio  6.6
   days 30-39   n=21    8.4-170.6 K   ratio 20.3
   days 39-55   n=68    4.9-192.4 K   ratio 39.6
   ```

   All three pass a 4:1 bar — but the first 39 days contain **one** anchor below
   15 K, so the leverage there is thin. **Default to affine: one slope, one
   gauge.** Earn extra knots only at named epochs that pass the coverage test on
   a robust metric (≥ 5 anchors in each of two separated decades), never by
   `linspace`.
6. **Seeding from Phase A.** Λ by direct inversion of the settled points, C from
   `τ·Λ′`, replacing the power-law seed in `build()`. Add `--seed-only`, which
   prints both without running `least_squares` — a one-second sanity check on a
   fifteen-minute fit, and the closest thing to a model-free reading of the
   cryostat.
7. **Add the post-recal trace**, drop anchors falling inside a fitted record's
   span (trap T4), set `RECORD_SHARE` explicitly (trap T5). Still `groups=`, no
   campaign drift. **Report how much of the 2.5 K the trajectory alone
   recovers** — this separates "those holds were only ever anchors" from "the
   model needs a drift term".
8. **Turn on the campaign drift**, retire `groups=`/`anchor_groups`, and set
   `ANCHOR_SIGMA_K` to `{fit_recorder: 1.0, fit_cd10: 1.0, postcal: 0.5}`
   (trap T3). Then the test that decides whether this worked:

   > **Leave-one-epoch-out.** Drop every anchor after 2026-09-04, refit, and
   > *predict* the three post-recal holds. Landing them inside 0.5 K having
   > never seen them means the drift has been measured rather than fitted.
   > **Do not proceed if this fails.**

9. **Then, and only then, test the aux channels** against the fitted drift —
   the second half of the agreed approach. Regress `d(t)` on 1st Stage / RAD
   SHIELD / 2nd Stage where they exist, fit the coefficients on Aug 24 – Sep 03
   and predict Sep 04 – Sep 09. Report R² and the held-out error.
10. **Migrate the five callers**, regenerate, clear `SUPERSEDED_NOTE`, re-document.

### Callers to migrate

`plot_gain.py:205`, `export_response.py:71`, `plan_sweep.py:80`,
`pid_tuning.py:107`, `plot_ode.py:251`. **`bath.py` is not one** — it calls
`F.load_sweep()` and has its own `fit()`. `settling.py`, `fit_lambda.py` and
`plot_ladder.py` need no change.

- `plot_ode.py` stays **single-record and drift-free**: it is the knot-count
  complexity study, and adding a drift term to a knot ladder confounds exactly
  what the ladder exists to separate.
- `plan_sweep.py` gains `--as-of`, so a ladder planned two weeks out includes
  two weeks of drift.
- `pid_tuning.py` currently fits `(9, 4)` with no weights, drift or groups — a
  different, uncached model from the one that ships. Migrate it to the preset.

---

## 8. Traps

Each of these will produce a fit that converges and means nothing.

**T1 · Λ's level was never identifiable, and that is fine.** Only
`Λ(T_s) − Λ(T_c)` enters the anchor residual, the integrator's RHS and (through
Λ′) the τ residual. A constant added to Λ is an exact null direction; what
breaks it is the log-log smoothness prior, not data. `export_response.py`
already knows — it exports `Q` rather than Λ for exactly this reason. **What
must be identifiable is Λ′ and Q(T).** Do not spend effort "pinning the level".

**T2 · The campaign drift and the within-record drift have opposite signs.**
The 43 h sweep's 22.8 h opening hold drifts **−3.8 mK/h** (cooling) at a fixed
heater — the entire stated justification for the existing `DRIFT_SIGMA_W` — while
the campaign rate is **+7 mK/h** (warming). Over that record the campaign ramp
predicts +0.30 K where the hold measured −0.16 K, against a 0.168 K rms. **These
are not the same phenomenon and one term cannot be both.** Keep two: a
per-record short-timescale wander, and the campaign ramp, with separate priors.

**T3 · `ANCHOR_SIGMA_K["fit_cd10"] = 3.0` is the drift already forgiven.** If the
ramp now explains the July/September disagreement and the 3 K bar stays, the
ramp is fitting a residual that has been priced out and will come back
under-determined. It must drop in the same commit that turns the ramp on.

**T4 · The three holds would be double-counted** — once as anchors, once as
106 h of trajectory. Not fatal, they are consistent, but it silently multiplies
their weight and makes `ANCHOR_SHARE` no longer mean what it says. Drop anchors
falling inside a fitted record's span. The same problem exists latently today for
the 17 sweep-derived anchors, so fixing it improves the status quo.

**T5 · `RECORD_SHARE` must be explicit.** Normalising weights by time lets the
post-recal record — 112 h of which ~106 h is three holds carrying one bit each —
outvote the 43 h sweep 2.6 : 1 on the strength of sitting still. An implicit
relative weight between records is the easiest way to get a joint fit that
converges and means nothing.

**T6 · Aux channels must stay out of the ODE.** `1st Stage`, `RAD SHIELD` and
`2nd Stage` are blank **2026-07-23 → 08-20** — 28 of 55 days — so a regressor
built on them either drops a third of the campaign or imputes it. Diagnose with
them (step 9); do not fit with them. `THE CHONKE` reads 289.999–290.000
throughout: a held setpoint, no signal.

**T7 · The 2026-09-04 12:07 recalibration is a step, not a ramp.** Everything
before it has a remapped Coldplate column. A residual imperfection in that remap
presents as a step at that date and a ramp will absorb it as slope. Offer an
optional named step there and report whether it is significant.

**T8 · Two tests will fail, and one of them by design.**
`tests_ltspm3/test_fitted_response.py` pins eight (percent, kelvin) points to
**±0.05 K**; a successful refit moves them by kelvins. **Regenerate the numbers;
do not loosen the tolerance.** And `tests_ltspm3/test_fitted_table.py` checks
`SUPERSEDED_NOTE` in both directions — clearing it without regenerating the
table fails on the sentinel branch.

**T9 · Two live inconsistencies to settle deliberately, not by accident.** The
shipped `T_c(T)` column comes from `plot_gain.coldplate_of` (an 8-bin median
PCHIP over the dwells) while `plot_gain` itself draws `bath.py`'s fitted 175 s
pole — two different models, one shipped and one displayed. And
`export_response --verify` reported **Q max rel error 5.0e-2** on the trial
refit against a docstring claiming a part in 10⁵; find out why before shipping.

---

## 9. Out of scope

`ltspm3/control/` — the software PID is complete and tested and is left alone.
The viewer, the MATLAB interface and Windows deployment remain the standing
priority in `CLAUDE.md`; this is `analysis/`, which ships nothing but one
generated table. Nothing here arms a heater or moves an output.
