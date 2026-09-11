# `analysis/` — fitting the LTSPM3 thermal model

Exploratory, not shipped. Nothing here is imported by `lschart` or `ltspm3`,
and **nothing in `control/` was changed** by any of it. These are the numbers
for a decision, not the decision.

The outputs are gitignored — CSVs and PNGs are regenerated in a few minutes.
**The inputs are not**: they live versioned in
[`reference/cooldown-10/`](../reference/cooldown-10), gzipped, and everything
here runs from a fresh clone with no setup beyond
`pip install -e ".[analysis]"`.

## Where the inputs live, and why they are in the repo

Everything is **the cooldown-10 archive**: three non-overlapping tables that
between them are the whole of cooldown 10 — which began 2026-07-15 and is still
running — and `segments.csv`, the manifest naming the windows inside them.
Read [`reference/cooldown-10/README.md`](../reference/cooldown-10/README.md)
first; it carries the provenance, the segment boundaries and the caveats.

| | |
|---|---|
| `cd10_20260715_prepython.csv.gz` | 2026-07-15 → 08-20, 5 segments. The pre-Python chart recorder's half, via `xls_to_csv`. Its heater column is **reconstructed** from the `ANALOG` commands in the log's Notes, not read back. Coldplate remapped |
| `cd10_20260824_recorder.csv.gz` | 2026-08-24 → 09-04 12:07, 4 segments. The Python recorder up to the calibration cutover, Coldplate remapped. **Contains the 43 h sweep** |
| `cd10_20260904_recorder.csv.gz` | 2026-09-04 23:38 → 09-09, 2 segments. Post-cutover, the recorder's own numbers. **Contains the 09-05 ladder, the three long holds and the 09-09 transient** |
| `segments.csv` | 239 named windows. **This is the dataset.** |
| `sweep_decimated.csv.gz` | the sweep window adaptively thinned by `decimate.py` — 4,968 rows and 62 kB against 77,374 and 1.4 MB. Regenerable in twenty seconds, committed because it is what the expensive fit actually reads. It is the **one file left** in `reference/heater-calibration/`, which makes that directory a candidate for retirement — deliberately not done in passing |

These are derived and versioned anyway, which reverses this repository's usual
rule, because of what they are derived *from*: recorder logs in the gitignored
`data/`, which a fresh clone does not have. Gzipped because git stores the same
compressed bytes either way, so plain CSV would only buy 79 MB in every working
tree instead of 13 — including for coworkers who wanted the strip chart and
nothing else. `analysis/_data.py` opens either transparently and resolves names
against the repository rather than the working directory.

## Curate, do not discover — 2026-09-10

Until this landed, the fits **found** their own dataset: five overlapping tables
(two region exports and three flattened logs, in which every dwell appeared two
or three times) scanned for constant-heater dwells by a rule keyed on
`steps.U_TOL_PCT`, deduplicated by end timestamp, and the survivor decided by
argument order. Change the constant and the dataset changed with nothing in
review to show it.

Now the dataset is `reference/cooldown-10/segments.csv`, and a change to the
finder arrives as a diff:

```bash
python analysis/curate.py --propose      # diff against the committed manifest
python analysis/curate.py --propose -v   # ...with every window's grading numbers
python analysis/segments.py              # validate it and print it
python analysis/segments.py --tables     # the archive's segment structure
```

Three things in the manifest are a human's — the `mask` rows, the `trace` rows
and the `note` column — and everything else is computed by `steps.py`'s finder
and grader. **Masks are an input to the proposal, not an output**, so a dwell
that would cross one is cut at its edge; that is what lets `--propose` be a
regression check and still respect a judgement, and it means there is one
mechanism for "do not use this" instead of a per-row override flag.

The five old tables are **deleted**. What replacing them cost, measured:

| | before | after the archive | after the review |
|---|---|---|---|
| dwells found | 234 | 236 | 312 |
| usable anchors | 111 | 111 | **149** (147 in 4–200 K, 45 with τ) |
| Λ12/C4/drift-3 `rms_k` | 0.2113, at `nfev = 300 = max_nfev` — an upper bound, not a fit | 0.2129, converged in 182 | **0.2045** |
| anchor residual `anchor_k` | 1.8796 | 1.8781 | **1.6127** |
| `tau_137_s` / `mass_g` | 567.8 / 4.7965 | 568.2 / 4.7956 | 569.9 / 4.7931 |

