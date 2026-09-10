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
They deserve a margin: u=63.072% was held three times and landed 2.84 K apart,
and the July-August logs disagree with the September ones by about 2 K at
matched power.  So a September hold is worth +-1 K and a ``fit_cd10`` hold is
worth +-3 K, and the fit is free to miss them by that much.

``fit_cd10`` is NOT a different cooldown -- cooldown 10 started 2026-07-15 and
is still running.  It is the pre-Python chart-recorder half of this one, and
the cryostat has drifted across it: at seven outputs where the two halves
overlap the sample sits 1.8-2.9 K WARMER in September than it did in July and
August, same sign every time.  That is what the per-group power offset below
measures, and it is why group 0 -- the recent data -- is the reference: the
shipped model has to describe the cryostat as it is now.

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

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares

from _data import SWEEP as SWEEP_NAME
from _data import open_table

R_OHM, V_FS, GAIN = 75.5, 10.0, 1.11
SWEEP = SWEEP_NAME
ANCHORS = "analysis/steps.csv"

#: Bump when anything about the parameterisation or the objective changes.
#: It is part of the cache key, so bumping it invalidates every stored fit
#: at once -- which is the point: the input digest catches changed DATA and
#: cannot possibly catch changed CODE.
FIT_CACHE_VERSION = 3

#: Margin on a settled point, in kelvin, added in quadrature to twice its own
#: extrapolation distance.  Keyed on the source table: ``fit_cd10`` is the
#: July-August half of this cooldown and sits about 2 K off the September half
#: at matched power (see the module docstring), so it is given the wider bar.
#: Everything else -- the sweep, the ladder, both recorder tables -- is the
#: current state and shares the narrow one.
ANCHOR_SIGMA_K = {"fit_recorder": 1.0, "fit_cd10": 3.0}
ANCHOR_FLOOR_K = 0.3

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


def load_sweep(path=SWEEP, filter_tau_s=None):
    with open_table(path) as fh:
        rows = list(csv.DictReader(fh))
    t = np.array([_f(r, "Time") for r in rows])
    T = np.array([_f(r, "Sample") for r in rows])
    Tc = np.array([_f(r, "Coldplate") for r in rows])
    u = np.array([_f(r, "ls218.aout1") for r in rows])
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


def _rows(path=ANCHORS):
    with open_table(path) as fh:
        return list(csv.DictReader(fh))


def anchor_groups(path=ANCHORS, t_max=None):
    """Which half of the cooldown each anchor came from: 0 recent, 1 fit_cd10.

    One cooldown, two states.  The July-August logs disagree with the September
    ones by 1.8-2.9 K at matched output, measured directly at seven overlapping
    outputs, and no single Lambda can satisfy both.  Fitted as one free power
    offset, that stops being an error and becomes a measurement; see
    `fit(groups=...)`.

    Group 0 is the reference and gets no offset, so the curve itself describes
    the recent state -- which is the one the simulator and the loop have to
    match.  PASS THIS to `fit()` for anything that ships; without it the
    shipped curve splits the difference and is about 1 K wrong for both.
    """
    out = []
    for r in _rows(path):
        if not r.get("grade"):
            continue
        if t_max is not None and _f(r, "T_inf") > t_max:
            continue
        out.append(1 if r["source"].startswith("fit_cd10") else 0)
    return np.array(out, dtype=int)


def load_anchors(path=ANCHORS, t_max=None):
    """Every dwell whose steady state is usable, with its own error bar."""
    out = []
    for r in _rows(path):
        if not r.get("grade"):
            continue
        T = _f(r, "T_inf")
        if t_max is not None and T > t_max:
            continue
        cool = ANCHOR_SIGMA_K["fit_cd10" if r["source"].startswith("fit_cd10")
                              else "fit_recorder"]
        own = max(ANCHOR_FLOOR_K, 2.0 * abs(_f(r, "settle_K")))
        out.append((T, _f(r, "Coldplate"), _f(r, "P_W"), math.hypot(cool, own)))
    a = np.array(out)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def load_taus(path=ANCHORS, t_max=None):
    """Dwells whose relaxation ran long enough for tau to mean something."""
    out = []
    for r in _rows(path):
        if r.get("grade") != "tau":
            continue
        T = _f(r, "T_inf")
        if t_max is not None and T > t_max:
            continue
        out.append((T, _f(r, "tau_s")))
    a = np.array(out)
    return a[:, 0], a[:, 1]


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


def build(n_lam, n_cap, T_lo, T_hi):
    kl = np.geomspace(T_lo, T_hi, n_lam)
    kc = np.geomspace(T_lo, T_hi, n_cap)
    # Seed Lambda on the top settled hold rather than on its absolute value:
    # what the data fixes is the DIFFERENCE Lambda(180.6) - Lambda(8.5) = 0.778 W,
    # and a seed that gets the difference wrong starts the integration with a
    # net power of the same order as the heater and runs away before the
    # optimiser sees a usable gradient.
    shape = lambda T: (T / 180.6) ** 0.35            # noqa: E731
    a_lam = 0.778 / (shape(180.6) - shape(8.5))
    # C ~ 1 J/K at 137 K, which is a few grams of copper and sapphire, and
    # shaped like the Debye mix so the fit starts where the prior wants it
    c_ref = mix_c(np.array([CAP_SHAPE_REF_K]))[0]
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
CACHE_DIR = os.path.join("analysis", ".fit_cache")


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


