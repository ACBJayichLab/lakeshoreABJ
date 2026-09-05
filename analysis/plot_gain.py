"""Heater vs steady temperature, out of the fitted model.

One figure, four panels.  **Percent appears exactly once**, in panel (a),
because percent is what somebody standing at this cryostat types -- and it
carries this rig's whole actuator chain, the 218's full scale and the 1.11
voltage gain in front of the heater, none of which transfers anywhere.
Everything else is watts.  Power does transfer: anyone driving the same
75.5 ohm heater can put their own cryostat on the same axis, and the
differential dT/dP in K/W IS the thermal resistance of their link rather than
a property of anybody's DAC.

(This used to draw the whole three-panel figure twice, once per axis.  Two
figures differing only in the abscissa is two things to keep in step and one
of them is always the stale one.)

The curve comes out of the fit without root-finding, by parameterising on
temperature rather than on output:

    Q(T)  =  Lambda(T) - Lambda(T_c(T))          the power that holds T
    u(T)  =  100 * sqrt(Q R) / (G * V_fs)        exactly invertible

T_c is not a constant.  The coldplate runs 4.7 K with the heater off and 6.9 K
at 180 K, and it is measured, so it is interpolated from the settled dwells
rather than assumed -- which also makes the curve self-consistent instead of
being a family of curves indexed by a bath temperature nobody chose.

Those two numbers used to read 5.7 and 8.5.  The 218 was carrying another
thermometer's curve on input 2 until 2026-09-04, and reference/heater-calibration/
has since been remapped; see analysis/README.md.  What went with it was the
reason this model was declared undefined below ~12 K: the sample appeared to
settle COLDER than its own heat sink -- 4.88 K against 5.67 K at zero power --
which no increasing Lambda can produce.  On the corrected curve the coldplate
is at 4.67 K and the sample sits 0.21 K above it, an ordinary small parasitic
load and nothing a Lambda cannot represent.

The shaded region below the fit and the note on the plot are STILL DRAWN, and
should be, until somebody re-derives the low-temperature end on the corrected
T_c.  The anomaly that motivated the restriction is gone; whether the fit now
extends down there is a separate question that has not been asked yet.
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
import bath as B  # noqa: E402
import fit_ode as F  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/gain_curve.png"

#: The production model, as opposed to the complexity study in plot_ode.py.
#:
#: Fitted on the adaptively decimated sweep (analysis/decimate.py) with a
#: three-knot slow drift in the steady state and a curvature penalty on
#: dLambda/dT -- 0.168 K rms over 43 h against 0.4467 for (9, 4) on the full
#: grid with no drift, and a conductance four times smoother than twenty free
#: knots would give.
#: The drift is 2 mW peak to peak on an 800 mW heater and cannot move faster
#: than about 11 h, so it changes where the cryostat settles and leaves the
#: dynamics alone: tau(137 K) and the implied mass are the same either way.
N_LAM, N_CAP, N_DRIFT = 20, 4, 3


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


def figure(r, T, Tc, Q, u, dTdu, rows, out):
    """One figure: percent once, because that is what the operator types, and
    watts everywhere else, because that is the half another cryostat can use.

    Percent carries this rig's entire actuator chain -- the 218's full scale
    and the 1.11 voltage gain in front of the heater -- and none of it
    transfers.  Power does: the differential dT/dP in K/W IS the thermal
    resistance of the link.  So panel (a) is the one anyone standing at this
    cryostat needs, and (b) to (d) are the physics.
    """
    P = 1e3 * Q                                       # mW
    mU = np.array([_g(v, "u_pct") for v in rows])
    mP = 1e3 * np.array([_g(v, "P_W") for v in rows])
    mT = np.array([_g(v, "T_inf") for v in rows])
    cd10 = np.array([v["source"].startswith("fit_cd10") for v in rows])

    # CD10 is a different cooldown -- different contact, different radiation,
    # a different parasitic load -- and no single Lambda can satisfy both.  The
    # fit measures that as ONE free power offset (fit_ode.anchor_groups), so
    # CD10's dwells are drawn against the same curve shifted by it rather than
    # against a curve that was never theirs.  Without this they sat 2.2 K high
    # as a body, which reads as a model error and is not one.
    offset_mw = 1e3 * float(r["group_w"][0]) if r["n_group"] else 0.0
    mP = mP + np.where(cd10, offset_mw, 0.0)
    mU = np.where(cd10, 100.0 * np.sqrt(np.maximum(mP, 0) * 1e-3 * F.R_OHM)
                  / (F.GAIN * F.V_FS), mU)
    cd10_label = (f"settled dwells, CD10 ({offset_mw:+.1f} mW)" if offset_mw
                  else "settled dwells, CD10 (other cooldown)")


    fig, ax = plt.subplots(1, 4, figsize=(21.0, 5.0))
    fig.suptitle("LTSPM3 steady state from the fitted model — "
                 f"Λ {N_LAM} knots, C {N_CAP}, slow drift {N_DRIFT} knots — "
                 f"sweep rms {r['rms_k']:.2f} K over 43 h"
                 "   ·   T$_c$ on the corrected Coldplate curve (X186279)",
                 fontsize=12.5)

    def dwells(a, x, mx, ylabel, title):
        a.plot(x, T, "-", color="#2c7a7b", lw=2.0, label="model steady state")
        a.plot(mx[~cd10], mT[~cd10], "o", ms=5, mfc="none", color="#1a202c",
               label="settled dwells, this cooldown")
        a.plot(mx[cd10], mT[cd10], "s", ms=4, mfc="none", color="#c05621",
               label=cd10_label)
        a.axvspan(0, float(x.min()), color="#e2e8f0", alpha=.7, lw=0)
        a.set_xlabel(ylabel); a.set_ylabel("steady sample T  [K]")
        a.set_title(title)
        a.grid(alpha=.3); a.legend(fontsize=8, loc="upper left")

    dwells(ax[0], u, mU, "heater output u  [%]",
           "(a) what to type — this rig's DAC and 1.11 gain")
    ax[0].annotate("below the fitted range —\nnot re-derived since\n"
                   "the 2026-09-04 T$_c$ remap", (float(u.min()), 20),
                   textcoords="offset points", xytext=(30, 30), fontsize=8,
                   color="#4a5568",
                   arrowprops=dict(arrowstyle="->", color="#a0aec0", lw=.9))

    dwells(ax[1], P, mP, "heater power  P = V² / 75.5 Ω   [mW]",
           "(b) the transferable one — same heater, any cryostat")

    a = ax[2]
    pred = np.interp(mP, P, T)
    a.plot(mT[~cd10], (pred - mT)[~cd10], "o", ms=5, mfc="none",
           color="#1a202c", label="this cooldown")
    a.plot(mT[cd10], (pred - mT)[cd10], "s", ms=4, mfc="none",
           color="#c05621", label=f"CD10 ({offset_mw:+.1f} mW)")
    a.axhline(0, color="#718096", lw=.7)
    a.axhspan(-1, 1, color="#2c7a7b", alpha=.10, lw=0, label="±1 K")
    a.set_xlabel("measured steady T  [K]")
    a.set_ylabel("model − measured  [K]")
    a.set_title("(c) against the settled dwells, each cooldown on its own offset")
    a.grid(alpha=.3); a.legend(fontsize=8)

    # Panel (d) is tau, not thermal resistance.  dT/dP is a static property and
    # it is in the printed table below; tau is what a settle costs, what a
    # sweep rate has to respect, and what sets every gain in pid_tuning.  It is
    # also the honest one to draw, because seventeen relaxations were measured
    # and no thermal resistance ever was.
    a = ax[3]
    tau = r["cap"](r["pc"], T) / r["lam"].slope(r["pl"], T)
    a.loglog(T, tau / 60.0, "-", color="#2c7a7b", lw=2.0,
             label="fitted  τ = C / (dΛ/dT)")
    tT, tV = F.load_taus(t_max=float(T.max()))
    a.plot(tT, tV / 60.0, "o", ms=6, mfc="none", mew=1.4, color="#1a202c",
           zorder=5, label=f"{len(tT)} measured relaxations")
    for Tq, tq in ((137.3, 620.0), (137.0, 709.0)):
        a.plot(Tq, tq / 60.0, "*", ms=14, color="#805ad5", zorder=6)
    a.annotate("620 s and 709 s at 137 K,\nmeasured independently",
               (137.0, 709.0 / 60.0), textcoords="offset points",
               xytext=(-16, -50), fontsize=8, ha="right", color="#553c9a",
               arrowprops=dict(arrowstyle="-", color="#805ad5", lw=.8))
    a.set_xlabel("steady sample T  [K]"); a.set_ylabel("τ  [min]")
    a.set_title("(d) time constant — what a settle actually costs")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="upper left")

    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("wrote", out)


def main():
    data, w = F.load_decimated()
    hi = float(data[1].max())
    anchors, taus = F.load_anchors(t_max=hi), F.load_taus(t_max=hi)
    groups = F.anchor_groups(t_max=hi)
    r = F.fit(N_LAM, N_CAP, data, anchors, taus, weights=w, n_drift=N_DRIFT,
              groups=groups)
    print(f"  Lambda {N_LAM} knots, C {N_CAP}, drift {N_DRIFT}: "
          f"rms {r['rms_k']:.3f} K, opening hold {r['hold_max_k']:.2f} K "
          f"over {r['hold_h']:.1f} h")
    if N_DRIFT:
        print(f"  slow drift {1e3 * r['drift_w'].min():+.2f} to "
              f"{1e3 * r['drift_w'].max():+.2f} mW over {hi and 43:.0f} h, "
              f"against an 800 mW heater")

    rows = [x for x in csv.DictReader(open(F.ANCHORS, newline="", encoding="utf-8"))
            if x.get("grade") and _g(x, "T_inf") <= hi]
    tc_of, _, _ = coldplate_of(rows)

    T = np.geomspace(4.9, 190.0, 1200)

    # T_c comes from the fitted bath, not from the dwell map, and the two are
    # not the same claim.  coldplate_of() maps T_c against T_SAMPLE, which is a
    # correlation between two things the heater drives; bath.py fits T_c
    # against POWER, which is the causal direction, to 27.6 mK over the whole
    # record.  Swapping them halves the residual trend against the settled
    # dwells -- -6.4 to -3.6 mK/K on this cooldown, -11.4 to -3.3 on CD10,
    # whose correlation falls from -0.50 to -0.16.
    #
    # It is implicit, since Q depends on T_c and T_c on Q, so it is iterated.
    # T_c moves 2.3 K against a sample at 5-190 K, so this converges in three.
    bath, _ = B.fit(F.load_sweep())
    Tc = np.clip(tc_of(T), 1.0, None)
    for _ in range(4):
        Q = r["lam"](r["pl"], T) - r["lam"](r["pl"], Tc)
        Tc = np.clip(bath.steady(np.clip(Q, 0.0, None)), 1.0, None)
    Q = r["lam"](r["pl"], T) - r["lam"](r["pl"], Tc)

    # Stop at the lowest power anybody actually held, not at Q > 0.  As the
    # sample approaches its own heat sink both Lambda(T) - Lambda(T_c) and
    # dQ/dT go to zero together, so dT/dP is 0/0 and the curve grows a spike
    # that is arithmetic rather than cryostat -- and it appears exactly where
    # there is no measurement to contradict it.  The floor is the smallest
    # settled dwell's power, so the curve ends where the evidence does.
    floor = float(np.nanmin([_g(v, "P_W") for v in rows]))
    ok = Q >= floor
    T, Tc, Q = T[ok], Tc[ok], Q[ok]
    u = 100.0 * np.sqrt(Q * F.R_OHM) / (F.GAIN * F.V_FS)

    # Local gain analytically, not by differencing the curve.  u(T) is very
    # flat where the steady state is steep, so np.gradient(T, u) divides by
    # nearly zero and returns spikes that are arithmetic, not cryostat.
    #
    #   dQ/dT = Lambda'(T) - Lambda'(T_c) dT_c/dT      the coldplate follows
    #   du/dT = (50/(G V_fs)) sqrt(R/Q) dQ/dT
    # dQ/dT = L'(T) - L'(T_c) dT_c/dT and dT_c/dT = (dT_c/dQ)(dQ/dT), so the
    # coldplate's response folds in as a denominator rather than as a term
    # differenced off an empirical map:
    #
    #     dQ/dT = L'(T) / (1 + L'(T_c) dT_c/dQ)
    dTcdQ = bath.dsteady(np.clip(Q, 0.0, None))
    dQdT = (r["lam"].slope(r["pl"], T)
            / (1.0 + r["lam"].slope(r["pl"], Tc) * dTcdQ))
    dudT = (50.0 / (F.GAIN * F.V_FS)) * np.sqrt(F.R_OHM / Q) * dQdT
    dTdu = 1.0 / dudT

    if r["n_group"]:
        print(f"  CD10 sits {1e3 * r['group_w'][0]:+.2f} mW from this cooldown "
              f"at matched temperature")
    figure(r, T, Tc, Q, u, dTdu, rows, OUT)

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
