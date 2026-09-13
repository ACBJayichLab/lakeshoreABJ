"""Fit every constant-heater dwell as a single-pole relaxation.

The earlier extractor took the mean of a hold's last 20 minutes and demanded
the hold be an hour long.  That is the right test when tau is ten minutes and
the wrong one everywhere else, and it threw away most of the sweep: below
100 K tau is seconds, so a three-minute dwell is already tens of tau and is
settled to well under the sensor noise.

Fitting the dwell instead of averaging it gives three things where averaging
gave one:

``T_inf``
    the steady state the dwell was heading for, extrapolated rather than
    approximated by wherever it happened to have got to.  This is a point on
    ``Lambda`` exactly as a long hold is.
``tau_s``
    the local time constant, measured directly.  With ``Lambda`` known this
    turns C into a measurement -- ``C = tau * dLambda/dT`` -- instead of the
    barely-constrained fit parameter it is when only the trajectory sees it.
``reach``
    ``span / tau``, how many time constants the dwell actually ran.  A fit
    over less than about 3 tau returns tau far too small at an R^2 that still
    reads as healthy (see docs/ltspm3/commissioning.md), so this is the column
    that decides whether a tau may be believed, and R^2 is not.

The fit is ``T(t) = T_inf + A exp(-t/tau)``: nonlinear in tau alone, so tau is
found by 1-D search with (T_inf, A) solved exactly at each trial.  That has no
starting-guess failure mode, which matters across 500 dwells nobody will look
at individually.
"""
from __future__ import annotations

import csv
import math

import numpy as np
from scipy.optimize import minimize_scalar

import segments as S
from _data import open_table

R_OHM, V_FS, GAIN = 75.5, 10.0, 1.11

#: Sensor noise, from docs/ltspm3/thermal-response.md: quadratic in T,
#: floored near 1.8 mK.  Used to decide whether a dwell HAS a transient worth
#: fitting and whether its residual is at the measurement limit.
NOISE_FLOOR_K = 0.0018
NOISE_QUADRATIC = 1.36e-6

#: A dwell must run this many time constants before its tau is believed.
MIN_REACH = 3.0
#: ...and its transient must be this many times the sensor noise, or there is
#: nothing to fit a time constant to and only T_inf survives.
#:
#: It has to be a big number, not a marginal one.  A 70 s dwell at 154 K with a
#: 0.19 K transient -- six sigma, reach 3.5 -- reported tau = 20 s where every
#: neighbour says 600 s: with a window far shorter than the real tau, the fit
#: has nothing but noise to work with and obligingly fits it.  reach alone
#: cannot catch that, because reach is computed from the tau being tested.
MIN_AMPLITUDE_SIGMA = 20.0

#: ...and it must not have run SO long that the relaxation was over before the
#: window ended, because then a pole has nothing left to fit but the cryostat's
#: drift.
#:
#: "Fitting many hours for a tau is a bad idea.  The response time is clearly
#: minute scale" (Jeff, 2026-09-13).  A window of 20 time constants has
#: e^-20 = 2e-9 of its transient left in the last sample; anything past that is
#: the slow wander of REFIT_PLAN.md section 6.1, which is exactly what put four
#: graded anchors 0.4-1.7 K out when a pole was fitted to a HOLD's level.  The
#: same disease, one column over.
#:
#: **20 sits in a gap the data has anyway**: over the 38 graded relaxations in
#: the archive, reach runs 3.4 to 16.7 and then jumps to 32.5, 33.9, 40.9 and
#: up to 471.  Nothing lands between 17 and 32.  The bar removes 12 of them,
#: all holds or near-holds, including the 33 h window at 118.3 K whose tau came
#: back 712 s where a clean 2.6 K step at the same temperature -- 40 mK away --
#: measures 524.7 +- 4.5 s.
#:
#: A dropped dwell is still an ANCHOR: only its tau is refused, its steady
#: state is untouched, and `load_anchors` never looked at this.
MAX_REACH = 20.0

