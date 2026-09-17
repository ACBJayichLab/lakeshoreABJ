"""Fit the tier-1 ODE to the 43 h sweep, anchored on the settled holds.

    C(T) dT/dt = Q(u) - [ Lambda(T) - Lambda(T_c(t)) ]

Both u(t) and T_c(t) are driven from the log, so the only unknowns are the two
curves.  Both are parameterised the same way -- a monotone cubic through knots
in (log T, log y), with the knot values forced increasing because
Lambda' = k*A/L > 0 and because nothing in copper, sapphire or diamond gives a
falling heat capacity between 5 K and 190 K.  The number of knots in each is
the complexity knob, and they are turned one at a time: a joint grid confounds
the two and hides the fact that they are not equally constrained.

The settled holds enter as extra residuals rather than as hard constraints.
They deserve a margin: u=63.072% was held three times and landed 2.84 K apart.
So an anchor is worth about a kelvin, and the fit is free to miss it by that
much.  On top of that every anchor carries a POWER-side bar, ``DELTA_P_FRAC``
-- the heater circuit does not always deliver what the readback says it does --
and the two are added in quadrature in watts, because a kelvin is worth five
times as much power at 30 K as it is at 140 K.

``prepython`` is NOT a different cooldown -- cooldown 10 started 2026-07-15 and
is still running.  It is the pre-Python chart-recorder half of this one, and
the cryostat has drifted across it: at seven outputs where the two halves
overlap the sample sits 1.8-2.9 K WARMER in September than it did in July and
August, same sign every time.  That used to be fitted as one free power offset
between two named halves of the campaign; since REFIT_PLAN.md Phase B step 8 it
is a RAMP IN DATE -- one slope in watts per day, ``campaign=True`` -- because
the anchors span 55 days continuously and a step function of them is a model of
the filenames rather than of the cryostat.  The ramp is zero at the reference
epoch, the latest moment any input describes, so the curve itself still
describes the cryostat as it is NOW: the state the simulator and the loop have
to match.

Integration is exponential Euler: T += g*tau*(1 - exp(-dt/tau)) with
tau = C/Lambda'.  It is exact for the linearised relaxation and unconditionally
stable, which matters because C falls steeply at the cold end and an explicit
step would go unstable there long before the interesting physics did.

**Converged fits are cached** under ``analysis/.fit_cache/``, keyed on a digest
of the sweep, the anchors, the taus and the knot counts.  Four scripts here fit
the same (9, 4) model and each one used to spend a quarter of an hour
rediscovering it; now the ladder pays for all of them.  The key contains the
data, so remapping ``T_c`` invalidates every entry on its own -- but it cannot
see a change to the objective in THIS file, which is what FIT_CACHE_VERSION is
for.  Bump it, or delete the directory.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from bisect import bisect_right
from dataclasses import dataclass, replace

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares

import segments as _seg
from _data import REPO_ROOT as _REPO_ROOT
from _data import open_table

R_OHM, V_FS, GAIN = 75.5, 10.0, 1.11

#: The trajectory this fits, as a MANIFEST WINDOW rather than a file: the 43 h
#: hand-walked sweep, 2026-09-02 16:01 -> 09-04 11:00, 4.9-192.6 K.
#:
#: It was ``region_20260903-123832_complete_sweep_even_larger.csv``, a region
#: export, and the window has the same bounds to the second and the same rows.
#: Two things are better for it being a window.  The export had been rounded
#: before the Coldplate remap and its ``T_c`` is 1.2 mK rms off the log's own
#: (41 mK at worst, where it had written 6.4000); and a window can be *widened*
#: -- the same hold whose approach the export cut off 3.3 h into is what made
#: the opening anchor read 180.07 K instead of 180.563 K.
SWEEP = "trace-sweep-20260902"
ANCHORS = "analysis/measured.csv"

#: Bump when anything about the parameterisation or the objective changes.
#: It is part of the cache key, so bumping it invalidates every stored fit
#: at once -- which is the point: the input digest catches changed DATA and
#: cannot possibly catch changed CODE.
#:
#: 4: REFIT_PLAN.md Phase B step 1.  The objective is unchanged and was proved
#: so -- see :func:`production_inputs` -- but the refactor is exactly the kind
#: of change the input digest cannot see, which is what this number is for.
#:
#: 5: the seed changed -- step 6, and then the gauge search was deleted.  THE
#: SEED IS NOT IN THE KEY beyond the ``seed_measured`` flag, so changing how it
#: is computed leaves entries that are reachable and wrong.  It caught me out
#: once: after deleting the gauge search, two comparison rows came back in zero
#: seconds carrying the answer the old seed had found.  A stale hit does not
#: look like an error, it looks like a fast run.
#:
#: 6: the anchors' error bar gained its power-side term, ``DELTA_P_FRAC`` --
#: REFIT_PLAN.md trap T10.  A denominator changed, so every stored fit is an
#: answer to a different question.
#:
#: 7: step 7.  ``trim_anchors`` (trap T4) drops 36 anchors from the production
#: fit and ``record_scale`` (trap T5) names the between-record weighting, and
#: then the drift knots became one block PER RECORD.  The first two show up in
#: the input digest on their own; the third does not, because with one record it
#: is inert and there is nothing in the data to see.  That is precisely what
#: this number is for -- and bumping it is also how the inertness gets PROVED,
#: by forcing the refit and comparing rather than by reading a cached answer
#: back and calling it agreement.
#:
#: 8: step 8.  The per-era power offset is gone and a campaign RAMP is in its
#: place, ``ANCHOR_SIGMA_K`` came down with it (trap T3), and both change the
#: objective for every preset -- including the ones that pass neither, because
#: the anchors' error bars moved underneath them.
FIT_CACHE_VERSION = 8

#: Margin on a settled point, in kelvin, added in quadrature to twice its own
#: extrapolation distance.  Keyed on the ERA the anchor came from
#: (``segments.ERAS``).
#:
#: This used to be keyed on the input FILENAME, tested with
#: ``source.startswith("fit_cd10")``.  Every era now has its own entry and a
#: missing one raises, because the failure mode of the old test was silent: an
#: input rename made every anchor ``recent``, the per-era offset fitted nothing,
#: and the curve came out about a kelvin wrong for both halves with no symptom.
#:
#: **``prepython`` was 3.0 K and is 1.0 -- REFIT_PLAN.md trap T3, paid in the
#: same commit that turned the campaign ramp on.**  That 3 K was never a
#: measurement of anything about a July anchor; it was the campaign drift,
#: forgiven once, at the only place the model had to put it.  With ``campaign``
#: fitting the drift explicitly, leaving it up would have priced the same
#: residual twice -- the ramp would be free to explain a disagreement the bar
#: had already excused, and would come back under-determined for it.
#:
#: ``postcal`` gets **0.5**: it is the only era measured end to end by
#: ``measure.py`` on the corrected Coldplate curve, with each hold's own drift
#: fitted rather than smeared into its level.  It is a tightening, and T10 is
#: the reason it is a safe one -- over 40-120 K, where these anchors are,
#: ``DELTA_P_FRAC`` already dominates the bar by 2-3x, so halving the kelvin
#: half moves the total by a few percent and no anchor ends up claiming more
#: precision than the heater circuit can deliver.
ANCHOR_SIGMA_K = {"prepython": 1.0, "recorder": 1.0, "postcal": 0.5}
ANCHOR_FLOOR_K = 0.3

#: Fractional uncertainty on the watts the heater circuit actually DELIVERS, as
#: against the watts ``P(u)`` computes from the 218's readback.
#:
#: REFIT_PLAN.md trap T10.  On 2026-09-10 11:33 the sample fell 3.63 K with the
#: readback unmoved at 64.016 %, and Jeff traced it to a wiring / heater-circuit
#: fault.  Every fit here takes ``Q = P(u)`` on trust, so that fault is a
#: SYSTEMATIC on the watts-to-kelvin curve for the whole campaign rather than a
#: defect in one window, and it stays until a more robust circuit replaces this
#: one.  Its one measured size is about 5 mW at 0.67 W -- 0.7 %.  How much
#: smaller the ordinary margin is, the log cannot say, so that is the bar: it is
#: the only number there is, and it is a BAR rather than a correction because
#: the sign on any given day is unknown.
#:
#: IT ENTERS IN POWER, NOT IN KELVIN, which is the whole reason for carrying it
#: apart from ``ANCHOR_SIGMA_K``.  The anchor residual is already a power --
#: ``dQ``, in watts -- divided by ``Lambda'`` so that it reads as kelvin, and
#: the local gain runs 119 K/W at 30 K to 634 K/W at 140 K (analysis/drift.py).
#: A single kelvin bar is therefore five times the wrong size at one end or the
#: other, while ``dP`` is the same 0.7 % of delivered power everywhere.  Added
#: in quadrature to the kelvin bar's own power equivalent, ``Lambda' * sigma``,
#: so that neither can be tightened past the other -- which is exactly what T10
#: forbids.
#:
#: PROPORTIONAL TO Q, WITH NO FLOOR.  A circuit that fails to deliver part of
#: what it is asked for has nothing to fail to deliver at zero output, so the
#: base-temperature anchors are held by their kelvin bar alone.
#:
#: Phase 1 of PID_PLAN.md exports this number beside the table as one term of
#: the typical band; it is defined here because this is where it is first used.
DELTA_P_FRAC = 0.007

#: How far below the lowest Lambda knot the objective may evaluate Lambda, as a
#: fraction of that knot's temperature.
#:
#: The residual is ``Lambda(T_s) - Lambda(T_c)`` everywhere -- the anchors, the
#: integrator's RHS, the exported Q -- so the curve is evaluated at the
#: COLDPLATE as well as at the sample, and :func:`knot_range` has only ever
#: looked at the sample.  Today the coldest ``T_c`` is 4.5299 K (8 anchors and
#: 64 of the sweep's samples are under the knot) against a bottom knot at
#: 4.6536, so the bottom **2.7 % in T** is a log-log linear continuation of the
#: curve rather than a piece of it.
#:
#: That is deliberate, and :func:`knot_range` has the measurement: putting a
#: knot down there is measurably worse and nothing identifies the region either
#: way.  It is also not a free parameter -- the continuation's slope is PCHIP's
#: end slope, fixed by the two lowest knot values, which the roughness prior
#: does reach.  A bounded extrapolation of a penalised curve is a different
#: thing from an unconstrained one.
#:
#: What it is NOT is safe to leave unwatched.  A later record with a colder
#: coldplate -- a better cold head, a changed radiation shield -- widens this
#: silently and at some width the continuation stops meaning anything.  5 % is
#: room for roughly double today's gap and no more.
KNOT_EXTRAP_TOL = 0.05

#: Measured time constants (analysis/steps.py) enter as residuals in log tau.
#: They are what turns C from a fit parameter into a measurement: with Lambda
#: known, C = tau * dLambda/dT.  The margin is generous because a relaxation
#: fit's tau scatters -- 433 to 850 s across the dwells near 137 K -- and
#: because one pole is a simplification of a body with internal gradients.
TAU_SIGMA_FACTOR = 1.5
TAU_SHARE = 0.10
#: Fractional margin on a sweep sample.  Residuals are fitted in log T so that
#: 5 K counts as much as 187 K; an absolute-K objective would ignore the whole
#: cold end, which is the half with no settled anchor in it.
SWEEP_SIGMA_REL = 0.01
#: The anchors together carry this share of the sweep's weight.
ANCHOR_SHARE = 0.10

#: What decides one record's weight AGAINST THE OTHERS -- as distinct from how
#: the samples inside one record are weighted against each other, which is
#: ``Record.w`` and the decimator's business.  REFIT_PLAN.md trap T5.
#:
#: ``"equal"``    every record carries the same total, whatever its length.
#: ``"samples"``  each carries its own sample count -- what the code did before
#:                anybody chose, and what T5 warns about.
#:
#: **It has to be explicit because duration is not evidence.** The post-recal
#: record is 96.5 h of which 96 h is two settled holds; the sweep is 43 h of
#: 4.9-192.6 K in continuous movement. Weighted by time the first outvotes the
#: second 2.6 : 1 on the strength of sitting still, and a joint fit that
#: converges and means nothing is the easiest thing in this file to produce.
#:
#: ``"equal"`` is Jeff's call, 2026-09-12. It is not a claim that the two
#: records carry the same information -- it is a refusal to let LENGTH be the
#: vote, which is the specific failure T5 describes. What each record has to say
#: about Lambda still arrives through its own residuals.
#:
#: **Inert at one record, exactly**: the scale is sqrt((N_eff/1)/len(r)) = 1, so
#: the production fit's ``w_sweep`` is unchanged bit for bit and every stored
#: cache entry stays valid. It is in the key anyway, through ``w_sweep``.
RECORD_SHARE = "equal"
#: Integration step, and the cadence the residual is evaluated on.  The log's
#: own cadence is 2 s, and that is what this should be: at 25 K tau is about
#: 4 s, so a 4 s step is one time constant and the recovery ramp -- where the
#: sample slews at 4 K/s -- picks up an error that looks exactly like model
#: mismatch and is not.  Coarsening to 4 s costs 0.06 K of rms and triples the
#: worst residual, from 10.7 K to 14.4 K, all of it in one nine-minute window.
STEP_S = 2.0

#: Roughness penalty on Lambda, as a second difference of its knot values in
#: (log T, log Lambda).  Zero turns it off.
#:
#: THE PROBLEM IT SOLVES.  Knot count was doing two jobs at once and could only
#: do one of them well.  Twelve knots place the two settled holds far better
#: than nine (0.32 K against 0.45 before the drift term), because both holds
#: sit in the top decade where geomspace puts one interval -- but the same
#: twelve knots put freedom into the bottom decade too, where the sweep has two
#: settled dwells and passes through in minutes, and the curve grows wiggles
#: there that are the parameterisation talking, not the cryostat.  dLambda/dT
#: IS the physical conductance and dT/dP IS the thermal resistance, so a wiggle
#: is not cosmetic: it is a claim about the link that nothing measured.
#:
#: WHAT TO PENALISE, and the first attempt got it wrong.  Penalising the
#: curvature of log Lambda did nothing measurable: Lambda is the conductance
#: INTEGRAL, and curvature in an integral is only slope in its derivative, so a
#: prior on it barely reaches the curve anybody looks at.  What is displayed
#: and what the loop cares about is dLambda/dT -- it IS the physical
#: conductance k(T)A/L, and tau = C/(dLambda/dT).  So the penalty acts on the
#: second difference of **log(dLambda/dT)** against log T.
#:
#: Its null space is then exactly a POWER-LAW CONDUCTANCE, k ~ T^a, which costs
#: nothing; any curvature away from one costs.  That is the right prior here:
#: k(T) for a metal or a dielectric is smooth over a decade, and nothing in
#: this cryostat justifies structure the settled dwells cannot see.
#:
#: Scaled like every other prior in this file: a share of the sweep's weight
#: divided by the departure that share is worth.  The first version of this was
#: a bare multiplier and was four orders too weak -- 0.005, 0.02 and 0.08 gave
#: byte-identical fits, which is what a penalty that never enters the objective
#: looks like.  If a knob's whole range does nothing, it is not tuned, it is
#: disconnected.
#: MEASURED at 20 knots on the decimated sweep, scored on the full grid:
#:
#:   share   rms K    roughness of dLambda/dT
#:   0.00    0.1649       129.0
#:   0.02    0.1678        33.1     <- default
#:   0.05    0.1715        31.7
#:   0.20    0.1864        29.4
#:
#: 0.02 buys a FOUR TIMES smoother conductance for 1.8% of rms, and beats 12
#: unpenalised knots on both counts at once (0.2024 K, roughness 55.2).  Past
#: 0.05 the roughness has stopped falling and only the fit is getting worse,
#: which is what the knee of a regularisation path looks like.
LAMBDA_SMOOTH_SHARE = 0.02
#: Second difference of log Lambda per knot that the prior treats as free.
#: 0.30 is a factor of 1.35 of curvature between adjacent knots, which is far
#: more than a conductivity maximum needs and far less than a wiggle.
LAMBDA_SMOOTH_SIGMA = 0.30

#: A slow, unmeasured load on the sample, in watts, fitted as a few knots in
#: TIME rather than in temperature.  Off by default (``n_drift=0``).
#:
#: WHY IT IS NEEDED.  The sweep's two settled holds are missed in opposite
#: directions -- -0.39 K at 180.5 K and +0.55 K at 192.4 K -- with only 49 mK
#: of scatter inside each, so the model reproduces each hold thirty times
#: better than it places the pair.  And during the 22.8 h opening hold, at a
#: heater that never moves, the sample drifts -3.8 mK/h while every other
#: channel in the cryostat is flat to +-1.5 mK/h.  Something slow is changing
#: the steady state and it is not in the log.
#:
#: WHY IT DOES NOT SPOIL THE DYNAMICS.  With three knots over 43 h the fastest
#: this term can move is about 11 h, against tau = 572 s for the sample.  It is
#: four orders of magnitude too slow to stand in for a relaxation, so Lambda
#: and C still have to earn the transients.  That separation is the whole
#: reason it is safe to add: the steady state may drift, the dynamics are
#: universal.
#:
#: The prior keeps it at the size the holds imply.  dT/dP is about 546 K/W at
#: 180 K, so 0.4 K of hold bias is 0.7 mW; 2 mW is a generous ceiling and stops
#: the term from absorbing anything Lambda should be explaining.
DRIFT_SIGMA_W = 2.0e-3
DRIFT_SHARE = 0.05

#: The CAMPAIGN ramp's prior: how many watts per day of slow parasitic load the
#: fit may find before the objective starts objecting.  ``campaign=True``.
#:
#: REFIT_PLAN.md Phase B step 8, and it is a SECOND term rather than more knots
#: on the one above -- trap T2, measured.  The 43 h sweep's 22.8 h opening hold
#: drifts -3.8 mK/h (cooling) at a fixed heater while the campaign runs
#: +7 mK/h (warming): opposite signs, four orders of magnitude apart in
#: timescale, and one term cannot be both.  ``DRIFT_SIGMA_W`` is the wander
#: WITHIN a record, on the record's own clock; this is the trend ACROSS the
#: campaign, on the wall clock, and it is the one the anchors measure.
#:
#: **In watts per day, not kelvin per day**, which analysis/drift.py had to
#: establish before this could be written: the same underlying change reads
#: 0.033 K/day at 30 K and 0.186 K/day at 118 K, because the local gain runs
#: 2 to 14 K/%.  Divided back through dT/dP the three independent output bands
#: agree to +-25 % -- 0.281, 0.335, 0.223 mW/day, median **0.281** -- against
#: 0.27 mW/day measured a fourth way in section 2.3 and 4.6 mW over ~20 days
#: as the old per-era step.  So the drift is a POWER at the heater's own node,
#: and it enters exactly where the heater does, in the anchor residual and in
#: the ODE's right-hand side alike.
#:
#: 1 mW/day is **3.5x the measured rate**, deliberately.  The prior is here to
#: stop a nuisance term walking off with Lambda's level, not to tell the fit
#: what the answer is -- if this number decided the slope, the leave-one-epoch-
#: out gate in analysis/holdout.py would be testing the prior rather than the
#: cryostat.
CAMPAIGN_SIGMA_W_PER_DAY = 1.0e-3
CAMPAIGN_SHARE = 0.05

#: WHAT THE CAMPAIGN DRIFT IS PROPORTIONAL TO -- and this was measured, after
#: the obvious answer was tried and refuted.
#:
#: ``"watts"``   a constant parasitic load on the sample: ``camp = s * days``.
#:               Jeff's reading of the sign, and what REFIT_PLAN.md step 5 and
#:               trap T2 assume.
#: ``"power"``   a fixed FRACTION of the delivered heat going missing:
#:               ``camp = s * days * P(u) / CAMPAIGN_REF_W``.  Identical at the
#:               power the drift was measured at, and zero with the heater off.
#:
#: **The cold end refuses ``"watts"``, three ways.**  The drift was only ever
#: measured above 27 K -- analysis/drift.py needs 8 anchors over 10 days inside
#: a 1.5 % output band, and no band below 52 % output has them -- so what a
#: constant load implies at 5 K was an extrapolation nobody had checked.
#:
#:   * The sweep's own zero-output tail has **0.577 mW** between the sample and
#:     the coldplate at 4.90 K.  The record sits 6.9-8.6 days before the
#:     reference epoch, so 0.281 mW/day asks for -2.2 mW there: the model's
#:     sample would have to sit BELOW its own heat sink.  Pinned at 0.15 mW/day
#:     the integrator drives the cold tail into its 1 K clamp and overflows.
#:   * At 4.75 K the fitted local resistance is 527 K/W, so 0.281 mW/day is
#:     **1.35 K of base temperature in 12 days**.  The zero-output anchors move
#:     4.7516 K (08-24) to 4.856 and 4.926 K (09-05) -- about 0.1 K, and their
#:     COLDPLATE moved 4.530 to 4.68-4.69 K, which accounts for it on its own.
#:   * Left free, the fit takes **0.041 mW/day** -- seven times below the warm
#:     bands -- and loosening the prior tenfold does not move it.  It is not
#:     the prior refusing, it is the cold end.
#:
#: ``"power"`` survives all three because it vanishes with the heater, and it
#: is not a worse description of where the drift WAS measured: over the three
#: bands P(u) runs 0.45, 0.66 and 0.71 W, a factor of 1.6, against a measured
#: 0.281 / 0.335 / 0.223 mW/day that spans 1.5 with no trend either way.  The
#: bands cannot separate the two shapes; the cold end can, and does.
#:
#: The mechanism it implies is not the one this plan started with.  A constant
#: load is "the cryostat is losing cooling power"; a fixed fraction of the
#: delivered heat is **the heater circuit delivering less of what it is asked
#: for** -- which is the same failure mode as trap T10's, whose one measured
#: instance is 0.7 % on 2026-09-10, slowly rather than all at once.  It is
#: consistent with the circuit Jeff describes as reseated and not repaired.
#: The fit cannot tell a degrading heater from a degrading link (at steady
#: state both scale with the heat carried); it can tell either from a constant
#: parasitic watt, and it does.
CAMPAIGN_FORM = "power"

#: Whether the SHIPPED fit carries the ramp at all.  It does not, and that is a
#: measurement rather than a preference.
#:
#: Step 8 turned the ramp on because section 7.1 showed the model could not
#: satisfy two records at two dates.  Step 8 then found the cause: a wire Jeff
#: reseated on 2026-09-04 changed the delivered power by 0.79 % (REFIT_PLAN.md
#: section 7.2), which is 3.2 K at 118 K and the whole of the disagreement.
#:
#: **So the question became whether the ramp is a RATE or a staircase of
#: handling events, and the one epoch that can answer it says staircase.**
#: Gauge the model on the 45 ladder rungs of 2026-09-05 and predict the three
#: long holds one to four days later -- disjoint anchors, one undisturbed epoch,
#: analysis/holdout.py --in-epoch:
#:
#:     ramp on    -0.242  -0.293  -0.467 K      worst 0.467
#:     ramp OFF   -0.234  -0.052  +0.012 K      worst 0.234
#:
#: The ramp is adding warming across six days in which the cryostat did not
#: warm.  It costs 2.6 % of ``rms_k`` to remove and it buys a prediction twice
#: as good, so it is off.  What is left of the campaign's apparent drift is
#: handling: events at dates only a person can supply, and modelling those in
#: detail is modelling something that changes the next time anyone touches the
#: cryostat (Jeff, 2026-09-12).
#:
#: The machinery stays, and so does everything measured with it -- ``campaign=``
#: and ``campaign_w=`` are how the above was established, and if handling dates
#: ever arrive they are how the alternative gets tested.
PRODUCTION_CAMPAIGN = False

#: The power ``CAMPAIGN_SIGMA_W_PER_DAY`` and the fitted slope are quoted at,
#: so that "mW/day" means the same thing under either form and can still be
#: compared with analysis/drift.py.  0.65 W is the middle of the three bands
#: the drift was measured in (0.45, 0.66, 0.71 W) and about where the cryostat
#: is held.
CAMPAIGN_REF_W = 0.65

#: How far C(T) may depart from a Debye SHAPE, as a factor either way.
#:
#: Without this the fit sends C to zero below ~30 K, and it is right to: down
#: there tau is seconds, every dwell in the sweep is thousands of tau long, and
#: the sample tracks its steady state whatever C is.  Any small enough C fits
#: equally well, so the optimiser takes the smallest -- 1e-80 J/K, which is not
#: a measurement of anything.  The prior constrains the SHAPE only, relative to
#: C at 137 K, so the data still sets the magnitude where it can see it, and
#: nothing is imposed on the one number a heat capacity actually contributes.
CAP_SHAPE_FACTOR = 3.0
CAP_SHAPE_REF_K = 137.0
#: The shape prior together carries this share of the sweep's weight.
CAP_SHAPE_SHARE = 0.05

R_GAS = 8.314462
#: Sapphire > Cu > diamond by mass.  The split is a stand-in for a weighing,
#: which is fine: it is used for the SHAPE of C(T), and between 5 K and 190 K
#: all three are far below their Debye temperatures and their shapes are alike.
MIX = (("Al2O3", 1047.0, 5, 101.96, 0.50),
       ("Cu", 343.0, 1, 63.55, 0.35),
       ("diamond", 2230.0, 1, 12.01, 0.15))


def debye_c(T, theta, n_atom, molar_mass_g):
    """Debye heat capacity, J/(g K)."""
    T = np.atleast_1d(np.asarray(T, float))
    out = np.empty_like(T)
    for k, t in enumerate(T):
        x = np.linspace(1e-6, min(theta / t, 60.0), 400)
        f = x**4 * np.exp(x) / np.expm1(x) ** 2
        out[k] = 9 * n_atom * R_GAS * (t / theta) ** 3 * np.trapezoid(f, x)
    return out / molar_mass_g


def mix_c(T):
    return sum(w * debye_c(T, th, n, m) for _, th, n, m, w in MIX)


def power_w(u):
    return (GAIN * V_FS * np.asarray(u) / 100.0) ** 2 / R_OHM


def _f(row, key):
    try:
        return float(row[key])
    except (TypeError, ValueError, KeyError):
        return math.nan


#: The 218's own input filter as it is now configured: 4 points on a 4 Hz
#: sample, so a single pole at about 1 s.
#:
#: MEASURED, twice, and it is off by default because of what the measurements
#: say rather than out of caution.
#:
#: On the sweep's 22.8 h hold at 180.6 K the sample's sample-to-sample noise is
#: 28.1 mK; running this filter over the logged 2 s data leaves 22.4 mK.  It
#: removes a fifth of a term that is already three orders below the residual,
#: because a 1 s pole is FASTER than the 2 s cadence the log was written at and
#: there is very little left in the record for it to remove.  End to end, on
#: the (3, 4) fit: **the residual moves by 0.007 mK against an rms of 5.23 K**.
#:
#: It is not merely useless here, it is slightly harmful.  The same filter
#: displaces the trace by up to **1.48 K** during the recovery ramp, where the
#: sample slews at 4 K/s -- that is measurement lag, it looks exactly like
#: model mismatch, and the fit would spend real freedom absorbing it.  Filter
#: a fit's input to remove noise it does not have and you buy lag it did not.
#:
#: Where it does matter is the LOOP, not the fit: it puts a 1 s lag in the
#: measurement path, which is nothing against tau = 600 s at 137 K and is a
#: quarter of tau at 25 K.  That belongs in pid_tuning.py, against the plant.
HARDWARE_FILTER_TAU_S = 1.0


def _ema(x, tau_s, dt_s):
    """Single-pole low pass, dt-aware, the same form the recorder's own filters
    use: ``alpha = 1 - exp(-dt/tau)`` rather than a fixed alpha."""
    alpha = 1.0 - math.exp(-dt_s / tau_s)
    out = np.empty_like(x)
    acc = x[0]
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        out[i] = acc
    return out


def sweep_window(window=SWEEP):
    """The manifest ``Slice`` behind :func:`load_sweep`.

    Separate because ``load_sweep`` returns the fit's own relative clock and
    some callers -- ``decimate.write`` -- need the absolute one as well.
    """
    return _seg.load(window)


def load_sweep(path=SWEEP, filter_tau_s=None):
    s = sweep_window(path)
    t, T, Tc, u = s.t, s.T, s.Tc, s.u
    ok = ~(np.isnan(t) | np.isnan(T) | np.isnan(Tc) | np.isnan(u))
    t, T, Tc, u = t[ok], T[ok], Tc[ok], u[ok]
    grid = np.arange(t[0], t[-1], STEP_S)
    out = (grid, np.interp(grid, t, T), np.interp(grid, t, Tc),
           np.interp(grid, t, u))
    if filter_tau_s:
        # Thermometers only.  The heater is a commanded step, and low-passing
        # a step turns the one input the fit knows exactly into a guess.
        out = (out[0], _ema(out[1], filter_tau_s, STEP_S),
               _ema(out[2], filter_tau_s, STEP_S), out[3])
    return out


#: The adaptively thinned sweep, written by analysis/decimate.py --write.
#: Same run, same instrument numbers, 4,968 rows instead of 77,375.
DECIMATED = "sweep_decimated.csv"


def load_decimated(path=DECIMATED):
    """``((t, T, Tc, u), weights)`` from the thinned sweep.

    The weights are what make this equivalent rather than merely smaller: a
    kept sample stands for its ``span_s`` of the record, and without that the
    22.8 h hold -- thinned 149:1 -- would quietly stop being half the
    objective.  Verified against the full grid: fitting here and scoring there
    moves the (9, 4) rms from 0.4467 to 0.4504 K, 0.8%, with tau(137 K) and the
    implied mass unchanged to three figures, in 7.3 s rather than 190.
    """
    with open_table(path) as fh:
        rows = list(csv.DictReader(fh))
    arr = {k: np.array([_f(r, k) for r in rows])
           for k in ("Time", "span_s", "Sample", "Coldplate", "ls218.aout1")}
    span = arr["span_s"]
    return ((arr["Time"], arr["Sample"], arr["Coldplate"], arr["ls218.aout1"]),
            np.sqrt(span / span.mean()))


@dataclass(frozen=True)
class Record:
    """One trajectory the ODE is integrated down, with its own clock.

    A bare ``(t, T, Tc, u)`` tuple was enough while there was exactly one, and
    REFIT_PLAN.md Phase B is about there being several: the 43 h sweep, the
    post-recalibration trace, and whatever else earns a ``trace`` row in the
    manifest.  Three things the tuple could not carry are what make that
    possible.

    ``w`` -- the per-sample weight, normalised to unit mean square here rather
    than inside :func:`fit`, so that ``sum(w**2) == len(t)`` for every record
    and the prior shares stay balanced against the total the same way they were
    against one record's ``len(t)``.  A uniform grid gets ones, which normalise
    to ones, so this is inert on the existing path.

    ``t0`` -- the ABSOLUTE unix second of ``t[0]``.  ``t`` stays relative
    because the integrator wants it that way and because two records have no
    common origin, but the campaign drift of REFIT_PLAN.md section 2.3 is a
    function of wall-clock date and cannot be written without this.  It is the
    one field that exists purely for step 8.

    ``name`` -- so a residual, a cache key or a diagnostic can say which record
    it came from.  With one record that is decoration; with three it is the
    difference between a number and a number you can act on.
    """

    name: str
    t: np.ndarray
    T: np.ndarray
    Tc: np.ndarray
    u: np.ndarray
    w: np.ndarray
    t0: float = math.nan

    def __post_init__(self):
        w = np.asarray(self.w, float)
        rms = math.sqrt(float(np.mean(w ** 2)))
        object.__setattr__(self, "w", w / rms if rms > 0 else w)

    @classmethod
    def from_tuple(cls, data, weights=None, name="sweep", t0=math.nan):
        """Accept the ``(t, T, Tc, u)`` five call sites still pass."""
        if isinstance(data, Record):
            if weights is not None:
                raise SystemExit(
                    f"fit_ode: Record {data.name!r} already carries its own "
                    f"weights and a separate weights= was passed as well.  One "
                    f"of the two would be silently dropped, and it is the kind "
                    f"that only shows up as a fit that is subtly wrong -- put "
                    f"the weights on the Record.")
            return data
        t, T, Tc, u = data
        w = np.ones(len(t)) if weights is None else np.asarray(weights, float)
        return cls(name=name, t=np.asarray(t, float), T=np.asarray(T, float),
                   Tc=np.asarray(Tc, float), u=np.asarray(u, float), w=w, t0=t0)

    def __len__(self) -> int:
        return len(self.t)

    @property
    def T0(self) -> float:
        """The integrator's initial condition: this record's first sample."""
        return float(self.T[0])

    @property
    def t_abs(self) -> np.ndarray:
        return self.t0 + self.t

    def as_tuple(self):
        return self.t, self.T, self.Tc, self.u


@dataclass(frozen=True)
class Anchors:
    """Settled dwells as steady-state residuals, with the clock they were measured on.

    ``t_abs`` is the whole point of the type and it comes from ``t_mid``, the
    midpoint of the window, NOT from ``t_end`` as REFIT_PLAN.md step 1 assumed
    when it was written.  Phase A changed what a level means: a hold is fitted
    with level and drift and the level is quoted at the midpoint, because that
    is where a linear drift's error is smallest.  Anchoring the campaign ramp
    on the end of a 70 h window would put the anchor 35 h away from the
    temperature it reports.  archive/AUDIT-2026-09-10-REJOINDER.md asks for exactly
    this convention to be honoured here.

    ``era`` is the label ``ANCHOR_SIGMA_K`` is keyed on and is carried for
    reporting only -- **no residual is a function of it any more.**  Step 8
    retired the per-era power offset: two named halves of one continuous
    campaign were a step function fitted to a slope, and ``t_abs`` is what the
    slope is fitted on now.  Keeping the label costs nothing and is what lets a
    diagnostic say WHICH anchors a residual belongs to.
    """

    T: np.ndarray
    Tc: np.ndarray
    Q: np.ndarray
    sigma: np.ndarray
    t_abs: np.ndarray
    era: tuple = ()
    source: tuple = ()
    #: Heater output in percent.  Not used by the objective -- ``Q`` is what
    #: enters it -- and carried because the campaign drift is measured at
    #: FIXED OUTPUT, which is a statement about ``u`` and not about watts.
    #: See :mod:`drift`.
    u: np.ndarray = None

    @classmethod
    def from_tuple(cls, a):
        if isinstance(a, Anchors):
            return a
        T, Tc, Q, sigma = (np.asarray(v, float) for v in a)
        return cls(T=T, Tc=Tc, Q=Q, sigma=sigma,
                   t_abs=np.full(len(T), math.nan), era=("",) * len(T),
                   u=np.full(len(T), math.nan))

    def __len__(self) -> int:
        return len(self.T)

    @property
    def days(self) -> np.ndarray:
        """Days from the earliest anchor -- the axis the campaign drift runs on."""
        return (self.t_abs - np.nanmin(self.t_abs)) / 86400.0

    def select(self, keep) -> "Anchors":
        """The subset ``keep`` masks, with every column carried along.

        Eight parallel arrays and two tuples, which is exactly the shape of
        thing that gets sliced in six places and misaligned in one of them --
        :func:`trim_anchors` did it by hand, and the leave-one-epoch-out gate
        in analysis/holdout.py needs the same operation on a different mask.
        """
        keep = np.asarray(keep, bool)

        def labels(t):
            # Left alone when it is not per-anchor: `from_tuple` leaves both
            # empty, and a tuple that is not the right length is not a column.
            if len(t) != len(keep):
                return t
            return tuple(v for v, k in zip(t, keep) if k)

        return replace(
            self, T=self.T[keep], Tc=self.Tc[keep], Q=self.Q[keep],
            sigma=self.sigma[keep], t_abs=self.t_abs[keep], u=self.u[keep],
            era=labels(self.era), source=labels(self.source))


def load_rows(path=ANCHORS):
    """The measurement table as plain dicts, resolved against the repository.

    Public, and the only way anything reads that file.  Five plotting and
    export modules used to open it with a bare ``open()`` relative to the
    working directory while this module resolved it against ``REPO_ROOT``, so
    run from anywhere but the repository root the fit loaded and the anchor
    rows silently did not (archive/AUDIT-2026-09-10.md, finding 5).
    """
    with open_table(path) as fh:
        return list(csv.DictReader(fh))


def _era_sigma(row) -> float:
    """``ANCHOR_SIGMA_K`` for one anchor's era.  Raises on an era it has no bar for."""
    era = (row.get("era") or "").strip()
    try:
        return ANCHOR_SIGMA_K[era]
    except KeyError:
        raise SystemExit(
            f"fit_ode: anchor from {row.get('source')!r} has era {era!r}, which "
            f"ANCHOR_SIGMA_K has no bar for.  Re-run analysis/steps.py -- an "
            f"anchor table written before the era column existed cannot be "
            f"weighted, and defaulting it silently is how the per-era offset "
            f"came to fit nothing.") from None


