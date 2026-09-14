"""How wrong the residual is allowed to be, measured rather than chosen.

PID_PLAN.md section 3: the monitor and the supervisor both judge the cryostat
by ONE number, the missing power::

    dQ = C(T_s) dT_s/dt + [Lambda(T_s) - Lambda(T_c)] - P(u)          [W]

and a residual without a band is not a judgement.  This module measures the
band's terms from the same archive the model was fitted to, and
``export_response.py`` freezes them into ``ltspm3/model/_fitted_table.py``
beside the curves, so ``control/`` and ``monitor.py`` read one source and
cannot disagree about what typical means.

**In watts, because every measured disturbance is a power** and the gain runs
0.35 to 13.3 K/% across the band.  A kelvin threshold is two different
thresholds at the two ends of this cryostat.

Two error bars, and keeping them apart is the whole design
----------------------------------------------------------

``sigma_q_w`` is a NOISE band: what ``dQ`` does over minutes to days while the
cryostat behaves.  ``bias_q_w`` is a CALIBRATION offset: how far the whole
curve's level may sit from the truth because the heater circuit delivers a
little more or less than ``P(u)`` says.  They are different quantities and
adding them together destroys the residual's only use.

* The bias is ``DELTA_P_FRAC`` = 0.7 % of the delivered power -- 4.7 mW at
  118 K, which is 2.8 K.  It is CONSTANT over hours and days.  It changes when
  somebody handles the heater wiring, and REFIT_PLAN.md section 7.2 measured
  three such events at a few tenths of an ohm in series with a 75.5 ohm heater.
* Put it in the noise band and 3 sigma at 118 K is 14 mW.  The 2026-09-10
  fault was **-4.9 mW** and phase 2's replay requires it to warn.  A band that
  carries a systematic which cannot change inside thirty minutes cannot see a
  fault that happens inside thirty minutes.

So the bias is exported under its own name and the band does not carry it.
**That is not the bias being ignored, it is the bias being put where it acts.**
The loop has integral action, so a constant power offset is absorbed and never
reaches the setpoint; the feedforward and the open-loop ramp-down carry it as a
terminal error of 0.7 % of full power, which at 5 K/min down to base is a few
kelvin of overshoot in an emergency descent and harms nothing.  The one
consumer that feels it is the monitor's ABSOLUTE residual, and the monitor's
answer is a trailing baseline, not a wider alarm (plan 2).

What each term is, and where the number comes from
--------------------------------------------------

=====================  =========================================================
``TC_RMS_K``           the coldplate is not a bath.  ``bath.py`` fits it as a
                       175 s pole on a monotone curve in heater power and what
                       is left is 27.5 mK rms over a 2.30 K swing.  Enters as
                       ``TC_RMS_K x Lambda'(T_c)``, and ``Lambda'`` at 6.6 K is
                       NINE TIMES ``Lambda'`` at 118 K -- so at the warm end
                       this term is the largest in the band and it is the SINK
                       that puts it there, not the sample.
``SIGMA_TINF_K``       the median anchor error bar, ``measure.py``.  What a
                       settled temperature is known to.
``DIURNAL_K``          the building's 24 h cycle as an rms, ``measure.py``'s
                       ``sigma_long`` -- the median amplitude over the holds
                       that resolve a whole cycle, divided by sqrt(2).  It is
                       NOT a modelling error and no fit removes it.
``SIGMA_MODEL_K``      what the model gets wrong INSIDE ONE EPOCH, in kelvin:
                       the residual at the gauged in-epoch anchors, which is
                       the ladder the level was gauged on plus the three long
                       holds it then predicted.  It is REFIT_PLAN.md section 1
                       row 2 recomputed through the shipped grid, so the band
                       cannot claim the model is better than the scoreboard
                       says.  **In kelvin, and that was measured the other way
                       round first**: as a fraction of the delivered power the
                       same residual is 0.07 % at 118 K and 17 % at 5.4 K,
                       where 8 mW of heater holds the sample.  Flat in kelvin
                       is what it actually is -- 0.11 K below 25 K, 0.18 K
                       above 40 K -- so it enters as
                       ``SIGMA_MODEL_K x Lambda'(T_s)``, the same shape as the
                       thermometry terms beside it.
``DRIFT_W_PER_DAY``    ``drift.py``: +0.281 mW/day, median of three independent
                       output bands spanning a factor of 1.6 in power, spread
                       0.223 to 0.335.  Quoted at ``DRIFT_REF_W`` and applied
                       as a FRACTION of the delivered power, for the same
                       reason.
``DRIFT_T0``           the day the level was gauged.  The band grows from
                       there, because that is when the level was last true.
``SIGMA_C_FRAC``       how well C is known: fitted tau against every measured
                       one over 40-120 K, 3.0 % rms, which is section 1's third
                       row read as a number rather than as a verdict.
                       ``tau = C/Lambda'`` and ``Lambda'`` is pinned by a
                       hundred settled anchors, so the tau spread IS C's error
                       bar.  Enters only through ``C x |dT/dt|``, so it is
                       invisible at a hold and is what widens the band during a
                       5 K/min sweep.
``TAU_BATH_S``         the coldplate pole, for the monitor's ``dT_c`` locus
                       check.  Not a term in ``sigma_q_w``.
=====================  =========================================================

The drift enters at its FULL measured rate rather than at its uncertainty,
which is the opposite of what PID_PLAN.md section 3 first wrote down.  The
reason is REFIT_PLAN.md section 7.3: the shipped fit carries no drift term, so
nothing subtracts the drift and the band must cover all of it.  And it enters
symmetrically even though the measured sign is positive, because section 7.3's
finding is that the "drift" is a staircase of handling events rather than a
rate -- a rate whose sign you trust is a rate you would have subtracted.

Run it::

    python analysis/band.py
"""
from __future__ import annotations

