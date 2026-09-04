"""Fit every constant-heater dwell as a single-pole relaxation.

The earlier extractor took the mean of a hold's last 20 minutes and demanded
the hold be an hour long.  That is the right test when tau is ten minutes and
the wrong one everywhere else, and it threw away most of the sweep: below
100 K tau is seconds, so a three-minute dwell is already tens of tau and is
settled to well under the sensor noise.

Fitting the dwell instead of averaging it gives three things where averaging
gave one:

``T_inf``
    the steady state the dwell was heading for, extrapolated rather than
    approximated by wherever it happened to have got to.  This is a point on
    ``Lambda`` exactly as a long hold is.
``tau_s``
    the local time constant, measured directly.  With ``Lambda`` known this
    turns C into a measurement -- ``C = tau * dLambda/dT`` -- instead of the
    barely-constrained fit parameter it is when only the trajectory sees it.
``reach``
    ``span / tau``, how many time constants the dwell actually ran.  A fit
    over less than about 3 tau returns tau far too small at an R^2 that still
    reads as healthy (see docs/ltspm3/commissioning.md), so this is the column
    that decides whether a tau may be believed, and R^2 is not.

The fit is ``T(t) = T_inf + A exp(-t/tau)``: nonlinear in tau alone, so tau is
found by 1-D search with (T_inf, A) solved exactly at each trial.  That has no
starting-guess failure mode, which matters across 500 dwells nobody will look
at individually.
"""
from __future__ import annotations

import csv
import math

import numpy as np
from scipy.optimize import minimize_scalar

R_OHM, V_FS, GAIN = 75.5, 10.0, 1.11

#: Sensor noise, from docs/ltspm3/thermal-response.md: quadratic in T,
#: floored near 1.8 mK.  Used to decide whether a dwell HAS a transient worth
#: fitting and whether its residual is at the measurement limit.
NOISE_FLOOR_K = 0.0018
NOISE_QUADRATIC = 1.36e-6

#: A dwell must run this many time constants before its tau is believed.
MIN_REACH = 3.0
#: ...and its transient must be this many times the sensor noise, or there is
#: nothing to fit a time constant to and only T_inf survives.
#:
#: It has to be a big number, not a marginal one.  A 70 s dwell at 154 K with a
#: 0.19 K transient -- six sigma, reach 3.5 -- reported tau = 20 s where every
#: neighbour says 600 s: with a window far shorter than the real tau, the fit
#: has nothing but noise to work with and obligingly fits it.  reach alone
#: cannot catch that, because reach is computed from the tau being tested.
MIN_AMPLITUDE_SIGMA = 20.0
#: A "steady" point extrapolates from where the dwell ended to T_inf.  Past
#: this much extrapolation it is a prediction of the model being fitted, not a
#: measurement of the cryostat.
MAX_SETTLE_K = 2.0

TIME_KEYS = ("t_s", "Time")
HEATER_KEYS = ("u_pct", "ls218.aout1", "heater_pct")

#: How far the reported heater may wander before a dwell is deemed to have
#: ended, in percent.
#:
#: NOT the DAC resolution.  The 218's AOUT? readback flickers between adjacent
#: codes -- 52.496 / 52.499 alternating sample to sample -- so an exact test,
#: or one at the 0.001% resolution, shreds a twenty-minute dwell into a
#: hundred one-sample runs and every dwell below 100 K disappears.  The
#: smallest deliberate step anywhere in this data is 0.1%, so 0.02% separates
#: flicker from intent with a factor of five in hand either way.
U_TOL_PCT = 0.02


def noise_k(T):
    return max(NOISE_FLOOR_K, NOISE_QUADRATIC * T * T)


def power_w(u):
    return (GAIN * V_FS * u / 100.0) ** 2 / R_OHM


def _pick(header, keys):
    for k in keys:
        if k in header:
            return k
    return None


def load(path):
    """(t, T_sample, T_coldplate, u, segment, timestamps) from any recorder-shaped CSV."""
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    if not rows:
        return None
    tk = _pick(rows[0], TIME_KEYS)
    uk = _pick(rows[0], HEATER_KEYS)
    if tk is None or uk is None:
        raise SystemExit(f"{path}: no time or heater column")

    def col(key, cast=float):
        out = []
        for r in rows:
            try:
                out.append(cast(r[key]))
            except (TypeError, ValueError, KeyError):
                out.append(math.nan)
        return np.array(out)

    seg = (col("segment") if "segment" in rows[0]
           else np.zeros(len(rows)))
    t, T, Tc, u = col(tk), col("Sample"), col("Coldplate"), col(uk)
    ok = ~(np.isnan(t) | np.isnan(T) | np.isnan(u))
    stamps = [r.get("Timestamp", "") for r in rows]
    return (t[ok], T[ok], Tc[ok], u[ok], seg[ok],
            [s for s, k in zip(stamps, ok) if k])


