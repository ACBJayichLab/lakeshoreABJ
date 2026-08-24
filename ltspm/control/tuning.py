"""Gain scheduling and IMC tuning: a first-order closed loop with no overshoot.

Why this replaces the steady-state curve as the control model
-------------------------------------------------------------

The sample is a weakly-pinned island: small mass, small heater, one real
thermal link to the coldplate (radiation is mostly from parts that are
themselves sunk to the coldplate).  Everything that matters to a controller is
therefore *local*:

* the **gain** ``K = dT/d(pct)`` in K/%, set by the conductance of that link;
* the **time constant** ``tau = C/G``, set by the island's heat capacity.

Both vary with temperature, and both change again when the coldplate moves --
heating an island off a 300 K coldplate is a different plant from heating it
off a 4 K one, because the effective cooling power is completely different.

A global percent-to-temperature curve tries to encode all of that in one
function and cannot: fitting ``dT ~ pct^6.51`` and ``dT ~ (pct-56.9)^0.92`` to
the same 24 settled points gives R^2 of 0.9969 and 0.99998 respectively, and
they disagree by tens of kelvin outside the fitted band.  The logs do not
contain the information needed to choose.  Local gain and time constant, by
contrast, are exactly what a step test measures, need no extrapolation, and are
re-measurable whenever the rig changes.

The tuning
----------

Model the plant near an operating point as first order::

    G(s) = K / (1 + tau*s)

An IMC / pole-cancellation PI controller then gives a closed loop that is
*first order* -- no overshoot at any gain, which is what "critically damped"
buys on a cryostat where overshoot is wasted hours::

    Ti = tau
    Kp = tau / (K * tau_cl)          =>   closed loop = 1 / (1 + tau_cl*s)

``tau_cl`` is chosen directly: it *is* the closed-loop response time.  That is
the whole appeal -- one number with a physical meaning, rather than two gains
that interact.

Two phases
----------

Holding and moving want opposite things, so they get different ``tau_cl``:

* **HOLD** -- stabilising at temperature for hours.  Disturbances are slow
  (bath drift, radiation) and the measurement floor is a few mK of *correlated*
  noise.  A slow loop rejects that noise; a fast one amplifies it into the
  heater.  So ``tau_cl`` is long.
* **MOVE** -- following a commanded sweep, or approaching setpoint after a
  fault.  Here bandwidth is the point, and a few mK of extra noise on the way
  is irrelevant.  So ``tau_cl`` is short.

Switching between them is hysteretic, because a loop that oscillates between
tunings is worse than either.
"""

from __future__ import annotations

import bisect
import enum
import math
from dataclasses import dataclass, field


class ControlPhase(enum.Enum):
    HOLD = "hold"    # stabilising: slow, quiet
    MOVE = "move"    # sweeping or approaching: responsive


@dataclass(frozen=True)
class OperatingPoint:
    """One measured step response.

    ``gain_k_per_pct`` and ``tau_s`` are *local* quantities -- what the plant
    does for a small change here, not a claim about the whole range.
    """

    kelvin: float
    gain_k_per_pct: float
    tau_s: float
    #: Coldplate temperature when this was measured.  The same island off a
    #: 300 K plate is a different plant; recording it makes that visible
    #: instead of silently averaging two regimes together.
    coldplate_k: float | None = None
    note: str = ""


#: Provisional schedule.  Only the 137 K row is measured -- one clean step
#: response (65.9% -> 137.3 K, tau ~= 620 s, gain ~13 K/%) out of ~200 heater
#: commands in the reference logs; every other command there is a sub-2 K trim
#: that drift swamps.  The other rows are the steady-state curve's local slope,
#: which is a much weaker claim.
#:
#: **This is the table a step test should replace.**  See tools/steptest.py.
PROVISIONAL_SCHEDULE: tuple[OperatingPoint, ...] = (
    OperatingPoint(18.2, 1.6, 300.0, note="slope only, tau guessed"),
    OperatingPoint(99.6, 10.0, 620.0, coldplate_k=8.2, note="slope only, tau from 137 K"),
    OperatingPoint(137.3, 13.0, 620.0, coldplate_k=8.25, note="MEASURED step response"),
    OperatingPoint(170.7, 13.4, 620.0, coldplate_k=8.3, note="slope only, tau from 137 K"),
)