import datetime as _dt
import sys

import numpy as np

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402
import holdout as H  # noqa: E402
import segments as S  # noqa: E402

#: ``bath.py``'s answer, and it costs a least_squares over the 43 h sweep to
#: get.  Measured here rather than pasted: see invariant 9.
_BATH: tuple[float, float] | None = None

#: ``drift.py``'s three independent output bands, in watts per day at
#: ``fit_ode.CAMPAIGN_REF_W``.  Restated here rather than re-measured because
#: ``drift.py`` is a report and not a library -- its answer is three numbers a
#: person reads, and re-deriving them costs a second fit for no new information.
#: Re-run ``python analysis/drift.py`` after any archive export and update these
#: two lines if the median moves; ``band.py`` prints both so the check is one
#: command.
DRIFT_W_PER_DAY = 0.281e-3
DRIFT_SPREAD_W_PER_DAY = (0.223e-3, 0.335e-3)


def bath_pole() -> tuple[float, float]:
    """``(tau_bath_s, residual_rms_k)`` -- the coldplate as a driven pole.

    The rms is over the WHOLE 43 h record, excursion included, not over the two
    settled holds where it is 1.6 and 3.7 mK.  The band has to cover a sweep as
    well as a hold, and the excursion is what a sweep looks like.
    """
    global _BATH
    if _BATH is None:
        import bath
        b, d = bath.fit()
        _BATH = (float(b.tau_s), float(d["rms_k"]))
    return _BATH


def thermometry(rows=None) -> tuple[float, float]:
    """``(sigma_T_inf_k, diurnal_rms_k)`` -- the two kelvin terms, measured.

    The median anchor bar, and the long-term fluctuation ``measure.py`` gets
    from the holds that span a whole day.  The median in both cases and for the
    same reason: the amplitudes run 4 to 69 mK and one noisy prepython window
    must not set the error bar for three hundred anchors.
    """
    rows = rows if rows is not None else F.load_rows()
    graded = [r for r in rows if (r.get("grade") or "").strip()]
    sigma = np.array([float(r["sigma_T_inf"]) for r in graded])
    long = np.array([float(r["sigma_long"]) for r in graded
                     if str(r.get("sigma_long", "")).strip()])
    return float(np.median(sigma)), float(np.median(long))


