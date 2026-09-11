# Handoff — 2026-09-10 (Phase A, option 4, and half of Phase B)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
refit's own state is [REFIT_PLAN.md](REFIT_PLAN.md). This goes stale.

Previous: [HANDOFF-2026-09-07.md](HANDOFF-2026-09-07.md).

> ## THE HEATER IS ON, AND HAS BEEN FOR FIVE DAYS
>
> The recorder is running `config-ltspm3-heater.yaml` and the 218's analog
> output has been held at **64.016 %** since 2026-09-08 15:48. It was 63.699 %
> from 09-05 16:55 before that. Nothing will move it on its own — invariant 6,
> availability outranks tidiness, and cutting this heater is a change of state
> rather than a retreat to safety. Move it deliberately, or leave it.
>
> As of 2026-09-10 14:43 the sample reads **114.24 K**, coldplate 6.626 K, and is
> still falling after the heater-circuit fault noted below.

## Note: the sample fell 3.6 K on 2026-09-10 at a fixed readback — a heater-circuit fault

**Onset 11:33, `ls218.aout1` readback unmoved at 64.016 % throughout.** Jeff
traced it in person to a **wiring / heater-circuit issue**. Fixing it is out of
scope for this thread; what is in scope is what it means for the model.

One-minute means, 11:32 → 14:24: Sample **−3.630 K**, Coldplate −18 mK, 1st
Stage +12 mK, 2nd Stage −7 mK, RAD SHIELD −238 mK, readback −0.0003 %. A pole
on the first 90 minutes gives +3.10 K at τ = 614 s — the plant's own time
constant at this temperature — so the prompt part is a **step in delivered
power at the sample**, about 4.9 mW at the 639 K/W the two September holds
measure between them; a −0.2 K/h tail follows, tracking the shield. As of
14:43 the sample read 114.24 K and was still falling. **Nothing here is a
hazard**: the stage is cooling, the readback is unchanged, `status.json`
reports `control: null`.

**What the model can and cannot model.** Everything in `analysis/` and in
`ltspm3/fitted_response.py` takes `Q = P(u)` from the 218's readback and
assumes the circuit delivers it. That assumption is the model's input, not a
term in it — and Jeff's finding is that **the circuit does not always deliver
it**. The consequence is not confined to the event window. Delivered watts
carry an uncertainty of unknown size for the whole campaign, which is a
**systematic error on the watts-to-kelvin curve itself**, and it stays until a
more robust circuit replaces this one. The one measurement of its size is this
event: about 5 mW at 0.67 W, or 0.7 % of delivered power, worth 3.6 K at
114 K. How much smaller the ordinary margin is, the log cannot say. So:

- **Carry it as a power-side error bar, not a temperature one.** REFIT_PLAN.md
  trap T10. A steady-state anchor at output `u` is a measurement of `T` at a
  power `P(u) ± δP`; the fit should see `δP` as such, because it scales with
  the local gain (639 K/W here, 4 to 13 K per percent across the band) rather
  than being one number in kelvin. Do not tighten `ANCHOR_SIGMA_K` or Phase
  A's `sigma_T_inf` past what an unknown `δP` allows; that is precision the
  circuit did not deliver.
- **From 11:33 until the circuit is repaired and verified, `Q` is unknown** and
  nothing in that stretch is an anchor — not the transient and not the settled
  level after it, however flat. Once it is flat, archive it and give the whole
  stretch one `mask` row with a paragraph, as `mask-20260909-180604` has
  (`reference/cooldown-10/README.md` has the commands; do not archive a
  half-event, §0.4). The three post-recalibration holds before 11:33 stand.
- **A fault of this kind is indistinguishable, in the log, from "a load path
  the model does not have" (§2.5) or from a per-era power offset.** The 09-09
  18:06 event is *not* attributed to it — there the cold-head channels stepped
  and the sample followed at τ ≈ 2,200–3,700 s, a change at the cold head, not
  at the sample — but the general point stands: a fit can only be as right as
  the heater circuit was during its inputs. Record repairs and known circuit
  changes as manifest rows, the way `era` records the recalibration.
