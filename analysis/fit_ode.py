"""Fit the tier-1 ODE to the 8.8 h sweep, anchored on the settled holds.

    C(T) dT/dt = Q(u) - [ Lambda(T) - Lambda(T_c(t)) ]

Both u(t) and T_c(t) are driven from the log, so the only unknowns are the two
curves.  Both are parameterised the same way -- a monotone cubic through knots
in (log T, log y), with the knot values forced increasing because
Lambda' = k*A/L > 0 and because nothing in copper, sapphire or diamond gives a
falling heat capacity between 5 K and 190 K.  The number of knots in each is
the complexity knob, and they are turned one at a time: a joint grid confounds
the two and hides the fact that they are not equally constrained.

The settled holds enter as extra residuals rather than as hard constraints.
They deserve a margin: u=63.072% was held three times in one cooldown and
landed 2.84 K apart, and the two cooldowns disagree by 3.2 K at matched power.
So a recorder hold (same cooldown as the sweep) is worth +-1 K and a CD10 hold
is worth +-3 K, and the fit is free to miss them by that much.

Integration is exponential Euler: T += g*tau*(1 - exp(-dt/tau)) with
tau = C/Lambda'.  It is exact for the linearised relaxation and unconditionally
stable, which matters because C falls steeply at the cold end and an explicit
step would go unstable there long before the interesting physics did.
"""
from __future__ import annotations

import csv
import math
from bisect import bisect_right

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares

R_OHM, V_FS, GAIN = 75.5, 10.0, 1.11
SWEEP = "data/heater calibration steps/region_20260903-123832_complete_sweep.csv"
ANCHORS = "analysis/steady_points.csv"

#: Margin on a settled hold, in kelvin.  Same cooldown as the sweep, or not.
ANCHOR_SIGMA_K = {"fit_recorder": 1.0, "fit_cd10": 3.0}
#: Fractional margin on a sweep sample.  Residuals are fitted in log T so that
#: 5 K counts as much as 187 K; an absolute-K objective would ignore the whole
#: cold end, which is the half with no settled anchor in it.
SWEEP_SIGMA_REL = 0.01
#: The anchors together carry this share of the sweep's weight.
ANCHOR_SHARE = 0.10
#: Sub-sample the sweep to this cadence.  The fastest pole of interest is
#: minutes; 4 s is already far finer.
STEP_S = 4.0

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


def load_sweep(path=SWEEP):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    t = np.array([_f(r, "Time") for r in rows])
    T = np.array([_f(r, "Sample") for r in rows])
    Tc = np.array([_f(r, "Coldplate") for r in rows])
    u = np.array([_f(r, "ls218.aout1") for r in rows])
    ok = ~(np.isnan(t) | np.isnan(T) | np.isnan(Tc) | np.isnan(u))
    t, T, Tc, u = t[ok], T[ok], Tc[ok], u[ok]
    grid = np.arange(t[0], t[-1], STEP_S)
    return (grid, np.interp(grid, t, T), np.interp(grid, t, Tc),
            np.interp(grid, t, u))


def load_anchors(path=ANCHORS):
    out = []
    for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
        if abs(_f(r, "drift_K_per_h")) < 0.15 and _f(r, "hold_h") > 1.0:
            src = "fit_recorder" if r["source"].startswith("fit_recorder") else "fit_cd10"
            out.append((_f(r, "T_K"), _f(r, "Coldplate"), _f(r, "P_W"),
                        ANCHOR_SIGMA_K[src]))
    a = np.array(out)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


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
        self.lk = np.log(np.asarray(T_knots, float))
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


def integrate(lam, cap, pl, pc, t, Tc, u, T0):
    """Exponential Euler down the sweep.  Returns the modelled T(t)."""
    Q = power_w(u)
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


