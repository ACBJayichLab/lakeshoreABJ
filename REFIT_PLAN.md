# Thermal model refit — plan

**Status: PHASE A DONE with option 4; PHASE B steps 1-3 and 5-7 DONE, and
traps T4, T5 and T10 with them.** Next is step 8, and §7.1 is the measurement
saying it is needed. Steps 1-3 were refactors and are
proved inert; step 6 is the first thing that moves a curve, and it made the
production fit CONVERGE. **6c and T10 are PID_PLAN.md phase 1 §1.1's two
"settle first" items and both are answered** — the anchors now carry a
power-side error bar (T10, and the trajectory fits 13.6 % better for it), and
the cold basin stays as it is because nothing below 7 K identifies it (6c).
The production fit is `cost` 836.07, `rms_k` 0.1295, `anchor_k` 1.5590,
τ(137 K) 582.3 s in 111 evaluations.
**Nothing has regenerated the shipped table yet** —
`ltspm3/model/_fitted_table.py` still carries its `SUPERSEDED_NOTE`.
Prerequisite work is at `da295af`; Phase 0 is at `6432128` (the archive and the
manifest) and the commit after it (the rewiring and the deletion).
**AUDIT-2026-09-10 findings 1 and 2 are now done in BOTH graders.** The sweep
tool's guard went on the plant's tau first (rejoinder step 1); `analysis/` has
followed it (step 2, section 6.2 option 4), building tau(T) from the archive's
own tau anchors rather than importing one, so invariant 1 still holds and the
two graders no longer diverge. **149 anchors -> 136**, 37 taus unchanged.
**Shape:** three phases, two hard pauses. Phase 0 → *pause* → Phase A → *pause* → Phase B.
Update this Status line as phases land.

### Where a new session picks up

| | |
|---|---|
| **done** | §5.1 the archive — `reference/cooldown-10/`, three non-overlapping tables covering the whole cooldown, with a README carrying provenance, segment boundaries and caveats |
| **done** | §5.4 — the fresh export past 2026-09-09 18:06. The transient is in `cd10_20260904_recorder.csv.gz` segment 0, recording continuous across it |
| **done** | §5.2 the manifest — `reference/cooldown-10/segments.csv`, 315 windows. §5.3 `analysis/segments.py` + `analysis/curate.py` |
| **done** | the rewiring. `_data.py`, `steps.py`, `plot_ladder.py`, `decimate.py` and `fit_ode.load_sweep` read the archive; the five old tables are deleted |
| **done** | Phase A (§6) - `analysis/measure.py`, `analysis/measured.csv`, all four exit criteria met by `measure.py --verify`. Findings in §6.1 |
| **done** | AUDIT-2026-09-10 finding 2, floor half - a tau at the search floor is no longer graded `tau`. 45 → **37** tau anchors, 149 unchanged. §6.2 |
| **done** | AUDIT-2026-09-10-REJOINDER step 1 - the sweep tool's `settled()` guard runs on `MIN_REACH × tau_pred_s` where the plan carries a prediction, a ceiling pin has to answer to it too, `MIN_SPAN_S` is labelled a proxy in both graders, and the audit's synthetic case is a test. Commissioning corrected |
| **done** | §6.2 **option 4** / rejoinder step 2 — `steps.plant_clock` measures τ(T) from the 37 τ anchors and `steps.long_enough` puts the guard on it, in `analysis/` as well as in the sweep tool. **149 → 136 anchors**, 37 τ unchanged *by construction*. §6.3 |
| **done** | Phase B **steps 1, 2, 3** — the refactors, proved inert two ways: the production 12/4 path bit-identical on a forced refit, and the full-grid 9/4, 3/4 and tier2 paths bit-identical against the pre-refactor `fit_ode.py` taken from git. §7 |
| **done** | Phase B **step 5** — `analysis/drift.py`. The drift is a **power**: 0.281 mW/day, three bands agreeing to ±25 % where K/day spans 5.6×. Coverage says **affine**. §7 step 5 |
| **done** | Phase B **step 6** — Λ and C seeded from the anchors, `fit_ode.py --seed-only`. The model-free C(137 K) = 4.96 g against the fit's 4.79. And **6b**: the production fit did not converge from the old seed in 1000 evaluations and converges from this one in 562, so `MAX_NFEV` is 1500 — and the two seeds converge to DIFFERENT optima, the measured one 14.5 % better on the objective. §7 steps 6, 6b |
| **done** | trap **T10** — the anchors' error bar gained its power side, `DELTA_P_FRAC = 0.007`, in quadrature with the kelvin bar *in watts*. It binds over 40–120 K and nowhere below 20 K; `rms_k` 0.1499 → **0.1295** for 0.7 % on `anchor_k`. `FIT_CACHE_VERSION` 6. §8 T10 |
| **done** | Phase B **step 7** — the post-recal trace row, T4 (36 anchors were counted twice, not 17), T5 (`RECORD_SHARE = "equal"`), and per-record drift knots. **The trajectory recovers 57 % of the miss and leaves 1.47 K against a 0.3 K target**, with the post-recal wander term railing at its 2 mW prior: the model needs step 8's campaign ramp. §7.1 |
| **done** | Phase B **6c** — the below-10 K basin. **The knots stay.** Nothing identifies dΛ/dT below 7 K: four placements spread it 10× at 4.55 K and 1.02× at 7 K, one *extra* knot down there costs 83 % of the objective, and dropping all four zero-output anchors moves the curve in the sixth figure. Λ *is* evaluated at the coldplate, 2.7 % below the bottom knot — now bounded by `KNOT_EXTRAP_TOL`. §7 step 6c |
| **next** | Phase B **step 8** — the campaign drift on, `ANCHOR_SIGMA_K` down in the same commit (T3), and leave-one-epoch-out is the gate. §7.1 is the evidence it is needed. Then 9–10. Read traps T1–T10 and §6.1's last paragraph first |
| **half** | rejoinder step 4 - `segments.read_table` now REFUSES a non-monotonic clock, naming the row, so the 2026-11-01 daylight-saving fold is loud instead of silently selecting wrong rows through `searchsorted`. The source fix, taking `t_s` from the recorder's own `Time` column in `lschart/tools/fit_table.py`, is still to do and is dated |
| **then** | rejoinder step 5 - finding 5's leftovers |

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
of the ladder. **Step 6b settled this**: the 20/4 preset does not converge from
the old seed until 1157 evaluations, converges from the measured seed in
562, and the two land on DIFFERENT optima — the measured one 14.5 % better on
the objective. `MAX_NFEV` is now 1500. Every fit number written above this
line predates that.

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

