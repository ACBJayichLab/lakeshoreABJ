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

``ltspm3/model/fitted_response.py`` interpolates that table and integrates it.  The
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
import datetime as _dt
import sys

import numpy as np

sys.path.insert(0, "analysis")
import band  # noqa: E402
import fit_ode as F  # noqa: E402
import holdout as H  # noqa: E402
from plot_gain import N_CAP, N_DRIFT, N_LAM, coldplate_of  # noqa: E402

OUT = "ltspm3/model/_fitted_table.py"

#: Points on the log grid.  300 is a 0.35% spacing over 4.9-195 K, which the
#: --verify pass shows is four orders of magnitude finer than the fit's own
#: uncertainty.  It is also 120 lines, which is a readable file.
N_GRID = 300

#: The grid's ends.  The fit is defined over 0.95-1.05 times the sweep's range
#: and the sweep is 4.9-192.6 K, so this is the whole of where the model has
#: anything to say.  Outside it the interpolation clamps, deliberately: a
#: simulator that silently extrapolates a fitted curve is inventing a cryostat.
T_LO, T_HI = 4.7, 195.0


def measure_gauge(r):
    """``(fraction, window, n)`` -- the delivered-power gauge for the CURRENT epoch.

    The one number a reseated wire changes, and the reason this export needs it:
    the fit's Lambda is levelled across the whole campaign, and the cryostat has
    been opened since.  Measured on the most recent programmed ladder, whose
    rungs span 5-120 K in under five hours and so cannot themselves drift
    (REFIT_PLAN.md section 7.3).

    **It is a calibration with a shelf life, not a property of the cryostat.**
    Any work on the heater wiring expires it, which is why the generated header
    carries the number, the window and the date rather than quietly folding it
    in.
    """
    rungs = H.ladder_rungs()
    return H.gauge(r, rungs), H.LADDER, len(rungs)


