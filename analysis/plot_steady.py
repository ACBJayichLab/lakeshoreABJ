"""The settled heater-to-temperature points, and the conductance they imply.

Panel (a) is the honest coverage picture: 37 of the 38 settled holds live in
63-69% / 96-181 K, one lone hold sits at 43% / 18.4 K, and the 8.8 h sweep of
2026-09-03 is the only thing that visits anything in between.
"""
from __future__ import annotations

import csv
import math
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

R_OHM, V_FS = 75.5, 10.0
SRC = "analysis/steady_points.csv"
SWEEP = "data/heater calibration steps/region_20260903-123832_complete_sweep.csv"
OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/steady.png"

power = lambda u: (V_FS * u / 100.0) ** 2 / R_OHM


def f(r, k):
    try:
        return float(r.get(k, ""))
    except (TypeError, ValueError):
        return math.nan


rows = list(csv.DictReader(open(SRC, newline="", encoding="utf-8")))
settled = [r for r in rows if abs(f(r, "drift_K_per_h")) < 0.15 and f(r, "hold_h") > 1.0]
unsettled = [r for r in rows if r not in settled]

groups = {
    "CD10 settled (2026-07/08)":
        ([r for r in settled if r["source"].startswith("fit_cd10")], "#2b6cb0", "o"),
    "recorder settled (2026-08/09)":
        ([r for r in settled if r["source"].startswith("fit_recorder")], "#c05621", "s"),
}

sw = list(csv.DictReader(open(SWEEP, newline="", encoding="utf-8")))
su = np.array([f(r, "ls218.aout1") for r in sw])
sT = np.array([f(r, "Sample") for r in sw])
scp = np.array([f(r, "Coldplate") for r in sw])

fig, ax = plt.subplots(2, 2, figsize=(13.0, 9.5))
fig.suptitle("LTSPM3 steady state — 38 settled holds (>1 h, |drift| < 0.15 K/h) "
             "against the 8.8 h sweep of 2026-09-03", fontsize=13)

# ---- (a) coverage -------------------------------------------------------
a = ax[0, 0]
a.plot(su, sT, "-", color="#cbd5e0", lw=1.2, zorder=1,
       label="sweep trajectory (NOT settled: 2-20 min dwells)")
for name, (g, c, m) in groups.items():
    a.plot([f(r, "u_pct") for r in g], [f(r, "T_K") for r in g],
           m, color=c, ms=6, zorder=3, label=name)
a.plot([f(r, "u_pct") for r in unsettled], [f(r, "T_K") for r in unsettled],
       "x", color="#a0aec0", ms=6, zorder=2, label="hold rejected (drifting)")
a.axvspan(43.5, 62.5, color="#f6e05e", alpha=.18, zorder=0)
a.text(53, 205, "NO settled point\nanywhere in here\n(20 K – 96 K)",
       ha="center", va="center", fontsize=9, color="#975a16")
a.set_xlabel("heater output u  [%]"); a.set_ylabel("sample T  [K]")
a.set_title("(a) what the settled data actually covers")
a.grid(alpha=.3); a.legend(fontsize=7.5, loc="upper left")

# ---- (b) rise vs power, log-log ----------------------------------------
a = ax[0, 1]
for name, (g, c, m) in groups.items():
    a.loglog([f(r, "P_W") for r in g],
             [f(r, "T_K") - f(r, "Coldplate") for r in g], m, color=c, ms=6, label=name)
P = np.array([f(r, "P_W") for r in settled])
dT = np.array([f(r, "T_K") - f(r, "Coldplate") for r in settled])
hi = P > 0.5
m_hi = np.polyfit(np.log(P[hi]), np.log(dT[hi]), 1)[0]
lo = np.array([power(43.0), 0.5269]), np.array([11.57, 88.59])
m_lo = np.log(lo[1][1] / lo[1][0]) / np.log(lo[0][1] / lo[0][0])
a.loglog(P[hi], np.exp(np.polyval(np.polyfit(np.log(P[hi]), np.log(dT[hi]), 1),
                                  np.log(P[hi]))), "--", color="#2b6cb0", lw=1.4,
         label=f"local slope 96–181 K:  m = {m_hi:.2f}")
a.loglog(*lo, "--", color="#38a169", lw=1.4,
         label=f"18 K → 96 K secant:  m = {m_lo:.2f}")
a.set_xlim(0.2, 0.7); a.set_ylim(8, 250)
a.set_xlabel("heater power  P = (u/100 · 10 V)² / 75.5 Ω   [W]")
a.set_ylabel("ΔT = T$_{sample}$ − T$_{coldplate}$  [K]")
a.set_title(f"(b) ΔT ∝ P$^m$ — m runs {m_lo:.1f} → {m_hi:.1f}, no single exponent")
a.grid(alpha=.3, which="both"); a.legend(fontsize=7.5, loc="lower right")

# ---- (c) conductance ----------------------------------------------------
a = ax[1, 0]
for name, (g, c, m) in groups.items():
    T = np.array([f(r, "T_K") for r in g]); Pg = np.array([f(r, "P_W") for r in g])
    cp = np.array([f(r, "Coldplate") for r in g])
    a.semilogy(T, 1e3 * Pg / (T - cp), m, color=c, ms=6,
               label=f"{name.split()[0]}  secant  Λ/ΔT")
    o = np.argsort(T); T, Pg = T[o], Pg[o]
    k = np.diff(T) > 3.0
    a.semilogy(0.5 * (T[:-1] + T[1:])[k], 1e3 * (np.diff(Pg) / np.diff(T))[k],
               m, color=c, ms=6, mfc="none",
               label=f"{name.split()[0]}  differential  dΛ/dT")
a.annotate("differential ≈ 1.3 mW/K, flat over 116–181 K",
           xy=(155, 1.35), xytext=(95, 2.4), fontsize=8.5, color="#4a5568",
           arrowprops=dict(arrowstyle="->", color="#4a5568", lw=.9))
a.annotate("secant falls 21 → 3.7 mW/K", xy=(22, 20), xytext=(55, 24),
           fontsize=8.5, color="#4a5568",
           arrowprops=dict(arrowstyle="->", color="#4a5568", lw=.9))
a.set_ylim(0.8, 40)
a.set_xlabel("sample T  [K]"); a.set_ylabel("conductance  [mW/K]")
a.set_title("(c) secant ≫ differential — the link saturates")
a.grid(alpha=.3, which="both"); a.legend(fontsize=7.5, loc="center left")

# ---- (d) the bath moves -------------------------------------------------
a = ax[1, 1]
a.plot(sT, scp, "-", color="#cbd5e0", lw=1.2, label="sweep, coldplate")
for name, (g, c, m) in groups.items():
    a.plot([f(r, "T_K") for r in g], [f(r, "Coldplate") for r in g], m,
           color=c, ms=6, label=f"{name.split()[0]}  coldplate")
    a.plot([f(r, "T_K") for r in g], [f(r, "Magnet") for r in g], m, color=c, ms=5,
           mfc="none", label=f"{name.split()[0]}  magnet")
a.set_xlabel("sample T  [K]"); a.set_ylabel("T  [K]")
a.set_title("(d) T$_{bath}$ is not constant: 5.6 → 8.5 K over the sweep")
a.grid(alpha=.3); a.legend(fontsize=7.5, loc="upper left")

fig.tight_layout()
fig.savefig(OUT, dpi=130)
print("wrote", OUT, f"| settled {len(settled)} rejected {len(unsettled)} "
      f"| m_hi {m_hi:.3f} m_lo {m_lo:.3f}")