@dataclass
class TuningConfig:
    """Closed-loop response times, and the bounds that keep them sane."""

    enabled: bool = True

    #: Stabilising.  Long: the disturbances are slow and the noise is not.
    hold_tau_cl_s: float = 1800.0
    #: Sweeping or approaching.  Short enough to follow a ramp.
    move_tau_cl_s: float = 300.0

    #: Enter MOVE when a ramp is running or the error exceeds this.
    move_error_k: float = 0.25
    #: Return to HOLD only below this, and only after settling for a while.
    hold_error_k: float = 0.10
    hold_settle_s: float = 120.0

    #: Absolute bounds on the scheduled gains.  A bad schedule entry, or an
    #: operating point far outside the table, must not produce a violent loop.
    min_kp_pct_per_k: float = 0.002
    max_kp_pct_per_k: float = 0.50
    min_ti_s: float = 60.0
    max_ti_s: float = 7200.0

    schedule: tuple[OperatingPoint, ...] = PROVISIONAL_SCHEDULE


def imc_pi(gain_k_per_pct: float, tau_s: float, tau_cl_s: float) -> tuple[float, float]:
    """PI gains for a first-order closed loop of time constant ``tau_cl_s``.

    Returns ``(kp_pct_per_k, ti_s)``.  Pole cancellation: ``Ti = tau`` removes
    the plant pole, leaving an integrator, so the loop is first order and
    cannot overshoot however ``tau_cl`` is chosen.
    """
    if gain_k_per_pct <= 0:
        raise ValueError("plant gain must be positive")
    if tau_s <= 0 or tau_cl_s <= 0:
        raise ValueError("time constants must be positive")
    return tau_s / (gain_k_per_pct * tau_cl_s), tau_s


class PlantSchedule:
    """Local gain and time constant as functions of temperature."""

    def __init__(self, points=PROVISIONAL_SCHEDULE) -> None:
        pts = sorted(points, key=lambda p: p.kelvin)
        if not pts:
            raise ValueError("schedule needs at least one operating point")
        self.points = tuple(pts)
        self._k = [p.kelvin for p in self.points]

    @staticmethod
    def _blend(x, x0, x1, y0, y1):
        """Interpolate in log space -- gain and tau are positive and span
        decades, so a linear blend would badly misrepresent the middle."""
        if x1 == x0:
            return y0
        f = (x - x0) / (x1 - x0)
        f = max(0.0, min(1.0, f))          # clamp: never extrapolate a gain
        return math.exp(math.log(y0) + f * (math.log(y1) - math.log(y0)))

    def _bracket(self, kelvin: float) -> tuple[OperatingPoint, OperatingPoint]:
        if len(self.points) == 1:
            return self.points[0], self.points[0]
        i = bisect.bisect_left(self._k, kelvin)
        if i <= 0:
            return self.points[0], self.points[0]      # clamp, do not extrapolate
        if i >= len(self.points):
            return self.points[-1], self.points[-1]
        return self.points[i - 1], self.points[i]

    def gain_at(self, kelvin: float) -> float:
        a, b = self._bracket(kelvin)
        return self._blend(kelvin, a.kelvin, b.kelvin, a.gain_k_per_pct, b.gain_k_per_pct)

    def tau_at(self, kelvin: float) -> float:
        a, b = self._bracket(kelvin)
        return self._blend(kelvin, a.kelvin, b.kelvin, a.tau_s, b.tau_s)

    def extrapolating(self, kelvin: float) -> bool:
        """True outside the measured range, where gains are clamped not fitted."""
        return kelvin < self._k[0] or kelvin > self._k[-1]