def evaluate():
    """The production fit, sampled onto the grid."""
    rec, anchors, taus = F.production_inputs()
    top = float(rec.T.max())
    # The ramp is OFF in the shipped fit -- see fit_ode.PRODUCTION_CAMPAIGN.
    # What the curve does NOT carry is the delivered-power gauge of whichever
    # epoch it is read in; that is one number, and holdout.py --in-epoch
    # measures it.
    r = F.fit(N_LAM, N_CAP, rec, anchors, taus, n_drift=N_DRIFT,
              campaign=F.PRODUCTION_CAMPAIGN)
    rows = [x for x in F.load_rows()
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
    # In COMMANDED watts, which is what every consumer converts to percent.
    # The fit's own Lambda is in commanded watts for a level averaged over the
    # campaign; the gauge moves it to the epoch the cryostat is in now.  Divide,
    # do not multiply: g > 0 means the circuit delivers MORE than P(u) says
    # relative to the fit, so holding T takes LESS commanded power than the
    # ungauged curve claims.
    gauge, gauge_window, gauge_n = measure_gauge(r)
    Q = Q / (1.0 + gauge)
    if np.any(np.diff(Q) <= 0):
        raise SystemExit("Q(T) is not monotone -- it is inverted by "
                         "interpolation downstream, so this must not ship")
    if Q[0] <= 0:
        raise SystemExit(f"Q({T[0]:.2f} K) = {Q[0]:.3e} W is not positive -- "
                         "the grid starts at or below the coldplate")
    g = {
        "T": T,
        "q": Q,
        "gauge": gauge,
        "gauge_window": gauge_window,
        "gauge_n": gauge_n,
        "slope": r["lam"].slope(r["pl"], T),
        "cap": r["cap"](r["pc"], T),
        "tc": Tc,
    }
    # The band is measured AGAINST THE GRID, not against the fit -- its model
    # term is the residual `missing_power_w` will actually make, and that is
    # the grid's job as much as the fit's.  So it comes last, and it needs the
    # rest of `g` to have been built.
    g["band"] = band.measure(r, g)
    return r, g


def verify(r, g) -> None:
    """How much the grid costs, against the fit it was sampled from.

    Checked at the midpoints of the grid intervals -- the worst case for linear
    interpolation -- rather than at the knots, where it is exact by
    construction and proves nothing.
    """
    T = g["T"]
    mid = np.sqrt(T[:-1] * T[1:])                    # geometric midpoints

    def err(name, exact, table, floor=None):
        """Relative error, and where it is worth reading.

        REFIT_PLAN.md trap T9 asked why this reported 5e-2 against a docstring
        claiming a part in 10^5.  **It is the bottom two grid points and nothing
        else.**  ``q`` is Lambda(T) - Lambda(T_c), which goes to ZERO as the
        sample approaches its own heat sink, so a relative error there divides
        by a number the grid itself is driving to nothing: 3.8e-2 at 4.73 K
        where q = 0.13 mW, 3.8e-3 above 5 K, 6e-4 above 6 K and 6e-5 above 8 K.
        The docstring is right everywhere the quantity means anything.  So the
        absolute error is reported beside it, because that is what a consumer
        feels -- 45 uW at worst, over a heater that runs to 800 mW.
        """
        rel = np.abs(table / exact - 1.0)
        abs_err = np.abs(np.asarray(table) - np.asarray(exact))
        extra = ""
        if floor is not None:
            warm = np.asarray(exact) >= floor
            if warm.any():
                extra = f"   above {floor} : {rel[warm].max():.2e}"
        print(f"  {name:<10} max {rel.max():.2e}  rms "
              f"{np.sqrt((rel**2).mean()):.2e}   abs {abs_err.max():.2e}{extra}")

    def interp_log(x, xs, ys):
        return np.exp(np.interp(np.log(x), np.log(xs), np.log(ys)))

    # Gauged, because g["q"] is: comparing a gauged table against an ungauged
    # reference reports the gauge itself as an interpolation error, which is a
    # 0.9 % lie in the one row anybody reads.
    exact_q = ((r["lam"](r["pl"], mid)
                - r["lam"](r["pl"], np.interp(mid, T, g["tc"])))
               / (1.0 + g["gauge"]))
    err("Q", exact_q, interp_log(mid, T, g["q"]), floor=0.05)
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
#: It lives here rather than being pasted into ltspm3/model/_fitted_table.py because
#: that file is generated: a hand-edited warning is deleted by the next refit,
#: which is precisely the moment somebody is relying on it.
SUPERSEDED_NOTE = ""
#: CLEARED 2026-09-13, when the refit reconciled the table with the ladder that
#: had contradicted it.  REFIT_PLAN.md section 1, all three rows, with the level
#: gauged on the most recent ladder and the three long holds PREDICTED from it:
#: -0.25 / -0.08 / -0.01 K against a 0.3 K target, 0.139 K rms across 45 rungs
#: against 0.5 K, and 6.7 % worst on the relaxations a single pole can describe
#: against 10 %.
#:
#: The mechanism stays, and the empty string is load-bearing:
#: tests_ltspm3/test_fitted_table.py asserts in BOTH directions, so a stale
#: warning left in the generated header now fails the build exactly as a
#: dropped one did.
#:
#: What replaced it is not a caveat but a DATE.  The header carries the
#: delivered-power gauge, the window it was measured on and the day it was
#: measured, because the level is a calibration with a shelf life -- any work on
#: the heater wiring expires it, while the shape does not.  See
#: REFIT_PLAN.md section 7.3.


#: One block comment per band constant, because a bare float in a generated
#: file is a number nobody can check.  Each says what it is, what it is worth
#: and which module measured it; ``analysis/band.py``'s docstring is the long
#: form and this is the version that travels with the number.
_BAND_DOC = {
    "SIGMA_MODEL_K": (
        "What the model gets wrong INSIDE ONE EPOCH, in kelvin, at the anchors",
        "of the ladder it was gauged on plus the holds it then predicted.  This",
        "is REFIT_PLAN.md section 1 row 2 recomputed through THIS grid, so the",
        "band cannot claim the model is better than the scoreboard says.  In",
        "kelvin because that is the shape it has: as a fraction of the",
        "delivered power the same residual is 0.07 % at 118 K and 17 % at",
        "5.4 K.  Enters as SIGMA_MODEL_K * Lambda'(T_s).",
    ),
    "SIGMA_TINF_K": (
        "The median anchor error bar -- what a settled temperature is known to.",
    ),
    "DIURNAL_K": (
        "The building's 24 h cycle as an rms: the median amplitude over the",
        "holds that resolve a whole cycle, over sqrt(2).  Not a modelling error",
        "and no fit removes it.",
    ),
    "TC_RMS_K": (
        "The coldplate is not a bath.  What is left after bath.py fits it as a",
        "TAU_BATH_S pole on a monotone curve in heater power, over the whole",
        "43 h record -- excursion included, because a sweep looks like the",
        "excursion.  Enters as TC_RMS_K * Lambda'(T_c), and Lambda' at 6.6 K is",
        "nine times Lambda' at 118 K, which makes this the LARGEST term in a",
        "settled band at the warm end.",
    ),
    "TAU_BATH_S": (
        "The coldplate's own pole.  Not a term in the band: it is the monitor's",
        "dT_c locus check (PID_PLAN.md section 3).",
    ),
    "SIGMA_C_FRAC": (
        "How well C is known, from fitted tau against every measured one over",
        "40-120 K -- tau = C/Lambda' and Lambda' is pinned by a hundred settled",
        "anchors, so the tau spread IS C's error bar.  Enters only through",
        "SIGMA_C_FRAC * C * |dT/dt|, so it is invisible at a hold and is what",
        "widens the band during a sweep.  Quoted where a relaxation is graded",
        "and nowhere else; see analysis/band.py for why the same figure is 25 %",
        "over all 25 taus and why that is not a C error.",
    ),
    "DRIFT_W_PER_DAY": (
        "The campaign drift, drift.py: the median of three independent output",
        "bands spanning a factor of 1.6 in power.  Quoted at DRIFT_REF_W and",
        "applied as a FRACTION of the delivered power -- the cold end refuses a",
        "constant parasitic watt three ways (REFIT_PLAN.md section 7.2).",
        "At its FULL rate rather than its uncertainty, because the shipped fit",
        "carries no drift term and so nothing subtracts it; and symmetrically,",
        "because section 7.3 found a staircase of handling events rather than a",
        "rate, and a rate whose sign you trust is one you would subtract.",
    ),
    "DRIFT_SPREAD_W_PER_DAY": (
        "The three bands the median came from.  Reported, not used.",
    ),
    "DRIFT_REF_W": (
        "The power the drift is quoted at -- the middle of the three bands.",
    ),
    "DRIFT_T0": (
        "The day the level was gauged, and the band's t = 0.  Not the export",
        "date and not the fit's: the level is true at the moment it was",
        "measured, and the export can be re-run a month later on the same",
        "ladder.",
    ),
    "DELTA_P_FRAC": (
        "THE BIAS, AND IT IS NOT IN THE BAND.  How far the whole curve's level",
        "may sit from the truth because the heater circuit delivers a little",
        "more or less than P(u) says -- 4.7 mW at 118 K, which is 2.8 K.  It is",
        "CONSTANT over hours and days and changes when somebody handles the",
        "wiring.  Put it in the noise band and 3 sigma at 118 K is 14 mW, and",
        "the 2026-09-10 fault that has to warn was -4.9 mW.  Exported under its",
        "own name so the consumers that feel it can carry it: the monitor's",
        "absolute residual, the feedforward and the open-loop ramp-down.  The",
        "closed loop has integral action and never sees it.",
    ),
}


def _band_lines(b: dict) -> list:
    """The band constants, each under the comment that says what it is."""
    out = ["#: THE BAND.  Measured by analysis/band.py from the same archive the",
           "#: curves were fitted to, and frozen here so control/ and monitor.py",
           "#: read ONE source and cannot disagree about what typical means.",
           "#: PID_PLAN.md section 3; the long form is analysis/band.py.",
           "#:",
           "#:     sigma_Q^2 = (TC_RMS_K Lambda'(T_c))^2 + (SIGMA_TINF_K Lambda')^2",
           "#:               + (DIURNAL_K Lambda')^2 + (SIGMA_MODEL_K Lambda')^2",
           "#:               + (DRIFT_W_PER_DAY days P/DRIFT_REF_W)^2",
           "#:               + (SIGMA_C_FRAC C |dT/dt|)^2"]
    for name in ("SIGMA_MODEL_K", "SIGMA_TINF_K", "DIURNAL_K", "TC_RMS_K",
                 "TAU_BATH_S", "SIGMA_C_FRAC", "DRIFT_W_PER_DAY",
                 "DRIFT_SPREAD_W_PER_DAY", "DRIFT_REF_W", "DRIFT_T0",
                 "DELTA_P_FRAC"):
        out.append("")
        out.extend("#: " + line for line in _BAND_DOC[name])
        value = b[name]
        if name == "DRIFT_T0":
            out.append(f"DRIFT_T0 = {value!r}")
            out.append(f"DRIFT_T0_UNIX = {b['DRIFT_T0_UNIX']:.0f}")
        elif isinstance(value, tuple):
            out.append(f"{name} = {tuple(float(f'{x:.6g}') for x in value)!r}")
        else:
            out.append(f"{name} = {value:.6g}")
        if name == "SIGMA_MODEL_K":
            out.append(f"#: worst {1e3 * b['SIGMA_MODEL_MAX_K']:.0f} mK over "
                       f"{b['SIGMA_MODEL_N']} anchors")
        if name == "SIGMA_C_FRAC":
            out.append(f"#: over {b['SIGMA_C_N']} measured relaxations")
    return out


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
        "",
        "THE LEVEL IS A CALIBRATION AND IT HAS A SHELF LIFE.  `q` below is in",
        "COMMANDED watts, and how much of a commanded watt reaches the sample",
        "changes when the heater wiring is handled -- 0.8 % when a wire was",
        f"reseated on 2026-09-04, which is 3.2 K at 118 K.  Gauged "
        f"{100 * g['gauge']:+.3f} %",
        f"on {g['gauge_n']} rungs of {g['gauge_window']}, measured {stamp}.",
        "",
        "ANY WORK ON THE HEATER CIRCUIT EXPIRES THIS TABLE'S LEVEL.  Re-measure",
        "with `python analysis/holdout.py --in-epoch` and re-export.  The SHAPE",
        "-- tau(T), the local gain -- is unaffected and does not expire.",
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
        "#: WHICH FIT THIS IS.  fit_ode's cache key: the inputs, the knot counts,",
        "#: the priors and every anchor's date, hashed.  A schedule row or a",
        "#: pasted number that carries a different key was computed against a",
        "#: different cryostat -- PID_PLAN.md trap 'pastes rot'.",
        f"FIT_KEY = {r['key']!r}",
        "",
        "#: The delivered-power gauge applied to `q` above, and the window and day",
        "#: it was measured on.  ONE number, and the one a wire reseat changes.",
        f"GAUGE_FRAC = {g['gauge']:.6g}",
        f"GAUGE_WINDOW = {g['gauge_window']!r}",
        f"GAUGE_N = {g['gauge_n']}",
        "",
        *_band_lines(g["band"]),
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
    ap.add_argument("--dry-run", action="store_true",
                    help="measure and report, write nothing -- the shipped "
                         "table is what the simulator runs on")
    args = ap.parse_args()

    r, g = evaluate()
    print(f"fit: Lambda {N_LAM} knots, C {N_CAP}, drift {N_DRIFT} -- "
          f"rms {r['rms_k']:.3f} K, tau(137 K) {r['tau_137_s']:.0f} s")
    print(f"gauge: {100 * g['gauge']:+.3f} % of delivered power, from "
          f"{g['gauge_n']} rungs of {g['gauge_window']}")
    print(f"       holding 118 K takes {1e3 * float(np.interp(118.0, g['T'], g['q'])):.1f} mW "
          f"commanded, {1e3 * float(np.interp(118.0, g['T'], g['q'])) * g['gauge']:+.1f} mW "
          f"of which is the gauge")
    band.report(r, g)
    if args.verify:
        verify(r, g)
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    write(r, g, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
