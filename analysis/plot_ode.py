"""Plot the fitted ODE: the trajectory, and the two curves that produced it.

Three complexity levels are overlaid on every curve panel.  Where they agree,
the data has settled the answer; where they fan out, the answer is coming from
the parameterisation rather than from the cryostat, and that is the honest way
to read a phenomenological fit.
"""
from __future__ import annotations

import csv
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/ode_fit.png"
LEVELS = ((7, 4), (8, 4), (9, 4))
COLORS = ("#2b6cb0", "#c05621", "#2c7a7b")

#: The two independent step-response measurements in the record, for scale.
MEASURED_TAU = ((137.3, 620.0, "620 s @ 137 K\nCD10 step"),
                (137.0, 709.0, "709 s @ 137 K\n2026-08-24"))

mix_c = F.mix_c


def _g(r, k):
    try:
        return float(r[k])
    except (TypeError, ValueError):
        return math.nan


def anchor_rows(t_max=None):
    """Settled dwells, from the relaxation fits in analysis/steps.py."""
    rows = list(csv.DictReader(open(F.ANCHORS, newline="", encoding="utf-8")))
    return [r for r in rows if r.get("grade")
            and (t_max is None or _g(r, "T_inf") <= t_max)]


def trajectory_figure(fits, data, grid, best):
    t, T, Tc, u = data
    th = t / 3600.0
    srows = anchor_rows(t_max=float(data[1].max()))

    fig = plt.figure(figsize=(14.0, 10.5))
    gs = fig.add_gridspec(3, 3, height_ratios=[1.35, 0.75, 1.25], hspace=.45,
                          wspace=.28)
    n_tau = len(F.load_taus(t_max=float(T.max()))[0])
    fig.suptitle("LTSPM3 tier-1 ODE fitted to the 8.8 h sweep of 2026-09-03, "
                 f"anchored on {len(srows)} settled dwells "
                 f"and {n_tau} measured time constants", fontsize=13.5)

    a = fig.add_subplot(gs[0, :])
    a.plot(th, T, color="#1a202c", lw=2.0, label="sample, measured")
    for r in fits:
        a.plot(th, r["model"], color=r["color"], lw=1.1, alpha=.95,
               label=f"model, Λ {r['n_lam']} knots — rms {r['rms_k']:.2f} K")
    a.plot(th, Tc, color="#3182ce", lw=1.0, alpha=.7,
           label="coldplate (driven input, not fitted)")
    a.set_ylabel("T  [K]")
    a.set_title("(a) trajectory — u(t) and T$_c$(t) are inputs; Λ(T) and C(T) are the fit")
    a.grid(alpha=.3)
    a.legend(fontsize=8, ncol=2, loc="lower left")
    a2 = a.twinx()
    a2.plot(th, u, color="#cbd5e0", lw=0.9, zorder=0)
    a2.set_ylabel("heater u  [%]", color="#a0aec0")
    a2.set_ylim(-2, 210)
    a2.tick_params(axis="y", colors="#a0aec0")

    a = fig.add_subplot(gs[1, :])
    for r in fits:
        a.plot(th, r["model"] - T, color=r["color"], lw=1.0, alpha=.9,
               label=f"Λ {r['n_lam']} knots")
    a.axhline(0, color="#718096", lw=.7)
    a.set_xlabel("time  [h]"); a.set_ylabel("model − data  [K]")
    a.axvspan(8.20, 8.35, color="#f6e05e", alpha=.25, lw=0)
    a.text(8.13, 1.9, "recovery ramp\n4 K/s", fontsize=8, ha="right",
           color="#975a16")
    a.set_ylim(-2.5, 2.5)
    a.set_title("(b) residual — outside the shaded ramp the whole record is "
                "within 2.1 K")
    a.grid(alpha=.3); a.legend(fontsize=8, ncol=3)

    a = fig.add_subplot(gs[2, 0])
    for r in fits:
        a.loglog(grid, r["lam"](r["pl"], grid), color=r["color"], lw=1.5,
                 label=f"Λ {r['n_lam']} knots")
    # Lambda(T_inf) = Q + Lambda(T_c), and T_c is NOT the same for every dwell:
    # the coldplate runs 5.6 K with the heater off and 8.5 K at 180 K.  A single
    # offset puts the cold dwells a factor of five off the curve and makes a fit
    # that is right look wrong.
    aT = np.array([_g(r, "T_inf") for r in srows])
    aQ = np.array([_g(r, "P_W") for r in srows])
    aTc = np.array([_g(r, "Coldplate") for r in srows])
    a.loglog(aT, aQ + best["lam"](best["pl"], aTc), "o", ms=4, mfc="none",
             color="#1a202c", label=f"{len(srows)} settled dwells,  Q + Λ(T$_c$)")
    a.set_xlabel("T  [K]"); a.set_ylabel("Λ  [W]")
    a.set_title("(c) conductance integral Λ(T)")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=7.5, loc="upper left")

    a = fig.add_subplot(gs[2, 1])
    for r in fits:
        a.loglog(grid, 1e3 * r["lam"].slope(r["pl"], grid), color=r["color"], lw=1.5,
                 label=f"Λ {r['n_lam']} knots")
    # finite differences only inside ONE cooldown -- the two disagree by 3.2 K
    # at matched power, and differencing across that boundary manufactures
    # conductances that belong to neither
    same = [r for r in srows if not r["source"].startswith("fit_cd10")]
    Ts = np.array([_g(r, "T_inf") for r in same])
    # difference Lambda, not Q: the coldplate moves 2.9 K across these dwells,
    # so dQ/dT alone attributes that motion to the link
    Ps = (np.array([_g(r, "P_W") for r in same])
          + best["lam"](best["pl"], np.array([_g(r, "Coldplate") for r in same])))
    o = np.argsort(Ts)
    Ts, Ps = Ts[o], Ps[o]
    k = np.diff(Ts) > 3.0
    a.loglog(0.5 * (Ts[:-1] + Ts[1:])[k], 1e3 * (np.diff(Ps) / np.diff(Ts))[k],
             "o", ms=4, mfc="none", color="#1a202c",
             label="settled dwells, finite dΛ/dT")
    a.set_xlabel("T  [K]"); a.set_ylabel("dΛ/dT = k(T)·A/L  [mW/K]")
    a.set_title("(d) differential conductance")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=7.5, loc="lower left")

    a = fig.add_subplot(gs[2, 2])
    for r in fits:
        a.loglog(grid, r["cap"](r["pc"], grid), color=r["color"], lw=1.5,
                 label=f"C, with Λ {r['n_lam']} knots")
    mass = best["mass_g"]
    ref = mass * mix_c(grid)
    a.fill_between(grid, ref / F.CAP_SHAPE_FACTOR, ref * F.CAP_SHAPE_FACTOR,
                   color="#805ad5", alpha=.12, lw=0,
                   label=f"shape prior, ×{F.CAP_SHAPE_FACTOR:.0f} either way")
    a.loglog(grid, ref, "--", color="#805ad5", lw=1.5,
             label=f"Debye mix, {mass:.1f} g\n50% Al₂O₃ / 35% Cu / 15% C")
    a.set_xlabel("T  [K]"); a.set_ylabel("C  [J/K]")
    a.set_title("(e) heat capacity, against a Debye reference")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=7.5, loc="upper left")

    fig.savefig(OUT, dpi=130, bbox_inches="tight")
    print("wrote", OUT)