def anchor_epoch(row) -> float:
    """When this anchor's level was true, in unix seconds.

    ``t_mid``, and the choice matters for step 8.  Phase A quotes a hold's
    level at the MIDPOINT of its window with the drift beside it, because a
    drifting hold has no single temperature and the midpoint is where a linear
    drift's error is smallest.  Dating it by ``t_end`` instead -- which is what
    REFIT_PLAN.md step 1 said, written before Phase A existed -- would put the
    70 h hold's anchor 35 h away from the moment it describes.
    """
    text = (row.get("t_mid") or "").strip()
    if not text:
        return math.nan
    try:
        return _seg._stamp(text)
    except ValueError:
        return math.nan


def load_anchors(path=ANCHORS, t_max=None) -> Anchors:
    """Every dwell whose steady state is usable, with its own error bar.

    Returns an :class:`Anchors`.  It was a bare four-tuple and no caller
    unpacked it, so this is a widening rather than a break; ``fit`` still
    accepts the tuple through :meth:`Anchors.from_tuple`.
    """
    T, Tc, Q, sigma, when, era, ids, u = [], [], [], [], [], [], [], []
    for r in load_rows(path):
        if not r.get("grade"):
            continue
        Ti = _f(r, "T_inf")
        if t_max is not None and Ti > t_max:
            continue
        cool = _era_sigma(r)
        own = max(ANCHOR_FLOOR_K, 2.0 * abs(_f(r, "settle_K")))
        T.append(Ti)
        Tc.append(_f(r, "Coldplate"))
        Q.append(_f(r, "P_W"))
        sigma.append(math.hypot(cool, own))
        when.append(anchor_epoch(r))
        era.append((r.get("era") or "").strip())
        ids.append((r.get("id") or r.get("source") or "").strip())
        u.append(_f(r, "u_pct"))
    return Anchors(T=np.array(T, float), Tc=np.array(Tc, float),
                   Q=np.array(Q, float), sigma=np.array(sigma, float),
                   t_abs=np.array(when, float),
                   era=tuple(era), source=tuple(ids),
                   u=np.array(u, float))


