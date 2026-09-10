"""Freeze the fitted model into a table `ltspm3` can read with the stdlib.

The simulator's ``ResponseParams`` is a two-pole lag onto a power law, with ONE
time constant -- 620 s, inferred from a single step at 137 K -- and a steady
state from the CD10 percent-to-kelvin table.  Both halves are now superseded:
the fitted ODE has tau running from under a second at 10 K to about 490 s at
110 K, and a steady state that is 17 K different from the old curve in the
middle of the range.  Rehearsing a sweep against the old response therefore
tests the procedure against a cryostat that does not exist.

The fit itself cannot ship.  It needs scipy, the 43 h sweep and a couple of
minutes, and ``ltspm3`` needs none of those and must keep needing none of them.
So the fit is evaluated here, once, and written out as a table of the four
curves the ODE is made of::

    C(T) dT/dt = Q(u) - [Lambda(T) - Lambda(T_c)]

``ltspm3/fitted_response.py`` interpolates that table and integrates it.  The
generated module is checked in -- it is small, it is the only way a fresh clone
gets a calibrated simulator without installing scipy, and it carries its own
provenance so a stale one is visible rather than merely wrong.

Why a table and not the knots
-----------------------------

The knots are the parameters of a monotone cubic in ``(log T, log y)``, and
reproducing them means shipping PchipInterpolator's construction as well.  A
log-log grid does not: the curves are smooth and monotone by construction, so
linear interpolation between 120 points spaced by 0.9% in T is exact to about
a part in 10^5 -- checked, not assumed, by ``--verify``.

Usage::

    python analysis/export_response.py                  # rewrite the table
    python analysis/export_response.py --verify         # and check the error
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import sys

import numpy as np

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402
from plot_gain import N_CAP, N_DRIFT, N_LAM, coldplate_of  # noqa: E402

OUT = "ltspm3/_fitted_table.py"

#: Points on the log grid.  300 is a 0.35% spacing over 4.9-195 K, which the
#: --verify pass shows is four orders of magnitude finer than the fit's own
#: uncertainty.  It is also 120 lines, which is a readable file.
N_GRID = 300

#: The grid's ends.  The fit is defined over 0.95-1.05 times the sweep's range
#: and the sweep is 4.9-192.6 K, so this is the whole of where the model has
#: anything to say.  Outside it the interpolation clamps, deliberately: a
#: simulator that silently extrapolates a fitted curve is inventing a cryostat.
T_LO, T_HI = 4.7, 195.0


def evaluate():
    """The production fit, sampled onto the grid."""
    data, w = F.load_decimated()
    top = float(data[1].max())
    # groups=, like plot_gain.py: the July-August anchors get a free power
    # offset so the curve THIS EXPORTS describes the cryostat's present state
    # rather than splitting the difference with a state it left in August.
    r = F.fit(N_LAM, N_CAP, data, F.load_anchors(t_max=top), F.load_taus(t_max=top),
              weights=w, n_drift=N_DRIFT, groups=F.anchor_groups(t_max=top))
    rows = [x for x in csv.DictReader(open(F.ANCHORS, newline="", encoding="utf-8"))
            if x.get("grade") and float(x["T_inf"]) <= top]
    tc_of, _, _ = coldplate_of(rows)

    T = np.geomspace(T_LO, T_HI, N_GRID)
    Tc = np.clip(tc_of(T), 1.0, None)
    # Q, not Lambda.  Lambda is 2.38 W at the cold end and the difference that
    # matters there is 8 mW, so reconstructing Q by subtracting two interpolated
    # Lambdas throws away five significant figures before it starts -- and
    # clamps the T_c term at the bottom of the grid, which is worse: it made
    # every cold steady state 0.2 K too warm.  Q is exported evaluated, once.
    Q = r["lam"](r["pl"], T) - r["lam"](r["pl"], Tc)
    if np.any(np.diff(Q) <= 0):
        raise SystemExit("Q(T) is not monotone -- it is inverted by "
                         "interpolation downstream, so this must not ship")
    if Q[0] <= 0:
        raise SystemExit(f"Q({T[0]:.2f} K) = {Q[0]:.3e} W is not positive -- "
                         "the grid starts at or below the coldplate")
    return r, {
        "T": T,
        "q": Q,
        "slope": r["lam"].slope(r["pl"], T),
        "cap": r["cap"](r["pc"], T),
        "tc": Tc,
    }


def verify(r, g) -> None:
    """How much the grid costs, against the fit it was sampled from.

    Checked at the midpoints of the grid intervals -- the worst case for linear
    interpolation -- rather than at the knots, where it is exact by
    construction and proves nothing.
    """
    T = g["T"]
    mid = np.sqrt(T[:-1] * T[1:])                    # geometric midpoints

    def err(name, exact, table):
        rel = np.abs(table / exact - 1.0)
        print(f"  {name:<10} max {rel.max():.2e}  rms {np.sqrt((rel**2).mean()):.2e}")

    def interp_log(x, xs, ys):
        return np.exp(np.interp(np.log(x), np.log(xs), np.log(ys)))

    exact_q = (r["lam"](r["pl"], mid)
               - r["lam"](r["pl"], np.interp(mid, T, g["tc"])))
    err("Q", exact_q, interp_log(mid, T, g["q"]))
    err("Lambda'", r["lam"].slope(r["pl"], mid), interp_log(mid, T, g["slope"]))
    err("C", r["cap"](r["pc"], mid), interp_log(mid, T, g["cap"]))
    err("T_c", np.interp(mid, T, g["tc"]), interp_log(mid, T, g["tc"]))

    tau = g["cap"] / g["slope"]
    print(f"  tau: {tau.min():.2f} s at {T[0]:.1f} K -> {tau.max():.0f} s at "
          f"{T[-1]:.0f} K")


#: Emitted verbatim into the generated header; empty string when there is
#: nothing to say.
#:
#: The provenance block below records what the fit was fitted TO.  It cannot
#: record what has contradicted it SINCE, and on 2026-09-05 something did.  That
#: finding lived in HANDOFF.md, two docs, analysis/README.md and _data.py -- and
#: in none of the files that ship the table, so a `--simulate` rehearsal or a
#: re-plan next month inherited the error with nothing on screen to say so.
#:
#: It lives here rather than being pasted into ltspm3/_fitted_table.py because
#: that file is generated: a hand-edited warning is deleted by the next refit,
#: which is precisely the moment somebody is relying on it.
SUPERSEDED_NOTE = """\
KNOWN LOW, and not yet refitted.  The programmed ladder of 2026-09-05 measured
the steady state LOW BY UP TO 4.5 K across 40-98 K -- 25 times the rms residual
quoted above -- through the band where the 43 h sweep left no settled point at
all and this fit was therefore interpolating.  tau came back within 3% from
77 K to 114 K, so the dynamics travelled and the steady state did not.

