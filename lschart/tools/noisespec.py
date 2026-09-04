"""Where a channel's noise lives in frequency -- and therefore what, if
anything, a low-pass filter can do about it.

The question this exists to answer is asked about once per cryostat: *there is
10-15 mK of jitter on the sample thermometer; would an RC filter on the sensor
leads help?*  It cannot be answered from an rms figure, because "10 mK rms" is
the same number whether the noise is broadband hash at 100 Hz -- which a
capacitor removes entirely -- or the room warming up and cooling down over a
day, which no filter reaches without also removing the measurement.

So this reports **four** things, and the fourth is the one that settles it:

``bands``
    rms in each octave-ish band, so the noise's home is visible rather than
    inferred.  Implemented with moving-average differences, not an FFT: a
    settled hold is never quite stationary and a periodogram of a drifting
    record puts most of its power in bin 1 and tells you nothing.

``decimate``
    how broadband the noise is *inside* the band.  Take every k-th sample with
    no averaging: that folds the octaves between the new Nyquist and the old
    one back on top of the survivors, so a broadband component holds its
    variance.  Block-*averaging* the same k samples removes those octaves
    instead.  The gap between the two curves is the content between the two
    Nyquists, measured rather than modelled.

    **The record's own Nyquist is a hard wall and nothing here sees past it.**
    A log at 4 s cannot say whether there is noise at 2 Hz; that noise aliased
    in before the first row was written.  Answering *that* needs either a
    faster record or an instrument put across the leads -- which is exactly why
    a filter chosen from a table like this one is a filter chosen blind.

``taus``
    the attenuation a single-pole low pass of time constant tau actually
    achieves, **measured by running the filter over the record**, next to the
    ``sqrt(dt / 2*tau)`` a white-noise model predicts.  Correlated noise makes
    the model wildly optimistic and the gap is the point.

``shared``
    the correlation between channels in a chosen band.  Two thermometers at
    different temperatures on different stages cannot share *sensor* noise; if
    they correlate, whatever they share is in the instrument, the harness or
    the ground, and it is common-mode.

None of it is Lake Shore-specific, and none of it is specific to the LTSPM3:
point it at any recorder CSV or any legacy ``.xls`` log.  The LTSPM3's own
answer, and what was concluded from it, is in ``docs/ltspm3/noise.md``.

Usage::

    python -m lschart.tools.noisespec "reference/logs/CD10/*sample_monitor3.xls"
    python -m lschart.tools.noisespec --from-csv "data/cd10/*.csv" -c ls218.sample
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import glob as _glob
import math
import sys

import numpy as np

#: Band edges as *periods* in seconds, fastest first.  The fastest band is
#: open-ended downwards -- it is everything up to the record's own Nyquist.
DEFAULT_PERIODS = (20.0, 120.0, 600.0, 3600.0, 6 * 3600.0)

#: Time constants swept by ``taus``.  Spans "far below anything a thermal
#: system does" to "comparable with the response's own pole", because the
#: interesting result is usually that nothing useful happens until the top end.
DEFAULT_TAUS = (0.1, 1.0, 3.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1200.0)


# -- loading ---------------------------------------------------------------


def load_csv(pattern: str) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Read recorder CSVs into ``(t_s, {channel: values})``.

    Files are concatenated in name order, which for the recorder's own
    ``{prefix}_{date}.csv`` layout is chronological.  Rows where a channel is
    blank are dropped from *every* channel, so the columns stay aligned -- a
    correlation between channels sampled at different instants is not a
    correlation.
    """
    paths = sorted(_glob.glob(pattern))
    if not paths:
        raise SystemExit(f"no CSV matched {pattern!r}")
    times: list[float] = []
    cols: dict[str, list[float]] = {}
    names: list[str] | None = None
    for path in paths:
        with open(path, newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if not header:
                continue
            skip = {"Timestamp", "Time", "Validity", "State", "Notes"}
            here = [c for c in header if c not in skip]
            if names is None:
                names = here
                cols = {c: [] for c in names}
            for row in reader:
                if len(row) != len(header):
                    continue
                d = dict(zip(header, row))
                try:
                    t = _dt.datetime.fromisoformat(d["Timestamp"]).timestamp()
                    vals = [float(d[c]) for c in names]
                except (KeyError, ValueError):
                    continue
                times.append(t)
                for c, v in zip(names, vals):
                    cols[c].append(v)
    if not times:
        raise SystemExit(f"{pattern!r} matched files but no usable rows")
    order = np.argsort(np.array(times))
    t = np.array(times)[order]
    return t - t[0], {c: np.array(v)[order] for c, v in cols.items() if v}


def load_xls(pattern: str) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Read legacy ``.xls`` chart-recorder logs, concatenated by start time."""
    from .import_xls import load_dir

    logs = [lg for lg in load_dir(pattern) if lg.started is not None]
    if not logs:
        raise SystemExit(f"no readable .xls matched {pattern!r}")
    logs.sort(key=lambda lg: lg.started)
    names = [c for c in logs[0].populated_channels]
    times: list[float] = []
    cols: dict[str, list[float]] = {c: [] for c in names}
    for lg in logs:
        base = lg.started.timestamp()
        for i, t in enumerate(lg.t_s):
            row = [lg.channels.get(c, [None] * len(lg.t_s))[i] for c in names]
            if any(v is None for v in row):
                continue
            times.append(base + t)
            for c, v in zip(names, row):
                cols[c].append(v)
    if not times:
        raise SystemExit(f"{pattern!r} matched logs but no complete rows")
    t = np.array(times)
    return t - t[0], {c: np.array(v) for c, v in cols.items()}


# -- the arithmetic --------------------------------------------------------


def detrend(v: np.ndarray) -> np.ndarray:
    """Remove the linear trend.  Drift is not noise, and a settled hold still
    drifts -- 0.5 mK/h over six hours is 3 mK of "rms" that is nothing of the
    kind."""
    x = np.arange(len(v), dtype=float)
    return v - np.polyval(np.polyfit(x, v, 1), x)


def quietest_window(v: np.ndarray, dt: float, hours: float = 6.0) -> np.ndarray:
    """The detrended window of ``hours`` with the smallest rms.

    Quietest rather than first, because a record almost always contains a
    settling tail or an excursion, and the *floor* is what a filter has to beat.
    """
    n = int(round(hours * 3600.0 / dt))
    if n < 16 or n > len(v):
        return detrend(v)
    best = min((v[i:i + n] for i in range(0, len(v) - n + 1, max(n // 4, 1))),
               key=lambda w: float(w.std()))
    return detrend(best)


def sample_jitter(v: np.ndarray) -> tuple[float, float, float]:
    """``(jitter, robust_jitter, lag1_rho)`` -- the sample-to-sample part.

    This is the number an operator actually points at: the reading changes by
    *this much* between consecutive samples when nothing has any business
    moving.  ``std(diff)/sqrt(2)`` isolates it without modelling the slow part
    at all, because a smooth signal contributes almost nothing to a first
    difference -- which is exactly why it beats a band-limited rms here, where
    the slow part is real thermal response rather than noise.

    The robust version uses the median absolute deviation of the differences,
    so one genuine step or one dropped sample does not inflate it.  They should
    agree; a robust value well below the plain one means the record contains
    real transients, not a higher noise floor.

    **A jitter that does not change when the polling rate changes is the
    signature of noise whose bandwidth exceeds the Nyquist of both rates** --
    i.e. of aliasing.  Band-limited noise would show consecutive samples
    growing *less* correlated, and the jitter rising, as the interval opens up.
    """
    d = np.diff(v)
    plain = float(np.std(d, ddof=1) / np.sqrt(2.0))
    mad = float(1.4826 * np.median(np.abs(d - np.median(d))) / np.sqrt(2.0))
    rho = float(np.corrcoef(v[:-1], v[1:])[0, 1]) if len(v) > 2 else float("nan")
    return plain, mad, rho


def _moving_average(v: np.ndarray, dt: float, period: float):
    """``(smoothed, window_points)``, or ``None`` if the window does not fit.

    A window longer than the record is not a slow band, it is an empty
    statement -- and ``np.convolve(mode="same")`` silently returns the *kernel*
    length when asked, which would make it an empty statement of the wrong
    shape.  Half the record is the longest window worth trusting.
    """
    n = max(3, int(round(period / dt)))
    if n > len(v) // 2:
        return None
    return np.convolve(v, np.ones(n) / n, mode="same"), n


def band_rms(v: np.ndarray, dt: float,
             periods: tuple[float, ...] = DEFAULT_PERIODS) -> list[tuple]:
    """rms in each band, as ``(label, period_lo, period_hi, rms)``.

    A band is the difference of two moving averages, and the edges are trimmed
    by the longer window so the convolution's tapering ends never enter the
    statistic -- they would read as a large slow component that is not there.
    """
    out = []
    for lo, hi in zip((None,) + periods, periods + (None,)):
        if lo is None:
            got = _moving_average(v, dt, hi)
            if got is None:
                continue
            base, n = got
            x = (v - base)[n:-n]
            label = f"< {hi:g} s"
        elif hi is None:
            got = _moving_average(v, dt, lo)
            if got is None:
                continue
            base, n = got
            x = base[n:-n]
            label = f"> {lo:g} s"
        else:
            a, b = _moving_average(v, dt, lo), _moving_average(v, dt, hi)
            if a is None or b is None:
                continue
            n = max(a[1], b[1])
            x = (a[0] - b[0])[n:-n]
            label = f"{lo:g}-{hi:g} s"
        if len(x) > 8:
            out.append((label, lo, hi, float(x.std(ddof=1))))
    return out


def decimation_test(v: np.ndarray, dt: float,
                    factors: tuple[int, ...] = (1, 2, 4, 8, 16)) -> list[tuple]:
    """``(k, eff_dt, rms_decimated, rms_averaged, implied_out_of_band)``.

    Decimating keeps every k-th sample and folds the octaves between the new
    Nyquist and the record's own back into the band; block-averaging the same k
    samples removes them.  Both are high-passed identically -- by *sample
    count*, not by time -- so the only difference between the columns is that
    content, and the last column is its rms.

    A decimated column that stays flat while the averaged one falls means the
    noise is broadband over the octaves being folded.  It says **nothing** about
    frequencies above the record's own Nyquist: those aliased in when the log
    was written and no amount of arithmetic afterwards separates them again.
    """
    def hp(x: np.ndarray, points: int = 24) -> np.ndarray:
        base = np.convolve(x, np.ones(points) / points, mode="same")
        return (x - base)[points:-points]

    out = []
    for k in factors:
        nb = len(v) // k
        if nb < 64:
            break
        dec = v[:nb * k:k]
        avg = v[:nb * k].reshape(nb, k).mean(axis=1)
        sd_dec = float(hp(dec).std(ddof=1))
        sd_avg = float(hp(avg).std(ddof=1))
        extra = math.sqrt(max(sd_dec ** 2 - sd_avg ** 2, 0.0))
        out.append((k, k * dt, sd_dec, sd_avg, extra))
    return out


def single_pole(v: np.ndarray, dt: float, tau: float) -> np.ndarray:
    """dt-aware exponential low pass -- the same coefficient the supervisor's
    ``ExponentialFilter`` uses, so a number measured here is a number the
    running control loop would actually see."""
    alpha = 1.0 - math.exp(-dt / tau)
    out = np.empty_like(v)
    acc = float(v[0])
    for i, x in enumerate(v):
        acc += alpha * (float(x) - acc)
        out[i] = acc
    return out


def filter_sweep(v: np.ndarray, dt: float,
                 taus: tuple[float, ...] = DEFAULT_TAUS) -> list[tuple]:
    """``(tau, rms_out, measured_attenuation, white_model_attenuation)``.

    ``None`` for a tau below the record's own cadence: a filter faster than the
    sampling cannot be evaluated from the samples, and pretending otherwise is
    how a 100 ms time constant gets justified from 8 s data.

    The white-noise model is ``sqrt(dt / (2*tau))`` -- the noise gain of a
    single pole fed uncorrelated samples.  It is printed alongside because the
    ratio between it and the measured column *is* the answer: near 1, the noise
    is white and filtering works; far above 1, the noise is correlated, the
    filter is fighting the signal band, and no time constant rescues it.
    """
    raw = float(v.std(ddof=1))
    out = []
    for tau in taus:
        if tau < dt / 2.0:
            out.append((tau, None, None, None))
            continue
        y = single_pole(v, dt, tau)
        prime = min(int(5 * tau / dt), len(y) // 2)
        y = y[prime:]
        if len(y) < 32:
            out.append((tau, None, None, None))
            continue
        sd = float(y.std(ddof=1))
        white = math.sqrt(dt / (2.0 * tau))
        out.append((tau, sd, sd / raw if raw else float("nan"), white))
    return out


def shared_matrix(cols: dict[str, np.ndarray], dt: float,
                  period: float = 120.0) -> tuple[list[str], np.ndarray]:
    """Correlation between channels, high-passed at ``period``.

    High-passed because every channel on a cryostat drifts with the cryostat and
    would correlate trivially over hours.  What is worth knowing is whether they
    move together on a timescale no thermal path between them could carry -- and
    that can only be electrical.
    """
    names = list(cols)
    n = min(len(v) for v in cols.values())
    rows = []
    for c in names:
        got = _moving_average(cols[c][:n], dt, period)
        if got is None:
            raise ValueError(f"{period:g} s does not fit in {n} samples")
        base, w = got
        rows.append((cols[c][:n] - base)[w:-w])
    m = min(len(r) for r in rows)
    return names, np.corrcoef([r[:m] for r in rows])


# -- reporting -------------------------------------------------------------


def report(t: np.ndarray, cols: dict[str, np.ndarray], channel: str,
           *, hours: float, out=sys.stdout) -> None:
    if channel not in cols:
        raise SystemExit(f"no channel {channel!r}; have {', '.join(cols)}")
    dt = float(np.median(np.diff(t)))
    v = cols[channel]
    w = quietest_window(v, dt, hours)
    step = np.diff(np.unique(np.round(v, 6)))
    step = step[step > 1e-9]
    p = lambda *a: print(*a, file=out)  # noqa: E731

    p(f"{channel}:  {len(v)} samples, {(t[-1] - t[0]) / 3600:.1f} h, "
      f"cadence {dt:.2f} s, mean {v.mean():.4f} K")
    p(f"  display quantum {np.median(step) * 1e3:.2f} mK"
      if len(step) else "  display quantum: unresolved")
    p(f"  quietest {hours:g} h window, detrended: rms {w.std(ddof=1) * 1e3:.2f} mK")
    jit, mad, rho = sample_jitter(w)
    p(f"  SAMPLE-TO-SAMPLE JITTER {jit * 1e3:.2f} mK "
      f"(robust {mad * 1e3:.2f}, lag-1 rho {rho:+.2f})")
    p(f"    -- the 218 makes ~{max(1, round(2.0 * dt)):d} readings per logged sample "
      f"at 2 rdg/s per input; averaging them would divide this by "
      f"~{max(1.0, (2.0 * dt) ** 0.5):.1f}x if it is white at the instrument's rate")

    p("\n  WHERE THE NOISE LIVES (rms by band)")
    for label, _lo, _hi, sd in band_rms(w, dt):
        p(f"    {label:>16s}  {sd * 1e3:8.2f} mK")

    p(f"\n  HOW BROADBAND, INSIDE THE BAND (this record sees to "
      f"{1 / (2 * dt):.3f} Hz and no further)")
    p(f"    {'k':>4s} {'eff dt':>9s} {'decimated':>12s} {'averaged':>12s}"
      f" {'the octaves between':>21s}")
    for k, eff, dec, avg, extra in decimation_test(w, dt):
        p(f"    {k:4d} {eff:8.0f}s {dec * 1e3:9.2f} mK {avg * 1e3:9.2f} mK"
          f" {extra * 1e3:18.2f} mK")
    p("    (decimated flat + averaged falling = broadband over those octaves)")

    p("\n  WHAT A SINGLE-POLE LOW PASS ACTUALLY BUYS")
    p(f"    {'tau':>8s} {'rms out':>12s} {'measured':>10s} {'if white':>10s}"
      f" {'model is off by':>17s}")
    for tau, sd, meas, white in filter_sweep(w, dt):
        if sd is None:
            p(f"    {tau:7.1f}s {'--':>12s} {'--':>10s} {'--':>10s}"
              f"   below this record's cadence")
            continue
        p(f"    {tau:7.1f}s {sd * 1e3:9.2f} mK {meas:9.2f}x {white:9.2f}x"
          f" {meas / white:16.1f}x")

    if len(cols) > 1:
        names, C = shared_matrix(cols, dt)
        p("\n  SHARED BETWEEN CHANNELS (high-passed at 120 s)")
        p("    " + " " * 14 + "".join(f"{n[:12]:>13s}" for n in names))
        for i, n in enumerate(names):
            p(f"    {n[:12]:>12s}  " + "".join(f"{C[i, j]:13.2f}"
                                               for j in range(len(names))))
        p("    (thermometers on different stages cannot share SENSOR noise)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m lschart.tools.noisespec",
        description="Where a channel's noise lives, and what a filter can do about it.",
    )
    ap.add_argument("pattern", help="glob of legacy .xls logs, or of CSVs with --from-csv")
    ap.add_argument("--from-csv", action="store_true",
                    help="read the recorder's own CSV instead of legacy .xls")
    ap.add_argument("-c", "--channel", default=None,
                    help="channel to analyse (default: the first one present)")
    ap.add_argument("--hours", type=float, default=6.0,
                    help="length of the quiet window to characterise (default 6)")
    args = ap.parse_args(argv)

    t, cols = (load_csv if args.from_csv else load_xls)(args.pattern)
    report(t, cols, args.channel or next(iter(cols)), hours=args.hours)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