def diagnostics_figure(fits, data, grid):
    T = data[1]  # noqa: F841
    fig, ax = plt.subplots(1, 3, figsize=(16.0, 4.8))

    a = ax[0]
    for r in fits:
        tau = r["cap"](r["pc"], grid) / r["lam"].slope(r["pl"], grid)
        a.loglog(grid, tau / 60.0, color=r["color"], lw=1.8,
                 label=f"Λ {r['n_lam']} knots")
    tT, tV = F.load_taus(t_max=float(data[1].max()))
    a.plot(tT, tV / 60.0, "o", ms=6, mfc="none", mew=1.4, color="#1a202c",
           zorder=5, label=f"{len(tT)} measured relaxations")
    for i, (Tm, taum, lab) in enumerate(MEASURED_TAU):
        a.plot(Tm, taum / 60.0, "*", ms=15, color="#805ad5", zorder=6)
        a.annotate(lab, (Tm, taum / 60.0), textcoords="offset points",
                   xytext=(-30, -34 - 46 * i), fontsize=8, ha="right",
                   color="#553c9a",
                   arrowprops=dict(arrowstyle="-", color="#805ad5", lw=.8))
    a.set_xlabel("T  [K]"); a.set_ylabel("τ = C / (dΛ/dT)  [min]")
    a.set_title("(f) time constant — curves fitted, circles measured")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="upper left")

    a = ax[1]
    for r in fits:
        a.plot(T, r["model"] - T, ".", ms=1.5, alpha=.35, color=r["color"],
               label=f"Λ {r['n_lam']} knots — rms {r['rms_k']:.2f} K")
    a.axhline(0, color="#718096", lw=.7)
    a.set_xlabel("sample T  [K]"); a.set_ylabel("model − data  [K]")
    a.set_ylim(-8, 8)
    a.set_title("(g) where the model misses")
    a.grid(alpha=.3)
    leg = a.legend(fontsize=8, markerscale=6)
    for h in leg.legend_handles:
        h.set_alpha(1)

    a = ax[2]
    if os.path.exists(F.LADDER_CSV):
        rows = list(csv.DictReader(open(F.LADDER_CSV, newline="", encoding="utf-8")))
        lam_rows = [r for r in rows if r["axis"] == "Lambda"]
        cap_rows = [r for r in rows if r["axis"] == "C"]
        a.semilogy([int(r["n_lam"]) for r in lam_rows],
                   [float(r["rms_k"]) for r in lam_rows], "o-", color="#c05621",
                   label="knots in Λ  (C fixed at 4)")
        a.semilogy([int(r["n_cap"]) for r in cap_rows],
                   [float(r["rms_k"]) for r in cap_rows], "s-", color="#2b6cb0",
                   label="knots in C  (Λ fixed at 9)")
        a.semilogy([int(r["n_lam"]) for r in lam_rows],
                   [float(r["hold_max_k"]) for r in lam_rows], "o--",
                   color="#c53030", mfc="none",
                   label="worst error on the opening hold")
        a.axhline(0.5, color="#c53030", lw=.8, ls=":")
        bad = [int(r["n_lam"]) for r in lam_rows
               if float(r["hold_max_k"]) > 0.5]
        if bad:
            a.axvspan(min(bad) - .4, max(bad) + .4, color="#fed7d7", alpha=.45,
                      lw=0, zorder=0)
            a.text((min(bad) + max(bad)) / 2, 0.55, "rejected: drifts during\n"
                   "a hold that did not move", fontsize=7.5, ha="center",
                   va="bottom", color="#742a2a")
        a.set_xlabel("knots in the curve being freed")
        a.set_ylabel("residual  [K]")
        a.set_title("(h) which curve the data constrains, and what it rejects")
        a.grid(alpha=.3, which="both"); a.legend(fontsize=7.5, loc="lower left")
    else:
        a.text(.5, .5, "run analysis/fit_ode.py first", ha="center",
               va="center", transform=a.transAxes, color="#718096")
        a.axis("off")

    fig.tight_layout()
    out2 = OUT.replace(".png", "_curves.png")
    fig.savefig(out2, dpi=130)
    print("wrote", out2)