def load_taus(path=ANCHORS, t_max=None):
    """Dwells whose relaxation ran long enough for tau to mean something."""
    out = []
    for r in load_rows(path):
        if r.get("grade") != "tau":
            continue
        T = _f(r, "T_inf")
        if t_max is not None and T > t_max:
            continue
        out.append((T, _f(r, "tau_s")))
    a = np.array(out)
    return a[:, 0], a[:, 1]


def load_record(window=SWEEP) -> Record:
    """One manifest ``trace`` as a :class:`Record`, on the full 2 s grid."""
    s = sweep_window(window)
    t, T, Tc, u = load_sweep(window)
    ok = ~np.isnan(s.epoch)
    t0 = float(s.epoch[ok][0]) if ok.any() else math.nan
    return Record.from_tuple((t, T, Tc, u), name=window, t0=t0)


def _decimated_t0(path=DECIMATED) -> float:
    """The decimated table's first absolute timestamp, in unix seconds.

    Its own ``Timestamp`` column, and NOT the manifest window it was written
    from: resolving the window means reading a 460,000-row archive table, which
    is the twenty seconds the decimated grid exists to avoid.  That column is
    there for this -- REFIT_PLAN.md Phase B step 4 added it and nothing had
    read it until now.
    """
    with open_table(path) as fh:
        row = next(csv.DictReader(fh), None)
    text = (row or {}).get("Timestamp", "").strip()
    if not text:
        raise SystemExit(
            f"fit_ode: {path} has no Timestamp on its first row.  The absolute "
            f"clock is what the campaign drift is a function of; regenerate it "
            f"with analysis/decimate.py --write.")
    return _seg._stamp(text)


