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

## READ THIS FIRST: the sample fell 3.6 K this morning at a fixed heater — and Jeff was in the lab

**Onset 2026-09-10 11:33, output unmoved at 64.016 % throughout.** Jeff has
since said the time coincides with his own actions at the cryostat, and his
working hypothesis is that **laser heating of the sample was turned off**. The
record supports that reading, and it changes what this event means for the
refit — see below. It is a hypothesis until Jeff's lab timeline confirms it.

One-minute means, because 2nd Stage carries tens of mK of hash:

| channel | 11:32 | 12:47 | 14:24 | change |
|---|---|---|---|---|
| **Sample** | 118.457 | 115.163 | 114.827 | **−3.630 K** |
| Coldplate | 6.645 | 6.629 | 6.627 | −18 mK |
| 1st Stage | 28.668 | 28.608 | 28.679 | +12 mK |
| 2nd Stage | 3.961 | 3.954 | 3.954 | −7 mK |
| RAD SHIELD | 40.701 | 40.524 | 40.463 | −238 mK |
| `ls218.aout1` | 64.0153 | 64.0154 | 64.0150 | −0.0003 % |

**What the shape says.** A single pole fitted to the first 90 minutes after
onset gives an amplitude of **+3.10 K and τ = 614 s**, rms 33 mK — which is the
plant's own time constant at this temperature (534 ± 6 s measured over 70 h at
114 K, 4 % low at reach 4.7 the way `fit_pole` always is). So the prompt part is
a **step change in heat load at the sample**, relaxing exactly as the fitted
model says the sample relaxes. Behind it is a slow tail, about −0.2 K/h from
12:30 onward, tracking RAD SHIELD as it cools by 0.24 K over the same hours.

**What the size says.** The two September holds bracket this output and give the
local gain directly: (118.609 − 114.390 K) / (0.6688 − 0.6622 W) = **639 K/W**;
the shipped model says 636 at the same point. So the prompt 3.10 K step is
**4.9 mW** removed from the sample, and the full 3.63 K to 14:24 is 5.7 mW.
That is milliwatt-class optical power absorbed at a diamond sample and its
mount — exactly the size an excitation laser delivers — and the sign is what a
removed load looks like: sample colder, coldplate slightly colder, shield
slowly colder as the scattered light and the sample's own radiation drop.

**It is NOT the same signature as 2026-09-09 18:06** (`mask-20260909-180604`),
and the previous draft of this handoff was wrong to say so. On 09-09 the
**cold-head channels stepped** — 1st Stage −80 mK, 2nd Stage −19 mK, coldplate
−7.6 mK — and the sample followed slowly, τ ≈ 2,200–3,700 s from the pole, which
is not the plant's τ; the sample's 0.34 K is 0.5 mW equivalent. Today the cold
head did not move (+12 / −7 mK, noise) and the sample stepped at the plant's
own τ. One is a change at the cold head; the other is a change at the sample.
Whether Jeff was also in the lab at 18:06 on 09-09 is worth asking, because a
0.5 mW-equivalent change could be a smaller optical event, a shutter, or a
room-light change — but it is a separate question.

### Why this matters more to the refit than the event itself

The refit is chasing three discrepancies, and every one of them is the size of
a milliwatt-class load that was not logged:

| discrepancy | size | equivalent at 639 K/W |
|---|---|---|
| three settled holds vs the shipped model (§2.1) | +4.35 / +4.42 / +4.44 K | ~7 mW |
| July–August vs September at matched output (§2.3, "campaign drift") | 1.8–2.9 K warmer | **4.8–5.4 mW** — the fit already measures this as a per-era power step |
| today's step | 3.63 K | 5.7 mW |

**If a laser was on during some archive windows and off during others, the
manifest currently has no column that knows it, and a fit that does not know it
will absorb a binary external input into Λ(T), C(T) or a drift ramp** — which is
trap T2 and trap T6 at once, and the most plausible single explanation on the
table for why a physically sensible model is 4.4 K off three holds that agree
with each other to a tenth. Note in particular that the 43 h sweep the ODE is
fitted to ended 09-04 11:00 and the ladder that found the model 4.5 K low began
09-05 11:45; the recorder was down 09-04 12:07 → 23:38 between them, so a
change in laser state in that gap would be invisible in the log and would look
exactly like "the model is low everywhere above 40 K".

**What to do about it, in order:**

1. **Leave the heater alone** and let it flatten. Nothing here is a hazard: the
   stage is cooling, the output is unchanged, `status.json` reports
   `control: null`.
2. **Get the laser timeline from Jeff for the whole cooldown** — on/off times
   and approximate power at the sample — and record it as a **state column in
   the manifest** (or an era split), the way `era` records the recalibration.
   Then tag every anchor. Until this is done, do not start Phase B: it would
   fit a switch as physics.
3. **Once the sample is flat, archive 11:33 onward.** The transient itself is a
   `mask` with a paragraph, as `mask-20260909-180604` has. But the settled
   stretch after it is **not** a mask — it is a legitimate hold at 64.016 % in
   the laser-off state, and it is the first anchor whose load state is known
   for certain. Tag it as such. `reference/cooldown-10/README.md` has the
   commands. Do not archive a half-event (§0.4).
4. **Log lab actions into the recorder.** The CSV has a `Notes` column and it is
   empty across both events; nothing in the lab can write to it. A `note`
   command kind through the spool — text only, no gate needed — would have made
   both of these attributable on the day rather than a day later. Small, and it
   belongs in `lschart`, which is the standing priority anyway.
5. If the laser goes back on, the sample should step back up by the same ~3 K
   at the plant's τ. That is a free, decisive test of the hypothesis and it
   costs nothing but noting the time.

> **The data is only on this machine.** `data/` is gitignored, so the event
> lives in `data/ltspm3-heater_2026-09-10.csv` and nowhere else. A fresh clone
> does not have it.

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

1. **The laser timeline, then the live event above once it is flat** — steps 2
   and 3 of READ THIS FIRST. Phase B waits on the first.
2. Option 4 in `analysis/`, at the pause.
3. **Phase B** (§7), only after the laser state is a column. It does not depend on 2: its τ residuals read
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
