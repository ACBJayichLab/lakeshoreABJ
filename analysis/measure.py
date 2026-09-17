"""Reduce every named window of the archive to numbers with honest error bars.

This is the measurement stage, and it is deliberately separate from the fit.
:mod:`steps` finds dwells and grades them; :mod:`curate` turns that into the
committed manifest; this module measures the windows the manifest names and
writes ``analysis/measured.csv``, which is what every fit reads.  Nothing here
chooses a dataset -- the manifest already did, and a disagreement between the
finder and the manifest shows up as a ``curate --propose`` diff rather than as
a silent difference in what got measured.

What each kind of window is asked
---------------------------------

``jump`` -- a driven step held a few time constants.  Fitted as
``T(t) = T_inf + A exp(-t/tau)`` by :func:`steps.fit_pole`, unchanged, so a
jump's ``T_inf`` and ``tau_s`` are bit-identical to what the grader saw.  What
is new is an uncertainty on each, from the residual covariance.

``hold`` -- eight hours or more at a fixed output.  A single pole is the WRONG
model here and was quietly doing damage: once the relaxation is over there is
nothing left for the exponential to describe but the cryostat's slow drift, and
it obligingly describes it, returning a time constant of days and an asymptote
the sample never reaches.  Fitting

    T(t) = T_0 + m t + A exp(-t/tau) + a cos(w x) + b sin(w x)

instead moves five graded anchors by more than 0.1 K and three of them by more
than a kelvin -- ``rec-20260828-141631``, a 74.9 h hold, by **1.53 K**, on
which the pole had read reach 0.19.  ``T_pole`` is kept beside ``T_inf`` in the
output precisely so that change stays visible.

``trace`` and ``mask`` are not measured.  A trace is integrated by the ODE, not
reduced to a number; a mask exists to keep dwells out of the proposal and has
already done its work by the time the manifest is read.

The three error bars, and why none of them is optional
------------------------------------------------------

``sigma_stat``
    the fit's own, from ``s^2 (J'J)^-1`` -- **inflated by the residual's
    integrated autocorrelation time** (:func:`act_inflation`).  Without that
    inflation a 70 h hold at a 0.6 s cadence claims 125,888 independent
    measurements and an error on the mean of a few tens of microkelvin, which
    is nonsense on an instrument whose noise is mostly slow wander.  The
    inflation is reported as ``n_eff`` so the claim is auditable.
``sigma_extrap``
    ``remainder_K``: how far the fitted pole still had to travel at the last
    sample.  Past the end of the data ``T_inf`` is a statement about the model
    rather than a measurement of the cryostat, so the distance extrapolated is
    carried as model error.  This is ``fit_ode``'s ``2*|settle_K|`` computed on
    the smooth curve instead of on the last sample, which carried one sample of
    noise -- 30 mK at 114 K, a third of the bar it was compared against.
``sigma_long``
    the long-term fluctuation, MEASURED rather than assumed: the diurnal
    amplitude that the holds which resolve it actually show (:func:`diurnal`).
    A 40-minute jump sits at an unknown phase of that cycle, so its steady
    state carries the cycle's rms, ``A/sqrt(2)``, as an irreducible offset --
    and so does a hold that fitted the harmonic out, because removing the 24 h
    component does not remove the wander at six hours or at a week.  It is the
    same number on every row, which is the point: it is the floor under a
    level, and no window in this archive measures it away.

Usage::

    python analysis/measure.py                 # write analysis/measured.csv
    python analysis/measure.py -v              # ...and print every window
    python analysis/measure.py --holds         # the hold table and the baths
"""
from __future__ import annotations

import csv
import datetime as _dt
import math

import numpy as np
from scipy.optimize import minimize_scalar

import segments as S
import steps

#: The diurnal angular frequency.  One cycle per 24 h, on the building's clock
#: rather than on any window's -- see :func:`_day_seconds`.
OMEGA = 2.0 * math.pi / 86400.0

#: A window must span a WHOLE diurnal period before the harmonic is fitted.
#:
#: 24 h, and not the 8 h that makes a window a ``hold``.  Eight hours is enough
#: for the cycle to show as curvature in principle, and it is not enough to
#: separate that curvature from a free linear drift and a free exponential in
#: the same model: fitted below a period the harmonic runs away, and the four
#: shortest holds in the archive return amplitudes of 464, 306, 300 and 299 mK
#: against the 4-69 mK that every hold spanning a full day reports.  Those are
#: not the building; they are the drift wearing the harmonic's hat.
#:
#: So a hold shorter than a day is fitted with level, drift and relaxation, and
#: the cycle enters its error bar as ``sigma_long`` instead of its model.
HARMONIC_MIN_S = 86400.0

#: Report a hold as ``drifting`` past this.  Not a rejection -- a drift is a
#: measurement, and measuring it is half of why Phase B exists -- but a hold
#: moving this fast has no single temperature, and a reviewer should see which
#: ones they are.  10 mK/h over an 8 h hold is 80 mK, about the width of the
#: error bars here.
DRIFT_FLAG_K_PER_H = 0.010

#: Report a hold as ``pole-moved`` when the proper model disagrees with the
#: single pole by this much.  0.1 K, a third of ``fit_ode.ANCHOR_FLOOR_K``:
#: below it the change cannot move a fit, above it the old anchor was wrong.
POLE_FLAG_K = 0.1

