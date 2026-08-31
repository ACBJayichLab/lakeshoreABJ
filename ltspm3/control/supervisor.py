"""The safety envelope around the sample-heater PID.

Everything in this file exists to answer one question: *is it safe to move the
heater right now, and by how much?*  The PID only ever proposes; the supervisor
disposes.

The layers, outermost first -- a proposal must survive all of them:

1. **Mode.**  ``OFF`` writes nothing at all, ever.
2. **Sensor health.**  A single doubtful reading freezes the output.  Sustained
   failure ramps down.  Nothing raises the heater in response to a fault.
3. **Premise checks.**  This loop is specified for millikelvin trim.  If the
   error exceeds ``max_error_k``, or the PID suddenly wants ``anomaly_demand_pct``
   more output than it currently has, the premise is broken -- something is wrong
   with the cryostat, not with the control -- so hold, and ramp down if it persists.
4. **Authority band.**  A hard clamp to ``operating_point +/- authority_pct``,
   intersected with an absolute never-exceed range.  However wrong everything
   else goes, the heater cannot leave this window.
5. **Rate limit.**  Per-update step and per-minute rate caps.
6. **Dither.**  Sub-code resolution, since one 0.01% code is ~76 mK here.
7. **Readback verification.**  ``AOUT?`` must agree with what we sent.

Defaults are chosen so that the worst thing an unattended failure can do is
slowly reduce heat.
"""

from __future__ import annotations

import enum
import logging
import math
import time
from dataclasses import dataclass, field

from lschart.model import Reading, Validity
from lschart.transport import TransportError
from .coherence import CoherenceConfig, CoherenceMonitor
from .dither import SigmaDeltaDither
from .feedforward import Feedforward, FeedforwardConfig
from .filters import MeasurementFilter
from .health import HealthState, SensorGuard, SensorGuardConfig
from .pid import PID, PIDConfig
from .ramp import RampConfig, SetpointRamp, SetpointSmoother
from .tuning import Tuner, TuningConfig

log = logging.getLogger(__name__)


class LoopMode(enum.Enum):
    OFF = "off"        # never writes to the instrument
    MANUAL = "manual"  # operator sets the value; still clamped and rate limited
    PID = "pid"        # closed loop


class SupervisorState(enum.Enum):
    IDLE = "idle"
    TRACKING = "tracking"          # closed loop, healthy
    HOLDING = "holding"            # output frozen pending clarity
    RAMPING_DOWN = "ramping_down"  # sustained fault -> slowly back off the heat
    LOCKED_OUT = "locked_out"      # ramp complete; needs an operator acknowledge


