"""Keep the samples that carry information, and weight them by the time they stand for.

The sweep is 77,375 samples on a uniform 2 s grid, and **52% of them are one
hold**: 22.8 h at 69.027% where the sample moves 87 mK in total. Another 30%
are the 13.9 h hold. Every fit here integrates all 77,375 of them, and
`least_squares` does it again per parameter per iteration to difference the
Jacobian -- so a 14-parameter step integrates a day and a half of a cryostat
sitting still, fourteen times.

Two things make dropping most of that free rather than merely cheap.

**The integrator does not care.** `integrate` reads its step from
``dt = t[k+1] - t[k]``, and exponential Euler is *exact* for a relaxation
towards a constant target -- which is precisely what a hold is. A 600 s step
across a stretch where `u` and `T_c` are constant is not an approximation of
300 two-second steps, it is the same answer.

**The objective is preserved by weighting.** Decimating alone would silently
rewrite what is being fitted: the holds carry half the residual because they
carry half the samples, and thinning them 300:1 would quietly demote them to
noise. So every kept sample is weighted by ``sqrt(span)``, where `span` is the
time it stands for. The sum of squares is then a Riemann sum of the same
integral the uniform grid was approximating, and the fit means what it meant.

The rule for keeping a sample -- any of:

* the temperature has moved by ``tol_k`` since the last one kept, which makes
  the grid dense exactly where the dynamics are and sparse where they are not;
* the heater changed, because a step is the most informative sample in the
  record and landing one between two kept points would smear it;
* ``dt_max_s`` has elapsed, so a hold still gets sampled often enough to show a
  drift.

Run it::

    python analysis/decimate.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

from _data import DATA_DIR

sys.path.insert(0, "analysis")

#: How far the sample may move between kept points, in kelvin.  0.05 K is a
#: little above the 28 mK sample-to-sample noise, so a hold is thinned to the
#: `dt_max_s` floor while a slew keeps every sample it has.
TOL_K = 0.05

#: Longest step allowed, in seconds.  Not an accuracy limit -- the integrator
#: is exact on a hold -- but a resolution limit on the drift: at 300 s a 22.8 h
#: hold still gets 274 points, which is plenty to see -3.8 mK/h.
DT_MAX_S = 300.0

#: A heater move smaller than this is the 218's readback flicker, not a
#: command.  Measured at 0.003% in docs/ltspm3/cryostat.md.
U_EPS_PCT = 0.005


#: Seconds of smoothing before deciding whether the signal has MOVED.
#:
#: This is the difference between a 5x thinning and a 34x one, and it is the
#: only subtle parameter here.  The decision is "has the underlying temperature
#: changed", and comparing raw samples answers "has the temperature plus 28 mK
#: of noise changed" -- which on a dead-flat hold is yes, constantly, so the
#: holds were being kept almost in full for no information at all.  At 60 s the
#: noise on the decision signal is 28/sqrt(30) = 5 mK and `tol_k` is ten sigma.
#:
#: Only the DECISION is smoothed.  The values kept are the raw ones, because
#: the residual has to be against what the instrument said.
SMOOTH_S = 60.0


def _smooth(t, y, tau_s):
    """Zero-phase single-pole, run forwards then backwards.  Forwards only
    would delay the decision by tau and shift every retained transient late."""
    if not tau_s:
        return np.asarray(y, float)
    dt = float(np.median(np.diff(t)))
    a = 1.0 - np.exp(-dt / tau_s)
    out = np.asarray(y, float).copy()
    acc = out[0]
    for i, v in enumerate(out):
        acc += a * (v - acc)
        out[i] = acc
    acc = out[-1]
    for i in range(len(out) - 1, -1, -1):
        acc += a * (out[i] - acc)
        out[i] = acc
    return out


def select(t, T, u, tol_k=TOL_K, dt_max_s=DT_MAX_S, u_eps=U_EPS_PCT,
           smooth_s=SMOOTH_S):
    """Indices to keep, and the span in seconds each one stands for.

    The first and last samples are always kept, so the record's extent and the
    initial condition are untouched.
    """
    t = np.asarray(t, float)
    decide = _smooth(t, T, smooth_s)
    keep = [0]
    last_t, last_T, last_u = t[0], decide[0], u[0]
    for i in range(1, len(t) - 1):
        if (abs(decide[i] - last_T) >= tol_k
                or abs(u[i] - last_u) > u_eps
                or t[i] - last_t >= dt_max_s):
            keep.append(i)
            last_t, last_T, last_u = t[i], decide[i], u[i]
    keep.append(len(t) - 1)
    idx = np.array(keep, dtype=int)

    # Each kept sample stands for the half-interval either side of it, so the
    # spans tile the record exactly and sum to its full duration.
    edges = np.empty(len(idx) + 1)
    edges[0] = t[idx[0]]
    edges[-1] = t[idx[-1]]
    edges[1:-1] = 0.5 * (t[idx[:-1]] + t[idx[1:]])
    return idx, np.diff(edges)


def decimate(data, **kw):
    """``(t, T, Tc, u)`` -> the same, thinned, plus the per-sample spans."""
    t, T, Tc, u = data
    idx, span = select(t, T, u, **kw)
    return (t[idx], T[idx], Tc[idx], u[idx]), span, idx


def weights(span):
    """``sqrt(span/mean)``, so a weighted sum of squares is the time integral
    the uniform grid was approximating -- and so the numbers stay the same
    size, which keeps every tolerance in `fit_ode` meaning what it meant."""
    span = np.asarray(span, float)
    return np.sqrt(span / span.mean())


#: Where the processed sweep is written.  Versioned beside the other fit
#: inputs: it is derived, but it is derived by this file from a table that is
#: already in the repository, and having it committed means the expensive
#: pipeline runs from a clone without a preparation step nobody documents.
OUT_NAME = "sweep_decimated.csv.gz"


def write(path=None, data=None, **kw):
    """Write the thinned sweep as a recorder-shaped CSV plus ``span_s``.

    Every column the fit reads is carried through unchanged -- these are the
    instrument's own numbers at the kept instants, not averages of the samples
    that were dropped.  Averaging would look tidier and would be wrong: it
    would put a value in the record that the box never reported, and on a slew
    it would report the middle of a 8 K step as though it were measured.

    ``Timestamp`` is written as well as ``Time``.  The relative clock is all a
    single-record fit needs, but the campaign drift of ``REFIT_PLAN.md`` §2.3 is
    a rate per DAY, and a table that only knows how far it is into its own run
    cannot be placed on that axis at all.  It costs about 15 kB.
    """
    import csv
    import datetime as dt
    import gzip

    import fit_ode as F
    full = data if data is not None else F.load_sweep()
    t0 = F.sweep_window().epoch[0]
    t, T, Tc, u = full
    idx, span = select(t, T, u, **kw)
    if path is None:
        path = os.path.join(DATA_DIR, OUT_NAME)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Timestamp", "Time", "span_s", "Sample", "Coldplate",
                    "ls218.aout1"])
        for k, sp in zip(idx, span):
            stamp = dt.datetime.fromtimestamp(t0 + t[k])
            w.writerow([stamp.isoformat(timespec="milliseconds"),
                        f"{t[k]:.3f}", f"{sp:.3f}", f"{T[k]:.4f}",
                        f"{Tc[k]:.4f}", f"{u[k]:.4f}"])
    return path, len(idx), len(t)


def main() -> int:
    import fit_ode as F
    full = F.load_sweep()
    t, T, _Tc, u = full
    small, span, idx = decimate(full)
    h = t / 3600.0
    print(f"full      {len(t):>7} samples, uniform {np.median(np.diff(t)):.0f} s")
    print(f"decimated {len(idx):>7} samples  ({len(t) / len(idx):.0f}x fewer), "
          f"steps {np.diff(small[0]).min():.0f}-{np.diff(small[0]).max():.0f} s")
    print(f"spans sum to {span.sum() / 3600:.2f} h against the record's "
          f"{(t[-1] - t[0]) / 3600:.2f} h\n")

    print(f"{'stretch':24s}{'hours':>8}{'full':>9}{'kept':>7}{'ratio':>8}")
    for lo, up, name in ((0, 22.5, "opening hold, 69.027%"),
                         (22.5, 30, "the excursion"),
                         (30, 43.1, "closing hold, 69.998%")):
        m = (h >= lo) & (h < up)
        k = ((h[idx] >= lo) & (h[idx] < up)).sum()
        print(f"{name:24s}{up - lo:>8.1f}{m.sum():>9}{k:>7}"
              f"{m.sum() / max(k, 1):>7.0f}x")

    # Does the thinned grid still carry the trajectory?  Put it back on the
    # full grid and compare -- this is interpolation error, not model error,
    # and it has to be far below the 28 mK the thermometer itself has.
    back = np.interp(t, small[0], small[1])
    err = back - T
    print("\nlinear reconstruction of the full trace from the kept points:")
    print(f"  {1000 * np.sqrt(np.mean(err**2)):.1f} mK rms, "
          f"{1000 * np.abs(err).max():.0f} mK max, against 28 mK of sensor noise")
    for lo, up, name in ((0, 22.5, "opening hold"), (22.5, 30, "the excursion"),
                         (30, 43.1, "closing hold")):
        m = (h >= lo) & (h < up)
        print(f"  {name:16s}{1000 * np.sqrt(np.mean(err[m]**2)):>8.1f} mK rms"
              f"{1000 * np.abs(err[m]).max():>9.0f} mK max")

    if "--write" in sys.argv:
        path, kept, was = write(data=full)
        print(f"\nwrote {path}  ({kept} of {was} rows, "
              f"{os.path.getsize(path) / 1024:.0f} kB)")
    else:
        print(f"\n--write to save it to {os.path.join(DATA_DIR, OUT_NAME)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