#: How close to a search bound counts as sitting ON it.
#:
#: A tau at one end of :func:`steps.pole_bounds` is the search reporting that
#: the time constant is outside what the window can see, and it is NOT a
#: measurement -- but the covariance around it looks like one.  Six of the
#: eight floor-pinned dwells the archive grades ``tau`` come back at 4.0 s with
#: a statistical bar of 11 %, which is a confident-looking number for "faster
#: than two samples".  So the pin is reported, in ``tau_pinned`` and in
#: ``flags``, and a reader who cares can refuse it.
#:
#: Reported and not refused: grading is ``steps.py``'s and reaches a fit through
#: the manifest, where a verdict change is a diff somebody reads.  See
#: archive/AUDIT-2026-09-10.md finding 2, whose fix belongs there.
#:
#: 5 %, which is far tighter than the factor of two between adjacent
#: candidates and loose enough to survive the optimiser stopping just inside
#: its bracket.
PIN_TOL = 0.05

#: Sokal's automatic windowing constant for the autocorrelation sum: stop at
#: the first lag M with ``M >= ACT_WINDOW * tau_int(M)``.  5 is the usual
#: choice and is a compromise -- too small truncates the tail of a slowly
#: decaying correlation, too large lets the noise in that tail dominate the
#: sum.
ACT_WINDOW = 5.0

#: Never claim fewer than this many effective samples, however correlated the
#: residual looks.  A window reduced below four effective samples is telling
#: you the model is wrong, not that the error bar is enormous, and an unbounded
#: inflation there turns one badly fitted hold into a bar that swallows the
#: review.  Reported through ``n_eff``, and ``flags`` says ``n_eff-clamped``,
#: so a row sitting on the clamp is visible rather than merely humble.
MIN_N_EFF = 4.0

#: The columns of ``measured.csv``.  The first block is ``steps.csv``'s schema
#: unchanged -- five modules read those names -- and everything from ``id`` on
#: is new.  Order is fixed here so that a diff of the file is readable.
FIELDS = (
    # steps.csv's schema, so every existing reader keeps working
    "source", "t_start", "t_end", "span_s", "n", "u_pct", "P_W",
    "T_inf", "T_end", "settle_K", "T_lo", "T_hi", "tau_s", "reach",
    "amp_K", "amp_sigma", "rms_K", "rms_sigma", "end_rate_k_per_h",
    "remainder_K", "Coldplate", "file", "era", "grade",
    # which window this is, in the manifest's own words
    "id", "kind", "quality", "flags",
    # the clock.  t_mid is where T_inf is quoted; days is from the archive's
    # first sample, which is the axis the campaign drift is measured on.
    "t_mid", "days",
    # the error bars
    "sigma_T_inf", "sigma_stat", "sigma_extrap", "sigma_long",
    "sigma_tau_s", "n_eff", "act_s", "tau_pinned",
    # holds only
    "T_pole", "drift_k_per_h", "sigma_drift_k_per_h",
    "diurnal_k", "sigma_diurnal_k", "diurnal_peak_h",
    # holds only: how the baths reach the sample
    "beta_coldplate", "beta_stage1", "beta_stage2", "beta_shield",
    "bath_r2", "bath_rms_K",
)

#: Which channel each ``beta_`` column regresses on.  Names, not indices,
#: because the 218's inputs were relabelled at the 2026-08-26 part-roll and a
#: positional map is how that kind of thing goes wrong silently.
BATHS = (
    ("beta_coldplate", S.COLDPLATE),
    ("beta_stage1", "1st Stage"),
    ("beta_stage2", "2nd Stage"),
    ("beta_shield", "RAD SHIELD"),
)

#: A bath channel joins the regression only if it is this complete over the
#: window.  The four cold-head channels are blank 2026-07-23 -> 08-20, so most
#: prepython holds have Coldplate and nothing else; a channel that is 40 %
#: present would otherwise contribute a coefficient fitted to whichever hours
#: happened to be logged.
BATH_COMPLETE = 0.99


def _day_seconds(epoch: np.ndarray) -> np.ndarray:
    """Seconds since local midnight of the first sample, running on past 24 h.

    The harmonic's argument has to be the BUILDING's clock and not the
    window's: fitted against ``t``, a hold's phase says which hour of its own
    life the sample was warmest, which is not a fact about the cryostat and
    cannot be compared between two holds.  Local, because whatever drives a
    24 h cycle here keeps local time.  The archive spans July to September, so
    no daylight-saving transition falls inside any window.
    """
    first = _dt.datetime.fromtimestamp(float(epoch[0]))
    midnight = _dt.datetime(first.year, first.month, first.day).timestamp()
    return epoch - midnight