def fit(n_lam, n_cap, data=None, anchors=None, taus=None, max_nfev=300,
        tier2=False, weights=None, n_drift=0, groups=None):
    """Fit Lambda and C to a sweep.

    ``weights`` is the per-sample weight of the sweep residual, and exists for
    the adaptively decimated grid (see ``analysis/decimate.py``): a kept sample
    stands for a span of time rather than for one 2 s tick, and without the
    weight a 22.8 h hold thinned 149:1 would silently stop mattering.  It is
    normalised to unit mean square, so ``sum(w**2) == len(t)`` exactly as on a
    uniform grid -- which is what keeps the anchor, tau and shape shares below
    balanced against the sweep the same way they were.
    """
    t, T, Tc, u = data if data is not None else load_sweep()
    aT, aTc, aQ, aS = anchors if anchors is not None else load_anchors()
    tauT, tauV = taus if taus is not None else load_taus()
    lam, cap, pl0, pc0 = build(n_lam, n_cap, 0.95 * T.min(), 1.05 * T.max())
    n = len(pl0)
    if weights is None:
        w_sweep = np.ones(len(t))
    else:
        w_sweep = np.asarray(weights, float)
        w_sweep = w_sweep / math.sqrt(float(np.mean(w_sweep ** 2)))
    w_anchor = math.sqrt(ANCHOR_SHARE * len(t) / len(aT))
    logT = np.log(T)

    pT = np.geomspace(T.min(), T.max(), 12)
    ref = np.array([CAP_SHAPE_REF_K])
    p_target = np.log(mix_c(pT) / mix_c(ref)[0])
    w_shape = (math.sqrt(CAP_SHAPE_SHARE * len(t) / len(pT))
               / math.log(CAP_SHAPE_FACTOR))
    w_tau = (math.sqrt(TAU_SHARE * len(t) / max(len(tauT), 1))
             / math.log(TAU_SIGMA_FACTOR))
    log_tau = np.log(tauV)

    # The drift knots are in TIME, evenly, and there are very few of them --
    # see DRIFT_SIGMA_W.  Linear interpolation rather than a spline: with three
    # knots a spline's extra smoothness buys nothing and its overshoot is one
    # more way for a nuisance term to reach somewhere it should not.
    # One free power offset per anchor group beyond the first.  Group 0 is the
    # recent state and is the reference, so it gets no offset -- an offset on
    # every group would be degenerate with Lambda's own level.
    groups = np.zeros(len(aT), int) if groups is None else np.asarray(groups, int)
    n_group = int(groups.max()) if len(groups) else 0

    dk = np.linspace(t[0], t[-1], n_drift) if n_drift else np.zeros(0)
    w_drift = (math.sqrt(DRIFT_SHARE * len(t) / max(n_drift, 1))
               / DRIFT_SIGMA_W)

    def drift_of(p):
        if not n_drift:
            return None
        return np.interp(t, dk, p[len(p) - n_tail:len(p) - n_group])

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
    w_rough = (math.sqrt(LAMBDA_SMOOTH_SHARE * len(t) / max(len(rough_T) - 2, 1))
               / LAMBDA_SMOOTH_SIGMA
               if LAMBDA_SMOOTH_SHARE and len(rough_T) > 2 else 0.0)
    rough_lx = np.log(rough_T)

    n_tail = n_drift + n_group

    def split_p(p):
        """(lambda knots, capacity knots) with the tail parameters removed."""
        body = p[:-n_tail] if n_tail else p
        return body[:n], (body[n:-2] if tier2 else body[n:])

    def group_offsets(p):
        """Per-anchor power offset, in watts.  Zero for the reference group."""
        if not n_group:
            return 0.0
        q = np.concatenate([[0.0], p[len(p) - n_group:]])
        return q[groups]

    def run(p):
        pl, pc = split_p(p)
        q = drift_of(p)
        if not tier2:
            return integrate(lam, cap, pl, pc, t, Tc, u, T[0], q_extra=q)
        f, g = unpack2(p[:-n_tail] if n_tail else p)
        return integrate2(lam, cap, pl, pc, f, g, t, Tc, u, T[0])

    def resid(p):
        pl, pc = split_p(p)
        model = run(p)
        r_sweep = w_sweep * (np.log(model) - logT) / SWEEP_SIGMA_REL
        dQ = lam(pl, aT) - lam(pl, aTc) - aQ - group_offsets(p)
        r_anchor = w_anchor * dQ / lam.slope(pl, aT) / aS
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
            parts.append(w_drift
                         * p[len(p) - n_tail:len(p) - n_group])
        return np.concatenate(parts)

    p0 = np.concatenate([pl0, pc0]
                        + ([np.array(TIER2_SEED)] if tier2 else [])
                        + ([np.zeros(n_drift)] if n_drift else [])
                        + ([np.zeros(n_group)] if n_group else []))
    # EVERY constant the objective reads goes in the key, not just the ones
    # that were being tuned the day it was written.  ANCHOR_SHARE was not in
    # here, so a study that varied it got the first run's answer back four
    # times and the knob looked dead -- the same failure mode LAMBDA_SMOOTH_SHARE
    # was caught by, one level up.
    key = cache_key(n_lam, n_cap, tier2, max_nfev, t, T, Tc, u, aT, aQ, aS,
                    tauV, w_sweep, groups,
                    np.array([n_drift, LAMBDA_SMOOTH_SHARE, LAMBDA_SMOOTH_SIGMA,
                              ANCHOR_SHARE, ANCHOR_FLOOR_K, TAU_SHARE,
                              TAU_SIGMA_FACTOR, SWEEP_SIGMA_REL, DRIFT_SHARE,
                              DRIFT_SIGMA_W, CAP_SHAPE_SHARE, CAP_SHAPE_FACTOR,
                              CAP_SHAPE_REF_K], float))
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
    drift_w = (x[len(x) - n_tail:len(x) - n_group] if n_drift
               else np.zeros(0))
    group_w = x[len(x) - n_group:] if n_group else np.zeros(0)
    err = model - T
    # Weighted, so a decimated fit reports the same quantity a full-grid one
    # does: rms over TIME, not over samples.  Unweighted these agree exactly on
    # a uniform grid, which is why this was invisible before.
    wsq = w_sweep ** 2
    hold = opening_hold(t, u)
    moving = np.abs(np.gradient(T, t)) > 2e-3          # > 7.2 K/h
    return {
        "tier2": tier2, "split": split, "mass_ratio": mass_ratio,
        "rms_moving_k": float(np.sqrt(np.mean(err[moving] ** 2))),
        "rms_still_k": float(np.sqrt(np.mean(err[~moving] ** 2))),
        "frac_moving": float(np.mean(moving)),
        "hold_k": float(np.sqrt(np.mean(err[hold] ** 2))),
        "hold_max_k": float(np.max(np.abs(err[hold]))),
        "hold_h": float((t[hold][-1] - t[hold][0]) / 3600.0),
        "n_lam": n_lam, "n_cap": n_cap, "npar": len(x), "nfev": nfev,
        "n_drift": n_drift, "drift_w": drift_w, "drift_t": dk,
        "n_group": n_group, "group_w": group_w,
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


def _worker(job):
    """One rung, in its own process.

    Returns only what the table needs.  A fit dict carries two spline objects
    and the whole 77,375-sample trajectory, and pickling those back across the
    pipe costs more than some of the arithmetic that made them.
    """
    import time
    n_lam, n_cap, t_max = job
    t0 = time.time()
    r = fit(n_lam, n_cap, load_sweep(), load_anchors(t_max=t_max),
            load_taus(t_max=t_max))
    return _summary(r, "", time.time() - t0)


def ladder(rows, data, anchors, title, out=None, taus=None, workers=None):
    import time
    axis = title.split()[2].rstrip(",")
    workers = LADDER_WORKERS if workers is None else workers
    print(f"\n{title}")
    print(_HEAD)
    got = []
    if workers > 1 and len(rows) > 1:
        from concurrent.futures import ProcessPoolExecutor
        t_max = float(data[1].max())
        wall = time.time()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            # map keeps the results in the order the rungs were given, so the
            # table still reads top to bottom however the workers finish.
            done = list(pool.map(_worker, [(a, b, t_max) for a, b in rows]))
        for d in done:
            d["axis"] = axis
            got.append(d)
            print(_line(d), flush=True)
        serial = sum(d["secs"] for d in done)
        print(f"  {len(rows)} rungs on {workers} workers: "
              f"{time.time() - wall:.0f} s wall, {serial:.0f} s of fitting")
    else:
        for n_lam, n_cap in rows:
            t0 = time.time()
            d = _summary(fit(n_lam, n_cap, data, anchors, taus), axis,
                         time.time() - t0)
            got.append(d)
            print(_line(d), flush=True)
    if out is not None:
        out.extend(got)
    return got


LADDER_CSV = "analysis/ladder.csv"


if __name__ == "__main__":
    data = load_sweep()
    hi = float(data[1].max())
    anchors, taus = load_anchors(t_max=hi), load_taus(t_max=hi)
    print(f"sweep   {len(data[0])} samples at {STEP_S:.0f} s, "
          f"{data[0][-1] / 3600:.1f} h, {data[1].min():.1f}-{data[1].max():.1f} K")
    print(f"anchors {len(anchors[0])} settled dwells, "
          f"{anchors[0].min():.1f}-{anchors[0].max():.1f} K")
    print(f"taus    {len(taus[0])} measured, "
          f"{taus[0].min():.1f}-{taus[0].max():.1f} K")
    rows = []
    ladder(LADDER_LAMBDA, data, anchors,
           "freedom in Lambda, C held at 4 knots", rows, taus)
    ladder(LADDER_CAP, data, anchors,
           "freedom in C, Lambda held at 9 knots", rows, taus)
    with open(LADDER_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\nwrote", LADDER_CSV)