def load_decimated_record(path=DECIMATED, name=SWEEP) -> Record:
    """The thinned sweep as a :class:`Record`, weights and absolute clock included."""
    data, w = load_decimated(path)
    return Record.from_tuple(data, weights=w, name=name, t0=_decimated_t0(path))


#: The post-recalibration trajectory, as a manifest window.  2026-09-05 17:35 ->
#: 09-09 18:06, 96.5 h, 114.2-121.0 K: the tail of the 69.94 h hold at 63.699 %,
#: the 09-08 steps that walked the sample to 121 K, and the 27.06 h hold after
#: them.  Its two arguable boundaries are argued in its own manifest note.
POSTCAL = "trace-postcal-20260905"


def load_trace_record(window, **kw) -> Record:
    """A manifest ``trace`` as a decimated :class:`Record`, thinned in memory.

    :func:`load_decimated_record` reads a COMMITTED table, which is what the
    sweep has and what keeps the expensive fit reproducible from a clone without
    a preparation step.  This one pays the twenty seconds instead, because a
    second committed table is only worth having once a second record actually
    ships -- that is step 10, not step 7.

    The thinning is ``decimate``'s, unchanged and with its own constants, so
    both records are on the same rule.  It matters more here than on the sweep:
    the post-recal record is 96.5 h of which 96 h is two settled holds, 173,728
    samples thinned 134:1 to 1,301, and the reconstruction is 17.5 mK rms
    against the thermometer's own 28.
    """
    import decimate as _dec
    full = load_record(window)
    small, span, _ = _dec.decimate((full.t, full.T, full.Tc, full.u), **kw)
    return Record(name=window, t=small[0], T=small[1], Tc=small[2], u=small[3],
                  w=_dec.weights(span), t0=full.t0)


def as_records(data, weights=None) -> list:
    """``data`` as a list of :class:`Record`, however it was passed.

    One record, a list of them, or the bare ``(t, T, Tc, u)`` tuple five call
    sites still use.  The ambiguity worth being careful about is that a tuple
    of four arrays and a list of four Records look alike to ``len()`` and to
    nothing else, so the test is on the element type.
    """
    if isinstance(data, Record):
        return [Record.from_tuple(data, weights=weights)]
    if isinstance(data, (list, tuple)) and data and isinstance(data[0], Record):
        if weights is not None:
            raise SystemExit(
                "fit_ode: weights= with a list of Records.  Each record "
                "carries its own; a single array cannot mean anything across "
                "several clocks.")
        return list(data)
    return [Record.from_tuple(data, weights=weights)]


def trim_anchors(anchors, records):
    """``(anchors, dropped)`` -- anchors inside a fitted record's span removed.

    REFIT_PLAN.md trap T4.  A dwell that lies inside a trajectory the ODE is
    integrated down is already in the objective, sample by sample, as the
    trajectory; keeping it as an anchor too counts the same evidence twice.  It
    is not fatal -- the two agree, they are the same seconds of the same log --
    but it silently multiplies that dwell's weight and stops ``ANCHOR_SHARE``
    meaning what it says.

    Dated by ``t_abs``, which is the window's MIDPOINT (see
    :func:`anchor_epoch`), so a dwell straddling a record's edge is judged by
    where its level was quoted.  An anchor whose date is unknown is ``nan``,
    every comparison against it is false, and it is kept -- which is the right
    way round: "cannot be shown to be inside" is not "inside".

    The 17 sweep-derived anchors have been double-counted in the production fit
    all along, so this is not only for the records step 7 adds.
    """
    if not len(anchors) or not records:
        return anchors, ()
    inside = np.zeros(len(anchors), bool)
    for r in records:
        if math.isnan(r.t0) or not len(r.t):
            continue
        inside |= ((anchors.t_abs >= r.t0)
                   & (anchors.t_abs <= r.t0 + float(r.t[-1] - r.t[0])))
    if not inside.any():
        return anchors, ()
    dropped = tuple(np.asarray(anchors.source, object)[inside])
    return anchors.select(~inside), dropped


def record_scale(records, n_eff):
    """One multiplier per record, applying :data:`RECORD_SHARE`.

    Each record arrives with unit-mean-square weights, so it would contribute
    ``sum(w**2) = len(r)`` to the objective -- its sample count.  These
    multipliers redistribute that total without changing it: whatever the
    policy, ``sum over records of (scale * w)**2`` is ``n_eff``, so
    ``ANCHOR_SHARE`` and every other share below still means the fraction of
    the trajectory it always meant.

    Returns ``[1.0]`` for a single record under either policy, which is what
    makes this inert on everything that ships today.
    """
    if RECORD_SHARE == "samples":
        return [1.0] * len(records)
    if RECORD_SHARE != "equal":
        raise SystemExit(
            f"fit_ode: RECORD_SHARE = {RECORD_SHARE!r} is not a policy this "
            f"knows.  It is 'equal' or 'samples', and it is explicit on "
            f"purpose -- see REFIT_PLAN.md trap T5.")
    each = n_eff / len(records)
    return [math.sqrt(each / len(r)) for r in records]


def knot_range(records, anchors=None, taus=None):
    """``(T_lo, T_hi)`` for :func:`build` -- where the curves get freedom.

    Over every record, because with more than one trajectory the knots have to
    span all of them or the second record is fitted on an extrapolation.

    **``anchors`` and ``taus`` are accepted and are OFF by default.  That was a
    deferral and is now a decision, and the measurement is below.**  REFIT_PLAN.md
    step 2 listed covering them here as inert plumbing and it is not inert: the
    coldest anchor is ``rec-20260824-171059`` at **4.7516 K**, below the sweep's
    own 4.8985 K, so including it moves ``T_lo`` from 4.6536 to 4.5140 K and
    every geomspaced knot with it.  The plan deferred the switch to step 6,
    where the seeding moved anyway; PID_PLAN.md phase 1 section 1.1 is where it
    got measured.

    **Nothing identifies the curve below about 7 K, so more freedom there is
    worse, not better.**  Four placements of the bottom knot, production preset,
    the conductance they give as a ratio of largest to smallest:

    ====== ====== ====== ====== ====== ====== ====== ======
    T (K)   4.55   4.75   5.00   5.50   6.00   7.00  10.00
    spread  10.0x   5.4x   2.4x   1.6x  1.19x  1.02x  1.03x
    ====== ====== ====== ====== ====== ====== ====== ======

    Above 7 K the placement does not matter; below 6 K it decides the answer.
    Three things are meant to determine that region and none of them does.  The
    140 sweep samples under 10 K are 2.8 % of the grid and **0.3 % of the
    weight**, the roughness prior is evaluated at knot midpoints and reaches no
    lower than 5.14 K, and the anchors do not reach either: at 4.75 K one kelvin
    of bar is 1.8 mW, and the four zero-output anchors -- the only ones that
    could speak about the bottom -- are missed by 0.2 to 1.2 mW, which is inside
    it.  **Dropping all four changes the fitted curve in the sixth significant
    figure** (cost 836.075 -> 837.245, every conductance ratio 1.00x, and the
    refit predicts them exactly as badly as the fit that saw them).

    So the cold end is whatever basin the optimiser lands in, and handing it
    another parameter is how you find that out the hard way:

    ================================ ======== ======= ==========
    bottom knot, upper 19 unmoved      cost     rms_k   ``nfev``
    ================================ ======== ======= ==========
    4.6536 K, as shipped               836.1  0.1295         111
    plus one knot at 4.5299 K         1526.6  0.1623         110
    moved to 4.5299 K                  852.5  0.1308         220
    ================================ ======== ======= ==========

    One EXTRA knot below all the data costs **83 % of the objective** and
    deforms 5-7 K wholesale (Q(6 K) 18.4 -> 10.9 mW).  Moving the bottom knot
    instead costs 2 % and twice the iterations to reach a curve that differs
    nowhere above 6 K.  Neither buys anything: the steady state is determined to
    better than 0.05 K above 6 K under every placement, tau below 7 K is not
    determined under any of them, and PID_PLAN.md section 3 says tau below 30 K
    does not enter the loop while the monitor has no opinion below 28 % output,
    which is about 11 K.  **The switch stays off.**

    What DID have to change is that the objective evaluates Lambda at the
    coldplate as well as at the sample and this function has never looked at
    ``T_c``.  See :data:`KNOT_EXTRAP_TOL`.
    """
    lo = min(float(r.T.min()) for r in records)
    hi = max(float(r.T.max()) for r in records)
    if anchors is not None and len(anchors):
        lo, hi = min(lo, float(anchors.T.min())), max(hi, float(anchors.T.max()))
    if taus is not None and len(taus[0]):
        lo = min(lo, float(np.min(taus[0])))
        hi = max(hi, float(np.max(taus[0])))
    return 0.95 * lo, 1.05 * hi


def production_inputs(decimated=True, t_max=None):
    """``(record, anchors, taus)`` -- the ONE definition of what a fit reads.

    Three call sites built this by hand in five identical lines each
    (``plot_gain``, ``export_response``, ``plan_sweep``), and the ladder built
    a fourth version in two places that were already drifting apart -- see
    :func:`_worker`.  Every one of them ends with the same subtle step: the
    anchors and the taus are cut at the record's own ``T.max()``, because the
    fit has nothing to say above the hottest sample it ever saw and an anchor
    up there would be extrapolation weighted as measurement.  That cut being
    open-coded five times is how the two ladder paths came to disagree.

    ``t_max`` defaults to the record's own maximum, which is what every caller
    passed.  Pass one explicitly only to ask a deliberately different question.
    """
    rec = load_decimated_record() if decimated else load_record()
    hi = float(rec.T.max()) if t_max is None else t_max
    return rec, load_anchors(t_max=hi), load_taus(t_max=hi)


class LogLog:
    """A positive, strictly increasing curve on fixed knots: monotone cubic in
    (log T, log y).

    Held as log-values so positivity is free, and as a base plus exponentiated
    increments so monotonicity is too -- no bounds, no penalty terms, and the
    optimiser is never in a position to propose a falling conductance.

    PCHIP rather than piecewise-linear, because the derivative is a
    deliverable here and not just an intermediate.  dLambda/dT IS the physical
    conductance and C/(dLambda/dT) IS tau, so a C0 interpolant hands back a
    staircase whose steps sit exactly at the knots -- an artefact of where the
    knots were put, presented as if it were the cryostat.  PCHIP is C1 and
    still monotonicity-preserving, at the same parameter count.

    Outside the knots both value and slope extrapolate linearly in log-log
    from the end knot, which keeps it monotone and keeps tau finite.
    """

    def __init__(self, T_knots):
        self.knots = np.asarray(T_knots, float)
        self.lk = np.log(self.knots)
        self.n = len(self.lk)

    def unpack(self, p):
        return np.concatenate(([p[0]], p[0] + np.cumsum(np.exp(p[1:self.n]))))

    def _spline(self, p):
        return PchipInterpolator(self.lk, self.unpack(p), extrapolate=False)

    def _eval(self, p, T):
        sp = self._spline(p)
        x = np.log(np.asarray(T, float))
        xc = np.clip(x, self.lk[0], self.lk[-1])
        d = sp.derivative()(xc)
        return np.exp(sp(xc) + d * (x - xc)), d, x

    def __call__(self, p, T):
        return self._eval(p, T)[0]

    def slope(self, p, T):
        """dy/dT = (dlog y / dlog T) * y / T."""
        y, d, _ = self._eval(p, T)
        return d * y / np.asarray(T, float)

    def scalar(self, p):
        """Plain-Python evaluator for the integration loop.

        The inner loop runs once per sample per residual evaluation and numpy
        scalar dispatch dominates it, so the spline is unpacked to coefficient
        lists once and evaluated with bisect and Horner.
        """
        sp = self._spline(p)
        xs = list(sp.x)
        c = [list(row) for row in sp.c]
        top = len(xs) - 2
        d_lo = c[2][0]
        s_hi = xs[-1] - xs[-2]
        d_hi = (3 * c[0][top] * s_hi + 2 * c[1][top]) * s_hi + c[2][top]
        y_lo, y_hi = c[3][0], sp(xs[-1])

        def at(T):
            x = math.log(T)
            if x <= xs[0]:
                y, dy = y_lo + d_lo * (x - xs[0]), d_lo
            elif x >= xs[-1]:
                y, dy = y_hi + d_hi * (x - xs[-1]), d_hi
            else:
                i = bisect_right(xs, x) - 1
                i = 0 if i < 0 else (top if i > top else i)
                s = x - xs[i]
                y = ((c[0][i] * s + c[1][i]) * s + c[2][i]) * s + c[3][i]
                dy = (3 * c[0][i] * s + 2 * c[1][i]) * s + c[2][i]
            Y = math.exp(y)
            return Y, dy * Y / T
        return at


