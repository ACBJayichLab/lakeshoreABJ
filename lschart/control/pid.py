"""PID sized for millikelvin trim, not for slewing a cryostat.

Two departures from a textbook PID, both driven by this rig:

* **Derivative on a regressed slope, not on the error.**  Differencing a 10 mK-rms
  signal at 4 s would produce 3.5 mK/s of pure noise; the caller supplies a
  least-squares dT/dt instead (see :class:`~lschart.control.filters.SlopeEstimator`).
  Taking it on the measurement rather than the error also removes setpoint kick.

* **Integral clamped in output units.**  ``Ki * integral`` is limited directly to
  the authority band, so the integral alone can never demand more than the
  supervisor would allow -- windup cannot survive a long clamp.

Gains are in output percent per kelvin.  With a local plant gain near 7.6 K/%,
a Kp of 0.02 %/K is a loop gain of ~0.15 -- gentle on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PIDConfig:
    kp: float = 0.02          # %/K
    ti: float = 900.0         # s   integral time (0 disables integral action)
    td: float = 0.0           # s   derivative time (0 disables)
    setpoint: float = 96.0    # K
    out_min: float = 0.0      # %   set by the supervisor from its authority band
    out_max: float = 100.0    # %
    integral_limit_pct: float | None = None  # |Ki*I| cap; defaults to the band

    @property
    def ki(self) -> float:
        return self.kp / self.ti if self.ti > 0 else 0.0

    @property
    def kd(self) -> float:
        return self.kp * self.td


@dataclass
class PIDTerms:
    p: float = 0.0
    i: float = 0.0
    d: float = 0.0
    error: float = 0.0
    unclamped: float = 0.0
    output: float = 0.0
    saturated: bool = False


class PID:
    """``output = bias + (ff(setpoint) - ff(setpoint_at_prime)) + P + I + D``

    ``bias`` is whatever the heater was doing when control was handed over, so
    the loop starts bumpless.  The feedforward term contributes *nothing* at
    that instant (it is differenced against its own value at prime) and grows
    only as the setpoint moves -- so it never disturbs a steady hold, and it
    supplies the bulk of the output change during a sweep, which a gain this
    gentle would otherwise take an integral time to build.
    """

    def __init__(self, config: PIDConfig | None = None, feedforward=None) -> None:
        self.cfg = config or PIDConfig()
        self.integral = 0.0          # in kelvin-seconds
        self.bias = 0.0              # the output at handover
        self.feedforward = feedforward
        self._ff_at_prime = 0.0
        self.terms = PIDTerms()

    def _ff(self, setpoint: float) -> float:
        if self.feedforward is None or not self.feedforward.enabled:
            return 0.0
        return self.feedforward.percent_for(setpoint) - self._ff_at_prime

    # -- bumpless handover -------------------------------------------------

    def prime(self, output: float) -> None:
        """Make the controller currently demand exactly ``output``.

        Called whenever control resumes -- switching from manual, or re-arming
        after a fault -- so the heater never steps on handover.  The
        feedforward reference is re-zeroed here too, which is what keeps the
        handover bumpless no matter how far the setpoint has since moved.
        """
        self.bias = output
        self.integral = 0.0
        if self.feedforward is not None and self.feedforward.enabled:
            self._ff_at_prime = self.feedforward.percent_for(self.cfg.setpoint)
        self.terms = PIDTerms(output=output)

    def set_setpoint(self, kelvin: float) -> None:
        self.cfg.setpoint = kelvin

    # -- the loop ----------------------------------------------------------

    def _integral_cap(self) -> float:
        if self.cfg.integral_limit_pct is not None:
            return abs(self.cfg.integral_limit_pct)
        return abs(self.cfg.out_max - self.cfg.out_min)

    def update(self, measurement: float, slope: float, dt: float) -> PIDTerms:
        """``slope`` is dT/dt in K/s from the filter, already smoothed."""
        cfg = self.cfg
        error = cfg.setpoint - measurement

        p = cfg.kp * error

        # Integrate first, then decide whether to keep it: conditional
        # integration plus a hard clamp in output units.
        if cfg.ki > 0 and dt > 0:
            candidate = self.integral + error * dt
            cap = self._integral_cap() / cfg.ki
            self.integral = max(-cap, min(cap, candidate))
        i = cfg.ki * self.integral

        # Derivative acts against the *rate of the measurement*, so a rising
        # temperature always subtracts heat regardless of the setpoint.
        d = -cfg.kd * slope

        unclamped = self.bias + self._ff(cfg.setpoint) + p + i + d
        output = max(cfg.out_min, min(cfg.out_max, unclamped))
        saturated = output != unclamped

        # Back-calculation: unwind the integral by exactly the amount the clamp
        # rejected, so it does not keep charging while the output sits pinned.
        if saturated and cfg.ki > 0:
            self.integral += (output - unclamped) / cfg.ki
            cap = self._integral_cap() / cfg.ki
            self.integral = max(-cap, min(cap, self.integral))
            i = cfg.ki * self.integral

        self.terms = PIDTerms(
            p=p, i=i, d=d, error=error,
            unclamped=unclamped, output=output, saturated=saturated,
        )
        return self.terms
