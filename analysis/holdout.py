"""Step 8's gate: predict the holds the fit was never shown, and score section 1.

REFIT_PLAN.md Phase B step 8 turns the campaign drift on, and the question it
has to answer is not "does the fit look better" -- a free parameter always
makes a fit look better.  It is:

    **Drop every anchor after the cutover, refit, and PREDICT the three
    post-recalibration holds.  Landing them inside 0.5 K having never seen
    them means the drift has been measured rather than fitted.**

That is the only test here that the plan calls a gate, and the plan says do not
proceed if it fails.  Everything else this module prints is context for reading
it: section 1's three targets, which are the definition of done, and the
objective profiled against the campaign slope, which is what says whether the
slope is determined by the data or merely permitted by it.

Two conventions, both deliberate.

**A hold's miss is computed by INVERTING Lambda, not by dividing the power
residual by the local slope.**  ``plot_residuals`` does the latter, on purpose:
it is the objective's own arithmetic and nothing can hide behind an
interpolation.  Here the question is where the model says the cryostat WOULD
have sat, which is a root -- ``Lambda(T) - Lambda(T_c) = Q + campaign(t)`` --
and at 4 K of miss the linearisation is worth 0.1 K.

**Positive means the model is LOW**, as everywhere else in this repository: the
cryostat sat warmer than the model puts it.

Usage::

    python analysis/holdout.py                 # the scoreboard, then the gate
    python analysis/holdout.py --profile       # cost against a pinned slope
    python analysis/holdout.py --shapes        # what the leftover looks like
    python analysis/holdout.py --postcal       # with the second record
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402
import segments as _seg  # noqa: E402

#: The production preset, imported rather than restated so this cannot drift
#: from what ``plot_gain`` and ``export_response`` actually fit.
from plot_gain import N_CAP, N_DRIFT, N_LAM  # noqa: E402

#: The three long settled holds of REFIT_PLAN.md section 2.1 -- 11.5 h, 69.9 h
#: and 24.5 h, each settled to under 1.4 mK/h, and the row section 1 grades.
HOLDS = ("pc-20260904-233855", "pc-20260905-165509", "pc-20260908-154814")

#: The programmed ladder section 1's second row grades.  As a manifest window:
#: the rungs are whichever graded dwells fall inside it, which is a question
#: the archive answers rather than a count copied into a constant.
LADDER = "trace-ladder-20260905"

#: Leave-one-epoch-out: every anchor after this goes.  It is the Coldplate
#: recalibration, 2026-09-04 12:07 -- the last moment before the three holds,
#: and a boundary the data already has for an independent reason.
CUTOVER = "2026-09-04T12:07:00"

#: Section 1's targets.
TARGET_HOLD_K = 0.3
TARGET_LADDER_K = 0.5
TARGET_TAU_FRAC = 0.10
#: The band section 1's tau row is quoted over.
TAU_BAND_K = (40.0, 120.0)
#: The gate's own bar, from step 8.
GATE_K = 0.5


def invert(r, T_c, q_w):
    """Solve ``Lambda(T) - Lambda(T_c) = q_w`` for T, on the fitted curve.

    Lambda is monotone by construction, so this is an interpolation of its
    inverse and needs no root finder.  The grid is fine enough that its own
    error is microkelvin -- checked by halving it.
    """
    lam, pl = r["lam"], r["pl"]
    grid = np.geomspace(3.0, 260.0, 40000)
    L = lam(pl, grid)
    base = lam(pl, np.asarray(T_c, float))
    return np.interp(np.asarray(q_w, float) + base, L, grid)


def rows_by_id(path=F.ANCHORS):
    return {(x.get("id") or x.get("source") or "").strip(): x
            for x in F.load_rows(path)}


def hold_table(r, ids=HOLDS, rows=None):
    """One row per named hold: measured, modelled, and the miss in kelvin."""
    rows = rows or rows_by_id()
    out = []
    for i in ids:
        row = rows[i]
        T = float(row["T_inf"])
        Tc = float(row["Coldplate"])
        Q = float(row["P_W"])
        when = F.anchor_epoch(row)
        camp = float(F.campaign_power_w(r, np.array([when]), np.array([Q]))[0])
        model = float(invert(r, Tc, Q + camp))
        out.append({"id": i, "T": T, "Tc": Tc, "Q": Q, "when": when,
                    "campaign_w": camp, "model": model, "miss_k": T - model})
    return out


def ladder_rungs(window=LADDER, rows=None):
    """The graded dwells inside the programmed ladder, by the manifest's clock."""
    rows = rows or rows_by_id()
    w = next(x for x in _seg.windows() if x.id == window)
    return [x for x in rows.values()
            if x.get("grade") and w.start <= F.anchor_epoch(x) <= w.end]