def integrate(lam, cap, pl, pc, t, Tc, u, T0, q_extra=None):
    """Exponential Euler down the sweep.  Returns the modelled T(t).

    ``q_extra`` is an additional power in watts, per sample -- the slow drift
    term.  It enters exactly where the heater does, because that is what it is:
    a small unmeasured load on the same node.
    """
    Q = power_w(u)
    if q_extra is not None:
        Q = Q + q_extra
    lam_c = lam(pl, np.maximum(Tc, 1e-3))
    lam_at, cap_at = lam.scalar(pl), cap.scalar(pc)
    out = np.empty_like(t)
    T = float(T0)
    exp = math.exp
    for k in range(len(t) - 1):
        out[k] = T
        dt = t[k + 1] - t[k]
        lo, g = lam_at(T)
        c = cap_at(T)[0]
        tau = c / (g if g > 1e-12 else 1e-12)
        T += ((Q[k] - lo + lam_c[k]) / c) * tau * (1.0 - exp(-dt / tau))
        T = 1.0 if T < 1.0 else (1000.0 if T > 1000.0 else T)
    out[-1] = T
    return out


def integrate2(lam, cap, pl, pc, split, mass_ratio, t, Tc, u, T0):
    """Two nodes: the sample, and the copper between it and the coldplate.

    Built so the STEADY STATE IS UNCHANGED.  Take the two halves of the link to
    share a conductivity shape and differ only in geometry; then in series

        Lambda_1 = Lambda / f        Lambda_2 = Lambda / (1 - f)

    reproduces the fitted total for any f, because f/A + (1-f)/A = 1/A.  Set
    dT/dt = 0 in both nodes and the middle temperature drops out, leaving
    Lambda(T_s) - Lambda(T_c) = Q exactly as before.  So f and the mass ratio
    g = C_m / C_s buy dynamics and nothing else, which is the only honest way
    to ask whether a second node is what the transients are missing -- if the
    split could also move the steady state it would just be more freedom.

    Two extra parameters, both physical: where the thermal resistance sits
    between sample and copper, and how much copper there is.
    """
    Q = power_w(u)
    lam_c = lam(pl, np.maximum(Tc, 1e-3))
    lam_at, cap_at = lam.scalar(pl), cap.scalar(pc)
    inv_f, inv_g = 1.0 / split, 1.0 / (1.0 - split)
    out = np.empty_like(t)
    Ts = Tm = float(T0)
    exp = math.exp
    for k in range(len(t) - 1):
        out[k] = Ts
        dt = t[k + 1] - t[k]
        ls, gs = lam_at(Ts)
        lm, gm = lam_at(Tm)
        cs = cap_at(Ts)[0]
        cm = mass_ratio * cap_at(Tm)[0]
        q1 = (ls - lm) * inv_f
        q2 = (lm - lam_c[k]) * inv_g
        tau_s = cs / max(gs * inv_f, 1e-12)
        tau_m = cm / max(gm * (inv_f + inv_g), 1e-12)
        Ts += ((Q[k] - q1) / cs) * tau_s * (1.0 - exp(-dt / tau_s))
        Tm += ((q1 - q2) / cm) * tau_m * (1.0 - exp(-dt / tau_m))
        Ts = 1.0 if Ts < 1.0 else (1000.0 if Ts > 1000.0 else Ts)
        Tm = 1.0 if Tm < 1.0 else (1000.0 if Tm > 1000.0 else Tm)
    out[-1] = Ts
    return out


def _seed(knots, fn):
    ly = np.log(fn(knots))
    return np.concatenate(([ly[0]], np.log(np.maximum(np.diff(ly), 1e-6))))


#: Seed Lambda and C from the anchors rather than from a power law.
#:
#: REFIT_PLAN.md Phase B step 6.  **The first thing in Phase B that changes an
#: answer**, so it is a switch and not a rewrite: ``fit(seed_measured=False)``
#: is the old seed, the flag is in the cache key, and the two can be run side
#: by side.  What it is worth is in ``analysis/README.md``.
SEED_MEASURED = True

#: Evaluation budget for ``least_squares``.
#:
#: **300 was not a budget, it was a truncation.**  Measured on the production
#: 20/4 preset: with the old power-law seed the fit is still going at 300 AND
#: at 1000, so every production number this repository has quoted was an upper
#: bound rather than a fit -- which section 7's preamble suspected and this
#: confirms.  With ``SEED_MEASURED`` it terminates on its own at **562**.
#:
#: 1500, then: past where BOTH seeds converge -- the measured one at 562 and
#: the power law at 1157 -- so a comparison between them is a comparison of
#: two converged fits and not of one fit and one truncation.  And it is
#: free for the fits that already converged -- the ladder's rungs stop at 76 to
#: 138 and a cap they never reach costs nothing.  Raising it only spends time
#: on a fit that was being cut off.
MAX_NFEV = 1500

#: Anchors per bin when the measured seed reduces them to a curve.  Enough that
#: a bin's median is not one anchor, few enough that 136 anchors still give
#: more bins than Lambda has knots.
SEED_BIN_N = 6


def measured_lambda(anchors, gauge=True):
    """``(T, Lambda)`` read straight off the settled anchors -- no fit in it.

    At steady state ``Lambda(T_s) - Lambda(T_c) = Q``, with no heat capacity
    anywhere in the statement, so every settled dwell IS a point on Lambda.
    That is REFIT_PLAN.md principle 1, and until now nothing used it except as
    a residual -- the fit started from a power law and had to discover this.

    Two things have to be dealt with to turn the anchors into a curve.

    ``T_c`` is not constant, so the equation gives ``Lambda(T) - Lambda(T_c)``
    against a moving baseline.  Solved by iterating: take ``Lambda = Q``,
    interpolate it at each anchor's own ``T_c``, add that back, repeat.  It
    converges in two or three passes because ``Lambda`` at 5-7 K is a percent
    of ``Q``, which is the same reason the baseline was ignorable to begin with.

    **The LEVEL is a gauge and is NOT chosen here.**  What comes back is the
    ``Lambda(T_c) = 0`` reading, which is what the anchors literally say.  Trap
    T1: only differences of ``Lambda`` enter the anchor residual, the
    integrator and the tau residual, so an added constant is an exact null
    direction of the likelihood.

    **Two attempts to choose it better were both wrong, and the second was
    worse than useless.**  The penalty acts on ``log(dLambda/dT)`` and
    ``d(Lambda + c)/dT = dLambda/dT``, so the constant is invisible to the
    prior as well and a search for "the level the prior likes" is degenerate --
    the first version duly returned the top of whatever grid it was handed,
    9.68 W against a curve spanning 0.97.  The second searched the residue,
    which is real but pure discretisation (the parameterisation interpolates
    **log Lambda** between knots, so a constant moves the curve between them),
    through the actual :class:`LogLog`.  That was defensible and still wrong,
    because it was measured:

        gauge      cost     nfev     (production 20/4, run to convergence)
        0.0000   994.64      149
        0.0175   994.64      566     <- what the search returned at 20 knots
        2.4121  1163.02      561     <- what it returned at 9 knots

    A zero gauge reaches the same optimum **3.8x faster**, and the 9-knot
    answer -- which railed at its grid edge, 3x the span -- lands the fit in
    the WORSE basin, exactly where the old power-law seed was stuck.  So a
    search over a null direction bought nothing at best and destroyed step 6's
    entire benefit at worst.  It is gone.  Do not reintroduce one without
    running that table.
    """
    o = np.argsort(anchors.T)
    T, Tc, Q = anchors.T[o], anchors.Tc[o], anchors.Q[o]
    lam = Q.copy()
    for _ in range(4):
        lam = Q + np.interp(Tc, T, lam)
    # Bin in log T and take medians: several anchors sit within millikelvin of
    # each other at different dates and different powers, and the campaign
    # drift is exactly the spread between them.  A median is the right summary
    # of a quantity whose scatter is the thing being fitted later.
    nbin = max(int(len(T) / SEED_BIN_N), 4)
    edges = np.geomspace(T[0], T[-1] * (1 + 1e-9), nbin + 1)
    idx = np.clip(np.searchsorted(edges, T, "right") - 1, 0, nbin - 1)
    bT, bL = [], []
    for b in range(nbin):
        m = idx == b
        if m.any():
            bT.append(float(np.median(T[m])))
            bL.append(float(np.median(lam[m])))
    bT, bL = np.array(bT), np.array(bL)
    # Lambda is an integral of a positive conductance, so it increases.  The
    # medians can still step down where two bins straddle an era; a running
    # maximum is the least-assuming repair and leaves a monotone curve the
    # LogLog parameterisation can actually hold.
    bL = np.maximum.accumulate(bL)
    # Strictly increasing, not merely non-decreasing: `_seed` takes the log of
    # successive differences and a repeated value is log(0).
    bL = bL + np.arange(len(bL)) * 1e-12
    return bT, bL


def measured_capacity(taus, lam_of):
    """``(T, C)`` from ``C = tau * dLambda/dT`` -- C measured, not assumed.

    With ``Lambda`` known a measured time constant stops being a check on C and
    becomes a measurement of it, which is REFIT_PLAN.md principle 1's other
    half and step 6's instruction, taken literally.

    **A single magnitude on the Debye SHAPE is not good enough, and that was
    measured rather than assumed.**  The first version of this fitted one
    factor to the mix and reconstructed ``tau = C/Lambda'`` at 137 K as 804 s
    where the tau anchors there say 607 -- 32 % out, because a median ratio
    over 25-192 K is dominated by wherever the real C departs from Debye most,
    and it lands that error at the temperature the fit is judged on.  So the
    shape comes from the data where there are taus.

    Outside their 25.8-192.4 K the Debye mix takes over, scaled to match at the
    nearest measured point.  That is the right division of labour: below 25 K
    there is no tau to read C from, C falls by three orders of magnitude, and
    the shape prior (``CAP_SHAPE_FACTOR``) is going to hold it to that mix
    anyway.

    Medians in log-T bins, and monotone by running maximum: tau near 137 K
    scatters 433 to 850 s across dwells, which is what a single pole does on a
    body with internal gradients rather than an error to average away.
    """
    tT, tV = taus
    c_ref = mix_c(np.array([CAP_SHAPE_REF_K]))[0]
    if len(tT) < 4:
        grid = np.geomspace(5.0, 200.0, 12)
        return grid, mix_c(grid) / c_ref
    o = np.argsort(tT)
    T, C = tT[o], tV[o] * lam_of.derivative()(tT[o])
    good = np.isfinite(C) & (C > 0)
    T, C = T[good], C[good]
    nbin = max(int(len(T) / 3), 4)
    edges = np.geomspace(T[0], T[-1] * (1 + 1e-9), nbin + 1)
    idx = np.clip(np.searchsorted(edges, T, "right") - 1, 0, nbin - 1)
    bT, bC = [], []
    for b in range(nbin):
        m = idx == b
        if m.any():
            bT.append(float(np.median(T[m])))
            bC.append(float(np.median(C[m])))
    bT, bC = np.array(bT), np.maximum.accumulate(np.array(bC))
    bC = bC * (1.0 + np.arange(len(bC)) * 1e-12)
    return bT, bC


def _capacity_seed(bT, bC):
    """``C(T)`` over the whole knot range: measured inside, Debye outside."""
    c_ref = mix_c(np.array([CAP_SHAPE_REF_K]))[0]
    lo_scale = bC[0] / (mix_c(np.array([bT[0]]))[0] / c_ref)
    hi_scale = bC[-1] / (mix_c(np.array([bT[-1]]))[0] / c_ref)
    inside = PchipInterpolator(np.log(bT), np.log(bC), extrapolate=False)

    def at(T):
        T = np.atleast_1d(np.asarray(T, float))
        out = np.exp(inside(np.log(T)))
        cold = T < bT[0]
        hot = T > bT[-1]
        if cold.any():
            out[cold] = lo_scale * mix_c(T[cold]) / c_ref
        if hot.any():
            out[hot] = hi_scale * mix_c(T[hot]) / c_ref
        return out
    return at