def act_inflation(resid: np.ndarray) -> tuple:
    """``(kappa, lag)`` -- the variance inflation from residual autocorrelation.

    ``kappa`` is the integrated autocorrelation time: the factor by which the
    variance of a mean over correlated samples exceeds ``sigma^2/n``.  Multiply
    a covariance by it and ``n/kappa`` is the number of independent
    measurements the window really holds.

    Why this is not optional here.  The 218's noise is mostly slow wander
    rather than white hash (docs/ltspm3/noise.md, tools/noisespec.py), so
    treating a 70 h hold's 125,888 samples as independent understates its error
    bar by about two orders of magnitude.  The resulting bar would then outvote
    every other anchor in the fit on the strength of having sat still for three
    days.

    Sokal's automatic windowing: sum ``rho_k`` until the lag reaches
    ``ACT_WINDOW`` times the sum so far.  Truncation is unavoidable -- the tail
    of an empirical autocorrelation is noise with the same integral as signal
    -- and this is the standard way of choosing where.  Falls back to the AR(1)
    estimate ``(1+rho_1)/(1-rho_1)`` when the window never closes, which is the
    case for a short jump where there is no tail to look at.
    """
    n = len(resid)
    if n < 8:
        return 1.0, 0
    r = resid - resid.mean()
    if float(np.dot(r, r)) <= 0.0:
        return 1.0, 0
    # FFT autocovariance, zero-padded so the circular wrap does not fold the
    # tail of the correlation back onto its head.
    nf = 1 << int(2 * n - 1).bit_length()
    f = np.fft.rfft(r, nf)
    acov = np.fft.irfft(f * np.conj(f), nf)[:n].real
    rho = acov / acov[0]
    total = 0.0
    for lag in range(1, n):
        total += float(rho[lag])
        tau_int = 1.0 + 2.0 * total
        if lag >= ACT_WINDOW * tau_int:
            return max(tau_int, 1.0), lag
    rho1 = float(rho[1])
    if rho1 >= 1.0 - 1e-9:
        return float(n) / MIN_N_EFF, n - 1
    return max((1.0 + rho1) / (1.0 - rho1), 1.0), n - 1


def _pinned(tau: float, lo: float, hi: float) -> str:
    """``'floor'``, ``'ceiling'`` or ``''`` -- see :data:`PIN_TOL`."""
    if tau <= lo * (1.0 + PIN_TOL):
        return "floor"
    if tau >= hi * (1.0 - PIN_TOL):
        return "ceiling"
    return ""


def _cov(J: np.ndarray, resid: np.ndarray, kappa: float) -> np.ndarray:
    """``s^2 kappa (J'J)^-1``, with ``s^2`` on the residual degrees of freedom.

    **The columns are normalised before the inverse and unscaled after**, and
    that is not tidiness.  A hold's Jacobian holds a column of ones, a column
    of seconds reaching 250,000, and a ``dT/dtau`` column peaking at 0.007 K/s
    -- seven orders of magnitude, so ``J'J`` comes out at a condition number
    around 1e15 and ``pinv``'s default cutoff discards the tau direction as
    numerically absent.  It does that silently: the answer is a covariance
    matrix with a zero in it, and ``sigma_tau_s`` reads exactly 0.000 for six
    of the seven holds whose time constant the archive believes.  An error bar
    of zero on a fitted parameter is the most dangerous number this module
    could publish, so the scaling is load-bearing.

    Normalising by column norm leaves the condition number that is really
    there -- the collinearity between drift and a long exponential -- which is
    what ``pinv`` should be judging.
    """
    n, p = J.shape
    dof = max(n - p, 1)
    s2 = float(np.dot(resid, resid)) / dof
    scale = np.sqrt((J * J).sum(axis=0))
    scale[scale <= 0.0] = 1.0
    C = np.linalg.pinv((J / scale).T @ (J / scale))
    return s2 * kappa * C / np.outer(scale, scale)


def measure_jump(t: np.ndarray, y: np.ndarray) -> dict:
    """``T_inf`` and ``tau`` with an uncertainty on each, from the pole fit.

    The point estimate is :func:`steps.fit_pole` called unchanged, so a jump's
    numbers here are the ones the grader graded, to the last digit.  What is
    added is the covariance of ``(T_inf, A, tau)``, which needs the Jacobian's
    third column -- ``dT/dtau = A t/tau^2 exp(-t/tau)`` -- and so cannot be
    read off the linear solve ``fit_pole`` does at fixed tau.
    """
    t = t - t[0]
    T_inf, A, tau, rms = steps.fit_pole(t, y)
    e = np.exp(-t / tau)
    J = np.column_stack([np.ones_like(t), e, A * t / (tau * tau) * e])
    resid = (T_inf + A * e) - y
    kappa, _ = act_inflation(resid)
    cov = _cov(J, resid, kappa)
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 0.0
    lo, hi = steps.pole_bounds(t)
    return {
        "T_inf": T_inf, "A": A, "tau_s": tau, "rms_K": rms,
        "sigma_stat": math.sqrt(max(cov[0, 0], 0.0)),
        "sigma_tau_s": math.sqrt(max(cov[2, 2], 0.0)),
        "n_eff": len(t) / kappa, "act_s": kappa * dt,
        "tau_pinned": _pinned(tau, lo, hi),
    }


