# Handoff — 2026-09-12 (PID phase 0 done, phase 1 §1.1's prerequisites, REFIT step 7)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
refit's own state is [REFIT_PLAN.md](REFIT_PLAN.md) and the route to a working
loop is [PID_PLAN.md](PID_PLAN.md). This goes stale.

Previous: [HANDOFF-2026-09-10.md](HANDOFF-2026-09-10.md).

> ## THE HEATER IS ON, AND THE 09-10 FAULT IS OVER — BUT RESEATED IS NOT REPAIRED
>
> The recorder is running `config-ltspm3-heater.yaml`. The 218's analog output
> has been at **64.0100 %** since 2026-09-10 14:49 and the sample has been flat
> at **118.3 K** since 09-11 00:00. Nothing will move it on its own — invariant
> 6 — so move it deliberately or leave it.
>
> **What happened on 09-10, from the log and from Jeff.** The sample fell from
> 118.47 K starting 11:30, reaching 112.80 K at 14:40:43. Jeff **reseated a
> connector** at about 14:39–14:41 — the sample dips a further 1.8 K in 90 s as
> it is disturbed and **turns around at 14:41, six minutes before the first
> heater command**. The commands at 14:47:55–14:49:03 (64.031 → 65.008 →
> 64.010) were deliberate, modulating the output to watch the response.
>
> **It is a reseat, not a repair** (Jeff, 2026-09-12). The repair is changing
> the op-amp driver to a robust, correct differential design; until then the
> fault mode is present and `DELTA_P_FRAC = 0.007` stays a systematic on the
> whole campaign. **Two independent signatures say the reseat left a deficit**:
> at matched output the sample sits **0.30 K colder** than before the fault,
> and it carries **twice the wander over 300–1200 s** (16.4 mK against 9.5 at
> τ = 600 s) while the coldplate is unchanged at 0.24 mK.

## What this session did

Nine commits, `6ff126b..` on `worktree-bridge-cse_01BPYYTRMXQabkWHg5hU3gTo`.
925 passing, `ruff` clean, `curate.py --propose` reports no diff.

| | |
|---|---|
| `6ff126b` | **trap T10** — the anchors get a power-side error bar |
| `dd62f1a` | **step 6c** — the below-10 K basin, settled: the knots stay |
| `3583ac4` | the manifest gets `trace-postcal-20260905` |
| `0eb3381` | **traps T4 and T5** — `RECORD_SHARE` explicit, 36 anchors counted twice |
| `982714b` | **step 7** — per-record drift, and the two-record measurement |
| `b46779f` | **phase 0 items 1–2** — the archive past the fault, and its mask |
| `32a1583` | **phase 0 item 4** — four documents, and `analysis/allan.py` |
| `d6dbe5d` | **phase 0 item 3** — the `note` command |

**PID_PLAN phase 0 is done bar one live proof**, and **phase 1 §1.1's two
"settle first" items are done**. REFIT_PLAN Phase B is at **step 8**.

## The five things worth a reviewer's attention

**1. The anchors' error bar has two halves and they combine in watts.**
`DELTA_P_FRAC = 0.007`, in quadrature with `ANCHOR_SIGMA_K × Λ′`. It binds over
40–120 K (1.7–2.6× the kelvin bar) and does nothing below 20 K. `rms_k`
0.1499 → **0.1295** for 0.7 % on `anchor_k`, and quadrupling the bar moves the
77 K steady state by 0.10 K — the answer does not turn on the size of the one
fault that measured it. REFIT_PLAN T10.

**2. Nothing identifies the conductance below about 7 K, and more freedom there
is worse.** Four bottom-knot placements spread dΛ/dT **10× at 4.55 K** and
1.02× at 7 K; one *extra* knot below the data costs **83 % of the objective**;
dropping all four zero-output anchors moves the curve in the sixth figure. The
knots stay. What the question turned up is that **Λ is evaluated at the
coldplate**, 2.7 % below the bottom knot, which `knot_range` never looked at —
now bounded by `KNOT_EXTRAP_TOL`. REFIT_PLAN 6c.

**3. 36 anchors were in the objective twice, not the 17 trap T4 estimated.**
Dropping them moves Q(T) by at most **0.070 K** from 5 to 192 K and `rms_k` not
at all — and the fit that dropped them predicts them anyway, median miss well
under 0.1 K.