def build(n_lam, n_cap, T_lo, T_hi, anchors=None, taus=None):
    """Knots and their seeds.  Measured from the anchors when they are given.

    REFIT_PLAN.md Phase B step 6.  The fallback below is the original seed and
    is kept for callers with no anchors to hand -- and as the thing the
    measured seed is compared against.
    """
    kl = np.geomspace(T_lo, T_hi, n_lam)
    kc = np.geomspace(T_lo, T_hi, n_cap)
    c_ref = mix_c(np.array([CAP_SHAPE_REF_K]))[0]

    if anchors is not None and len(anchors) >= 4:
        bT, bL = measured_lambda(anchors)
        # C is measured on the Lambda' the ANCHORS give, before the gauge: the
        # derivative is what tau multiplies and it does not depend on the level.
        cT, cC = measured_capacity(taus if taus is not None else load_taus(),
                                   PchipInterpolator(bT, bL, extrapolate=True))
        lam_of = PchipInterpolator(np.log(bT), np.log(bL), extrapolate=True)
        lam_fn = lambda T: np.exp(lam_of(np.log(np.asarray(T, float))))  # noqa: E731
        return (LogLog(kl), LogLog(kc),
                _seed(kl, lam_fn),
                _seed(kc, _capacity_seed(cT, cC)))

    # Seed Lambda on the top settled hold rather than on its absolute value:
    # what the data fixes is the DIFFERENCE Lambda(180.6) - Lambda(8.5) = 0.778 W,
    # and a seed that gets the difference wrong starts the integration with a
    # net power of the same order as the heater and runs away before the
    # optimiser sees a usable gradient.
    shape = lambda T: (T / 180.6) ** 0.35            # noqa: E731
    a_lam = 0.778 / (shape(180.6) - shape(8.5))
    # C ~ 1 J/K at 137 K, which is a few grams of copper and sapphire, and
    # shaped like the Debye mix so the fit starts where the prior wants it
    return (LogLog(kl), LogLog(kc),
            _seed(kl, lambda T: a_lam * shape(T)),
            _seed(kc, lambda T: 1.00 * mix_c(T) / c_ref))




def opening_hold(t, u):
    """Mask of the settled stretch the sweep opens on.

    The sweep begins with 2.2 h at a constant heater on a cryostat that had
    been there for 26 h, so the truth over that stretch is known exactly: it
    does not move.  A model that drifts there has the steady state wrong at
    the one temperature the data pins hardest, and no amount of transient
    agreement elsewhere redeems it -- so this is reported separately from the
    overall rms, which a long flat stretch would otherwise flatter.
    """
    same = np.abs(u - u[0]) <= 0.02
    end = int(np.argmin(same)) if not same.all() else len(t)
    m = np.zeros(len(t), bool)
    m[:end] = True
    return m


#: Starting split and mass ratio for a tier-2 fit: half the resistance on
#: each side, and a middle mass equal to the sample's.  Both are held in
#: logit/log space so the optimiser cannot walk f out of (0, 1) or g negative.
TIER2_SEED = (0.0, 0.0)


#: Where a converged parameter vector is kept so the figure scripts do not
#: each spend a quarter of an hour re-deriving one another's fit.  Gitignored:
#: it is derived, it is keyed on a digest of the inputs, and a stale entry
#: cannot survive a change to the sweep, the anchors or the taus because all
#: three are in the key.  Delete the directory to force a refit.
#:
#: Resolved against ``REPO_ROOT`` and not against the working directory, which
#: is archive/AUDIT-2026-09-10.md finding 5 one more time.  ``load_rows`` was fixed and
#: this was not, so running a script from inside ``analysis/`` created
#: ``analysis/analysis/.fit_cache`` and quietly refitted everything from
#: scratch against a second cache -- no error, no warning, just a quarter of an
#: hour and a directory nobody expected.  Found doing exactly that.
CACHE_DIR = os.path.join(_REPO_ROOT, "analysis", ".fit_cache")


def cache_key(n_lam, n_cap, tier2, max_nfev, *arrays) -> str:
    h = hashlib.sha256()
    for name in (n_lam, n_cap, tier2, max_nfev, FIT_CACHE_VERSION):
        h.update(repr(name).encode())
    for a in arrays:
        b = np.ascontiguousarray(a, dtype=np.float64)
        h.update(repr(b.shape).encode())
        h.update(b.tobytes())
    return h.hexdigest()[:32]


def cache_load(key: str, npar: int):
    """``(x, nfev)`` for a previous run of this exact fit, or ``None``.

    Refuses an entry of the wrong length rather than reshaping it: that would
    mean the key collided or the parameterisation changed, and either way a
    silent wrong answer is the one outcome worth spending a refit to avoid.
    """
    path = os.path.join(CACHE_DIR, key + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return None
    x = np.array(blob.get("x", ()), dtype=float)
    if x.size != npar:
        return None
    return x, int(blob.get("nfev", 0))


def cache_store(key: str, x, nfev: int) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = os.path.join(CACHE_DIR, key + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"x": [float(v) for v in x], "nfev": int(nfev)}, fh)
    os.replace(tmp, os.path.join(CACHE_DIR, key + ".json"))


