"""The coldplate is not a bath.  It is a first-order lag driven by the heater.

`fit_ode` treats `T_c(t)` as an exogenous input, read from the log.  For fitting
that is exact and free -- the sink temperature is measured, so nothing has to be
inferred.  For *control* it is useless, and quietly so: raising the sample
heater warms the coldplate too, and that comes straight back as a change in the
sample's own sink temperature.  A plant that takes `T_c` as given cannot see
that loop at all, and there is no log to read from when you are predicting.

So fit it.  Over the whole 43 h sweep, one pole on a monotone steady-state
curve in heater power::

    T_c_inf = f(Q)                     monotone, 6 knots, 0 -> 800 mW
    tau dT_c/dt = T_c_inf - T_c

MEASURED, 2026-09-05, on the corrected Coldplate curve:

===========================  ==========================================
tau_bath                     **175 s** (2.9 min)
whole record                 **27.6 mK rms**, 617 mK max, over a 2.30 K swing
the 22.8 h opening hold      1.5 mK rms
the 13.9 h closing hold      3.8 mK rms
the 7.5 h excursion          65.8 mK rms
T_c at u = 0                 4.80 K
T_c at u = 70%               6.93 K
===========================  ==========================================

That is 1.2% of the swing from a two-parameter-family model, which is about as
well as the sample's own thermometer is read.

**Why it matters for tuning, and it is not a small effect.**  tau_bath = 175 s
against the sample's tau = 572 s at 137 K: the sink moves at a third of the
rate the sample does, not at zero rate and not instantaneously.  The loop sees
a second pole of comparable order, and it is a pole in the direction that
*helps* -- warming the sink reduces the gradient the heater has to maintain, so
the open-loop step is faster than a fixed-sink model predicts and the fitted
plant is conservative.  Sizing gains on the fixed-sink plant is therefore safe
but leaves performance on the table.

**What it does not explain.**  Both long holds are settled to within 0.1 mK/h
in `T_c`, so none of this touches the +-0.4 K steady-state bias between them.
That is a Lambda shape question at the top of the range -- 12 knots gets it to
0.32 K -- and a lag cannot produce a steady-state error by construction.

Run it::

    python analysis/bath.py
"""
from __future__ import annotations

import sys

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402

#: Knots for the steady-state curve, in watts.  Six across 0 to full scale: the
#: relation is smooth and monotone and there is no structure in it to resolve,
#: which is the whole reason this works with so few parameters.
N_KNOTS = 6


class Bath:
    """``T_c_inf(Q)`` and one time constant.  Callable on a heater trace."""

    def __init__(self, knots, values, tau_s):
        self.knots = np.asarray(knots, float)
        self.values = np.asarray(values, float)
        self.tau_s = float(tau_s)
        self._f = PchipInterpolator(self.knots, self.values, extrapolate=True)

    def steady(self, q_w):
        """Where the coldplate ends up, for a heater power held forever."""
        return self._f(np.asarray(q_w, float))

    def dsteady(self, q_w):
        """dT_c/dQ, in K/W.  Analytic off the spline, because it enters the
        steady-state gain and differencing a curve there returns spikes."""
        return self._f.derivative()(np.asarray(q_w, float))

    def run(self, q_w, dt_s, t_c0):
        """Integrate the lag over a power trace.  Exponential Euler, as the
        sample ODE uses, so a coarse step stays exact for a pure relaxation."""
        alpha = 1.0 - np.exp(-dt_s / self.tau_s)
        target = self.steady(q_w)
        out = np.empty_like(target)
        acc = float(t_c0)
        for i, v in enumerate(target):
            acc += alpha * (v - acc)
            out[i] = acc
        return out


def _unpack(p, knots):
    """Base plus positive increments: the curve cannot fall with power, which
    is physics rather than a fitting convenience -- more heat into a link
    cannot make its cold end colder."""
    return np.concatenate([[p[0]], p[0] + np.cumsum(np.exp(p[1:len(knots)]))])


def fit(data=None, n_knots=N_KNOTS, max_nfev=400):
    """Fit the lag to the sweep.  Returns ``(Bath, diagnostics)``."""
    t, _T, Tc, u = data if data is not None else F.load_sweep()
    q = F.power_w(u)
    dt = float(np.median(np.diff(t)))
    knots = np.linspace(0.0, float(q.max()), n_knots)

    def model(p):
        return Bath(knots, _unpack(p, knots), np.exp(p[n_knots])).run(q, dt, Tc[0])

    p0 = np.concatenate([[Tc.min()], np.log(np.full(n_knots - 1, 0.5)),
                         [np.log(600.0)]])
    s = least_squares(lambda p: model(p) - Tc, p0, method="trf",
                      x_scale="jac", max_nfev=max_nfev)
    bath = Bath(knots, _unpack(s.x, knots), float(np.exp(s.x[n_knots])))
    err = bath.run(q, dt, Tc[0]) - Tc
    return bath, {
        "rms_k": float(np.sqrt(np.mean(err**2))),
        "max_k": float(np.abs(err).max()),
        "swing_k": float(Tc.max() - Tc.min()),
        "t": t, "Tc": Tc, "err": err, "q": q, "dt_s": dt,
    }


def main() -> int:
    data = F.load_sweep()
    bath, d = fit(data)
    h = d["t"] / 3600.0
    print(f"tau_bath = {bath.tau_s:.1f} s  ({bath.tau_s / 60:.1f} min)")
    print(f"  {1000 * d['rms_k']:.1f} mK rms, {1000 * d['max_k']:.0f} mK max, "
          f"over a {d['swing_k']:.2f} K swing "
          f"({100 * d['rms_k'] / d['swing_k']:.1f}% of it)")
    for lo, up, name in ((0, 22.5, "opening hold, 22.8 h"),
                         (30, 43, "closing hold, 13.9 h"),
                         (22.5, 30, "the excursion, 7.5 h")):
        m = (h >= lo) & (h <= up)
        e = d["err"][m]
        print(f"  {name:22s} bias {1000 * e.mean():+7.1f} mK   "
              f"rms {1000 * np.sqrt(np.mean(e**2)):7.1f} mK")
    print(f"\n{'heater %':>9}{'power mW':>11}{'T_c steady K':>15}")
    for uq in (0, 20, 40, 55, 63, 66, 69, 70):
        q = F.power_w(uq)
        print(f"{uq:>9.1f}{1e3 * q:>11.1f}{float(bath.steady(q)):>15.3f}")
    tau_s = 572.0                       # the sample at 137 K, from fit_ode
    print(f"\ntau_bath / tau_sample(137 K) = {bath.tau_s / tau_s:.2f} -- the sink "
          f"moves at a third of the sample's rate,\nwhich is neither of the two "
          f"things a fixed-sink plant can assume.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