def ladder_miss(r, rungs=None):
    """``(rms_k, max_k, n)`` for the model against the ladder's own rungs."""
    rungs = rungs if rungs is not None else ladder_rungs()
    T = np.array([float(x["T_inf"]) for x in rungs])
    Tc = np.array([float(x["Coldplate"]) for x in rungs])
    Q = np.array([float(x["P_W"]) for x in rungs])
    when = np.array([F.anchor_epoch(x) for x in rungs])
    model = invert(r, Tc, Q + F.campaign_power_w(r, when, Q))
    d = T - model
    return float(np.sqrt(np.mean(d ** 2))), float(np.max(np.abs(d))), len(d)


def tau_miss(r, taus=None, band=TAU_BAND_K):
    """``(worst_frac, median_frac, n)`` -- fitted tau against every measured one.

    Both, because section 1's row says EVERY measured tau and section 2.4's
    table -- the one that reports the row as passing -- is a smooth comparison
    at seven temperatures.  A single dwell's tau scatters 433 to 850 s near
    137 K, so the worst of eleven and the median of eleven are different
    questions and the row is only meaningful with both in front of it.
    """
    tT, tV = taus if taus is not None else F.load_taus()
    m = (tT >= band[0]) & (tT <= band[1])
    if not m.any():
        return float("nan"), float("nan"), 0
    fitted = (r["cap"](r["pc"], tT[m]) / r["lam"].slope(r["pl"], tT[m]))
    rel = np.abs(fitted / tV[m] - 1.0)
    return float(np.max(rel)), float(np.median(rel)), int(m.sum())


def scoreboard(r, rows=None, taus=None) -> bool:
    """REFIT_PLAN.md section 1's three rows, measured.  True if all three pass.

    Section 1 is the definition of done and its "where it stands today" column
    has been quoted from three different fits over the life of this plan, which
    is how a definition of done stops defining anything.  This prints it from
    the fit in front of it, with the target beside each row.
    """
    holds = hold_table(r, rows=rows)
    mean = float(np.mean([h["miss_k"] for h in holds]))
    worst = max(abs(h["miss_k"]) for h in holds)
    lr, lmax, ln = ladder_miss(r)
    tf, tmed, tn = tau_miss(r, taus)
    ok = (worst < TARGET_HOLD_K, lr < TARGET_LADDER_K, tf < TARGET_TAU_FRAC)

    def mark(good):
        return "PASS" if good else "FAIL"

    three = "  ".join(f"{h['miss_k']:+.2f}" for h in holds)
    ladder = f"{lr:.3f} K rms, {lmax:.2f} max"
    worst_tau = f"{100 * tf:.1f} % worst, {100 * tmed:.1f} % median"
    print("REFIT_PLAN.md section 1 -- the definition of done")
    print(f"  {'row':<34}{'target':>12}{'measured':>26}  verdict")
    print(f"  {'the three long settled holds':<34}{'< 0.3 K':>12}{three:>26}"
          f"  {mark(ok[0])}   mean {mean:+.2f} K")
    print(f"  {'the 2026-09-05 ladder':<34}{'< 0.5 K rms':>12}{ladder:>26}"
          f"  {mark(ok[1])}   {ln} rungs")
    print(f"  {'every measured tau, 40-120 K':<34}{'< 10 %':>12}{worst_tau:>26}"
          f"  {mark(ok[2])}   {tn} taus")
    return all(ok)


