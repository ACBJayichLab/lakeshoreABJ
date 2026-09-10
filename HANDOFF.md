# Handoff — 2026-09-10 (Phase A of the refit, and two audits answered)

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
> As of 2026-09-10 14:25 the sample reads **114.83 K**, coldplate 6.628 K.

## READ THIS FIRST: the sample fell 3.6 K this morning at a fixed heater

**A second event of the kind [REFIT_PLAN.md](REFIT_PLAN.md) §2.5 describes, ten
times larger, and it is still going.** Onset **2026-09-10 11:33**, output
unmoved at 64.016 % throughout, the last digits being readback flicker.

One-minute means below, because 2nd Stage carries tens of mK of hash and a
single sample of it says whatever you like:

| channel | 11:32 | 12:47 | 14:24 | change |
|---|---|---|---|---|
| **Sample** | 118.457 | 115.163 | 114.827 | **−3.630 K** |
| Coldplate | 6.645 | 6.629 | 6.627 | −18 mK |
| 1st Stage | 28.668 | 28.608 | 28.679 | +12 mK |
| 2nd Stage | 3.961 | 3.954 | 3.954 | −7 mK |
| RAD SHIELD | 40.701 | 40.524 | 40.463 | −238 mK |
| `ls218.aout1` | 64.0153 | 64.0154 | 64.0150 | **−0.0003 %** |

Most of the fall happened in forty minutes (118.46 → 115.28 by 12:12) and the
tail is still running at about **−220 mK/h**. Compare the 2026-09-09 18:06
transient, which is `mask-20260909-180604` in the manifest: sample −0.306 K,
coldplate −7.6 mK. **Same signature, an order of magnitude bigger.**

Why it matters: propagated through the fitted Λ′ ratio, an 18 mK coldplate step
accounts for at most ~0.17 K of the sample's 3.63 K. So about 3.5 K of this is
invisible to `Λ(T_s) − Λ(T_c)`, which is the *entire* steady-state model. This
is §2.5's load path, no longer a 0.3 K curiosity.

Note the sign: the sample is getting **colder** at constant power, i.e. *more*
cooling. That is the opposite sign to the campaign drift Phase B is built to
absorb (+0.167 K/day, warming), so it is a second phenomenon and not more of
the first — trap T2's warning, arriving again from a new direction.

**What to do about it, in order:**

1. **Leave the heater alone** and let it flatten. Nothing here is a hazard: the
   stage is cooling, the output is unchanged, `status.json` reports
   `control: null` (no software PID armed).
2. **Once flat, export a fresh archive window covering 11:33 onward** and give
   it a `mask` row with a paragraph, exactly as `mask-20260909-180604` has.
   `reference/cooldown-10/README.md` has the commands. **This is the "cheap
   now, impossible later" case the plan already learned once** (§0.4) — but do
   not archive a half-event, which is why it is not done in this session.
3. Ask Jeff. Two of these in two days at a fixed output is a fact about the
   cryostat, not about the fit, and it may be a vacuum or shield change worth
   knowing about before more model effort goes in.

> **The data is only on this machine.** `data/` is gitignored, so the event
> lives in `data/ltspm3-heater_2026-09-10.csv` and nowhere else. A fresh clone
> does not have it.

## What this session did

Nine commits, `39dd52a..c58b264`, **merged to `main`**. 914 passing, ruff
clean, `curate.py --propose` reports no diff, `measure.py --verify` meets all
four exit criteria.

| | |
|---|---|
| `4d4c538` | manifest boundaries to the **millisecond**. A second-truncated `t_end` lands before the sample it came from, so slicing a window back out returned one row fewer than the grader saw — 308 of 312 windows, worth up to **0.50 K** of `T_inf` and 83 % of a τ |
| `48b71b0` | the rehearsal test gets a `--journal`, so it stops dropping a CSV in the working directory every run |
| `311c204` | **Phase A** — `analysis/measure.py`, `analysis/measured.csv` |
| `e3a87c4` | a τ at the search **floor** is no longer graded `tau`. 8 verdict changes, `load_taus` 45 → 37 |
| `1296f70` | `AUDIT-2026-09-10-REPLY.md` — the two parts of the audit's finding 2 that were not applied, and why |
| `595d87a` | (Jeff) the rejoinder: both conceded, and the live sweep tool is where the fix still had to land |
| `c58b264` | the sweep tool's guard moved onto the **plant's** τ |

