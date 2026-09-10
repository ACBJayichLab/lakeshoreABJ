# Reply to [AUDIT-2026-09-10](AUDIT-2026-09-10.md), finding 2

Written while applying that finding during Phase A of [REFIT_PLAN.md](REFIT_PLAN.md).
The audit is right about the disease and right that these numbers were reaching
the refit. Its remedy has four parts; **two are applied, two are not**, and
this is the argument and the measurements for the two that are not, so that the
decision is auditable against the reasoning rather than only against the diff.

| part of finding 2 | verdict |
|---|---|
| expose the search bounds so a pin can be detected | **applied** — `steps.pole_bounds`, `pole_floor`, `pole_ceiling`, mirrored in `sweep.py` |
| refuse `"tau"` for a floor pin | **applied** — 8 verdict changes, `load_taus` 45 → 37 |
| refuse *any* grade for a floor pin whose amplitude is under `MIN_AMPLITUDE_SIGMA` | **not applied** — the discriminator is anti-correlated with correctness (§1) |
| refuse *any* grade for a ceiling pin | **not applied** — costs five good anchors to catch two (§2). A better test exists (§4) |

Everything below is reproducible from the archive at this commit.

## The one-sentence diagnosis

**A τ at a search bound is not a fact about the dwell.** It is a fact about the
*ratio* between the dwell's length and the plant's time constant at that
temperature — and the window is only one of those two terms. No test computed
from the window alone can recover the ratio, because the other term is not in
the window. Every attempt to do it anyway has to pick a proxy, and all three
proxies in this repository fail in the same direction.

That is finding **1**'s own conclusion — "the guard is a wall clock standing in
for a plant clock" — and finding 2's remedy is two more wall clocks wearing
different hats.

## 1. The noise-pole clause is anti-correlated with correctness

The proposal is to refuse any grade for a floor pin whose transient is under
`MIN_AMPLITUDE_SIGMA`, "that is a noise pole". Sometimes it is. But **a dwell
that is genuinely finished is also flat, and also has no transient**, and it is
the best kind of anchor there is.

`tests_ltspm3/test_sweep_tool.py::test_a_flat_dwell_is_steady_but_carries_no_believable_tau`
pins exactly that case and the clause breaks it — which is how the problem
surfaced. Three dwells, and `amp_sigma` on each:

| dwell | what it is | `amp_sigma` |
|---|---|---|
| the test case: 300 samples, 598 s, 42 K, flat to 0.5 mK | **settled**, and ideal | **0.08** |
| `pp-20260815-095502`: 200 s at 147.1 K, plant τ ≈ 620 s | a third of a τ | 0.7 |
| `pp-20260817-211021`: 330 s at 170.4 K, plant τ ≈ 612 s | half a τ | 1.4 |

All three are far under the bar of 20, so the clause treats them alike. Worse,
**the ordering runs backwards**: the settled dwell has the *smallest*
amplitude of the three. It has to. A finished relaxation is flat because it is
finished; a barely-started slow one is flat because the first few percent of an
exponential is nearly a straight line. Flatness is simultaneously the best
evidence and the worst, so no threshold on it can be right, and a threshold
that fires on small amplitudes preferentially discards the good windows.

`MIN_SPAN_S = 60` does not rescue it either — both real cases run 200 s and
330 s and sail past it. That is the wall clock failing for the third time.

**What is done instead.** Both are reported, not refused. `measure.py` gives
them `sigma_tau_s` of **214 %** and **103 %** of τ, against a median of 1.39 %
over the 37 τ anchors — which is the honest signal available without a plant
model, and is louder than a boolean.

## 2. The ceiling clause costs five good anchors to catch two doubtful ones

46 dwells in the archive sit on the ceiling; 7 are graded, all `steady`.
Refusing all 7:

