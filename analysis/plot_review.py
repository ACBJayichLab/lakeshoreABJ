"""The four figures behind REVIEW-2026-09-10.md, for eyeballing rather than reading.

Each one is a decision that was made from numbers, drawn so the numbers can be
checked by looking:

``review-1-plantclock.png``
    Option 4.  Did the plant clock separate the windows it was asked about, or
    is the bar sitting in the middle of the data?
``review-2-modelfree.png``
    Step 6.  Does Lambda and C read straight off the anchors -- no ODE, no
    least_squares -- agree with what the fit converges to?
``review-3-basins.png``
    Step 6b.  The two seeds converge to different optima.  Are they different
    CURVES, or only different cost numbers?
``review-4-drift.png``
    Step 5.  Is the campaign drift a power?  K/day should scatter across output
    bands and mW/day should not.

    python analysis/plot_review.py [outdir]

Outputs are gitignored, like every other PNG here.  Nothing in this module is
imported by anything; it reads the same loaders the fits do.
"""
from __future__ import annotations

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import PchipInterpolator

sys.path.insert(0, "analysis")
import drift as D  # noqa: E402
import fit_ode as F  # noqa: E402
import steps as S  # noqa: E402

KEEP, DROP, REF = "#1b7837", "#b2182b", "#4d4d4d"
#: The production preset, as plot_gain.py defines it.
N_LAM, N_CAP, N_DRIFT = 20, 4, 3


def _judged():
    """Every dwell the plant clock was asked about, with its verdict."""
    rows = S.archive_dwells()
    out = []
    for r in rows:
        if not S.pole_unbelievable(r):
            continue
        tp = r.get("tau_plant_s")
        if not (tp and np.isfinite(tp)):
            continue
        out.append(r)
    return out


def fig_plantclock(outdir):
    """Option 4: is the bar in a gap, or in the middle of the data?"""
    rows = _judged()
    T = np.array([r["T_inf"] for r in rows])
    reach = np.array([r["reach_plant"] for r in rows])
    margin = np.array([r["plant_margin"] for r in rows])
    # Colour is the PLANT CLOCK's verdict, not the final grade.  A window that
    # clears the clock can still be refused by the settle or end-rate tests --
    # colouring by `grade` mixes two decisions and makes the gap below look
    # inverted, which is how this plot was wrong the first time.
    passes = margin >= 1.0

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 5.0))

    a1.axhline(S.MIN_REACH, color=REF, lw=1.2, ls="--")
    a1.text(4.4, S.MIN_REACH * 1.15, f"MIN_REACH = {S.MIN_REACH:g}",
            color=REF, fontsize=9)
    for m, c, lab in ((passes, KEEP, f"clears the plant clock ({passes.sum()})"),
                      (~passes, DROP, f"refused by it ({(~passes).sum()})")):
        a1.loglog(T[m], reach[m], "o", ms=5, mfc="none", mew=1.3, color=c,
                  label=lab)
    a1.set_xlabel("T_inf  [K]")
    a1.set_ylabel("span / tau_plant   (reach on the PLANT's clock)")
    a1.set_title("every window whose own pole says nothing")
    a1.legend(loc="upper left", fontsize=9)
    a1.grid(alpha=.25, which="both")

    # The margin, sorted, so the gap either side of 1.0 is the whole story.
    o = np.argsort(margin)
    for m, c in ((passes[o], KEEP), (~passes[o], DROP)):
        idx = np.flatnonzero(m)
        a2.semilogy(idx, margin[o][idx], "o", ms=4.5, color=c, alpha=.85)
    a2.axhline(1.0, color=REF, lw=1.2, ls="--")
    # The gap is a property of ALL the margins, not of the verdicts: the
    # largest below the bar and the smallest above it.
    lo = margin[margin < 1.0].max() if (margin < 1.0).any() else np.nan
    hi = margin[margin >= 1.0].min() if (margin >= 1.0).any() else np.nan
    if np.isfinite(lo) and np.isfinite(hi):
        a2.axhspan(lo, hi, color="0.85", zorder=0)
        a2.text(1, (lo * hi) ** .5,
                f"  no window between {lo:.2f} and {hi:.2f}",
                fontsize=9.5, va="center", color=REF)
    a2.set_xlabel("window, sorted by margin")
    a2.set_ylabel("margin  =  how wrong tau(T) would have to be to flip it")
    a2.set_title("the bar sits in a gap, not in the data")
    a2.grid(alpha=.25, which="both")

    fig.suptitle("Option 4 -- the guard runs on a plant clock analysis/ MEASURES "
                 "(REVIEW section 1)", fontsize=11)
    fig.tight_layout()
    return _save(fig, outdir, "review-1-plantclock.png")