- Nothing in the log records what a person did at the cryostat. The CSV has a
  `Notes` column and it is empty across both events. A `note` command kind
  through the spool would make the next one attributable on the day.

> **The data is only on this machine.** `data/` is gitignored, so the event
> lives in `data/ltspm3-heater_2026-09-10.csv` and nowhere else.

## What this session did

Nine commits, `39dd52a..c58b264`, **merged to `main`**, and three after them on a
branch. 915 passing, ruff clean, `curate.py --propose` reports no diff,
`measure.py --verify` meets all four exit criteria.

| | |
|---|---|
| `4fb4d20` | `AUDIT-2026-09-10.md` — the audit these commits answer |
| `4d4c538` | manifest boundaries to the **millisecond**. A second-truncated `t_end` lands before the sample it came from, so slicing a window back out returned one row fewer than the grader saw — 308 of 312 windows, worth up to **0.50 K** of `T_inf` and 83 % of a τ |
| `48b71b0` | the rehearsal test gets a `--journal`, so it stops dropping a CSV in the working directory every run |
| `311c204` | **Phase A** — `analysis/measure.py`, `analysis/measured.csv` |
| `e3a87c4` | a τ at the search **floor** is no longer graded `tau`. 8 verdict changes, `load_taus` 45 → 37 |
| `1296f70` | `AUDIT-2026-09-10-REPLY.md` — the two parts of the audit's finding 2 that were not applied, and why |
| `595d87a` | (Jeff) the rejoinder: both conceded, and the live sweep tool is where the fix still had to land |
| `c58b264` | the sweep tool's guard moved onto the **plant's** τ |
| `821c85e` | **option 4** — the `analysis/` grader's guard moved onto the plant's τ as well, on a clock it MEASURES. 149 → 136 anchors, 37 τ unchanged. §6.3 |
| `ac69d7e` | **Phase B steps 1-3 and 5** — the types, the plumbing proved inert, and the drift gate |
| `b38ecbe` | **Phase B step 6** — the measured seed, which converges where the old one did not and lands 14.5 % better |

## Where the refit stands

**Phase A is done and awaiting a human review.** That review is the pause, and
it is the point of the phase: §6.1 of the plan is the write-up, and
`analysis/measure.py --holds --verify` reproduces every number in it.

The three things worth a reviewer's attention:

- **A single pole was the wrong model for a hold**, and it had five graded
  anchors wrong by more than 0.1 K — three past a kelvin, worst `rec-20260828-141631` at
  **−1.534 K** on a 74.9 h hold. `T_pole` sits beside `T_inf` in
  `measured.csv` so every one of those is auditable.
- **τ at 114 K was two windows all along.** 513 s is the first 40 minutes of
  `pc-20260905-165509`; over all 70 h the same relaxation gives 534.0 ± 6.0 s.
  The 40-minute answer is 4.0 % low at reach 4.7, which is `fit_pole`'s own
  documented reach bias, now measured.
- **The diurnal amplitude is measured and its phase is not** — 3.9 to 69.2 mK
  over eight holds, median 16.19, but the peak hour lands anywhere in the 24 h.
  It is a bound on day-timescale wander, not a building cycle, and Phase B must
  not model it as one.

## Option 4 is done — and it cost 14 anchors, not 2

[REFIT_PLAN.md](REFIT_PLAN.md) **§6.3** is the write-up;
`analysis/curate.py --propose --plant` reproduces every number in it.

`steps.plant_clock` interpolates τ(T) from the archive's own 37 τ anchors and
`steps.long_enough` asks `MIN_REACH × τ(T)` of any dwell whose pole cannot be
believed. **149 → 136 anchors; 37 τ anchors unchanged**, which is forced rather
than lucky — a `tau` grade is the negation of the condition the guard tests, so
the set building the clock is disjoint from the set tested against it, and
`archive_dwells` raises if that ever stops being true.

The three things worth a reviewer's attention:

- **§6.2 predicted two verdict changes because it scored only the ceiling
  pins.** Twelve of the fourteen come from the `amp_sigma` branch, where the
  wall clock was standing in for the plant clock just as badly. All eleven are
  128.8–180.5 K and ran under three of the plant's time constants.