class Tuner:
    """Picks (kp, ti) for the present temperature and phase."""

    def __init__(self, config: TuningConfig | None = None) -> None:
        self.cfg = config or TuningConfig()
        self.schedule = PlantSchedule(self.cfg.schedule)
        self.phase = ControlPhase.HOLD
        self._settled_since: float | None = None

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def tau_cl_for(self, phase: ControlPhase) -> float:
        return (self.cfg.move_tau_cl_s if phase is ControlPhase.MOVE
                else self.cfg.hold_tau_cl_s)

    def update_phase(self, t: float, *, error_k: float, ramping: bool) -> ControlPhase:
        """Hysteretic HOLD/MOVE selection.

        A ramp always means MOVE.  Otherwise MOVE is entered on a large error
        and left only after the error has been small for ``hold_settle_s`` --
        chattering between two tunings is worse than either of them.
        """
        c = self.cfg
        if ramping or abs(error_k) > c.move_error_k:
            self.phase = ControlPhase.MOVE
            self._settled_since = None
        elif self.phase is ControlPhase.MOVE:
            if abs(error_k) <= c.hold_error_k:
                if self._settled_since is None:
                    self._settled_since = t
                elif t - self._settled_since >= c.hold_settle_s:
                    self.phase = ControlPhase.HOLD
                    self._settled_since = None
            else:
                self._settled_since = None
        return self.phase

    def gains_for(self, kelvin: float, phase: ControlPhase | None = None
                  ) -> tuple[float, float]:
        """``(kp, ti)`` for this temperature and phase, bounded."""
        c = self.cfg
        phase = phase or self.phase
        gain = self.schedule.gain_at(kelvin)
        tau = self.schedule.tau_at(kelvin)
        kp, ti = imc_pi(gain, tau, self.tau_cl_for(phase))
        kp = max(c.min_kp_pct_per_k, min(c.max_kp_pct_per_k, kp))
        ti = max(c.min_ti_s, min(c.max_ti_s, ti))
        return kp, ti


def identify_first_order(samples, *, settle_fraction: float = 0.1
                         ) -> tuple[float, float, float]:
    """Fit ``(T_final, tau, r2)`` to a step response.

    ``samples`` is ``(t_s, kelvin)`` starting at the step.  The fit is on
    ``ln|T_inf - T|``, which is linear in t for a first-order response;
    ``T_inf`` is taken from the tail.  Points within ``settle_fraction`` of
    ``T_inf`` are dropped because their logarithm is dominated by noise.

    This is the whole of what a step test needs to yield -- combined with the
    output step size it gives gain, and it gives tau directly.
    """
    pts = [(t, v) for t, v in samples if v is not None]
    if len(pts) < 20:
        raise ValueError("need at least 20 samples to identify a step response")
    t0, y0 = pts[0]
    tail = pts[-max(5, len(pts) // 20):]
    t_inf = sum(v for _, v in tail) / len(tail)
    span = t_inf - y0
    if abs(span) < 1e-9:
        raise ValueError("no step in this data")

    xs, ys = [], []
    for t, v in pts:
        r = t_inf - v
        if r * span <= 0 or abs(r) < settle_fraction * abs(span):
            continue
        xs.append(t - t0)
        ys.append(math.log(abs(r)))
    if len(xs) < 10:
        raise ValueError("not enough of the transient survives the settle cut")

    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((a - mx) ** 2 for a in xs)
    slope = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / den
    if slope >= 0:
        raise ValueError("response is not decaying toward a final value")
    res = sum((b - (my + slope * (a - mx))) ** 2 for a, b in zip(xs, ys))
    tot = sum((b - my) ** 2 for b in ys)
    return t_inf, -1.0 / slope, (1.0 - res / tot if tot > 0 else 1.0)
