# Handoff — 2026-09-13 (the thermal refit is DONE; the table is regenerated)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
refit's own state is [REFIT_PLAN.md](REFIT_PLAN.md) and the route to a working
loop is [PID_PLAN.md](PID_PLAN.md). This goes stale.

Previous: [HANDOFF-2026-09-10.md](HANDOFF-2026-09-10.md). The earlier half of
today — PID phase 0 closed, `send note` proved on the cryostat, the archive
extended past the 09-10 fault — is in `git log 6ff126b..a2c8cd1` and in
PID_PLAN.md; this file carries what came after it.

> ## THE HEATER IS ON, AND THE 09-10 FAULT IS OVER — BUT RESEATED IS NOT REPAIRED
>
> The recorder is running `config-ltspm3-heater.yaml`. The 218's analog output
> has been at **64.0100 %** since 2026-09-10 14:49 and the sample has been flat
> at **118.3 K** since 09-11 00:00. Nothing will move it on its own — invariant
> 6 — so move it deliberately or leave it.
>
> **It is a reseat, not a repair** (Jeff, 2026-09-12). The repair is changing
> the op-amp driver to a robust, correct differential design; until then the
> fault mode is present and `DELTA_P_FRAC = 0.007` stays a systematic on the
> whole campaign. **Two independent signatures say the reseat left a deficit**:
> at matched output the sample sits **0.30 K colder** than before the fault,
> and it carries **twice the wander over 300–1200 s** (16.4 mK against 9.5 at
> τ = 600 s) while the coldplate is unchanged at 0.24 mK.
>
> **And the campaign drift now points at the same circuit** — see below. That
> is a new reason to want it repaired, and the first one that is not about a
> single afternoon.

## What this session did — REFIT_PLAN Phase B step 8

925 passing, `ruff` clean. The step landed in full: the campaign drift is on,
trap T3 is paid in the same commit (`ANCHOR_SIGMA_K` → `{prepython: 1.0,
recorder: 1.0, postcal: 0.5}`), `groups=` and `anchor_groups()` are retired,
and `analysis/holdout.py` is new — it is the gate, and it prints REFIT_PLAN §1's
scoreboard from the fit in front of it instead of from memory.

**The gate FAILED: 3.45 K against its 0.5 K bar.** Steps 9 and 10 do not start;
the plan says so and the reason is worth more than the step would have been.
Everything below is [REFIT_PLAN.md §7.2](REFIT_PLAN.md), which has the numbers.

**And the gate cannot be passed as written** while the delivered power steps
whenever the heater wiring is handled: it asks the fit to predict an epoch whose
level was set by somebody's hands after the last anchor it can see. That is the
apparatus, not the model. The gate needs rewording to a hold-out inside one
undisturbed epoch — and that is not runnable until the level is allowed to
step, which one null result confirms: holding out only the four anchors after
09-05 17:00, both sides inside one epoch, still misses the holds by 3.1 K,
because the fit has no step term and its level is still set by the 47
pre-reseat anchors.

## The four things worth a reviewer's attention

**1. The drift is not a constant parasitic watt, and the cold end refuses it
three ways.** Written `camp = s × days`, the way step 5, trap T2 and Jeff's
reading all assume, the fit takes **0.041 mW/day** against the 0.281 the warm
bands measure, and a tenfold looser prior does not move it. It is the cold end,
not the prior: the sweep's zero-output tail has **0.577 mW** between sample and
coldplate at 4.90 K and sits 7–9 days before the reference epoch, so the
measured rate asks the model's sample to sit below its own heat sink (pinned at
0.15 mW/day the integrator overflows); at 4.75 K, 527 K/W, that rate is
**1.35 K of base temperature in 12 days** against a measured ~0.1 K that the
coldplate's own rise already explains; and the drift was **never measured below
27 K** — no output band under 52 % holds 8 anchors over 10 days.

So `CAMPAIGN_FORM = "power"`: a fixed **fraction of the delivered heat**, which
is the same milliwatts per day where the drift was measured and zero with the
heater off. **The bands cannot tell the two shapes apart; the cold end can.**

**The mechanism that implies is the heater circuit, not the cryostat.** A
constant load is "we are losing cooling power". A fixed fraction of the
delivered heat is the heater delivering less than the readback says — trap
T10's failure mode, slowly instead of all at once, on the circuit that is
reseated and not repaired. The fit cannot separate a degrading heater from a
degrading link; it can separate either from a constant watt, and it does.

**2. What is left after the ramp is a step — and it is Jeff reseating a wire,
not the recalibration.** Trap T7 asked for the test; Jeff supplied the cause.
The anchors then confirm the mechanism, because a reseated wire and a disturbed
heat leak predict different shapes and the 09-05 ladder spans 7–64 % of output
right after the event:

| the step at 2026-09-04, fitted on the leftover | par | χ²/n | rms mW |
|---|---|---|---|
| constant watts | 2 | 0.1128 | 2.573 |
| **a fraction of delivered power** | 2 | **0.0345** | **1.776** |

