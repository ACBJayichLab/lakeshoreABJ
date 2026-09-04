"""PI gains scheduled against temperature, derived from the fitted plant.

Linearise the tier-1 ODE about a settled point.  With
C(T) dT/dt = Q - [Lambda(T) - Lambda(T_c)], a small change gives

    C dT' = dQ - Lambda'(T) dT'      ->      G(s) = K / (1 + tau s)

    K_W = 1 / Lambda'(T)      [K/W]     steady gain
    K_u = dT/du               [K/%]     the same thing in the units the loop
                                        actually commands
    tau = C(T) / Lambda'(T)   [s]

so the plant is a first-order lag whose gain and time constant both come
straight out of the fit.  Nothing else needs measuring.

BUT THE LOOP IS NOT THE PLANT.  Two lags in the existing chain are comparable
to the cryostat's own and cannot be left out:

* the measurement low-pass, ``filters.py`` default tau = 60 s;
* the acquisition cycle, ``interval_s`` default 1 s, plus the median-5 filter's
  group delay and the zero-order hold -- about 3 s of pure delay together.

Below about 60 K the 60 s FILTER is slower than the cryostat, so the dominant
lag of the loop is the instrument's, not the sample's.  Above it the sample
dominates.  A schedule derived from the plant alone would be wrong on one side
of that crossover or the other.

Skogestad's half rule folds the smaller lag into an effective delay:

    tau_eff = tau_1 + tau_2 / 2        theta_eff = tau_2 / 2 + L

and SIMC then gives, for a chosen closed-loop time constant tau_c >= theta:

    Kc = tau_eff / (K (tau_c + theta))       Ti = min(tau_eff, 4 (tau_c + theta))

Td = 0 throughout: the plant is first order with a short delay, and derivative
action buys nothing there while amplifying a measurement whose noise is
quadratic in T.  ``PIDConfig.td`` should stay at 0.

Gains come out in PIDConfig's units -- kp in percent per kelvin, ti in seconds.
"""
from __future__ import annotations

import csv
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, "analysis")
import fit_ode as F  # noqa: E402
from plot_gain import coldplate_of  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/pid_tuning.png"
N_LAM, N_CAP = 9, 4

#: The loop, as configured today.
TAU_FILTER_S = 60.0        # filters.py LowPass default
DEAD_TIME_S = 3.0          # 1 s cycle + median-5 group delay + ZOH
#: What PIDConfig ships with, for comparison.
CURRENT_KP, CURRENT_TI = 0.02, 900.0
#: One DAC code on the 218 analog output.
DAC_STEP_PCT = 0.01

#: Sensor noise, quadratic in T, from docs/ltspm3/thermal-response.md.
NOISE_FLOOR_K, NOISE_QUAD = 0.0018, 1.36e-6
#: Allan deviation at 96 K: 6.1 mK @ 4 s, 4.1 @ 60 s, 2.5 @ 600 s -- about 2x
#: worse than 1/sqrt(N), because the noise is correlated (lag-1 +0.51).  As a
#: power law that is sigma_A(t) = sigma_A(4 s) (t/4)^-0.178, and it is what
#: decides how much averaging a slower loop actually buys: going from a 4 s
#: loop to a 600 s one cuts the noise by 2.4x, not by 12x.
ALLAN_REF_S, ALLAN_EXP = 4.0, 0.178
ALLAN_AT_4S_OVER_SIGMA = 6.1 / 12.5      # measured at 96 K


def sensor_noise_k(T):
    return np.maximum(NOISE_FLOOR_K, NOISE_QUAD * np.asarray(T) ** 2)


def allan_k(t_s, T):
    """Rms left after averaging for t_s seconds, at temperature T."""
    return (ALLAN_AT_4S_OVER_SIGMA * sensor_noise_k(T)
            * (np.maximum(t_s, ALLAN_REF_S) / ALLAN_REF_S) ** -ALLAN_EXP)


