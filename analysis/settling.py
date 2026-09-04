"""What sets the settling time, and what minute-scale would cost the hold.

The complaint is 20-30 minutes to settle.  That is not the PID gains.  The
fitted plant has tau = 435 s at 100 K and 598 s at 180 K, so an open-loop
approach takes 3 tau = 22 to 30 minutes on its own.  The observation matches
the cryostat's own time constant almost exactly, and no retuning of a loop
that merely *waits* for the plant can beat it.

Getting to minutes means not waiting.  To move the sample by dT in time t you
must put C(T) dT joules into it over and above the new steady power:

    P_excess = C(T) dT / t          u_excess = P_excess / (dQ/du)

and dQ/du = 2P/u exactly.  So the output has to overshoot its final value
during the move and come back -- which is precisely what a velocity
feedforward is, and the term already exists (``PIDTerms.vff``).  What it needs
is the right gain, and that gain is

    g_v(T) = C(T) / (dQ/du)      [% per (K/s)]

which nobody could write down before C(T) was measured.

THE POINT FOR THE HOLD: this term is exactly zero when the setpoint is not
moving.  Raising kp would buy speed and cost stability, because a faster loop
follows more of a measurement whose noise goes as T^2.  Raising the velocity
feedforward buys speed and costs the hold NOTHING, because at constant
setpoint it contributes nothing to the output.  The two requirements are not
in conflict; they are served by different terms.

What blocks it today is authority, not tuning.  From SupervisorConfig:

    max_rate_pct_per_min = 0.20      the output may not slew faster than this
    max_step_pct         = 0.02      nor move more than this in one command
    max_velocity_ff_pct  = 1.00      cap on the term that does the overdriving
    RampConfig.rate_k_per_min = 0.5  and the setpoint itself moves this slowly

A 5 K move in one minute at 180 K needs about 4% of extra output.  At
0.20 %/min the output takes twenty minutes just to get there.
"""
from __future__ import annotations

import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402
import pid_tuning as P  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/settling.png"

#: The move this is all sized for.
STEP_K = 5.0
#: SupervisorConfig / RampConfig as they ship.
MAX_RATE_PCT_MIN = 0.20
MAX_VFF_PCT = 1.00
RAMP_K_PER_MIN = 0.5
#: Output ceilings worth considering for the overdrive.
U_CEILINGS = ((72.0, "#2c7a7b"), (75.0, "#c05621"), (80.0, "#805ad5"))