**Definition of done.** All three rows above green; `ltspm3/model/_fitted_table.py`
regenerated; `SUPERSEDED_NOTE` cleared; full suite and `ruff` clean; the docs
corrected. Nothing in `lschart/` or `ltspm3/control/` touched.

---

## 2. Why — the reasoning, with the numbers

A session picking this up cold should be able to re-derive every number here.
All of them came from `analysis/measured.csv` (then called `steps.csv`) and the
recorder logs in `data/`.

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
6. `ltspm3/control/` is not touched. `ltspm3/model/_fitted_table.py` is *generated*,
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

Not done: the refit itself. `ltspm3/model/_fitted_table.py` is untouched and still
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
- ✅ **The manifest has been read.** Jeff read it 2026-09-10 and found a real
  defect on the first pass, which is what the pause is for: `MIN_SPAN_S = 60`
  was an *admission* threshold upstream of the grader, and it had thrown away
  **sixteen rungs** of the programmed sweep — 46 s dwells that ran 11.5 time
  constants at 5–25 K, remainder 0.000 K, end rate under 0.05 K/h. The bar now
  applies only to a dwell with no resolvable transient, which is the one
  failure it was really guarding against. **149 anchors where there were 111**,
  147 in band, 45 with a believable τ; the 4–40 K bands went 30 → 68. The
  manifest diff was 76 additions and **no** verdict changes, and every fit
  metric improved — `rms_k` 0.2129 → 0.2045 and `anchor_k` 1.8781 → **1.6127**,
  with 35 % more anchors to satisfy. Details in `analysis/README.md`, "A wall
  clock was throwing away the best anchors".

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
>
> **Done, and further than that.** All five now call `fit_ode.load_rows()`,
> which is the old private `_rows` promoted — one loader for the anchor table
> instead of five, and it resolves against `REPO_ROOT` rather than the working
> directory, which is AUDIT-2026-09-10 finding 5's second item.

### Exit criteria

- Reproduces τ at 114 K and 118 K (513 s, 525 s) and the three hold
  temperatures (96.516, 114.390, 118.609 K).
- The diurnal amplitude is a **measured number with a phase**, not an assumption.
- Uncertainties are populated for every row and are not all equal.

### 6.1 What Phase A found — read this at the pause

Everything below is reproducible with `analysis/measure.py --holds --verify`.

**Exit criteria: all four met.** The three hold temperatures reproduce to
**−0.5, +2.3 and −17.9 mK** once the fitted drift is run back to the end of the
hold, which is where §2.1's numbers were quoted; all three are inside their own
2σ. τ at 118 K is **524.7 ± 4.5 s** against 525.

**τ at 114 K was two different windows all along.** 513 s is the first **40
minutes** of `pc-20260905-165509` — what the pre-archive region export
contained — and the archive merges that rung into the hold that followed it,
because the sweep tool left the heater there and nothing moved for three days.
Fitted over all 70 h the same relaxation converges on **534.0 ± 6.0 s**. The
40-minute window reads **4.0 % low at reach 4.7**, which is `fit_pole`'s own
documented reach bias, measured rather than argued. `sigma_tau_s` is
statistical and does **not** contain it.

**A single pole is the wrong model for a hold, and it was costing kelvins.**
Once a relaxation is over the exponential has only the cryostat's drift left to
describe, and it describes it — returning τ of days and an asymptote the sample
never reaches. Fitting level + drift + relaxation + a 24 h harmonic instead
moves five graded anchors by more than 0.1 K, three of them past a kelvin:

| hold | span | pole | Phase A | move | drift |
|---|---|---|---|---|---|
| `rec-20260828-141631` | 74.9 h | 150.423 | 148.889 | **−1.534** | +4.23 mK/h |
| `pp-20260815-100312` | 16.6 h | 145.996 | 147.451 | **+1.455** | −4.96 mK/h |
| `pp-20260723-112526` | 56.7 h | 99.966 | 98.858 | **−1.108** | +15.72 mK/h |
| `pp-20260813-133702` | 9.8 h | 141.993 | 142.808 | +0.815 | −6.32 mK/h |
| `pp-20260809-231502` | 32.9 h | 133.563 | 133.170 | −0.393 | +5.86 mK/h |

These are anchors the fit has been reading for the whole campaign. `T_pole` is
kept beside `T_inf` in `measured.csv` so the change stays auditable.

**This is the hold half of AUDIT-2026-09-10 finding 2, and it changes the
fix.** Two of the audit's seven ceiling-pinned anchors are
`pp-20260813-133702` and `pp-20260815-100312` above — its −0.76 K and −1.36 K
rows, and its worst example. The audit's prescription, "refuse any grade for a
ceiling pin", would **drop both**, when what was wrong was the model and not
the window. The bound test wants to be per-kind, or to run after a hold gets
its proper fit. The jump half is untouched by Phase A and still needs the
audit's fix: **8 τ-graded floor pins** at 5.4–25.1 K, all `jump`s.
`measured.csv` reports every pin in `tau_pinned` and `flags` — reported, not
refused, because grading is `steps.py`'s and reaches a fit through the
manifest.

**The diurnal amplitude is measured. Its phase is not.** Eight holds span a
full day; amplitudes **3.9 to 69.2 mK, median 16.19**, and that median sets
`sigma_long = 11.45 mK` on every row. But the peak hour comes back at 0.1, 2.1,
8.3, 15.7, 19.4, 19.6, 22.4 and 22.9 — scattered over the whole 24 h. **So
this is a bound on day-timescale wander, not a building cycle with a known
clock, and Phase B must not model it as one.**

**24 h, not 8 h, is where the harmonic becomes identifiable.** §5's `HOLD_MIN_S`
docstring claims 8 h suffices because the cycle shows as curvature there. It
does not survive contact with a free drift and a free exponential in the same
model: fitted below a period the harmonic runs away, and the four shortest
holds return 464, 306, 300 and 299 mK. So `HARMONIC_MIN_S = 86400`, and a
shorter hold takes the cycle in its error bar instead of in its model.

**The statistical bar has to carry the residual's autocorrelation.** The 218's
noise is mostly slow wander, so the 70 h hold's 125,888 samples are worth
**84** independent ones (an autocorrelation time near 3,000 s). Without that
inflation its error on the mean is tens of microkelvin and it outvotes every
other anchor for having sat still. `n_eff` and `act_s` are in the table so the
claim is auditable. Two bugs found writing this: `pinv` was discarding the τ
direction of a hold's covariance as numerically absent, publishing
`sigma_tau_s` of **exactly 0.000** for six of seven τ-graded holds (the columns
span seven orders of magnitude and are now normalised before the inverse); and
the manifest's second-resolution `t_end` was dropping the last sample of 308 of
312 windows, worth up to **0.50 K** of `T_inf` and 83 % of a τ — fixed by
recording boundaries to the millisecond.