def measure_hold(t: np.ndarray, epoch: np.ndarray, y: np.ndarray) -> dict:
    """Level, drift, relaxation and -- if the window spans a day -- the harmonic.

    ``T(t) = T_0 + m t + A exp(-t/tau) + a cos(w x) + b sin(w x)``, nonlinear in
    ``tau`` alone, so ``tau`` is found by the same 1-D search
    :func:`steps.fit_pole` uses and the linear parameters are solved exactly at
    each trial.  ``x`` is time of day, not time into the window.

    ``tau`` is bounded above at a third of the span, and that bound is the
    whole reason this model behaves where a bare pole does not: an exponential
    whose ``tau`` is comparable to the record is indistinguishable from a
    straight line, and given the choice the fit takes the exponential and
    reports an asymptote the cryostat never visits.  Capping ``tau`` says the
    exponential describes what finishes inside the window and the linear term
    describes what does not, which is a statement about the two timescales
    rather than about the fit.

    The level is quoted at the window's MIDPOINT, with the harmonic left out of
    it.  A drifting hold has no single temperature, so the honest report is a
    temperature and the time it was true -- the midpoint, because that is where
    a linear drift's error is smallest -- and the harmonic is a fluctuation
    about that level rather than part of it.
    """
    t = t - t[0]
    span = float(t[-1])
    x = _day_seconds(epoch)
    harmonic = span >= HARMONIC_MIN_S

    def build(tau):
        cols = [np.ones_like(t), t, np.exp(-t / tau)]
        if harmonic:
            cols += [np.cos(OMEGA * x), np.sin(OMEGA * x)]
        return np.column_stack(cols)

    def solve(tau):
        M = build(tau)
        c, *_ = np.linalg.lstsq(M, y, rcond=None)
        return M, c, M @ c - y

    lo = max(2.0 * float(np.median(np.diff(t))), 1.0)
    hi = max(span / 3.0, lo * 2.0)
    o = minimize_scalar(
        lambda lt: float(np.sqrt(np.mean(solve(math.exp(lt))[2] ** 2))),
        bounds=(math.log(lo), math.log(hi)), method="bounded")
    tau = math.exp(o.x)
    M, c, resid = solve(tau)
    rms = float(np.sqrt(np.mean(resid ** 2)))

    A = float(c[2])
    J = np.column_stack([M, A * t / (tau * tau) * np.exp(-t / tau)])
    kappa, _ = act_inflation(resid)
    cov = _cov(J, resid, kappa)

    t_mid = 0.5 * span
    g = np.zeros(J.shape[1])
    g[0], g[1] = 1.0, t_mid                     # level + drift, no harmonic
    out = {
        "T_inf": float(c[0] + c[1] * t_mid),
        "A": A, "tau_s": tau, "rms_K": rms,
        "drift_k_per_h": 3600.0 * float(c[1]),
        "sigma_drift_k_per_h": 3600.0 * math.sqrt(max(cov[1, 1], 0.0)),
        "sigma_stat": math.sqrt(max(float(g @ cov @ g), 0.0)),
        "sigma_tau_s": math.sqrt(max(cov[-1, -1], 0.0)),
        "n_eff": len(t) / kappa,
        "act_s": kappa * float(np.median(np.diff(t))),
        "diurnal_k": math.nan, "sigma_diurnal_k": math.nan,
        "diurnal_peak_h": math.nan,
        # this model's own bracket, not fit_pole's: hi is span/3, so a pin here
        # says the relaxation did not finish inside the window
        "tau_pinned": _pinned(tau, lo, hi),
    }
    if harmonic:
        a, b = float(c[3]), float(c[4])
        amp = math.hypot(a, b)
        # d(amp)/d(a,b) = (a,b)/amp, through the covariance rather than added
        # in quadrature, because a and b are not independent here.
        gr = np.zeros(J.shape[1])
        gr[3], gr[4] = a / max(amp, 1e-15), b / max(amp, 1e-15)
        out["diurnal_k"] = amp
        out["sigma_diurnal_k"] = math.sqrt(max(float(gr @ cov @ gr), 0.0))
        out["diurnal_peak_h"] = (math.atan2(b, a) / OMEGA / 3600.0) % 24.0
    return out


def bath_regression(sl: S.Slice, tau: float, ok: np.ndarray) -> dict:
    """How much of a hold's movement the bath channels explain, instead of time.

    Same window and the same relaxation term, but the drift and the harmonic
    are replaced by the temperatures of the things the sample is bolted to.
    The two residuals are then directly comparable and answer a sharp question:
    did this hold move because time passed, or because its heat sinks moved?

    ``R^2`` is against the sample's variance after the relaxation is removed,
    so a hold dominated by its own step does not score well merely for having
    one.  Channels blank over the window are skipped rather than imputed -- the
    four cold-head channels are absent 2026-07-23 -> 08-20, and inventing them
    is how a regressor comes to cover a third of the campaign silently
    (REFIT_PLAN.md trap T6).
    """
    t = sl.t[ok]
    t = t - t[0]
    y = sl.T[ok]
    cols = [np.ones_like(t), np.exp(-t / tau)]
    names, out = [], {k: math.nan for k, _ in BATHS}
    for key, chan in BATHS:
        if chan not in sl.table.chan:
            continue
        v = sl.col(chan)[ok]
        good = ~np.isnan(v)
        if good.mean() < BATH_COMPLETE or float(np.nanstd(v)) <= 0.0:
            continue
        cols.append(np.where(good, v - np.nanmean(v), 0.0))
        names.append(key)
    if not names:
        return out | {"bath_r2": math.nan, "bath_rms_K": math.nan}
    M = np.column_stack(cols)
    c, *_ = np.linalg.lstsq(M, y, rcond=None)
    resid = M @ c - y
    for i, key in enumerate(names):
        out[key] = float(c[2 + i])
    # The reference: level and relaxation, nothing else.  What the baths are
    # credited with is the movement left over once the step is accounted for.
    M0 = np.column_stack(cols[:2])
    c0, *_ = np.linalg.lstsq(M0, y, rcond=None)
    base = float(np.sum((M0 @ c0 - y) ** 2))
    rss = float(np.sum(resid ** 2))
    return out | {
        "bath_r2": (1.0 - rss / base) if base > 0 else math.nan,
        "bath_rms_K": float(np.sqrt(np.mean(resid ** 2))),
    }


