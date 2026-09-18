"""How a hold's noise grows or shrinks with the averaging time.

**This is the figure of merit for the hold**, and it is here rather than in
`steps.py` because it answers a different question.  `measure.py` asks where a
window was heading and how confident it is; this asks whether averaging for
longer *helps*.  Those come apart exactly when it matters: a stretch can be
quiet at ten seconds and wander at an hour, and the rms over the whole window
is the same number for both.

This module reports the ladder.  **It does not hold the hold criterion any
more**: that is `analysis/hold_quality.py`, because as of 2026-09-18 the rule
needs TWO windows -- within 1.1x of a matched open-loop run, or below 10 mK
(Jeff; docs/ltspm3/requirements.md section 1c).

The retired criterion was::

    sigma_y(tau) <= sigma_y(10 s)   for all tau in [10 s, L/4]

-- "slow wander below the ten-second noise floor", relative to the window's own
short-tau floor rather than to an absolute bar.  The reason it was relative is
still true and still worth knowing: above 195 K the thermometer itself is the
floor at 109 mK, so an absolute bar there would be a statement about the sensor
rather than about the loop.  What it could not do is compare, and a criterion a
window applies to ITSELF cannot tell a quiet loop from a quiet cryostat -- the
open-loop night at 118 K fails it by 2.9x and the armed night fails it by 1.05x
while never exceeding 9 mK anywhere.  The question "did the loop make it
quieter" has to be asked of two runs on one scale, which is what the tool
downstream of this one does.

Overlapping Allan deviation, not the non-overlapping one: at the long taus that
decide this criterion a 33 h hold has only a handful of independent bins, and
throwing away the overlapping estimates throws away the confidence with them.
The equivalent degrees of freedom are reported beside every point so that a
number resting on three bins looks like one.

**The mean is removed per bin by the Allan definition itself**, which is why a
drifting hold does not simply read worse everywhere: a linear drift appears as
sigma_y growing like tau, and that is the shape to look for.

What it measures on this cryostat, open loop at 118 K, sample channel::

    tau s          4     10     60    130    600   3600  23668
    pc-0908     7.79   8.73   7.95   7.38   9.52  12.72  24.49   <- pre-fault
    pc-0910     7.81   8.75   8.28   9.29  16.44  10.61  14.18   <- post-reseat
    edf        23666   9466   1577    727    157     25      3

The last column rests on three bins and is quoted as a direction, not a
number.

**Averaging stops helping at about two minutes.**  The pre-fault hold falls to
a floor of 7.38 mK at tau = 130 s and rises monotonically after it -- 12.7 mK at
an hour, 24.5 mK at 6.6 h -- which is drift taking over from noise.  So the
criterion above is NOT met open loop, by a factor of 2.9, and that is the point:
flattening that rise is what the loop is for.  It is not a target already met
and quoted back.

The two rows also make the second, independent case that the 2026-09-10
connector was RESEATED AND NOT REPAIRED.  At matched output the post-reseat hold
carries about twice the pre-fault wander over 300-1200 s -- 16.4 mK against
9.5 at tau = 600 s -- while the COLDPLATE over the same window is 0.24 mK and is
if anything quieter than before.  Excess on the sample node and none on the cold
head is what a contact whose resistance moves looks like.  The other signature is
the 0.30 K steady-state offset at matched power; see the note on
``pc-20260910-144849`` in the manifest.

Beware of comparing against a PREDICTION.  ``plans/pid-4-commissioning.md`` C6 used
to quote 6.1 / 4.1 / 2.5 mK at 4 / 60 / 600 s from a 1/sqrt(N) model corrected
for lag-1 correlation.  That model has no drift term, so it goes on promising
improvement through the region where this cryostat has stopped improving, and at
600 s it is optimistic by nearly 4x.  Measure it; do not model it.

Run it::

    python analysis/allan.py                      # the open-loop reference hold
    python analysis/allan.py --window ID          # any manifest window
    python analysis/allan.py --csv PATH --column Sample
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np

sys.path.insert(0, "analysis")

import segments as S  # noqa: E402

#: The bar: 26.30 h at 64.0155 % on 118.633 K, 2026-09-08 15:48 -> 09-09 18:06,
#: ending at the power transient.  The quietest long window in the archive and
#: OPEN LOOP, so it is what a software PID has to beat rather than a result a
#: PID produced.
#:
#: Deliberately the PRE-FAULT hold and not the longer post-reseat one, which is
#: the present state of the cryostat but carries the reseated connector's excess
#: at 300-1200 s.  For that one, ``--window pc-20260910-144849``; the two are
#: tabulated above and the difference between them is the finding.
REFERENCE = "pc-20260908-154814"

#: Averaging times reported, in seconds.  Geometric, because the interesting
#: structure is over decades and a linear ladder spends all its points at the
#: end where there is least confidence.
DECADES_PER_STEP = 4


def taus(dt: float, span: float, per_decade: int = DECADES_PER_STEP):
    """Averaging times from two samples to a quarter of the record.

    ``span/4`` is where the criterion stops, and it is not arbitrary: at
    ``tau = L/4`` the estimate has about three independent bins in it and
    anything longer is reporting the endpoints of one realisation.
    """
    lo, hi = 2 * dt, span / 4.0
    if hi <= lo:
        return np.array([lo])
    n = max(int(round(per_decade * math.log10(hi / lo))), 1)
    return np.unique(np.round(np.geomspace(lo, hi, n + 1) / dt) * dt)


def adev(t, y, tau_list=None):
    """``(tau, sigma_y, edf)`` -- overlapping Allan deviation of ``y(t)``.

    ``y`` is a temperature, so this is the deviation of the QUANTITY, not of a
    fractional frequency: the units come out in kelvin and a reader comparing
    against the thermometer's own noise does not have to scale anything.

    Resampled onto a uniform grid first.  The recorder's cadence jitters -- a
    retry costs a cycle -- and the overlapping estimator indexes by sample, so
    an unresampled series quietly means a slightly different tau in every bin.
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    dt = float(np.median(np.diff(t)))
    grid = np.arange(t[0], t[-1], dt)
    y = np.interp(grid, t, y)
    n = len(y)
    span = float(grid[-1] - grid[0])
    if tau_list is None:
        tau_list = taus(dt, span)
    # Cumulative sum, so every bin mean is two lookups rather than a slice.
    c = np.concatenate([[0.0], np.cumsum(y)])
    out_tau, out_sig, out_edf = [], [], []
    for tau in np.atleast_1d(tau_list):
        m = int(round(tau / dt))
        if m < 1 or 2 * m >= n:
            continue
        # Bin means at every offset: the overlapping estimator.
        means = (c[m:] - c[:-m]) / m
        d = means[m:] - means[:-m]
        if not len(d):
            continue
        sig = math.sqrt(0.5 * float(np.mean(d ** 2)))
        # Equivalent degrees of freedom, white-noise approximation.  It is the
        # right order and it is here to stop a long-tau point being read as
        # though it were as solid as a short-tau one.
        out_tau.append(m * dt)
        out_sig.append(sig)
        out_edf.append(max(n / m - 1.0, 1.0))
    return np.array(out_tau), np.array(out_sig), np.array(out_edf)


