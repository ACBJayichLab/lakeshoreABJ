"""Pull settled (heater, temperature) points out of the flattened fit tables.

A "hold" is a maximal run of rows inside one segment over which ``u_pct`` never
changes.  A hold is *settled* if, over its final window, the sample drift is
small compared with what the slow tail would still be doing.  The drift is
reported rather than thresholded away, because "how settled" is exactly the
number that decides whether a point may anchor the steady-state curve.
"""
from __future__ import annotations

import csv
import datetime as dt
import math

R_HEATER_OHM = 75.5
V_FULL_SCALE = 10.0          # 218 analog output full scale, volts

#: VOLTAGE gain between the commanded percent and what reaches the heater, so
#: the delivered power carries G**2 = 1.232.  It is a constant multiplier on
#: Q, which means it rescales Lambda and C by 1.232 and leaves every exponent,
#: every ratio and tau = C / Lambda' untouched.  It matters only where absolute
#: watts do.
ACTUATOR_GAIN = 1.11


def power_w(pct: float) -> float:
    v = ACTUATOR_GAIN * V_FULL_SCALE * pct / 100.0
    return v * v / R_HEATER_OHM


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                t = float(r["t_s"])
            except (TypeError, ValueError):
                continue
            rows.append((t, r))
    return rows


def _f(row, key):
    v = row.get(key, "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def _slope(ts, ys):
    """Least-squares K per hour."""
    n = len(ts)
    if n < 3:
        return math.nan
    mt = sum(ts) / n
    my = sum(ys) / n
    sxx = sum((t - mt) ** 2 for t in ts)
    if sxx <= 0:
        return math.nan
    sxy = sum((t - mt) * (y - my) for t, y in zip(ts, ys))
    return 3600.0 * sxy / sxx


def holds(rows, *, min_hold_s=1800.0, tail_s=1200.0, channels=()):
    """Every constant-heater run longer than ``min_hold_s``."""
    out = []
    run = []
    prev_u = None
    prev_seg = None
    for t, r in rows:
        u = _f(r, "u_pct")
        seg = r.get("segment")
        if math.isnan(u):
            continue
        if prev_u is None or abs(u - prev_u) > 1e-3 or seg != prev_seg:
            if run:
                out.append(run)
            run = []
        run.append((t, r))
        prev_u, prev_seg = u, seg
    if run:
        out.append(run)

    points = []
    for run in out:
        span = run[-1][0] - run[0][0]
        if span < min_hold_s:
            continue
        t_end = run[-1][0]
        tail = [(t, r) for t, r in run if t >= t_end - tail_s]
        if len(tail) < 5:
            continue
        ts = [t for t, _ in tail]
        samp = [_f(r, "Sample") for _, r in tail]
        if any(math.isnan(s) for s in samp):
            continue
        u = _f(run[0][1], "u_pct")
        rec = {
            "t_start": run[0][1].get("Timestamp", ""),
            "t_end": run[-1][1].get("Timestamp", ""),
            "hold_h": span / 3600.0,
            "u_pct": u,
            "P_W": power_w(u),
            "T_K": sum(samp) / len(samp),
            "drift_K_per_h": _slope(ts, samp),
            "rms_mK": 1000.0 * math.sqrt(
                sum((s - sum(samp) / len(samp)) ** 2 for s in samp) / len(samp)),
            "n": len(run),
        }
        for ch in channels:
            vals = [_f(r, ch) for _, r in tail]
            vals = [v for v in vals if not math.isnan(v)]
            rec[ch] = sum(vals) / len(vals) if vals else math.nan
        points.append(rec)
    return points


CHANNELS = ("Coldplate", "Magnet", "RAD SHIELD", "THE CHONKE", "1st Stage", "2nd Stage")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("tables", nargs="+")
    ap.add_argument("--min-hold-h", type=float, default=0.5)
    ap.add_argument("--tail-min", type=float, default=20.0)
    ap.add_argument("-o", "--out")
    a = ap.parse_args()

    pts = []
    for path in a.tables:
        got = holds(load(path), min_hold_s=3600 * a.min_hold_h,
                    tail_s=60 * a.tail_min, channels=CHANNELS)
        for g in got:
            g["source"] = path.replace("\\", "/").rsplit("/", 1)[-1]
        pts += got
    pts.sort(key=lambda p: p["u_pct"])

    hdr = ("source", "t_end", "hold_h", "u_pct", "P_W", "T_K",
           "drift_K_per_h", "rms_mK") + CHANNELS
    print(f"{len(pts)} holds")
    print(" ".join(f"{h:>13}" if h != "source" else f"{h:<28}" for h in hdr))
    for p in pts:
        cells = []
        for h in hdr:
            v = p[h]
            if h == "source":
                cells.append(f"{v:<28}")
            elif isinstance(v, str):
                cells.append(f"{v[5:19]:>13}")
            else:
                cells.append(f"{v:>13.4g}")
        print(" ".join(cells))

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(hdr) + ["t_start", "n"])
            w.writeheader()
            for p in pts:
                w.writerow({k: p[k] for k in w.fieldnames})
        print("wrote", a.out)