def diurnal(rows: list) -> tuple:
    """``(amplitude, rows_used)`` -- the long-term fluctuation, measured.

    Only holds that span a whole day and that a fit would actually use.  The
    median rather than the mean or the rms: the amplitudes run 4 to 69 mK and
    the top of that range is one noisy prepython window, which an rms would let
    set the error bar for all three hundred anchors.
    """
    used = [r for r in rows
            if r["kind"] == "hold" and r["grade"]
            and r["span_s"] >= HARMONIC_MIN_S
            and not math.isnan(r["diurnal_k"])]
    if not used:
        raise SystemExit(
            "measure: no hold spans a full day, so the long-term fluctuation "
            "cannot be measured -- and it must not be assumed.  See "
            "REFIT_PLAN.md section 6.")
    return float(np.median([r["diurnal_k"] for r in used])), used


def _flags(r: dict) -> str:
    out = []
    if r["kind"] == "hold":
        if r["span_s"] < HARMONIC_MIN_S:
            out.append("short-for-harmonic")
        if abs(r["drift_k_per_h"]) > DRIFT_FLAG_K_PER_H:
            out.append("drifting")
        if abs(r["T_inf"] - r["T_pole"]) > POLE_FLAG_K:
            out.append("pole-moved")
    if r["n_eff"] <= MIN_N_EFF * 1.001:
        out.append("n_eff-clamped")
    if r["tau_pinned"]:
        out.append("tau-" + r["tau_pinned"])
    if r["kind"] == "hold" and r["pole_pinned"] == "ceiling":
        # the pole this hold's T_pole came from gave up; the hold model did not
        out.append("pole-ceiling")
    return " ".join(out)


#: ``use`` in the manifest, ``grade`` in the table five modules already read.
#: ``ode`` never reaches here -- a trace is integrated, not reduced.
_GRADE = {"tau": "tau", "steady": "steady", "excluded": ""}


def measure(path: str | None = None) -> tuple:
    """``(rows, diurnal_amplitude, holds_it_came_from)``.

    One row per ``jump`` and ``hold`` in the manifest.  The long-term term is
    measured from the finished table and then applied back to it, which is why
    it comes out of here beside the rows rather than as a column computed
    inside the loop.
    """
    t0 = min(float(S.read_table(f).epoch[0]) for f in S.TABLES)
    rows = []
    for w in S.windows(path):
        if w.kind not in ("jump", "hold"):
            continue
        sl = S.load(w, path)
        ok = ~(np.isnan(sl.T) | np.isnan(sl.u))
        if int(ok.sum()) < steps.MIN_N:
            raise SystemExit(
                f"measure: {w.id} has {int(ok.sum())} usable rows, under "
                f"steps.MIN_N = {steps.MIN_N}.  The manifest names a window "
                f"nothing can be fitted to; run curate.py --propose.")
        t, e, y = sl.t[ok], sl.epoch[ok], sl.T[ok]
        u, tc = sl.u[ok], sl.Tc[ok]
        span = float(t[-1] - t[0])

        if w.kind == "hold":
            m = measure_hold(t, e, y)
            # The single pole this hold WOULD have got, kept so that the change
            # is visible and so that a pinned pole can be named as the reason.
            m["T_pole"], _, pole_tau, _ = steps.fit_pole(t, y)
            m |= bath_regression(sl, m["tau_s"], ok)
        else:
            m = measure_jump(t, y)
            m["T_pole"], pole_tau = m["T_inf"], m["tau_s"]

        sigma_noise = steps.noise_k(float(np.mean(y)))
        remainder = abs(m["A"]) * math.exp(-span / m["tau_s"])
        mid = 0.5 * (float(e[0]) + float(e[-1]))
        r = {
            "source": w.file, "file": w.file, "era": S.era(w.file),
            "t_start": w.t_start, "t_end": w.t_end,
            "span_s": span, "n": int(ok.sum()),
            "u_pct": float(np.mean(u)), "P_W": steps.power_w(float(np.mean(u))),
            "T_inf": m["T_inf"], "T_end": float(y[-1]),
            "settle_K": m["T_inf"] - float(y[-1]),
            "T_lo": float(np.min(y)), "T_hi": float(np.max(y)),
            "tau_s": m["tau_s"], "reach": span / m["tau_s"],
            "amp_K": abs(m["A"]), "amp_sigma": abs(m["A"]) / sigma_noise,
            "rms_K": m["rms_K"], "rms_sigma": m["rms_K"] / sigma_noise,
            "end_rate_k_per_h": (3600.0 * abs(m["A"]) / m["tau_s"]
                                 * math.exp(-span / m["tau_s"])),
            "remainder_K": remainder,
            "Coldplate": float(np.nanmean(tc)),
            "grade": _GRADE[w.use],
            "id": w.id, "kind": w.kind, "quality": w.quality,
            "t_mid": S._iso(mid), "days": (mid - t0) / 86400.0,
            "sigma_extrap": remainder,
        }
        r["tau_pinned"] = m.get("tau_pinned", "")
        r["pole_pinned"] = _pinned(pole_tau, *steps.pole_bounds(t))
        for k in ("sigma_stat", "sigma_tau_s", "n_eff", "act_s", "T_pole",
                  "drift_k_per_h", "sigma_drift_k_per_h", "diurnal_k",
                  "sigma_diurnal_k", "diurnal_peak_h", "bath_r2",
                  "bath_rms_K", *(k for k, _ in BATHS)):
            r[k] = m.get(k, math.nan)
        r["n_eff"] = max(r["n_eff"], MIN_N_EFF)
        rows.append(r)

    amp, used = diurnal(rows)
    for r in rows:
        # EVERY row, including the eight holds that fitted the harmonic out of
        # their own level.  Removing the 24 h Fourier component does not remove
        # the wander at 6 h or at a week, and the measured diurnal amplitude is
        # the only number in this archive that sizes any of it.  Exempting
        # those eight gave them bars of 1.4 mK -- a claim to have pinned the
        # cryostat's level ten times tighter than one sample of sensor noise,
        # from a hold whose own drift term is a straight line through 30 h.
        r["sigma_long"] = amp / math.sqrt(2.0)
        r["sigma_T_inf"] = math.sqrt(r["sigma_stat"] ** 2
                                     + r["sigma_extrap"] ** 2
                                     + r["sigma_long"] ** 2)
        r["flags"] = _flags(r)
    rows.sort(key=lambda r: r["T_inf"])
    return rows, amp, used