The third column is [the review finding](#a-wall-clock-was-throwing-away-the-best-anchors--2026-09-10) below. The middle column is what replacing the tables cost by itself:

103 of the 105 shared graded anchors reproduce to **3 nK**. Every difference is
a boundary effect and every one favours the archive; each has a `note` on its
row in the manifest. The two that matter:

- **The sweep's opening hold.** The region export starts 3.3 h into it, so over
  the 22.80 h it kept, the single-pole fit had only the drift to work with and
  returned τ = 19 days at reach 0.1 and `T_inf` 180.07 K. The archive has the
  approach as well: 26.10 h, τ = 1001 s, reach 94, **180.563 K**. That is
  exactly the failure `steps.py`'s own docstring describes.
- **The 09-09 hold at 64.015 %.** Nothing versioned reached past 16:16 that
  day, so it was 24.47 h at 118.535 K. It is now 26.30 h at 118.570 K, cut at
  18:06:04 by the one mask — and there is a **new** anchor after the transient,
  2.67 h at 118.091 K, which is the only measurement anywhere of two settled
  steady states at one heater output three hours apart.

`T_c` also moves by **1.2 mK rms** (41 mK worst) because the region export had
been rounded before the Coldplate remap — it wrote values like `6.4000` — where
the archive carries the log's own precision. Every cached fit was invalidated
by it.

## A wall clock was throwing away the best anchors — 2026-09-10

The first thing the manifest review caught. Jeff read it and said good data was
being cut, pointing at the programmed sweep as clearly robust — and it was not a
judgement call, it was a bug.

`MIN_SPAN_S = 60` was an **admission** threshold: `dwells()` applied it upstream
of `grade()`, so a dwell it rejected never got a verdict and nothing anywhere
reported its absence. On 2026-09-05 that dropped **sixteen rungs** of the
programmed ladder — 7.19 % to 49.82 %, 5.4 K to 25.7 K:

| | |
|---|---|
| dwell length | 46–48 s, which at 5–25 K is **11.5 time constants** |
| remainder | **0.000 K** |
| end rate | 0.001–0.052 K/h, against a 0.5 K/h bar |
| transient | 60 to 1265 × the sensor noise |

They are the best-settled points in the archive and a clock threw them out. The
earlier count of "three rungs" was wrong because the other thirteen are
duplicated by the cancelled run earlier that day, so the loss looked like
redundancy. Worse, the response at the time propagated the clock *outward*:
`ltspm3/tools/sweep.py` began refusing `--min-dwell` below 60 s, forbidding the
tool from doing the thing it had done well.

**What the clock was really protecting against is not shortness.** It is a dwell
with no resolvable transient. Over 48 s at 145 K, where τ ≈ 600 s, the sample
moved 80 mK against 28 mK of sensor noise; the pole was fitted to noise, came
back τ = 8.6 s, and `reach` — computed from that τ — read 5.6 and certified as
finished a window 1/12 of a time constant into an 8-hour relaxation still 0.76 K
from its answer. **`reach` cannot catch that, because `reach` is computed from
the τ being tested.**

So the clock now applies exactly where the pole cannot be believed:

```python
def settled(r):
    if r["amp_sigma"] < MIN_AMPLITUDE_SIGMA and r["span_s"] < MIN_SPAN_S:
        return False        # the fitted tau is noise; reach means nothing
    return (r["end_rate_k_per_h"] <= MAX_END_RATE_K_PER_H
            or (r["reach"] >= MIN_REACH
                and r["remainder_K"] <= SETTLED_REMAINDER_K))
```

`MIN_N` — a sample *count*, which is what a fit needs — is the only thing left
upstream of the grader. The manifest diff was **76 additions and no changes**:
nothing that was an anchor stopped being one, and no verdict flipped.

| band K | before | after | of which τ |
|---|---|---|---|
| 4–15 | 15 | **30** | 6 |
| 15–40 | 15 | **38** | 12 |
| 40–98 | 15 | 15 | 7 |
| 98–130 | 14 | 14 | 5 |
| 130–160 | 40 | 40 | 12 |
| 160–200 | 10 | 10 | 1 |
| 200–300 | 2 | 2 | 2 |
| **all** | **111** | **149** | **45** |

Every fit metric improved, including the anchor residual — with 35 % more
anchors to satisfy, which is the evidence that the recovered points are real:
bad anchors raise `anchor_k`, and it fell 14 %.

The new anchors are all September, so the thin cold-end leverage in the
**first 39 days** that `REFIT_PLAN.md` §7 step 5 flags is *unchanged* — the
`prepython` era still has no anchor below 15 K. What improved is the recorder
and postcal eras, 3 → 8 and 12 → 22 below 15 K.

**The era is a column now, not a filename test.** Five places needed to know
which side of the two boundaries an anchor fell on: `ANCHOR_SIGMA_K`, the
per-era power offset, and three plots. They knew it by
`source.startswith("fit_cd10")`, and the failure mode of that was silent — an
input rename would make every anchor `recent`, the offset would fit nothing, and
the curve would come out about a kelvin wrong for both halves with no symptom.
`measured.csv` carries `era` in {`prepython`, `recorder`, `postcal`},
`segments.ERAS` is the map, and an era with no entry raises. The `prepython`
set is 37 anchors, which is the old `fit_cd10` set exactly.

## …and then the same wall clock was keeping the worst ones — 2026-09-10

The other half, and it is the same sentence read the other way. `MIN_SPAN_S`
stopped being an admission threshold above; what it became was a *conditional*
test — a dwell whose pole cannot be believed has to have lasted 60 s — and 60 s
is still a duration on a plant whose τ runs from under 0.1 s at 5 K to 850 s at
142 K. A duration cannot express "several time constants" across a factor of
five thousand, so it is wrong at both ends: it threw away 46 s at 11.5 τ, and it
kept 200 s at a third of one.

**`analysis/` can now ask the real question, because Phase A measured the
answer.** The dwells that resolved their own transients carry τ over
25.8–247.6 K; `steps.plant_clock` interpolates them log-log, and
`steps.long_enough` asks `MIN_REACH × τ(T)`. This is `REFIT_PLAN.md` §6.2
option 4, and it is `AUDIT-2026-09-10`'s finding **1** applied where finding 2
also needed it.

```python
def settled(r, tau_plant_s=None):
    if pole_unbelievable(r) and not long_enough(r, tau_plant_s):
        return False        # the fitted tau says nothing; reach means nothing
    return (r["end_rate_k_per_h"] <= MAX_END_RATE_K_PER_H
            or (r["reach"] >= MIN_REACH
                and r["remainder_K"] <= SETTLED_REMAINDER_K))
```

**Three things are load-bearing and each is checked rather than argued.**

*It is not circular.* A `tau` grade requires `reach ≥ MIN_REACH` and
`amp_sigma ≥ MIN_AMPLITUDE_SIGMA`, which is exactly the negation of
`pole_unbelievable` plus a bound no ceiling pin can meet — a ceiling pin has
`reach ≤ 1/19` by construction. So the set that *builds* the clock is disjoint
from the set *tested against* it. `archive_dwells` grades once without the
clock, builds it, re-grades with it, and raises if any `tau` verdict moved.
None does, which is why one round of iteration is the whole of it.

*It does not depend on a file a clone lacks.* `analysis/measured.csv` is
gitignored. A grader that read it would produce a manifest nobody else could
reproduce, so `plant_clock` reads the rows `steps` just fitted. That costs
nothing measurable: the interpolant from all 37 τ anchors, from the 29 shorter
than `HOLD_MIN_S` (whose poles carry no campaign drift), and from
`measured.csv`'s own τ column give **the same verdict on all 44 rows** the
guard is asked about.

*The archive separates cleanly.* `margin` is the factor τ(T) would have to be
wrong by to flip a verdict, and over the 97 windows the guard judges there is a
**gap from 0.86 to 2.41 with nothing in it** — the decision is not sitting on
top of the data. `curate.py --plant` prints all 97 with their margins.

| | before | after |
|---|---|---|
| anchors | 149 | **136** |
| with a believable τ | 37 | **37** — unchanged, by construction |
| verdict changes | | **14 dropped, 1 recovered** (net −13) |
| `quality` relabelled `unsettled` → `unresolved` | | 17, all already excluded |

The one recovered anchor is `rec-20260824-171059`, 57 s at 4.75 K: a ceiling pin
the 60 s clock had refused, which on the plant's clock ran 11 time constants.
It is the sixteen rungs again, and it is the reason to be confident this change
is not simply a stricter bar — the same test that drops 14 warm windows keeps a
cold one the wall clock could not.

**What was dropped, and it is not what the plan expected.** `REFIT_PLAN.md`
§6.2 predicted two verdict changes, having measured only the ceiling pins. Two
of the fourteen are those (`pp-20260808-155602`, `rec-20260901-222818`); the
other twelve come from the `amp_sigma` branch, which the plan did not score
because the wall clock was standing in for the plant clock there too. Every one
is a warm dwell — 128.8 to 180.5 K, spans 70 s to 1641 s — that ran under three
of the plant's time constants with no transient of its own to prove otherwise.
Two of them are `AUDIT-2026-09-10-REPLY.md` §1's own examples, 200 s at 147.1 K
and 330 s at 170.4 K, which that document called "a third of a τ" and "half a
τ" while having no mechanism to refuse them.

**Four rows are decided by under a factor of two and each carries a manifest
note.** `pp-20260808-155602` is the rejoinder's named row, decided at 0.63 —
τ(99.42 K) would have to be 37 % low to keep it, and where the interpolant is
checkable against the shipped table at that temperature the two agree to 2.2 %.
`rec-20260828-130841` is the closest call in the archive at 0.86.

## The drift is a power, not a temperature — 2026-09-10

`analysis/drift.py`, REFIT_PLAN.md Phase B step 5. The refit exists because the
cryostat drifts and the model treats 55 days of anchors as simultaneous; this
is the gate that says how much, and where a drift knot may go.

**A drift quoted in K/day is not a property of the cryostat.** It is the
underlying change times the *local gain*, which runs 2 K/% at 30 K to 14 K/% at
145 K. Three independent output bands, de-overlapped so no anchor is counted
twice:

| band % | n | days | T range | K/% | K/W | K/day | **mW/day** |
|---|---|---|---|---|---|---|---|
| 52.0–53.5 | 9 | 50.0 | 27.2–32.0 K | 2.04 | 118.7 | 0.0334 | **0.281** |
| 62.9–64.4 | 9 | 48.5 | 98.4–118.6 K | 11.52 | 555.1 | 0.1862 | **0.335** |
| 65.2–66.7 | 30 | 25.4 | 129.2–148.9 K | 13.67 | 634.4 | 0.1413 | **0.223** |

**K/day spans 5.6×. mW/day agrees to ±25 %.** The drift is a power, so it
belongs at the heater's own node — which is where `fit_ode`'s existing
within-record drift term already puts it, and where the campaign ramp has to go
too. A temperature offset would be wrong at both ends of the band by construction.

Three routes to the same number: **+0.281 mW/day** median here, **+0.27 mW/day**
from §2.3's band (which this table does not reuse), and the 12/4 fit's own
per-era `group_w` offset of **4.60 mW** across the ~20 days separating the two
eras' centroids, against 0.281 × 20 = 5.6 mW.

The regression is deliberately **unweighted**. The anchors' bars are dominated
by `ANCHOR_SIGMA_K`, which is 3.0 K for `prepython` and 1.0 for the rest — an
era label, not a measurement — so weighting by them would down-weight exactly
the old half of the campaign that carries the date leverage, and the drift
would come back small for a reason that has nothing to do with the cryostat.

**Coverage decides the knot count, and it says affine.** A knot interval whose
anchors do not span temperature cannot tell "the cryostat got warmer" from "Λ
is lower here" — both lift every anchor in the interval equally. The test is
≥ 5 anchors in each of two clumps ≥ 3× apart in T, reported at the most
balanced passing split:

```
affine     days  0.0-55.3   n=136   4.8-247.6 K   EARNED  (68 near 17 K, 68 near 135 K)
3 knots    days  0.0-27.7   n= 12  22.5-247.6 K   NO      no split 3x apart with 5 either side
           days 27.7-55.3   n=124   4.8-192.4 K   ok      (62 near 15 K, 62 near 133 K)
```

A bare max/min ratio passes the failing interval at 11.0 and separates nothing,
which is why the test is not a ratio.

## Λ and C without a fit — 2026-09-10

`analysis/fit_ode.py --seed-only`, REFIT_PLAN.md Phase B step 6. Every number
it prints comes from `Λ(T_s) − Λ(T_c) = Q` at a settled dwell and `C = τ·dΛ/dT`
at a measured relaxation. **No ODE is integrated anywhere**, which makes it both
a two-second sanity check on a fifteen-minute fit and the closest thing here to
a model-free reading of the cryostat. It is also what the fit is now seeded
from, in place of a hand-calibrated power law.

It agrees with the fit to a few percent, from two methods sharing no machinery:

| | model-free | converged fit |
|---|---|---|
| C(137 K) | 1.006 J/K = **4.960 g** of the Debye mix | **4.79 g** |
| τ(137 K) | ~610 s | 567 s |
| Λ(186 K) − Λ(8.7 K) | 0.752 W | 0.778 W (the old seed's calibration) |
| local resistance at 130 K | 620 K/W | 639 K/W between the two September holds |

**Two things went wrong getting there and both are worth keeping.**

*The level of Λ is a gauge, two attempts to choose it were wrong, and the
second was actively harmful.* Trap T1 says an added constant is a null
direction of the data; it is one for the roughness penalty too, since the
penalty acts on `log(dΛ/dT)` and `d(Λ+c)/dT = dΛ/dT`. The first search duly
returned the top of whatever grid it was handed — 9.68 W against a curve
spanning 0.97. The second searched the genuine residue (the parameterisation
interpolates **log Λ** between knots, so a constant moves the curve *between*
them) through the real `LogLog`, which was defensible. Measuring it killed it:

| gauge | cost | `nfev` |
|---|---|---|
| 0 | **994.64** | **149** |
| 0.0175 — the search's answer at 20 knots | 994.64 | 566 |
| 2.4121 — its answer at 9 knots, railed at 3× the span | **1163.02** | 561 |

Zero reaches the same optimum 3.8× faster, and the 9-knot answer drops the fit
into the *worse* basin — the one the power-law seed was stuck in. The search is
deleted; Λ is seeded on the anchors' own `Λ(T_c) = 0`.

**And the seed was choosing which local minimum the fit landed in.** That is
worth more than the 4 % of rms it was supposed to buy. On 20/4 with the drift
term and the per-era offset, run out to convergence:

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

Note the better optimum has a **higher** `anchor_k` with a lower `rms_k`. That
is REFIT_PLAN §1's tension — the model as posed cannot satisfy the trajectory
and the holds at once — appearing in the shape of the basin rather than in a
weight study.

*A single magnitude on the Debye shape is not good enough for C.* Fitting one
factor to the mix reconstructed τ(137 K) as **804 s where the τ anchors there
say 607** — 32 % out, because a median ratio taken over 25–192 K lands its
error wherever C departs from Debye most, and that is not where you want it.
The shape now comes from the τ anchors over 28.4–192.4 K and falls back to the
Debye mix outside, scaled to match at the join. Below 25 K there is no τ to
read C from, C falls three orders of magnitude, and the shape prior holds it to
that mix regardless — so that is the right division of labour rather than a
compromise.

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
# 0. is the manifest still what the finder proposes?   (~1 min)
.venv/Scripts/python.exe analysis/curate.py --propose
.venv/Scripts/python.exe analysis/curate.py --propose --plant   # the plant clock
#    and every verdict it decided, sorted by margin -- the four thin ones are
#    the rows to read, and each has a note on it in the manifest

# 1. the manifest's windows -> measurements with error bars   (~15 s)
#    no arguments: it reads the archive and the committed manifest
.venv/Scripts/python.exe analysis/measure.py
.venv/Scripts/python.exe analysis/measure.py --holds     # drift, diurnal, baths
.venv/Scripts/python.exe analysis/measure.py --verify    # REFIT_PLAN.md 6's gate

# 1b. is the cryostat drifting, and may a drift knot go there?   (~5 s)
#     REFIT_PLAN.md Phase B step 5's gate.  Reads the anchors and nothing else.
.venv/Scripts/python.exe analysis/drift.py
.venv/Scripts/python.exe analysis/drift.py --knots 3      # would 3 knots earn it?

# 1c. Lambda and C read straight off the anchors, no fit at all    (~2 s)
#     The model-free reading.  A sanity check on a fifteen-minute fit, and
#     what the fit is now SEEDED from -- REFIT_PLAN.md Phase B step 6.
.venv/Scripts/python.exe analysis/fit_ode.py --seed-only

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

# 4. plan the next sweep -- what to fill in, and how long it takes  (~10 s)
.venv/Scripts/python.exe analysis/plan_sweep.py

# 5. refreeze the simulator's copy of the model, if the fit moved   (~10 s)
.venv/Scripts/python.exe analysis/export_response.py --verify
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
| `steps.py` | the finder, the pole and the bars — **no `__main__` any more**, see `measure.py`. `archive_dwells()` is the **one** scan of the archive and `curate.py` builds the manifest from it. `fit_pole` fits `T = T∞ + A e^(−t/τ)`; `pole_bounds` says what interval it searched τ on, because a τ *at* a bound is the search giving up and neither grader notices (AUDIT-2026-09-10 finding 2). **Read the `U_TOL_PCT` note**: the 218's readback flickers between adjacent codes, and an exact match shreds every dwell below 29 K. |
| `measure.py` | **what a fit reads.** Measures the windows the manifest names and writes `measured.csv`: a jump keeps `fit_pole`'s numbers exactly, a hold gets level + drift + relaxation + a 24 h harmonic, and every row carries `sigma_T_inf` = statistical ⊕ extrapolation ⊕ the measured long-term fluctuation, plus `t_mid` and `days`. `--verify` checks REFIT_PLAN.md §6's exit criteria. **A single pole is the wrong model for a hold** and had four graded anchors 0.4–1.7 K out; `T_pole` is kept beside `T_inf` so that stays visible. |
| `fit_ode.py` | integrates the ODE down the 8.8 h sweep and fits Λ and C as monotone cubics in (log T, log y). One curve's knots freed at a time. Writes `ladder.csv`. |
| `decimate.py` | the sweep, thinned where nothing is happening and kept where it is. **16x fewer samples, 26x faster to fit, 0.8% different.** Writes `sweep_decimated.csv.gz` |
| `bath.py` | the coldplate as a first-order lag driven by the heater, not as a bath. **tau = 175 s, 27.6 mK rms over a 2.30 K swing.** What makes the plant self-contained |
| `_data.py` | where the inputs live and how to open them; every reader here goes through it |
| `segments.py` | the archive and the manifest. `read_table` for a whole table, `load(id)` for one named window, `check()` to validate the lot |
| `curate.py` | proposes the manifest and diffs a proposal against the committed one. **The review artefact** |
| `fit_lambda.py` | asks whether the settled points alone can separate `σ_r T⁴` from conduction. They cannot — see below. |
| `diagram.py` | the model, with each ODE term on its arrow |
| `plot_gain.py` | heater → steady temperature, on both a percent and a **power** axis. The watts one is the one to hand to somebody on a different cryostat with the same heater. |
| `plot_ode.py` | trajectory, residual, Λ, dΛ/dT, C, τ, and the ladder |
| `pid_tuning.py` | SIMC PI gains scheduled against T, on the loop *as configured* |
| `settling.py` | why a settle takes 20–30 min, and what a 10 K/min sweep needs |
| `export_response.py` | evaluates the production fit once and freezes Q(T), Λ′(T), C(T) and T_c(T) onto a 300-point log grid as `ltspm3/_fitted_table.py`, so the simulator has the real model without scipy. **Q is exported evaluated, not as Λ**: Λ is 2.38 W where Q is 8 mW, and reconstructing the difference from two interpolated Λ costs five significant figures. `--verify` reports the grid error. Re-run after any refit. |
| `plan_sweep.py` | where to put the rungs of the **next** sweep, and what each will cost. Inverts the fitted steady state for `u(T)` and reads `τ(T) = C/Λ′` off the same fit, then applies `steps.py`'s grader backwards to say how long each dwell has to run. Writes `sweep_plan.csv`, which `ltspm3/tools/sweep.py` runs. |

## What came out

Re-run 2026-09-05: 43 h sweep, corrected `T_c`, current dwell grader. Every
number below came out of that run. The full ladder is `analysis/ladder.csv`.

| | |
|---|---|
| fit quality | **0.1577 K rms** over 43 h and 4.9–192.6 K — Λ 20 knots with a curvature penalty on dΛ/dT, C 4, 3 drift knots, a per-cooldown offset, fitted on the decimated sweep. 10.9 K max, all of it inside one 9-minute recovery slew. Without the drift term, 0.404 K at Λ 10 |
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

**The remap was done on the LOGS**, kelvin → resistance → kelvin, by

```bash
python -m lschart.tools.recalibrate --column Coldplate \
  --from reference/sensor-curves/X186276.340 \
  --to   reference/sensor-curves/X186279.340 \
  "data/cd10/*.csv" -o data/coldplate-recal/cd10
```

and the two pre-cutover archive tables are built from
`data/coldplate-recal/`, so `fit_lambda`, `fit_ode`, `plot_gain`, `plot_ode`
and `steps` read the corrected `T_c` with no argument and no code change. The
third table is post-cutover and is the recorder's own numbers, untouched, so no
table mixes two calibrations. Only `Coldplate` moved; every other column is
byte-identical. See [cryostat.md](../docs/ltspm3/cryostat.md).

The five pre-archive tables were remapped in place at the time, and are in git
history at `72c3f32` as logged and at `6432128` as remapped.

**908 rows of the pre-Python half came back blank rather than converted.** They
were clamped at the top of the loaded table — the wrong curve rails at
330.324 K — and a clamped reading has no resistance behind it to convert. They
are the first ninety minutes of the cooldown, at room temperature, and every
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

## Each cooldown gets its own offset — 2026-09-05

The residual against the settled dwells had a systematic look that survived
everything thrown at it: 9 to 20 Λ knots, the curvature penalty, the drift
term, and driving `T_c` from the bath. Correlating it against every anchor
property said why it was not a Λ problem:

| | vs `T_inf` | vs `settle_K` | vs `span_s` | vs `tau_s` |
|---|---|---|---|---|
| this cooldown | −0.36 | **−0.55** | −0.55 | −0.31 |
| CD10 | −0.18 | **−0.71** | +0.03 | +0.61 |

The residual tracks how far each anchor's `T_inf` was *extrapolated* better
than it tracks temperature. And the dominant feature was never the slope: it
was a **2.46 K separation between the two cooldowns**, with the same
−3.7 mK/K slope inside each. A common slope on two independent cooldowns is a
property of the anchors, not of either cryostat state.

CD10 is a different cooldown — different contact, different radiation, a
different parasitic load — and no single Λ can satisfy both. That has been a
caveat in this file since it was written. It is now **one fitted parameter**:

| | sweep rms | this cooldown | CD10 |
|---|---|---|---|
| one curve for both | 0.1678 K | mean −0.25 K, max \|res\| 2.39 | mean **+2.21 K**, max 3.53 |
| a power offset for CD10 | **0.1577 K** | mean −0.11 K, max 2.39 | mean **+0.17 K**, max 1.50 |

**CD10 sits −3.67 mW from this cooldown at matched temperature**, which at
~550 K/W near 180 K is about 2 K and is the "two cooldowns differ by 3.2 K"
caveat, measured instead of absorbed into per-anchor margins. Both clouds now
sit within 0.2 K of zero and Λ stays universal — the same principle as the
slow drift, one timescale up.

Things that did **not** explain it, all measured before this one was tried:
more Λ knots (9→20 leaves the trend at −6.4 mK/K), the curvature penalty,
adding the fitted drift to the drawn steady-state curve (it makes this cooldown
*worse*, −0.25 → −0.74 K), and dropping CD10's anchors entirely (sweep rms
0.4467 → 0.4383).

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
