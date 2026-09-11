"""Is the cryostat drifting, by how much, and where may a drift knot go?

REFIT_PLAN.md Phase B step 5, and it is a **gate** rather than a stage of the
fit: nothing here changes a curve.  It answers the two questions step 8 is not
allowed to guess at, and it answers them from the anchors alone.

Why the fit needs this at all
-----------------------------

The refit's whole diagnosis (REFIT_PLAN.md section 2.3) is that the cryostat
drifts and the model treats 55 days of anchors as simultaneous.  The remedy is
a slow term in DATE.  The danger is that a drift term and ``Lambda`` are the
same parameter unless the data can tell them apart, and whether it can is a
property of the anchors' *coverage*, not of the model:

    **Inside one drift-knot interval, an anchor set that does not span
    temperature cannot separate "the cryostat got warmer" from "Lambda is
    lower here".**  Both raise every anchor in the interval by the same amount.

So a knot interval has to be earned by anchors at genuinely different
temperatures inside it, and `linspace` earns nothing.  That is what
:func:`coverage` measures and what :func:`knots_pass` decides.

What is measured here
---------------------

``drift`` -- the campaign rate, in K/day at FIXED HEATER OUTPUT.  Measured by
regressing ``T_inf`` jointly on ``u_pct`` and date inside a narrow band of
output, because a band is what holds ``Lambda`` still: two anchors at the same
output are at the same place on the curve, so what is left between them is
time.  The band is chosen by :func:`best_band` rather than by hand -- widest
date span first, then most anchors -- and both the gain and the residual come
out beside the rate so the number can be judged rather than quoted.

``gain`` -- K per percent of output, locally.  It is a by-product of the same
regression and it is the independent check REFIT_PLAN.md section 2.3 uses:
the fit says 13-15 K/% in that band, and if this disagrees the band is wrong
or the anchors are.

Usage::

    python analysis/drift.py              # the rate, the gain, the coverage
    python analysis/drift.py --knots 3    # would 3 linspaced knots pass?
"""
from __future__ import annotations

import datetime as _dt
import math

import numpy as np

import fit_ode as F

#: Width of the output band the campaign rate is measured in, in percent.
#:
#: Narrow enough that ``Lambda`` is effectively constant across it and wide
#: enough to hold anchors from both ends of the campaign.  At the 13 K/% the
#: fit reports near 100 K, 1.5 % is about 20 K of sample temperature -- which
#: sounds enormous until you notice it is the same 20 K at both dates, so it
#: enters the regression as the ``u`` coefficient and leaves the date
#: coefficient alone.  That is the whole reason ``u`` is a regressor here
#: rather than a filter.
BAND_PCT = 1.5

#: A band has to span at least this many days to say anything about a rate of
#: K/day.  Half the drift the campaign shows over 55 days is 4.6 K; over 10
#: days it is 1.7 K, comfortably above the 0.3 K an anchor's own bar allows.
BAND_MIN_DAYS = 10.0

#: ...and hold at least this many anchors, or the slope is two points and a
#: line through them.
BAND_MIN_N = 8

#: A drift-knot interval is only earned if its anchors span temperature.
#:
#: TWO SEPARATED DECADES with at least this many anchors in each, which is a
#: stricter and more honest test than a bare max/min ratio: 20 anchors at 145 K
#: and one at 8 K give a ratio of 18 and separate nothing, because the single
#: cold point is free to be an outlier and the drift will happily absorb it.
#: REFIT_PLAN.md step 5 asks for exactly this -- "a robust metric, never by
#: linspace".
KNOT_MIN_PER_DECADE = 5
#: How far apart the two decades have to be, as a ratio of their midpoints.
KNOT_DECADE_RATIO = 3.0


def _days(anchors) -> np.ndarray:
    return (anchors.t_abs - np.nanmin(anchors.t_abs)) / 86400.0


