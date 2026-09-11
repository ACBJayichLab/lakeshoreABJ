"""Where to put the rungs of a characterisation sweep, and what each will cost.

The 43 h sweep this model is fitted to covered 4.9-192.6 K, but it was walked
by hand and it dwelt where somebody was watching.  Panel (a) of
``plot_gain.py`` shows the result: a dense cluster from 63% up, a handful of
points below 56%, and **between 38 K and 100 K almost nothing** -- which is
exactly the band where the local gain goes from 4 K/% to 13 K/% and where a
controller therefore has to know what it is doing.

This picks the rungs to fill that in, and it does it from the fit rather than
from a guess, because two of the three things a plan needs are things only the
fit knows:

``u(T)``
    the output that holds a temperature.  Parameterised on temperature, so no
    root-finding: ``Q(T) = Lambda(T) - Lambda(T_c(T))`` and
    ``u = 100 sqrt(Q R)/(G V_fs)``, exactly invertible.  ``T_c`` is
    interpolated from the settled dwells rather than assumed constant -- the
    coldplate runs 4.7 K cold and 6.9 K at 180 K, and it is measured.
``tau(T) = C(T) / Lambda'(T)``
    what the dwell will cost.  It spans three orders of magnitude over this
    range, which is why one dwell length for the whole ladder is either a waste
    of an afternoon at the cold end or a dataset of unusable points at the warm
    one.

The third thing -- the stop rule -- is ``steps.py``'s grader, and the dwell
predicted here is what that rule implies for a step of the size this ladder
takes::

    span >= tau * ln(dT / max_settle_k)             extrapolation small enough
    span >= tau * ln(3600 dT / (tau * max_end_rate)) still moving slowly enough
    span >= min_reach * tau                          tau itself believable

``ltspm3/tools/sweep.py`` reads the CSV this writes and runs the ladder, and it
re-applies that same rule to the dwell as it actually happens, so a rung whose
tau the model has wrong ends when the cryostat says so and not when this file
predicted it would.

Usage::

    python analysis/plan_sweep.py                       # 30 rungs, 5.3-110 K
    python analysis/plan_sweep.py --n 24 --hi 90
    python analysis/plan_sweep.py --space power -o /tmp/plan.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import sys

import numpy as np

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402
from plot_gain import N_CAP, N_DRIFT, N_LAM, coldplate_of  # noqa: E402

#: The grader's bars, from steps.py.  Repeated as defaults rather than imported
#: so that changing one here cannot silently change what the fitter keeps.
MIN_REACH = 3.0
MAX_SETTLE_K = 2.0
MAX_END_RATE_K_PER_H = 0.5

#: Aim for this fraction of each bar rather than for the bar.  See `dwell_for`:
#: a plan with no margin loses the rungs whose tau the model has slightly low,
#: and those are the expensive ones at the top of the ladder.
AIM = 0.5


def model(hi_k: float = 190.0, n: int = 4000):
    """``(T, u, tau, dTdu)`` along the fitted steady state.

    The production fit -- the one ``plot_gain.py`` draws -- on the adaptively
    decimated sweep, cached, so this costs seconds after the first run.
    """
    rec, anchors, taus = F.production_inputs()
    top = float(rec.T.max())
    # groups=True, as export_response.py and plot_gain.py: plan the next ladder
    # against the cryostat as it is now, not against a July-September average.
    r = F.fit(N_LAM, N_CAP, rec, anchors, taus, n_drift=N_DRIFT, groups=True)
    rows = [x for x in F.load_rows()
            if x.get("grade") and float(x["T_inf"]) <= top]
    tc_of, _, _ = coldplate_of(rows)

    T = np.geomspace(4.9, hi_k, n)
    Tc = np.clip(tc_of(T), 1.0, None)
    Q = r["lam"](r["pl"], T) - r["lam"](r["pl"], Tc)
    # Stop where the evidence stops, not where Q > 0: as the sample approaches
    # its own heat sink both Q and dQ/dT go to zero together and the curve
    # grows a spike that is arithmetic rather than cryostat.
    floor = float(np.nanmin([float(v["P_W"]) for v in rows]))
    ok = Q >= floor
    T, Tc, Q = T[ok], Tc[ok], Q[ok]
    u = 100.0 * np.sqrt(Q * F.R_OHM) / (F.GAIN * F.V_FS)
    dQdT = (r["lam"].slope(r["pl"], T)
            - r["lam"].slope(r["pl"], Tc) * tc_of.derivative()(T))
    dudT = (50.0 / (F.GAIN * F.V_FS)) * np.sqrt(F.R_OHM / Q) * dQdT
    tau = r["cap"](r["pc"], T) / r["lam"].slope(r["pl"], T)
    return r, T, u, tau, 1.0 / dudT, Q


def targets(lo: float, hi: float, n: int, space: str, T, u, Q):
    """The temperatures to sit at.

    ``log`` by default.  Every curve here is fitted in ``(log T, log y)`` and
    the physics is power-law-ish over this range, so equal *ratios* is equal
    information; equal kelvin spends two thirds of the ladder above 40 K, where
    the old sweep already has points.
    """
    if space == "log":
        return np.geomspace(lo, hi, n)
    if space == "linear":
        return np.linspace(lo, hi, n)
    if space == "power":
        # Equal steps in heater power -- equal steps in what the operator is
        # actually turning, and the only spacing that is uniform in the
        # quantity the DAC resolves.
        Qs = np.interp([lo, hi], T, Q)
        return np.interp(np.linspace(Qs[0], Qs[1], n), Q, T)
    if space == "u":
        us = np.interp([lo, hi], T, u)
        return np.interp(np.linspace(us[0], us[1], n), u, T)
    raise SystemExit(f"unknown spacing {space!r}")


def dwell_for(d_t: float, tau: float, *, min_reach: float, max_settle_k: float,
              max_end_rate: float, min_s: float, max_s: float,
              aim: float = AIM) -> float:
    """How long the grader will need this rung to be held.

    All three of its tests are the same exponential seen from different sides,
    so each becomes a multiple of tau and the longest wins.  The end-rate test
    is almost always the one that does: it is the strictest by roughly
    ``ln(3600 * max_settle / (tau * max_end_rate))``, about two further time
    constants at 110 K.

    ``aim`` is why this does not size for the bar itself.  Sizing a dwell to
    land exactly on 0.5 K/h means every rung whose tau the model has slightly
    low comes back at 0.51 and is thrown away -- which is what a rehearsal
    against the fitted plant does, four times out of thirty, on a plan with no
    margin at all.  Half the bar costs ``tau ln 2`` per rung, about half an hour
    across a 30-rung ladder, and it is the cheapest half hour in the campaign.
    """
    d_t = abs(d_t)
    if tau <= 0 or d_t <= 0:
        return min_s
    settle_target = max_settle_k * aim
    rate_target = max_end_rate * aim
    need = min_reach * tau
    if d_t > settle_target:
        need = max(need, tau * math.log(d_t / settle_target))
    rate0 = 3600.0 * d_t / tau
    if rate0 > rate_target:
        need = max(need, tau * math.log(rate0 / rate_target))
    return min(max_s, max(min_s, need))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=30, help="how many rungs")
    ap.add_argument("--lo", type=float, default=5.3, help="coldest rung, K")
    ap.add_argument("--hi", type=float, default=110.0, help="warmest rung, K")
    ap.add_argument("--space", default="log", choices=("log", "linear", "power", "u"))
    ap.add_argument("--start-k", type=float, default=None,
                    help="where the cryostat is now, for the first rung's step "
                         "size (default: the first rung is free)")
    ap.add_argument("--min-reach", type=float, default=MIN_REACH)
    ap.add_argument("--max-settle-k", type=float, default=MAX_SETTLE_K)
    ap.add_argument("--max-end-rate", type=float, default=MAX_END_RATE_K_PER_H)
    ap.add_argument("--min-dwell", type=float, default=120.0)
    ap.add_argument("--max-dwell", type=float, default=3600.0)
    ap.add_argument("--aim", type=float, default=AIM,
                    help="fraction of each grading bar to size the dwell for; "
                         "1.0 sizes for the bar itself and loses every rung the "
                         "model was slightly optimistic about")
    ap.add_argument("-o", "--out", default="analysis/sweep_plan.csv")
    args = ap.parse_args()

    r, T, u, tau, dTdu, Q = model(hi_k=max(190.0, args.hi))
    if args.hi > T.max() or args.lo < T.min():
        print(f"note: the model covers {T.min():.2f}-{T.max():.1f} K; "
              f"the ladder is being clipped to it", file=sys.stderr)
    lo = max(args.lo, float(T.min()))
    hi = min(args.hi, float(T.max()))

    tgt = targets(lo, hi, args.n, args.space, T, u, Q)
    U = np.interp(tgt, T, u)
    TAU = np.interp(tgt, T, tau)
    GAIN = np.interp(tgt, T, dTdu)

    print(f"model: Lambda {N_LAM} knots, C {N_CAP}, drift {N_DRIFT} -- "
          f"sweep rms {r['rms_k']:.3f} K, tau(137 K) {r['tau_137_s']:.0f} s")
    print(f"{'#':>3}{'u %':>9}{'T K':>8}{'dT':>7}{'K/%':>7}{'tau s':>8}"
          f"{'dwell s':>9}{'cum h':>8}")

    rows = []
    prev = args.start_k
    cum = 0.0
    for i, (Tq, uq, tq, gq) in enumerate(zip(tgt, U, TAU, GAIN), 1):
        d_t = abs(Tq - prev) if prev is not None else float(Tq - lo)
        dwell = dwell_for(d_t, float(tq), min_reach=args.min_reach,
                          max_settle_k=args.max_settle_k,
                          max_end_rate=args.max_end_rate,
                          min_s=args.min_dwell, max_s=args.max_dwell,
                          aim=args.aim)
        cum += dwell
        rows.append({"u_pct": f"{uq:.3f}", "T_pred_k": f"{Tq:.3f}",
                     "d_t_k": f"{d_t:.3f}", "gain_k_per_pct": f"{gq:.3f}",
                     "tau_pred_s": f"{tq:.1f}", "dwell_pred_s": f"{dwell:.0f}"})
        print(f"{i:>3}{uq:>9.3f}{Tq:>8.2f}{d_t:>7.2f}{gq:>7.2f}{tq:>8.0f}"
              f"{dwell:>9.0f}{cum / 3600.0:>8.2f}")
        prev = float(Tq)

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"\n{len(rows)} rungs, {cum / 3600.0:.2f} h if every dwell runs to "
          f"its predicted length.  Wrote {args.out}")
    print("\nthe same ladder as bare percents, for --percents:")
    print("  " + ",".join(f"{x:.3f}" for x in U))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