def describe(r) -> None:
    """The fit itself, in one line, plus whatever ramp it carries."""
    print(f"  Lambda {r['n_lam']} knots, C {r['n_cap']}, drift {r['n_drift']} "
          f"knots, {r['n_anchor']} anchors: cost {r['cost']:.2f}, rms "
          f"{r['rms_k']:.4f} K, anchor {r['anchor_k']:.4f} K, "
          f"tau(137) {r['tau_137_s']:.1f} s, nfev {r['nfev']}")
    if r["campaign"]:
        ref = dt.datetime.fromtimestamp(r["campaign_t_ref"])
        print(f"  campaign {1e3 * r['campaign_w_per_day']:+.4f} mW/day "
              f"({r['campaign_form']}, quoted at "
              f"{1e3 * F.CAMPAIGN_REF_W:.0f} mW), zero at {ref:%Y-%m-%d %H:%M}"
              f"{'  [PINNED]' if r['campaign_pinned'] else ''}")
    if r["n_drift"]:
        print("  per-record wander "
              + "; ".join(f"{n}: " + " ".join(f"{1e3 * v:+.2f}" for v in row)
                          for n, row in zip(r["records"], r["drift_w"]))
              + " mW")


def inputs(postcal=False):
    """``(records, anchors, taus)`` -- production, plus the second record if asked.

    ``trace-postcal-20260905`` is REFIT_PLAN.md step 7's record and section
    7.1's fit B.  Loaded here rather than read from a committed decimated table
    because that is step 10's job; it costs about twenty seconds.
    """
    rec, anchors, taus = F.production_inputs()
    if not postcal:
        return rec, anchors, taus
    return [rec, F.load_trace_record(F.POSTCAL)], anchors, taus


def production(campaign=True, postcal=False, **kw):
    rec, anchors, taus = inputs(postcal)
    return F.fit(N_LAM, N_CAP, rec, anchors, taus, n_drift=N_DRIFT,
                 campaign=campaign, **kw), anchors, taus


def gate(cutover=CUTOVER, campaign=True, postcal=False):
    """Leave-one-epoch-out, and the verdict step 8 is not allowed to skip.

    Every anchor dated after ``cutover`` is dropped and the fit is re-run; the
    three holds are then PREDICTED.  The sweep record needs no attention: it
    ends 2026-09-04 11:00, an hour before the cutover, so the held-out fit sees
    no sample of the epoch it is being asked about.  **With ``postcal`` that
    stops being true** -- the second record IS that epoch -- so the run says so
    rather than printing a verdict that has been shown its answer.

    What this actually tests is the SLOPE.  The holds sit 1 to 7 days past the
    last anchor the held-out fit can see, and at the rate the warm bands
    measure that is 1 to 2 K of drift at 118 K -- so a slope that is 40 % wrong
    misses the gate.  A fit with no ramp at all cannot pass it except by
    accident, which is the point of setting the bar where step 8 sets it.
    """
    rec, anchors, taus = inputs(postcal)
    when = _seg._stamp(cutover)
    keep = ~(anchors.t_abs > when)
    held = anchors.select(keep)
    print(f"\nleave-one-epoch-out at {cutover}: "
          f"{len(anchors)} anchors -> {len(held)}, "
          f"{int(np.sum(~keep))} dropped")
    if postcal:
        print("  NOT A HELD-OUT TEST: the post-recal record is in the fit,")
        print("  so the epoch being predicted is one the model has been shown.")
    r = F.fit(N_LAM, N_CAP, rec, held, taus, n_drift=N_DRIFT, campaign=campaign)
    describe(r)
    table = hold_table(r)
    print(f"  {'hold':<22}{'measured':>10}{'predicted':>11}{'miss K':>9}"
          f"{'ramp mW':>10}  (never seen by this fit)")
    for h in table:
        print(f"  {h['id']:<22}{h['T']:>10.3f}{h['model']:>11.3f}"
              f"{h['miss_k']:>+9.3f}{1e3 * h['campaign_w']:>+10.3f}")
    worst = max(abs(h["miss_k"]) for h in table)
    ok = worst < GATE_K
    print(f"  worst {worst:.3f} K against a {GATE_K:.1f} K bar -- "
          f"{'PASS' if ok else 'FAIL'}")
    return ok, r, table