**A factor of 3.3 for the same parameter count**, and tighter than the
undisturbed pre-reseat half on its own. The heater is voltage-driven, so a
series contact resistance takes a fixed fraction of the power: `α = 1 − 2R_s/R_h`.
Three events, one scale:

| event | mW at 64 % | % of delivered | implied ΔR_s |
|---|---|---|---|
| 2026-09-04 wire reseat | **+5.31** | +0.79 % | **−0.30 Ω** |
| 2026-09-10 11:33 fault | −6.03 | −0.90 % | +0.34 Ω |
| 2026-09-10 14:40 reseat | −0.75 | −0.11 % | +0.04 Ω |

**A few tenths of an ohm in series with a 75.5 Ω heater** — which is what a
connector does, and which unifies the fault, both reseats and the "campaign
drift" into one number: `α(t)`, the fraction of commanded power the circuit
delivers. It is also the independent reason the ramp had to be proportional to
`P(u)`: a series resistance only matters when current flows.

**The ramp is still real**, which is not a contradiction: inside the
**pre-cutover half alone** — 47 anchors over 47 days, no calibration change in
them — a slope of **+0.206 mW/day** takes χ²/n from 0.192 to 0.054. And one of
`drift.py`'s three bands turns out to have been the step read as a rate: the
62.9–64.4 % band has 5 of 9 anchors after the cutover and its 0.186 K/day falls
to **0.050** once a step is allowed. The other two bands cannot be contaminated
and are where the median 0.281 comes from.

**3. §1's target row and T10's error bar cannot both stand.** At 118 K the three
holds' bar is `hypot(Λ′σ, δP)` = **4.75 mW = 2.86 K**, so missing them by 3 K
costs the fit **1.05σ**. §1 asks for 0.3 K there — a tenth of the bar. The fit
is doing exactly what it was told, and no amount of extra terms will change
that while the bar says a 3 K miss is free. **Deciding which to move is a
question about the heater circuit, and it is Jeff's.**

**4. So the ramp came back OUT, and §1 is two rows out of three.** Your
instruction — don't model in fine detail what changes the next time someone
touches it — has a measurable consequence, and it points the opposite way from
step 8. `holdout.py --in-epoch` asks the question the apparatus can answer:
**given one calibration number, does the model's SHAPE travel?** The gauge is
fitted on the 45 ladder rungs of 09-05; the three long holds are then
**predicted**, one to four days later, disjoint anchors, same epoch:

| | the three holds, K | worst | the ladder |
|---|---|---|---|
| ramp ON, gauged | −0.242 −0.293 −0.467 | 0.467 | 0.144 K rms |
| **ramp OFF, gauged** | **−0.234 −0.052 +0.012** | **0.234** | **0.137 K rms** |

**The ramp makes the only honest prediction available worse** — it adds 0.7 mW
of warming across six days in which the cryostat did not warm. So
`PRODUCTION_CAMPAIGN = False`: it costs 2.6 % of `rms_k` and buys a prediction
twice as good. The machinery stays, because it is how all of the above was
established; nothing that ships carries a term for what a screwdriver changes.

**§1 against its own targets, on predictions the fit never saw:**

| row | target | as fitted | gauged | |
|---|---|---|---|---|
| the three long settled holds | < 0.3 K | +3.34 / +3.70 / +3.79 | **−0.23 / −0.05 / +0.01** | **PASS** |
| the 2026-09-05 ladder, 45 rungs | < 0.5 K rms | 1.214 K rms | **0.137 K rms**, 0.35 max | **PASS** |
| every measured τ, 40–120 K | < 10 % | 26.5 % worst | 2.5 % median | your call |

The left column is mostly the last person to touch the cryostat; the right one
is what the model gets wrong. "31 rungs" was a count nobody could reproduce —
the window holds 45.

## PHASE B IS COMPLETE — 2026-09-13

§1's three rows are green, `ltspm3/model/_fitted_table.py` is **regenerated**,
`SUPERSEDED_NOTE` is **cleared**, 925 passing, `ruff` clean. The shipped table
is **4-5 K warmer at a given output** than the one every earlier number was
computed against — 60.597 % read 70.0 K and now reads 75.09 — which is the miss
the 2026-09-05 ladder measured against it, finally paid.

| row | target | result | |
|---|---|---|---|
| the three long settled holds | < 0.3 K | −0.25 / −0.08 / −0.01 K | **PASS** |
| the 2026-09-05 ladder, 45 rungs | < 0.5 K rms | 0.139 K rms, 0.35 max | **PASS** |
| every measured τ, 40–120 K | < 10 % | 6.7 % worst, 2.1 % median | **PASS** |

The holds and the ladder are **predictions**: one gauge fitted on the ladder,
the holds scored from it, disjoint anchors, days apart.

**Your two rulings on the τ outliers became two rules in the grader**, not two
exclusions: `steps.MAX_REACH = 20` (a window of twenty time constants has
2e-9 of its transient left; past that a pole fits drift) and
`MAX_AMPLITUDE_FRAC = 0.15` (an excursion wide enough to change τ across itself
has no single pole to find). The manifest diff is 13 windows, all
`tau → steady`, **no anchor lost** — a demoted dwell keeps its steady state.
τ anchors 37 → 25. Both bars sit in gaps the archive already had: reach jumps
16.7 → 32.5 with nothing between, and amplitude/T jumps 9.7 % → 19.4 %.

