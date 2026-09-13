"""Which tau measurements can a single pole describe?  REFIT_PLAN.md section 1, row 3.

Section 1's third target is "every measured tau, 40-120 K, within 10 %", and it
reads 26.5 % worst against 2.5 % median.  That gap is two windows out of eleven,
and **neither of them is a model error**.  This draws the evidence, because the
argument is one a picture settles and a table does not.

    (a) tau(T): the fitted curve against every graded relaxation in the band,
        with the two that cannot be believed ringed
    (b) the same thing as a ratio, against the 10 % band the target asks for
    (c) the 68 K window's own trace, beside the two rungs either side of it --
        a 14.5 K COOLING excursion where its neighbours are 7 K steps up
    (d) the two dwells at 118.35 K that disagree with each other by 36 %

**Why each one fails, in terms of the measurement alone.**

``pc-20260905-111947`` runs 83.0 -> 68.4 K, and a relaxation is dated by where
it ENDS.  Across that span the plant's own time constant changes by about half,
so there is no single pole to find: the fit returns something that belongs
near the middle of the excursion and it is compared against the model at the
bottom of it.  Its neighbours in the ladder move 6.5 and 7.0 K and agree with
the model to 2.5 %.

``pc-20260910-144849`` is 1.1 K of motion across 33 hours -- reach 168, meaning
the relaxation was over more than a hundred time constants before the window
ended.  What a pole fits there is the cryostat's drift, which is exactly what
REFIT_PLAN.md section 6.1 established for a hold's LEVEL and is no different
for its tau.  The check is in the data: ``pc-20260908-150234`` sits at the same
temperature to 40 mK, is a clean 2.6 K step with a full settle, measures
524.7 +- 4.5 s, and the model reproduces it to **0.3 %**.

Drop those two and the worst of the remaining nine is **6.9 %**, inside the
target.  No smoothing, no interpolation and no change to the model.

    python analysis/plot_tau.py [out.png]
"""
from __future__ import annotations

import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402
import holdout as H  # noqa: E402
import segments as _seg  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/tau_outliers.png"

BAND = H.TAU_BAND_K
#: The two the argument is about, and why each one cannot be believed.
DOUBTED = {
    "pc-20260905-111947": "83.0 -> 68.4 K: tau changes ~50 % across it",
    "pc-20260910-144849": "1.1 K over 33 h: a drift fit, not a relaxation",
}
#: Its neighbours in the ladder, for contrast in panel (c).
NEIGHBOURS = ("pc-20260905-142405", "pc-20260905-144517")
#: The pair at one temperature, panel (d).
PAIR = ("pc-20260908-150234", "pc-20260910-144849")

INK, INK_2 = "#0b0b0b", "#52514e"
GOOD, DOUBT, MODEL = "#2a78d6", "#eb6834", "#1a202c"
BANDC = "#e6e5e1"


def graded():
    """Every dwell whose relaxation was graded, through the one loader."""
    return [x for x in F.load_rows() if x.get("grade") == "tau"]