def regress(u, days, T):
    """``(gain_k_per_pct, drift_k_per_day, rms, n)`` for ``T ~ a + b u + c t``.

    Ordinary least squares, deliberately.  The anchors carry error bars and
    this does not use them: the bars are dominated by ``ANCHOR_SIGMA_K``, which
    is **3.0 K for prepython and 1.0 for the rest** -- an era label, not a
    measurement -- so weighting by them would down-weight exactly the old half
    of the campaign that carries the date leverage, and the drift would come
    back small for a reason that has nothing to do with the cryostat.  That is
    trap T3 arriving early.
    """
    M = np.column_stack([np.ones_like(u), u, days])
    c, *_ = np.linalg.lstsq(M, T, rcond=None)
    resid = M @ c - T
    return (float(c[1]), float(c[2]),
            float(np.sqrt(np.mean(resid ** 2))), len(T))


def dq_du(u):
    """``dQ/du`` in W per percent of output, at output ``u``.

    ``Q = (G V u/100)^2 / R``, so this is ``2 (G V/100)^2 u / R`` -- the heater
    is a resistor and its power is quadratic, which is why a percent is worth
    twice as much at 64 % as at 32 %.
    """
    return 2.0 * (F.GAIN * F.V_FS / 100.0) ** 2 * np.asarray(u, float) / F.R_OHM


def band_table(anchors, width=BAND_PCT):
    """Every usable output band, each measuring the drift independently.

    **The point of the table is the last column.**  A drift quoted in K/day is
    not a property of the cryostat: it is the underlying change multiplied by
    the local gain, which runs from about 2 K/% at 30 K to 13 K/% at 100 K, so
    the same cause reads six times larger at the warm end.  Divided back
    through ``dT/dP`` it becomes mW/day of equivalent parasitic load, and THAT
    is the number that should agree between bands if the drift is one thing.
    REFIT_PLAN.md section 2.3 calls this "the fit's own currency" and quotes
    +0.27 mW/day for it.

    Bands are the sliding windows :func:`best_band` scores, de-overlapped by
    taking the best-scoring one and then skipping anything sharing an anchor
    with it, so each row is an independent measurement rather than the same
    anchors read six ways.
    """
    u, days, T = anchors.u, _days(anchors), anchors.T
    cands = []
    for lo in np.unique(u):
        m = (u >= lo) & (u <= lo + width)
        if int(m.sum()) < BAND_MIN_N:
            continue
        span = float(days[m].max() - days[m].min())
        if span < BAND_MIN_DAYS:
            continue
        cands.append((span, int(m.sum()), float(lo), m))
    cands.sort(reverse=True, key=lambda c: (c[0], c[1]))

    rows, taken = [], np.zeros(len(u), bool)
    for span, n, lo, m in cands:
        if (m & taken).any():
            continue
        taken |= m
        gain, rate, rms, n = regress(u[m], days[m], T[m])
        u_bar = float(np.mean(u[m]))
        k_per_w = gain / float(dq_du(u_bar))
        rows.append({
            "u_lo": lo, "u_hi": lo + width, "u_bar": u_bar, "n": n,
            "day_span": span, "T_lo": float(T[m].min()), "T_hi": float(T[m].max()),
            "gain_k_per_pct": gain, "k_per_w": k_per_w,
            "drift_k_per_day": rate, "rms_k": rms,
            "drift_mw_per_day": 1e3 * rate / k_per_w if k_per_w else math.nan,
        })
    return rows


def best_band(anchors, width=BAND_PCT):
    """The output band that measures the rate best: longest in days, then biggest.

    Every anchor's output is tried as the band's low edge, which is a handful
    of hundreds of candidates and needs no cleverness.  Longest date span
    first, because a rate divided by a short baseline is mostly noise, and the
    count only breaks ties.
    """
    u, days = anchors.u, _days(anchors)
    best = None
    for lo in np.unique(u):
        m = (u >= lo) & (u <= lo + width)
        n = int(m.sum())
        if n < BAND_MIN_N:
            continue
        span = float(days[m].max() - days[m].min())
        if span < BAND_MIN_DAYS:
            continue
        score = (span, n)
        if best is None or score > best[0]:
            best = (score, lo, m)
    return (None, None) if best is None else (best[1], best[2])


