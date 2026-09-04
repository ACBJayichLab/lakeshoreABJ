"""The lumped thermal model, drawn, with every ODE term on the arrow it belongs to.

The topology is the point.  Everything the sample touches sinks at the
coldplate -- structure, wiring and radiation alike -- so there is no parasitic
input from a warm stage and no second boundary temperature.  Three parallel
paths between the same two nodes simply add, and radiation to a common cold
end has the same potential-difference form as a conduction link, so the whole
sample balance collapses to one monotone Lambda(T).
"""
from __future__ import annotations

import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/model_diagram.png"

FILL = {"heater": "#fbe0dd", "sample": "#fde8d7", "wire": "#ece9fb",
        "copper": "#fdf3d6", "rad": "#daf0ea", "cold": "#dcecfa",
        "base": "#c3daf2"}
EDGE = {"heater": "#c53030", "sample": "#c05621", "wire": "#6b46c1",
        "copper": "#b7791f", "rad": "#2c7a7b", "cold": "#2b6cb0",
        "base": "#2b6cb0"}
TEXT = {"heater": "#742a2a", "sample": "#7b341e", "wire": "#44337a",
        "copper": "#744210", "rad": "#234e52", "cold": "#1a365d",
        "base": "#1a365d"}

fig, ax = plt.subplots(figsize=(12.0, 9.8))
ax.set_xlim(0, 100)
ax.set_ylim(-28, 100)
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
        ax.text(cx, y0 + 0.64 * (y1 - y0), title, ha="center", va="center",
                fontsize=12, color=TEXT[kind], zorder=3)
        ax.text(cx, y0 + 0.24 * (y1 - y0), sub, ha="center", va="center",
                fontsize=9.5, color=TEXT[kind], alpha=.88, zorder=3)


def arrow(x0, y0, x1, y1, color="#4a5568", lw=1.4):
    ax.add_patch(FancyArrowPatch(
        (x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=14,
        linewidth=lw, color=color, zorder=4, shrinkA=0, shrinkB=0))


box(34, 66, 88, 98, "heater", "Heater  75.5 Ω", "voltage driven")
arrow(50, 88, 50, 80.6, color="#c53030")
ax.text(52, 85.6, r"$Q(u) = \left(G \cdot 10\,\mathrm{V}\cdot u/100\right)^2 / R$",
        fontsize=11.5, ha="left", va="center", color="#742a2a")
ax.text(52, 82.8, r"$G \approx 1.11$ voltage gain  —  exact, nothing to fit",
        fontsize=9.5, ha="left", va="center", color="#742a2a", alpha=.85)

box(8, 92, 66, 80, "sample", "Sample stage    $T_s$ ,  $C_s(T_s)$",
    "copper + sapphire + diamond")

box(8, 31, 40, 56, "wire", "Wiring bundle",
    "$\Lambda_w(T_s) - \Lambda_w(T_c)$")
box(36, 64, 40, 56, "copper", "Intermediate copper",
    "$T_m$ ,  $C_m(T_m)$   —   tier 2 only", dashed=True)
box(69, 92, 40, 56, "rad", "Radiation",
    "$\\sigma_r (T_s^4 - T_c^4)$\nenclosure ≈ 4 K, some 40 K")

for x, c in ((19.5, "#6b46c1"), (50, "#b7791f"), (80.5, "#2c7a7b")):
    arrow(x, 66, x, 56.6, color=c)
    arrow(x, 40, x, 30.6, color=c)
ax.text(52, 61.0, r"$\Lambda_1(T_s) - \Lambda_1(T_m)$", fontsize=11,
        ha="left", va="center", color="#744210")
ax.text(52, 35.4, r"$\Lambda_2(T_m) - \Lambda_2(T_c)$", fontsize=11,
        ha="left", va="center", color="#744210")

box(8, 92, 16, 30, "cold", "Coldplate    $T_c$",
    "measured 5.63 → 8.47 K  —  every path sinks here, so it is the only boundary")
arrow(50, 16, 50, 10.6, color="#2b6cb0")
box(8, 92, 2, 10, "base", "cryocooler second stage  ≈ 4 K")

ax.text(50, -5.0,
        "three parallel paths, one cold end, so they add:      "
        r"$\Lambda(T) = \Lambda_{struct}(T) + \Lambda_w(T) + \sigma_r T^4$",
        fontsize=12, ha="center", va="center", color="#2d3748")
ax.text(50, -12.5,
        "tier 1   " + r"$C(T)\,\dot T = Q(u) - [\,\Lambda(T) - \Lambda(T_c(t))\,]$"
        "        — structurally exact; the only approximation is lumping the copper node",
        fontsize=11.5, ha="center", va="center", color="#553c9a")
ax.text(50, -21.5,
        r"$\Lambda(T) \equiv \int^{T} k(T')\,(A/L)\,dT'$.  At steady state"
        r" $\Lambda(T_s) = Q(u) + \Lambda(T_c)$, so every settled hold measures $\Lambda$"
        " directly, up to one additive constant."
        "\nThere is no warm anchor anywhere: no parasitic source term, no second"
        " boundary temperature, and nothing that heats the sample but the heater.",
        fontsize=10, ha="center", va="center", color="#4a5568")

ax.set_title("LTSPM3 lumped thermal model — nodes, links, and where each ODE term lives",
             fontsize=13.5, pad=8, color="#1a202c")
fig.tight_layout()
fig.savefig(OUT, dpi=140, facecolor="white")
print("wrote", OUT)