def dwells(t, T, Tc, u, seg, stamps, min_span_s=60.0, min_n=15):
    """Maximal runs of constant u inside one segment; see U_TOL_PCT."""
    out = []
    start = 0
    for i in range(1, len(t) + 1):
        end = i == len(t)
        if not end and abs(u[i] - u[start]) <= U_TOL_PCT and seg[i] == seg[start]:
            continue
        n = i - start
        if n >= min_n and t[i - 1] - t[start] >= min_span_s:
            out.append((start, i))
        start = i
    return out


def fit_pole(t, y):
    """T(t) = T_inf + A exp(-t/tau).  Returns (T_inf, A, tau, rms)."""
    t = t - t[0]
    span = t[-1]

    def solve(tau):
        e = np.exp(-t / tau)
        M = np.column_stack([np.ones_like(e), e])
        coef, *_ = np.linalg.lstsq(M, y, rcond=None)
        return coef, float(np.sqrt(np.mean((M @ coef - y) ** 2)))

    # tau anywhere from a couple of samples to several times the dwell: the
    # upper end is deliberately loose so a dwell that has NOT settled says so
    # by returning a tau longer than itself, instead of being clipped into
    # looking settled
    lo, hi = max(2.0 * np.median(np.diff(t)), 1.0), 20.0 * span
    r = minimize_scalar(lambda lt: solve(math.exp(lt))[1],
                        bounds=(math.log(lo), math.log(hi)), method="bounded")
    tau = math.exp(r.x)
    coef, rms = solve(tau)
    return float(coef[0]), float(coef[1]), tau, rms


def analyse(path, label=None):
    t, T, Tc, u, seg, stamps = load(path)
    rows = []
    for a, b in dwells(t, T, Tc, u, seg, stamps):
        tt, yy = t[a:b], T[a:b]
        span = tt[-1] - tt[0]
        T_inf, A, tau, rms = fit_pole(tt, yy)
        sigma = noise_k(float(np.mean(yy)))
        reach = span / tau
        rows.append({
            "source": label or path.replace("\\", "/").rsplit("/", 1)[-1],
            "t_end": stamps[b - 1][:19],
            "span_s": span,
            "n": b - a,
            "u_pct": float(np.mean(u[a:b])),
            "P_W": power_w(float(np.mean(u[a:b]))),
            "T_inf": T_inf,
            "T_end": float(yy[-1]),
            "settle_K": T_inf - float(yy[-1]),   # how far it still had to go
            "tau_s": tau,
            "reach": reach,
            "amp_K": abs(A),
            "amp_sigma": abs(A) / sigma,
            "rms_K": rms,
            "rms_sigma": rms / sigma,
            "Coldplate": float(np.nanmean(Tc[a:b])),
        })
    return rows


#: A dwell that barely moved is settled whatever its reach says -- reach is
#: meaningless when there is no transient to time, because tau is then fitted
#: to noise.  Above this the 3-tau rule applies to T_inf exactly as it does to
#: tau: a dwell cut off two time constants into a large relaxation returns a
#: T_inf that is short by more than the extrapolation admits.  Both outliers
#: this caught had reach ~2 and transients of 5 K and 18 K, and sat 12 K and
#: 15 K off a curve every settled neighbour agrees with.
QUIET_AMPLITUDE_K = 0.5


def grade(r):
    """'tau' if the time constant may be believed, 'steady' if only T_inf, else ''."""
    if abs(r["settle_K"]) > MAX_SETTLE_K:
        return ""
    if r["reach"] < MIN_REACH and r["amp_K"] >= QUIET_AMPLITUDE_K:
        return ""
    if (r["reach"] >= MIN_REACH and r["amp_sigma"] >= MIN_AMPLITUDE_SIGMA
            and r["rms_sigma"] < 8.0):
        return "tau"
    return "steady"


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("-o", "--out", default="analysis/steps.csv")
    a = ap.parse_args()

    # A region export is a slice of the log it was exported from, so passing
    # both the sweep and fit_recorder.csv finds every dwell twice.  Dwell
    # boundaries are set by the heater, which is the same in both, so the end
    # timestamp identifies a dwell across files.
    rows, seen = [], set()
    for p in a.paths:
        for r in analyse(p):
            key = (r["t_end"], round(r["u_pct"], 2))
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
    for r in rows:
        r["grade"] = grade(r)
    rows.sort(key=lambda r: r["T_inf"])

    keep = [r for r in rows if r["grade"]]
    taus = [r for r in rows if r["grade"] == "tau"]
    print(f"{len(rows)} dwells -> {len(keep)} usable steady points, "
          f"{len(taus)} with a believable tau\n")
    print(f"{'T_inf':>8}{'u%':>8}{'P W':>8}{'tau s':>9}{'reach':>7}"
          f"{'span s':>8}{'amp K':>8}{'rms mK':>8}{'settle K':>9}  grade")
    for r in rows:
        if not r["grade"]:
            continue
        print(f"{r['T_inf']:>8.2f}{r['u_pct']:>8.3f}{r['P_W']:>8.4f}"
              f"{r['tau_s']:>9.1f}{r['reach']:>7.1f}{r['span_s']:>8.0f}"
              f"{r['amp_K']:>8.2f}{1e3 * r['rms_K']:>8.1f}{r['settle_K']:>9.3f}"
              f"  {r['grade']}")

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\nwrote", a.out)