**4. The post-recal trajectory recovers 57 % and the model needs step 8.**
Adding `trace-postcal-20260905` as a second record takes the three holds from
+3.44 K mean low to **+1.47 K**, against a 0.3 K target. Three things then say
the campaign ramp is the missing piece: the two records **cannot both be
satisfied** (the sweep's own residual goes 0.1295 → 0.3262 K), the per-record
wander term **rails trying to be the era offset** (+2.01/+2.15/+2.42 mW against
a 2 mW prior), and `group_w` moves with it. §7.1.

**5. The stability figure had never been measured.** `commissioning.md` quoted
a 1/√N prediction: 6.1 / 4.1 / 2.5 mK at 4 / 60 / 600 s. `analysis/allan.py`
measures it, validated exactly against white noise, a linear drift and a sine.
Open loop at 118 K, 26.3 h:

| τ | 4 s | 10 s | 60 s | 130 s | 600 s | 1 h | 6.6 h |
|---|---|---|---|---|---|---|---|
| σ_y, mK | 7.79 | 8.73 | 7.95 | **7.38** | 9.52 | 12.72 | 24.49 |
| edf | 23666 | 9466 | 1577 | 727 | 157 | 25 | 3 |

**Averaging stops helping at about two minutes.** The prediction was optimistic
by nearly 4× at 600 s, because a model with no drift term goes on promising
improvement through the region where this cryostat has stopped improving.
PID_PLAN §1's criterion is **not met open loop, by 2.9×** — which is the point
of it.

## Two things deliberately not done

**`send note "x"` has not been proved on the live recorder.** That is phase 0's
last exit-gate item. The recorder has owned the GPIB board continuously since
2026-09-08 and is running the code as it was before the command existed, so the
gate is met at the **next restart** — a deliberate act, not a test step.

**REFIT_PLAN §1's target table has not been re-derived.** Its "where it stands
today" column reads 2.36 / 2.65 / 2.81 K for the three post-recal holds; the
shipped table measures **4.06 / 4.21 / 4.25**, which agrees with §2.1 and
`SUPERSEDED_NOTE` and is not `T_pole`. 2.5 K is 60 % of 4.17, so that column is
almost certainly §2.2's *first refit*. **§1 is the definition of done, so it has
to be fixed before it is used as one.**

## Then, in order

1. **REFIT step 8** — campaign drift on, `ANCHOR_SIGMA_K` down in the same
   commit (T3), `groups=`/`anchor_groups` retired. §7.1 is the evidence it is
   needed; `load_trace_record(F.POSTCAL)` is the one call for the second
   record. **Leave-one-epoch-out is the gate and the plan says do not proceed
   if it fails.**
2. Steps 9 and 10: aux channels against the fitted drift, then migrate the five
   callers, regenerate `_fitted_table.py`, clear `SUPERSEDED_NOTE`. T8 says
   `test_fitted_response.py`'s pins are **regenerated, not loosened**.
3. PID_PLAN phase 1 §1.2 — the band constants (`DELTA_P_FRAC` is defined and in
   the cache key; `SIGMA_C_FRAC` and the rest are not) and the two residual
   functions. **`missing_power_w` should be written against
   `pc-20260908-154814` vs `pc-20260910-144849`** — the matched-output pair
   either side of the fault, which is the one measurement of what the reseat
   left. The by-hand arithmetic in that row's note is flagged as not the answer.
4. Phase 1 §1.3 — the 300 K pipeline, dry-run on the 180 K top rung. The ladder
   itself waits: Jeff says much later, rely on extrapolation until then, so the
   table's `T_MAX_K` does not move and the monitor's no-opinion region stays.

## Traps this session added to the list

- **The cache key does not contain the knot range.** Any study that moves
  `T_lo` must give each variant its own `CACHE_DIR`, or it gets another
  variant's answer back in zero seconds — which does not look like an error, it
  looks like agreement.
- **Moving `T_lo` re-places all 20 geomspaced knots**, so a four-way comparison
  confounds the cold end with the 10–20 K knots landing elsewhere — 11 of 36
  cost units in the case measured. Pass explicit knots to isolate it.
- **Check an archive extension is additive before trusting it.** Re-run the
  original glob and compare byte for byte first. The manifest's boundaries are
  timestamps, so a table whose earlier rows shifted would move every window
  under them silently.
- **Heredocs on stdin mangle UTF-8** in this environment: `—` and `→` arrive as
  `?` and a patch script fails its own assertion. Write the script to a file.
- **`analysis/measured.csv` and `analysis/.fit_cache/` do not exist on a fresh
  clone or in a new worktree.** `measure.py` takes 11 s; the production fit then
  takes about 50 s.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/measure.py
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/curate.py --propose
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/allan.py
```