**Anchor error bars now run 11.5 mK to 668 mK, median 14.2 mK**, over 149
anchors — populated, and not all equal.

**Still on fit_ode's own error model.** `load_anchors` continues to compute
`hypot(ANCHOR_SIGMA_K[era], max(0.3, 2·|settle_K|))`; switching it onto
`sigma_T_inf` is Phase B steps 6–8, deliberately after this review. But note
that `settle_K` itself has changed for holds — it is now the drift across half
the window rather than a pole's extrapolation — so `rec-20260828-141631`'s bar
goes from 3.36 K to 0.32 K while its position moves 1.53 K. **The fit will
move at Phase B step 1, and that is not a refactor failing to be inert.**

### 6.2 AUDIT-2026-09-10 finding 2 — the floor is fixed, the ceiling is a question

`fit_pole` searches τ on `[2 × cadence, 20 × span]` and a result *at* either
end is the search saying "outside what I can see". `steps.pole_bounds` is
exposed and `steps.pole_floor` / `pole_ceiling` name the two cases;
`ltspm3/tools/sweep.py` mirrors them and the mirror test now covers
`POLE_PIN_TOL`, `POLE_TAU_MIN_SAMPLES` and `POLE_TAU_SPAN_FACTOR`.

**The floor half is done.** A floor-pinned dwell no longer grades `tau` — τ of
two samples' cadence is not a time constant, and the plant's own τ at 5–25 K
runs from under 0.1 s to 3.4 s. The manifest diff is **8 verdict changes, all
`tau` → `steady`**, nothing added and nothing lost, and `load_taus` goes 45 →
37 with the lowest surviving τ now 5.1 s instead of 4.0. The steady state of a
fast relaxation is still real, so those 8 stay anchors; 149 is unchanged.

**The audit's noise-pole clause was not applied, and should not be.** It
proposes refusing any grade for a floor pin whose amplitude is under
`MIN_AMPLITUDE_SIGMA`, on the grounds that such a pole was fitted to noise.
Sometimes it was — but a dwell that is genuinely *finished* is also flat and
also has no transient, and it is the best kind of anchor there is.
`test_a_flat_dwell_is_steady_but_carries_no_believable_tau` pins that case
deliberately (600 s flat to 0.5 mK at 42 K) and the clause breaks it. Nothing
inside the window separates the two; what separates them is the plant's τ at
that temperature, which is finding **1**'s conclusion arriving again. The
archive's two real cases are `pp-20260815-095502` (200 s at 147.1 K) and
`pp-20260817-211021` (330 s at 170.4 K), both past `MIN_SPAN_S`, and
`measured.csv` gives them `sigma_tau_s` of **214 %** and **103 %** of τ, which
is the honest signal available without a plant model.

**The ceiling half needs a decision, and here is the evidence for it.** The
audit proposes refusing any grade for a ceiling pin. Measured over all 46
ceiling-pinned dwells in the archive:

- 7 are graded. Refusing them all would drop **three long holds drifting under
  4 mK/h** (`pc-20260909-212001`, `pp-20260813-133702`, `pp-20260815-100312`)
  and **two dwells at 4.84 and 5.14 K** where the plant's τ is under a tenth
  of a second — five windows that are settled — to catch two that are doubtful
  (`pp-20260808-155602`, 840 s at 99.4 K; `rec-20260901-222818`, 232 s at
  170.6 K).
- Two of those three holds are the audit's own −0.76 K and −1.36 K rows, and
  §6.1 shows the problem was the **model**, not the window: fitted with level
  and drift they are anchors at 142.808 and 147.451 K.
- **A slope bar cannot separate them.** All seven graded ceiling pins drift
  under 0.5 K/h — the largest is −0.460 — so a `MAX_END_RATE_K_PER_H` test on
  a straight-line fit changes no verdict at all. The other 39 ceiling pins are
  already `excluded` and stay so at any threshold between 0.5 and 0.8 K/h.

So the discriminator is again the plant's τ. **Four ways out, and the argument
between them is in [AUDIT-2026-09-10-REPLY.md](AUDIT-2026-09-10-REPLY.md):**

1. Refuse a ceiling pin only for a window shorter than `curate.HOLD_MIN_S`,
   moving the test to where the hold/jump split already lives. Catches both
   doubtful cases, keeps all five good ones — and is a wall clock again.
2. Grade a hold on `measure.py`'s level-and-drift fit rather than on a pole.
   Architecturally the right answer and the largest change: grading for a hold
   would move out of `steps.py`.
3. Leave it reported. `measured.csv` carries `tau_pinned` and `flags` on every
   row, so nothing is hidden, and the two doubtful anchors carry `sigma_tau_s`
   of 8,660 % and 15,394 % of τ.
4. **Recompute `reach` against the plant's τ instead of the fitted one.** This
   is the one to pick. It separates the seven perfectly against the existing
   `MIN_REACH = 3.0` — the five settled ones score 18 to 6,771 and the two
   doubtful ones 1.93 and 0.38 — introduces no new constant, and is finding
   **1**'s own prescription. `analysis/` can now do it without breaking
   invariant 1, because `measured.csv` itself carries the plant clock: 37 τ
   anchors over 25.8–247.6 K, from windows that resolved their own transients
   and so are a set disjoint from the ceiling pins being tested. It costs one
   round of iteration — grade, build τ(T), then settle the pins — which
   "curate, do not discover" contains, because the output is a committed
   manifest and not a fixed point rediscovered per run. The reply's §4 has the
   coverage caveat below 25.8 K and why the verdict is insensitive to it.

> **Option 4 was taken.** What it actually cost is §6.3 below, and it is not
> what this section predicted: **thirteen** dropped anchors, not two, because
> the reasoning above scored only the ceiling pins and the plant clock replaces
> the wall clock on the `amp_sigma` branch as well. One anchor is *recovered*.

**Option 4 is now live in the sweep tool and still open in `analysis/`.**
[AUDIT-2026-09-10-REJOINDER.md](AUDIT-2026-09-10-REJOINDER.md) makes the point
§6.2 and the reply both missed: the reasoning above is scoped to `analysis/`
and its review pause, while `ltspm3/tools/sweep.py` runs against the cryostat
on the day with no pause protecting it. So the guard went in there first —
`PoleFit.long_enough`, `MIN_REACH × Tread.tau_pred_s` whenever the plan carries
a prediction, with a ceiling pin now having to answer to it as well. The two
graders therefore **diverge on this one test on purpose**, and both say so at
`MIN_SPAN_S`: `ltspm3` already imports the plant model and `analysis/` must not.