def main() -> int:
    r, _, _ = H.production()

    def tau_model(T):
        T = np.atleast_1d(np.asarray(T, float))
        return r["cap"](r["pc"], T) / r["lam"].slope(r["pl"], T)

    rows = {(x.get("id") or "").strip(): x for x in graded()}
    band = [x for x in rows.values() if BAND[0] <= float(x["T_inf"]) <= BAND[1]]
    band.sort(key=lambda x: float(x["T_inf"]))

    T = np.array([float(x["T_inf"]) for x in band])
    meas = np.array([float(x["tau_s"]) for x in band])
    sig = np.array([float(x["sigma_tau_s"]) for x in band])
    fit = tau_model(T)
    bad = np.array([(x.get("id") or "").strip() in DOUBTED for x in band])

    print(f"{'id':<22}{'T K':>8}{'measured s':>12}{'model s':>10}{'miss %':>9}")
    for x, t, m, f in zip(band, T, meas, fit):
        tag = "  <-- doubted" if (x.get("id") or "").strip() in DOUBTED else ""
        print(f"{x['id']:<22}{t:>8.2f}{m:>12.1f}{f:>10.1f}"
              f"{100 * (f / m - 1):>+9.1f}{tag}")
    keep = ~bad
    print(f"\n  worst over all {len(band)}: "
          f"{np.max(np.abs(100 * (fit / meas - 1))):.1f} %")
    print(f"  worst over the {int(keep.sum())} that can be believed: "
          f"{np.max(np.abs(100 * (fit[keep] / meas[keep] - 1))):.1f} %"
          f"   (target 10 %)")

    fig, ax = plt.subplots(2, 2, figsize=(13.5, 9.0))
    fig.suptitle("REFIT_PLAN section 1, row 3: which relaxations a single pole "
                 "can describe", fontsize=12.5)

    # (a) tau(T)
    a = ax[0, 0]
    grid = np.geomspace(40.0, 125.0, 300)
    a.plot(grid, tau_model(grid), "-", color=MODEL, lw=1.8, label="fitted model")
    a.errorbar(T[keep], meas[keep], yerr=sig[keep], fmt="o", ms=6, mfc="none",
               color=GOOD, lw=1.2, label="graded relaxations")
    a.errorbar(T[bad], meas[bad], yerr=sig[bad], fmt="s", ms=9, mfc="none",
               color=DOUBT, lw=1.4, mew=1.8, label="cannot be believed")
    for x, t, m in zip(band, T, meas):
        i = (x.get("id") or "").strip()
        if i in DOUBTED:
            a.annotate(DOUBTED[i], (t, m), textcoords="offset points",
                       xytext=(14, -4), fontsize=8, color=DOUBT, ha="left",
                       va="top")
    a.set_xlabel("sample temperature  [K]")
    a.set_ylabel(r"$\tau$  [s]")
    a.set_title("(a)  every graded relaxation, 40-120 K", loc="left")
    a.grid(alpha=.3); a.legend(fontsize=8, loc="upper left")

    # (b) the ratio, against the target
    b = ax[0, 1]
    b.axhspan(-10, 10, color=BANDC, label="the 10 % target")
    b.axhline(0, color=INK_2, lw=.8)
    b.plot(T[keep], 100 * (fit[keep] / meas[keep] - 1), "o", ms=6, mfc="none",
           color=GOOD, mew=1.4)
    b.plot(T[bad], 100 * (fit[bad] / meas[bad] - 1), "s", ms=9, mfc="none",
           color=DOUBT, mew=1.8)
    b.set_xlabel("sample temperature  [K]")
    b.set_ylabel("model - measured  [%]")
    b.set_title("(b)  the same, as the target reads it", loc="left")
    b.grid(alpha=.3); b.legend(fontsize=8, loc="lower right")

    # (c) the 68 K window and its neighbours, each on its own clock
    c = ax[1, 0]
    for wid, colour in ([("pc-20260905-111947", DOUBT)]
                        + [(n, GOOD) for n in NEIGHBOURS]):
        sl = _seg.load(wid)
        t = (sl.t - sl.t[0]) / 60.0
        # From the trace itself, because amp_K carries no sign.
        a0, a1 = float(sl.T[0]), float(sl.T[-1])
        c.plot(t, sl.T, "-", color=colour, lw=1.8,
               label=f"{wid}   {a0:.1f} -> {a1:.1f} K  ({a1 - a0:+.1f})")
    c.set_xlabel("minutes into the window")
    c.set_ylabel("sample  [K]")
    c.set_title("(c)  a 14.5 K cooling excursion, beside two 7 K steps up",
                loc="left")
    c.grid(alpha=.3); c.legend(fontsize=8)

    # (d) two dwells at one temperature
    d = ax[1, 1]
    for wid, colour in ((PAIR[0], GOOD), (PAIR[1], DOUBT)):
        x = rows[wid]
        s = _seg.load(wid)
        t = (s.t - s.t[0]) / 3600.0
        d.plot(t, s.T, "-", color=colour, lw=1.6,
               label=f"{wid}  {float(x['tau_s']):.0f} s "
                     f"+- {float(x['sigma_tau_s']):.0f}  "
                     f"({float(x['span_s']) / 3600:.1f} h, "
                     f"{float(x['amp_K']):+.1f} K)")
    d.set_xlabel("hours into the window")
    d.set_ylabel("sample  [K]")
    d.set_title("(d)  118.35 K twice: the model matches the step to 0.3 %",
                loc="left")
    d.grid(alpha=.3); d.legend(fontsize=8, loc="lower right")

    fig.tight_layout()
    fig.savefig(OUT, dpi=140)
    print("\nwrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
