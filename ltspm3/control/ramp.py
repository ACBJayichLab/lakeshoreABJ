"""Setpoint ramping -- move the target slowly enough that the loop can follow.

This exists because of a conflict between two requirements that are both real:

* the supervisor refuses a setpoint STEP larger than ``warn_error_k``
  outright (rule 8, enforced in ``set_setpoint``), because a step that size is
  a typo as often as it is an instruction -- 300 K typed for 30 would walk the
  sample to the top of the table.  And while the setpoint is not moving, an
  error past ``warn_error_k`` is what the premise check warns on;
* the operator wants to sweep temperature programmatically, and wants the loop
  to come back to setpoint after a fault ramp-down.

Both of those are large deliberate errors, and under a step change they are
indistinguishable from the broken-premise case the check is there to catch.

Ramping the *setpoint* rather than stepping it resolves this cleanly: the
target moves at a rate the cryostat can actually follow, the trajectory is a
commanded thing with a rate limit on it, and the premise check keeps its full
meaning for genuine anomalies.  Nothing has to be relaxed.  The lag a ramp
commands is not an excursion, and the watt residual says so in the one variable
that knows the difference -- it carries ``C dT/dt`` explicitly.

There is one rate -- ``max_rate_k_per_min``, 5 K/min -- and everything that
moves a setpoint uses it: a commanded sweep, the approach after a fault, and
the open-loop ramp-down.  It used to be four numbers (0.5 default, 5 ceiling,
0.25 approach, and two percent-rates with a knee for the ramp-down) and they
disagreed by a factor of twenty.

What makes one rate possible is that the OUTPUT limit is now derived from it
rather than set beside it: ``max_rate_k_per_min / K(T)``, floored where the
model has no opinion.  A rate in percent is a different rate in kelvin at every
temperature -- 0.2 %/min is 2.5 K/min at 118 K and 0.07 K/min at 10 K -- which
is why picking a kelvin rate to fit a percent rate could only ever be right in
one place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RampConfig:
    #: **THE ONE RATE** (Jeff, 2026-09-11), and the other of the two rate
    #: fields left of eight.  It is the default sweep rate, the rate a
    #: post-fault approach walks in at, the rate the open-loop fault ramp-down
    #: descends at, and the ceiling on anything a client asks for.  Those were
    #: four numbers and they disagreed.
    #:
    #: `rate_k_per_min: 0.5` is gone with them.  It was chosen to sit under
    #: what an output rate limiter of 0.2 %/min could deliver at one operating
    #: point -- a rate in kelvin picked to fit a rate in percent -- and both
    #: halves of that arrangement are now derived from the gain instead.
    max_rate_k_per_min: float = 5.0

    #: Corner rounding, **as a multiple of the loop's own dead time**.
    #:
    #: A linear ramp has a discontinuous derivative at both ends.  The loop
    #: cannot follow a corner, so it lags into the ramp and then overshoots out
    #: of it -- and no retuning fixes that, because the demand itself is not
    #: physically realisable.  Rounding the corners costs a little tracking lag
    #: and removes the overshoot.  0 disables smoothing.
    #:
    #: It was a flat ``smooth_tau_s: 300.0``, measured against the 620 s
    #: response: overshoot fell from 464 mK to 25 mK on a 3 K sweep and became
    #: independent of sweep rate, which is the signature of a trajectory the
    #: loop can actually follow.  **That measurement was taken at 0.5 K/min,
    #: where a ramp lasts hours.**  At Jeff's 5 K/min a 10 K sweep lasts 120 s,
    #: and a 300 s corner means the loop never approaches the commanded rate at
    #: all: measured five cycles into a sweep, the smoother was passing
    #: 0.0027 K/s against the ramp's 0.0833, so the band widened by 0.14 %
    #: where 5 K/min at 180 K needs 4.23 %.  The cryostat was not sweeping at
    #: the rate it had been given; it was sweeping at whatever the corner let
    #: through.
    #:
    #: What it IS now is the closed-loop response time, ``move_speed * tau(T)``
    #: -- see ``HeaterSupervisor._smooth_tau_s`` -- because a trajectory with
    #: corners sharper than the loop's own response is by construction one the
    #: loop cannot follow.  This field is the FLOOR under that, in dead times,
    #: for the cold end where ``tau`` is shorter than the measurement.
    #:
    #: **Eight, measured.**  A 3 K move at 30 K overshoots 17.9 % at four dead
    #: times and 0.1 % at eight, and nothing above 60 K notices the difference
    #: -- the closed-loop term is what binds up there.  Four was the first
    #: draft's guess from the delay alone.
    smooth_delays: float = 8.0


class SetpointRamp:
    """A setpoint that walks from where it is to where it was told to go.

    ``value(t)`` is what the PID should actually chase right now; ``target`` is
    where it will end up.  With no ramp in progress the two are equal.
    """

    def __init__(self, setpoint_k: float, config: RampConfig | None = None) -> None:
        self.cfg = config or RampConfig()
        self._from_k = setpoint_k
        self._to_k = setpoint_k
        self._t0: float | None = None
        self._rate_k_per_s = 0.0

    # -- state -------------------------------------------------------------

    @property
    def target(self) -> float:
        return self._to_k

    @property
    def ramping(self) -> bool:
        return self._t0 is not None

    @property
    def span(self) -> float:
        """Size of the move currently commanded (0.0 when not ramping)."""
        return abs(self._to_k - self._from_k) if self._t0 is not None else 0.0

    @property
    def rate_k_per_s(self) -> float:
        """Signed commanded rate, or 0.0 when not ramping."""
        return self._rate_k_per_s if self._t0 is not None else 0.0

    def value(self, t: float) -> float:
        """The commanded setpoint at ``t`` -- linear, exact, unsmoothed."""
        if self._t0 is None:
            return self._to_k
        travelled = self._rate_k_per_s * (t - self._t0)
        span = self._to_k - self._from_k
        if abs(travelled) >= abs(span):
            self._t0 = None
            self._from_k = self._to_k
            return self._to_k
        return self._from_k + travelled

    def eta_s(self, t: float) -> float:
        if self._t0 is None or self._rate_k_per_s == 0:
            return 0.0
        remaining = abs(self._to_k - self.value(t))
        return remaining / abs(self._rate_k_per_s)

    # -- commands ----------------------------------------------------------

    def jump_to(self, kelvin: float) -> None:
        """Set the target with no ramp at all.

        Only sensible for a small trim, or before the loop is armed -- a large
        jump is exactly what the premise check is designed to refuse.
        """
        self._from_k = self._to_k = kelvin
        self._t0 = None
        self._rate_k_per_s = 0.0

    def start(
        self,
        t: float,
        to_k: float,
        *,
        from_k: float | None = None,
        rate_k_per_min: float | None = None,
    ) -> None:
        """Begin ramping to ``to_k``.

        ``from_k`` defaults to the setpoint currently in force, which keeps the
        setpoint continuous.  Pass the *measured* temperature instead when
        resuming control after the cryostat has drifted away from the old target
        -- otherwise the ramp starts from a value the cryostat is nowhere near and
        the loop opens with the very error the ramp exists to avoid.
        """
        rate = (self.cfg.max_rate_k_per_min if rate_k_per_min is None
                else rate_k_per_min)
        rate = abs(rate)
        if rate <= 0:
            raise ValueError("ramp rate must be positive")
        if rate > self.cfg.max_rate_k_per_min:
            raise ValueError(
                f"ramp rate {rate} K/min exceeds max_rate_k_per_min "
                f"{self.cfg.max_rate_k_per_min}; the loop cannot follow it"
            )

        start_k = self.value(t) if from_k is None else from_k
        self._from_k = start_k
        self._to_k = to_k
        if start_k == to_k:
            self._t0 = None
            self._rate_k_per_s = 0.0
            return
        self._t0 = t
        self._rate_k_per_s = (rate / 60.0) * (1.0 if to_k > start_k else -1.0)

    def abort(self, t: float) -> float:
        """Stop where we are.  Returns the setpoint now held."""
        here = self.value(t)
        self.jump_to(here)
        return here


class SetpointSmoother:
    """Rounds the corners off a commanded trajectory.

    A linear ramp has a discontinuous derivative at both ends.  A first-order
    a cryostat cannot follow a corner, so the loop lags going into the ramp and
    overshoots coming out of it -- and no retuning removes that, because the
    demand itself is not physically realisable.  Low-passing the setpoint costs
    a little tracking lag and removes the overshoot.

    Kept separate from :class:`SetpointRamp` so the ramp stays an exact,
    directly-testable generator and the smoothing is visibly a control choice.
    It also reports the *achieved* rate, which is what velocity feedforward
    should be built on: it rises from zero and decays back to zero, so the
    feedforward does the same instead of stepping at each end of the ramp.
    """

    def __init__(self, tau_s: float = 150.0, *, value: float | None = None) -> None:
        self.tau_s = tau_s
        self.value = value
        self.rate_k_per_s = 0.0
        self._last_t: float | None = None

    def reset(self, value: float | None = None) -> None:
        self.value = value
        self.rate_k_per_s = 0.0
        self._last_t = None

    @property
    def settled(self) -> bool:
        return abs(self.rate_k_per_s) < 1e-9

    def update(self, t: float, target: float) -> float:
        if self.tau_s <= 0 or self.value is None or self._last_t is None:
            self.value = target
            self.rate_k_per_s = 0.0
            self._last_t = t
            return target
        dt = t - self._last_t
        self._last_t = t
        if dt <= 0:
            return self.value
        alpha = 1.0 - math.exp(-dt / self.tau_s)
        previous = self.value
        self.value += alpha * (target - self.value)
        self.rate_k_per_s = (self.value - previous) / dt
        return self.value
