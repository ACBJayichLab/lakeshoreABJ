# Handoff — 2026-09-12 (REFIT step 8: the ramp is in, the gate failed, and it found two things)

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

**2. What is left after the ramp is a step at the recalibration — trap T7,
measured.** On the leftover anchor residual, weighted by each anchor's own bar:
a step at 2026-09-04 12:07 gives χ²/n **0.1128** against **0.1371** for another
day-slope, one parameter each, and it is **+3.03 mW**. Given the step, no slope
is left (0.016 mW/day). T7 predicted exactly this absorption.

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

**4. §1's scoreboard is generated now, and all three rows fail.** The "where it
stands today" column had been quoted from three different fits over the life of
the plan; `python analysis/holdout.py` prints it:

| row | target | measured | |
|---|---|---|---|
| the three long settled holds | < 0.3 K | **+3.04 / +3.15 / +3.01 K** | FAIL |
| the 2026-09-05 ladder, 45 rungs | < 0.5 K rms | **1.107 K rms**, 3.13 max | FAIL |
| every measured τ, 40–120 K | < 10 % | **26.4 % worst**, 2.7 % median | FAIL |

"31 rungs" was a count nobody could reproduce — the window holds 45. And the τ
row needs a decision: the median is 2.7 % and §2.4 is right that the *shape*
travels, but a single dwell's τ scatters 433–850 s near 137 K, so "every
measured τ" may not be reachable by a one-pole model at all.

With the post-recal record added as a second trajectory (§7.1's fit B) the
holds come to **+1.66 / +1.12 / +0.93** and the ramp lands on **+0.287 mW/day**
against `drift.py`'s independently measured 0.281 — the best evidence in the
session that the ramp is real. But that record's own wander knots then rail at
**+1.35 to +2.10 mW** against a 2 mW prior: the same step, absorbed by the only
parameter in reach.

## Then, in order

1. **Two questions for Jeff before any more fitting.** They decide what the
   next term even is, and neither is a modelling choice:
   - **What is the +3 mW step at 2026-09-04?** The candidates want different
     fixes: an imperfect Coldplate remap (belongs in the data, not the
     objective — and arithmetically strained: T_c sits where Λ′ is 2–9 mW/K, so
     3 mW needs the remap to be 0.3–1.5 K wrong); something physical in that
     window; or the drift's mechanism changing. **The postcal era is not
     homogeneous** — it contains the 09-09 transient and the 09-10 fault.
   - **Is `DELTA_P_FRAC = 0.007` the ordinary margin, or the worst case?** If
     the ordinary margin is much smaller, the bar comes down and §1's 0.3 K
     becomes reachable; if it is right, §1's target is asking for a tenth of
     the uncertainty and should say so instead.
2. **Then trap T7's named step**, fitted beside the ramp, and re-run
   `analysis/holdout.py`. Worth about 1.8 K at the holds on the one-record fit.
   Only after 1: if the step is the remap, it belongs in `recalibrate.py`.
3. **Steps 9 and 10 stay shut.** Nothing regenerates `_fitted_table.py` while
   the gate is red; `SUPERSEDED_NOTE` stays.
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
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/holdout.py
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/holdout.py --profile
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/holdout.py --shapes
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/holdout.py --shapes --no-campaign
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/drift.py
```

`analysis/measured.csv` and `analysis/.fit_cache/` do not exist on a fresh
clone or in a new worktree. `measure.py` takes 11 s; the production fit then
takes about a minute, and `--profile` runs seven of them.