#: ...and its excursion must be narrow enough that ONE time constant describes
#: the whole of it, as a fraction of where it ended up.
#:
#: "The step spans more than the region valid for a single step" (Jeff,
#: 2026-09-13), of ``pc-20260905-111947``: 83.0 -> 68.4 K, a 14.5 K COOLING
#: excursion whose neighbours in the same ladder are 6.8 and 7.2 K steps up.
#: tau is C/Lambda' and both move with temperature -- across that span the
#: fitted model says tau changes by about half -- so there is no single pole to
#: find.  A relaxation is dated by where it ENDS, so what the fit returns
#: belongs somewhere in the middle of the excursion and is then scored at the
#: bottom of it; that one window is 24 % off the model where its neighbours are
#: within 2.5 %.
#:
#: **It is a proxy and is labelled one.**  The quantity that actually matters
#: is how much tau(T) changes between ``T_lo`` and ``T_hi``, and that cannot be
#: asked here: :func:`plant_clock` is built FROM the graded relaxations, so a
#: window wide enough to be doubted is also the one bending the clock it would
#: be tested against -- measured, and the ratio comes back 1.12 for the 14.5 K
#: excursion against 1.50 for a 6 K step that is fine.  Amplitude over
#: temperature is uncontaminated and separates cleanly: 21.3 % and 19.4 % for
#: the two wide excursions in the archive, then a factor-of-two gap down to
#: 9.7 % for everything else.  0.15 sits in that gap.
MAX_AMPLITUDE_FRAC = 0.15
#: A "steady" point extrapolates from where the dwell ended to T_inf.  Past
#: this much extrapolation it is a prediction of the model being fitted, not a
#: measurement of the cryostat.
MAX_SETTLE_K = 2.0

#: The one ADMISSION threshold: fewer samples than this and there is not enough
#: to fit two parameters and a pole to, whatever the dwell lasted.
#:
#: It is a count and not a duration on purpose.  What a fit needs is samples per
#: time constant, and MIN_N against the log's own cadence already says that --
#: 15 samples is 30 s at the recorder's 2 s and 120 s at the chart recorder's
#: 8 s, which is the right way round, because the slow log is the old one where
#: tau was long.
MIN_N = 15

#: A wall clock, and it is now only the FIRST PASS's stand-in for one.
#:
#: It used to be an admission threshold beside MIN_N, upstream of ``grade()``
#: and so invisible to it, rejecting any dwell under 60 s before the grader
#: could speak.  On 2026-09-05 that threw away **sixteen** rungs of the
#: programmed sweep: 46-48 s each, which at 5-25 K is **11.5 time constants**,
#: with a remainder of 0.000 K, end rates of 0.001-0.052 K/h against a 0.5 bar,
#: and transients 60 to 1265 times the sensor noise.  They are the best-settled
#: points in the whole archive and a clock threw them out.  (The docstring here
#: said "three good rungs".  It was sixteen, and the thirteen it missed are the
#: ones the earlier cancelled run happens to duplicate -- so the loss looked
#: smaller than it was.)  Worse, the fix at the time propagated the clock
#: outward: ``ltspm3/tools/sweep.py`` began REFUSING ``--min-dwell`` below 60 s,
#: so the tool was forbidden from doing the thing it had done well.
#:
#: What the clock was really protecting against is one specific failure, and it
#: is not shortness.  It is a dwell whose fitted pole **cannot be believed**:
#: over 48 s at 145 K, where tau is about 600 s, the sample moved 80 mK against
#: 28 mK of sensor noise, so the pole was fitted to noise, came back tau = 8.6 s,
#: and ``reach`` -- computed from that tau -- read 5.6 and certified as settled a
#: window that was 1/12 of a time constant into an 8-hour relaxation still
#: 0.76 K from its answer.  ``reach`` cannot catch that, because reach is
#: computed from the tau being tested.  See :func:`pole_unbelievable`.
#:
#: **A duration cannot answer the question it is standing in for**, and that is
#: not an opinion: measured, this bar leaks at exactly its own value, certifying
#: a 60 s dwell at 145 K with 0.76 K still to go 16 times in 200.  The real
#: question is whether the dwell ran several of the PLANT's time constants, on a
#: plant whose tau spans a factor of five thousand.
#:
#: So it is asked, here as well as in the sweep tool.  ``analysis/`` cannot
#: import a plant model -- invariant 1 -- and it does not need to: it MEASURES
#: one.  The dwells that resolved their own transients carry tau over
#: 25.8-247.6 K, and :func:`plant_clock` interpolates them, so
#: :func:`long_enough` asks ``MIN_REACH * tau_plant`` exactly as
#: ``sweep.py``'s does.  This bar survives as that function's fallback: the
#: first of :func:`archive_dwells`' two passes has no plant clock yet, and a
#: caller grading one dwell in isolation has none either.  **It is a proxy
#: wherever it is still reached**, and the only thing that makes it harmless in
#: the first pass is that no ``tau`` grade can depend on it -- see
#: :func:`archive_dwells`.
#:
#: The two graders no longer diverge on this test.  They did between
#: AUDIT-2026-09-10-REJOINDER.md step 1 and step 2, on purpose and for one
#: reason -- ``ltspm3`` had a plant model and this module did not.  Phase A gave
#: this module a measured one.  See REFIT_PLAN.md section 6.2 option 4.
MIN_SPAN_S = 60.0