When option 4 is taken here, one row needs a manifest note: **`pp-20260808-155602`
is decided by a margin of 1.6 in τ, not the three orders of magnitude the other
six enjoy.** Checked at 99 K rather than assumed — the τ anchors bracketing it
interpolate to 445.9 s against the shipped table's 436.4 s, 2.2 % apart, giving
reach 1.88 and 1.92; τ would have to be 37 % low for the pin to pass. The
verdict holds, and it is the one of the seven a reviewer should actually look
at.

Phase B does not depend on this being settled first. Its τ residuals read
`load_taus`, which the floor fix has already cleaned, and no ceiling pin is
graded `tau`.

### 6.3 Option 4, applied — what it cost and what it bought

`steps.plant_clock` interpolates τ(T) log-log through the dwells graded `tau`;
`steps.long_enough` asks `MIN_REACH × τ(T)` of any dwell whose pole cannot be
believed — `steps.pole_unbelievable`, which is the ceiling pin **and** the
amplitude case, exactly as `sweep.py` has had it since the rejoinder's step 1.
`curate.py --propose --plant` prints every verdict it decided, with a margin.
Reproducible from a clean clone; `analysis/README.md` has the long version.

**149 → 136 anchors. 37 τ anchors, unchanged — by construction, not by luck.**
A `tau` grade needs `reach ≥ 3` and `amp_sigma ≥ 20`, which is the negation of
`pole_unbelievable` plus a bound no ceiling pin can meet, so the set that builds
the clock is disjoint from the set tested against it. `archive_dwells` grades
once without the clock, builds it, re-grades, and **raises** if a `tau` verdict
moved. That is the non-circularity claim of §6.2 turned into an assertion.

| | |
|---|---|
| dropped | 14 — 2 ceiling pins, **12 from the `amp_sigma` branch** |
| recovered | 1 — `rec-20260824-171059`, 57 s at 4.75 K, which is 11 τ |
| relabelled `unsettled` → `unresolved` | 17, all already excluded |

**The plan predicted two. It scored only the ceiling pins.** The wall clock was
standing in for the plant clock on the amplitude branch too, and there it was
letting through warm short dwells: all twelve are 128.8–180.5 K, 70 s to
1641 s, under three of the plant's time constants with no transient of their
own to argue otherwise. Two are AUDIT-2026-09-10-REPLY.md §1's own examples —
200 s at 147.1 K and 330 s at 170.4 K, which it called "a third of a τ" and
"half a τ" while having no mechanism to refuse them.

**The decision is not sitting on top of the data.** Over the 97 windows the
guard judges, the margin — the factor τ(T) would have to be wrong by to flip a
verdict — has a **gap from 0.86 to 2.41 with nothing in it**. Four rows land
under 2× and each has a manifest note; `pp-20260808-155602` is the rejoinder's
named row at 0.63, where τ(99.42 K) would have to be 37 % low and the
interpolant agrees with the shipped table to 2.2 %.

**Which construction of τ(T) is used does not matter, and that was measured.**
All 37 τ anchors, the 29 shorter than `HOLD_MIN_S` (whose poles carry no
campaign drift), and `measured.csv`'s own τ column give the **same verdict on
all 44 rows**. So `plant_clock` reads the rows `steps` just fitted rather than
`measured.csv` — which is gitignored, and a grader whose verdicts depend on a
file a fresh clone does not have produces a manifest nobody can reproduce.

**Were the 13 actually wrong?** Fitted on the old manifest at Λ12/C4/drift-3,
their own per-anchor residuals against the ones kept:

| | kept (133) | dropped (14 in band) |
|---|---|---|
| median \|residual\| | **0.229 K** | **1.476 K** |
| rms | 1.542 K | 1.788 K |

A factor of **6.4 in the median**, and the seven worst are all `prepython` and
all the **same sign**, −2.1 to −3.5 K, which is what a set of dwells cut short
in the same direction looks like. But two of the fourteen fit to under 0.13 K,
and that is the honest shape of this: **the guard removes anchors that cannot
be shown to have settled, not anchors that are demonstrably wrong.** Some were
probably fine. Nothing in the window can say which, which is the whole argument.

**The fit is unchanged.** Λ12/C4/drift-3, same preset the rest of this document
quotes:

| | before (147 in band) | after (134) |
|---|---|---|
| `rms_k` | 0.1999 | 0.2025 |
| `anchor_k` | 1.5674 | **1.5426** |
| `mass_g` | 4.7903 | 4.7877 |
| `tau_resid` | 0.1469 | 0.1473 |

`anchor_k` falls 1.6 % — but with 13 fewer anchors that is **not by itself
evidence**, unlike Phase 0's fall *under a 35 % larger set*; the residual table
above is the evidence. What the numbers do say is that dropping them costs the
trajectory and the dynamics nothing: `rms_k` moves 1.3 % and the implied mass
0.05 %. **Both fits stop at `nfev = 300 = max_nfev`**, so both are upper bounds
rather than converged fits — the cap §7's preamble warns about, still unfixed.

> ### ⏸ PAUSE — review the measurement table before fitting anything to it.
> Stage A is the direct measurement. If it is wrong, Stage B will fit it
> beautifully and be wrong with it.

---

## 7. Phase B — the ODE over all good traces

**Goal:** hit all three targets in §1 at once.
**Est:** 2–3 sessions. This is the long phase; the ordering below is what makes
it bisectable.

**Steps 1–4 are refactors and must be bit-identical. Prove it before step 5.**

1. ~~**Types and loaders.**~~ **DONE.** `Record` (own clock, `T0`, weights,
   absolute `t0`, name) and `Anchors` (`t_abs`, `group`, `source`).
   `production_inputs()` replaces three identical five-line blocks in
   `plot_gain`, `export_response` and `plan_sweep`, and with them the `t_max =
   float(data[1].max())` cut that was open-coded five times.
   `FIT_CACHE_VERSION` 3 → 4.
   - **`t_abs` comes from `t_mid`, not `t_end`.** This plan predates Phase A.
     A hold's level is now quoted at its window's midpoint, so dating the
     anchor by its end would put the 70 h hold 35 h from the moment it
     describes — the rejoinder asks for exactly this convention.
   - `anchor_groups()` is now `load_anchors().group` rather than its own copy
     of the "is this row an anchor" filter. Three loops had to agree on which
     rows exist; a `t_max` drifting in one of them would have misaligned
     `groups` against `aT` by a row and mis-assigned every offset after it.
   - **`aTc` was missing from the cache key.** It enters the objective through
     `lam(pl, aTc)`, so a Coldplate remap — which happened on 2026-09-04 — did
     not invalidate a stored fit. Added.