def main():
    data = F.load_sweep()
    hi = float(data[1].max())
    anchors, taus = F.load_anchors(t_max=hi), F.load_taus(t_max=hi)
    print(f"  {len(anchors[0])} settled dwells, {len(taus[0])} measured taus")
    fits = []
    for (nl, nc), c in zip(LEVELS, COLORS):
        r = F.fit(nl, nc, data, anchors, taus)
        r["color"] = c
        fits.append(r)
        print(f"  Lambda {nl} knots, C {nc}: {r['npar']} par  rms {r['rms_k']:.3f} K  "
              f"max {r['max_k']:.2f} K  anchors {r['anchor_k']:.2f} K")
    best = min(fits, key=lambda r: r["rms_k"])
    grid = np.geomspace(0.95 * data[1].min(), 1.05 * data[1].max(), 400)
    trajectory_figure(fits, data, grid, best)
    diagnostics_figure(fits, data, grid)

    print(f"\nbest = Lambda {best['n_lam']} knots, C {best['n_cap']}")
    print(f"  implied mass {best['mass_g']:.2f} g of the Debye mix")
    print(f"  tau(137 K) = {best['tau_137_s']:.0f} s  (measured 620 s and 709 s)")
    print(f"  tau residual {best['tau_resid']:.3f} in log = "
          f"x{math.exp(best['tau_resid']):.2f} against {len(taus[0])} measurements")
    for Tq in (10.0, 30.0, 100.0, 180.0):
        q = np.array([Tq])
        gq = float(best["lam"].slope(best["pl"], q)[0])
        cq = float(best["cap"](best["pc"], q)[0])
        print(f"  {Tq:5.0f} K:  dLambda/dT {1e3 * gq:7.2f} mW/K"
              f"   C {cq:8.4f} J/K   tau {cq / gq / 60:8.2f} min")


if __name__ == "__main__":
    main()