TIME_KEYS = ("t_s", "Time")
HEATER_KEYS = ("u_pct", "ls218.aout1", "heater_pct")

#: How far the reported heater may wander before a dwell is deemed to have
#: ended, in percent.
#:
#: NOT the DAC resolution.  The 218's AOUT? readback flickers between adjacent
#: codes -- 52.496 / 52.499 alternating sample to sample -- so an exact test,
#: or one at the 0.001% resolution, shreds a twenty-minute dwell into a
#: hundred one-sample runs and every dwell below 100 K disappears.  The
#: smallest deliberate step anywhere in this data is 0.1%, so 0.02% separates
#: flicker from intent with a factor of five in hand either way.
U_TOL_PCT = 0.02


def noise_k(T):
    return max(NOISE_FLOOR_K, NOISE_QUADRATIC * T * T)


def power_w(u):
    return (GAIN * V_FS * u / 100.0) ** 2 / R_OHM


def _pick(header, keys):
    for k in keys:
        if k in header:
            return k
    return None


def load(path):
    """(t, T_sample, T_coldplate, u, segment, timestamps) from any recorder-shaped CSV."""
    with open_table(path) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return None
    tk = _pick(rows[0], TIME_KEYS)
    uk = _pick(rows[0], HEATER_KEYS)
    if tk is None or uk is None:
        raise SystemExit(f"{path}: no time or heater column")

    def col(key, cast=float):
        out = []
        for r in rows:
            try:
                out.append(cast(r[key]))
            except (TypeError, ValueError, KeyError):
                out.append(math.nan)
        return np.array(out)

    seg = (col("segment") if "segment" in rows[0]
           else np.zeros(len(rows)))
    t, T, Tc, u = col(tk), col("Sample"), col("Coldplate"), col(uk)
    ok = ~(np.isnan(t) | np.isnan(T) | np.isnan(u))
    stamps = [r.get("Timestamp", "") for r in rows]
    return (t[ok], T[ok], Tc[ok], u[ok], seg[ok],
            [s for s, k in zip(stamps, ok) if k])


def dwells(t, T, Tc, u, seg, stamps, min_span_s=0.0, min_n=MIN_N):
    """Maximal runs of constant u inside one segment; see U_TOL_PCT.

    ``min_span_s`` defaults to nothing.  A duration bar here would run upstream
    of :func:`grade` and be invisible to it, which is how sixteen rungs of the
    2026-09-05 sweep were thrown away without ever getting a verdict -- see
    ``MIN_SPAN_S``, which is now a conditional test inside :func:`settled`
    instead.  Left as a parameter because it is a useful knob for asking what a
    bar would have cost.
    """
    out = []
    start = 0
    for i in range(1, len(t) + 1):
        end = i == len(t)
        if not end and abs(u[i] - u[start]) <= U_TOL_PCT and seg[i] == seg[start]:
            continue
        n = i - start
        if n >= min_n and t[i - 1] - t[start] >= min_span_s:
            out.append((start, i))
        start = i
    return out


#: How close to a search bound counts as sitting ON it.  5 %, far tighter than
#: the factor of two between adjacent candidate taus and loose enough to
#: survive the optimiser stopping just inside its bracket.
POLE_PIN_TOL = 0.05

#: The tau search runs from this many samples of the log's own cadence...
POLE_TAU_MIN_SAMPLES = 2.0
#: ...to this many times the dwell's span.  The upper end is deliberately loose
#: so that a dwell which has NOT settled says so by returning a tau longer than
#: itself, instead of being clipped into looking settled.
POLE_TAU_SPAN_FACTOR = 20.0