#: REFIT_PLAN.md section 6's exit criteria, as numbers.
#:
#: ``T_END_K`` are the three post-recalibration holds of section 2.1.  They are
#: quoted there at the END of the hold -- they were produced by an extractor
#: that averaged a hold's last twenty minutes -- while this module quotes a
#: level at the midpoint with the drift beside it, so the check extrapolates
#: back down the fitted drift before comparing.  That is the whole reason the
#: comparison works to half a millikelvin on the first of them: the 17 mK gap
#: between midpoint and end IS the measured -3.02 mK/h over 5.76 h.
T_END_K = {
    "pc-20260904-233855": 96.516,
    "pc-20260905-165509": 114.390,
    "pc-20260908-154814": 118.609,
}

#: The two time constants section 6 names, and the window each was measured on.
#:
#: 525 s is a jump and reproduces directly.  **513 s is not the 70 h hold's
#: time constant** -- it is the first 40 minutes of it, which is what the
#: pre-archive region export contained, and the archive merges that rung into
#: the hold that followed it because the sweep tool left the heater there and
#: nothing moved for three days.  Fitted over all 70 h the same relaxation
#: converges on 534 s.  Both numbers are right about their own window and the
#: check tests both, because the 4 % gap between them is a measurement of the
#: reach bias ``steps.fit_pole``'s docstring warns about rather than a
#: disagreement to be resolved.
#: The targets are REFIT_PLAN.md's own, to the second, and not this module's
#: output rounded back -- a check against a number this file produced is a
#: regression pin and proves nothing about the plan's criterion.  534 s is the
#: exception and IS a regression pin: it is Phase A's own new measurement of
#: the converged time constant, with nothing earlier to check it against.
TAU_S = {"pc-20260908-150234": 525.0}
TAU_PARTIAL = ("pc-20260905-165509", 40 * 60.0, 513.0, 534.0)
#: How close counts, for a target quoted to the second.
TAU_TOL_S = 1.5


