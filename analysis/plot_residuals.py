"""The fit against every anchor it was given, so its quality can be judged by eye.

One figure, four panels, two models on every one:

* **the current production fit** -- ``fit_ode.fit`` at the preset ``plot_gain``
  and ``export_response`` use, read from the cache when warm;
* **the shipped table** -- ``ltspm3.model.fitted_response``, which is what the
  simulator, the sweep tool and (until Phase 3 of PID_PLAN.md) the controller
  actually run on.  It is known low; this is where you see by how much.

    (a) steady state, T against heater power, every in-band anchor by era
    (b) the steady-state residual, in KELVIN and in MILLIWATTS -- the second
        is what the residual delta-Q of PID_PLAN.md section 3 will read, and
        the first is the same number through the local slope
    (c) tau(T) = C / Lambda' against the tau-graded dwells
    (d) the ratio measured / fitted tau, with the 10 % band the plan asks for

Residuals are computed in POWER, the way the objective sees them:
``[Lambda(T_meas) - Lambda(Tc_meas)] - (Q + group offset)`` -- the power the model
needs beyond what was applied, so POSITIVE means the model runs cold -- then divided by
``Lambda'(T_meas)`` for kelvin.  No root-finding, so nothing here can hide a
miss behind an interpolation.

    python analysis/plot_residuals.py [out.png]
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402
from _data import REPO_ROOT  # noqa: E402
from plot_gain import N_CAP, N_DRIFT, N_LAM  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/fit_residuals.png"

#: The generated table, read as a FILE.  ``analysis/`` imports neither package
#: (CLAUDE.md invariant 1 and the layout note), and this table is analysis's own
#: output, so parsing the literal back is the honest way to compare against it.
SHIPPED_TABLE = Path(REPO_ROOT) / "ltspm3" / "model" / "_fitted_table.py"


class Shipped:
    """``ltspm3.model.fitted_response``'s three lookups, re-done on the file's TABLE.

    Columns: T [K], q [W] = Lambda(T) - Lambda(T_c), slope [W/K], cap [J/K], tc [K].
    Log-log interpolation as the shipped module does; clamped at the table's ends.
    """

    def __init__(self, path):
        import ast
        src = path.read_text(encoding="utf-8")
        start = src.index("TABLE = (")
        end = src.index("\n)\n", start) + 2
        rows = np.array(ast.literal_eval(src[start + len("TABLE = "):end].strip()), float)
        self.T, self.q, self.slope, self.cap, self.tc = rows.T
        self.T_MIN_K, self.T_MAX_K = float(self.T[0]), float(self.T[-1])
        self._lT = np.log(self.T)

    def _log_interp(self, kelvin, col):
        k = np.clip(np.asarray(kelvin, float), self.T_MIN_K, self.T_MAX_K)
        return np.exp(np.interp(np.log(k), self._lT, np.log(col)))

    def steady_power_w(self, kelvin):
        return self._log_interp(kelvin, self.q)

    def lambda_slope_w_per_k(self, kelvin):
        return self._log_interp(kelvin, self.slope)

    def tau_s(self, kelvin):
        return self._log_interp(kelvin, self.cap) / self._log_interp(kelvin, self.slope)


shipped = Shipped(SHIPPED_TABLE)

# Categorical hues in fixed order (dataviz reference palette, light surface).
ERA_COLOUR = {"prepython": "#2a78d6", "recorder": "#eb6834", "postcal": "#1baf7a"}
ERA_LABEL = {"prepython": "July-Aug (.xls logs)", "recorder": "Aug 24 - Sep 04",
             "postcal": "after the Coldplate recalibration"}
FIT_COLOUR = "#0b0b0b"
SHIPPED_COLOUR = "#52514e"
BAND = "#e6e5e1"


def main() -> int:
    rec, anchors, taus = F.production_inputs()
    r = F.fit(N_LAM, N_CAP, rec, anchors, taus, n_drift=N_DRIFT, groups=True)
    lam, cap, pl, pc = r["lam"], r["cap"], r["pl"], r["pc"]
    a = anchors
    rows = {(row.get("id") or row.get("source") or "").strip(): row for row in F.load_rows()}
    era = np.array([rows[s]["era"] for s in a.source])

    # The per-era power offset the fit measures: group 0 has none, group g >= 1
    # is group_w[g - 1] -- the same indexing fit() uses on the anchor residual.
    group_w = np.asarray(r["group_w"], float)
    offset_w = np.where(a.group > 0, group_w[np.maximum(a.group - 1, 0)] if len(group_w)
                        else 0.0, 0.0)

    # --- the residual, in power and in kelvin ------------------------------
    q_model = lam(pl, a.T) - lam(pl, a.Tc)
    q_meas = a.Q + offset_w
    # Power the model needs beyond what was applied (mW), and the same through the
    # local slope: T_meas - T_model(P).  POSITIVE means the model runs cold.
    dq_mw = 1e3 * (q_model - q_meas)
    slope = lam.slope(pl, a.T)                       # W/K
    dk = (q_model - q_meas) / slope
    # the shipped table, same convention, no era offset (it has none)
    q_ship = np.array([shipped.steady_power_w(T) for T in a.T])
    dk_ship = (q_ship - a.Q) / np.array([shipped.lambda_slope_w_per_k(T) for T in a.T])

    # --- figure ------------------------------------------------------------
    fig, ax = plt.subplots(2, 2, figsize=(13, 9.5))
    fig.suptitle("LTSPM3 thermal model against every anchor -- production fit "
                 f"(Lambda {N_LAM} knots, C {N_CAP}, drift {N_DRIFT}; rms {r['rms_k']:.3f} K, "
                 f"anchor {r['anchor_k']:.3f} K) vs the shipped table", fontsize=11)

    def by_era(axis, x, y, yerr=None, marker="o"):
        for e, c in ERA_COLOUR.items():
            m = (era == e) if era is not None else np.ones(len(x), bool)
            if not m.any():
                continue
            if yerr is not None:
                axis.errorbar(x[m], y[m], yerr=yerr[m], fmt="none", ecolor=c,
                              elinewidth=0.8, alpha=0.5, capsize=0)
            axis.plot(x[m], y[m], marker, ms=4.5, mfc="none", mec=c, mew=1.1,
                      ls="none", label=ERA_LABEL[e])

    # (a) steady state
    A = ax[0, 0]
    T = np.geomspace(max(4.7, float(a.T.min()) * 0.98), float(a.T.max()) * 1.02, 400)
    Tc_of = np.interp(T, np.sort(a.T), a.Tc[np.argsort(a.T)])
    A.plot(1e3 * (lam(pl, T) - lam(pl, Tc_of)), T, "-", color=FIT_COLOUR, lw=1.6,
           label="production fit")
    Ts = np.geomspace(shipped.T_MIN_K, shipped.T_MAX_K, 300)
    A.plot([1e3 * shipped.steady_power_w(t) for t in Ts], Ts, "--", color=SHIPPED_COLOUR,
           lw=1.2, label="shipped table")
    by_era(A, 1e3 * a.Q, a.T)
    A.set_xscale("log"); A.set_yscale("log")
    A.set_xlabel("heater power P  [mW]"); A.set_ylabel("settled sample T  [K]")
    A.set_title("(a) steady state, every in-band anchor")
    A.grid(alpha=0.25, which="both"); A.legend(fontsize=8, loc="lower right")

    # (b) residual, kelvin and milliwatts on two stacked axes (one axis each)
    B = ax[0, 1]
    B.axhspan(-0.3, 0.3, color=BAND, lw=0, label="REFIT target +/-0.3 K")
    B.axhline(0, color=SHIPPED_COLOUR, lw=0.6)
    B.plot(a.T, dk_ship, "x", ms=4, color=SHIPPED_COLOUR, alpha=0.6, ls="none",
           label="shipped table")
    by_era(B, a.T, dk, yerr=None)
    B.set_xscale("log")
    B.set_xlabel("measured settled T  [K]")
    B.set_ylabel("measured - model  [K]   (+ = model runs cold)")
    B.set_title("(b) steady-state residual in kelvin  (fit: circles; shipped: x)")
    B.grid(alpha=0.25, which="both"); B.legend(fontsize=8, loc="lower left")
    lim = max(2.0, float(np.percentile(np.abs(np.r_[dk, dk_ship]), 98)) * 1.1)
    B.set_ylim(-lim, lim)

    # (c) tau
    C = ax[1, 0]
    tau_fit = cap(pc, T) / lam.slope(pl, T)
    C.loglog(T, tau_fit, "-", color=FIT_COLOUR, lw=1.6, label="production fit  C / Lambda'")
    C.loglog(Ts, [shipped.tau_s(t) for t in Ts], "--", color=SHIPPED_COLOUR, lw=1.2,
             label="shipped table")
    tT, tV = taus
    tau_rows = [row for row in F.load_rows() if row.get("grade") == "tau"]
    tE = np.array([row["era"] for row in tau_rows])
    tTT = np.array([float(row["T_inf"]) for row in tau_rows])
    tVV = np.array([float(row["tau_s"]) for row in tau_rows])
    tS = np.array([float(row["sigma_tau_s"] or 0) for row in tau_rows])
    for e, c in ERA_COLOUR.items():
        m = tE == e
        if m.any():
            C.errorbar(tTT[m], tVV[m], yerr=tS[m], fmt="o", ms=4.5, mfc="none", mec=c,
                       ecolor=c, elinewidth=0.8, alpha=0.9, label=ERA_LABEL[e])
    C.axvspan(4, 25, color=BAND, lw=0)
    C.text(5, float(np.nanmax(tVV)) * 0.6, "tau < 2 s cadence:\nunmeasurable", fontsize=8,
           color="#52514e")
    C.set_xlabel("settled T  [K]"); C.set_ylabel("tau  [s]")
    C.set_title("(c) time constant against the tau-graded dwells")
    C.grid(alpha=0.25, which="both"); C.legend(fontsize=8, loc="lower right")

    # (d) tau ratio
    D = ax[1, 1]
    ratio = tVV / (cap(pc, tTT) / lam.slope(pl, tTT))
    ratio_ship = tVV / np.array([shipped.tau_s(t) for t in tTT])
    D.axhspan(0.9, 1.1, color=BAND, lw=0, label="+/-10 % (REFIT target)")
    D.axhline(1, color=SHIPPED_COLOUR, lw=0.6)
    D.plot(tTT, ratio_ship, "x", ms=4, color=SHIPPED_COLOUR, alpha=0.6, ls="none",
           label="shipped table")
    for e, c in ERA_COLOUR.items():
        m = tE == e
        if m.any():
            D.errorbar(tTT[m], ratio[m], yerr=tS[m] / (tVV[m] / ratio[m]), fmt="o", ms=4.5,
                       mfc="none", mec=c, ecolor=c, elinewidth=0.8, label=ERA_LABEL[e])
    D.set_xscale("log")
    D.set_xlabel("settled T  [K]"); D.set_ylabel("measured tau / model tau")
    D.set_title("(d) tau ratio  (fit: circles; shipped: x)")
    D.set_ylim(0.4, 1.8)
    D.grid(alpha=0.25, which="both"); D.legend(fontsize=8, loc="upper left")

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(OUT, dpi=130)

    # --- the numbers behind the picture -------------------------------------
    print(f"anchors {len(a)}  taus {len(tVV)}  nfev {r['nfev']}  cost {r['cost']:.1f}")
    print(f"{'era':<10}{'n':>4}{'fit rms K':>11}{'fit med|K|':>12}{'shipped rms K':>15}"
          f"{'fit rms mW':>12}")
    for e in ERA_COLOUR:
        m = (era == e) if era is not None else np.ones(len(a), bool)
        if not m.any():
            continue
        print(f"{e:<10}{m.sum():>4}{np.sqrt(np.mean(dk[m]**2)):>11.3f}"
              f"{np.median(np.abs(dk[m])):>12.3f}{np.sqrt(np.mean(dk_ship[m]**2)):>15.3f}"
              f"{np.sqrt(np.mean(dq_mw[m]**2)):>12.2f}")
    for lo, hi in ((4, 25), (25, 60), (60, 120), (120, 200)):
        m = (tTT >= lo) & (tTT < hi)
        if m.any():
            print(f"tau {lo:>3}-{hi:<3} K  n={m.sum():<3} ratio median {np.median(ratio[m]):.3f}"
                  f"  spread {np.std(ratio[m]):.3f}   shipped {np.median(ratio_ship[m]):.3f}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