def pole_floor(r) -> bool:
    """Is this dwell's tau sitting on the bottom of the search interval?

    Then it is not a measurement.  The floor is two samples of the log's own
    cadence, so a result there says "faster than I can see" -- and the archive
    has 48 graded dwells at exactly 4.0 s, 8 of them graded ``tau``, at 5.4 to
    25.1 K where the plant's own time constant runs from under 0.1 s to 3.4 s.
    Those eight were entering ``fit_ode``'s objective as time-constant
    residuals of nine sigma in log tau, pulling C(T) at precisely the cold end
    REFIT_PLAN.md says has the least leverage to spare.

    The steady state of a fast relaxation is still real, so a floor pin keeps
    its ``steady``.  It loses only its ``tau``.

    **It loses only its tau even when there was no resolvable transient
    either**, and that is a deliberate departure from
    AUDIT-2026-09-10.md finding 2, which proposes refusing any grade there on
    the grounds that such a pole was fitted to noise.  Sometimes it was.  But a
    dwell that is genuinely finished is ALSO flat and also has no transient,
    and it is the best kind of anchor there is --
    ``test_a_flat_dwell_is_steady_but_carries_no_believable_tau`` pins exactly
    that case, 600 s flat to 0.5 mK at 42 K, and the refusal breaks it.

    Nothing in this window separates the two.  What separates them is the
    plant's tau at that temperature: 600 s of flat at 42 K is many time
    constants, 200 s of flat at 147 K is a third of one.  ``MIN_SPAN_S`` used to
    be the only test available and it catches the short version while missing
    the archive's two real cases, at 147.1 and 170.4 K over 200 s and 330 s.
    :func:`long_enough` on a MEASURED plant clock catches both -- they score
    reach 0.33 and 0.49 against ``MIN_REACH`` -- and keeps the 42 K case, which
    is what a wall clock could never do.  Their steady states are refused now
    rather than merely doubted; ``measure.py`` still reports ``sigma_tau_s`` of
    214 % and 103 % of tau beside them.
    """
    return r["tau_s"] <= r["tau_lo"] * (1.0 + POLE_PIN_TOL)


def pole_ceiling(r) -> bool:
    """Is this dwell's tau sitting on the top of the search interval?

    Then the exponential has degenerated to a straight line, and ``reach``,
    ``remainder_K`` and ``end_rate_k_per_h`` -- all read off that tau -- mean
    nothing.  46 dwells in the archive are here and 6 of them are graded.

    **Not refused on its own**, which is the deliberate difference from the
    floor case.  AUDIT-2026-09-10.md finding 2 proposes refusing every ceiling
    pin.  Measured against the archive that would drop four windows that are
    settled -- two long holds drifting under 4 mK/h, and two dwells at 4.8 and
    5.1 K where the plant's tau is under a tenth of a second -- to catch two
    that are doubtful.  A straight-line slope cannot separate them either:
    every graded ceiling pin drifts under 0.5 K/h, so a slope bar changes no
    verdict at all.

    What separates a settled drift from a truncated relaxation is the plant's
    tau at that temperature, so a ceiling pin is one of the two ways a pole
    becomes :func:`pole_unbelievable` and has to answer to :func:`long_enough`
    instead.  That separates the six perfectly against the existing
    ``MIN_REACH``: the four settled ones score 7.2 to 101, the two doubtful
    ones 1.88 and 0.35.  REFIT_PLAN.md section 6.2 option 4.

    Phase A's ``measure.py`` fits the long ones with level and drift instead of
    a pole, which is what makes them anchors worth keeping at all -- section 6.1.
    """
    return r["tau_s"] >= r["tau_hi"] * (1.0 - POLE_PIN_TOL)


def pole_unbelievable(r) -> bool:
    """Are ``reach``, ``remainder_K`` and ``end_rate_k_per_h`` meaningless?

    All three are computed FROM the fitted tau, so they are only as good as it
    is, and there are two ways for it to be worthless.  The dwell had no
    resolvable transient, so the pole was fitted to noise -- 48 s at 145 K moved
    80 mK against 28 mK of sensor noise, came back tau = 8.6 s, and ``reach``
    read 5.6 on a window one twelfth of the way into an eight-hour relaxation.
    Or tau is at the top of the search, where the exponential has degenerated
    into a straight line and the rate test passes on any transient under
    ``span / 342`` K whatever the plant is doing.

    Both fail OPTIMISTICALLY, which is why they are worth naming together, and
    naming them together is what lets one guard cover both.  Mirrors
    ``ltspm3/tools/sweep.py``'s property of the same name.
    """
    return r["amp_sigma"] < MIN_AMPLITUDE_SIGMA or pole_ceiling(r)