Both `ltspm3.tools.sweep --simulate` and analysis/plan_sweep.py read this table
and inherit the error.  Invariant 9: where a measured number contradicts the
model, the number wins.

CLEAR SUPERSEDED_NOTE in analysis/export_response.py when a refit reconciles
them.  HANDOFF.md has the command order; tests_ltspm3/test_fitted_table.py
checks that this warning is actually present in the generated file."""


def write(r, g, path: str) -> None:
    stamp = _dt.date.today().isoformat()
    lines = [
        '"""The fitted thermal model, frozen onto a grid.  GENERATED -- do not edit.',
        "",
        "    python analysis/export_response.py",
        "",
        f"Fitted {stamp} to {F.SWEEP}",
        f"(43 h, 4.9-192.6 K) with Lambda on {N_LAM} knots, C on {N_CAP}, and a",
        f"{N_DRIFT}-knot slow drift in the steady state.  Sweep residual",
        f"{r['rms_k']:.3f} K rms, {r['max_k']:.2f} K max; tau(137 K) = "
        f"{r['tau_137_s']:.0f} s;",
        f"implied mass {r['mass_g']:.2f} g of the Cu/sapphire/diamond mix.",
        *(["", *SUPERSEDED_NOTE.split("\n")] if SUPERSEDED_NOTE else []),
        "",
        "Columns, one row per grid point:",
        "",
        "    T        K       sample temperature",
        "    q        W       Lambda(T) - Lambda(T_c), the power that holds T.",
        "                     Exported evaluated rather than as Lambda: at the cold",
        "                     end Lambda is 2.38 W and this difference is 8 mW.",
        "    slope    W/K     Lambda'(T) -- so tau = C/Lambda' needs no differencing",
        "    cap      J/K     C(T), the heat capacity",
        "    tc       K       the coldplate, interpolated from the settled dwells",
        '"""',
        "",
        "#: The actuator chain, this rig's: the 218's full scale, the 1.11 voltage",
        "#: gain in front of the heater, and the heater itself.  P = (G V u/100)^2 / R.",
        f"R_OHM = {F.R_OHM}",
        f"V_FS = {F.V_FS}",
        f"GAIN = {F.GAIN}",
        "",
        "#: Outside this the table clamps rather than extrapolating.",
        f"T_MIN_K = {g['T'][0]:.6g}",
        f"T_MAX_K = {g['T'][-1]:.6g}",
        "",
        "#: ``(T, lam, slope, cap, tc)``",
        "TABLE = (",
    ]
    for T, q, slope, cap, tc in zip(g["T"], g["q"], g["slope"], g["cap"], g["tc"]):
        lines.append(f"    ({T:.6g}, {q:.8g}, {slope:.8g}, {cap:.8g}, {tc:.6g}),")
    lines.append(")")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"wrote {path}: {len(g['T'])} rows, "
          f"{g['T'][0]:.2f}-{g['T'][-1]:.0f} K")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--verify", action="store_true",
                    help="report the interpolation error at the grid midpoints")
    args = ap.parse_args()

    r, g = evaluate()
    print(f"fit: Lambda {N_LAM} knots, C {N_CAP}, drift {N_DRIFT} -- "
          f"rms {r['rms_k']:.3f} K, tau(137 K) {r['tau_137_s']:.0f} s")
    if args.verify:
        verify(r, g)
    write(r, g, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
