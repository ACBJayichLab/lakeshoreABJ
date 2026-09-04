"""Heater vs steady temperature, out of the fitted model.

This is the curve the control loop actually needs, and it comes out of the fit
without any root-finding: parameterise by temperature rather than by output.

    Q(T)  =  Lambda(T) - Lambda(T_c(T))          the power that holds T
    u(T)  =  100 * sqrt(Q R) / (G * V_fs)        exactly invertible

T_c is not a constant.  The coldplate runs 5.7 K with the heater off and 8.5 K
at 180 K, and it is measured, so it is interpolated from the settled dwells
rather than assumed -- which also makes the curve self-consistent instead of
being a family of curves indexed by a bath temperature nobody chose.

Below about 12 K the sample settles COLDER than the coldplate reads -- 4.88 K
against 5.67 K at zero power.  That is thermometry, plus whatever small heat
leak sits on the magnet side, and it is real but it is not the link: no
increasing Lambda can produce it.  The model is undefined there and the plot
says so rather than extrapolating into it.
"""
from __future__ import annotations

import csv
import math
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import PchipInterpolator

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/gain_curve.png"
N_LAM, N_CAP = 9, 4


def _g(r, k):
    try:
        return float(r[k])
    except (TypeError, ValueError):
        return math.nan


def coldplate_of(rows, n_bins=8):
    """T_c as a smooth increasing function of T_sample, from the settled dwells.

    Binned before interpolating.  A spline through all eighty points follows
    every wobble in the coldplate's own reading, and since dT_c/dT enters the
    gain those wobbles come back as spikes in a curve that should be smooth.
    """
    T = np.array([_g(r, "T_inf") for r in rows])
    C = np.array([_g(r, "Coldplate") for r in rows])
    o = np.argsort(T)
    T, C = T[o], C[o]
    edges = np.geomspace(T.min(), T.max(), n_bins + 1)
    xs, ys = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (T >= lo) & (T <= hi)
        if m.sum():
            xs.append(float(np.median(T[m])))
            ys.append(float(np.median(C[m])))
    y = np.maximum.accumulate(np.array(ys))
    return PchipInterpolator(np.array(xs), y, extrapolate=True), T.min(), T.max()