@dataclass
class SupervisorConfig:
    """All limits are in output percent unless the name says kelvin.

    The defaults assume the cryostat's measured operating point (~63.1% for ~96 K)
    and the local gain of ~10.0 K/% there.  At that gain the 1.0% authority band
    is about +/-10 K of ultimate authority, and ``max_step_pct`` of 0.02 is about
    200 mK of movement per 4 s update -- generous for trim, useless for damage.

    These scale with the gain, which is NOT constant: ~13.8 K/% was measured at
    66.6%, so the same band is worth ~+/-14 K up there.  Re-centre the operating
    point on the output that actually holds the intended temperature, and
    consider narrowing ``authority_pct`` with it.
    """

    operating_point_pct: float = 63.076
    authority_pct: float = 1.0
    hard_min_pct: float = 0.0
    hard_max_pct: float = 70.0

    max_step_pct: float = 0.02
    max_rate_pct_per_min: float = 0.20

    # Premise checks -- "this should only ever be a small correction".
    max_error_k: float = 1.0
    #: A one-cycle lurch in the *feedback* terms (P+I+D) this large means a bad
    #: reading is driving the loop, not that the cryostat needs the output.
    anomaly_demand_pct: float = 0.50
    anomaly_hold_s: float = 180.0

    #: Dominant thermal time constant.  620 s is measured, but from the *one*
    #: clean step response the reference logs contain (65.9% -> 137.3 K); every
    #: other command there is a sub-2 K trim.  Heat capacity varies with
    #: temperature, so this certainly does too -- treat it as provisional and
    #: re-measure with a deliberate step test.  A
    #: first-order response asked to follow a setpoint ramp of rate r settles at a
    #: tracking error of exactly r * tau -- at 0.5 K/min that is 3 K, which
    #: would trip max_error_k on every legitimate sweep.  So while a ramp is in
    #: progress the premise check is widened by the lag the ramp itself
    #: commands, and by nothing else.  When not ramping the allowance is zero
    #: and the check is exactly as strict as before.
    response_lag_s: float = 620.0
    #: Ceiling on that allowance, so a fast ramp cannot blind the check entirely.
    max_ramp_error_k: float = 6.0

    #: Hard cap on the feedforward contribution.  The steady-state curve is
    #: calibrated for one regime -- cooler running, shields cold.  With the
    #: cooler off (before a cooldown, after a warmup) the same percent produces
    #: a very different temperature, and a temperature log alone cannot tell
    #: those regimes apart.  Feedforward enters incrementally so a whole-curve
    #: offset cancels; this bounds what a wrong local *slope* can do.
    max_feedforward_pct: float = 0.40
    #: Velocity feedforward gets its own, larger budget.  It is derived from a
    #: trajectory *we commanded*, not from extrapolating a curve, so it is a
    #: different kind of risk -- and following a ramp of rate r genuinely needs
    #: r*tau/K, which at 0.6 K/min and tau=620 s is already 0.6%.  Capping it at
    #: the positional limit does not prevent the drive, it just makes the
    #: integral supply it instead and then overshoot when the ramp ends.
    max_velocity_ff_pct: float = 1.00
    #: Once settled, the measurement should agree with kelvin_for(output).  If
    #: it disagrees by more than this the calibration does not describe the
    #: present regime -- say so loudly rather than quietly trusting it.
    model_trust_k: float = 15.0
    #: "Settled" for that check: slope below this and no ramp in progress.
    model_check_slope_k_per_s: float = 0.002
    #: Re-arming after a fault ramp-down is the hardest approach: the cryostat is
    #: still settling toward the output the ramp-down left it at, so the gap
    #: grows for a while before it closes.  Approach more gently than a sweep.
    approach_rate_k_per_min: float = 0.25

    # Fault response.
    rampdown_pct_per_min: float = 0.50
    safe_output_pct: float = 0.0
    require_ack_after_fault: bool = True

    # Instrument interaction.
    dac_step_pct: float = 0.01
    dither: bool = True
    verify_readback: bool = True
    readback_tol_pct: float = 0.015
    comms_fault_after_s: float = 60.0

    #: What to do when the program exits.  "hold" leaves the heater exactly where
    #: it is, which is almost always right -- zeroing a sample heater on a live
    #: cryostat is its own hazard.
    on_exit: str = "hold"


@dataclass
class SupervisorStatus:
    t: float = 0.0
    mode: LoopMode = LoopMode.OFF
    state: SupervisorState = SupervisorState.IDLE
    health: HealthState = HealthState.UNKNOWN
    raw_k: float | None = None
    filtered_k: float | None = None
    slope_k_per_s: float = 0.0
    noise_k: float = 0.0
    setpoint_k: float = 0.0          # what the PID is chasing right now
    setpoint_target_k: float = 0.0   # where the ramp is heading
    ramping: bool = False
    error_k: float | None = None
    demand_pct: float | None = None     # what the PID asked for, unclamped
    target_pct: float | None = None     # after every limit, before dithering
    output_pct: float | None = None     # the code actually written
    readback_pct: float | None = None
    validity: Validity = Validity.GOOD
    corroborated: bool | None = None
    #: measured - model, once settled.  None while ramping or still moving.
    model_error_k: float | None = None
    model_trusted: bool = True
    phase: str = "hold"
    kp: float = 0.0
    ti: float = 0.0
    velocity_ff_pct: float = 0.0
    reason: str = ""
    alarms: list[str] = field(default_factory=list)
    wrote: bool = False