def campaign_rate(anchors):
    """``dict`` -- the drift at fixed output, and the band it was measured in."""
    lo, m = best_band(anchors)
    if lo is None:
        raise SystemExit(
            f"drift: no {BAND_PCT} % output band holds {BAND_MIN_N} anchors "
            f"over {BAND_MIN_DAYS} days.  The campaign rate cannot be measured "
            f"from this anchor set, and step 8 must not assume one.")
    days = _days(anchors)
    gain, rate, rms, n = regress(anchors.u[m], days[m], anchors.T[m])
    return {
        "u_lo": float(lo), "u_hi": float(lo + BAND_PCT), "n": n,
        "gain_k_per_pct": gain, "drift_k_per_day": rate, "rms_k": rms,
        "day_lo": float(days[m].min()), "day_hi": float(days[m].max()),
        "T_lo": float(anchors.T[m].min()), "T_hi": float(anchors.T[m].max()),
        "mask": m,
    }


def coverage(anchors, edges):
    """One row per drift-knot interval: can its anchors tell time from Lambda?

    ``edges`` are in days.  The verdict per interval is :func:`knots_pass`'s
    two-decade test, and the ``ratio`` column is reported beside it because it
    is what REFIT_PLAN.md step 5 first proposed -- and because seeing the two
    disagree is the point.  A ratio of 20 on twenty warm anchors and one cold
    one passes the ratio test and fails this one.
    """
    days = _days(anchors)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (days >= a) & (days < b) if b < edges[-1] else (days >= a)
        T = anchors.T[m]
        row = {"day_lo": float(a), "day_hi": float(b), "n": int(m.sum())}
        if len(T):
            row |= {"T_lo": float(T.min()), "T_hi": float(T.max()),
                    "ratio": float(T.max() / T.min())}
        else:
            row |= {"T_lo": math.nan, "T_hi": math.nan, "ratio": math.nan}
        row["ok"], row["why"] = _decades_ok(T)
        out.append(row)
    return out


def _decades_ok(T):
    """``(ok, why)`` -- are there two separated clumps with enough in each?

    The split is tried at every gap in the sorted temperatures rather than at a
    fixed boundary, so "two decades" means whatever two the data actually has.
    """
    if len(T) < 2 * KNOT_MIN_PER_DECADE:
        return False, (f"{len(T)} anchors, needs "
                       f"{2 * KNOT_MIN_PER_DECADE}")
    s = np.sort(T)
    best = None
    for i in range(KNOT_MIN_PER_DECADE, len(s) - KNOT_MIN_PER_DECADE + 1):
        lo, hi = s[:i], s[i:]
        if float(np.median(hi) / np.median(lo)) >= KNOT_DECADE_RATIO:
            # The MOST BALANCED passing split, not the first one found.  The
            # first is always the most lopsided -- 5 anchors against all the
            # rest -- which understates the evidence and reads as if the
            # interval only just scraped through.
            score = min(len(lo), len(hi))
            if best is None or score > best[0]:
                best = (score, lo, hi)
    if best is None:
        return False, (f"{len(T)} anchors but no split {KNOT_DECADE_RATIO}x "
                       f"apart with {KNOT_MIN_PER_DECADE} either side")
    _, lo, hi = best
    return True, (f"{len(lo)} near {np.median(lo):.0f} K, "
                  f"{len(hi)} near {np.median(hi):.0f} K")


def knots_pass(anchors, edges):
    """Every interval earns its knot, or this returns False and says which did not."""
    rows = coverage(anchors, edges)
    return all(r["ok"] for r in rows), rows


