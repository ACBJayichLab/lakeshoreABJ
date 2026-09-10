# Rejoinder to [AUDIT-2026-09-10-REPLY](AUDIT-2026-09-10-REPLY.md)

Written 2026-09-10 for the session carrying Phase A forward. It says what the
audit concedes, where the reply stops short, and the order to do things in.
Everything the reply measured was re-derived on `1f5943b` before this was
written: 912 passing, ruff clean, `curate.py --propose` no diff at 149 anchors
and 37 taus, zero stray journals after a full suite run.

| | |
|---|---|
| reply §1, the noise-pole clause | **conceded** — the clause was wrong, and it fires hardest on the best anchors |
| reply §2, the ceiling refusal | **conceded** — wrong sign on five of seven; level + drift is the right model for a hold |
| reply §4, reach against the plant's tau | **agreed, and it is finding 1** — but it is scoped to `analysis/` and the live tool is where the failure costs data |
| one margin in §4 | **overstated** — the 99.42 K pin is decided by a factor of 1.5, not three orders of magnitude |

## What the audit concedes

**The noise-pole clause was the audit's error.** A finished dwell is flat for
the same reason a barely started slow one is, so no threshold on amplitude can
separate them, and a threshold that fires on small amplitudes preferentially
discards the good windows. The flat-dwell test demonstrates it. Withdrawn.

**The blanket ceiling refusal had the wrong sign on five of seven rows.** The
reply's remedy is better than the audit's: the two long holds the audit called
"the campaign drift read as a relaxation" were exactly that, and the answer is
to fit level plus drift rather than to discard sixteen hours of data. Phase A
does this and quotes the level at the window's midpoint with the drift beside
it, which is the right convention for Phase B's drift ramp and must be honoured
there (trap T3).

**The millisecond-boundary fix (`4d4c538`) was a genuine catch the audit
missed.** A manifest round-trip that silently drops one sample from 308 of 312
windows is precisely the class of defect the curate design exists to surface,
and it did.

## Where the reply stops short

The reply's option 4 — recompute `reach` against the plant's tau instead of the
fitted one — is the correct fix and it is finding 1's prescription. The reply
scopes it to `analysis/` and defers it to the manifest pause. Both are right
for `analysis/`. Neither applies to the live sweep tool, which is where the
failure actually costs data on the day, and which has no pause protecting it.

- `ltspm3/tools/sweep.py`'s `settled()` still carries the 60 s wall clock
  (line 362 at `1f5943b`); `PoleFit.tau_ceiling` is reported in the journal
  and never acted on; and [commissioning](docs/ltspm3/commissioning.md) still
  says "Use whatever `--min-dwell` the plan calls for". Measured in the audit:
  at `--min-dwell 60` a 145 K rung with 0.76 K still to go is certified
  `steady` one run in ten, 0.69 K short.
- **The sweep tool is not window-alone.** The reply's thesis — no test computed
  from the window can recover span / tau_plant because the plant's tau is not
  in the window — holds for `steps.py`. In the sweep the plan is in the room:
  `Tread.tau_pred_s` and `dwell_pred_s` per rung, `link.tau_fast_s` on the
  link. Invariant 1 is about `lschart`; `ltspm3` already imports the plant
  model. The plant-clock guard can go in today with no manifest change.
- **One margin is overstated.** The reply claims "three orders of magnitude of
  daylight on either side" of `MIN_REACH = 3.0`. True for six of the seven
  pins (18 to 6,771 above; 0.38 below). The 99.42 K pin scores **1.93**, a
  factor of 1.5 under the bar. So the tau(T) interpolant built from
  `measured.csv` matters at 99 K, and its uncertainty has to be on that row
  before the verdict is trusted. The 40–120 K validation to better than 10 %
  is enough if it holds at 99 K specifically; check it there rather than
  assume it.

## Steps, in order

1. **Sweep tool first.** In `settled()`, replace the wall-clock guard with
   `span_s >= MIN_REACH × tread.tau_pred_s` when the plan carries a prediction,
   and make a ceiling pin fail `settled()` unless the same test passes. Keep
   60 s as the fallback for a plan without predictions and **label it a proxy
   in the code**, which is the reply's own advice for any wall clock that
   survives. Turn the audit's synthetic case into a test: 145 K, tau 600 s,
   0.76 K to go, sensor noise, `--min-dwell 60`, assert zero certifications
   before 3 tau. Fix the commissioning sentence in the same commit; until then
   it should say 120 s. No manifest change, no pause needed.
2. **Option 4 in `analysis/`, at the Phase A pause.** Build tau(T) from the 37
   tau anchors, record the interpolant and its uncertainty per row, re-propose.
   Expect two verdict changes; write a manifest note on the 99.42 K one saying
   it was decided by a 55 % margin.
3. **Then Phase B**, which the reply correctly says does not depend on 2.
4. **Finding 4 before November.** The naive-timestamp fold on 2026-11-01 is
   dated and untouched. A monotonic assertion in `segments.read_table` is free
   and makes it loud; taking `t_s` from the recorder's `Time` column in
   `fit_table` fixes it at the source.
5. Finding 5's leftovers whenever.

## Not in dispute

The floor fix (`e3a87c4`), the journal fix (`48b71b0`), the single anchor
loader (`load_rows`) and the measurement stage as a separate pass from the fit
are all right and should not be reopened. The framing both documents share —
**the grader believes a pole the fitter could not actually fit** — is the
sentence to keep.
