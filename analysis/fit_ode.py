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

from _data import SWEEP as SWEEP_NAME
from _data import open_table

R_OHM, V_FS, GAIN = 75.5, 10.0, 1.11
SWEEP = SWEEP_NAME
ANCHORS = "analysis/steps.csv"

#: Margin on a settled point, in kelvin, added in quadrature to twice its own
#: extrapolation distance.  CD10 is a different cooldown from the sweep and
#: disagrees with it by 3.2 K at matched power; the recorder is the same one.
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
    with open_table(path) as fh:
        rows = list(csv.DictReader(fh))
    t = np.array([_f(r, "Time") for r in rows])
    T = np.array([_f(r, "Sample") for r in rows])
    Tc = np.array([_f(r, "Coldplate") for r in rows])
    u = np.array([_f(r, "ls218.aout1") for r in rows])
    ok = ~(np.isnan(t) | np.isnan(T) | np.isnan(Tc) | np.isnan(u))
    t, T, Tc, u = t[ok], T[ok], Tc[ok], u[ok]
    grid = np.arange(t[0], t[-1], STEP_S)
    return (grid, np.interp(grid, t, T), np.interp(grid, t, Tc),
            np.interp(grid, t, u))


def _rows(path=ANCHORS):
    with open_table(path) as fh:
        return list(csv.DictReader(fh))


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


def fit(n_lam, n_cap, data=None, anchors=None, taus=None, max_nfev=300,
        tier2=False):
    t, T, Tc, u = data if data is not None else load_sweep()
    aT, aTc, aQ, aS = anchors if anchors is not None else load_anchors()
    tauT, tauV = taus if taus is not None else load_taus()
    lam, cap, pl0, pc0 = build(n_lam, n_cap, 0.95 * T.min(), 1.05 * T.max())
    n = len(pl0)
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

    def unpack2(p):
        f = 1.0 / (1.0 + math.exp(-p[-2]))
        return min(max(f, 1e-3), 1 - 1e-3), math.exp(p[-1])

    def run(p):
        if not tier2:
            return integrate(lam, cap, p[:n], p[n:], t, Tc, u, T[0])
        f, g = unpack2(p)
        return integrate2(lam, cap, p[:n], p[n:-2], f, g, t, Tc, u, T[0])

    def resid(p):
        pl, pc = p[:n], (p[n:-2] if tier2 else p[n:])
        model = run(p)
        r_sweep = (np.log(model) - logT) / SWEEP_SIGMA_REL
        dQ = lam(pl, aT) - lam(pl, aTc) - aQ
        r_anchor = w_anchor * dQ / lam.slope(pl, aT) / aS
        shape = np.log(cap(pc, pT) / cap(pc, ref)[0])
        r_shape = w_shape * (shape - p_target)
        r_tau = w_tau * (np.log(cap(pc, tauT) / lam.slope(pl, tauT)) - log_tau)
        return np.concatenate([r_sweep, r_anchor, r_shape, r_tau])

    p0 = np.concatenate([pl0, pc0] + ([np.array(TIER2_SEED)] if tier2 else []))
    s = least_squares(resid, p0, method="trf", x_scale="jac", max_nfev=max_nfev)
    pl, pc = s.x[:n], (s.x[n:-2] if tier2 else s.x[n:])
    split, mass_ratio = unpack2(s.x) if tier2 else (float("nan"),) * 2
    model = run(s.x)
    err = model - T
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
        "n_lam": n_lam, "n_cap": n_cap, "npar": len(s.x), "nfev": s.nfev,
        "lam": lam, "cap": cap, "pl": pl, "pc": pc,
        "t": t, "T": T, "Tc": Tc, "u": u, "model": model,
        "rms_k": float(np.sqrt(np.mean(err**2))),
        "max_k": float(np.max(np.abs(err))),
        "rms_pct": float(100 * np.sqrt(np.mean((err / T) ** 2))),
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


def ladder(rows, data, anchors, title, out=None, taus=None):
    import time
    print(f"\n{title}")
    print(f"{'knots L':>8}{'knots C':>8}{'par':>5}{'rms K':>9}{'max K':>9}"
          f"{'rms %':>8}{'hold K':>8}{'anchor K':>10}{'tau res':>9}"
          f"{'mass g':>8}{'tau137':>9}{'nfev':>6}{'s':>7}")
    got = []
    for n_lam, n_cap in rows:
        t0 = time.time()
        r = fit(n_lam, n_cap, data, anchors, taus)
        print(f"{n_lam:>8}{n_cap:>8}{r['npar']:>5}{r['rms_k']:>9.3f}"
              f"{r['max_k']:>9.3f}{r['rms_pct']:>8.2f}{r['hold_max_k']:>8.2f}"
              f"{r['anchor_k']:>10.2f}"
              f"{r['tau_resid']:>9.3f}{r['mass_g']:>8.2f}{r['tau_137_s']:>9.0f}"
              f"{r['nfev']:>6}{time.time() - t0:>7.1f}", flush=True)
        got.append({"axis": title.split()[2].rstrip(","), "n_lam": n_lam,
                    "n_cap": n_cap, "npar": r["npar"], "rms_k": r["rms_k"],
                    "max_k": r["max_k"], "rms_pct": r["rms_pct"],
                    "anchor_k": r["anchor_k"], "mass_g": r["mass_g"],
                    "tau_137_s": r["tau_137_s"], "tau_resid": r["tau_resid"],
                    "hold_k": r["hold_k"], "hold_max_k": r["hold_max_k"]})
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