def profile(slopes=(0.0, 0.05, 0.1, 0.2, 0.281, 0.4), campaign=True,
            postcal=False):
    """The objective with the slope PINNED, so its shape can be read.

    A fitted slope is not evidence on its own: it is where the optimum is, and
    an optimum in a flat direction means the data had no opinion.  Pinning the
    slope and refitting everything else traces the profile, and the free fit is
    printed beside it as the check that the two agree.
    """
    rec, anchors, taus = inputs(postcal)
    print(f"\nthe objective against the campaign slope "
          f"({F.CAMPAIGN_FORM!r}, mW/day at {1e3 * F.CAMPAIGN_REF_W:.0f} mW)")
    print(f"  {'mW/day':>8}{'cost':>10}{'rms_k':>8}{'anchor_k':>10}"
          f"{'nfev':>6}   the three holds, K low")
    for s in list(slopes) + [None]:
        kw = ({"campaign": campaign} if s is None
              else {"campaign_w": s * 1e-3})
        try:
            r = F.fit(N_LAM, N_CAP, rec, anchors, taus, n_drift=N_DRIFT, **kw)
        except (OverflowError, ValueError) as exc:
            print(f"  {s:>8.3f}   the integrator failed: "
                  f"{type(exc).__name__}: {exc}")
            continue
        tag = (f"free -> {1e3 * r['campaign_w_per_day']:.3f}" if s is None
               else f"{s:.3f}")
        miss = "  ".join(f"{h['miss_k']:+.3f}" for h in hold_table(r))
        print(f"  {tag:>8}{r['cost']:>10.2f}{r['rms_k']:>8.4f}"
              f"{r['anchor_k']:>10.4f}{r['nfev']:>6}   {miss}")


def leftover(r, anchors, records):
    """``(left, bar, days, after, era)`` -- what the fit did not explain.

    ``left`` is the anchor's own miss in watts with the fitted ramp taken back
    off, so it is what a further term would have to describe.  The anchors are
    trimmed the way ``fit`` trims them (trap T4), or the arrays would not line
    up with the fit that produced them.
    """
    a, _ = F.trim_anchors(F.Anchors.from_tuple(anchors), F.as_records(records))
    lam, pl = r["lam"], r["pl"]
    miss = lam(pl, a.T) - lam(pl, a.Tc) - a.Q
    left = miss - F.campaign_power_w(r, a.t_abs, a.Q)
    bar = np.hypot(lam.slope(pl, a.T) * a.sigma, F.DELTA_P_FRAC * np.abs(a.Q))
    cut = _seg._stamp(CUTOVER)
    return left, bar, (a.t_abs - cut) / 86400.0, a.t_abs > cut, np.array(a.era)