def _stamp(anchors, day) -> str:
    return _dt.datetime.fromtimestamp(
        float(np.nanmin(anchors.t_abs)) + day * 86400.0).strftime("%Y-%m-%d")


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="the campaign drift, and whether a drift knot is earned")
    ap.add_argument("--knots", type=int, default=0,
                    help="test N linspaced knots as well as the affine default")
    a = ap.parse_args(argv)

    anchors = F.load_anchors()
    days = _days(anchors)
    print(f"{len(anchors)} anchors, {days.max():.2f} days, "
          f"{_stamp(anchors, 0)} -> {_stamp(anchors, days.max())}\n")

    r = campaign_rate(anchors)
    print("the campaign drift, at fixed heater output")
    print(f"  band          {r['u_lo']:.3f}-{r['u_hi']:.3f} %  "
          f"{r['n']} anchors, {r['T_lo']:.1f}-{r['T_hi']:.1f} K")
    print(f"  baseline      day {r['day_lo']:.1f} to {r['day_hi']:.1f}  "
          f"({r['day_hi'] - r['day_lo']:.1f} days)")
    print(f"  local gain    {r['gain_k_per_pct']:+.2f} K/%")
    print(f"  DRIFT         {r['drift_k_per_day']:+.4f} K/day   "
          f"= {r['drift_k_per_day'] * days.max():+.2f} K over the campaign")
    print(f"  residual      {r['rms_k']:.3f} K rms")
    k_per_w = r["gain_k_per_pct"] / float(dq_du(0.5 * (r["u_lo"] + r["u_hi"])))
    print(f"  -> {1e3 * r['drift_k_per_day'] / k_per_w:+.3f} mW/day of "
          f"equivalent parasitic load, which is the number to compare")

    rows = band_table(anchors)
    print("\nthe same drift measured in every independent output band")
    print("  K/day is NOT a property of the cryostat -- it is the underlying "
          "change times the\n  local gain, 2 K/% at 30 K and 13 K/% at 100 K.  "
          "mW/day is, and these should agree.")
    print(f"  {'band %':>14}{'n':>4}{'days':>7}{'T range K':>15}"
          f"{'K/%':>8}{'K/W':>9}{'K/day':>9}{'mW/day':>9}{'rms K':>8}")
    for b in rows:
        print(f"  {b['u_lo']:>6.2f}-{b['u_hi']:<7.2f}{b['n']:>4}"
              f"{b['day_span']:>7.1f}{b['T_lo']:>7.1f}-{b['T_hi']:<7.1f}"
              f"{b['gain_k_per_pct']:>8.2f}{b['k_per_w']:>9.1f}"
              f"{b['drift_k_per_day']:>9.4f}{b['drift_mw_per_day']:>9.3f}"
              f"{b['rms_k']:>8.3f}")
    mw = np.array([b["drift_mw_per_day"] for b in rows])
    if len(mw) > 1:
        print(f"  -> {len(mw)} bands: median {np.median(mw):+.3f} mW/day, "
              f"spread {mw.min():+.3f} to {mw.max():+.3f}")
    print("  REFIT_PLAN.md section 2.3 measured +0.27 mW/day, in a band this "
          "one does not\n  reuse, and independently 4.8-5.4 mW as a per-era "
          "step offset over ~20 days.")

    print("\ncoverage -- can each interval's anchors tell time from Lambda?")
    print(f"  a knot interval is earned by {KNOT_MIN_PER_DECADE} anchors in "
          f"each of two clumps {KNOT_DECADE_RATIO}x apart in T.")
    plans = [("affine (1 slope, the default)", [0.0, days.max()])]
    if a.knots >= 2:
        plans.append((f"{a.knots} linspaced knots",
                      list(np.linspace(0.0, days.max(), a.knots))))
    for label, edges in plans:
        ok, rows = knots_pass(anchors, edges)
        print(f"\n  {label}:  {'EARNED' if ok else 'NOT EARNED'}")
        print(f"    {'days':>14}{'n':>5}{'T range K':>16}{'ratio':>8}  verdict")
        for row in rows:
            print(f"    {row['day_lo']:>6.1f}-{row['day_hi']:<7.1f}{row['n']:>5}"
                  f"{row['T_lo']:>8.1f}-{row['T_hi']:<7.1f}{row['ratio']:>8.1f}"
                  f"  {'ok' if row['ok'] else 'NO'}  {row['why']}")

    print("\n  DEFAULT TO AFFINE.  One slope over the campaign is one parameter "
          "against 55 days\n  and it is the only shape the coverage clearly "
          "supports.  Earn more knots at NAMED\n  epochs that pass the test "
          "above -- a recalibration, a repair -- never by linspace.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