class HeaterSupervisor:
    """Owns the heater output.  Nothing else should write to the analog output."""

    def __init__(
        self,
        instrument,                      # LS218 (duck-typed: set/get_analog_percent)
        *,
        channel: str,
        config: SupervisorConfig | None = None,
        pid_config: PIDConfig | None = None,
        guard_config: SensorGuardConfig | None = None,
        coherence_config: CoherenceConfig | None = None,
        ramp_config: RampConfig | None = None,
        feedforward_config: FeedforwardConfig | None = None,
        tuning_config: TuningConfig | None = None,
        filter_kwargs: dict | None = None,
        clock=time.monotonic,
    ) -> None:
        self.inst = instrument
        self.channel = channel
        self.cfg = config or SupervisorConfig()
        self.guard = SensorGuard(guard_config, name=channel)
        self.coherence = CoherenceMonitor(coherence_config)
        self.filter = MeasurementFilter(**(filter_kwargs or {}))
        self.clock = clock

        self.feedforward = Feedforward(feedforward_config)
        self.tuner = Tuner(tuning_config)
        self.pid = PID(
            pid_config or PIDConfig(),
            feedforward=self.feedforward,
            ff_limit_pct=self.cfg.max_feedforward_pct,
        )
        self.ramp = SetpointRamp(self.pid.cfg.setpoint, ramp_config)
        self.smoother = SetpointSmoother(self.ramp.cfg.smooth_tau_s,
                                         value=self.pid.cfg.setpoint)
        self._apply_band_to_pid()

        self.dither = SigmaDeltaDither(self.cfg.dac_step_pct)
        self.mode = LoopMode.OFF
        self.state = SupervisorState.IDLE
        self.status = SupervisorStatus()

        self.output_pct: float | None = None   # last value we commanded
        self.manual_pct: float = self.cfg.operating_point_pct
        self._anomaly_since: float | None = None
        self._comms_bad_since: float | None = None
        self._last_t: float | None = None
        self._locked_reason = ""
        self._rampdown_complete = False
        self._ramp_allowance_k = 0.0
        self._pending_approach = False
        self._model_warned = False
        self._last_feedback = 0.0

    # -- authority band ----------------------------------------------------

    @property
    def band(self) -> tuple[float, float]:
        c = self.cfg
        lo = max(c.hard_min_pct, c.operating_point_pct - c.authority_pct)
        hi = min(c.hard_max_pct, c.operating_point_pct + c.authority_pct)
        if lo > hi:
            raise ValueError(
                f"empty authority band: operating point {c.operating_point_pct} "
                f"is outside hard limits [{c.hard_min_pct}, {c.hard_max_pct}]"
            )
        return lo, hi

    def _apply_band_to_pid(self) -> None:
        lo, hi = self.band
        self.pid.cfg.out_min = lo
        self.pid.cfg.out_max = hi

    def clamp(self, pct: float) -> float:
        lo, hi = self.band
        return max(lo, min(hi, pct))

    # -- operator controls -------------------------------------------------

    def arm(self, setpoint_k: float) -> None:
        """Adopt ``setpoint_k`` and close the loop.

        The controller half of :meth:`lschart.app.Application.arm`, which owns
        deciding *what* to arm to but must not know what a :class:`LoopMode` is.
        Stepped, not ramped: the caller has already established that this is
        where the cryostat is now, so there is nothing to traverse.
        """
        self.set_setpoint(setpoint_k, ramp=False)
        self.set_mode(LoopMode.PID)

    def set_mode(self, mode: LoopMode) -> None:
        if self.state is SupervisorState.LOCKED_OUT and mode is not LoopMode.OFF:
            raise PermissionError(
                f"supervisor is locked out ({self._locked_reason}); acknowledge() first"
            )
        if mode is self.mode:
            return
        log.warning("heater mode %s -> %s", self.mode.value, mode.value)
        self.mode = mode
        if mode is LoopMode.PID:
            # Bumpless: start from wherever the heater actually is, *now* --
            # not from where the PID last thought it was.  After a fault
            # ramp-down those differ by the whole ramp, and priming from the
            # stale value makes the first demand a phantom step of that size,
            # which the anomaly check then reads as a broken premise.
            current = self._read_output(default=self.output_pct)
            if current is None:
                current = self.cfg.operating_point_pct
            self.output_pct = current
            self.pid.prime(self.clamp(current))
            self._last_feedback = 0.0
            self.smoother.reset(self.pid.cfg.setpoint)
            self.dither.reset()
            # Approach the target from wherever the cryostat actually is.  After a
            # fault ramp-down the cryostat can be many kelvin away, and that gap is
            # not an anomaly -- it is the thing we are arming in order to close.
            # Approach the target from wherever the cryostat actually is.  This is
            # deferred rather than done here: acknowledge() resets the filter,
            # so at this instant there is usually no measurement to ramp from,
            # and starting from the stale target reopens the loop with exactly
            # the error the ramp exists to avoid.
            self._pending_approach = True
            self.state = SupervisorState.TRACKING
        elif mode is LoopMode.MANUAL:
            self.manual_pct = self.output_pct if self.output_pct is not None else self.manual_pct
            self.state = SupervisorState.IDLE
        else:
            self.state = SupervisorState.IDLE
        self._anomaly_since = None

    def set_setpoint(
        self,
        kelvin: float,
        *,
        ramp: bool = True,
        rate_k_per_min: float | None = None,
    ) -> None:
        """Change the target temperature.

        Ramps by default.  A step change of more than ``max_error_k`` is
        indistinguishable from a broken premise, so stepping the setpoint is
        how you stall the loop rather than how you move it -- see
        :mod:`ltspm3.control.ramp`.  ``ramp=False`` is for small trims.
        """
        # An explicit setpoint command takes charge of the trajectory: the
        # deferred post-fault approach must not silently re-rate an operator's
        # sweep to approach_rate_k_per_min behind their back.
        self._pending_approach = False
        if not ramp:
            # A step means a step: bypass the smoother too.  Otherwise
            # ramp=False silently becomes a gentle approach, and the premise
            # check -- whose whole job is to refuse a setpoint the loop was
            # never asked to reach gradually -- never sees the error.
            self.ramp.jump_to(kelvin)
            self.smoother.reset(kelvin)
        else:
            t = self.clock()
            # Ramp from the measurement, not the old setpoint: if the cryostat has
            # drifted (a fault ramp-down, say) starting from the stale setpoint
            # opens with exactly the error the ramp exists to avoid.
            here = self.filter.value if self.filter.primed else None
            self.ramp.start(t, kelvin, from_k=here, rate_k_per_min=rate_k_per_min)
        log.warning(
            "setpoint -> %.4f K (%s)", kelvin,
            "ramping" if self.ramp.ramping else "immediate",
        )

    def sweep_to(self, kelvin: float, rate_k_per_min: float) -> None:
        """Programmatic sweep.  Same mechanism as any other setpoint move."""
        self.set_setpoint(kelvin, ramp=True, rate_k_per_min=rate_k_per_min)

    def abort_ramp(self) -> float:
        """Stop a sweep where it stands and hold that temperature.

        Holds the *smoothed* setpoint -- the value the loop is actually
        chasing -- not the raw ramp position it was lagging behind.
        """
        t = self.clock()
        self.ramp.abort(t)
        held = self.smoother.value if self.smoother.value is not None else self.ramp.target
        self.ramp.jump_to(held)
        self.smoother.reset(held)
        self.pid.cfg.setpoint = held
        log.warning("ramp aborted, holding %.4f K", held)
        return held

    def panic_hold(self) -> float:
        """Stop regulating and leave the heater exactly where it is.

        The controller half of ``lschart``'s ``hold`` command, and the one seam
        the file interface reaches into this package by -- called duck-typed by
        name, so ``lschart`` still never imports ``ltspm3``.

        Composed rather than invented: :meth:`abort_ramp` stops any sweep where
        it stands, and ``set_mode(MANUAL)`` adopts the output the heater is
        currently at.  Both already existed; what did not was a single name for
        the pair, and a panic action assembled from three calls at the call
        site is one that can be assembled wrong.

        **The clamp and the rate limiter still apply.**  Manual mode is not raw
        access to the DAC -- every value still passes ``clamp`` and
        ``_rate_limit`` on the way out, which is exactly why this goes through
        the supervisor rather than around it.

        Note this holds a *power*, not a temperature.  Nothing regulates the
        sample afterwards, so it will drift with the cryostat -- which is the
        opposite of what "hold" means on a 33x loop, and is why the viewer says
        so out loud before doing it.  :meth:`arm` is the way back.

        Returns the percentage being held.
        """
        self.abort_ramp()
        self.set_mode(LoopMode.MANUAL)
        held = self.manual_pct
        log.warning("PANIC HOLD: loop open, heater frozen at %.3f%%", held)
        return held

    def set_manual_percent(self, pct: float) -> None:
        """Request a manual output.  Still clamped and rate limited on the way out."""
        self.manual_pct = pct

    def acknowledge(self) -> None:
        """Clear a lockout after the operator has looked at the cryostat.

        This disarms the loop (``mode -> OFF``) rather than resuming it.  Two
        reasons: the operator asked for recovery to always be deliberate, and
        ``set_mode`` short-circuits when the mode is unchanged -- so leaving
        the mode at PID meant the subsequent re-arm never re-primed the PID,
        and the loop simply locked out again a few minutes later.
        """
        log.warning("operator acknowledged lockout: %s", self._locked_reason)
        self.mode = LoopMode.OFF
        self.state = SupervisorState.IDLE
        self._locked_reason = ""
        self._anomaly_since = None
        self._comms_bad_since = None
        self._rampdown_complete = False
        self.guard.reset()
        self.filter.reset()
        self.coherence.reset()

    # -- instrument I/O ----------------------------------------------------

    def _read_output(self, *, default: float | None = None) -> float:
        try:
            value = self.inst.get_analog_percent()
            self._comms_bad_since = None
            return value
        except (TransportError, ValueError) as exc:
            log.warning("could not read heater output: %s", exc)
            if default is None:
                raise
            return default

    def _write_output(self, pct: float, status: SupervisorStatus) -> bool:
        """Write, then prove it landed.  Returns True if the value was sent."""
        try:
            self.inst.set_analog_percent(pct)
            self.output_pct = pct
            self._comms_bad_since = None
        except (TransportError, ValueError) as exc:
            self._note_comms_failure(status, f"write failed: {exc}")
            return False

        if self.cfg.verify_readback:
            try:
                back = self.inst.get_analog_percent()
                status.readback_pct = back
                if abs(back - pct) > self.cfg.readback_tol_pct + self.cfg.dac_step_pct / 2:
                    status.alarms.append(
                        f"readback {back:.3f}% disagrees with commanded {pct:.3f}%"
                    )
            except (TransportError, ValueError) as exc:
                self._note_comms_failure(status, f"readback failed: {exc}")
        return True

    def _note_comms_failure(self, status: SupervisorStatus, message: str) -> None:
        now = self.clock()
        if self._comms_bad_since is None:
            self._comms_bad_since = now
        elapsed = now - self._comms_bad_since
        status.alarms.append(f"{message} ({elapsed:.0f} s)")
        if elapsed >= self.cfg.comms_fault_after_s:
            # We cannot ramp what we cannot reach; make the situation loud.
            status.alarms.append(
                "COMMS LOST: heater is stranded at its last commanded value"
            )
        log.error("heater comms: %s", message)

    # -- the cycle ---------------------------------------------------------

    def step(
        self,
        t: float,
        reading: Reading | None,
        readings: dict[str, Reading] | None = None,
    ) -> SupervisorStatus:
        """Advance one control cycle.  ``t`` is a monotonic timestamp in seconds.

        ``readings`` is the whole frame.  Supplying it lets the supervisor ask
        whether any *other* channel saw the same event, which is the only
        reliable way to tell a fast cooldown from a sick sensor -- see
        :mod:`ltspm3.control.coherence`.  Omitting it degrades gracefully to
        the absolute slew limit.
        """
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t

        self.pid.cfg.setpoint = self.smoother.update(t, self.ramp.value(t))
        s = SupervisorStatus(
            t=t,
            mode=self.mode,
            setpoint_k=self.pid.cfg.setpoint,
            setpoint_target_k=self.ramp.target,
            ramping=self.ramp.ramping or not self.smoother.settled,
        )
        s.raw_k = reading.kelvin if reading is not None else None

        # -- believe the sensor? --------------------------------------------
        if readings:
            self.coherence.update(t, readings)
        corroborated, why = self.coherence.corroboration(self.channel, t)
        s.corroborated = corroborated

        spike = (
            self.filter.is_spike(reading.kelvin, dt, t=t)
            if (reading is not None and reading.usable)
            else False
        )
        guard = self.guard.update(
            t, reading, spike=spike, corroborated=corroborated, corroboration_why=why,
            noise_k=self.filter.noise_estimate(), dt=dt,
        )
        s.health = guard.state
        s.validity = guard.validity
        s.reason = guard.reason

        if guard.validity.good and guard.kelvin is not None:
            if self.filter.is_stale(t):
                # The cryostat has had time to move since the last accepted sample.
                # Continuing from the frozen value would make every honest
                # reading look like an outlier, and a rejected reading never
                # refreshes the reference -- a deadlock nothing recovers from.
                log.warning("%s: filter stale, reseeding at %.4f K", self.channel, guard.kelvin)
                self.filter.reseed(t, guard.kelvin)
                s.filtered_k, s.slope_k_per_s = guard.kelvin, 0.0
            else:
                filtered, slope = self.filter.update(t, guard.kelvin, dt)
                s.filtered_k, s.slope_k_per_s = filtered, slope
        else:
            s.filtered_k = self.filter.value
            s.slope_k_per_s = 0.0
            if guard.changed:
                log.warning("%s health -> %s: %s", self.channel, guard.state.value, guard.reason)
        s.noise_k = self.filter.noise_estimate()

        if self.mode is LoopMode.OFF or self.state is SupervisorState.LOCKED_OUT:
            self.state = (
                SupervisorState.LOCKED_OUT
                if self.state is SupervisorState.LOCKED_OUT
                else SupervisorState.IDLE
            )
            s.state = self.state
            s.output_pct = self.output_pct
            self.status = s
            return s

        # -- decide a target -------------------------------------------------
        if self.mode is LoopMode.MANUAL:
            target = self.clamp(self.manual_pct)
            self.state = SupervisorState.IDLE
        else:
            target = self._pid_target(t, s, dt)

        if target is None:                      # hold: re-send nothing, change nothing
            s.state = self.state
            s.output_pct = self.output_pct
            self.status = s
            return s

        # -- rate limit ------------------------------------------------------
        current = self.output_pct
        if current is None:
            current = self._read_output(default=self.clamp(self.cfg.operating_point_pct))

        if self.state is SupervisorState.RAMPING_DOWN:
            # A ramp-down has to be able to leave the authority band -- otherwise
            # it can never reach safe_output_pct.  Its rate is already set by
            # rampdown_pct_per_min in _rampdown_target, so the trim-sized limiter
            # (which is ~30x slower) must not apply on top of it.  Only the
            # absolute hard limits still hold.
            target = max(self.cfg.hard_min_pct, min(self.cfg.hard_max_pct, target))
        else:
            target = self._rate_limit(current, target, dt)
            target = self.clamp(target)
        s.target_pct = target

        # -- quantise and write ----------------------------------------------
        code = self.dither.quantise(target) if self.cfg.dither else round(
            target / self.cfg.dac_step_pct
        ) * self.cfg.dac_step_pct
        code = max(self.cfg.hard_min_pct, min(self.cfg.hard_max_pct, code))

        # Re-apply the band AFTER quantising.  Rounding to the nearest code can
        # land above a target that was itself inside the band, and the band is
        # specified as an unconditional cap on heat -- half a code of overshoot
        # is still overshoot.  Stepping down a code (rather than clamping to a
        # non-code value) keeps the output on the DAC grid.
        if self.state is not SupervisorState.RAMPING_DOWN:
            _, band_hi = self.band
            while code > band_hi + 1e-9:
                code -= self.cfg.dac_step_pct
            code = max(code, self.cfg.hard_min_pct)

        if self.output_pct is None or abs(code - self.output_pct) >= self.cfg.dac_step_pct / 2:
            s.wrote = self._write_output(code, s)
        else:
            self.output_pct = code
        if self._rampdown_complete and self.cfg.require_ack_after_fault:
            self.state = SupervisorState.LOCKED_OUT
            self._rampdown_complete = False
            log.error("heater ramp-down complete; locked out pending acknowledge()")

        s.output_pct = self.output_pct
        s.state = self.state
        self.status = s
        return s

    # -- PID branch, with the premise checks ------------------------------

    def _pid_target(self, t: float, s: SupervisorStatus, dt: float) -> float | None:
        """Return the desired output, or ``None`` meaning 'hold, change nothing'."""
        health = s.health

        if health is HealthState.FAULT:
            return self._rampdown_target(t, s, "sensor fault", dt)

        if health is not HealthState.OK or not self.filter.primed:
            self.state = SupervisorState.HOLDING
            s.alarms.append(f"holding: sensor {health.value}")
            return None

        assert s.filtered_k is not None

        if self._pending_approach:
            # First trustworthy measurement since arming: walk the setpoint in
            # from here rather than presenting the loop with the whole gap.
            self._pending_approach = False
            gap = self.ramp.target - s.filtered_k
            if abs(gap) > self.cfg.max_error_k:
                self.ramp.start(
                    t, self.ramp.target, from_k=s.filtered_k,
                    rate_k_per_min=self.cfg.approach_rate_k_per_min,
                )
                # Put the ramped setpoint in force *before* priming.  prime()
                # captures the feedforward reference at the setpoint then in
                # effect, so priming against the old target makes the
                # feedforward term open negative and drive the heater the wrong
                # way for the whole approach.
                self.smoother.reset(s.filtered_k)
                s.setpoint_k = self.pid.cfg.setpoint = self.smoother.update(
                    t, self.ramp.value(t))
                self.pid.prime(self.clamp(
                    self.output_pct if self.output_pct is not None else s.filtered_k
                ))
                log.warning(
                    "arming %+.3f K from target; ramping in at %.2f K/min",
                    gap, self.cfg.approach_rate_k_per_min,
                )

        # Snapshot so a hold can restore the integral bit-for-bit: PID.update
        # both integrates and back-calculates, so an arithmetic "undo" drifts.
        # Snapshot the whole controller state *before* the tuner touches it.
        # A hold must leave the PID exactly as it found it, and rescheduling
        # does move it: set_gains re-solves the integral to preserve P+I, so
        # against a standing error even a tiny change in kp shifts the stored
        # integral by (dkp * error / ki) -- and 1/ki is ~3000 here.
        state_before = (self.pid.integral, self.pid.cfg.kp, self.pid.cfg.ti)

        # Retune for where we are and what we are doing.  Both the gain and the
        # time constant of a weakly-pinned island change with temperature, so a
        # single fixed pair of gains is only ever right at one operating point.
        if self.tuner.enabled:
            phase = self.tuner.update_phase(
                t,
                error_k=self.pid.cfg.setpoint - s.filtered_k,
                ramping=self.ramp.ramping or not self.smoother.settled,
            )
            kp, ti = self.tuner.gains_for(s.filtered_k, phase)
            self.pid.set_gains(kp, ti)
            s.phase = phase.value
        s.kp, s.ti = self.pid.cfg.kp, self.pid.cfg.ti

        # Velocity feedforward: sustain a ramp instead of lagging it.  Uses the
        # scheduled gain and time constant, i.e. exactly the two local numbers
        # a step test measures -- so this gets better as the schedule does.
        vel = 0.0
        if not self.smoother.settled and self.tuner.enabled:
            gain = self.tuner.schedule.gain_at(s.filtered_k)
            tau = self.tuner.schedule.tau_at(s.filtered_k)
            if gain > 0:
                vel = self.smoother.rate_k_per_s * tau / gain
                limit = self.cfg.max_velocity_ff_pct
                vel = max(-limit, min(limit, vel))
        self.pid.velocity_ff_pct = vel
        s.velocity_ff_pct = vel

        terms = self.pid.update(s.filtered_k, s.slope_k_per_s, dt)
        s.demand_pct = terms.unclamped
        s.error_k = terms.error

        anomalies: list[str] = []

        # A commanded ramp buys exactly the lag it commands, and no more.  The
        # allowance decays with the thermal time constant once the ramp stops
        # rather than vanishing at the instant it does: the cryostat is still
        # legitimately catching up then, and a cliff there turned every
        # completed sweep into an anomaly hold.
        # Two contributions, both from moves we asked for: the steady lag of
        # following a ramp (rate * tau), and the size of the excursion still
        # outstanding.  Neither says anything about an *uncommanded* error,
        # which is what the check is actually guarding against.
        commanded = min(
            abs(self.ramp.rate_k_per_s) * self.cfg.response_lag_s + self.ramp.span,
            self.cfg.max_ramp_error_k,
        )
        if dt > 0 and self.cfg.response_lag_s > 0:
            self._ramp_allowance_k *= math.exp(-dt / self.cfg.response_lag_s)
        allowance = self._ramp_allowance_k = max(commanded, self._ramp_allowance_k)
        error_limit = self.cfg.max_error_k + allowance
        if abs(terms.error) > error_limit:
            anomalies.append(
                f"error {terms.error:+.3f} K exceeds max_error_k "
                f"{self.cfg.max_error_k} K"
                + (f" + {allowance:.2f} K ramp allowance" if allowance else "")
            )
        # What this check is for is a *bad reading* driving the loop: the
        # feedback terms lurch in one cycle.  So watch those terms directly.
        #
        # Comparing total demand against the present output (the original form)
        # cannot work alongside feedforward: a commanded ramp legitimately puts
        # ~r*tau/K of extra demand in at once, the rate limiter needs many
        # cycles to apply it, and for all of those cycles demand-minus-output
        # sits above the threshold -- a permanent false anomaly that escalates
        # to a ramp-down.  Discounting only the *change* in feedforward does not
        # fix it, because the un-applied level is what shows up in the gap.
        feedback = terms.p + terms.i + terms.d
        jump = feedback - self._last_feedback
        self._last_feedback = feedback
        if abs(jump) > self.cfg.anomaly_demand_pct:
            anomalies.append(
                f"feedback demand jumped {jump:+.3f}% in one cycle "
                f"(limit {self.cfg.anomaly_demand_pct}%)"
            )

        if anomalies:
            # Whatever we decide below, we are not acting on this PID output, so
            # the integral must not keep charging while the loop refuses to move.
            self.pid.integral, self.pid.cfg.kp, self.pid.cfg.ti = state_before
            self._last_feedback = feedback
            if self._anomaly_since is None:
                self._anomaly_since = t
                log.warning("heater anomaly, holding: %s", "; ".join(anomalies))
            held = t - self._anomaly_since
            s.alarms.extend(anomalies)
            if held >= self.cfg.anomaly_hold_s:
                return self._rampdown_target(t, s, "anomaly persisted", dt)
            self.state = SupervisorState.HOLDING
            s.reason = f"anomaly held {held:.0f}/{self.cfg.anomaly_hold_s:.0f} s"
            return None

        self._anomaly_since = None
        self._check_model(s)
        self.state = SupervisorState.TRACKING
        return terms.output

    def _check_model(self, s: SupervisorStatus) -> None:
        """Is the calibration still describing this cryostat, in this regime?

        The steady-state curve was measured with the cooler running.  Before a
        cooldown or after a warmup it does not apply, and nothing in a
        temperature log distinguishes those cases -- so check it against
        reality instead of assuming.  Only meaningful once settled: during a
        ramp the measurement is *supposed* to lag the model.
        """
        if self.ramp.ramping or self.output_pct is None or s.filtered_k is None:
            return
        if abs(s.slope_k_per_s) > self.cfg.model_check_slope_k_per_s:
            return
        expected = self.feedforward.kelvin_for(self.output_pct)
        s.model_error_k = s.filtered_k - expected
        if abs(s.model_error_k) > self.cfg.model_trust_k:
            s.model_trusted = False
            s.alarms.append(
                f"calibration does not describe this regime: {self.output_pct:.3f}% "
                f"should settle near {expected:.1f} K but reads {s.filtered_k:.1f} K "
                f"({s.model_error_k:+.1f} K). Feedforward is capped at "
                f"{self.cfg.max_feedforward_pct}%; the integral is doing the work."
            )
            if not self._model_warned:
                self._model_warned = True
                log.warning(
                    "temperature does not match the calibration curve (%+.1f K at %.3f%%) -- "
                    "different cooler/vacuum state?  Control continues on feedback.",
                    s.model_error_k, self.output_pct,
                )
        else:
            s.model_trusted = True
            self._model_warned = False

    def _rampdown_target(
        self, t: float, s: SupervisorStatus, why: str, dt: float
    ) -> float | None:
        if self.state is not SupervisorState.RAMPING_DOWN:
            log.error("heater RAMPING DOWN (%s)", why)
            self.state = SupervisorState.RAMPING_DOWN
            self._locked_reason = why
        s.alarms.append(f"ramping down: {why}")

        current = self.output_pct
        if current is None:
            current = self._read_output(default=self.cfg.safe_output_pct)

        # Deliberately slow.  A fault is not an emergency on this cryostat; the risk
        # of a fast change is greater than the risk of a slow one.
        step = self.cfg.rampdown_pct_per_min * (max(dt, 0.0) / 60.0)
        safe = self.cfg.safe_output_pct
        if abs(current - safe) <= step:
            # Reached the safe value: hand it back this cycle and lock out on the
            # next one, so step() still writes it before the early-return kicks in.
            self._rampdown_complete = True
            return safe
        return current - step if current > safe else current + step

    def _rate_limit(self, current: float, target: float, dt: float) -> float:
        step = self.cfg.max_step_pct
        if dt > 0:
            # No `or step` fallback here: that made the limiter loosest exactly
            # when dt was smallest, which is backwards.
            step = min(step, self.cfg.max_rate_pct_per_min * (dt / 60.0))
        delta = target - current
        if abs(delta) <= step:
            return target
        return current + step * (1.0 if delta > 0 else -1.0)

    # -- shutdown ----------------------------------------------------------

    def shutdown(self) -> None:
        if self.cfg.on_exit == "zero":
            log.warning("on_exit=zero: commanding heater to %.3f%%", self.cfg.safe_output_pct)
            try:
                self.inst.set_analog_percent(self.cfg.safe_output_pct)
            except (TransportError, ValueError) as exc:
                log.error("failed to zero heater on exit: %s", exc)
        else:
            log.info("on_exit=hold: leaving heater at %s%%", self.output_pct)
        self.mode = LoopMode.OFF
