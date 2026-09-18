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

**There is ONE rate** -- ``max_rate_k_per_min`` -- and everything that moves a
setpoint uses it: a commanded sweep, the approach after a fault, and the
open-loop ramp-down.

What makes one rate possible is that the OUTPUT limit is derived from it rather
than set beside it: ``max_rate_k_per_min / K(T)``, floored where the model has
no opinion.  A rate in percent is a different rate in kelvin at every
temperature, which is why picking a kelvin rate to fit a percent rate can only
ever be right in one place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RampConfig:
    #: **THE ONE RATE** (Jeff): the default sweep rate, the rate a post-fault
    #: approach walks in at, the rate the open-loop fault ramp-down descends
    #: at, and the ceiling on anything a client asks for.  It is a SAFETY
    #: ceiling and not a target -- see docs/ltspm3/requirements.md.
    max_rate_k_per_min: float = 5.0

    #: Corner rounding, **as a multiple of the loop's own dead time**.
    #:
    #: A linear ramp has a discontinuous derivative at both ends.  The loop
    #: cannot follow a corner, so it lags into the ramp and then overshoots out
    #: of it -- and no retuning fixes that, because the demand itself is not
    #: physically realisable.  Rounding the corners costs a little tracking lag
    #: and removes the overshoot.  0 disables smoothing.
    #:
    #: The corner length itself is the closed-loop response time,
    #: ``move_speed * tau(T)`` -- ``HeaterSupervisor._smooth_tau_s``, which is
    #: where that argument lives.  A FLAT corner cannot serve this cryostat:
    #: long enough to help at the warm end, it swallows the commanded rate
    #: whole at the cold one, and the sweep runs at whatever the corner lets
    #: through rather than at the rate it was given.
    #:
    #: **This field is only the FLOOR under that**, in dead times, for the cold
    #: end where ``tau`` is shorter than the measurement and nothing else can
    #: set the corner.  Eight is measured: a move at the cold end overshoots
    #: tens of percent at four dead times and a fraction of one at eight, and
    #: at the warm end the closed-loop term binds and nothing notices.
    smooth_delays: float = 8.0

    #: **When the smoothed setpoint counts as ARRIVED**, in kelvin.
    #:
    #: A first-order smoother approaches its target exponentially and never
    #: reaches it, so "has it stopped" has to be asked with a tolerance, and
    #: the tolerance has to be in the units of the thing being asked about.
    #: Until 2026-09-18 it was ``abs(rate) < 1e-9`` -- an epsilon meaning "the
    #: arithmetic has underflowed", used as a settle criterion.  That is
    #: ~18 time constants from a 2 K move, and because it is absolute it took
    #: LONGER for a bigger move (16.9 tau for 0.5 K, 19.8 for 10 K) for no
    #: physical reason.  Measured on the cryostat: 9.3 minutes of `move` gains
    #: after a 2 K move that had been inside 50 mK for seven of them.
    #:
    #: 5 mK is a tenth of ``tuning.hold_error_k``, the tolerance the move is
    #: graded against; it is under the thermometer's own noise; and through the
    #: hold gains it is a sixth of one DAC step, so the setpoint motion still
    #: to come when this declares arrival cannot move the heater at all.
    settled_k: float = 0.005


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

    def __init__(self, tau_s: float = 150.0, *, value: float | None = None,
                 settled_k: float = RampConfig.settled_k) -> None:
        self.tau_s = tau_s
        self.value = value
        #: The last target handed to :meth:`update`.  Kept so `settled` can ask
        #: how far there is still to go, which is the question, rather than how
        #: fast it is going, which was only ever a proxy for it.
        self.target: float | None = None
        self.settled_k = settled_k
        self.rate_k_per_s = 0.0
        self._last_t: float | None = None

    def reset(self, value: float | None = None) -> None:
        self.value = value
        self.target = None
        self.rate_k_per_s = 0.0
        self._last_t = None

    @property
    def settled(self) -> bool:
        """Has the smoothed setpoint ARRIVED?  `RampConfig.settled_k`.

        **In kelvin, not in kelvin per second.**  This was
        ``abs(self.rate_k_per_s) < 1e-9`` until 2026-09-18: an absolute epsilon
        on a rate that decays exponentially, so it was reached only after
        ~18 time constants and took longer for a larger move.  Four things read
        it, and the delay reached all of them -- the phase the gains are chosen
        from, the `ramping` flag the status publishes, the velocity
        feedforward, and rule 4's kelvin premise rows, which stay switched off
        while the setpoint is moving.  Measured on the cryostat, that was
        9.3 minutes of `move` gains after a 2 K move which had settled inside
        50 mK after two.

        A smoother that has been `reset` and not yet updated is holding
        nothing, and says so.
        """
        if self.target is None or self.value is None:
            return True
        return abs(self.target - self.value) <= self.settled_k

    @property
    def rate_underflowed(self) -> bool:
        """The OLD `settled`, kept for one caller, and it is not a settle test.

        ``abs(rate) < 1e-9`` is reached about 18 time constants after a move --
        longer for a bigger one, because the threshold is absolute and the
        decay is exponential.  Three of the four things that used to read it
        wanted :attr:`settled` and are better for the change.  The fourth is
        the HOLD/MOVE gain switch, and that one turned out to be leaning on the
        delay rather than on the question: relaxing `kp` by 7.1x while the
        plant is still converging costs tens of millikelvin, and WHEN it costs
        them depends on where in the residual transient the switch lands.
        Swept on the bench 2026-09-18 over 380-480 s, the 0.5 / 2 / 10 K moves
        pass and fail in no order at all -- 420 s fixes the small move and
        breaks the large one, 440 s breaks the small one again.

        So this stays, quarantined to that one call site and named for what it
        actually is, until the gain change is made gradual instead of stepped.
        Picking a number out of that sweep would be tuning it into passing.
        -> PID_PLAN.md, phase 4.
        """
        return abs(self.rate_k_per_s) < 1e-9

    def update(self, t: float, target: float) -> float:
        self.target = target
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