def fit(n_lam, n_cap, data=None, anchors=None, taus=None, max_nfev=MAX_NFEV,
        tier2=False, weights=None, n_drift=0, campaign=False,
        campaign_w=None, key_only=False, seed_measured=SEED_MEASURED):
    """Fit Lambda and C to a sweep.

    ``weights`` is the per-sample weight of the sweep residual, and exists for
    the adaptively decimated grid (see ``analysis/decimate.py``): a kept sample
    stands for a span of time rather than for one 2 s tick, and without the
    weight a 22.8 h hold thinned 149:1 would silently stop mattering.  It is
    normalised to unit mean square, so ``sum(w**2) == len(t)`` exactly as on a
    uniform grid -- which is what keeps the anchor, tau and shape shares below
    balanced against the sweep the same way they were.

    ``campaign`` turns on the drift ramp of :data:`CAMPAIGN_SIGMA_W_PER_DAY`.
    **Pass it for anything that ships.**  It replaces the ``groups=`` per-era
    power offset, which was one step between two named halves of a continuous
    55-day campaign; without either, the curve splits the difference between
    July and September and is about a kelvin wrong for both.  It is off by
    default for the same reason ``groups=`` was: the knot ladder and
    ``pid_tuning`` are complexity studies and a drift term in them confounds
    what they exist to separate.

    ``campaign_w`` PINS the ramp at a given watts per day instead of fitting
    it, and is how the objective gets profiled in the one parameter step 8
    adds -- "is this slope measured or merely permitted" is a question about
    the shape of the objective around the optimum, and a fit that is free to
    move cannot answer it.  The prior term is kept in ``cost`` when the slope
    is pinned, as a constant, so the two are comparable numbers.
    """
    recs = as_records(data if data is not None else load_sweep(), weights)
    anc = Anchors.from_tuple(anchors if anchors is not None else load_anchors())
    anc, twice = trim_anchors(anc, recs)
    tauT, tauV = taus if taus is not None else load_taus()
    # The flat concatenations.  Every residual and every reported metric runs
    # over these, so with one record they are the arrays this function has
    # always used and nothing downstream can tell the difference.
    t = np.concatenate([r.t for r in recs])
    T = np.concatenate([r.T for r in recs])
    Tc = np.concatenate([r.Tc for r in recs])
    u = np.concatenate([r.u for r in recs])
    aT, aTc, aQ, aS = anc.T, anc.Tc, anc.Q, anc.sigma
    # The delivered-power bar, in watts, beside the kelvin one -- trap T10.
    # Derived here rather than carried on Anchors: it is a function of Q alone,
    # and a stored copy is one more thing that can fall out of step with it.
    aP = DELTA_P_FRAC * np.abs(aQ)
    lam, cap, pl0, pc0 = build(n_lam, n_cap, *knot_range(recs),
                               anchors=anc if seed_measured else None,
                               taus=(tauT, tauV))
    # Lambda is evaluated at the COLDPLATE too -- the residual is
    # Lambda(T_s) - Lambda(T_c) everywhere -- and knot_range looks only at the
    # sample.  So the bottom of the curve is a continuation rather than a piece
    # of it, by a margin nothing here chose.  Measured and accepted at 2.7 %;
    # checked so that a colder coldplate in some later record is loud.
    coldest = min([float(np.min(r.Tc)) for r in recs]
                  + ([float(np.min(aTc))] if len(aTc) else []))
    if coldest < (1.0 - KNOT_EXTRAP_TOL) * lam.knots[0]:
        raise SystemExit(
            f"fit_ode: Lambda's lowest knot is at {lam.knots[0]:.4f} K and the "
            f"objective evaluates Lambda down to {coldest:.4f} K -- the "
            f"coldplate, which knot_range does not see.  That is "
            f"{100 * (1 - coldest / lam.knots[0]):.1f} % below the knot, past "
            f"KNOT_EXTRAP_TOL = {100 * KNOT_EXTRAP_TOL:.0f} %.  Below the "
            f"bottom knot the curve is a log-log continuation and nothing "
            f"measures it; read knot_range's docstring before widening this.")
    n = len(pl0)
    #: The sample count every prior share is measured against.
    #:
    #: It was ``len(t)`` for the one record there was.  With several it has to
    #: be the total, or the anchors' share of "the sweep" would mean a
    #: different absolute weight depending on how many trajectories happened to
    #: be loaded.  Each record's weights are unit mean square, so this is also
    #: ``sum(w**2)`` -- the two agree exactly, which is what makes it inert.
    #:
    #: It does NOT decide the records' weight relative to EACH OTHER.  That is
    #: ``RECORD_SHARE``, just below.
    N_eff = sum(len(r) for r in recs)
    # Each record's own weights are unit mean square (Record.__post_init__), so
    # before this they contributed sum(w**2) = len(r) each -- their sample count,
    # which is the implicit weighting trap T5 names.  `record_scale` replaces
    # that with the declared one and leaves the total at N_eff either way, so
    # every prior share below still means what it did.
    w_sweep = np.concatenate([s * r.w for s, r in
                              zip(record_scale(recs, N_eff), recs)])
    w_anchor = math.sqrt(ANCHOR_SHARE * N_eff / len(aT))
    logT = np.log(T)

    pT = np.geomspace(T.min(), T.max(), 12)
    ref = np.array([CAP_SHAPE_REF_K])
    p_target = np.log(mix_c(pT) / mix_c(ref)[0])
    w_shape = (math.sqrt(CAP_SHAPE_SHARE * N_eff / len(pT))
               / math.log(CAP_SHAPE_FACTOR))
    w_tau = (math.sqrt(TAU_SHARE * N_eff / max(len(tauT), 1))
             / math.log(TAU_SIGMA_FACTOR))
    log_tau = np.log(tauV)

    # THE CAMPAIGN RAMP -- one slope, in watts per day, on the WALL CLOCK.
    #
    # This is the second of trap T2's two terms and the whole of step 8.  It
    # replaces a per-era power offset: two named halves of one continuous
    # cooldown, with a step between them, fitted to anchors that are in fact
    # spread evenly over 55 days.  What the anchors measure is a slope
    # (analysis/drift.py: +0.281 mW/day, three independent output bands
    # agreeing to +-25 % once the local gain is divided out), and a step
    # function of the filename could only ever put that slope's average in one
    # place and call the rest residual.
    #
    # ZERO AT THE REFERENCE EPOCH, which is the latest moment any input
    # describes.  The gauge matters because Lambda is quoted at it: the curve
    # that ships has to be the cryostat AS IT IS NOW, which is what group 0
    # being the reference used to buy.  It is reported, so a caller planning a
    # ladder two weeks out can carry the ramp forward rather than guess.
    if campaign_w is not None:
        campaign = True
    if campaign:
        bad = [s for s, when in zip(anc.source, anc.t_abs)
               if not math.isfinite(when)]
        if bad:
            raise SystemExit(
                f"fit_ode: campaign=True and {len(bad)} anchors have no date "
                f"({', '.join(map(str, bad[:3]))}...).  The ramp is a function "
                f"of wall-clock time and an undated anchor cannot be placed on "
                f"it; re-run analysis/measure.py, which writes t_mid.")
        undated = [r.name for r in recs if math.isnan(r.t0)]
        if undated:
            raise SystemExit(
                f"fit_ode: campaign=True and record(s) {undated} carry no "
                f"absolute clock.  A record built from a bare (t, T, Tc, u) "
                f"tuple has none -- load it with load_record or "
                f"load_decimated_record, which read it from the archive.")
    #: A pinned ramp is applied and not fitted, so it costs no parameter.
    n_camp = 1 if (campaign and campaign_w is None) else 0
    camp_fixed = 0.0 if campaign_w is None else float(campaign_w)
    t_ref = math.nan
    if campaign:
        t_ref = max(([float(np.nanmax(anc.t_abs))] if len(anc) else [])
                    + [float(r.t_abs[-1]) for r in recs if len(r)])
    if CAMPAIGN_FORM not in ("watts", "power"):
        raise SystemExit(
            f"fit_ode: CAMPAIGN_FORM = {CAMPAIGN_FORM!r} is not a shape this "
            f"knows.  It is 'watts' or 'power', and which one it is was "
            f"measured rather than chosen -- read its docstring.")
    # Days before the reference epoch, times whatever the ramp is proportional
    # to.  Under "power" that is the delivered heat over CAMPAIGN_REF_W, so the
    # slope means the same milliwatts per day under either form.
    a_days = ((anc.t_abs - t_ref) / 86400.0 if campaign
              else np.zeros(len(aT)))
    rec_days = ([(r.t_abs - t_ref) / 86400.0 for r in recs] if campaign
                else [])
    if campaign and CAMPAIGN_FORM == "power":
        a_days = a_days * (np.abs(aQ) / CAMPAIGN_REF_W)
        rec_days = [d * (power_w(r.u) / CAMPAIGN_REF_W)
                    for d, r in zip(rec_days, recs)]

    # ONE BLOCK OF DRIFT KNOTS PER RECORD, each on its OWN clock.  This used to
    # raise with more than one record, because a single block in TIME would have
    # several trajectories sharing a clock they do not have -- the sweep's t=0 is
    # 2026-09-02 16:01 and the post-recal record's is three days later, and
    # interpolating one set of knots against both would put the sweep's opening
    # hold and the 70 h hold on the same knot.
    #
    # This is the FIRST of trap T2's two terms: the per-record short-timescale
    # wander, the thing that makes the sweep's 22.8 h opening hold drift
    # -3.8 mK/h at a heater that never moves.  The campaign ramp -- +7 mK/h,
    # opposite sign, a function of wall-clock date rather than of time within a
    # record -- is ``campaign`` above, hung on ``Record.t0``, and it is a
    # different term with its own prior for exactly that reason.
    #
    # The knots are evenly spaced in TIME and there are very few of them -- see
    # DRIFT_SIGMA_W.  Linear interpolation rather than a spline: with three
    # knots a spline's extra smoothness buys nothing and its overshoot is one
    # more way for a nuisance term to reach somewhere it should not.
    #
    # Inert at one record: n_drift_all == n_drift and the single knot block is
    # the same linspace over the same clock.
    dks = ([np.linspace(r.t[0], r.t[-1], n_drift) for r in recs] if n_drift
           else [])
    n_drift_all = n_drift * len(recs)
    # Divided by the TOTAL number of drift knots, so that the term's share of
    # the objective does not grow with the number of records -- each record's
    # wander is a nuisance of the same size, not n times one.
    w_drift = (math.sqrt(DRIFT_SHARE * N_eff / max(n_drift_all, 1))
               / DRIFT_SIGMA_W)
    #: One parameter, so no count to divide by.
    w_camp = (math.sqrt(CAMPAIGN_SHARE * N_eff) / CAMPAIGN_SIGMA_W_PER_DAY
              if campaign else 0.0)

    def campaign_slope(p):
        """Watts per day: the fitted parameter, or the pinned value."""
        return p[-1] if n_camp else camp_fixed

    def campaign_of(p):
        """The ramp at each ANCHOR's own date, in watts.  Zero at ``t_ref``."""
        return campaign_slope(p) * a_days if campaign else 0.0

    def q_extra_of(p):
        """The unmeasured power added to each RECORD's right-hand side, in watts.

        Both slow terms, because both are loads on the same node as the heater:
        the per-record wander on the record's own clock, and the campaign ramp
        on the wall clock.  The ramp is nearly constant across a 43 h record --
        0.5 mW at the measured rate -- but its OFFSET from ``t_ref`` is not, and
        that offset is the whole point: a trajectory taken five days before the
        reference epoch has to be integrated against the cryostat as it was
        then, or it drags Lambda back to its own date.
        """
        if not n_drift and not campaign:
            return None
        knots = p[len(p) - n_tail:len(p) - n_camp] if n_camp else p[len(p) - n_tail:]
        out = []
        for i, r in enumerate(recs):
            q = np.zeros(len(r.t))
            if n_drift:
                q = q + np.interp(r.t, dks[i],
                                  knots[i * n_drift:(i + 1) * n_drift])
            if campaign:
                q = q + campaign_slope(p) * rec_days[i]
            out.append(q)
        return out

    def unpack2(p):
        f = 1.0 / (1.0 + math.exp(-p[-2]))
        return min(max(f, 1e-3), 1 - 1e-3), math.exp(p[-1])

    # Second-difference operator on the knot log-values.  Built once; the knots
    # are geomspaced, so they are evenly spaced in log T and no spacing weights
    # are needed.
    # Evaluated between the knots, not on them: PCHIP's derivative is pinned at
    # a knot by the neighbours, so sampling there measures the parameterisation
    # rather than the curve.  Midpoints in log T see what is actually drawn.
    rough_T = np.exp(0.5 * (np.log(lam.knots[:-1]) + np.log(lam.knots[1:])))
    w_rough = (math.sqrt(LAMBDA_SMOOTH_SHARE * N_eff / max(len(rough_T) - 2, 1))
               / LAMBDA_SMOOTH_SIGMA
               if LAMBDA_SMOOTH_SHARE and len(rough_T) > 2 else 0.0)
    rough_lx = np.log(rough_T)

    n_tail = n_drift_all + n_camp

    def split_p(p):
        """(lambda knots, capacity knots) with the tail parameters removed."""
        body = p[:-n_tail] if n_tail else p
        return body[:n], (body[n:-2] if tier2 else body[n:])

    def run(p):
        """The modelled T for every record, concatenated in record order.

        Each record is integrated on ITS OWN clock and from ITS OWN first
        sample.  Integrating a concatenation would step across the join with
        whatever ``dt`` the two ends happened to differ by and carry the first
        record's final temperature into the second's initial condition -- an
        ODE integrated across a gap converges on a number anyway, which is the
        same trap ``segments.load`` refuses for a window spanning one.
        """
        pl, pc = split_p(p)
        q = q_extra_of(p)
        out = []
        for i, rc in enumerate(recs):
            if not tier2:
                out.append(integrate(lam, cap, pl, pc, rc.t, rc.Tc, rc.u,
                                     rc.T0, q_extra=None if q is None else q[i]))
            else:
                f, g = unpack2(p[:-n_tail] if n_tail else p)
                out.append(integrate2(lam, cap, pl, pc, f, g, rc.t, rc.Tc,
                                      rc.u, rc.T0))
        return np.concatenate(out) if len(out) > 1 else out[0]

    def resid(p):
        pl, pc = split_p(p)
        model = run(p)
        r_sweep = w_sweep * (np.log(model) - logT) / SWEEP_SIGMA_REL
        dQ = lam(pl, aT) - lam(pl, aTc) - aQ - campaign_of(p)
        # In POWER throughout.  It reads as "kelvin missed over kelvin allowed"
        # only when dP is zero; with it, the denominator is the two bars added
        # in quadrature on the side the residual is actually computed on.
        r_anchor = w_anchor * dQ / np.hypot(lam.slope(pl, aT) * aS, aP)
        shape = np.log(cap(pc, pT) / cap(pc, ref)[0])
        r_shape = w_shape * (shape - p_target)
        r_tau = w_tau * (np.log(cap(pc, tauT) / lam.slope(pl, tauT)) - log_tau)
        parts = [r_sweep, r_anchor, r_shape, r_tau]
        if w_rough:
            g = np.log(np.maximum(lam.slope(pl, rough_T), 1e-30))
            d2 = ((g[2:] - g[1:-1]) / (rough_lx[2:] - rough_lx[1:-1])
                  - (g[1:-1] - g[:-2]) / (rough_lx[1:-1] - rough_lx[:-2]))
            parts.append(w_rough * d2)
        if n_drift:
            parts.append(w_drift * (p[len(p) - n_tail:len(p) - n_camp]
                                    if n_camp else p[len(p) - n_tail:]))
        if campaign:
            parts.append(np.array([w_camp * campaign_slope(p)]))
        return np.concatenate(parts)

    p0 = np.concatenate([pl0, pc0]
                        + ([np.array(TIER2_SEED)] if tier2 else [])
                        + ([np.zeros(n_drift_all)] if n_drift else [])
                        + ([np.zeros(n_camp)] if n_camp else []))
    # EVERY constant the objective reads goes in the key, not just the ones
    # that were being tuned the day it was written.  ANCHOR_SHARE was not in
    # here, so a study that varied it got the first run's answer back four
    # times and the knob looked dead -- the same failure mode LAMBDA_SMOOTH_SHARE
    # was caught by, one level up.
    # aTc was missing from this list, which meant a remap of the anchors' own
    # coldplate column -- exactly the thing that happened on 2026-09-04 -- did
    # not invalidate a stored fit.  It enters the objective through
    # ``lam(pl, aTc)`` and belongs in the key beside the rest.
    # The anchors' DATES are in the key because the campaign ramp makes them
    # an input to the objective rather than a label on it -- re-measuring a
    # window's t_mid moves where the ramp places it.  They are hashed whether
    # or not the ramp is on: an entry keyed without them could be reached by a
    # later run that does use them.
    key = cache_key(n_lam, n_cap, tier2, max_nfev, t, T, Tc, u, aT, aTc, aQ, aS,
                    tauV, w_sweep, anc.t_abs,
                    # The PARTITION, not just the concatenation: two records
                    # split differently hash the same flat arrays and are a
                    # different fit -- different knot blocks, different T0s.
                    np.array([len(r) for r in recs], float),
                    np.array([n_drift, seed_measured, n_camp, camp_fixed,
                              CAMPAIGN_SHARE, CAMPAIGN_SIGMA_W_PER_DAY,
                              CAMPAIGN_FORM == "power", CAMPAIGN_REF_W,
                              LAMBDA_SMOOTH_SHARE, LAMBDA_SMOOTH_SIGMA,
                              ANCHOR_SHARE, ANCHOR_FLOOR_K, DELTA_P_FRAC,
                              TAU_SHARE,
                              TAU_SIGMA_FACTOR, SWEEP_SIGMA_REL, DRIFT_SHARE,
                              DRIFT_SIGMA_W, CAP_SHAPE_SHARE, CAP_SHAPE_FACTOR,
                              CAP_SHAPE_REF_K], float))
    if key_only:
        # Returned from HERE rather than recomputed by a helper, so that the
        # key a caller checks is the key this function will use, by
        # construction.  A second implementation is a second thing to keep in
        # step, and the whole point of the check is that two paths had drifted.
        return key
    hit = cache_load(key, len(p0))
    if hit is not None:
        x, nfev = hit
    else:
        s = least_squares(resid, p0, method="trf", x_scale="jac",
                          max_nfev=max_nfev)
        x, nfev = s.x, s.nfev
        cache_store(key, x, nfev)
    pl, pc = split_p(x)
    split, mass_ratio = (unpack2(x[:-n_tail] if n_tail else x) if tier2
                         else (float("nan"),) * 2)
    model = run(x)
    # The objective's own value at the solution, 0.5*sum(r^2), which is what
    # least_squares actually minimises.  Reported because rms_k and anchor_k
    # are two WEIGHTED PARTS of it and can move in opposite directions -- two
    # seeds converging to different local minima is decided by this number and
    # by nothing else on the row.
    cost = 0.5 * float(np.dot(resid(x), resid(x)))
    # One ROW per record, so that a caller reading "the drift" gets a per-record
    # answer rather than a block it has to know the partition of.  min/max over
    # the whole thing still mean what they did, which is what plot_gain reads.
    drift_w = ((x[len(x) - n_tail:len(x) - n_camp] if n_camp
                else x[len(x) - n_tail:]).reshape(len(recs), n_drift)
               if n_drift else np.zeros(0))
    campaign_slope_w = (float(x[-1]) if n_camp
                        else (camp_fixed if campaign else 0.0))
    err = model - T
    # Weighted, so a decimated fit reports the same quantity a full-grid one
    # does: rms over TIME, not over samples.  Unweighted these agree exactly on
    # a uniform grid, which is why this was invisible before.
    wsq = w_sweep ** 2
    # Both of these are PER RECORD and then concatenated, and neither may be
    # computed on the flat arrays.  `opening_hold` reads u[0] and would call
    # the second record's opening a continuation of the first's; `np.gradient`
    # would straddle the join and report a slew of (T2 - T1) / (t2 - t1) across
    # a boundary where the two clocks have no relation at all.
    holds = [opening_hold(r.t, r.u) for r in recs]
    hold = np.concatenate(holds)
    moving = np.concatenate([np.abs(np.gradient(r.T, r.t)) > 2e-3
                             for r in recs])           # > 7.2 K/h
    hold_h = sum((r.t[m][-1] - r.t[m][0]) / 3600.0
                 for r, m in zip(recs, holds) if m.any())
    return {
        "tier2": tier2, "split": split, "mass_ratio": mass_ratio,
        "rms_moving_k": float(np.sqrt(np.mean(err[moving] ** 2))),
        "rms_still_k": float(np.sqrt(np.mean(err[~moving] ** 2))),
        "frac_moving": float(np.mean(moving)),
        "hold_k": float(np.sqrt(np.mean(err[hold] ** 2))),
        "hold_max_k": float(np.max(np.abs(err[hold]))),
        "hold_h": float(hold_h),
        "records": tuple(r.name for r in recs),
        "n_record": len(recs),
        "n_lam": n_lam, "n_cap": n_cap, "npar": len(x), "nfev": nfev,
        "n_anchor": len(aT), "anchors_twice": twice,
        "key": key, "cost": cost,
        "n_drift": n_drift, "drift_w": drift_w, "drift_t": dks,
        # The ramp, and the epoch it is zero at -- which is the date the whole
        # curve is quoted at, so nothing downstream may read one without the
        # other.  See campaign_power_w.
        "campaign": bool(campaign), "campaign_w_per_day": campaign_slope_w,
        "campaign_pinned": campaign_w is not None,
        "campaign_form": CAMPAIGN_FORM if campaign else "",
        "campaign_t_ref": t_ref,
        "drift_mw": float(1e3 * np.abs(drift_w).max()) if n_drift else 0.0,
        "lam": lam, "cap": cap, "pl": pl, "pc": pc,
        "t": t, "T": T, "Tc": Tc, "u": u, "model": model,
        "rms_k": float(np.sqrt(np.mean(wsq * err**2))),
        "max_k": float(np.max(np.abs(err))),
        "rms_pct": float(100 * np.sqrt(np.mean(wsq * (err / T) ** 2))),
        "anchor_k": float(np.sqrt(np.mean(
            ((lam(pl, aT) - lam(pl, aTc) - aQ) / lam.slope(pl, aT)) ** 2))),
        "tau_resid": float(np.sqrt(np.mean(
            (np.log(cap(pc, tauT) / lam.slope(pl, tauT)) - log_tau) ** 2))),
        "mass_g": float(cap(pc, np.array([CAP_SHAPE_REF_K]))[0]
                        / mix_c(np.array([CAP_SHAPE_REF_K]))[0]),
        "tau_137_s": float(cap(pc, np.array([CAP_SHAPE_REF_K]))[0]
                           / lam.slope(pl, np.array([CAP_SHAPE_REF_K]))[0]),
    }