def half_rule(tau_plant, tau_filter=TAU_FILTER_S, dead=DEAD_TIME_S):
    """Skogestad's half rule: fold the smaller lag half into the delay."""
    t1 = np.maximum(tau_plant, tau_filter)
    t2 = np.minimum(tau_plant, tau_filter)
    return t1 + t2 / 2.0, t2 / 2.0 + dead


def simc(K, tau_eff, theta, tau_c):
    """SIMC PI.  Returns (kp, ti) in the plant's own gain units."""
    kp = tau_eff / (K * (tau_c + theta))
    ti = np.minimum(tau_eff, 4.0 * (tau_c + theta))
    return kp, ti


def plant():
    """K_u [K/%], K_W [K/W] and tau [s] against temperature, from the fit."""
    data = F.load_sweep()
    hi = float(data[1].max())
    anchors, taus = F.load_anchors(t_max=hi), F.load_taus(t_max=hi)
    r = F.fit(N_LAM, N_CAP, data, anchors, taus)
    rows = [x for x in csv.DictReader(open(F.ANCHORS, newline="", encoding="utf-8"))
            if x.get("grade")
            and float(x["T_inf"]) <= hi]
    tc_of, _, _ = coldplate_of(rows)

    T = np.geomspace(6.0, 188.0, 700)
    Tc = np.clip(tc_of(T), 1.0, None)
    Q = r["lam"](r["pl"], T) - r["lam"](r["pl"], Tc)
    ok = Q > 0
    T, Tc, Q = T[ok], Tc[ok], Q[ok]

    u = 100.0 * np.sqrt(Q * F.R_OHM) / (F.GAIN * F.V_FS)
    dQdT = (r["lam"].slope(r["pl"], T)
            - r["lam"].slope(r["pl"], Tc) * tc_of.derivative()(T))
    K_W = 1.0 / dQdT
    K_u = K_W * (2.0 * Q / u)                 # dQ/du = 2P/u exactly
    tau = r["cap"](r["pc"], T) / r["lam"].slope(r["pl"], T)
    return r, T, u, K_u, K_W, tau