def long_enough(r, tau_plant_s=None, min_reach=None) -> bool:
    """Did this dwell run several of the PLANT's time constants?

    The one honest question to ask when the fitted pole cannot answer anything.
    ``tau_plant_s`` comes from :func:`plant_clock`, which measures it from the
    dwells that DID resolve their own transients, so ``MIN_REACH`` time
    constants is a real requirement in the plant's own units rather than a
    duration somebody picked.

    Falls back to ``MIN_SPAN_S`` with no plant clock -- the first of
    :func:`archive_dwells`' two passes, or a caller grading one dwell alone.
    That bar is a proxy and is labelled one where it is defined.
    """
    if tau_plant_s is not None and tau_plant_s > 0.0 and math.isfinite(tau_plant_s):
        return r["span_s"] >= (MIN_REACH if min_reach is None else min_reach) * tau_plant_s
    return r["span_s"] >= MIN_SPAN_S


def plant_clock(rows):
    """``tau(T)`` for the cryostat, MEASURED -- or ``None`` if nothing measured it.

    Log-log interpolation through the ``(T_inf, tau_s)`` of every dwell graded
    ``tau``, which is the only plant model ``analysis/`` is allowed: it is this
    archive's own dwells rather than anything imported, so invariant 1 holds and
    there is no second copy of ``ltspm3``'s curve to drift out of step.

    **It is not circular**, and that is a property of the grader rather than a
    hope.  A ``tau`` grade requires ``reach >= MIN_REACH``, ``amp_sigma >=
    MIN_AMPLITUDE_SIGMA`` and no floor pin, so no dwell that :func:`long_enough`
    is ever asked about can be in the set that defines it: a ceiling pin has
    ``reach <= 1/19`` by construction and a noise pole fails the amplitude bar.
    The set that builds the clock and the set tested against it are disjoint,
    and :func:`archive_dwells` asserts it every run rather than trusting this
    paragraph.

    Outside the measured band the nearest anchor is held rather than
    extrapolated.  At the cold end that OVERSTATES tau -- 5.19 s at 5 K, where
    the plant's own is nearer 0.01 s -- and overstating it makes
    :func:`long_enough` stricter, so the conservative direction is the one a
    clamp gives for free.  The two cold ceiling pins still pass at reach 7.2 and
    15.0; against the real curve they would score in the thousands, which is the
    margin the clamp spends.

    Which construction is used barely matters, and that was measured rather than
    assumed: the interpolant built from all 37 tau anchors, from the 29 whose
    span is under ``curate.HOLD_MIN_S`` (whose poles carry no campaign drift),
    and from ``measured.csv``'s own tau column (holds fitted with level and
    drift) give the SAME verdict on all 44 rows the guard is asked about.  So
    this reads the rows it just fitted and not ``analysis/measured.csv``, which
    is gitignored and therefore absent from a fresh clone -- a grader whose
    verdicts depend on a derived file that a checkout does not have is a
    manifest nobody else can reproduce.
    """
    pairs = sorted((r["T_inf"], r["tau_s"]) for r in rows
                   if r["tau_s"] > 0.0 and r["T_inf"] > 0.0)
    if len(pairs) < 2:
        return None
    lt = np.log(np.array([p[0] for p in pairs]))
    ltau = np.log(np.array([p[1] for p in pairs]))

    def tau_of(T):
        if not (T > 0.0):
            return math.nan
        return float(np.exp(np.interp(math.log(T), lt, ltau)))

    tau_of.band = (pairs[0][0], pairs[-1][0])
    tau_of.n = len(pairs)
    return tau_of


def pole_bounds(t):
    """``(lo, hi)`` -- the interval :func:`fit_pole` searches tau on.

    Exposed, and not inlined where it is used, because **a tau AT one of these
    bounds is the search saying "outside what I can see" and neither grader
    notices** (AUDIT-2026-09-10.md, finding 2).  Anything that wants to test
    for that has to be able to ask what the bounds were, and a caller that
    recomputes them from its own copy of the two factors is one edit away from
    testing against the wrong number.
    """
    return (max(POLE_TAU_MIN_SAMPLES * float(np.median(np.diff(t))), 1.0),
            POLE_TAU_SPAN_FACTOR * float(t[-1] - t[0]))


