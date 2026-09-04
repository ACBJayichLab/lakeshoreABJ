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
LEVELS = ((4, 3), (6, 3), (8, 3))
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


def anchor_rows():
    rows = list(csv.DictReader(open(F.ANCHORS, newline="", encoding="utf-8")))
    return [r for r in rows
            if abs(_g(r, "drift_K_per_h")) < 0.15 and _g(r, "hold_h") > 1.0]


def trajectory_figure(fits, data, grid, best):
    t, T, Tc, u = data
    th = t / 3600.0
    srows = anchor_rows()

    fig = plt.figure(figsize=(14.0, 10.5))
    gs = fig.add_gridspec(3, 3, height_ratios=[1.35, 0.75, 1.25], hspace=.45,
                          wspace=.28)
    fig.suptitle("LTSPM3 tier-1 ODE fitted to the 8.8 h sweep of 2026-09-03, "
                 "anchored on 38 settled holds", fontsize=13.5)

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
    a.set_ylim(-6, 6)
    a.set_title("(b) residual — clipped to ±6 K; the excursions past it are "
                "single samples in the fastest slews")
    a.grid(alpha=.3); a.legend(fontsize=8, ncol=3)

    a = fig.add_subplot(gs[2, 0])
    for r in fits:
        a.loglog(grid, r["lam"](r["pl"], grid), color=r["color"], lw=1.5,
                 label=f"Λ {r['n_lam']} knots")
    off = float(best["lam"](best["pl"], np.array([8.3]))[0])
    a.loglog([_g(r, "T_K") for r in srows], [_g(r, "P_W") + off for r in srows],
             "o", ms=5, mfc="none", color="#1a202c",
             label="settled holds,  Q + Λ(8.3 K)")
    a.set_xlabel("T  [K]"); a.set_ylabel("Λ  [W]")
    a.set_title("(c) conductance integral Λ(T)")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=7.5, loc="upper left")

    a = fig.add_subplot(gs[2, 1])
    for r in fits:
        a.loglog(grid, 1e3 * r["lam"].slope(r["pl"], grid), color=r["color"], lw=1.5,
                 label=f"Λ {r['n_lam']} knots")
    Ts = np.array([_g(r, "T_K") for r in srows])
    Ps = np.array([_g(r, "P_W") for r in srows])
    o = np.argsort(Ts)
    Ts, Ps = Ts[o], Ps[o]
    k = np.diff(Ts) > 5.0
    a.loglog(0.5 * (Ts[:-1] + Ts[1:])[k], 1e3 * (np.diff(Ps) / np.diff(Ts))[k],
             "o", ms=5, mfc="none", color="#1a202c", label="settled, finite dΛ/dT")
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
    T = data[1]
    fig, ax = plt.subplots(1, 3, figsize=(16.0, 4.8))

    a = ax[0]
    for r in fits:
        tau = r["cap"](r["pc"], grid) / r["lam"].slope(r["pl"], grid)
        a.loglog(grid, tau / 60.0, color=r["color"], lw=1.8,
                 label=f"Λ {r['n_lam']} knots")
    for i, (Tm, taum, lab) in enumerate(MEASURED_TAU):
        a.plot(Tm, taum / 60.0, "*", ms=15, color="#1a202c", zorder=5)
        a.annotate(lab, (Tm, taum / 60.0), textcoords="offset points",
                   xytext=(-30, -30 - 46 * i), fontsize=8, ha="right",
                   color="#1a202c",
                   arrowprops=dict(arrowstyle="-", color="#718096", lw=.8))
    a.set_xlabel("T  [K]"); a.set_ylabel("τ = C / (dΛ/dT)  [min]")
    a.set_title("(f) time constant vs temperature — 7 decades across the sweep")
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
                   label="knots in Λ  (C fixed at 3)")
        a.semilogy([int(r["n_cap"]) for r in cap_rows],
                   [float(r["rms_k"]) for r in cap_rows], "s-", color="#2b6cb0",
                   label="knots in C  (Λ fixed at 6)")
        a.set_xlabel("knots in the curve being freed")
        a.set_ylabel("rms residual over the sweep  [K]")
        a.set_title("(h) which curve the data actually constrains")
        a.grid(alpha=.3, which="both"); a.legend(fontsize=8)
    else:
        a.text(.5, .5, "run analysis/fit_ode.py first", ha="center",
               va="center", transform=a.transAxes, color="#718096")
        a.axis("off")

    fig.tight_layout()
    out2 = OUT.replace(".png", "_curves.png")
    fig.savefig(out2, dpi=130)
    print("wrote", out2)


def main():
    data, anchors = F.load_sweep(), F.load_anchors()
    fits = []
    for (nl, nc), c in zip(LEVELS, COLORS):
        r = F.fit(nl, nc, data, anchors)
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
    for Tq in (10.0, 30.0, 100.0, 180.0):
        q = np.array([Tq])
        gq = float(best["lam"].slope(best["pl"], q)[0])
        cq = float(best["cap"](best["pc"], q)[0])
        print(f"  {Tq:5.0f} K:  dLambda/dT {1e3 * gq:7.2f} mW/K"
              f"   C {cq:8.4f} J/K   tau {cq / gq / 60:8.2f} min")


if __name__ == "__main__":
    main()
