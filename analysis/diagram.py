"""The lumped thermal model, drawn, with every ODE term on the arrow it belongs to."""
from __future__ import annotations

import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/model_diagram.png"

FILL = {"heater": "#fbe0dd", "env": "#eeedea", "sample": "#fde8d7",
        "copper": "#fdf3d6", "cold": "#dcecfa", "base": "#c3daf2"}
EDGE = {"heater": "#c53030", "env": "#8a8a80", "sample": "#c05621",
        "copper": "#b7791f", "cold": "#2b6cb0", "base": "#2b6cb0"}
TEXT = {"heater": "#742a2a", "env": "#3d3d38", "sample": "#7b341e",
        "copper": "#744210", "cold": "#1a365d", "base": "#1a365d"}

fig, ax = plt.subplots(figsize=(11.5, 9.2))
ax.set_xlim(0, 100)
ax.set_ylim(-24, 100)
ax.axis("off")


def box(x0, x1, y0, y1, kind, title, sub=None, dashed=False):
    ax.add_patch(FancyBboxPatch(
        (x0, y0), x1 - x0, y1 - y0,
        boxstyle="round,pad=0,rounding_size=1.2",
        facecolor=FILL[kind], edgecolor=EDGE[kind], linewidth=1.1,
        linestyle="--" if dashed else "-", zorder=2))
    cx = 0.5 * (x0 + x1)
    if sub is None:
        ax.text(cx, 0.5 * (y0 + y1), title, ha="center", va="center",
                fontsize=12, color=TEXT[kind], zorder=3)
    else:
        ax.text(cx, y0 + 0.63 * (y1 - y0), title, ha="center", va="center",
                fontsize=12.5, color=TEXT[kind], zorder=3)
        ax.text(cx, y0 + 0.27 * (y1 - y0), sub, ha="center", va="center",
                fontsize=10, color=TEXT[kind], alpha=.85, zorder=3)


def arrow(x0, y0, x1, y1, color="#4a5568", ls="-", rad=0.0, lw=1.4):
    ax.add_patch(FancyArrowPatch(
        (x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=15,
        linewidth=lw, color=color, linestyle=ls, zorder=4,
        connectionstyle=f"arc3,rad={rad}", shrinkA=0, shrinkB=0))


box(20, 44, 84, 95, "heater", "Heater  75.5 Ω", "voltage driven")
box(56, 92, 84, 95, "env", "Environment", "shield 40 K  ·  room 289 K")
box(14, 92, 62, 76, "sample", "Sample stage    $T_s$ ,  $C_s(T_s)$",
    "copper + sapphire + diamond")
box(14, 92, 40, 54, "copper", "Intermediate copper    $T_m$ ,  $C_m(T_m)$",
    "tier 2 only  —  the slow, hours-long pole", dashed=True)
box(14, 92, 18, 32, "cold", "Coldplate    $T_c$",
    "measured 5.63 → 8.47 K  —  not a fixed bath")
box(14, 92, 4, 12, "base", "cryocooler second stage  ≈ 4 K")

arrow(32, 84, 32, 76.6)
ax.text(34, 80.2, r"$Q(u) = \left(G\cdot 10\,\mathrm{V}\cdot u/100\right)^2 / R$", fontsize=11,
        ha="left", va="center", color="#742a2a")
ax.text(34, 77.6, r"$G \approx 1.11$ (voltage),  $R = 75.5\ \Omega$  —  exact, no fit",
        fontsize=9.5, ha="left", va="center", color="#742a2a", alpha=.8)

arrow(74, 84, 74, 76.6)
ax.text(72, 80.2, r"$Q_{rad} + Q_{wire}$", fontsize=11, ha="right",
        va="center", color="#3d3d38")

arrow(53, 62, 53, 54.6)
ax.text(55, 58.3, r"$\Lambda_1(T_s) - \Lambda_1(T_m)$", fontsize=11.5,
        ha="left", va="center", color="#7b341e")

arrow(53, 40, 53, 32.6)
ax.text(55, 36.3, r"$\Lambda_2(T_m) - \Lambda_2(T_c)$", fontsize=11.5,
        ha="left", va="center", color="#744210")

arrow(53, 18, 53, 12.6, color="#2b6cb0")

# tier-1 bypass: delete the middle node and link the sample straight to the cold end
ax.plot([14, 7, 7, 14], [67, 67, 25, 25], color="#805ad5", lw=1.3,
        ls=(0, (5, 3)), zorder=1, solid_capstyle="butt")
arrow(9.5, 25, 14, 25, color="#805ad5", ls="--", lw=1.3)
ax.text(4.4, 46, "tier 1:  $\Lambda(T_s) - \Lambda(T_c)$", fontsize=11,
        rotation=90, ha="center", va="center", color="#553c9a")

ax.text(50, -2.5,
        "tier 2   " +
        r"$C_s(T_s)\,\dot T_s = Q(u) + Q_{par}(T_s) - [\Lambda_1(T_s) - \Lambda_1(T_m)]$"
        "        "
        r"$C_m(T_m)\,\dot T_m = [\Lambda_1(T_s) - \Lambda_1(T_m)] - [\Lambda_2(T_m) - \Lambda_2(T_c(t))]$",
        fontsize=11.5, ha="center", va="center", color="#2d3748")
ax.text(50, -9.5,
        "tier 1   " + r"$C(T)\,\dot T = Q(u) - [\Lambda(T) - \Lambda(T_c(t))]$",
        fontsize=11.5, ha="center", va="center", color="#553c9a")
ax.text(50, -17.5,
        r"$\Lambda(T) \equiv \int^{T} k(T')\,(A/L)\,dT'$   —   the conductance integral."
        "  Heat down a link is $\Lambda(T_{hot}) - \Lambda(T_{cold})$, exactly, for any $k(T)$."
        "\nAt steady state $\Lambda(T_s) = Q(u) + \Lambda(T_c)$, so every settled hold is one point"
        " on $\Lambda$ with no heat capacity in it.",
        fontsize=10, ha="center", va="center", color="#4a5568")

ax.set_title("LTSPM3 lumped thermal model — nodes, links, and where each ODE term lives",
             fontsize=13.5, pad=8, color="#1a202c")
fig.tight_layout()
fig.savefig(OUT, dpi=140, facecolor="white")
print("wrote", OUT)