def fit_pole(t, y):
    """T(t) = T_inf + A exp(-t/tau).  Returns (T_inf, A, tau, rms)."""
    t = t - t[0]

    def solve(tau):
        e = np.exp(-t / tau)
        M = np.column_stack([np.ones_like(e), e])
        coef, *_ = np.linalg.lstsq(M, y, rcond=None)
        return coef, float(np.sqrt(np.mean((M @ coef - y) ** 2)))

    lo, hi = pole_bounds(t)
    r = minimize_scalar(lambda lt: solve(math.exp(lt))[1],
                        bounds=(math.log(lo), math.log(hi)), method="bounded")
    tau = math.exp(r.x)
    coef, rms = solve(tau)
    return float(coef[0]), float(coef[1]), tau, rms


def _row(source, t_start, t_end, tt, yy, uu, cc):
    """One dwell, fitted.  The only place these columns are computed."""
    span = tt[-1] - tt[0]
    T_inf, A, tau, rms = fit_pole(tt, yy)
    # The interval the search ran on, carried on the row so that grade() can
    # tell a measurement from the search giving up.  A caller recomputing these
    # from its own copy of the two factors is one edit away from testing
    # against the wrong number.
    tau_lo, tau_hi = pole_bounds(tt - tt[0])
    sigma = noise_k(float(np.mean(yy)))
    return {
        "source": source,
        "t_start": t_start,
        "t_end": t_end,
        "span_s": span,
        "n": len(tt),
        "u_pct": float(np.mean(uu)),
        "P_W": power_w(float(np.mean(uu))),
        "T_inf": T_inf,
        "T_end": float(yy[-1]),
        "settle_K": T_inf - float(yy[-1]),       # how far it still had to go
        "T_lo": float(np.nanmin(yy)),
        "T_hi": float(np.nanmax(yy)),
        "tau_s": tau,
        "tau_lo": tau_lo,
        "tau_hi": tau_hi,
        "reach": span / tau,
        "amp_K": abs(A),
        "amp_sigma": abs(A) / sigma,
        "rms_K": rms,
        "rms_sigma": rms / sigma,
        # |dT/dt| at the last sample, from the fitted pole
        "end_rate_k_per_h": 3600.0 * abs(A) / tau * math.exp(-span / tau),
        # ...and how far it still had to travel, from the same pole.  This
        # is settle_K without the last sample's noise in it; see
        # SETTLED_REMAINDER_K.
        "remainder_K": abs(A) * math.exp(-span / tau),
        "Coldplate": float(np.nanmean(cc)),
    }


def analyse(path, label=None):
    """Every dwell in one recorder-shaped CSV.  For a file off the archive."""
    t, T, Tc, u, seg, stamps = load(path)
    return [_row(label or path.replace("\\", "/").rsplit("/", 1)[-1],
                 stamps[a][:19], stamps[b - 1][:19],
                 t[a:b], T[a:b], u[a:b], Tc[a:b])
            for a, b in dwells(t, T, Tc, u, seg, stamps)]