def in_epoch_anchors(rows=None) -> list:
    """The ladder the level is gauged on, plus the holds it then predicts.

    ONE undisturbed epoch, which is the only window in the archive where the
    model's error can be read without a reseated wire in it.  The ladder is
    in-sample for exactly one parameter over 45 rungs and the three holds are
    predictions; see ``holdout.py --in-epoch``.
    """
    rows = rows or H.rows_by_id()
    out = list(H.ladder_rungs(rows=rows))
    have = {id(x) for x in out}
    for hid in H.HOLDS:
        row = rows.get(hid)
        if row is not None and id(row) not in have:
            out.append(row)
    return out


def residuals_through_grid(g: dict, rows: list) -> tuple:
    """``(dQ, P)`` at each anchor, computed THROUGH THE EXPORTED GRID.

    Deliberately not through the fit's own curves.  What this measures is the
    error ``model.missing_power_w`` will actually make, which includes whatever
    the grid costs on top of the fit -- and the grid is what ships.  The
    arithmetic below is ``fitted_response.conductance_w`` line for line, and
    that duplication is the point: if the two ever disagree, the number
    exported is not the number the model returns.
    """
    T = np.array([float(x["T_inf"]) for x in rows])
    Tc = np.array([float(x["Coldplate"]) for x in rows])
    P = np.array([float(x["P_W"]) for x in rows])
    log_T = np.log(g["T"])

    def log_interp(x, ys):
        return np.exp(np.interp(np.log(np.clip(x, g["T"][0], g["T"][-1])),
                                log_T, np.log(ys)))

    q = log_interp(T, g["q"])
    locus = np.interp(T, g["T"], g["tc"])
    mid = 0.5 * (locus + Tc)
    return q + log_interp(mid, g["slope"]) * (locus - Tc) - P, P


def model_k(g: dict, rows=None) -> tuple[float, float, int]:
    """``(rms_k, max_k, n)`` -- the in-epoch residual, IN KELVIN.

    In kelvin, and this was measured the other way round first.  Written as a
    fraction of the delivered power the same residual is 0.07 % at 118 K and
    **17 % at 5.4 K**, where 8 mW of heater holds the sample: the model's error
    is not proportional to the power, and a band built that way would be blind
    at the cold end and hysterical at the warm one.

    Flat in kelvin is what it actually is -- 0.107 K rms below 25 K, 0.183 K
    above 40 K -- so the band carries it as ``SIGMA_MODEL_K x Lambda'(T_s)``,
    which is 3.3 mW at 10 K and 0.22 mW at 118 K.  That is the same shape as
    the thermometry terms beside it, and for the same reason: an error in where
    the curve sits is an error in temperature, and ``Lambda'`` is what turns
    one into the other.

    The number it returns is REFIT_PLAN.md section 1 row 2, recomputed through
    the shipped grid rather than through the fit.  **That is deliberate**: the
    band's model term and the gate the refit had to pass are the same
    measurement, so the band cannot quietly claim the model is better than the
    scoreboard says it is.

    Anchors at zero output are dropped -- the monitor has no opinion below 28 %
    output, so an anchor with the heater off is not a case it grades.
    """
    rows = rows if rows is not None else in_epoch_anchors()
    dQ, P = residuals_through_grid(g, rows)
    T = np.array([float(x["T_inf"]) for x in rows])
    slope = np.exp(np.interp(np.log(T), np.log(g["T"]), np.log(g["slope"])))
    live = P > 1e-3
    k = dQ[live] / slope[live]
    return (float(np.sqrt(np.mean(k ** 2))), float(np.max(np.abs(k))),
            int(live.sum()))


