# Handoff — 2026-09-12 (PID phase 1: both prerequisites, and step 7)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
refit's own state is [REFIT_PLAN.md](REFIT_PLAN.md) and the route to a working
loop is [PID_PLAN.md](PID_PLAN.md). This goes stale.

Previous: [HANDOFF-2026-09-10.md](HANDOFF-2026-09-10.md).

> ## THE HEATER IS ON, AND HAS BEEN FOR A WEEK
>
> Unchanged from 09-10 and carried forward deliberately. The recorder is
> running `config-ltspm3-heater.yaml` and the 218's analog output has been held
> at **64.016 %** since 2026-09-08 15:48. Nothing will move it on its own —
> invariant 6, availability outranks tidiness, and cutting this heater is a
> change of state rather than a retreat to safety. Move it deliberately, or
> leave it.
>
> **The 2026-09-10 11:33 heater-circuit fault is still open.** The sample fell
> 3.63 K at an unmoved readback; Jeff traced it to wiring. From 11:33 until the
> circuit is repaired and verified, `Q` is unknown and **nothing in that stretch
> is an anchor**. It is not masked yet — PID_PLAN.md phase 0 item 1 — because
> the archive has not been re-exported past 2026-09-09 23:59:59.

## What this session did

Five commits, `6ff126b..982714b`, on the `worktree-bridge-cse_…` branch. 915
passing, `ruff` clean, `curate.py --propose` reports no diff.

| | |
|---|---|
| `6ff126b` | **trap T10** — the anchors get a power-side error bar |
| `dd62f1a` | **step 6c** — the below-10 K basin, settled: the knots stay |
| `3583ac4` | the manifest gets `trace-postcal-20260905` |
| `0eb3381` | **traps T4 and T5** — `RECORD_SHARE` explicit, 36 anchors were counted twice |
| `982714b` | **step 7** — per-record drift, and the two-record measurement |

That is PID_PLAN.md **phase 1 §1.1's two "settle first" items** and
**REFIT_PLAN.md Phase B step 7**, complete. Next is step 8.

## The four things worth a reviewer's attention

**1. The anchors' error bar has two halves and they combine in watts.**
`DELTA_P_FRAC = 0.007`, the one measured size of the heater-circuit fault
(5 mW at 0.67 W), in quadrature with `ANCHOR_SIGMA_K × Λ′`. It binds over
40–120 K, where it is 1.7–2.6× the kelvin bar, and does nothing below 20 K —
which is the whole reason it cannot be one number in kelvin. The trajectory
comes back **13.6 % better** (`rms_k` 0.1499 → 0.1295) for 0.7 % on `anchor_k`,
and quadrupling the bar to 2.8 % moves the 77 K steady state by 0.10 K, so the
answer does not turn on the size of the single fault that measured it.
REFIT_PLAN.md T10 has the tables.

**2. Nothing identifies the conductance below about 7 K, and more freedom there
is worse.** Four bottom-knot placements spread dΛ/dT by **10× at 4.55 K** and
1.02× at 7 K. The sweep's 140 cold samples are 0.3 % of the weight, the
roughness prior reaches no lower than 5.14 K, and the four zero-output anchors
are missed by less than their own bar — *dropping all four moves the curve in
the sixth significant figure*. One **extra** knot below the data costs 83 % of
the objective. The knots stay. What the question turned up is that **Λ is
evaluated at the coldplate**, 2.7 % below the bottom knot, which `knot_range`
had never looked at; `KNOT_EXTRAP_TOL` now bounds that. REFIT_PLAN.md 6c.

**3. 36 anchors were in the objective twice, not the 17 trap T4 estimated.**
The record is the whole 43 h sweep window, so every dwell inside it was there
sample by sample *and* as an anchor. Dropping them moves Q(T) by at most
**0.070 K** anywhere from 5 to 192 K and `rms_k` not at all — and the fit that
dropped them predicts them anyway, median miss well under 0.1 K. Read that as
the evidence they carried nothing, not as a dataset being thinned.

**4. The post-recal trajectory recovers 57 % of the miss and the model needs
step 8.** Adding `trace-postcal-20260905` as a second record takes the three
holds from **+3.44 K mean low to +1.47 K**, against a 0.3 K target — 65 % of
the shipped table's +4.17. Three things then say the campaign ramp is the
missing piece:

- the two records **cannot both be satisfied** — the sweep's own residual goes
  0.1295 → 0.3262 K while the post-recal record fits at 0.0953 K;
- the per-record wander term **rails trying to be the era offset**: +2.01,
  +2.15, +2.42 mW against a 2 mW prior, a near-constant offset rather than a
  wander, which is exactly what T2 and T3 warn about;
- `group_w` moves with it, −4.40 → −6.15 mW.

## One discrepancy found and deliberately not fixed

**REFIT_PLAN.md §1's "where it stands today" column is not the shipped model.**
It reads 2.36 / 2.65 / 2.81 K for the three post-recal holds; the shipped table
measures **4.06 / 4.21 / 4.25**, which agrees with §2.1's "4.4 K low" and with
`SUPERSEDED_NOTE`. It is not `T_pole` — that is within 0.06 K of `T_inf` on all
three. 2.5 K is 60 % of 4.17, so the column is almost certainly §2.2's *first
refit*. **§1 is the definition of done, so that row has to be re-derived before
it is used as one.** Invariant 9.

## Then, in order

1. **Step 8** — campaign drift on, `ANCHOR_SIGMA_K` down in the same commit
   (T3), `groups=`/`anchor_groups` retired. §7.1 is the evidence it is needed
   and `load_trace_record(F.POSTCAL)` is the one call for the second record.
   **Leave-one-epoch-out is the gate and the plan says do not proceed if it
   fails.**
2. Steps 9 and 10: the aux channels against the fitted drift, then migrate the
   five callers, regenerate `_fitted_table.py`, clear `SUPERSEDED_NOTE`. T8
   says `tests_ltspm3/test_fitted_response.py`'s pins are **regenerated, not
   loosened**.
3. Then PID_PLAN phase 1 §1.2 — export the band constants (`DELTA_P_FRAC` is
   already defined and in the cache key, `SIGMA_C_FRAC` and the rest are not)
   and the two residual functions, `missing_power_w` and `sigma_q_w`.
4. Phase 1 §1.3 — the 300 K pipeline, dry-run on the existing 180 K top rung.
5. PID_PLAN phase 0 is still untouched: the 09-10 mask, the repair as a
   manifest row, the `note` command kind, and four stale documents.

## Traps this session added to the list

- **The cache key does not contain the knot range.** It contains the data, the
  knot *counts* and `FIT_CACHE_VERSION`. Any study that moves `T_lo` must give
  each variant its own `CACHE_DIR` or it will get another variant's answer back
  in zero seconds, which does not look like an error, it looks like agreement.
- **Moving `T_lo` re-places all 20 geomspaced knots**, so a four-way comparison
  of bottom-knot placements confounds the cold end with the 10–20 K knots
  landing elsewhere — 11 of 36 cost units, in the case measured. Isolate the
  bottom knot by passing explicit knots if the question is about the cold end.
- **`analysis/measured.csv` and `analysis/.fit_cache/` do not exist on a fresh
  clone or in a new worktree.** `python analysis/measure.py` takes 11 s and
  needs no arguments; the production fit then takes about 50 s.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/measure.py
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/curate.py --propose
```