def grade(tau, sig, floor_tau_s=10.0):
    """PID_PLAN.md section 1's criterion: ``(ok, floor, worst_tau, worst)``.

    ``floor`` is ``sigma_y`` at the tau nearest ``floor_tau_s``, and the test
    is that nothing longer exceeds it.  Returns the worst offender so that a
    failure says *where* rather than only that it failed.
    """
    if not len(tau):
        return False, math.nan, math.nan, math.nan
    k = int(np.argmin(np.abs(tau - floor_tau_s)))
    floor = float(sig[k])
    later = np.flatnonzero(tau >= tau[k])
    j = int(later[np.argmax(sig[later])])
    return bool(sig[j] <= floor), floor, float(tau[j]), float(sig[j])


def report(tau, sig, edf, label, floor_tau_s=10.0):
    ok, floor, wt, ws = grade(tau, sig, floor_tau_s)
    print(f"\n{label}")
    print(f"{'tau s':>10} {'sigma_y mK':>12} {'edf':>8}   vs floor")
    for a, s, e in zip(tau, sig, edf):
        mark = "" if s <= floor else "  <-- above the floor"
        print(f"{a:10.0f} {1e3 * s:12.3f} {e:8.0f}   {s / floor:6.2f}x{mark}")
    print(f"\n  floor sigma_y({floor_tau_s:.0f} s) = {1e3 * floor:.3f} mK")
    print(f"  worst at tau = {wt:.0f} s: {1e3 * ws:.3f} mK, {ws / floor:.2f}x the floor")
    print(f"  PID_PLAN section 1 criterion: {'MET' if ok else 'NOT MET'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window", default=REFERENCE,
                    help=f"manifest window id (default {REFERENCE})")
    ap.add_argument("--csv", help="a recorder CSV instead of a manifest window")
    ap.add_argument("--column", default="Sample")
    ap.add_argument("--floor-tau", type=float, default=10.0)
    ap.add_argument("--at", type=float, nargs="*",
                    help="report these taus as well, in seconds")
    args = ap.parse_args()

    if args.csv:
        import csv as _csv
        import datetime as _dt
        from _data import open_table
        t, y = [], []
        with open_table(args.csv) as fh:
            for r in _csv.DictReader(fh):
                v = (r.get(args.column) or "").strip()
                if not v:
                    continue
                t.append(_dt.datetime.fromisoformat(r["Timestamp"]).timestamp())
                y.append(float(v))
        t, y = np.array(t), np.array(y)
        label = f"{args.csv}  {args.column}  {len(t)} rows"
    else:
        sl = S.load(args.window)
        t, y = sl.epoch, sl.col(args.column)
        w = sl.window
        label = (f"{args.window}  {args.column}  {len(t)} rows, "
                 f"{(t[-1] - t[0]) / 3600:.2f} h, {w.u_pct}% , "
                 f"{np.mean(y):.3f} K mean")

    tl = taus(float(np.median(np.diff(t))), float(t[-1] - t[0]))
    if args.at:
        tl = np.unique(np.concatenate([tl, np.array(args.at, float)]))
    tau, sig, edf = adev(t, y, tl)
    ok = report(tau, sig, edf, label, args.floor_tau)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
