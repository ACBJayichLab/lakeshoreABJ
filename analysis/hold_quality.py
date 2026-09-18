"""Is the loop helping?  One command, two windows, one answer.

`allan.py` says how a hold's noise grows with the averaging time.  This asks the
question that has to be settled before the loop can be trusted with an
experiment, which is a DIFFERENT question: **is the closed loop better or worse
than leaving the heater alone?**  A single window cannot answer it.  The
criterion in PID_PLAN section 1 is self-referential -- it compares a window to
its own 10 s floor -- so a loop that quietly doubles the wander at every
timescale can still pass, and an open-loop night that happens to have a quiet
hour can still fail.

Measured 2026-09-17, on the first two matched 15 h windows this cryostat has
produced, at the same temperature and the same window length:

    open loop  2026-09-15 18:00 -> 09-16 09:00   sigma_y(2368 s) =  3.78 mK
    armed      2026-09-16 18:00 -> 09-17 09:00   sigma_y(2368 s) = 21.19 mK

A factor of 5.6 at 39 minutes of averaging, with edf 22 under it.  The armed
night missed PID_PLAN's criterion at 2.47x its floor; the open-loop night
essentially met it.  **That comparison is the whole reason this module exists**,
and it is one nobody could make before, because nothing in the tree put two
windows side by side.

It reports three things, and the middle one is the one that localises a fault:

* the **Allan table**, from `allan.py` -- reused, not reimplemented, so the two
  tools cannot disagree about what sigma_y means;
* a **band table** -- rms in the 10-40, 40-90 and 90-240 minute bands.  Allan
  tells you that averaging stopped helping; the bands tell you *at what period*
  the energy sits, which is what identifies a mechanism.  The 2026-09-16 night
  put 19.7 mK into 90-240 min against 2.6 open loop, and the closed loop's own
  natural period, `2*pi*sqrt(tau*Ti/(Kp*K))`, is 142 min on the armed gains.
  (That reduces to the more familiar `2*pi*tau/sqrt(Kp*K)` only when
  `Ti == tau`, which is what SIMC picks and is NOT what the armed file runs.);
* the **ratio**, per band and per tau, when a reference window is given.

A ratio near 1 is a loop that is not making things worse.  Under 1 is a loop
that is earning its place.  The 2026-09-16 bands are 1.63 / 3.34 / 7.59.

**The rule it grades against** (Jeff, 2026-09-18 --
`docs/ltspm3/requirements.md` section 1c): at every averaging time, within
`RATIO_BAR` of open loop **or** below `ABSOLUTE_BAR_K`.  Either clause passes.
The verdict says which clause carried it where, because a curve that passes
only on the floor is a different animal from one that passes on the ratio, and
the difference is the whole reason the 09-18 night needed a decision from Jeff
rather than a pass mark from a script.

**Match the CLOCK HOURS, not just the window length.**  A diurnal term lives
in these bands: grading 2026-09-18 00:00-10:30 against 09-15 22:00-08:30 read
1.39 / 1.44 / 1.85, and moving both to 00:00-08:30 read 1.57 / 1.97 / 1.56.
Two hours of the building waking up is worth a fifth of the answer.

Run it::

    python -m analysis.hold_quality --csv data/ltspm3-armed_2026-09-1[67].csv \\
        --from 2026-09-16T18:00 --to 2026-09-17T09:00 \\
        --vs data/ltspm3-heater_2026-09-1[56].csv \\
        --vs-from 2026-09-15T18:00 --vs-to 2026-09-16T09:00

`--vs` may be omitted, in which case only the absolute numbers are printed --
but a night graded without a reference is a night that can only be compared to
the last one somebody wrote down.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import allan  # noqa: E402

#: The periods the bands cover, in minutes.  Not round numbers for their own
#: sake: 10-40 is above the loop's dead time and below its integral corner,
#: 40-90 straddles the corner (`2*pi*Ti` is 94 min at `ti: 900`), and 90-240
#: is where this cryostat's closed-loop mode was measured on 2026-09-16.
BANDS = ((10.0, 40.0), (40.0, 90.0), (90.0, 240.0))

#: The grid the band arithmetic runs on.  The recorder's cadence is 2 s and the
#: shortest band edge is 10 min, so decimating to 10 s costs nothing and makes
#: an FFT over a 15 h window cheap.  It also makes two windows recorded at
#: different cadences directly comparable, which they otherwise are not.
GRID_S = 10.0

#: THE HOLD RULE -- Jeff, 2026-09-18, `docs/ltspm3/requirements.md` section 1c,
#: which is its one home and the only place to change it.  ONE rule over the
#: whole Allan curve, and EITHER clause passes: a hold within `RATIO_BAR` of a
#: matched open-loop window is not making anything worse, and one under
#: `ABSOLUTE_BAR_K` is under the thermometer's own noise whatever the ratio
#: says.
#:
#: Deliberately NOT command-line arguments.  A bar somebody typed at the prompt
#: is a bar nobody agreed to, and this is the number the loop is graded against.
RATIO_BAR = 1.1
ABSOLUTE_BAR_K = 10e-3


def when(text, flag):
    """Parse a `--from`/`--to`, or say what is wrong and stop.

    Bare `datetime.fromisoformat` raises `ValueError: Invalid isoformat string`
    out of the middle of `load`, which reaches the operator as a traceback with
    the offending flag nowhere in it.  This is a tool for a person at a console
    at the end of a night, so a bad timestamp is an ordinary thing to type.
    """
    if text is None:
        return None
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        raise SystemExit(
            f"{flag}: {text!r} is not a timestamp.\n"
            f"  Use 2026-09-16T18:00, or '2026-09-16 18:00', or a bare "
            f"2026-09-16 for midnight.\n"
            f"  Omit {flag} entirely to take the whole file."
        ) from None


def load(patterns, t_from=None, t_to=None, column="Sample"):
    """Timestamps and one column from one or more recorder CSVs.

    Globs are expanded here rather than left to the shell, because the shell on
    the cryostat is `cmd`, which does not expand them.

    ``t_from``/``t_to`` are `datetime` or None -- already parsed by `when`, so
    a bad one has been reported against the flag it came from rather than
    surfacing here.
    """
    paths = []
    for p in patterns:
        hit = sorted(glob.glob(p))
        paths.extend(hit or [p])
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise SystemExit("no such file(s): " + ", ".join(missing)
                         + "\n  (a glob that matches nothing is passed through "
                           "verbatim, which is what you are seeing)")
    lo = t_from.timestamp() if t_from else -np.inf
    hi = t_to.timestamp() if t_to else np.inf
    t, y = [], []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                v = (row.get(column) or "").strip()
                stamp = (row.get("Timestamp") or "").strip()
                if not v or not stamp:
                    continue
                try:
                    when = dt.datetime.fromisoformat(stamp).timestamp()
                except ValueError:
                    continue
                if lo <= when <= hi:
                    t.append(when)
                    y.append(float(v))
    order = np.argsort(t)
    return np.asarray(t)[order], np.asarray(y)[order], paths


def band_rms(t, y, lo_min, hi_min, grid_s=GRID_S):
    """rms of ``y`` restricted to periods between ``lo_min`` and ``hi_min``.

    Brick-wall in the frequency domain on a uniform grid.  The window is NOT
    tapered: a taper would bias the rms low by its own coherent gain, and what
    is wanted here is the amount of signal in the band rather than an unbiased
    spectral density.  Both windows get the same treatment, which is what the
    ratio needs.
    """
    good = np.isfinite(y)
    if good.sum() < 100:
        return float("nan")
    grid = np.arange(t[0], t[-1], grid_s)
    if len(grid) < 16:
        return float("nan")
    ys = np.interp(grid, t[good], y[good])
    n = len(grid)
    freq = np.fft.rfftfreq(n, grid_s)
    spec = np.fft.rfft(ys - ys.mean())
    with np.errstate(divide="ignore"):
        period_min = np.where(freq > 0, 1.0 / np.maximum(freq, 1e-15) / 60.0,
                              np.inf)
    keep = (freq > 0) & (period_min >= lo_min) & (period_min <= hi_min)
    if not keep.any():
        return float("nan")
    masked = np.zeros_like(spec)
    masked[keep] = spec[keep]
    return float(np.std(np.fft.irfft(masked, n)))


def bands(t, y):
    return [band_rms(t, y, lo, hi) for lo, hi in BANDS]


def describe(t, y, label, paths, floor_tau=10.0):
    span_h = (t[-1] - t[0]) / 3600.0
    print(f"\n{'=' * 72}")
    print(f"{label}")
    print(f"  {len(t)} rows, {span_h:.2f} h, "
          f"{dt.datetime.fromtimestamp(t[0]):%Y-%m-%d %H:%M} -> "
          f"{dt.datetime.fromtimestamp(t[-1]):%Y-%m-%d %H:%M}")
    print(f"  mean {np.mean(y):.4f} K   sd {np.std(y) * 1e3:.2f} mK   "
          f"p-p {np.ptp(y) * 1e3:.2f} mK")
    for path in paths:
        print(f"    {path}")
    tau, sig, edf = allan.adev(t, y)
    allan.report(tau, sig, edf, "  Allan deviation", floor_tau)
    b = bands(t, y)
    print("\n  band rms, by period:")
    for (lo, hi), v in zip(BANDS, b):
        print(f"    {lo:5.0f}-{hi:5.0f} min   {v * 1e3:8.2f} mK")
    return tau, sig, b


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", nargs="+", required=True,
                    help="recorder CSV(s); globs are expanded here")
    ap.add_argument("--from", dest="t_from", help="ISO timestamp, inclusive")
    ap.add_argument("--to", dest="t_to", help="ISO timestamp, inclusive")
    ap.add_argument("--vs", nargs="+",
                    help="reference CSV(s) -- normally an OPEN-LOOP window")
    ap.add_argument("--vs-from", dest="vs_from")
    ap.add_argument("--vs-to", dest="vs_to")
    ap.add_argument("--column", default="Sample")
    ap.add_argument("--floor-tau", type=float, default=10.0)
    args = ap.parse_args()

    t, y, paths = load(args.csv, when(args.t_from, "--from"),
                       when(args.t_to, "--to"), args.column)
    if len(t) < 100:
        print(f"only {len(t)} rows in that window -- nothing to grade",
              file=sys.stderr)
        return 1
    tau, sig, b = describe(t, y, f"UNDER TEST  ({args.column})", paths,
                           args.floor_tau)

    if not args.vs:
        print("\nNo reference given (--vs).  The absolute numbers above are "
              "only\ncomparable to another window measured the same way.")
        return 0

    rt, ry, rpaths = load(args.vs, when(args.vs_from, "--vs-from"),
                          when(args.vs_to, "--vs-to"), args.column)
    if len(rt) < 100:
        print(f"only {len(rt)} rows in the reference window", file=sys.stderr)
        return 1
    rtau, rsig, rb = describe(rt, ry, f"REFERENCE   ({args.column})", rpaths,
                              args.floor_tau)

    print(f"\n{'=' * 72}")
    print("RATIO -- under test / reference.  Above 1 is the loop making it worse.")
    print("\n  by band:")
    worst_band = 0.0
    for (lo, hi), a, r in zip(BANDS, b, rb):
        ratio = a / r if r else float("nan")
        worst_band = max(worst_band, ratio if np.isfinite(ratio) else 0.0)
        print(f"    {lo:5.0f}-{hi:5.0f} min   {a * 1e3:8.2f} / {r * 1e3:8.2f} mK"
              f"   = {ratio:6.2f}x")

    print("\n  by averaging time (nearest common taus):")
    worst_tau, worst_ratio = float("nan"), 0.0
    for a_tau, a_sig in zip(tau, sig):
        k = int(np.argmin(np.abs(rtau - a_tau)))
        # Only compare taus the two windows genuinely share: a ratio against a
        # tau twice as long is not a ratio.
        if not (0.5 <= rtau[k] / a_tau <= 2.0):
            continue
        ratio = a_sig / rsig[k] if rsig[k] else float("nan")
        if np.isfinite(ratio) and ratio > worst_ratio:
            worst_ratio, worst_tau = ratio, a_tau
        print(f"    tau {a_tau:8.0f} s   {a_sig * 1e3:8.3f} / "
              f"{rsig[k] * 1e3:8.3f} mK   = {ratio:6.2f}x")

    print(f"\n  worst band ratio {worst_band:.2f}x; "
          f"worst tau ratio {worst_ratio:.2f}x at tau = {worst_tau:.0f} s")

    # THE RULE, applied.  Either clause passes, at every averaging time.
    failed, carried_by_floor = [], []
    for a_tau, a_sig in zip(tau, sig):
        k = int(np.argmin(np.abs(rtau - a_tau)))
        if not (0.5 <= rtau[k] / a_tau <= 2.0):
            continue
        ratio = a_sig / rsig[k] if rsig[k] else float("nan")
        if np.isfinite(ratio) and ratio <= RATIO_BAR:
            continue
        if a_sig <= ABSOLUTE_BAR_K:
            carried_by_floor.append((a_tau, a_sig, ratio))
        else:
            failed.append((a_tau, a_sig, ratio))

    print(f"\n  RULE: within {RATIO_BAR}x of open loop, OR below "
          f"{ABSOLUTE_BAR_K * 1e3:.0f} mK -- at every averaging time.")
    if carried_by_floor:
        lo = min(t for t, _, _ in carried_by_floor)
        hi = max(t for t, _, _ in carried_by_floor)
        print(f"    {len(carried_by_floor)} tau from {lo:.0f} to {hi:.0f} s are "
              f"over {RATIO_BAR}x and pass on the {ABSOLUTE_BAR_K * 1e3:.0f} mK "
              "clause alone")
    if failed:
        print(f"  VERDICT: FAILS at {len(failed)} averaging times -- "
              "over the ratio AND over the floor:")
        for a_tau, a_sig, ratio in failed:
            print(f"    tau {a_tau:8.0f} s   {a_sig * 1e3:7.2f} mK   {ratio:5.2f}x")
        print("           Look at the band table above for the period, and "
              "compare it to\n           2*pi*sqrt(tau*Ti/(Kp*K)) -- the loop's "
              "own natural period is where\n           integral action has to "
              "pay for the drift rejection it buys.")
    else:
        print("  VERDICT: MEETS the hold rule at every averaging time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
