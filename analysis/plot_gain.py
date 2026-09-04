"""Heater vs steady temperature, out of the fitted model.

Drawn twice, against heater percent and against heater power.

Percent is what this cryostat is commanded in, and it carries this rig's whole
actuator chain -- the 218's full scale and the 1.11 voltage gain in front of
the heater.  None of that transfers.  Power does: anyone driving the same
75.5 ohm heater can put their own cryostat on the watts axis and compare
directly, and the differential gain in K/W is the thermal resistance of their
link, not a property of anybody's DAC.

The curve comes out of the fit without root-finding, by parameterising on
temperature rather than on output:

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


def figure(mode, r, T, Tc, Q, u, dTdu, rows, out):
    """One figure; `mode` picks the input axis, percent or power."""
    watts = mode == "watt"
    x = 1e3 * Q if watts else u
    xlabel = ("heater power  P = V² / 75.5 Ω   [mW]" if watts
              else "heater output u  [%]")
    mX = (1e3 * np.array([_g(v, "P_W") for v in rows]) if watts
          else np.array([_g(v, "u_pct") for v in rows]))
    mT = np.array([_g(v, "T_inf") for v in rows])
    cd10 = np.array([v["source"].startswith("fit_cd10") for v in rows])

    # dT/dP = (dT/du)(du/dP), and dP/du = 2P/u, so dT/dP = dT/du * u / (2P).
    # It is the differential thermal resistance of the link and the only one of
    # these two curves another cryostat can be compared against.
    gain = dTdu * u / (2.0 * Q) if watts else dTdu
    gname = "dT/dP  [K/W]" if watts else "dT/du  [K/%]"

    fig, ax = plt.subplots(1, 3, figsize=(16.5, 5.0))
    fig.suptitle("LTSPM3 steady state from the fitted model — "
                 f"Λ {N_LAM} knots, C {N_CAP}, sweep rms {r['rms_k']:.2f} K"
                 + ("   ·   power axis: transferable to any cryostat on the "
                    "same 75.5 Ω heater" if watts else
                    "   ·   percent axis: this rig's DAC and 1.11 voltage gain"),
                 fontsize=12.5)

    a = ax[0]
    a.plot(x, T, "-", color="#2c7a7b", lw=2.0, label="model steady state")
    a.plot(mX[~cd10], mT[~cd10], "o", ms=5, mfc="none", color="#1a202c",
           label="settled dwells, this cooldown")
    a.plot(mX[cd10], mT[cd10], "s", ms=4, mfc="none", color="#c05621",
           label="settled dwells, CD10 (other cooldown)")
    a.axvspan(0, float(x.min()), color="#e2e8f0", alpha=.7, lw=0)
    a.annotate("sample settles colder than\nthe coldplate reads — thermometry\n"
               "and stray magnet-side load", (float(x.min()), 20),
               textcoords="offset points", xytext=(30, 30), fontsize=8,
               color="#4a5568",
               arrowprops=dict(arrowstyle="->", color="#a0aec0", lw=.9))
    a.set_xlabel(xlabel); a.set_ylabel("steady sample T  [K]")
    a.set_title("(a) heater to temperature")
    a.grid(alpha=.3); a.legend(fontsize=8, loc="upper left")

    a = ax[1]
    pred = np.interp(mX, x, T)
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
    a.semilogy(T, gain, "-", color="#2c7a7b", lw=2.0,
               label="differential  " + ("dT/dP" if watts else "dT/du"))
    if watts:
        a.semilogy(T, (T - Tc) / Q, "--", color="#805ad5", lw=1.6,
                   label="secant  (T − T$_c$) / P")
        a.legend(fontsize=8, loc="lower right")
    for Tq, lab in ((99.6, "99.6 K"), (151.1, "151 K"), (180.6, "181 K")):
        gq = float(np.interp(Tq, T, gain))
        a.plot(Tq, gq, "*", ms=13, color="#805ad5", zorder=5)
        a.annotate(f"{lab}\n{gq:.0f}" + (" K/W" if watts else " K/%"),
                   (Tq, gq), textcoords="offset points", xytext=(-10, -36),
                   fontsize=8, ha="right", color="#553c9a",
                   arrowprops=dict(arrowstyle="-", color="#805ad5", lw=.8))
    a.set_xlabel("steady sample T  [K]"); a.set_ylabel(gname)
    a.set_title("(c) " + ("thermal resistance of the link"
                          if watts else "local gain"))
    a.grid(alpha=.3, which="both")

    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("wrote", out)


def main():
    data = F.load_sweep()
    hi = float(data[1].max())
    anchors, taus = F.load_anchors(t_max=hi), F.load_taus(t_max=hi)
    r = F.fit(N_LAM, N_CAP, data, anchors, taus)
    print(f"  Lambda {N_LAM} knots, C {N_CAP}: rms {r['rms_k']:.3f} K, "
          f"opening hold {r['hold_max_k']:.2f} K over {r['hold_h']:.1f} h")

    rows = [x for x in csv.DictReader(open(F.ANCHORS, newline="", encoding="utf-8"))
            if x.get("grade") and _g(x, "T_inf") <= hi]
    tc_of, _, _ = coldplate_of(rows)

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

    figure("pct", r, T, Tc, Q, u, dTdu, rows, OUT)
    figure("watt", r, T, Tc, Q, u, dTdu, rows,
           OUT.replace(".png", "_watts.png"))

    print(f"\n{'P mW':>9}{'u %':>8}{'T_ss K':>10}{'dT/dP K/W':>11}"
          f"{'secant K/W':>12}{'dT/du K/%':>11}")
    for Pq in (25, 50, 100, 200, 300, 400, 500, 600, 650, 700, 780):
        q = Pq * 1e-3
        if q < Q.min() or q > Q.max():
            continue
        Tq = float(np.interp(q, Q, T))
        print(f"{Pq:>9.0f}{float(np.interp(q, Q, u)):>8.2f}{Tq:>10.2f}"
              f"{float(np.interp(q, Q, dTdu * u / (2 * Q))):>11.0f}"
              f"{(Tq - float(np.interp(q, Q, Tc))) / q:>12.0f}"
              f"{float(np.interp(q, Q, dTdu)):>11.2f}")


if __name__ == "__main__":
    main()