def archive_dwells(masks_by_file=None):
    """Every dwell in the cooldown-10 archive, fitted and graded.

    The one scan.  ``curate`` builds the manifest from this, so nothing else
    needs a loop of its own -- which is what keeps the manifest and the
    measurement from disagreeing about which dwells exist.

    ``masks_by_file`` maps an archive table to the manifest's ``mask`` windows
    in it.  A dwell is cut at a mask's edges and one lying inside a mask is
    dropped; see ``curate``'s docstring for why that is an input here rather
    than a filter afterwards.

    **Two passes, because the grader needs a clock the graded dwells provide.**
    Pass 1 grades every dwell with no plant clock, so :func:`long_enough` falls
    back to ``MIN_SPAN_S``; :func:`plant_clock` is then built from whatever came
    out ``tau``; pass 2 re-grades with it.  The second pass is what
    REFIT_PLAN.md section 6.2 option 4 asks for and it is the verdict that
    reaches the manifest.

    The iteration terminates after exactly one round, and not by luck.  A
    ``tau`` grade needs ``reach >= MIN_REACH`` and ``amp_sigma >=
    MIN_AMPLITUDE_SIGMA``, which is precisely the negation of
    :func:`pole_unbelievable` plus a bound no ceiling pin can meet -- so the
    guard the two passes differ by cannot touch a ``tau`` verdict, the clock
    pass 2 would build is the clock pass 2 used, and a third pass would change
    nothing.  That is the same fact as "the clock is not circular", and the
    assertion below is it, checked rather than argued.
    """
    masks_by_file = masks_by_file or {}
    out = []
    for file in S.TABLES:
        table = S.read_table(file)
        # A distinct segment id over a mask makes dwells() break at both of its
        # edges -- it already refuses to run a dwell across a segment change,
        # which is the same requirement -- and marks the masked rows so the
        # dwell inside them can be dropped.
        seg = table.segment.copy()
        for i, m in enumerate(masks_by_file.get(file, []), start=1):
            seg[table.slice(m.start, m.end)] = -i
        ok = ~(np.isnan(table.epoch) | np.isnan(table.T) | np.isnan(table.u))
        t, T, Tc, u, sg = (table.epoch[ok], table.T[ok], table.Tc[ok],
                           table.u[ok], seg[ok])
        stamps = [""] * len(t)               # dwells() only passes these along
        for a, b in dwells(t, T, Tc, u, sg, stamps):
            if sg[a] < 0:
                continue                     # inside a mask
            r = _row(file, S._iso(t[a]), S._iso(t[b - 1]),
                     t[a:b], T[a:b], u[a:b], Tc[a:b])
            r["file"] = file
            r["era"] = S.era(file)
            r["grade"] = grade(r)                # pass 1: no plant clock yet
            out.append(r)

    clock = plant_clock([r for r in out if r["grade"] == "tau"])
    was_tau = {(r["file"], r["t_start"]) for r in out if r["grade"] == "tau"}
    for r in out:
        tp = clock(r["T_inf"]) if clock else math.nan
        r["tau_plant_s"] = tp
        r["reach_plant"] = r["span_s"] / tp if tp and math.isfinite(tp) else math.nan
        # How wrong tau_plant would have to be for this dwell's verdict to
        # flip, as a factor: >1 keeps it, <1 drops it, and near 1 is the row a
        # reviewer has to look at rather than wave through.
        r["plant_margin"] = (r["reach_plant"] / MIN_REACH
                             if math.isfinite(r["reach_plant"]) else math.nan)
        r["grade"] = grade(r, tp)                # pass 2: on the plant's clock
    now_tau = {(r["file"], r["t_start"]) for r in out if r["grade"] == "tau"}
    if now_tau != was_tau:
        raise SystemExit(
            f"steps: the plant clock changed {len(was_tau ^ now_tau)} tau "
            f"verdict(s), so it depends on itself and the manifest it produces "
            f"is not reproducible.  A tau grade must be independent of "
            f"long_enough() -- see plant_clock() and archive_dwells().")
    return out


#: The question a dwell has to answer is "were you still moving when you
#: ended", and this is the rate at which it was, in K/h, read off the fitted
#: pole at the last sample.
#:
#: A RATE ALONE CANNOT ANSWER IT ON THIS CRYOSTAT, which is what
#: SETTLED_REMAINDER_K below is for.  tau runs from a few seconds at 10 K to
#: about 500 s at 115 K, a factor of 500, so the same K/h means opposite things
#: at the two ends: at 70 K, 0.6 K/h is 0.04 K of travel left and a minute to
#: do it in; at 115 K it is real movement still to come.  Read as a bar on its
#: own it kept a 68 K dwell that had run 1.3 time constants and dropped a 70 K
#: dwell that had run 5.3 and was within 0.04 K of its answer.
#:
#: The 2026-09-05 ladder settled that empirically.  Its 114.28 K rung was
#: rejected here for 0.71 K/h; the cryostat then sat at that same output for
#: 69.9 h and came to rest at 114.396 K.  The rejected extrapolation was right
#: to 0.11 K.
#:
#: It replaces a test on total amplitude, which asked "did you move at all" and
#: got the long holds exactly backwards.  A 22.8 h hold at 180 K that drifts
#: 0.50 K -- fitting at 0.99 sigma, the sensor noise floor -- has an amplitude
#: just over the old 0.5 K bar, so the single-pole fit called that drift a
#: relaxation with tau = 19 DAYS, reach collapsed to 0.05, and the best anchor
#: in the whole dataset was thrown away.  Slow drift and an unfinished
#: relaxation both give reach < 1; what separates them is how fast the sample
#: was still moving at the end, and there the two are five orders of magnitude
#: apart:
#:
#:     22.8 h at 180.07 K      0.001 K/h    settled, keep
#:     74.9 h at 150.42 K      0.003 K/h    settled, keep
#:     74 s  at  53.86 K     231     K/h    cut off mid-relaxation, drop
#:     170 s at 116.89 K      31     K/h    cut off mid-relaxation, drop
MAX_END_RATE_K_PER_H = 0.5