- **One anchor is recovered** — `rec-20260824-171059`, 57 s at 4.75 K, which is
  11 τ and which the 60 s clock had refused. Same failure as the sixteen rungs,
  and the reason to read this as the right test rather than a stricter one.
- **The margin has a gap from 0.86 to 2.41** over the 97 windows judged, so no
  verdict is balanced on the interpolant. Four rows land under 2× and each has
  a manifest note, including the rejoinder's named `pp-20260808-155602` at 0.63.

Fit effect, Λ12/C4/drift-3: `rms_k` 0.1999 → 0.2025, `anchor_k` 1.5674 →
1.5426, `mass_g` 4.7903 → 4.7877. Both runs hit `nfev = 300 = max_nfev`, so
both are upper bounds. The 14 dropped anchors had a median residual of 1.476 K
against 0.229 K for those kept — but two of them fitted to under 0.13 K, and
that is the honest shape: **the guard removes anchors that cannot be shown to
have settled, not anchors shown to be wrong.**

## Phase B: steps 1–3, 5 and 6 are done

[REFIT_PLAN.md](REFIT_PLAN.md) §7 has each one. Two commits, `ac69d7e` and
`b38ecbe`. **Nothing has regenerated the shipped table** —
`ltspm3/_fitted_table.py` still carries its `SUPERSEDED_NOTE`.

- **Steps 1–3, the refactors, proved inert two ways.** `Record` and `Anchors`
  types, `production_inputs()`, `FitSpec`, per-record integration, `N_eff`.
  Bit-identical on the production path with a forced refit, and on the
  full-grid and tier2 paths against the pre-refactor `fit_ode.py` pulled out of
  git and run side by side.
- **Step 5, `analysis/drift.py`.** **The campaign drift is a power, not a
  temperature.** K/day spans 5.6× across three independent output bands;
  mW/day agrees to ±25 %. Median **+0.281 mW/day** against §2.3's independent
  +0.27 and the fit's own 4.60 mW per-era offset. Coverage says **affine** —
  three linspaced knots fail because days 0–27.7 hold 12 anchors and no cold
  clump.
- **Step 6, the seed, and it found more than it was looking for.**
  `fit_ode.py --seed-only` is a model-free reading — Λ from `Λ(T_s) − Λ(T_c) =
  Q`, C from `τ·Λ′`, no ODE anywhere — and it agrees with the fit to a few
  percent. **But the seed was choosing which local minimum the fit landed in:**
  run to convergence, the power-law seed reaches cost 1163.0 in 1157
  evaluations and the measured seed 994.6 in 562. **14.5 % better on the
  objective — a different answer, not a tolerance.** Every production number
  this repository has quoted comes from the worse basin. `MAX_NFEV` 300 → 1500;
  300 was a truncation, not a budget.

- **Λ's level is a gauge and is not searched, after two attempts that were.**
  The second was defensible and still wrong: measured, a zero gauge reaches the
  same optimum in **149** evaluations against 566, and the value the search
  returned at 9 knots (2.41 W, railed at 3× the span) lands the fit in the
  *worse* basin — undoing step 6 entirely. Deleted. The table is in
  `measured_lambda`'s docstring; do not reintroduce a search over a null
  direction without re-running it.

The other thing to look at rather than take on trust is the choice to take C's
*shape* from the τ anchors rather than from Debye — a single Debye magnitude
put τ(137 K) at 804 s where the anchors there say 607.

## The plots changed one conclusion — look before trusting §6b

`python analysis/plot_review.py` writes four PNGs into `analysis/`, one per
decision. `review-3-basins.png` is the one that mattered: the two optima step
6b calls "different" are **the same curve above ~7 K**. Max difference in
dΛ/dT is 0.5–0.8 % over 25–192 K and **928 % over 4.7–7 K**, and 42 % of the
cost gain comes from the 140 sweep samples below 10 K — where the measured-seed
conductance carries a spike the anchors do not show.