def shapes(campaign=True, postcal=False):
    """What shape describes the leftover: a slope in date, or a step at the cutover?

    REFIT_PLAN.md trap T7, and §7.2's evidence for it.  Weighted least squares
    by each anchor's own bar -- the objective's own weighting -- because an
    unweighted regression here is dominated by the 120-160 K anchors, which are
    both the widest-barred and the most numerous.

    Run over everything and then over the pre-cutover half ALONE, which is the
    test that matters: a slope that only exists across the calibration boundary
    is a step wearing a slope's clothes, and the only way to tell is to look
    where there is no boundary.
    """
    r, anchors, _ = production(campaign=campaign, postcal=postcal)
    describe(r)
    left, bar, days, after, era = leftover(r, anchors, inputs(postcal)[0])

    def table(label, m):
        w = 1.0 / bar[m]
        y, d, af = left[m], days[m], after[m].astype(float)
        cols = {"a constant": [np.ones(int(m.sum()))],
                "a slope in date": [np.ones(int(m.sum())), d]}
        if af.any() and not af.all():
            cols["a step at the cutover"] = [np.ones(int(m.sum())), af]
            cols["both"] = [np.ones(int(m.sum())), d, af]
        print(f"\n  {label}: {int(m.sum())} anchors, day {d.min():+.1f} to "
              f"{d.max():+.1f} either side of {CUTOVER[:10]}")
        print(f"  {'shape':<26}{'par':>4}{'chi2/n':>9}{'rms mW':>9}"
              f"   constant / slope per day / step")
        res0 = y
        print(f"  {'nothing':<26}{0:>4}"
              f"{float(np.mean((res0 / bar[m]) ** 2)):>9.4f}"
              f"{1e3 * np.sqrt(np.mean(res0 ** 2)):>9.3f}")
        for name, c in cols.items():
            M = np.column_stack(c)
            coef, *_ = np.linalg.lstsq(M * w[:, None], y * w, rcond=None)
            res = y - M @ coef
            print(f"  {name:<26}{M.shape[1]:>4}"
                  f"{float(np.mean((res / bar[m]) ** 2)):>9.4f}"
                  f"{1e3 * np.sqrt(np.mean(res ** 2)):>9.3f}   "
                  + "  ".join(f"{1e3 * v:+.3f}" for v in coef))

    print("\nwhat describes what the fit did not explain?  mW and mW/day")
    table("every anchor", np.ones(len(left), bool))
    table("pre-cutover only", ~after)
    table("post-cutover only", after)

    # And the same question of drift.py's own bands: does a band's rate survive
    # being allowed a step?  A band with no post-cutover anchor cannot tell.
    import drift as D
    full = F.load_anchors()
    cut = _seg._stamp(CUTOVER)
    fdays, faft = (full.t_abs - cut) / 86400.0, (full.t_abs > cut).astype(float)
    print("\n  analysis/drift.py's bands, with a cutover step allowed in")
    print(f"  {'band %':>14}{'n':>4}{'days':>7}{'K/day':>9}{'K/day|step':>12}"
          f"{'step K':>9}{'post':>6}")
    for b in D.band_table(full):
        m = (full.u >= b["u_lo"]) & (full.u <= b["u_hi"])
        M0 = np.column_stack([np.ones(int(m.sum())), full.u[m], fdays[m]])
        c0, *_ = np.linalg.lstsq(M0, full.T[m], rcond=None)
        head = (f"  {b['u_lo']:>6.2f}-{b['u_hi']:<7.2f}{int(m.sum()):>4}"
                f"{b['day_span']:>7.1f}{c0[2]:>9.4f}")
        if faft[m].any() and not faft[m].all():
            M1 = np.column_stack([M0, faft[m]])
            c1, *_ = np.linalg.lstsq(M1, full.T[m], rcond=None)
            print(head + f"{c1[2]:>12.4f}{c1[3]:>9.3f}"
                  f"{int(faft[m].sum()):>6}")
        else:
            print(head + f"{'--':>12}{'--':>9}{int(faft[m].sum()):>6}")
    print("  'post' is how many of the band's anchors are after the cutover.  "
          "None, or all,\n  and the band cannot separate a step from a slope "
          "at any price.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="step 8's gate and section 1's score")
    ap.add_argument("--profile", action="store_true",
                    help="trace the objective against a pinned campaign slope")
    ap.add_argument("--no-campaign", action="store_true",
                    help="score the fit with the ramp off, for comparison")
    ap.add_argument("--postcal", action="store_true",
                    help="add trace-postcal-20260905 as a second record "
                         "(section 7.1's fit B)")
    ap.add_argument("--shapes", action="store_true",
                    help="what shape the leftover residual has -- trap T7")
    a = ap.parse_args(argv)
    campaign = not a.no_campaign

    if a.shapes:
        shapes(campaign=campaign, postcal=a.postcal)
        return 0

    r, _, taus = production(campaign=campaign, postcal=a.postcal)
    print("the production fit" + ("" if campaign else ", campaign OFF")
          + (", + trace-postcal-20260905" if a.postcal else ""))
    describe(r)
    print()
    scoreboard(r, taus=taus)
    if a.profile:
        profile(campaign=campaign, postcal=a.postcal)
    gate(campaign=campaign, postcal=a.postcal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