def main():
    r, T, u, K_u, K_W, tau_p = plant()
    print(f"  plant from Lambda {N_LAM} knots, C {N_CAP}: "
          f"sweep rms {r['rms_k']:.3f} K")

    tau_eff, theta = half_rule(tau_p)
    levels = ((1.0, "#c05621", "tight   τ_c = θ"),
              (3.0, "#2c7a7b", "medium  τ_c = 3θ"),
              (10.0, "#2b6cb0", "smooth  τ_c = 10θ"))

    fig, ax = plt.subplots(2, 3, figsize=(16.5, 9.5))
    fig.suptitle("LTSPM3 PI schedule from the fitted plant — SIMC on the loop "
                 "as configured (60 s measurement filter, 1 s cycle)",
                 fontsize=13)

    a = ax[0, 0]
    a.loglog(T, K_u, color="#2c7a7b", lw=2.0, label="K$_u$ = dT/du  [K/%]")
    a.loglog(T, K_W, color="#805ad5", lw=2.0, ls="--", label="K$_W$ = dT/dP  [K/W]")
    a.set_xlabel("T  [K]"); a.set_ylabel("open-loop gain")
    a.set_title("(a) plant gain — 45× across the range")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8.5)

    a = ax[0, 1]
    a.loglog(T, tau_p, color="#2c7a7b", lw=2.0, label="cryostat  τ = C/Λ′")
    a.axhline(TAU_FILTER_S, color="#c05621", lw=1.6, ls="--",
              label=f"measurement filter  {TAU_FILTER_S:.0f} s")
    a.loglog(T, tau_eff, color="#1a202c", lw=1.4, label="τ$_{eff}$  (half rule)")
    a.loglog(T, theta, color="#805ad5", lw=1.4, ls=":", label="θ$_{eff}$  (half rule)")
    cross = float(np.interp(TAU_FILTER_S, tau_p, T))
    a.axvline(cross, color="#a0aec0", lw=.9)
    a.annotate(f"filter overtakes\nthe cryostat\nat {cross:.0f} K", (cross, 3),
               textcoords="offset points", xytext=(8, 0), fontsize=8,
               color="#4a5568")
    a.set_xlabel("T  [K]"); a.set_ylabel("time constant  [s]")
    a.set_title("(b) which lag dominates the loop")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="upper left")

    a = ax[0, 2]
    for mult, c, lab in levels:
        kp, _ = simc(K_u, tau_eff, theta, mult * theta)
        a.loglog(T, kp, color=c, lw=2.0, label=lab)
    a.axhline(CURRENT_KP, color="#1a202c", lw=1.4, ls="--",
              label=f"PIDConfig today  {CURRENT_KP} %/K")
    a.set_xlabel("T  [K]"); a.set_ylabel("k$_p$  [%/K]")
    a.set_title("(c) proportional gain")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="lower left")

    a = ax[1, 0]
    for mult, c, lab in levels:
        _, ti = simc(K_u, tau_eff, theta, mult * theta)
        a.loglog(T, ti, color=c, lw=2.0, label=lab)
    a.axhline(CURRENT_TI, color="#1a202c", lw=1.4, ls="--",
              label=f"PIDConfig today  {CURRENT_TI:.0f} s")
    a.set_xlabel("T  [K]"); a.set_ylabel("t$_i$  [s]")
    a.set_title("(d) integral time")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="upper left")

    a = ax[1, 1]
    for mult, c, lab in levels:
        kp, _ = simc(K_u, tau_eff, theta, mult * theta)
        a.loglog(T, kp * allan_k(TAU_FILTER_S, T), color=c, lw=2.0, label=lab)
    a.axhline(DAC_STEP_PCT, color="#1a202c", lw=1.4, ls="--",
              label="one DAC code, 0.01%")
    a.set_xlabel("T  [K]"); a.set_ylabel("heater jitter  k$_p$·σ  [%]")
    a.set_title("(e) what the noise does to the output")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="lower left")

    a = ax[1, 2]
    for mult, c, lab in levels:
        a.loglog(T, allan_k(mult * theta + theta, T) * 1e3, color=c, lw=2.0,
                 label=lab)
    a.loglog(T, sensor_noise_k(T) * 1e3, color="#1a202c", lw=1.4, ls="--",
             label="raw sensor noise")
    a.set_xlabel("T  [K]"); a.set_ylabel("achievable rms  [mK]")
    a.set_title("(f) the floor a loop of that speed can reach")
    a.grid(alpha=.3, which="both"); a.legend(fontsize=8, loc="upper left")

    fig.tight_layout()
    fig.savefig(OUT, dpi=130)
    print("wrote", OUT)

    print(f"\n{'T K':>7}{'u %':>7}{'K_u K/%':>9}{'K_W K/W':>9}{'tau_p s':>9}"
          f"{'tau_eff':>9}{'theta':>7}"
          f"{'kp tight':>10}{'ti tight':>9}{'kp med':>9}{'ti med':>8}"
          f"{'floor mK':>10}")
    for Tq in (10, 20, 30, 50, 77, 100, 137, 160, 180):
        if Tq < T.min() or Tq > T.max():
            continue
        i = int(np.argmin(np.abs(T - Tq)))
        te, th = tau_eff[i], theta[i]
        kt, it = simc(K_u[i], te, th, th)
        km, im = simc(K_u[i], te, th, 3 * th)
        print(f"{T[i]:>7.1f}{u[i]:>7.2f}{K_u[i]:>9.2f}{K_W[i]:>9.0f}"
              f"{tau_p[i]:>9.1f}{te:>9.1f}{th:>7.1f}"
              f"{kt:>10.3f}{it:>9.0f}{km:>9.3f}{im:>8.0f}"
              f"{1e3 * allan_k(4 * th, T[i]):>10.1f}")


if __name__ == "__main__":
    main()