## Where the refit stands

**Phase A is done and awaiting a human review.** That review is the pause, and
it is the point of the phase: §6.1 of the plan is the write-up, and
`analysis/measure.py --holds --verify` reproduces every number in it.

The three things worth a reviewer's attention:

- **A single pole was the wrong model for a hold**, and it had eight graded
  anchors wrong — four past a kelvin, worst `rec-20260828-141631` at
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

## The one open decision

**`analysis/`'s ceiling-pin policy** — [REFIT_PLAN.md](REFIT_PLAN.md) §6.2
option 4, and step 2 of [the rejoinder](AUDIT-2026-09-10-REJOINDER.md).

Recomputing `reach` against the plant's τ instead of the fitted one separates
all seven graded ceiling pins perfectly against the existing `MIN_REACH = 3.0`.
It is already live in `ltspm3/tools/sweep.py`. In `analysis/` it needs τ(T)
built from `measured.csv`'s own 37 τ anchors — which is not circular, because
no ceiling pin can be a τ anchor — and it costs one round of iteration.

Expect **two verdict changes**, and one of them needs a manifest note:
`pp-20260808-155602` is decided by a margin of **1.6 in τ**, not the three
orders of magnitude the other six enjoy. Checked at 99 K rather than assumed:
445.9 s interpolated against the shipped table's 436.4 s, 2.2 % apart, reach
1.88 and 1.92, and τ would have to be 37 % low to flip it.

It changes which windows are anchors — which is the manifest — so it belongs
after the review, not before.

## Then, in order

1. **The live event above**, once it is flat.
2. Option 4 in `analysis/`, at the pause.
3. **Phase B** (§7). It does not depend on 2: its τ residuals read
   `load_taus`, which the floor fix already cleaned, and no ceiling pin is
   graded `tau`. Read traps T1–T9 before starting, and §6.1's last paragraph —
   **the fit will move at step 1, and that is not a refactor failing to be
   inert**, because `settle_K` for a hold now means the drift across half the
   window rather than a pole's extrapolation.
4. **Before November:** the naive-timestamp fold. `segments.read_table` now
   *refuses* a non-monotonic clock and names the row, so 2026-11-01 02:00 is
   loud instead of silently selecting wrong rows through `searchsorted`. The
   source fix — take `t_s` from the recorder's own monotonic `Time` column in
   `lschart/tools/fit_table.py` — is still to do.
5. `AUDIT-2026-09-10.md` finding 5's leftovers.

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
- **The two graders diverge on one test, on purpose.** `ltspm3/tools/sweep.py`
  puts the no-believable-pole guard on the plant's τ; `analysis/steps.py` keeps
  the wall clock because invariant 1 forbids it a plant model, and refuses to
  believe a pinned pole instead. Both say so at `MIN_SPAN_S`. Do not reconcile
  them.
- **`fit_ode` is still on its own error model.** `load_anchors` computes
  `hypot(ANCHOR_SIGMA_K[era], max(0.3, 2·|settle_K|))`; switching it onto
  Phase A's `sigma_T_inf` is Phase B steps 6–8, deliberately after the review.
- **The manifest is the dataset.** A change to the finder or its constants
  arrives as a `curate.py --propose` diff somebody reads. Do not let a
  heuristic rediscover it per run.
- **A long bash heredoc fails in this environment** (roughly 100 lines and up,
  `unexpected EOF looking for matching quote`, even when the body is balanced).
  Write the script to a file and run it.