So the measured seed is worth having for **convergence and reproducibility**
(149 evaluations against 1157), and the two optima are the same model wherever
the model is used. Whether the winner is *right* below 10 K is open, and
`T_lo = 4.6536 K` sits below the coldest sample **and** the coldest anchor —
the lowest Λ knot is in a region with no data of either kind. **Settle that
before regenerating the shipped table.**

## Then, in order

1. **Mask the 09-10 fault window** once the sample is flat (the note above).
2. **Phase B step 7**, and it needs two things built first:
   - **A `trace` row for the post-recalibration record.** There is none. The
     manifest has `trace-sweep-20260902` and `trace-ladder-20260905` and
     nothing covering 2026-09-04 23:38 → 09-09 18:06, which is where the three
     holds are. A `trace` row is **the human's** under "curate, do not
     discover" — a dwell finder cannot know which stretch is one experiment —
     so propose the boundaries, do not just write them.
   - **Per-record drift blocks.** `fit(n_drift=...)` currently **raises** with
     more than one record, on purpose: one block of time knots cannot serve two
     clocks, and trap T2 says the answer is two terms with separate priors
     because the within-record wander (−3.8 mK/h, cooling) and the campaign
     ramp (+7 mK/h, warming) have opposite signs. Step 7 wants the first;
     step 8 adds the second.

   Then T4 (drop anchors inside a fitted record's span, or the three holds are
   counted twice) and T5 (`RECORD_SHARE` explicit, or 106 h of sitting still
   outvotes the 43 h sweep 2.6 : 1).
3. Steps 8–10: campaign drift on, `ANCHOR_SIGMA_K` down in the same commit
   (T3), **leave-one-epoch-out is the gate and the plan says do not proceed if
   it fails**, then the aux channels, then regenerate and clear the note.
3. **Before November:** the naive-timestamp fold. `segments.read_table` now
   *refuses* a non-monotonic clock and names the row, so 2026-11-01 02:00 is
   loud instead of silently selecting wrong rows through `searchsorted`. The
   source fix — take `t_s` from the recorder's own monotonic `Time` column in
   `lschart/tools/fit_table.py` — is still to do.
4. `AUDIT-2026-09-10.md` finding 5's leftovers.

**Not this**: `ltspm3/control/` is complete and out of scope (§9), and
`CLAUDE.md`'s standing priority is the viewer, the MATLAB interface and Windows
deployment — none of which this thread touched.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
```

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
```

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/curate.py --propose
```

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/measure.py --holds --verify
```

`measure.py` takes about 15 s and needs no arguments — it reads the archive and
the committed manifest. `analysis/steps.py` **has no `__main__` any more**; it
is the finder, the pole and the bars, and `measured.csv` superseded
`steps.csv`.

## Traps a new session should know

- **`analysis/measured.csv` is what every fit reads.** `analysis/steps.csv` is
  gone and is not regenerated. `fit_ode.load_rows()` is the single loader.
- **Both graders now put the no-believable-pole guard on the plant's τ**, and
  they got there separately: `ltspm3/tools/sweep.py` reads `Tread.tau_pred_s`
  off the plan, `analysis/steps.py` interpolates `steps.plant_clock` from the
  archive's own 37 τ anchors. They diverged on this one test between the
  rejoinder's step 1 and step 2, deliberately, because `analysis/` had no plant
  model and invariant 1 forbids it importing one — it does not forbid it
  *measuring* one. `MIN_SPAN_S` survives in both as a labelled fallback.
  `tests_ltspm3/test_sweep_tool.py` pins the reconciliation by name.
- **`fit_ode` is still on its own error model.** `load_anchors` computes
  `hypot(ANCHOR_SIGMA_K[era], max(0.3, 2·|settle_K|))`; switching it onto
  Phase A's `sigma_T_inf` is Phase B steps 6–8, deliberately after the review.
- **The manifest is the dataset.** A change to the finder or its constants
  arrives as a `curate.py --propose` diff somebody reads. Do not let a
  heuristic rediscover it per run.
- **A long bash heredoc fails in this environment** (roughly 100 lines and up,
  `unexpected EOF looking for matching quote`, even when the body is balanced).
  Write the script to a file and run it.