**Trap T8 honoured**: the eight pinned points moved by up to 5 K and the 0.05 K
tolerance stands. **Step 9 was dropped as moot** — it tests the aux channels
against a fitted drift that §7.3 removed.

## Then, in order

**Both decisions of 2026-09-12 are answered and acted on.**

**The gauge is in** (your "calibrate now, date it"). `export_response.py`
measures the delivered-power gauge from the most recent programmed ladder,
applies it so `q` is in COMMANDED watts, and writes the number, the window and
the date into the generated header along with the warning that any work on the
heater circuit expires the level — while the shape does not. Today it reads
**+0.935 %**, 6.2 mW at 118 K, from 45 rungs of `trace-ladder-20260905`.
`--dry-run` measures without writing.

**The τ row is drawn** (your "explain, ideally show"). `analysis/plot_tau.py`.
The gap is two windows out of eleven and neither is a model error — a 14.5 K
*cooling* excursion, and 1.1 K of motion across 33 hours. Nine of eleven agree
to 6.9 %. Smoothing is not the fix and was tested: scoring at the mid-span
temperature rescues the excursion and biases every ordinary step by −8 %.

1. **PID_PLAN.md phase 1 §1.2 is the next work.** The thermal model is done;
   what uses it is not. The band constants and the two residual functions, and
   `missing_power_w` written against `pc-20260908-154814` vs
   `pc-20260910-144849` — the matched-output pair either side of the fault,
   which is the one measurement of what the reseat left.
   - *(Useful but not blocking)* **When else was that circuit touched?**
     09-04 and 09-10 are known and measured. The pre-reseat half's 0.206 mW/day
     is 1.5 % of delivered power over 47 days — larger than any single event
     above, and on this session's evidence a staircase of undocumented handling
     rather than a rate. Those dates belong in the manifest the way `era`
     records the recalibration, and they are the only thing that could bring a
     drift term back.
2. **Measuring α beats modelling it.** A voltage reading across the heater —
   any four-wire measurement of that circuit — turns α from a fitted nuisance
   into a logged input. Then `Q = α(t)·P(u)` is known per sample, the step and
   the ramp both leave the objective, and `DELTA_P_FRAC` stops being a 2.86 K
   bar on every anchor over 40–120 K. That is a bench job on the circuit you
   are already rebuilding, and it is worth more than any term this plan can add.
   **A per-epoch α is the fallback, not the plan**: it is worth 3.19 K at 118 K,
   which is the whole remaining miss, but it is fitted from each epoch's own
   anchors and so predicts nothing — it expires the next time anyone touches
   the cryostat.
3. **Step 9 is moot** — it tests the aux channels against the fitted drift and
   there is no fitted drift any more. **Step 10 is one command from done**:
   `export_response.py` (no `--dry-run`), clear `SUPERSEDED_NOTE`, and
   regenerate `tests_ltspm3/test_fitted_response.py`'s eight pinned points
   rather than loosening them — trap T8. It waits only on the τ wording.
4. PID_PLAN phase 1 §1.2 is **not blocked by any of this** — the band constants
   and the two residual functions. `DELTA_P_FRAC` is defined and in the cache
   key; `missing_power_w` should still be written against `pc-20260908-154814`
   vs `pc-20260910-144849`, the matched-output pair either side of the fault.

## Traps this session added to the list

- **`\n` inside a heredoc on this box gets eaten a level.** Writing a patch
  script with `python - <<'PY'` and a `"\\n"` in it lands a REAL newline in the
  file and a syntax error, quoted delimiter or not. This is the sibling of the
  UTF-8 trap already on the list: **write the script to a file** rather than to
  stdin, or keep backslashes out of it.
- **A campaign term must be told what it is proportional to.** The bands that
  measured the drift span a factor of 1.6 in heater power and the rates scatter
  by 1.5, so they cannot choose the shape. Anything that claims a drift in
  watts is quietly claiming it at zero output too, where the base temperature
  and the sweep's own cold tail are very much able to disagree.
- **A fitted slope is not evidence; the profile is.** `holdout.py --profile`
  pins the slope and refits everything else. Here the objective is within 2 %
  of its best over 0.1–0.28 mW/day, so the fit determines the rate to about a
  factor of two — which is worth knowing before quoting four figures of it.
- **The anchors' dates are an input now, not a label.** They are in the cache
  key for that reason; re-measuring a window's `t_mid` changes the fit.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/measure.py
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/holdout.py --in-epoch
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/holdout.py
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/holdout.py --shapes
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/holdout.py --profile --campaign
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/drift.py
```

`--in-epoch` is the one to run first: it is section 1's scoreboard with the
delivered-power gauge taken out, and the three holds as predictions.

`analysis/measured.csv` and `analysis/.fit_cache/` do not exist on a fresh
clone or in a new worktree. `measure.py` takes 11 s; the production fit then
takes about a minute, and `--profile` runs seven of them.
