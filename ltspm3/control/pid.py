"""PID sized for millikelvin trim, not for slewing a cryostat.

Two departures from a textbook PID, both driven by this cryostat:

* **Derivative on a regressed slope, not on the error.**  Differencing a 10 mK-rms
  signal at 4 s would produce 3.5 mK/s of pure noise; the caller supplies a
  least-squares dT/dt instead (see :class:`~ltspm3.control.filters.SlopeEstimator`).
  Taking it on the measurement rather than the error also removes setpoint kick.

* **Integral clamped in output units.**  ``Ki * integral`` is limited directly to
  the authority band, so the integral alone can never demand more than the
  supervisor would allow -- windup cannot survive a long clamp.

Gains are in output percent per kelvin.  With the local gain of ~10.0 K/% at
the 63% operating point, a Kp of 0.02 %/K is a loop gain of ~0.2 -- gentle on
purpose.  (The 7.6 K/% quoted here previously came from the superseded n = 5
fit.  Up at 66.6% the measured gain is ~13.8 K/%, so the same Kp is a loop gain
near 0.28 there -- which is why the gains are SCHEDULED and not fixed.)
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
    ff: float = 0.0            # positional feedforward (setpoint curve)
    vff: float = 0.0           # velocity feedforward (commanded ramp rate)
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

    def __init__(self, config: PIDConfig | None = None, feedforward=None,
                 ff_limit_pct: float | None = None) -> None:
        self.cfg = config or PIDConfig()
        self.integral = 0.0          # in kelvin-seconds
        self.bias = 0.0              # the output at handover
        self.feedforward = feedforward
        #: Hard bound on the feedforward contribution.  The steady-state curve
        #: is calibrated for ONE regime (cooler running, shields cold); with the
        #: cooler off the same percent means something else entirely.  The
        #: incremental form below already cancels any whole-curve *offset*, so
        #: what a wrong regime costs is a wrong local *slope* -- and this caps
        #: how far that can push the output before the integral corrects it.
        self.ff_limit_pct = ff_limit_pct
        #: Velocity feedforward, in percent, set by the supervisor while a
        #: setpoint ramp is running.  Following a ramp of rate r on a first-order response
        #: ``K/(1+tau s)`` needs a *sustained* extra drive of ``r*tau/K`` on top
        #: of the steady-state output -- without it the loop lags, the integral
        #: winds up to supply the lag, and then unwinds past target when the
        #: ramp stops.  That is where a ramp's overshoot comes from, and no
        #: amount of retuning removes it: the information needed is the
        #: commanded trajectory, which only the caller has.
        self.velocity_ff_pct = 0.0
        self._ff_at_prime = 0.0
        self.ff_clamped = False
        self.terms = PIDTerms()

    def _ff(self, setpoint: float) -> float:
        """Feedforward as a *difference* from its value at prime.

        Differencing is what makes this robust to the calibration being taken
        in a different regime: an offset in the curve cancels identically, and
        only its local slope matters.
        """
        if self.feedforward is None or not self.feedforward.enabled:
            return 0.0
        delta = self.feedforward.percent_for(setpoint) - self._ff_at_prime
        limit = self.ff_limit_pct
        if limit is not None and abs(delta) > limit:
            self.ff_clamped = True
            return limit if delta > 0 else -limit
        self.ff_clamped = False
        return delta

    # -- bumpless handover -------------------------------------------------

    def set_gains(self, kp: float, ti: float) -> None:
        """Retune without bumping the output.

        Two things move when the gains change, and both have to be absorbed:

        * the integral is stored in kelvin-seconds but contributes ``ki * I``
          percent, so its contribution scales with the new ``ki``;
        * the proportional term is ``kp * error``, so with any standing error a
          change in ``kp`` steps the output directly.  Scheduling from the HOLD
          tuning to the MOVE tuning is a 10x change in ``kp``; against a 3 K
          error that is a 0.54% step, which on this cryostat is several kelvin.

        So the integral is re-solved to hold ``P + I`` fixed across the change.
        Gain scheduling is only safe if it is invisible in the output.
        """
        error = self.terms.error
        before = self.cfg.kp * error + self.cfg.ki * self.integral

        self.cfg.kp, self.cfg.ti = kp, ti
        new_ki = self.cfg.ki
        if new_ki > 0:
            self.integral = (before - kp * error) / new_ki
            cap = self._integral_cap() / new_ki
            self.integral = max(-cap, min(cap, self.integral))
        else:
            self.integral = 0.0

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

        ff = self._ff(cfg.setpoint)
        vff = self.velocity_ff_pct
        unclamped = self.bias + ff + vff + p + i + d
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
            p=p, i=i, d=d, ff=ff, vff=vff, error=error,
            unclamped=unclamped, output=output, saturated=saturated,
        )
        return self.terms