| dwell | T | span | fitted τ | line slope | what it is |
|---|---|---|---|---|---|
| `rec-20260824-171158` | 4.84 K | 38 s | 751 s | +0.225 K/h | settled — plant τ is 0.01 s |
| `rec-20260903-205138` | 5.14 K | 78 s | 1560 s | −0.353 K/h | settled — plant τ is 0.01 s |
| `pc-20260909-212001` | 118.09 K | 9598 s | 191959 s | −0.003 K/h | settled, 2.7 h |
| `pp-20260813-133702` | 141.99 K | 35440 s | 708796 s | −0.004 K/h | settled, 9.8 h |
| `pp-20260815-100312` | 146.00 K | 59910 s | 1198196 s | −0.004 K/h | settled, 16.6 h |
| `pp-20260808-155602` | 99.42 K | 840 s | 16800 s | −0.042 K/h | **doubtful** |
| `rec-20260901-222818` | 170.64 K | 232 s | 4640 s | −0.460 K/h | **doubtful** |

Five of the seven are settled and two are doubtful, so the blanket refusal has
the wrong sign on five rows out of seven.

**Two of the five are the audit's own worst examples.** Its −0.76 K and
−1.36 K rows are `pp-20260813-133702` and `pp-20260815-100312`, and its
sharpest sentence is about the second: "a 16.6 h hold at 146 K placed 1.36 K
past where it ended on a tau of 14 days… the campaign drift being read as a
relaxation." That reading is exactly right, and the conclusion drawn from it is
one step too far. **The window is not the problem; the model fitted to it is.**
Phase A fits these with level + drift instead of a pole and they come out as
anchors at 142.808 K and 147.451 K, drifting −4 mK/h — see
[REFIT_PLAN.md §6.1](REFIT_PLAN.md). Refusing them discards a 16.6-hour hold
because a two-parameter exponential could not describe a straight line.

**And a slope bar cannot separate them.** The obvious repair — a ceiling pin is
a straight line, so grade it on that line's slope — was measured over all 46.
Every one of the seven graded pins drifts under `MAX_END_RATE_K_PER_H = 0.5`
K/h, the largest being −0.460, and the other 39 are already `excluded` and stay
so at any bar between 0.5 and 0.8 K/h. **The test changes no verdict at all.**
It looked like the answer and it is inert.

## 3. Why the holds were never the hard case

Worth separating, because it shrinks the problem. For a **hold** the ratio that
a ceiling pin leaves ambiguous is not ambiguous: the longest τ anywhere in this
cooldown is 534 s at 114 K, so `curate.HOLD_MIN_S` of 8 h is already **54 time
constants**. Any hold has finished relaxing. There is nothing to decide.

Holds only looked like the hard case because a single pole was being fitted to
them, and a pole with nothing left to describe describes the drift. Once
`measure.py` fits them properly the hold half of the ceiling problem
evaporates — which is what §6.1 measured. **The residue is small and specific:
short dwells whose pole hits the ceiling.** Two of those are graded.

## 4. What would actually work, and it is the audit's finding 1

For those two, the plant's τ is the discriminator — so use it. `reach`
recomputed against the plant's τ rather than the fitted one, on the same seven:

```
  rec-20260824-171158     4.84 K     38 s   plant tau     0.01 s   reach  3796.25   ok
  rec-20260903-205138     5.14 K     78 s   plant tau     0.01 s   reach  6771.07   ok
  pc-20260909-212001    118.09 K   9598 s   plant tau   525.47 s   reach    18.27   ok
  pp-20260813-133702    141.99 K  35440 s   plant tau   599.36 s   reach    59.13   ok
  pp-20260815-100312    146.00 K  59910 s   plant tau   607.33 s   reach    98.65   ok
  pp-20260808-155602     99.42 K    840 s   plant tau   436.37 s   reach     1.93   UNDER MIN_REACH
  rec-20260901-222818   170.64 K    232 s   plant tau   611.56 s   reach     0.38   UNDER MIN_REACH
```