def _production_fit(seed_measured=True):
    rec, anchors, taus = F.production_inputs()
    r = F.fit(N_LAM, N_CAP, rec, anchors, taus, n_drift=N_DRIFT, groups=True,
              seed_measured=seed_measured)
    return r, anchors, taus


def fig_modelfree(outdir):
    """Step 6: the anchors' own reading against what the fit converges to."""
    r, anchors, taus = _production_fit(True)
    bT, bL = F.measured_lambda(anchors)
    lam_of = PchipInterpolator(bT, bL, extrapolate=True)
    cT, cC = F.measured_capacity(taus, lam_of)
    cap_at = F._capacity_seed(cT, cC)
    grid = np.geomspace(bT[0], bT[-1], 400)

    # The fit's Lambda carries an arbitrary constant (trap T1), so it is only
    # comparable to the anchors' gauge after matching one point.  Matched at
    # the coldest bin, which is where the anchors' own gauge is defined.
    lam_fit = r["lam"](r["pl"], grid)
    lam_fit = lam_fit - float(r["lam"](r["pl"], np.array([bT[0]]))[0]) + bL[0]

    fig, ax = plt.subplots(2, 2, figsize=(12.5, 9.0))
    (a1, a2), (a3, a4) = ax

    a1.loglog(bT, bL, "o", ms=6, mfc="none", mew=1.4, color=DROP,
              label="anchors, Lambda(T_c) = 0")
    a1.loglog(grid, lam_fit, "-", lw=1.6, color=KEEP,
              label="fit, level matched at the cold end")
    a1.set_ylabel("Lambda  [W]")
    a1.set_title("Lambda -- direct inversion of Lambda(T_s) - Lambda(T_c) = Q")

    a2.loglog(bT, lam_of.derivative()(bT), "o", ms=6, mfc="none", mew=1.4,
              color=DROP, label="anchors")
    a2.loglog(grid, r["lam"].slope(r["pl"], grid), "-", lw=1.6, color=KEEP,
              label="fit")
    a2.set_ylabel("dLambda/dT  [W/K]")
    a2.set_title("the conductance -- what the loop actually feels")

    a3.loglog(cT, cC, "o", ms=6, mfc="none", mew=1.4, color=DROP,
              label="tau x dLambda/dT at the tau anchors")
    a3.loglog(grid, cap_at(grid), "--", lw=1.3, color=REF, label="the seed")
    a3.loglog(grid, r["cap"](r["pc"], grid), "-", lw=1.6, color=KEEP,
              label="fit")
    a3.set_ylabel("C  [J/K]")
    a3.set_title("heat capacity -- measured where there are taus")

    tT, tV = taus
    a4.loglog(tT, tV, "o", ms=6, mfc="none", mew=1.4, color=DROP,
              label="measured taus")
    a4.loglog(grid, cap_at(grid) / lam_of.derivative()(grid), "--", lw=1.3,
              color=REF, label="model-free  C / Lambda'")
    a4.loglog(grid, r["cap"](r["pc"], grid) / r["lam"].slope(r["pl"], grid),
              "-", lw=1.6, color=KEEP, label="fit")
    a4.set_ylabel("tau  [s]")
    a4.set_title("time constant")

    for a in (a1, a2, a3, a4):
        a.set_xlabel("T  [K]")
        a.grid(alpha=.25, which="both")
        a.legend(fontsize=8.5, loc="best")

    fig.suptitle("Step 6 -- the model-free reading (no ODE, no least_squares) "
                 "against the converged fit (REVIEW section 4)", fontsize=11)
    fig.tight_layout()
    return _save(fig, outdir, "review-2-modelfree.png")