def capacity_frac(r: dict, band=H.TAU_BAND_K) -> tuple[float, int]:
    """``(rms_frac, n)`` -- how well C is known, from the measured relaxations.

    ``tau = C/Lambda'``, and ``Lambda'`` is pinned by a hundred settled anchors
    while C is pinned by nothing else, so the spread of fitted tau against
    measured tau IS the error bar on C.  3.0 % rms over 40-120 K, which is
    REFIT_PLAN.md section 1's third row read as a number rather than as a
    verdict (6.7 % worst, 2.1 % median).

    **Quoted over the band where a relaxation is graded, and nowhere else.**
    Over all 25 measured taus the same figure is 25 % rms, and that is not a
    C error: below 30 K tau is under ten seconds and a 2 s cadence cannot
    measure it, above 120 K the two windows that disagree are a cooling
    excursion and 1.1 K of motion across 33 hours (``analysis/plot_tau.py``).
    It does not matter at either end -- C is millijoules per kelvin at the cold
    end, so the dynamic term is micro-watts there whatever its error bar.

    The alternative reading, model-free C against fitted C
    (``fit_ode.py --seed-only``), comes back 14 % rms over six coarse bins with
    one of them 32 % out.  It is the noisier of the two estimates of the same
    quantity, and the taus are the ones the refit graded.
    """
    tT, tV = F.load_taus()
    m = (tT >= band[0]) & (tT <= band[1])
    fitted = r["cap"](r["pc"], tT[m]) / r["lam"].slope(r["pl"], tT[m])
    return float(np.sqrt(np.mean((fitted / tV[m] - 1.0) ** 2))), int(m.sum())


def gauge_epoch(window: str = H.LADDER) -> float:
    """The middle of the window the level was gauged on, in unix seconds.

    The band's ``t = 0``.  Not the export date and not the fit's date: the
    level is true at the moment it was measured, and the export could be run a
    month later on the same ladder.
    """
    w = next(x for x in S.windows() if x.id == window)
    return 0.5 * (w.start + w.end)


def measure(r: dict, g: dict) -> dict:
    """Every band constant, in one dict, in SI.  What the exporter writes."""
    tau_bath_s, tc_rms_k = bath_pole()
    sigma_tinf_k, diurnal_k = thermometry()
    model_rms, model_max, model_n = model_k(g)
    cap_frac, cap_n = capacity_frac(r)
    t0 = gauge_epoch()
    return {
        "DELTA_P_FRAC": F.DELTA_P_FRAC,
        "SIGMA_MODEL_K": model_rms,
        "SIGMA_MODEL_MAX_K": model_max,
        "SIGMA_MODEL_N": model_n,
        "DRIFT_W_PER_DAY": DRIFT_W_PER_DAY,
        "DRIFT_SPREAD_W_PER_DAY": DRIFT_SPREAD_W_PER_DAY,
        "DRIFT_REF_W": F.CAMPAIGN_REF_W,
        "DRIFT_T0_UNIX": t0,
        "DRIFT_T0": _dt.datetime.utcfromtimestamp(t0).strftime("%Y-%m-%d"),
        "SIGMA_TINF_K": sigma_tinf_k,
        "DIURNAL_K": diurnal_k,
        "TC_RMS_K": tc_rms_k,
        "TAU_BATH_S": tau_bath_s,
        "SIGMA_C_FRAC": cap_frac,
        "SIGMA_C_N": cap_n,
    }


#: The matched-output pair either side of the 2026-09-10 fault -- 33 h at
#: 64.0155 % before it and 33 h at 64.0100 % after the reseat.  Two holds, the
#: same heater output, four days apart, and the only measurement in the archive
#: of what the reseat left behind.  HANDOFF-2026-09-13 names this pair as what
#: ``missing_power_w`` is to be written against, and this is that check.
FAULT_PAIR = ("pc-20260908-154814", "pc-20260910-144849")


def _residual_rows(g: dict, rows: list, label: str) -> np.ndarray:
    """One block of the gate table: dQ by temperature band, in mW and in K."""
    dQ, P = residuals_through_grid(g, rows)
    T = np.array([float(x["T_inf"]) for x in rows])
    slope = np.exp(np.interp(np.log(T), np.log(g["T"]), np.log(g["slope"])))
    live = (P > 1e-3) & (T >= g["T"][0]) & (T <= g["T"][-1])
    print(f"  {label}  ({int(live.sum())} of {len(rows)} in range, heater on)")
    for lo, hi, name in ((0.0, 25.0, "under 25 K"), (25.0, 40.0, "25-40 K"),
                         (40.0, 1e9, "over 40 K"), (0.0, 1e9, "all")):
        m = live & (T >= lo) & (T < hi)
        if not m.any():
            continue
        k = dQ[m] / slope[m]
        print(f"  {name:>14}{int(m.sum()):>5}"
              f"{1e3 * np.sqrt(np.mean(dQ[m] ** 2)):>10.3f}"
              f"{1e3 * np.abs(dQ[m]).max():>10.3f}"
              f"{np.sqrt(np.mean(k ** 2)):>9.3f}{np.abs(k).max():>9.3f}")
    return dQ[live & (T >= 40.0)]