**Perfect separation against the existing `MIN_REACH = 3.0`**, with three
orders of magnitude of daylight on either side of the boundary. Both doubtful
dwells are caught, all five settled ones are kept, and no new constant is
introduced. This is finding 1's prescription — "a dwell with no resolvable
transient should have to run some multiple of the plant's tau at its own
temperature, not 60 s" — applied to finding 2, where it also works.

Finding 1 rejected it for `analysis/` on the grounds that "`analysis/steps.py`
has no plant model and should not import one". The second half is right and is
invariant 1. The first half stopped being true this commit: **`measured.csv`
now carries the plant clock** — 37 τ anchors over 25.8–247.6 K, from windows
that resolved their own transients. `analysis/` can read its own measurements
without importing anything.

Three things to be honest about before anyone builds it:

- **It is not circular, and that needs stating rather than assuming.** A τ
  anchor requires reach ≥ 3, `amp_sigma` ≥ 20 and no floor pin, and no ceiling
  pin can satisfy those. The set that defines τ(T) is disjoint from the set
  being tested against it.
- **It is a second pass.** The grader would need τ(T) before it can grade,
  and τ(T) comes from graded dwells — so: grade what can be graded without it,
  build τ(T), then settle the ceiling pins. That is one round of iteration, and
  "curate, do not discover" contains it: the output is a committed manifest a
  human reviewed, not a fixed point rediscovered per run.
- **Coverage is thin below 25.8 K and the verdict does not care.** The two cold
  pins need τ(T) extrapolated. It does not matter: at 38 s and 78 s, τ would
  have to be wrong by a factor of ~1,300 to pull reach under 3. Where the
  extrapolation is weak the answer is insensitive to it, which is the good case
  for extrapolating.

The table above used `ltspm3.fitted_response.tau_s` to show the discriminator
works, because it was to hand in a scratch script. That table's steady state is
the superseded one this whole refit exists to replace — but its **τ** is
validated to better than 10 % over 40–120 K ([REFIT_PLAN.md §2.4](REFIT_PLAN.md)),
which is the only column used here, and the margins above are three orders of
magnitude wide. A real implementation must read `measured.csv`, not that table,
or invariant 1 falls.

**Not implemented.** It changes which windows are anchors, which is the
manifest, which is the artefact the Phase A pause exists to put in front of a
human. §6.2 of the plan carries it as one of four options.

## What would change my mind

- **§1** — a discriminator computed from the window alone that separates the
  598 s flat dwell at 42 K from 200 s at 147 K. I do not think one exists,
  because the missing term is not in the window; a demonstration would be more
  interesting than this document.
- **§2** — evidence that `pc-20260909-212001`, `pp-20260813-133702` or
  `pp-20260815-100312` is *not* settled. Their drifts are −0.003, −0.004 and
  −0.004 K/h over 2.7, 9.8 and 16.6 hours, so it would have to be an argument
  about something other than the temperature record.
- **§4** — τ(T) from `measured.csv` turning out badly conditioned somewhere it
  matters. Then option 1 in §6.2 — the wall clock at `HOLD_MIN_S` — becomes the
  pragmatic choice, and it should be labelled a proxy in the code rather than
  presented as a physical test. That is the mistake this note is about, and it
  is worth making deliberately if it is made.

## What the audit got right, and this reply does not touch

The floor half is a real defect and it was doing real damage: 8 anchors
carrying τ = 4.0 s — two samples of cadence — at 5.4 to 25.1 K, where the
plant's own τ is under 0.1 s to 3.4 s, entering `fit_ode`'s objective as
residuals of nine sigma in log τ and pulling C(T) at exactly the cold end the
plan says has the least leverage to spare. `load_taus` is 45 → 37 and its
lowest surviving τ is 5.1 s. Nothing about that is in dispute, and it was found
by reading the tree rather than by anything breaking.

The framing that made it findable is also right and is worth keeping: **the
grader believes a pole the fitter could not actually fit.** Both halves of
finding 2, both halves of finding 1, and the hold problem Phase A found
independently are all that one sentence. The disagreement here is only about
which of them a window-local test can fix.
