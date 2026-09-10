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
term in it. So:

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

Nine commits, `39dd52a..c58b264`, **merged to `main`**. 914 passing, ruff
clean, `curate.py --propose` reports no diff, `measure.py --verify` meets all
four exit criteria.

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

1. **Mask the 09-10 fault window** once the sample is flat (the note above).
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