def verify(rows: list, amp: float, used: list) -> int:
    """Check REFIT_PLAN.md section 6's exit criteria.  Returns a process status.

    Deliberately here rather than in ``tests/``: ``analysis/`` needs scipy,
    which the recorder does not, and CI fails a build on a skipped test.  Same
    place ``export_response.py`` keeps its ``--verify`` for the same reason.
    """
    by_id = {r["id"]: r for r in rows}
    bad = []
    print("REFIT_PLAN.md section 6 exit criteria\n")

    print("1. the three post-recalibration hold temperatures")
    print(f"   {'id':<22}{'level@mid':>11}{'drift mK/h':>12}{'level@end':>11}"
          f"{'target':>9}{'diff mK':>9}{'2 sigma':>9}")
    for wid, target in T_END_K.items():
        r = by_id[wid]
        end = r["T_inf"] + r["drift_k_per_h"] * r["span_s"] / 7200.0
        tol = 2.0 * r["sigma_T_inf"]
        ok = abs(end - target) <= tol
        bad += [] if ok else [f"{wid} level {end:.4f} K against {target} K"]
        print(f"   {wid:<22}{r['T_inf']:>11.4f}{1e3 * r['drift_k_per_h']:>12.2f}"
              f"{end:>11.4f}{target:>9.3f}{1e3 * (end - target):>9.1f}"
              f"{1e3 * tol:>9.1f}  {'ok' if ok else 'FAIL'}")

    print("\n2. the measured time constants")
    for wid, target in TAU_S.items():
        r = by_id[wid]
        tol = max(r["sigma_tau_s"], TAU_TOL_S)
        ok = abs(r["tau_s"] - target) <= tol
        bad += [] if ok else [f"{wid} tau {r['tau_s']:.1f} s against {target} s"]
        print(f"   {wid:<22}{r['tau_s']:>9.1f} s +-{r['sigma_tau_s']:>6.1f}   "
              f"target {target:>7.1f} s   {'ok' if ok else 'FAIL'}")

    wid, cut, partial, whole = TAU_PARTIAL
    sl = S.load(wid)
    ok_rows = ~(np.isnan(sl.T) | np.isnan(sl.u))
    t, y = sl.t[ok_rows], sl.T[ok_rows]
    t = t - t[0]
    m = t <= cut
    tau_cut = steps.fit_pole(t[m], y[m])[2]
    tau_all = by_id[wid]["tau_s"]
    for label, got, target, span in (
            (f"first {cut / 60:.0f} min", tau_cut, partial, float(t[m][-1])),
            ("all of it", tau_all, whole, float(t[-1]))):
        good = abs(got - target) <= TAU_TOL_S
        bad += [] if good else [f"{wid} {label} tau {got:.1f} s against {target} s"]
        print(f"   {wid:<22}{got:>9.1f} s            target {target:>7.1f} s   "
              f"{'ok' if good else 'FAIL'}  ({label}, reach {span / got:.1f})")
    print(f"   -> the 40-minute window reads {100 * (1 - tau_cut / tau_all):.1f} % "
          f"low.  That is the reach bias, MEASURED:\n"
          f"      sigma_tau_s is statistical and does not contain it, so a tau "
          f"from a dwell of\n      reach 5 is worth about 4 % less than its "
          f"error bar claims.")

    print("\n3. the long-term fluctuation is measured, with a phase")
    phases = [r["diurnal_peak_h"] for r in used]
    if not used or any(math.isnan(p) for p in phases):
        bad.append("the diurnal amplitude has no phase")
    print(f"   amplitude {1e3 * amp:.2f} mK from {len(used)} holds; peak hour "
          f"{min(phases):.1f} to {max(phases):.1f}")
    print("   The AMPLITUDE is consistent and the PHASE is not: eight holds put "
          "the daily\n   maximum anywhere in the 24 h, so this term is a bound "
          "on day-timescale wander\n   and not a building cycle with a known "
          "clock.  Phase B must not model it as one.")

    print("\n4. every row has an uncertainty, and they differ")
    s = np.array([r["sigma_T_inf"] for r in rows])
    if np.any(~np.isfinite(s)):
        bad.append(f"{int((~np.isfinite(s)).sum())} rows have no sigma_T_inf")
    if len(np.unique(np.round(s, 6))) < len(s) // 4:
        bad.append("sigma_T_inf is near-constant across rows")
    st = np.array([r["sigma_tau_s"] for r in rows])
    if np.any(st <= 0.0):
        bad.append(f"{int((st <= 0).sum())} rows have sigma_tau_s <= 0")
    print(f"   sigma_T_inf   {1e3 * s.min():.1f} to {1e3 * s.max():.1f} mK, "
          f"{len(np.unique(np.round(s, 6)))} distinct values over {len(s)} rows")
    print(f"   sigma_tau_s   {100 * (st / np.array([r['tau_s'] for r in rows])).min():.2f} "
          f"to {100 * (st / np.array([r['tau_s'] for r in rows])).max():.2f} % of tau")

    if bad:
        print(f"\n{len(bad)} criterion(a) NOT met:")
        for b in bad:
            print("  " + b)
        return 1
    print("\nall four exit criteria met")
    return 0


def _fmt(v) -> str:
    """Blank for NaN, ``repr`` for everything else -- full float precision.

    A hold-only column is genuinely absent on a jump, and a blank cell is how
    ``float(row[k])`` downstream raises instead of quietly reading a zero.
    """
    if isinstance(v, float):
        return "" if math.isnan(v) else repr(v)
    return str(v)