2. ~~**Multi-record plumbing at R = 1.**~~ **DONE**, with one deliberate
   omission. `N_eff` replaces `len(t)` at all five prior-weight sites;
   `integrate`, `opening_hold` and `np.gradient` all run per record and
   concatenate; `knot_range()` exists.
   - **`knot_range()` does NOT cover the anchors, and cannot here.** This step
     has to be inert and that is not: the coldest anchor is
     `rec-20260824-171059` at **4.7516 K**, below the sweep's own 4.8985 K, so
     including it moves `T_lo` from 4.6536 to 4.5140 K and every geomspaced
     knot with it. That anchor is the one §6.3's plant clock **recovered** —
     the assumption that the records bracket the anchors was true when this
     plan was written and option 4 made it false. The switch is wired and off;
     turning it on belongs with step 6, where the seeding moves anyway.
   - `n_drift` with more than one record **raises**, pointing at T2. One block
     of time knots cannot serve two clocks, and T2 says the answer is two
     terms with separate priors, which is step 8.
3. ~~**`_worker`/`ladder` unification.**~~ **DONE.** `FitSpec` is the small
   picklable description both paths build their inputs from; `_worker` carries
   a spec instead of re-deciding. The assertion the step asks for is in
   `ladder()` and **is not vacuous even though both paths now run the same
   code**: it keys one rung in this process and the same rung *in a worker
   process* and compares, because a worker is a fresh interpreter with its own
   working directory and `_data.resolve` searches the CWD as well as
   `REPO_ROOT`. A worker resolving a different `measured.csv` is
   AUDIT-2026-09-10 finding 5 with a process boundary in it. `fit(key_only=True)`
   returns the key from the line that computes it, so there is no second
   implementation to keep in step.
4. ~~**`decimate.py` emits an absolute timestamp.**~~ **DONE in Phase 0's
   rewiring** — the table had to be regenerated anyway for the corrected `T_c`,
   and doing it twice would have been silly. `Timestamp` is beside `Time`;
   it cost 16 kB, 46 → 62 kB.
5. ~~**`analysis/drift.py` — the gate.**~~ **DONE**, and it changed the answer
   to "in what units". `python analysis/drift.py` reproduces everything below.

   **A drift in K/day is not a property of the cryostat.** It is the underlying
   change times the *local gain*, and the gain runs 2 to 14 K/% across the
   band, so the same cause reads six times bigger at the warm end. Three
   independent output bands, de-overlapped so no anchor is counted twice:

   | band % | n | days | T range | K/% | K/W | K/day | **mW/day** |
   |---|---|---|---|---|---|---|---|
   | 52.0–53.5 | 9 | 50.0 | 27.2–32.0 K | 2.04 | 118.7 | 0.0334 | **0.281** |
   | 62.9–64.4 | 9 | 48.5 | 98.4–118.6 K | 11.52 | 555.1 | 0.1862 | **0.335** |
   | 65.2–66.7 | 30 | 25.4 | 129.2–148.9 K | 13.67 | 634.4 | 0.1413 | **0.223** |

   **K/day spans 5.6×; mW/day agrees to ±25 %.** So the campaign drift is a
   *power*, and `fit_ode`'s existing drift term is already right to enter as
   watts at the heater's own node — step 8's ramp must do the same and must
   **not** be a temperature offset.

   Median **+0.281 mW/day**, against §2.3's independently measured **+0.27
   mW/day** in a band this table does not reuse. Over the ~20 days between the
   two eras' centroids that is 5.6 mW, against the **4.60 mW** the 12/4 fit
   measures as its own `group_w` offset. Three routes, one number.

   The gain column is §2.3's independent check and passes: 11.5 K/% at
   98–119 K and 13.7 at 129–149 K, against the fit's 13–15.

   **Coverage: affine is earned, three linspaced knots are not.** The test is
   ≥ 5 anchors in each of two clumps ≥ 3× apart in T, reported at the most
   *balanced* passing split rather than the first one found.

   ```
   affine     days  0.0-55.3   n=136   4.8-247.6 K   EARNED  (68 near 17 K, 68 near 135 K)
   3 knots    days  0.0-27.7   n= 12  22.5-247.6 K   NO      no split 3x apart with 5 either side
              days 27.7-55.3   n=124   4.8-192.4 K   ok      (62 near 15 K, 62 near 133 K)
   ```

   The failing interval is the thin early leverage this plan already warned
   about, now measured rather than estimated. **Default to affine: one slope,
   one gauge.** Earn extra knots only at named epochs that pass this test — a
   recalibration, a repair — never by `linspace`.
6. ~~**Seeding from Phase A.**~~ **DONE.** `fit_ode.py --seed-only` prints Λ and
   C with no `least_squares` anywhere, and `SEED_MEASURED` (in the cache key,
   so both seeds can be run side by side) makes it the fit's starting point.

   **Λ by direct inversion**, `Λ(T_s) − Λ(T_c) = Q` at every settled dwell,
   with `T_c` folded in by iterating (it converges in two passes, because Λ at
   5–7 K is a percent of Q). Medians in 22 log-T bins, monotone by running
   maximum — the scatter inside a bin *is* the campaign drift and a median is
   the right summary of it.

   **The level is a gauge, it is not searched, and two attempts to search it
   were both wrong.** Trap T1 says an added constant is a null direction of the
   data. It is one for the roughness penalty too — the penalty acts on
   `log(dΛ/dT)` and `d(Λ+c)/dT = dΛ/dT` — so the first `_gauge_level` measured
   a quantity invariant to its own argument and returned the top of its grid,
   9.68 W against a curve spanning 0.97. The second searched the genuine
   residue (the parameterisation interpolates **log Λ** between knots, so a
   constant moves the curve *between* them) through the real `LogLog`. That was
   defensible, and measuring it killed it:

   | gauge | cost | `nfev` |
   |---|---|---|
   | 0 | **994.64** | **149** |
   | 0.0175 — what the search returned at 20 knots | 994.64 | 566 |
   | 2.4121 — what it returned at 9 knots, railed at 3× the span | **1163.02** | 561 |

   A zero gauge reaches the same optimum **3.8× faster**, and the 9-knot answer
   lands the fit in the *worse* basin — exactly where the power-law seed was
   stuck, destroying step 6's whole benefit. `_gauge_level` is **deleted**; Λ is
   seeded on the anchors' own `Λ(T_c) = 0`. Do not reintroduce a search over a
   null direction without running that table.

   **C from `τ·Λ′`, and a single Debye magnitude was not good enough.** The
   first version fitted one factor to the Debye mix and reconstructed τ(137 K)
   as **804 s where the τ anchors there say 607** — 32 % out, because a median
   ratio over 25–192 K lands its error wherever C departs from Debye most.
   Taking the shape from the τ anchors where they exist (28.4–192.4 K, 9 bins)
   and falling back to Debye outside gives τ(137 K) ≈ 610 s.

   **The model-free reading agrees with the fit.** C(137 K) = **1.006 J/K =
   4.960 g** of the mix, against the converged fit's **4.79 g** — 3.5 % apart,
   from two methods sharing no machinery. Λ(186) − Λ(8.7) = 0.752 W against the
   0.778 W the old seed was hand-calibrated to. Local resistance peaks near
   546 K/W at 76 K and reads 620 K/W at 130 K, against the 639 K/W the two
   September holds measure between them.

   **What it is worth is in 6b**, because measuring it turned up something
   larger than a better starting point.