def main():
    data = F.load_sweep()
    hi = float(data[1].max())
    anchors, taus = F.load_anchors(t_max=hi), F.load_taus(t_max=hi)
    r = F.fit(N_LAM, N_CAP, data, anchors, taus)
    print(f"  Lambda {N_LAM} knots, C {N_CAP}: rms {r['rms_k']:.3f} K, "
          f"opening hold {r['hold_max_k']:.2f} K over {r['hold_h']:.1f} h")

    rows = [x for x in csv.DictReader(open(F.ANCHORS, newline="", encoding="utf-8"))
            if x.get("grade") and _g(x, "T_inf") <= hi]
    tc_of, t_lo, t_hi = coldplate_of(rows)

    T = np.geomspace(4.9, 190.0, 1200)
    Tc = np.clip(tc_of(T), 1.0, None)
    Q = r["lam"](r["pl"], T) - r["lam"](r["pl"], Tc)
    ok = Q > 0
    T, Tc, Q = T[ok], Tc[ok], Q[ok]
    u = 100.0 * np.sqrt(Q * F.R_OHM) / (F.GAIN * F.V_FS)

    # Local gain analytically, not by differencing the curve.  u(T) is very
    # flat where the steady state is steep, so np.gradient(T, u) divides by
    # nearly zero and returns spikes that are arithmetic, not cryostat.
    #
    #   dQ/dT = Lambda'(T) - Lambda'(T_c) dT_c/dT      the coldplate follows
    #   du/dT = (50/(G V_fs)) sqrt(R/Q) dQ/dT
    dQdT = (r["lam"].slope(r["pl"], T)
            - r["lam"].slope(r["pl"], Tc) * tc_of.derivative()(T))
    dudT = (50.0 / (F.GAIN * F.V_FS)) * np.sqrt(F.R_OHM / Q) * dQdT
    dTdu = 1.0 / dudT

    mT = np.array([_g(x, "T_inf") for x in rows])
    mU = np.array([_g(x, "u_pct") for x in rows])
    cd10 = np.array([x["source"].startswith("fit_cd10") for x in rows])

    fig, ax = plt.subplots(1, 3, figsize=(16.5, 5.0))
    fig.suptitle(f"LTSPM3 steady state from the fitted model — "
                 f"Λ {N_LAM} knots, C {N_CAP}, sweep rms {r['rms_k']:.2f} K",
                 fontsize=13)

    a = ax[0]
    a.plot(u, T, "-", color="#2c7a7b", lw=2.0, label="model steady state")
    a.plot(mU[~cd10], mT[~cd10], "o", ms=5, mfc="none", color="#1a202c",
           label="settled dwells, this cooldown")
    a.plot(mU[cd10], mT[cd10], "s", ms=4, mfc="none", color="#c05621",
           label="settled dwells, CD10 (other cooldown)")
    a.axvspan(0, float(u.min()), color="#e2e8f0", alpha=.7, lw=0)
    a.annotate("sample settles colder than\nthe coldplate reads — thermometry\n"
               "and stray magnet-side load", (float(u.min()), 20),
               textcoords="offset points", xytext=(30, 30), fontsize=8,
               color="#4a5568",
               arrowprops=dict(arrowstyle="->", color="#a0aec0", lw=.9))
    a.set_xlabel("heater output u  [%]"); a.set_ylabel("steady sample T  [K]")
    a.set_title("(a) heater to temperature")
    a.grid(alpha=.3); a.legend(fontsize=8, loc="upper left")

    a = ax[1]
    pred = np.interp(mU, u, T)
    a.plot(mT[~cd10], (pred - mT)[~cd10], "o", ms=5, mfc="none", color="#1a202c",
           label="this cooldown")
    a.plot(mT[cd10], (pred - mT)[cd10], "s", ms=4, mfc="none", color="#c05621",
           label="CD10")
    a.axhline(0, color="#718096", lw=.7)
    a.axhspan(-1, 1, color="#2c7a7b", alpha=.10, lw=0, label="±1 K")
    a.set_xlabel("measured steady T  [K]")
    a.set_ylabel("model − measured  [K]")
    a.set_title("(b) how well the curve reproduces the settled dwells")
    a.grid(alpha=.3); a.legend(fontsize=8)

    a = ax[2]
    a.semilogy(T, dTdu, "-", color="#2c7a7b", lw=2.0)
    for Tq, lab in ((99.6, "63.1% → 99.6 K"), (151.1, "67.0% → 151 K"),
                    (180.6, "69.0% → 181 K")):
        gq = float(np.interp(Tq, T, dTdu))
        a.plot(Tq, gq, "*", ms=13, color="#805ad5", zorder=5)
        a.annotate(f"{lab}\n{gq:.1f} K/%", (Tq, gq), textcoords="offset points",
                   xytext=(-10, -34), fontsize=8, ha="right", color="#553c9a",
                   arrowprops=dict(arrowstyle="-", color="#805ad5", lw=.8))
    a.set_xlabel("steady sample T  [K]"); a.set_ylabel("local gain dT/du  [K/%]")
    a.set_title("(c) local gain — the number that makes this hard to control")
    a.grid(alpha=.3, which="both")

    fig.tight_layout()
    fig.savefig(OUT, dpi=130)
    print("wrote", OUT)

    print(f"\n{'u %':>8}{'T_ss K':>10}{'gain K/%':>10}{'P mW':>9}")
    for uq in (20, 30, 40, 50, 55, 60, 63.076, 65, 66.95, 69.027, 72, 75):
        if uq < u.min() or uq > u.max():
            continue
        Tq = float(np.interp(uq, u, T))
        gq = float(np.interp(uq, u, dTdu))
        print(f"{uq:>8.2f}{Tq:>10.2f}{gq:>10.2f}{1e3 * F.power_w(uq):>9.1f}")


if __name__ == "__main__":
    main()