def write(path: str, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        for r in rows:
            w.writerow([_fmt(r[k]) for k in FIELDS])


def _nan(v, scale=1.0, width=10, prec=2) -> str:
    """One right-aligned cell, blank but STILL ``width`` wide if it is absent.

    A zero-width blank here silently shifts every later column left, which is
    how the bath table came to print a coldplate coefficient under ``R^2`` for
    the twenty prepython holds that have no cold-head channels.
    """
    return f"{'':>{width}}" if math.isnan(v) else f"{scale * v:>{width}.{prec}f}"


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="measure every named window of the cooldown-10 archive")
    ap.add_argument("-m", "--manifest", default=None)
    ap.add_argument("-o", "--out", default="analysis/measured.csv")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every measured window, not just the summary")
    ap.add_argument("--holds", action="store_true",
                    help="print the hold table and the bath regressions")
    ap.add_argument("--verify", action="store_true",
                    help="check REFIT_PLAN.md section 6's exit criteria and "
                         "exit non-zero if one is not met")
    a = ap.parse_args(argv)

    rows, amp, used = measure(a.manifest)
    anchors = [r for r in rows if r["grade"]]
    taus = [r for r in anchors if r["grade"] == "tau"]
    holds = [r for r in rows if r["kind"] == "hold"]

    print(f"{len(rows)} windows measured -> {len(anchors)} anchors, "
          f"{len(taus)} with a believable tau, {len(holds)} holds\n")

    if a.verbose:
        print(f"{'id':<22}{'kind':<6}{'grade':<7}{'T_inf':>10}{'+-':>8}"
              f"{'tau s':>10}{'+-':>9}{'reach':>8}{'n_eff':>9}  flags")
        for r in rows:
            print(f"{r['id']:<22}{r['kind']:<6}{r['grade'] or '-':<7}"
                  f"{r['T_inf']:>10.3f}{r['sigma_T_inf']:>8.3f}"
                  f"{r['tau_s']:>10.1f}{r['sigma_tau_s']:>9.1f}"
                  f"{r['reach']:>8.1f}{r['n_eff']:>9.1f}  {r['flags']}")
        print()

    if a.holds:
        print(f"{'id':<22}{'grade':<7}{'span h':>8}{'T_inf':>10}{'+-':>8}"
              f"{'T_pole':>10}{'drift mK/h':>11}{'diurn mK':>10}{'+-':>8}"
              f"{'peak h':>8}{'bath R2':>9}  flags")
        for r in holds:
            print(f"{r['id']:<22}{r['grade'] or '-':<7}{r['span_s'] / 3600:>8.2f}"
                  f"{r['T_inf']:>10.3f}{r['sigma_T_inf']:>8.3f}{r['T_pole']:>10.3f}"
                  f"{1e3 * r['drift_k_per_h']:>11.2f}"
                  f"{_nan(r['diurnal_k'], 1e3)}{_nan(r['sigma_diurnal_k'], 1e3, 8)}"
                  f"{_nan(r['diurnal_peak_h'], 1.0, 8)}"
                  f"{_nan(r['bath_r2'], 1.0, 9, 3)}  {r['flags']}")
        print(f"\n{'id':<22}{'coldplate':>11}{'1st stage':>11}{'2nd stage':>11}"
              f"{'shield':>11}{'bath R2':>9}{'bath rms':>10}{'time rms':>10}")
        for r in holds:
            if math.isnan(r["bath_r2"]):
                continue
            print(f"{r['id']:<22}"
                  + "".join(_nan(r[k], 1.0, 11, 3) for k, _ in BATHS)
                  + f"{r['bath_r2']:>9.3f}{1e3 * r['bath_rms_K']:>10.2f}"
                    f"{1e3 * r['rms_K']:>10.2f}")
        print()

    print(f"long-term fluctuation: median diurnal amplitude {1e3 * amp:.2f} mK "
          f"over the {len(used)} holds that span a full day")
    print("  amplitude mK  " + "  ".join(f"{1e3 * r['diurnal_k']:.1f}" for r in used))
    print("  +- mK         " + "  ".join(f"{1e3 * r['sigma_diurnal_k']:.1f}"
                                         for r in used))
    print("  peak hour     " + "  ".join(f"{r['diurnal_peak_h']:.1f}" for r in used))
    print(f"  -> sigma_long {1e3 * amp / math.sqrt(2):.2f} mK on every window "
          f"that does not resolve the cycle")

    s = np.array([r["sigma_T_inf"] for r in anchors])
    print(f"\nanchor error bars: {1e3 * s.min():.1f} to {1e3 * s.max():.1f} mK, "
          f"median {1e3 * np.median(s):.1f} mK over {len(anchors)} anchors")

    moved = [r for r in holds if "pole-moved" in r["flags"] and r["grade"]]
    if moved:
        print(f"\n{len(moved)} graded hold(s) that the single pole had wrong by "
              f"more than {POLE_FLAG_K} K:")
        for r in sorted(moved, key=lambda r: -abs(r["T_inf"] - r["T_pole"])):
            print(f"  {r['id']:<22}{r['span_s'] / 3600:>7.2f} h   pole "
                  f"{r['T_pole']:>9.3f} -> {r['T_inf']:>8.3f} K  "
                  f"({r['T_inf'] - r['T_pole']:+7.3f})   drift "
                  f"{1e3 * r['drift_k_per_h']:+7.2f} mK/h")

    # Not re-graded here, and deliberately so: grading lives in steps.py and
    # reaches the fits through the manifest, where a change to it is a diff
    # somebody reads.  But a hold the pole rejected for extrapolating too far
    # is a different window under the proper model, and the reviewer is the one
    # who gets to decide that -- so it is reported rather than acted on.
    rescued = [r for r in holds
               if not r["grade"] and r["quality"] == "over-extrapolated"
               and abs(r["settle_K"]) <= steps.MAX_SETTLE_K]
    if rescued:
        print(f"\n{len(rescued)} hold(s) excluded as over-extrapolated that "
              f"the level-and-drift model settles inside "
              f"steps.MAX_SETTLE_K = {steps.MAX_SETTLE_K} K:")
        for r in rescued:
            print(f"  {r['id']:<22}{r['span_s'] / 3600:>7.2f} h   pole "
                  f"{r['T_pole']:>9.3f} -> {r['T_inf']:>8.3f} K   settle "
                  f"{r['settle_K']:+6.3f} K   drift "
                  f"{1e3 * r['drift_k_per_h']:+7.2f} mK/h")
        print("  NOT admitted by this module.  Re-grading is steps.py's, and "
              "it reaches a fit\n  through the manifest -- see curate.py.")

    write(a.out, rows)
    print(f"\nwrote {a.out}  ({len(rows)} rows, {len(FIELDS)} columns)")
    if a.verify:
        print()
        return verify(rows, amp, used)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