6b. **`max_nfev = 300` was a truncation, and the seed was choosing the wrong
   local minimum.** §7's preamble suspected the first; the second was not
   suspected at all. Both production presets, run out to convergence on a
   clean cache:

   | preset | seed | cap | **cost** | `rms_k` | `anchor_k` | `mass_g` | τ(137) | `nfev` |
   |---|---|---|---|---|---|---|---|---|
   | 12/4 | power law | 300 | 1366.46 | 0.2025 | 1.5426 | 4.7877 | 567.1 | 300 **cut** |
   | 12/4 | power law | 1500 | 1364.96 | 0.2081 | 1.5428 | 4.7866 | 567.3 | 539 ✓ |
   | 12/4 | **measured** | 300 | **1252.25** | 0.1869 | 1.5481 | 4.7705 | 559.4 | **128** ✓ |
   | 12/4 | **measured** | 1500 | **1252.25** | 0.1869 | 1.5481 | 4.7705 | 559.4 | **128** ✓ |
   | 20/4 | power law | 300 | 1164.29 | 0.1579 | 1.5237 | 4.7892 | 566.5 | 300 **cut** |
   | 20/4 | power law | 1500 | 1163.02 | 0.1585 | 1.5244 | 4.7887 | 566.6 | 1157 ✓ |
   | 20/4 | **measured** | 300 | **994.64** | 0.1499 | 1.5479 | 4.8059 | 571.3 | **149** ✓ |
   | 20/4 | **measured** | 1500 | **994.64** | 0.1499 | 1.5479 | 4.8059 | 571.3 | **149** ✓ |

   `cost` is `0.5·Σr²` — what `least_squares` actually minimises — and it is on the
   row because `rms_k` and `anchor_k` are two *weighted parts* of it and they move
   in opposite directions here.

   **The two seeds converge to different optima.** The measured one is **8.3 %
   better at 12 knots and 14.5 % at 20**, and gets there **4.2× and 7.8× faster**
   (128 vs 539, 149 vs 1157). The objective is multi-modal and the power-law seed
   had been landing in the worse basin for the whole campaign, so every production
   number this repository has quoted comes from there.

   **`MAX_NFEV` 300 → 1500.** 300 was a truncation: neither seed had converged
   there. With the gauge search gone the shipped path no longer needs the
   headroom — it converges in 128–149 — so 1500 is there so that a comparison
   *against* the old seed is between two converged fits rather than between a fit
   and a truncation, and it costs nothing on rungs that stop earlier.

   **The plots qualify this.** `analysis/plot_review.py` draws the two optima as
   curves and above ~7 K they coincide: max difference in dΛ/dT is 0.5–0.8 % over
   25–192 K, 2.7 % over 10–25 K, and **928 % over 4.7–7 K**. Decomposing the
   cost, 42 % of the gain comes from the 140 sweep samples below 10 K (2.8 % of
   them), where the measured-seed conductance carries a spike the anchors do not
   show. So the seed is worth having for **convergence and reproducibility**, and
   the two optima are the same model wherever the model is used; it is **not**
   established that the winner is right below 10 K rather than merely lower.
   `T_lo = 4.6536 K` is below the coldest sample AND the coldest anchor, so the
   lowest Λ knot sits where there is no data — settle that before regenerating.
   **Settled in 6c**, and the two readings of the 928 % turn out to be the same
   reading: below 7 K nothing identifies the conductance, so a seed, a knot or
   an optimiser's basin all decide it equally. The knots stay.

   Note the better optimum has a **higher** `anchor_k` with a lower `rms_k`. That
   is REFIT_PLAN §1's tension — the model as posed cannot satisfy the trajectory
   and the holds at once — appearing in the shape of the basin rather than in a
   weight study.

6c. ~~**The below-10 K basin.**~~ **SETTLED 2026-09-12** — PID_PLAN.md phase 1
   §1.1's second prerequisite, and the thing 6b said to settle before
   regenerating. **The knots stay where they are.** Below about 7 K the
   conductance is not identified by anything, and every way of giving the
   region more attention makes the fit worse.

   Four placements of the bottom knot, production preset. The conductance they
   give, as a ratio of largest to smallest:

   | T (K) | 4.55 | 4.75 | 5.00 | 5.50 | 6.00 | 7.00 | 10.0 | 25.0 |
   |---|---|---|---|---|---|---|---|---|
   | spread | **10.0×** | 5.4× | 2.4× | 1.6× | 1.19× | 1.02× | 1.03× | 1.00× |

   **Above 7 K the placement does not matter; below 6 K it decides the
   answer.** Three things are supposed to determine that region and not one of
   them does:

   - the **sweep** has 140 samples under 10 K, 2.8 % of the grid and **0.3 %
     of the weight** after decimation;
   - the **roughness prior** is evaluated at knot midpoints and reaches no
     lower than 5.14 K — it does not act below the bottom knot at all;
   - the **anchors** cannot reach: at 4.75 K one kelvin of bar is 1.8 mW, and
     the four zero-output anchors are missed by 0.2–1.2 mW, *inside* it.
     Dropping all four moves the fitted curve in the sixth significant figure
     (cost 836.075 → 837.245, every conductance ratio 1.00×) and the refit
     misses them by exactly as much as the fit that saw them.

   **So the cold end is whichever basin the optimiser lands in**, and handing
   it another parameter is how you find that out. These three share their upper
   nineteen knots exactly, which the four-way comparison above does not — moving
   `T_lo` re-places every geomspaced knot, and 11 of that comparison's 36 cost
   units are the 10–20 K knots landing elsewhere, not the cold end:

   | bottom knot, upper 19 unmoved | cost | `rms_k` | `nfev` |
   |---|---|---|---|
   | 4.6536 K, as shipped | **836.1** | 0.1295 | 111 |
   | plus one knot at 4.5299 K | 1526.6 | 0.1623 | 110 |
   | moved to 4.5299 K | 852.5 | 0.1308 | 220 |

   One **extra** knot below all the data costs **83 % of the objective** and
   deforms 5–7 K wholesale — Q(6 K) 18.4 → 10.9 mW — on a parameter nothing
   determines. Moving the bottom knot instead costs 2 % and twice the
   iterations, for a curve that differs nowhere above 6 K.

   **And nothing downstream wants either.** The steady state is good to better
   than 0.05 K above 6 K under every placement and to about 0.3 K at 5 K; τ
   below 7 K is undetermined under all of them, and PID_PLAN.md §3 says τ below
   30 K never enters the loop while the monitor has no opinion below 28 %
   output — about 11 K, well above the whole argument. `knot_range`'s
   `anchors=`/`taus=` switch, deferred by step 2, **stays off**, now for a
   measured reason rather than a scheduling one.

   **What did change is what the question turned up.** Λ is evaluated at the
   **coldplate** as well as at the sample — the residual is Λ(T_s) − Λ(T_c)
   everywhere — and `knot_range` has only ever looked at the sample. The
   coldest T_c is 4.5299 K against a bottom knot at 4.6536: 8 anchors and 64
   sweep samples sit under the knot, and the bottom **2.7 % in T** is a log-log
   continuation rather than a piece of the curve. It is a *bounded* one — the
   continuation's slope is PCHIP's end slope, fixed by the two lowest knot
   values, which the prior does reach — so it is kept, and `KNOT_EXTRAP_TOL`
   now checks it. A later record with a colder coldplate would have widened it
   with nothing on screen to say so.