def main():
    r, T, u, K_u, K_W, tau_p = P.plant()
    Q = F.power_w(u)
    dQdu = 2.0 * Q / u                       # W per percent, exact
    C = r["cap"](r["pc"], T)                 # J/K
    g_v = C / dQdu                           # % per (K/s)
    print(f"  plant from Lambda {P.N_LAM} knots, C {P.N_CAP}: "
          f"sweep rms {r['rms_k']:.3f} K")

    fig, ax = plt.subplots(2, 2, figsize=(14.0, 9.5))
    fig.suptitle(f"LTSPM3 settling — what a {STEP_K:.0f} K move costs, and why "
                 "the hold need not pay for it", fontsize=13.5)

    # ---- (a) where the 20-30 minutes comes from --------------------------
    a = ax[0, 0]
    a.semilogy(T, 3 * tau_p / 60.0, color="#1a202c", lw=2.2,
               label="plant alone, 3τ  (no overdrive)")
    a.semilogy(T, np.full_like(T, STEP_K / RAMP_K_PER_MIN), color="#805ad5",
               lw=1.6, ls="--",
               label=f"setpoint ramp cap  {RAMP_K_PER_MIN} K/min")
    for t_min, c in ((1.0, "#c05621"), (2.0, "#2c7a7b")):
        need = C * STEP_K / (t_min * 60.0) / dQdu
        a.semilogy(T, need / MAX_RATE_PCT_MIN, color=c, lw=1.8,
                   label=f"time to slew the output for a {t_min:.0f} min move\n"
                         f"at {MAX_RATE_PCT_MIN} %/min")
    a.axhspan(20, 30, color="#fed7d7", alpha=.55, lw=0)
    a.text(190, 21, "observed 20–30 min", fontsize=8.5, ha="right",
           va="bottom", color="#742a2a")
    a.set_xscale("log")
    a.set_xlabel("T  [K]"); a.set_ylabel("minutes")
    a.set_title(f"(a) what a {STEP_K:.0f} K move costs today")
    a.set_ylim(3e-3, 200)
    a.grid(alpha=.3, which="both")
    a.legend(fontsize=7.5, loc="lower right")

    # ---- (b) the velocity feedforward gain -------------------------------
    a = ax[0, 1]
    a.loglog(T, g_v, color="#2c7a7b", lw=2.2,
             label="g$_v$ = C(T) / (dQ/du)")
    a.loglog(T, MAX_VFF_PCT / g_v * 60.0, color="#c05621", lw=1.8, ls="--",
             label=f"ramp the {MAX_VFF_PCT:.0f}% vff cap allows  [K/min]")
    for Tq in (30, 100, 180):
        i = int(np.argmin(np.abs(T - Tq)))
        a.plot(T[i], g_v[i], "*", ms=13, color="#805ad5", zorder=5)
        a.annotate(f"{T[i]:.0f} K\n{g_v[i]:.1f} %·s/K", (T[i], g_v[i]),
                   textcoords="offset points", xytext=(-8, 10), fontsize=8,
                   ha="right", color="#553c9a")
    a.set_xlabel("T  [K]"); a.set_ylabel("%·s/K      or      K/min")
    a.set_title("(b) velocity feedforward gain — the number C(T) now supplies")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="upper left")

    # ---- (c) overdrive needed vs what is allowed -------------------------
    a = ax[1, 0]
    for t_min, c in ((1.0, "#c05621"), (2.0, "#2c7a7b"), (5.0, "#2b6cb0")):
        a.semilogx(T, C * STEP_K / (t_min * 60.0) / dQdu, color=c, lw=2.0,
                   label=f"{STEP_K:.0f} K in {t_min:.0f} min")
    a.axhline(MAX_VFF_PCT, color="#1a202c", lw=1.5, ls="--",
              label=f"max_velocity_ff_pct  {MAX_VFF_PCT:.0f}%")
    a.axhline(MAX_RATE_PCT_MIN, color="#742a2a", lw=1.5, ls=":",
              label=f"max_rate_pct_per_min  {MAX_RATE_PCT_MIN}%/min")
    a.set_xlabel("T  [K]"); a.set_ylabel("extra output needed  [%]")
    a.set_ylim(0, 6)
    a.set_title("(c) the overdrive a fast move needs")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="upper left")

    # ---- (d) how fast the cryostat can go at all -------------------------
    a = ax[1, 1]
    for u_max, c in U_CEILINGS:
        p_max = F.power_w(np.full_like(T, u_max))
        a.loglog(T, 60.0 * np.maximum(p_max - Q, 1e-9) / C, color=c, lw=2.0,
                 label=f"heating, ceiling {u_max:.0f}%")
    a.loglog(T, 60.0 * Q / C, color="#1a202c", lw=2.0, ls="--",
             label="cooling, heater to zero")
    a.axhline(RAMP_K_PER_MIN, color="#742a2a", lw=1.5, ls=":",
              label=f"RampConfig default  {RAMP_K_PER_MIN} K/min")
    a.set_xlabel("T  [K]"); a.set_ylabel("achievable rate  [K/min]")
    # Below ~20 K C collapses and the arithmetic reports thousands of K/min.
    # It is not wrong, it is irrelevant: down there nothing else in the loop --
    # the 1 s cycle, the 60 s filter -- can keep up, so the rate is set by the
    # instrument and not by the cryostat.
    a.set_ylim(0.2, 2000)
    a.set_title("(d) what the cryostat can actually do — cooling is the fast way")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="lower left")

    fig.tight_layout()
    fig.savefig(OUT, dpi=130)
    print("wrote", OUT)

    print(f"\n{'T K':>7}{'u %':>7}{'C J/K':>8}{'tau_p s':>9}{'3tau min':>10}"
          f"{'g_v %s/K':>10}{'dU 1min':>9}{'dU 2min':>9}"
          f"{'up K/min':>10}{'down K/min':>11}")
    for Tq in (10, 20, 30, 50, 77, 100, 137, 160, 180):
        if Tq < T.min() or Tq > T.max():
            continue
        i = int(np.argmin(np.abs(T - Tq)))
        p75 = F.power_w(75.0)
        print(f"{T[i]:>7.1f}{u[i]:>7.2f}{C[i]:>8.4f}{tau_p[i]:>9.1f}"
              f"{3 * tau_p[i] / 60:>10.1f}{g_v[i]:>10.2f}"
              f"{C[i] * STEP_K / 60 / dQdu[i]:>9.2f}"
              f"{C[i] * STEP_K / 120 / dQdu[i]:>9.2f}"
              f"{60 * max(p75 - Q[i], 0) / C[i]:>10.1f}"
              f"{60 * Q[i] / C[i]:>11.1f}")


if __name__ == "__main__":
    main()