#: ...and the other way to answer the same question, for a dwell that was
#: DESIGNED rather than found: it ran at least MIN_REACH time constants and the
#: fitted pole says this little is left to go.  Either test passing is enough.
#:
#: The remainder, not ``settle_K``.  They are the same distance measured two
#: ways -- ``remainder_K`` is the fitted curve's own ``|A| exp(-span/tau)``,
#: ``settle_K`` is ``T_inf`` minus the LAST SAMPLE -- and at 114 K one sample
#: carries about 30 mK of noise, which is a third of the bar.  The 114.28 K
#: rung reads 0.042 K one way and 0.101 K the other for that reason alone.  A
#: threshold has to be applied to the smooth one.
#:
#: 0.15 K, and it is tied to something rather than chosen: it is half
#: ``fit_ode.ANCHOR_FLOOR_K``, so an admitted dwell's extrapolation lands
#: exactly at the smallest error bar the fit gives any anchor
#: (``own = max(0.3, 2*settle)``).  A remainder the error model already covers
#: cannot change an answer.  Measured, it also sits in a gap: the dwells this
#: admits run 0.001-0.139 K, the ones it does not, 0.225 and 0.393.
#:
#: Both halves are needed.  Reach alone would admit a 70 s dwell whose fitted
#: tau is 20 s because the fit had nothing but noise to work with; the
#: remainder alone would admit a slow drift that has barely started.  Together
#: they say "this relaxation is over", which is the only thing a steady point
#: has to be true of, and they say it in units the plant sets rather than in
#: K/h, which it does not.
SETTLED_REMAINDER_K = 0.15


def settled(r, tau_plant_s=None):
    """Is this dwell's relaxation over?  Either test may answer yes.

    Unless the pole cannot be believed at all -- see :func:`pole_unbelievable` --
    in which case the dwell first has to have run long enough on the PLANT's
    clock (:func:`long_enough`) before its own numbers get a hearing.
    """
    if pole_unbelievable(r) and not long_enough(r, tau_plant_s):
        return False
    return (r["end_rate_k_per_h"] <= MAX_END_RATE_K_PER_H
            or (r["reach"] >= MIN_REACH
                and r["remainder_K"] <= SETTLED_REMAINDER_K))


def grade(r, tau_plant_s=None):
    """'tau' if the time constant may be believed, 'steady' if only T_inf, else ''.

    Four ways a relaxation can fail to be a time-constant measurement while
    still being a perfectly good steady-state anchor: it never resolved its
    transient (``MIN_REACH``), it had no transient worth the name
    (``MIN_AMPLITUDE_SIGMA``), it ran so long that the transient was over and
    the pole is fitting drift (``MAX_REACH``), or it swept so far that tau
    itself changed across it (``MAX_AMPLITUDE_FRAC``).  The last two are new on
    2026-09-13 and are Jeff's, from REFIT_PLAN.md section 1's third row.

    **These two are NOT mirrored into ltspm3/tools/sweep.py**, and the
    divergence is deliberate in the same way ``long_enough``'s is: the sweep
    tool asks "has this rung settled, may I move on", which is a question about
    the LOWER bound only.  A rung that ran too long or moved too far is a
    grading question for a fit, and the sweep tool never grades anything.
    """
    if abs(r["settle_K"]) > MAX_SETTLE_K:
        return ""
    if not settled(r, tau_plant_s):
        return ""
    if (MIN_REACH <= r["reach"] <= MAX_REACH
            and r["amp_sigma"] >= MIN_AMPLITUDE_SIGMA
            and abs(r["amp_K"]) <= MAX_AMPLITUDE_FRAC * r["T_inf"]
            and r["rms_sigma"] < 8.0 and not pole_floor(r)):
        return "tau"
    return "steady"


# No ``__main__``.  It wrote ``analysis/steps.csv`` -- every dwell in the
# archive, fitted as a single pole -- and :mod:`measure` took that over in
# Phase A of REFIT_PLAN.md, writing ``analysis/measured.csv`` instead.
#
# The move is not a rename.  A single pole is the wrong model for a hold and
# was placing four graded anchors between 0.4 and 1.7 K away from where the
# cryostat actually sat, so the table a fit reads cannot be built by this
# module's grader alone.  What stays here is the finder, the pole and the
# bars: :func:`dwells`, :func:`fit_pole`, :func:`pole_bounds`, :func:`grade`
# and the constants ``ltspm3/tools/sweep.py`` mirrors.  :mod:`curate` turns
# them into the manifest and :mod:`measure` measures what the manifest names.