7. **Add the post-recal trace**, drop anchors falling inside a fitted record's
   span (trap T4), set `RECORD_SHARE` explicitly (trap T5). Still `groups=`, no
   campaign drift. **Report how much of the 2.5 K the trajectory alone
   recovers** — this separates "those holds were only ever anchors" from "the
   model needs a drift term".

   **The trace row is in**, `trace-postcal-20260905`, 2026-09-05T17:35:06 →
   09-09T18:06:03, 96.51 h, 173,727 rows, 114.2–121.0 K, one segment. Its two
   arguable boundaries are in its manifest note: it begins one second after
   `trace-ladder-20260905` because two `trace` rows may not overlap and the
   ladder's bounds reproduce a region export exactly, and it ends where
   `mask-20260909-180604` begins. The 11.5 h flat hold at 96.5 K before the
   ladder is deliberately outside it — by T4 a trace over a flat hold *converts*
   a measured anchor into trajectory the decimator would thin away.

   **T5 is settled: `RECORD_SHARE = "equal"`** (Jeff, 2026-09-12). Every record
   carries the same total whatever its length, which is not a claim that they
   hold the same information — it is a refusal to let *duration* be the vote,
   which is the specific failure T5 names. **Inert at one record by
   construction** (the scale is `sqrt((N_eff/1)/len(r)) = 1`): the production
   `w_sweep` is bit-identical and the cache key is unchanged.

   **T4 is in, and it was 36 anchors, not the 17 this trap estimated.** The
   whole 43 h sweep window is a record, so every dwell inside it was in the
   objective twice — once sample by sample, once as an anchor:

   | | anchors | cost | `rms_k` | `anchor_k` | τ(137) | `mass_g` | `nfev` |
   |---|---|---|---|---|---|---|---|
   | T4 off | 134 | 836.075 | 0.1295 | 1.5590 | 582.3 | 4.8357 | 111 |
   | T4 on | **98** | 850.515 | **0.1295** | 1.8218 | 581.7 | 4.8342 | 103 |

   **Q(T) moves by at most 0.070 K** anywhere from 5 to 192 K, and `rms_k` does
   not move at all to four figures. `anchor_k` rises because it is now an rms
   over a *different, harder* set — the 36 that left were the ones sitting
   inside the trajectory, which of course fitted.

   **The evidence that they carried nothing: the fit that dropped them predicts
   them anyway.** Median miss well under 0.1 K over 5–192 K; the worst are
   +0.306 K at 180.6 K, +0.220 at 130.8 and −0.320 at 5.1. So this is not a
   dataset being thinned, it is the same evidence stopping being counted twice —
   and `ANCHOR_SHARE` means what it says again.

   **Per-record drift knots** are in — `n_drift` with several records no longer
   raises. One block per record, each on its own clock, which is the first of
   T2's two terms; the campaign ramp is still step 8 and is still a separate
   term for the reason T2 gives. Inert at one record by construction, and
   *proved* so rather than assumed: `FIT_CACHE_VERSION` 7 forces the refit and
   it comes back 850.5153, the number T4 left.

### 7.1 What the trajectory alone recovers — and it is not enough

**Fit A** is the production path today: one record, the 43 h sweep. **Fit B**
adds `trace-postcal-20260905` as a second record at `RECORD_SHARE = "equal"`,
so two of the three post-recal holds stop being anchors (T4) and become 96.5 h
of trajectory the ODE is integrated down. The decimator thins it 134:1, 173,728
samples to 1,301, reconstructing the full trace to 17.5 mK rms against the
thermometer's own 28.

**Kelvin the model is LOW by at each hold** — `Λ(T_s) − Λ(T_c) > P` means the
model needs more power than was applied, so it would sit below where the
cryostat sat:

| hold | T | shipped table | fit A | **fit B** | in fit B |
|---|---|---|---|---|---|
| `pc-20260904-233855` | 96.5 K | +4.059 | +3.218 | **+1.616** | anchor |
| `pc-20260905-165509` | 114.4 K | +4.208 | +3.512 | **+1.287** | trajectory |
| `pc-20260908-154814` | 118.6 K | +4.250 | +3.597 | **+1.492** | trajectory |
| mean | | +4.172 | +3.442 | **+1.465** | |

**The trajectory recovers 57 % of fit A's miss and 65 % of the shipped table's,
and leaves 1.47 K against a 0.3 K target.** So step 7's question — "were those
holds only ever anchors, or does the model need a drift term" — is answered:
**it needs the drift term.** Three things say so at once.

**The two records cannot both be satisfied.** Adding the second one makes the
*first* fit far worse: the sweep's own weighted residual goes 0.1295 → 0.3262 K
while the post-recal record fits at 0.0953 K rms, 0.252 K max. That is not a
model being refined, it is a model being pulled between two dates.

**The per-record wander term rails trying to be the era offset.** `DRIFT_SIGMA_W`
is 2 mW and the fitted knots are

