"""Setpoint ramping -- move the target slowly enough that the loop can follow.

This exists because of a conflict between two requirements that are both real:

* the supervisor treats an error above ``max_error_k`` as evidence that
  something is wrong *with the cryostat*, freezes the heater and eventually ramps
  down.  That check is the main protection against acting on a bad reading, and
  loosening it would defeat the point of the whole safety envelope;
* the operator wants to sweep temperature programmatically, and wants the loop
  to come back to setpoint after a fault ramp-down.

Both of those are large deliberate errors, and under a step change they are
indistinguishable from the broken-premise case the check is there to catch.

Ramping the *setpoint* rather than stepping it resolves this cleanly: the
target moves at a rate the cryostat can actually follow, the tracking error stays
inside ``max_error_k`` throughout, and the premise check keeps its full meaning
for genuine anomalies.  Nothing has to be relaxed.

The default rate is deliberately below what the output rate limiter can
deliver.  At ~10.0 K/% (the 63% operating point) and ``max_rate_pct_per_min``
of 0.20 %/min the heater can chase about 2 K/min in steady state, so 0.5 K/min
leaves the loop with authority to spare for the correction on top of the ramp.
The margin is wider still at higher outputs, where the gain is steeper.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RampConfig:
    #: Default sweep rate.  See the module docstring for why this is well under
    #: what the output rate limiter can physically deliver.
    rate_k_per_min: float = 0.5
    #: Refuse anything faster than this.  A sweep the loop cannot follow shows
    #: up as a sustained large error, which the supervisor correctly reads as a
    #: broken premise -- so an over-fast ramp does not run away, it stalls the
    #: loop.  Better to reject it at the point of request.
    max_rate_k_per_min: float = 5.0

    #: Low-pass applied to the ramp before the PID sees it.
    #:
    #: A linear ramp has a discontinuous derivative at both ends.  The loop
    #: cannot follow a corner, so it lags into the ramp and then overshoots out
    #: of it -- and no retuning fixes that, because the demand itself is not
    #: physically realisable.  Rounding the corners costs a little tracking lag
    #: and removes the overshoot.  0 disables smoothing.
    #: 300 s measured best against the 620 s response: overshoot falls from
    #: 464 mK to 25 mK on a 3 K sweep, and becomes independent of sweep rate
    #: (25 mK at 0.3, 0.6 and 1.2 K/min alike), which is the signature of a
    #: trajectory the loop can actually follow.  Smoothing alone gets only part
    #: of that -- it works with the velocity feedforward, not instead of it.
    smooth_tau_s: float = 300.0


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
        rate = self.cfg.rate_k_per_min if rate_k_per_min is None else rate_k_per_min
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