def fig_basins(outdir):
    """Step 6b: are the two optima different CURVES or only different numbers?"""
    hi, anchors, taus = _production_fit(True)
    lo, _, _ = _production_fit(False)
    grid = np.geomspace(float(anchors.T.min()), float(anchors.T.max()), 400)

    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(15.0, 4.8))
    for r, c, lab in ((lo, DROP, f"power-law seed, cost {lo['cost']:.0f}, "
                                 f"nfev {lo['nfev']}"),
                      (hi, KEEP, f"measured seed, cost {hi['cost']:.0f}, "
                                 f"nfev {hi['nfev']}")):
        a1.loglog(grid, 1e3 * r["lam"].slope(r["pl"], grid), lw=1.7, color=c,
                  label=lab)
        a2.loglog(grid, r["cap"](r["pc"], grid), lw=1.7, color=c, label=lab)
        resid = ((r["lam"](r["pl"], anchors.T) - r["lam"](r["pl"], anchors.Tc)
                  - anchors.Q) / r["lam"].slope(r["pl"], anchors.T))
        a3.semilogx(anchors.T, resid, "o", ms=4, mfc="none", mew=1.1, color=c,
                    label=f"{lab.split(',')[0]}  rms {np.sqrt(np.mean(resid**2)):.3f} K")
    a1.set_ylabel("dLambda/dT  [mW/K]")
    a1.set_title("conductance")
    a2.set_ylabel("C  [J/K]")
    a2.set_title("heat capacity")
    a3.axhline(0, color=REF, lw=1.0)
    a3.set_ylabel("anchor residual  [K]")
    a3.set_title("per-anchor residual")
    for a in (a1, a2, a3):
        a.set_xlabel("T  [K]")
        a.grid(alpha=.25, which="both")
        a.legend(fontsize=8, loc="best")

    fig.suptitle("Step 6b -- the objective is multi-modal: two seeds, two "
                 "optima (REVIEW section 4)", fontsize=11)
    fig.tight_layout()
    return _save(fig, outdir, "review-3-basins.png")


def fig_drift(outdir):
    """Step 5: K/day should scatter between output bands and mW/day should not."""
    anchors = F.load_anchors()
    days = D._days(anchors)
    bands = D.band_table(anchors)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 5.0))

    a1.plot(days, anchors.T, "o", ms=3, color="0.8", label="all anchors")
    colors = plt.cm.viridis(np.linspace(.15, .85, len(bands)))
    for b, c in zip(bands, colors):
        m = (anchors.u >= b["u_lo"]) & (anchors.u <= b["u_hi"])
        a1.plot(days[m], anchors.T[m], "o", ms=6, color=c,
                label=f"{b['u_lo']:.1f}-{b['u_hi']:.1f} %  "
                      f"({b['drift_k_per_day']:+.3f} K/day)")
        d = np.array([days[m].min(), days[m].max()])
        a1.plot(d, anchors.T[m].mean() + b["drift_k_per_day"] * (d - days[m].mean()),
                "-", lw=2, color=c)
    a1.set_xlabel("days from the first anchor")
    a1.set_ylabel("T_inf  [K]")
    a1.set_title("the campaign, and the bands the drift is measured in")
    a1.legend(fontsize=8, loc="best")
    a1.grid(alpha=.25)

    x = np.arange(len(bands))
    kday = np.array([b["drift_k_per_day"] for b in bands])
    mwday = np.array([b["drift_mw_per_day"] for b in bands])
    a2.plot(x, kday / np.median(kday), "o-", ms=8, lw=1.5, color=DROP,
            label=f"K/day     -- spans {kday.max() / kday.min():.1f}x")
    a2.plot(x, mwday / np.median(mwday), "s-", ms=8, lw=1.5, color=KEEP,
            label=f"mW/day  -- spans {mwday.max() / mwday.min():.1f}x")
    a2.axhline(1.0, color=REF, lw=1.0, ls="--")
    a2.set_xticks(x)
    a2.set_xticklabels([f"{b['u_lo']:.1f}-{b['u_hi']:.1f} %\n"
                        f"{b['T_lo']:.0f}-{b['T_hi']:.0f} K" for b in bands],
                       fontsize=8)
    a2.set_ylabel("drift, normalised to its own median")
    a2.set_title(f"the drift is a POWER: {np.median(mwday):+.3f} mW/day")
    a2.legend(fontsize=9)
    a2.grid(alpha=.25)

    fig.suptitle("Step 5 -- a drift in K/day is not a property of the cryostat "
                 "(REVIEW section 3)", fontsize=11)
    fig.tight_layout()
    return _save(fig, outdir, "review-4-drift.png")


def _save(fig, outdir, name):
    path = os.path.join(outdir, name)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print("wrote", path)
    return path


def main(argv=None) -> int:
    outdir = (argv or sys.argv[1:] or ["analysis"])[0]
    os.makedirs(outdir, exist_ok=True)
    fig_plantclock(outdir)
    fig_modelfree(outdir)
    fig_basins(outdir)
    fig_drift(outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