def campaign_power_w(r, t_abs, q_w=None):
    """The fitted campaign load at absolute time(s) ``t_abs``, in watts.

    The one place the ramp's sign convention lives, because three call sites
    got it wrong independently when it was a per-era offset.  The anchor
    residual is ``Lambda(T_s) - Lambda(T_c) - Q - campaign``, so a dwell taken
    at date ``t`` sits on the reference curve at an EFFECTIVE power of
    ``Q + campaign(t)``.  The ramp is negative in the past -- the cryostat had
    more cooling then -- so a July dwell is drawn at a lower effective power
    than its heater readback, which is what makes it land on the same curve as
    a September one.

    ``q_w`` is the heat that was being delivered at that moment, and under
    ``CAMPAIGN_FORM == "power"`` it is REQUIRED, because the ramp is a fraction
    of it.  Refused rather than defaulted: a caller that forgets would get a
    silently zero offset on every dwell, which is the shape of "the ramp did
    nothing" and reads as a result.

    Returns zeros for a fit that had no ramp, so a caller need not branch.
    """
    t = np.asarray(t_abs, float)
    if not r.get("campaign"):
        return np.zeros_like(t)
    days = (t - r["campaign_t_ref"]) / 86400.0
    if r.get("campaign_form", CAMPAIGN_FORM) == "power":
        if q_w is None:
            raise SystemExit(
                "fit_ode: campaign_power_w needs q_w under CAMPAIGN_FORM = "
                "'power' -- the ramp is a fraction of the delivered heat, so "
                "without it there is nothing to take a fraction of.")
        days = days * (np.abs(np.asarray(q_w, float)) / CAMPAIGN_REF_W)
    return r["campaign_w_per_day"] * days


#: Vary one curve's freedom at a time.  A joint grid confounds the two and
#: hides the answer, which is that they are not equally constrained.
#: C is held at 4 knots while Lambda is freed, and Lambda at 9 while C is,
#: so each ladder frees one curve against the other's best available shape
#: rather than against a deliberately crippled one.
LADDER_LAMBDA = [(n, 4) for n in range(3, 11)]
LADDER_CAP = [(9, n) for n in range(2, 8)]


#: How many ladder rungs to fit at once.  The rungs are INDEPENDENT -- each is
#: a separate least_squares from its own seed, and none reads another's answer
#: -- so the ladder is embarrassingly parallel and was being run one at a time.
#:
#: Processes, not threads: the cost is `integrate`, a Python loop over 77,375
#: samples that holds the GIL the whole way, so threads would serialise exactly
#: the part that is slow.  Each worker re-reads the sweep from the gzipped
#: table, about a second against fits that take minutes.
#:
#: Capped at 4 rather than at the core count.  least_squares builds its
#: Jacobian by finite differences -- one extra integration per parameter -- and
#: the cryostat machine has a recorder to run as well.  `LADDER_WORKERS=1` in
#: the environment turns it off, which is what to do when a traceback needs to
#: be readable.
LADDER_WORKERS = int(os.environ.get("LADDER_WORKERS", "4"))

_HEAD = (f"{'knots L':>8}{'knots C':>8}{'par':>5}{'rms K':>9}{'max K':>9}"
         f"{'rms %':>8}{'hold K':>8}{'anchor K':>10}{'tau res':>9}"
         f"{'mass g':>8}{'tau137':>9}{'nfev':>6}{'s':>7}")


def _summary(r, axis, secs):
    return {"axis": axis, "n_lam": r["n_lam"], "n_cap": r["n_cap"],
            "npar": r["npar"], "rms_k": r["rms_k"], "max_k": r["max_k"],
            "rms_pct": r["rms_pct"], "anchor_k": r["anchor_k"],
            "mass_g": r["mass_g"], "tau_137_s": r["tau_137_s"],
            "tau_resid": r["tau_resid"], "hold_k": r["hold_k"],
            "hold_max_k": r["hold_max_k"], "nfev": r["nfev"], "secs": secs}


def _line(d):
    return (f"{d['n_lam']:>8}{d['n_cap']:>8}{d['npar']:>5}{d['rms_k']:>9.3f}"
            f"{d['max_k']:>9.3f}{d['rms_pct']:>8.2f}{d['hold_max_k']:>8.2f}"
            f"{d['anchor_k']:>10.2f}{d['tau_resid']:>9.3f}{d['mass_g']:>8.2f}"
            f"{d['tau_137_s']:>9.0f}{d['nfev']:>6}{d['secs']:>7.1f}")


@dataclass(frozen=True)
class FitSpec:
    """Everything needed to reproduce one fit, small enough to pickle.

    This exists because the ladder had **two ways of deciding what to fit**,
    and they were not the same one.  ``_worker`` called ``load_sweep()`` and
    ``load_anchors()`` itself and ignored everything its caller had prepared;
    ``ladder()`` used its ``data`` argument only on the serial path and only
    for ``t_max`` on the parallel one.  Today those happen to agree, because
    ``__main__`` passes the full-grid sweep, which is what the worker reloads
    -- so the bug is latent rather than live.  Hand the ladder the DECIMATED
    grid, or weights, or a drift term, and the four parallel rungs would have
    quietly fitted a different model from the serial ones and printed them in
    the same table.

    A spec is the fix: both paths build their inputs from the same small
    description through :func:`production_inputs`, and a worker carries the
    description rather than the data.  Keeping it small is still what lets the
    worker re-read the table instead of having 77,375 samples pickled to it
    per rung, which was the original and correct reason ``_worker`` reloaded.
    """

    n_lam: int
    n_cap: int
    decimated: bool = False
    t_max: float | None = None
    n_drift: int = 0
    campaign: bool = False
    tier2: bool = False
    max_nfev: int = MAX_NFEV

    def run(self, key_only=False):
        rec, anchors, taus = production_inputs(self.decimated, self.t_max)
        return fit(self.n_lam, self.n_cap, rec, anchors, taus,
                   max_nfev=self.max_nfev, tier2=self.tier2,
                   n_drift=self.n_drift, campaign=self.campaign,
                   key_only=key_only)

    def key(self) -> str:
        """The cache key :meth:`run` will use, without paying for the fit."""
        return self.run(key_only=True)


def _worker(spec):
    """One rung, in its own process.

    Returns only what the table needs.  A fit dict carries two spline objects
    and the whole 77,375-sample trajectory, and pickling those back across the
    pipe costs more than some of the arithmetic that made them.
    """
    import time
    t0 = time.time()
    return _summary(spec.run(), "", time.time() - t0)


def _worker_key(spec):
    """``spec.key()`` evaluated in a worker process.  See :func:`ladder`."""
    return spec.key()


def ladder(rows, title, out=None, workers=None, base=None):
    """One row per rung.  ``base`` is the :class:`FitSpec` the knots vary on."""
    import time
    axis = title.split()[2].rstrip(",")
    workers = LADDER_WORKERS if workers is None else workers
    base = base or FitSpec(0, 0)
    specs = [replace(base, n_lam=a, n_cap=b) for a, b in rows]
    print(f"\n{title}")
    print(_HEAD)
    got = []
    if workers > 1 and len(specs) > 1:
        from concurrent.futures import ProcessPoolExecutor
        wall = time.time()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            # REFIT_PLAN.md Phase B step 3 asks for this assertion, and it has
            # content even now that both paths run the same code: a worker is a
            # fresh interpreter with its own working directory, and
            # `_data.resolve` searches relative to the CWD as well as to
            # REPO_ROOT.  A worker that resolved a different `measured.csv`
            # would fit different anchors and say nothing -- which is
            # archive/AUDIT-2026-09-10.md finding 5 with a process boundary in it.  One
            # round trip, no fitting.
            here = specs[0].key()
            there = pool.submit(_worker_key, specs[0]).result()
            if here != there:
                raise SystemExit(
                    f"fit_ode: the ladder's two paths disagree about what they "
                    f"are fitting -- this process keys rung {rows[0]} as {here} "
                    f"and a worker keys it as {there}.  They resolve their "
                    f"inputs differently; check the working directory and "
                    f"_data.resolve before trusting any row of this table.")
            # map keeps the results in the order the rungs were given, so the
            # table still reads top to bottom however the workers finish.
            done = list(pool.map(_worker, specs))
        for d in done:
            d["axis"] = axis
            got.append(d)
            print(_line(d), flush=True)
        serial = sum(d["secs"] for d in done)
        print(f"  {len(rows)} rungs on {workers} workers: "
              f"{time.time() - wall:.0f} s wall, {serial:.0f} s of fitting")
    else:
        for spec in specs:
            t0 = time.time()
            d = _summary(spec.run(), axis, time.time() - t0)
            got.append(d)
            print(_line(d), flush=True)
    if out is not None:
        out.extend(got)
    return got


LADDER_CSV = "analysis/ladder.csv"


def seed_report(n_lam=20, n_cap=4, decimated=True) -> int:
    """Print Lambda and C as the anchors give them, with no ``least_squares``.

    REFIT_PLAN.md Phase B step 6.  A one-second sanity check on a
    fifteen-minute fit, and the closest thing in this repository to a
    model-free reading of the cryostat: every number below comes from
    ``Lambda(T_s) - Lambda(T_c) = Q`` at a settled dwell and ``C = tau
    dLambda/dT`` at a measured relaxation, with no ODE integrated anywhere.
    """
    rec, anchors, taus = production_inputs(decimated)
    bT, bL = measured_lambda(anchors)
    lam_of = PchipInterpolator(bT, bL, extrapolate=True)
    slope = lam_of.derivative()
    cT, cC = measured_capacity(taus, lam_of)
    cap_at = _capacity_seed(cT, cC)
    c_ref = mix_c(np.array([CAP_SHAPE_REF_K]))[0]
    c137 = float(cap_at(CAP_SHAPE_REF_K)[0])

    print(f"{len(anchors)} anchors -> {len(bT)} bins; "
          f"{len(taus[0])} taus -> {len(cT)} bins over "
          f"{cT[0]:.1f}-{cT[-1]:.1f} K")
    print("Lambda is on the anchors' own gauge, Lambda(T_c) = 0, and the level "
          "is NOT searched:\n  it is a null direction of the data and of the "
          "prior, and searching it cost 3.8x the\n  iterations for the same "
          "optimum -- see measured_lambda.")
    print(f"C({CAP_SHAPE_REF_K:.0f} K) = {c137:.4f} J/K "
          f"= {c137 / c_ref:.3f} g of the Debye mix\n")
    print(f"{'T K':>9}{'Lambda W':>11}{'dL/dT W/K':>12}{'K/W':>9}"
          f"{'C J/K':>9}{'tau s':>9}  C from")
    for T, L in zip(bT, bL):
        g = float(slope(T))
        C = float(cap_at(T)[0])
        how = "taus" if cT[0] <= T <= cT[-1] else "Debye"
        print(f"{T:>9.2f}{L:>11.4f}{g:>12.5f}{1.0 / g if g else float('nan'):>9.1f}"
              f"{C:>9.4f}{C / g if g else float('nan'):>9.1f}  {how}")
    print("\nLambda is quoted on the anchors' own gauge, Lambda(T_c) = 0.  "
          "K/W is 1/Lambda',\n  the local thermal resistance, and tau is "
          "C/Lambda' -- both independent of the gauge.")
    return 0


if __name__ == "__main__":
    import argparse

    _ap = argparse.ArgumentParser(description="the ODE fit and its ladder")
    _ap.add_argument("--seed-only", action="store_true",
                     help="print Lambda and C read off the anchors, and stop")
    _a = _ap.parse_args()
    if _a.seed_only:
        raise SystemExit(seed_report())

    # The ladder is the complexity study and is deliberately the PLAIN fit:
    # full grid, no weights, no drift term and no per-era offset.  Adding any
    # of them here would confound the thing the ladder exists to separate.
    base = FitSpec(0, 0, decimated=False)
    rec, anchors, taus = production_inputs(base.decimated, base.t_max)
    print(f"sweep   {len(rec)} samples at {STEP_S:.0f} s, "
          f"{rec.t[-1] / 3600:.1f} h, {rec.T.min():.1f}-{rec.T.max():.1f} K")
    print(f"anchors {len(anchors)} settled dwells, "
          f"{anchors.T.min():.1f}-{anchors.T.max():.1f} K")
    print(f"taus    {len(taus[0])} measured, "
          f"{taus[0].min():.1f}-{taus[0].max():.1f} K")
    rows = []
    ladder(LADDER_LAMBDA, "freedom in Lambda, C held at 4 knots", rows, base=base)
    ladder(LADDER_CAP, "freedom in C, Lambda held at 9 knots", rows, base=base)
    with open(LADDER_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\nwrote", LADDER_CSV)