| record | drift knots, mW |
|---|---|
| `trace-sweep-20260902` | +0.62 +0.06 +0.17 |
| `trace-postcal-20260905` | **+2.01 +2.15 +2.42** |

— the post-recal block is a near-constant **+2.2 mW sitting at and past its own
prior**, which is a nuisance term absorbing a systematic. That is exactly what
T2 and T3 warn about, and it is the shape of the missing campaign ramp showing
through the only term available to it. `group_w` moves the same way, −4.40 →
−6.15 mW.

**And the curve moves where the holds are, not everywhere**: Q falls by 1.8–2.0 K
equivalent over 100–119 K and by under 0.03 K below 50 K; τ(137 K) 581.7 →
550.0 s, τ(114 K) 507 → 486.

> **A discrepancy §1 has to answer for.** §1's "where it stands today" column
> reads 2.36 / 2.65 / 2.81 K for these three holds. The shipped table measures
> **4.06 / 4.21 / 4.25** — which agrees with §2.1's "4.4 K low" and with
> `SUPERSEDED_NOTE`, and is not `T_pole` (that is within 0.06 K of `T_inf` on
> all three). 2.5 K is 60 % of 4.17, so §1's column is almost certainly §2.2's
> *first refit*, not the shipped model. **Re-derive that row before §1 is used
> as the definition of done**; invariant 9 says the number wins.
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

**T10 · Delivered watts are not `P(u)`, and the margin is unknown.** On
2026-09-10 11:33 the sample fell 3.63 K at a fixed 64.016 % readback; Jeff traced
it to a wiring / heater-circuit fault (HANDOFF.md, 2026-09-10). Every fit here
takes `Q = P(u)` from the readback and assumes the circuit delivers it, so the
fault is a **systematic on the watts-to-kelvin curve** for the whole campaign,
not a defect in one window, and it stays until a more robust circuit replaces
this one. Its one measured size is about **5 mW at 0.67 W (0.7 %)**, worth 3.6 K
at 114 K through the 639 K/W local gain; the ordinary margin is smaller by an
amount the log cannot say. Carry it as a **power-side** uncertainty `δP` on
every anchor — it scales with the local gain, 4 to 13 K per percent across the
band, so one number in kelvin is wrong at both ends — and do not tighten
`ANCHOR_SIGMA_K` or `sigma_T_inf` past what an unknown `δP` allows. Fixing the
circuit is out of scope for this plan; recording when it was fixed is not, and
it belongs in the manifest the way `era` records the recalibration.

**DONE, 2026-09-12** — PID_PLAN.md phase 1 §1.1's first prerequisite.
`fit_ode.DELTA_P_FRAC = 0.007`, entering the anchor residual as
`dQ / hypot(Λ′·σ_K, δP)`: the two bars added in quadrature **on the power
side**, which is the side the residual is computed on anyway. Proportional to
`Q` with no floor — a circuit that fails to deliver part of what it is asked
for has nothing to fail to deliver at zero output. `FIT_CACHE_VERSION` 5 → 6.

**Where it binds.** Medians per band, production fit (Λ20 / C4 / drift 3,
`groups=True`), over the 136 anchors:

| T band | n | kelvin bar, mW | δP, mW | δP / kelvin bar | total widening |
|---|---|---|---|---|---|
| 0–10 K | 22 | 19.3 | 0.19 | 0.01 | 1.00 |
| 10–20 | 19 | 25.8 | 1.55 | 0.06 | 1.00 |
| 20–40 | 30 | 10.7 | 3.07 | 0.29 | 1.04 |
| 40–80 | 10 | 2.35 | 4.00 | 1.70 | 1.99 |
| 80–120 | 12 | 1.73 | 4.54 | 2.62 | **2.88** |
| 120–160 | 35 | 5.05 | 5.00 | 0.99 | 1.39 |
| 160–250 | 6 | 2.66 | 5.35 | 2.01 | 2.32 |

**It binds where the gain is highest and nowhere else**, which is the whole
reason for carrying it in watts. Over 40–120 K the circuit's margin is the
*dominant* uncertainty on an anchor and the kelvin bar was two to three times
too tight; below 20 K it is 6 % of the bar and invisible. The 120–160 K band
is already wide because most of those anchors are `prepython` at 3 K — and T3
says that 3 K comes down at step 8, at which point this term takes over there
too.

**What it changes.** `cost` is *not* comparable across this change — the
denominator moved — so the comparable columns are `rms_k` (the sweep residual,
which the anchor bar does not enter) and `anchor_k` (unweighted kelvin):

| δP | `rms_k` | `anchor_k` | τ(137 s) | `mass_g` | `group_w` | `nfev` |
|---|---|---|---|---|---|---|
| 0 | 0.1499 | 1.5479 | 571.3 | 4.8059 | −4.72 mW | 149 |
| 0.35 % | 0.1315 | 1.5575 | 578.9 | 4.8264 | −4.51 | 96 |
| **0.7 %** | **0.1295** | 1.5590 | 582.3 | 4.8357 | −4.36 | 111 |
| 1.4 % | 0.1306 | 1.5581 | 583.5 | 4.8391 | −4.29 | 113 |
| 2.8 % | 0.1313 | 1.5574 | 583.9 | 4.8400 | −4.34 | 115 |

A **13.6 % better trajectory for 0.7 % on the anchors**, and the steady state
moves by up to **0.51 K, at 122 K**. That is §1's tension easing rather than a
weight being traded: the anchors this frees are in the band the sweep passes
through, so the curve no longer has to choose between them.

**It is a plateau, not a knife edge**, and that is the property that matters
here. Quadrupling the bar from 0.7 % to 2.8 % moves the 77 K steady state by
0.10 K and `rms_k` by 1.4 %; half of it already buys most of the effect. T10's
own statement is that the ordinary margin is unknown and *smaller* than the one
fault that measured it, so a fit that turned on the exact number would be
reading a single fault's size as data. This one does not.

---

## 9. Out of scope

`ltspm3/control/` — **not because it is finished.** It is not: it has never
closed a loop on the cryostat, and `CLAUDE.md`'s priorities opened it to change
on 2026-09-11, under the eight rules of
[safety](docs/ltspm3/safety.md), one rule-scoped commit at a time. It is out of
scope *of this plan*, which is `analysis/` and ships nothing but one generated
table. Rebuilding the loop on the model this plan produces is
[PID_PLAN.md](PID_PLAN.md) phase 3. Nothing here arms a heater or moves an
output.

This section used to read "the software PID is complete and tested and is left
alone", and it was quoted as evidence that it was done.