def fit(n_lam, n_cap, data=None, anchors=None, max_nfev=300):
    t, T, Tc, u = data if data is not None else load_sweep()
    aT, aTc, aQ, aS = anchors if anchors is not None else load_anchors()
    lam, cap, pl0, pc0 = build(n_lam, n_cap, 0.95 * T.min(), 1.05 * T.max())
    n = len(pl0)
    w_anchor = math.sqrt(ANCHOR_SHARE * len(t) / len(aT))
    logT = np.log(T)

    pT = np.geomspace(T.min(), T.max(), 12)
    ref = np.array([CAP_SHAPE_REF_K])
    p_target = np.log(mix_c(pT) / mix_c(ref)[0])
    w_shape = (math.sqrt(CAP_SHAPE_SHARE * len(t) / len(pT))
               / math.log(CAP_SHAPE_FACTOR))

    def resid(p):
        pl, pc = p[:n], p[n:]
        model = integrate(lam, cap, pl, pc, t, Tc, u, T[0])
        r_sweep = (np.log(model) - logT) / SWEEP_SIGMA_REL
        dQ = lam(pl, aT) - lam(pl, aTc) - aQ
        r_anchor = w_anchor * dQ / lam.slope(pl, aT) / aS
        shape = np.log(cap(pc, pT) / cap(pc, ref)[0])
        r_shape = w_shape * (shape - p_target)
        return np.concatenate([r_sweep, r_anchor, r_shape])

    s = least_squares(resid, np.concatenate([pl0, pc0]),
                      method="trf", x_scale="jac", max_nfev=max_nfev)
    pl, pc = s.x[:n], s.x[n:]
    model = integrate(lam, cap, pl, pc, t, Tc, u, T[0])
    err = model - T
    return {
        "n_lam": n_lam, "n_cap": n_cap, "npar": len(s.x), "nfev": s.nfev,
        "lam": lam, "cap": cap, "pl": pl, "pc": pc,
        "t": t, "T": T, "Tc": Tc, "u": u, "model": model,
        "rms_k": float(np.sqrt(np.mean(err**2))),
        "max_k": float(np.max(np.abs(err))),
        "rms_pct": float(100 * np.sqrt(np.mean((err / T) ** 2))),
        "anchor_k": float(np.sqrt(np.mean(
            ((lam(pl, aT) - lam(pl, aTc) - aQ) / lam.slope(pl, aT)) ** 2))),
        "mass_g": float(cap(pc, np.array([CAP_SHAPE_REF_K]))[0]
                        / mix_c(np.array([CAP_SHAPE_REF_K]))[0]),
        "tau_137_s": float(cap(pc, np.array([CAP_SHAPE_REF_K]))[0]
                           / lam.slope(pl, np.array([CAP_SHAPE_REF_K]))[0]),
    }


#: Vary one curve's freedom at a time.  A joint grid confounds the two and
#: hides the answer, which is that they are not equally constrained.
LADDER_LAMBDA = [(n, 3) for n in range(2, 10)]
LADDER_CAP = [(6, n) for n in range(2, 8)]


def ladder(rows, data, anchors, title, out=None):
    import time
    print(f"\n{title}")
    print(f"{'knots L':>8}{'knots C':>8}{'par':>5}{'rms K':>9}{'max K':>9}"
          f"{'rms %':>8}{'anchor K':>10}{'mass g':>8}{'tau137':>9}"
          f"{'nfev':>6}{'s':>7}")
    got = []
    for n_lam, n_cap in rows:
        t0 = time.time()
        r = fit(n_lam, n_cap, data, anchors)
        print(f"{n_lam:>8}{n_cap:>8}{r['npar']:>5}{r['rms_k']:>9.3f}"
              f"{r['max_k']:>9.3f}{r['rms_pct']:>8.2f}{r['anchor_k']:>10.2f}"
              f"{r['mass_g']:>8.2f}{r['tau_137_s']:>9.0f}"
              f"{r['nfev']:>6}{time.time() - t0:>7.1f}", flush=True)
        got.append({"axis": title.split()[2].rstrip(","), "n_lam": n_lam,
                    "n_cap": n_cap, "npar": r["npar"], "rms_k": r["rms_k"],
                    "max_k": r["max_k"], "rms_pct": r["rms_pct"],
                    "anchor_k": r["anchor_k"], "mass_g": r["mass_g"],
                    "tau_137_s": r["tau_137_s"]})
    if out is not None:
        out.extend(got)
    return got


LADDER_CSV = "analysis/ladder.csv"


if __name__ == "__main__":
    data, anchors = load_sweep(), load_anchors()
    print(f"sweep   {len(data[0])} samples at {STEP_S:.0f} s, "
          f"{data[0][-1] / 3600:.1f} h, {data[1].min():.1f}-{data[1].max():.1f} K")
    print(f"anchors {len(anchors[0])} settled holds, "
          f"margins {sorted(set(anchors[3]))} K")
    rows = []
    ladder(LADDER_LAMBDA, data, anchors, "freedom in Lambda, C held at 3 knots", rows)
    ladder(LADDER_CAP, data, anchors, "freedom in C, Lambda held at 6 knots", rows)
    with open(LADDER_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\nwrote", LADDER_CSV)