def gate(g: dict, rows=None) -> int:
    """PID phase 1's exit gate on the residual, measured.  0 if it is met.

    ``plans/pid-1-model.md`` asks for ``missing_power_w`` under 1 mW at every
    settled anchor the fit was given.  Two things have to be said about that
    sentence before the number under it means anything, and both were measured
    rather than argued.

    **"Every anchor the fit was given" is 136 anchors over 57 days, and the
    level is gauged to ONE of them.**  REFIT_PLAN.md section 7.2: the campaign
    is a staircase of somebody handling the heater wiring, worth up to 0.8 % of
    delivered power per event, and the shipped table is levelled on the
    2026-09-05 ladder.  Scored against the whole campaign the residual is
    therefore mostly the dates -- 4.7 K rms over 40 K -- and scored inside the
    gauged epoch it is 0.13 K.  **The second number is the model's error.  The
    first is the history of the cryostat**, and no fit that ships one level can
    make it smaller.  This prints both, in that order, so the gap is visible
    rather than available for either side of an argument.

    **A gate in watts is a gate in kelvin divided by Lambda'.**  ``Lambda'``
    runs 24 mW/K at 10 K against 1.7 at 118 K, so 1 mW asks for 40 mK at the
    cold end and 0.6 K at the warm one -- fourteen times stricter exactly where
    the model is best.  In-epoch the gate is met above 40 K and missed below
    25 K, while in KELVIN the cold end is the better half (0.11 K against
    0.18 K).  The kelvin columns are the ones to read; the verdict returned is
    the gate as written, over 40 K, in-epoch.
    """
    rows = rows if rows is not None else [
        x for x in F.load_rows() if (x.get("grade") or "").strip()]
    print("\nthe residual at every settled anchor")
    print(f"  {'band':>14}{'n':>5}{'rms mW':>10}{'max mW':>10}"
          f"{'rms K':>9}{'max K':>9}")
    warm_epoch = _residual_rows(g, in_epoch_anchors(),
                                "\n  INSIDE THE GAUGED EPOCH -- the model's own error")
    _residual_rows(g, rows,
                   "\n  the whole campaign -- mostly the dates, not the model")
    worst_warm = float(np.abs(warm_epoch).max())
    verdict = "MET" if worst_warm < 1e-3 else "NOT MET"
    print(f"\n  the gate as written -- under 1 mW at every anchor, in-epoch, "
          f"over 40 K:\n  {1e3 * worst_warm:.3f} mW, {verdict}.  Below 25 K it "
          f"is missed in watts and met in\n  kelvin; see this function's "
          f"docstring for why those are the same statement.")

    by_id = {(x.get("id") or "").strip(): x for x in rows}
    pair = [by_id.get(i) for i in FAULT_PAIR]
    if all(pair):
        d, p = residuals_through_grid(g, pair)
        print("\nthe matched-output pair either side of the 2026-09-10 fault")
        for row, dq, pw in zip(pair, d, p):
            print(f"  {row['id']:>22}  {float(row['T_inf']):7.3f} K  "
                  f"{float(row['u_pct']):7.4f} %  dQ {1e3 * dq:+7.3f} mW")
        print(f"  the difference is {1e3 * (d[1] - d[0]):+.3f} mW at a matched "
              f"output, against the\n  -4.9 mW the fault itself measured.  "
              f"THE RESEAT DID NOT PUT IT BACK, and this is\n  the one number "
              f"in the archive that says by how much.")
    return 0 if worst_warm < 1e-3 else 1


