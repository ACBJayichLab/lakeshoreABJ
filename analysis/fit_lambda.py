"""What the settled holds can and cannot constrain about Lambda(T).

Everything the sample touches sinks at the coldplate -- wiring, structure and
radiation alike -- so the paths are in parallel between the same two nodes and
add.  Radiation to a common cold end has the same potential-difference form as
a conduction link, so it folds in rather than sitting outside as a source:

    Lambda(T) = Lambda_cond(T) + Lambda_wire(T) + sigma_r * T**4

Steady state gives Q = Lambda(T_s) - Lambda(T_c) with no heat capacity in it.
Lambda(T_c) is one unknown additive constant shared by every point, so the
settled holds measure Lambda(T) DIRECTLY, up to that offset -- there is
nothing to fit before plotting it.  What needs testing is whether they can
SEPARATE the T**4 piece, and what the floor on any such test is.
"""
from __future__ import annotations

import csv
import math

import numpy as np
from scipy.optimize import least_squares

SRC = "analysis/steps.csv"
SIGMA = 5.670e-8
#: Above this the only dwells are CD10's 07-16 ones, taken mid-cooldown with
#: the cryostat still falling.  They relax cleanly, so steps.py grades them
#: usable and it is right to -- what disqualifies them is not the dwell but the
#: REGIME: a cooling cryostat has a different Lambda from a settled one, and no
#: temperature log records the difference.  Left in, they drag the fit to a
#: negative conductance, which is the model saying so.
T_MAX_K = 190.0


def f(r, k):
    try:
        return float(r[k])
    except (TypeError, ValueError):
        return math.nan


def settled(rows):
    return [r for r in rows if r.get("grade") and f(r, "T_inf") <= T_MAX_K]


def load(src):
    rows = settled(list(csv.DictReader(open(SRC, newline="", encoding="utf-8"))))
    g = sorted((r for r in rows if r["source"].startswith(src)),
               key=lambda r: f(r, "T_inf"))
    return (np.array([f(r, "T_inf") for r in g]),
            np.array([f(r, "Coldplate") for r in g]),
            np.array([f(r, "P_W") for r in g]),
            np.array([f(r, "u_pct") for r in g]))


def knotted(logTk):
    """Lambda as a piecewise power law: linear in (log T, log Lambda).

    A locally varying exponent is the right freedom when the exponent itself
    drifts -- and it must, since dLambda/dT falls twentyfold between 13 K and
    140 K.  A single power law cannot do that and, asked to, runs to its bound.
    """
    def lam(logLk, x):
        return np.exp(np.interp(np.log(x), logTk, logLk))
    return lam


def report(src):
    T, Tc, Q, u = load(src)
    print(f"\n=== {src}: {len(T)} settled holds, {T.min():.1f}-{T.max():.1f} K")

    # the repeatability floor: the same commanded power, held more than once
    same = {}
    for ui, Ti in zip(u, T):
        same.setdefault(round(ui, 3), []).append(Ti)
    for ui, v in sorted(same.items()):
        if len(v) > 1:
            print(f"  repeatability: u={ui}% held {len(v)}x spans "
                  f"{max(v) - min(v):.2f} K at identical power")

    if len(T) < 8 or T.max() / T.min() < 2:
        knots = np.log(np.array([T.min(), T.max()]))
    else:
        knots = np.log(np.array([T.min(), np.median(T), T.max()]))
    lam = knotted(knots)
    n = len(knots)

    def fit(rad, b_fix=None):
        def resid(p):
            logLk = p[:n]
            b = b_fix if b_fix is not None else (p[n] if rad else 0.0)
            off = p[-1]
            return 1e3 * (lam(logLk, T) + b * T**4
                          - lam(logLk, Tc) - b * Tc**4 - off - Q)
        free = rad and b_fix is None
        p0 = (list(np.log(np.linspace(Q.min(), Q.max(), n) + 1e-3))
              + ([1e-12] if free else []) + [0.0])
        lo = [-30] * n + ([0.0] if free else []) + [-1.0]
        hi = [5] * n + ([1e-9] if free else []) + [1.0]
        s = least_squares(resid, p0, bounds=(lo, hi))
        b = b_fix if b_fix is not None else (s.x[n] if free else 0.0)
        return s.x[:n], b, float(np.sqrt(np.mean(s.fun**2)))

    def slope(logLk, b, x):
        """dLambda/dT, analytic: the log-log segment slope times Lambda/T."""
        i = np.clip(np.searchsorted(knots, np.log(x)) - 1, 0, n - 2)
        B = ((logLk[i + 1] - logLk[i]) / (knots[i + 1] - knots[i])
             if n > 1 else np.zeros_like(x))
        return B * lam(logLk, x) / x + 4 * b * x**3

    logLk, _, r0 = fit(False)
    d = slope(logLk, 0.0, T)
    print(f"  piecewise power law     residual {r0:7.3f} mW = "
          f"{1e-3 * r0 / np.median(d):.2f} K")
    print(f"  dLambda/dT              {1e3 * d.min():.2f} -> {1e3 * d.max():.2f} mW/K "
          f"over {T.min():.0f}-{T.max():.0f} K")

    _, b, r1 = fit(True)
    print(f"    + sigma_r T^4 (free)  residual {r1:7.3f} mW  "
          f"-- {'NO improvement' if r1 > 0.98 * r0 else 'improves'}: "
          f"radiation is degenerate with conduction here")

    # largest sigma_r that still fits: how much radiation the data TOLERATES
    tol = 1.10 * r0
    hi_b = 0.0
    for b_try in np.logspace(-14, -9, 200):
        lk, _, r = fit(True, b_fix=b_try)
        if r > tol:
            break
        hi_b = b_try
    dtop = slope(*fit(True, b_fix=hi_b)[:2], np.array([T.max()]))[0]
    print(f"  tolerated radiation     sigma_r <= {hi_b:.2e} W/K^4  ->  "
          f"eps*A <= {1e4 * hi_b / SIGMA:.1f} cm² (eps=1), which would be "
          f"{100 * 4 * hi_b * T.max()**3 / dtop:.0f}% of dLambda/dT at {T.max():.0f} K")


if __name__ == "__main__":
    for src in ("fit_cd10", "fit_recorder"):
        report(src)