def report(r=None, g=None) -> int:
    """The band, and what it is worth at three temperatures."""
    if r is None or g is None:
        import export_response as E
        r, g = E.evaluate()
    b = measure(r, g)

    print("the band's terms, measured")
    print(f"  TC_RMS_K          {1e3 * b['TC_RMS_K']:8.2f} mK    "
          f"coldplate, {b['TAU_BATH_S']:.0f} s pole, bath.py")
    print(f"  SIGMA_TINF_K      {1e3 * b['SIGMA_TINF_K']:8.2f} mK    "
          f"median anchor bar, measure.py")
    print(f"  DIURNAL_K         {1e3 * b['DIURNAL_K']:8.2f} mK    "
          f"the building's 24 h cycle, as an rms")
    print(f"  SIGMA_MODEL_K     {1e3 * b['SIGMA_MODEL_K']:8.2f} mK    "
          f"in-epoch residual over {b['SIGMA_MODEL_N']} anchors, "
          f"worst {1e3 * b['SIGMA_MODEL_MAX_K']:.0f} mK")
    print(f"  DRIFT_W_PER_DAY   {1e3 * b['DRIFT_W_PER_DAY']:8.3f} mW/day "
          f"at {1e3 * b['DRIFT_REF_W']:.0f} mW, from {b['DRIFT_T0']}")
    print(f"  SIGMA_C_FRAC      {100 * b['SIGMA_C_FRAC']:8.3f} %     "
          f"fitted tau against {b['SIGMA_C_N']} measured ones, 40-120 K")
    print(f"  DELTA_P_FRAC      {100 * b['DELTA_P_FRAC']:8.3f} %     "
          f"THE BIAS -- deliberately not in the band above")

    print("\nwhat that is worth, 3 sigma")
    print(f"  {'T K':>6}{'P mW':>9}{'sink':>8}{'therm':>8}{'model':>8}"
          f"{'day 0':>9}{'day 10':>9}{'5 K/min':>9}{'bias mW':>10}"
          f"{'day 0 K':>9}")
    for T in (10.0, 30.0, 60.0, 118.0, 180.0):
        q = float(np.exp(np.interp(np.log(T), np.log(g["T"]), np.log(g["q"]))))
        slope = float(np.exp(np.interp(np.log(T), np.log(g["T"]),
                                       np.log(g["slope"]))))
        cap = float(np.exp(np.interp(np.log(T), np.log(g["T"]),
                                     np.log(g["cap"]))))
        tc = float(np.interp(T, g["T"], g["tc"]))
        slope_c = float(np.exp(np.interp(np.log(tc), np.log(g["T"]),
                                         np.log(g["slope"]))))
        sink = b["TC_RMS_K"] * slope_c
        therm = np.hypot(b["SIGMA_TINF_K"], b["DIURNAL_K"]) * slope
        model = b["SIGMA_MODEL_K"] * slope
        rate = b["DRIFT_W_PER_DAY"] * q / b["DRIFT_REF_W"]
        dyn = b["SIGMA_C_FRAC"] * cap * (5.0 / 60.0)
        base = sink ** 2 + therm ** 2 + model ** 2
        day0 = 3.0 * float(np.sqrt(base))
        day10 = 3.0 * float(np.sqrt(base + (10.0 * rate) ** 2))
        sweep = 3.0 * float(np.sqrt(base + dyn ** 2))
        print(f"  {T:>6.0f}{1e3 * q:>9.1f}{1e3 * sink:>8.2f}{1e3 * therm:>8.2f}"
              f"{1e3 * model:>8.2f}{1e3 * day0:>9.2f}{1e3 * day10:>9.2f}"
              f"{1e3 * sweep:>9.2f}{1e3 * b['DELTA_P_FRAC'] * q:>10.2f}"
              f"{day0 / slope:>9.2f}")
    print("  sink/therm/model are 1 sigma; the three wide columns are 3 sigma "
          "of all of them\n  together -- settled at day 0, settled ten days "
          "later, and sweeping at 5 K/min\n  on the day it was gauged.")
    print("  The last column is the day-0 band in kelvin at the local gain.  "
          "Jeff asked for a\n  warning at a kelvin and the measured band "
          "arrives at one from the other end.")
    return gate(g)


if __name__ == "__main__":
    raise SystemExit(report())
