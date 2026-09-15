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
4. **Authority band.**  A window ``authority_pct`` wide either side of THE
   OUTPUT THAT HOLDS THE PRESENT SETPOINT, widened while a ramp is running by
   exactly the lead that ramp needs, and intersected with an absolute
   never-exceed range.  It used to be two config constants and it could not
   span 4 to 300 K: 10 K is 24 % of output and 180 K is 69 %, against a window
   one point wide.  See :meth:`HeaterSupervisor.band`.
   The **ceiling** is hard and immediate: however wrong everything else goes,
   the heater cannot go above this window, and ``hard_max_pct`` is the part of
   it that nothing moves.
   The **floor** bounds what the PID may *ask* for, not what the DAC must
   carry -- enforcing it on the output too meant the clamp ran after the rate
   limiter and undid it, so a loop told to freeze at 20% wrote 62% on the next
   cycle.  Below the band is less heat, which is never the dangerous direction.
   And it is never above where the heater already is: a floor that is would
   COMPEL heat, which is invariant 4 broken by the safety layer itself.
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

    **The band is no longer centred here.**  Phase 3 step 4: it is centred on
    the model's answer for the present setpoint, so there is nothing to
    re-centre by hand and nothing that goes stale when the cryostat is moved to
    a new temperature.  What is left in this class is the WIDTH of the window
    and the absolute limits around it.

    The width is in percent and the gain is not constant -- 0.34 K/% at 10 K
    against 12.7 at 140 K -- so one percent of authority is worth 0.34 K down
    there and 13 K up here.  That asymmetry is correct rather than unfortunate:
    what the band is protecting against is the loop wandering away from the
    output the model says is right, and a percent of output is a percent of
    output wherever it happens.
    """

    #: The band's centre when there is NO model to ask -- feedforward
    #: disabled, or a cryostat with no fitted curve.  It is no longer the band
    #: itself: see `HeaterSupervisor.band_centre_pct`, which asks the model
    #: what output holds the present setpoint.  Also the output a loop adopts
    #: when it has never read one.
    operating_point_pct: float = 63.076
    #: Half-width of the band, around whatever the centre is, PLUS the lead a
    #: commanded ramp needs (`ramp_lead_pct`).
    #:
    #: One percent is not arbitrary against the one systematic that moves the
    #: centre: the heater circuit may fail to deliver `DELTA_P_FRAC` = 0.7 % of
    #: the POWER, and u goes as sqrt(P), so that is 0.35 % of output.  The
    #: remaining 0.65 % is genuine margin, and the model's own shape error
    #: (0.135 K in-epoch) is far smaller than either.
    authority_pct: float = 1.0
    hard_min_pct: float = 0.0
    #: **The one cap nothing moves.**  Whatever the model, the setpoint or the
    #: arithmetic says, the output cannot exceed this.
    hard_max_pct: float = 70.0

    #: **THE OUTPUT RATE FLOOR, and one of the two rate fields left of eight.**
    #: The heater's percent rate is `max_rate_k_per_min / K(T)` -- the one rate
    #: converted through the gain, so 5 K/min means 5 K/min everywhere -- and
    #: this is what it falls back to where the model has no opinion.  0.38 %/min
    #: at 118 K, 14.6 %/min at 10 K.
    #:
    #: What it replaces is `max_step_pct: 0.02` and `max_rate_pct_per_min:
    #: 0.20`, both of which were trim rates: ten kelvin is 29 % of output at
    #: the cold end's 0.34 K/%, so 0.2 %/min allowed two and a half hours for a
    #: two-minute sweep and the premise check called the cryostat broken long
    #: before it arrived.
    min_rate_pct_per_min: float = 0.20

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
    #: **A ceiling on the DERIVED velocity feedforward, not the value itself.**
    #: What a ramp needs is `rate*tau/K` and the loop computes it
    #: (`ramp_lead_pct`); this is the most it may ever come to, for a rate
    #: nobody meant to command.  It was 1.00 and that was the value rather than
    #: the ceiling -- against the 4.10 % a 5 K/min sweep needs at 180 K, which
    #: meant the feedforward supplied a quarter of the drive and the integral
    #: wound up the rest and then overshot when the ramp ended.
    #:
    #: 6.0 is above the 4.10 % the fastest commanded rate needs at the worst
    #: temperature, with margin, and far below `hard_max_pct`.
    max_velocity_ff_pct: float = 6.00
    #: Once settled, the measurement should agree with kelvin_for(output).  If
    #: it disagrees by more than this the calibration does not describe the
    #: present regime -- say so loudly rather than quietly trusting it.
    model_trust_k: float = 15.0
    #: "Settled" for that check: slope below this and no ramp in progress.
    model_check_slope_k_per_s: float = 0.002
    # Fault response.  THE SAME ONE RATE, in kelvin, through the model's
    # inverse curve -- `_rampdown_target`.  Three percent-rate fields and a
    # knee are gone with it: they existed because a rate in percent means a
    # different thing at every temperature, and the knee at 40 % was an attempt
    # to patch that by hand.  Descending at `max_rate_k_per_min` by the model's
    # reckoning is the same descent everywhere, and it needs no sensor.
    #
    # Still nothing like an emergency stop.  A fault on this cryostat is not an
    # emergency, and the risk of a fast change remains larger than the risk of a
    # slow one (invariant 6).  118 K to base takes about 23 minutes.
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
        cadence_s: float | None = None,
        clock=time.monotonic,
    ) -> None:
        self.inst = instrument
        self.channel = channel
        self.cfg = config or SupervisorConfig()
        self.guard = SensorGuard(guard_config, name=channel)
        self.coherence = CoherenceMonitor(coherence_config)
        self.filter = MeasurementFilter(**(filter_kwargs or {}))
        self.clock = clock

        # THE CADENCE IS MEASURED, and seeded from the config rather than taken
        # from it.  The loop's dead time is derived from the cadence, and the
        # cadence a config asks for is not the one the bus delivers: the real
        # logs jitter, a retry costs a whole cycle, and `acquisition.interval_s`
        # is a floor on the period rather than a promise about it.  So the
        # config's number opens the account and the observed dt keeps it -- one
        # slow EMA, because a tuning that jittered with the bus would be worse
        # than either answer.
        self._cadence_s = float(cadence_s) if cadence_s else None
        self.feedforward = Feedforward(feedforward_config)
        self.tuner = Tuner(tuning_config)
        self.tuner.delay_s = self.delay_s
        self.pid = PID(
            pid_config or PIDConfig(),
            feedforward=self.feedforward,
            ff_limit_pct=self.cfg.max_feedforward_pct,
        )
        self.ramp = SetpointRamp(self.pid.cfg.setpoint, ramp_config)
        # The corner is DERIVED from the loop's dead time, so it is short
        # against any sweep worth commanding.  `delay_s` needs the filter and
        # the cadence, both of which exist by now.
        self.smoother = SetpointSmoother(self._smooth_tau_s(),
                                         value=self.pid.cfg.setpoint)
        #: Set BEFORE the first `_apply_band_to_pid`, because the band's floor
        #: now asks where the heater is.
        self.output_pct: float | None = None   # last value we commanded
        self._apply_band_to_pid()

        self.dither = SigmaDeltaDither(self.cfg.dac_step_pct)
        self.mode = LoopMode.OFF
        self.state = SupervisorState.IDLE
        self.status = SupervisorStatus()

        self.manual_pct: float = self.cfg.operating_point_pct
        self._anomaly_since: float | None = None
        self._comms_bad_since: float | None = None
        self._last_t: float | None = None
        self._locked_reason = ""
        self._rampdown_complete = False
        #: Where the open-loop fault ramp-down is descending FROM, and when it
        #: started.  Captured at the fault, because after that the sensor is
        #: not trusted.
        self._rampdown_from_k: float | None = None
        self._rampdown_t0: float | None = None
        self._ramp_allowance_k = 0.0
        self._pending_approach = False
        self._model_warned = False
        self._last_feedback = 0.0
        #: Did the previous cycle actually send bytes?  What decides whether
        #: `output_pct` may be trusted as "where the heater is" -- see
        #: :meth:`_where_the_heater_is`.
        self._wrote_last_cycle = False
        #: Which panic action stopped this loop, until it is armed again.
        #: `off`/`idle` is also where a never-armed loop sits, and those
        #: are different things to read on a screen.
        self._disengaged_by = ""

    # -- the loop's own dead time ------------------------------------------

    #: How slowly the measured cadence follows the bus.  Not a limit: it is how
    #: long one jittered cycle is remembered for, and it is long on purpose --
    #: a tuning that moved because a single read retried would be worse than
    #: either answer it moved between.
    CADENCE_TAU_S = 300.0

    @property
    def delay_s(self) -> float:
        """The loop's pure delay -- the filter chain at the measured cadence.

        3.0 s on this cryostat: a median-3 at 2 s, plus the zero-order hold,
        plus nothing for a low pass that is switched off.  Derived from the
        chain that produces it (:meth:`MeasurementFilter.group_delay_s`) so it
        cannot come to disagree with the filter it describes.
        """
        return self.filter.group_delay_s(self._cadence_s or 0.0)

    def _smooth_tau_s(self, kelvin: float | None = None) -> float:
        """How long the corner of a commanded ramp is rounded over.

        **The closed-loop response time in `move`**, floored on the loop's dead
        time.  ``move_speed * tau(T)``: 4.5 s at 30 K, 260 s at 118 K, 306 s at
        180 K.

        That is not a tuning knob dressed up -- it is the same number
        ``tau_cl`` is, and it has to be.  A setpoint trajectory with corners
        sharper than the closed loop's own response is one the loop cannot
        follow by construction, and what comes out is lag going in and
        overshoot coming out, which no retuning fixes.  Rounding over exactly
        `tau_cl` asks for the fastest trajectory that IS followable.

        Measured on the fitted plant, a 10 K sweep at 118 K with the premise
        check out of the way, against corner length:

            12 s     8.46 K of lag, 5.3 % overshoot, arrives in 11.3 min
            60 s     6.24 K,        5.6 %,            10.8 min
            180 s    2.65 K,        0.3 %,            30.4 min
            450 s    0.80 K,       -0.3 %,            50.6 min

        and ``move_speed * tau`` is 260 s there.  The two flat values this
        replaced are both visible in that table: 300 s was measured at
        0.5 K/min where it is about right at 118 K and eighty times too long at
        30 K, and the 12 s the first draft of step 5 derived from the dead time
        alone is the top row.

        **A rate is a ceiling, not a promise.**  What the table also says is
        that a 519 s plant does not move 10 K in the two minutes 5 K/min
        implies, whatever anybody configures; it takes about eleven at best,
        and thirty if you want the trajectory followed.
        """
        floor = self.ramp.cfg.smooth_delays * self.delay_s
        if kelvin is None:
            kelvin = self.filter.value if self.filter.primed else None
        if kelvin is None or not self.tuner.enabled:
            return floor
        return max(floor, self.tuner.cfg.move_speed * self.tuner.schedule.tau_at(kelvin))

    def _learn_cadence(self, dt: float) -> None:
        if dt <= 0:
            return
        if self._cadence_s is None:
            self._cadence_s = dt
        else:
            alpha = 1.0 - math.exp(-dt / self.CADENCE_TAU_S)
            self._cadence_s += alpha * (dt - self._cadence_s)
        self.tuner.delay_s = self.delay_s

    # -- authority band ----------------------------------------------------

    def target_band_centre_pct(self) -> float:
        """The output that holds the setpoint the loop is chasing RIGHT NOW.

        **The band follows the setpoint (phase 3 step 4, rule 5 reworded).**
        It used to be ``operating_point_pct`` -- one config constant, fixed for
        the life of the process, with ``_apply_band_to_pid()`` called once in
        ``__init__``.  On a cryostat that is asked to run from 4 to 300 K that
        cannot work: 10 K is 24.22 % of output and 180 K is 68.73 %, a span of
        44 points, against a window one point wide.  No sweep below 60 K was
        arithmetically possible, and above 100 K a completed sweep faulted
        afterwards because the window was still centred where it started.

        This is where the centre is HEADING.  :meth:`band_centre_pct` is where
        it has got to, and the difference between the two is the whole safety
        argument -- see there.

        The centre is the MODEL's answer, not the loop's opinion of itself, so
        a loop that has wandered cannot drag its own window along behind it.
        """
        if self.feedforward is not None and self.feedforward.enabled:
            return self.feedforward.percent_for(self.pid.cfg.setpoint)
        return self.cfg.operating_point_pct

    #: Kept as an alias because the centre is not slew limited and there is
    #: exactly one answer.  It was, for one draft of step 4, and that is worth
    #: recording because the reasoning looked right: `set_setpoint(x,
    #: ramp=False)` steps the setpoint and resets the smoother, so an
    #: unlimited centre opens the ceiling in one cycle.  **Limiting it is
    #: unnecessary and it breaks arming**: a loop armed with the heater at 0 %
    #: gets a window at 0 % that then crawls toward the setpoint at
    #: the output rate limit, and 0 to 63 % at a trim rate is hours during
    #: which the loop cannot reach anything.  Measured: 115 tests.
    #:
    #: The band opening is not what moves the heater.  Three things bound the
    #: consequence of a stepped setpoint and none of them is the centre's
    #: speed: `hard_max_pct`, which nothing moves; the OUTPUT rate limiter,
    #: which is what actually governs how fast the heater travels into a newly
    #: opened window; and rule 8's premise check, which refuses a setpoint step
    #: larger than `max_error_k` outright.  A band that opens instantly onto an
    #: output that can only move 0.2 %/min has not applied any heat.
    band_centre_pct = target_band_centre_pct

    def ramp_lead_pct(self) -> float:
        """The output lead a commanded ramp genuinely needs, in percent.

        A first-order plant following a ramp of rate ``r`` sits at an output
        ``r*tau/K`` above the one that would HOLD where it is -- that is what
        makes it move.  At 5 K/min this is 0.02 % at 10 K and **4.10 % at
        180 K**, which is four times the authority band and is why
        ``max_velocity_ff_pct: 1.00`` could not have worked: the feedforward
        was capped at a quarter of the drive, the integral had to supply the
        rest, and it then overshot when the ramp ended.

        So the band widens by exactly this while a ramp is running, and by
        nothing when one is not.  The authority is granted to the TRAJECTORY,
        which is a commanded thing with a rate limit on it, rather than to the
        loop in general.

        Taken from the smoother rather than the ramp, so it decays with the
        smoother's own time constant when a sweep ends instead of falling off
        a cliff while the cryostat is still catching up.
        """
        rate = abs(getattr(self.smoother, "rate_k_per_s", 0.0) or 0.0)
        if rate <= 0 or not self.tuner.enabled:
            return 0.0
        here = self.filter.value
        if here is None:
            here = self.pid.cfg.setpoint
        gain = self.tuner.schedule.gain_at(here)
        if gain <= 0:
            return 0.0
        lead = rate * self.tuner.schedule.tau_at(here) / gain
        return min(lead, self.cfg.max_velocity_ff_pct)

    @property
    def band(self) -> tuple[float, float]:
        c = self.cfg
        half = c.authority_pct + self.ramp_lead_pct()
        centre = self.band_centre_pct()
        lo = max(c.hard_min_pct, centre - half)
        hi = min(c.hard_max_pct, centre + half)
        # **THE FLOOR MAY NEVER BE ABOVE WHERE THE HEATER ALREADY IS.**  The
        # floor bounds what the PID may ASK for -- it is anti-windup, not a
        # demand -- and with a fixed centre that distinction never mattered
        # because the loop lived inside its own window.  With a centre that
        # follows the setpoint it matters immediately: in a regime the model
        # does not describe, the centre lands somewhere the cryostat is not,
        # and a floor above the present output makes `out_min` compel heat the
        # loop never asked for.  Measured on the cooler-off regime: a loop
        # holding steadily at 63.09 % was walked up to 64.68 % by its own
        # envelope, which is invariant 4 -- nothing raises the heater as a side
        # effect of anything -- broken by the safety layer itself.
        # And it must not be pinned TO it either, which is what `min(lo, here)`
        # does and it is a RATCHET: `out_min` equal to the present output means
        # the PID can never ask for less than it is already producing, so every
        # upward wiggle is locked in.  Measured in the cooler-off regime, where
        # the centre clamps to the ceiling and the nominal floor sits above the
        # loop entirely: the output walked 63.11 -> 63.51 % in seven minutes
        # with the error oscillating around zero and nothing asking for heat.
        #
        # So when the window is somewhere the loop is not, the floor is simply
        # the hard one.  Full downward freedom, which is the safe direction.
        here = self.output_pct
        if here is not None and lo > here:
            lo = c.hard_min_pct
        if lo > hi:
            # Only reachable by a centre outside the hard limits, which means
            # the model is asking for an output this cryostat is not allowed to
            # produce.  Clamp to the ceiling rather than raising: refusing to
            # have a band at all would take the loop out mid-sweep, and less
            # heat is never the dangerous direction.
            lo = hi = min(max(centre, c.hard_min_pct), c.hard_max_pct)
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
        if self.state is SupervisorState.RAMPING_DOWN and mode is not LoopMode.OFF:
            # Same shape as the lockout refusal below, and for the same reason:
            # automation may not call off a ramp-down that is already running.
            # `OFF` stays open so an operator's `hold` or `heaters_off` still
            # wins -- those are the human path, and they are meant to.
            raise PermissionError(
                f"supervisor is ramping the heater down ({self._locked_reason}). "
                "Let it finish and clear the latch with `send ack`, or stop it "
                "now with `send hold` (which freezes the heater where it is)"
            )
        if self.state is SupervisorState.LOCKED_OUT and mode is not LoopMode.OFF:
            # Name the way out, the way every other refusal in this system
            # does.  This used to say "acknowledge() first", which is a Python
            # method an operator at a terminal has no way to call -- a signpost
            # pointing at a wall.
            raise PermissionError(
                f"supervisor is locked out ({self._locked_reason}). Look at the "
                "cryostat, then clear the latch with `send ack` (or "
                "acknowledge() in process); `arm` again after that"
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
            # Read, else the last value we commanded, else the operating point.
            # The middle rung can be absent now -- `panic_off` clears it -- so
            # the fallback is spelled out rather than left to `_read_output`,
            # which raises when handed no default at all.
            current = self._read_output(
                default=self.output_pct if self.output_pct is not None
                else self.cfg.operating_point_pct
            )
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
            self._disengaged_by = ""
            self.state = SupervisorState.TRACKING
        elif mode is LoopMode.MANUAL:
            # Adopt where the heater actually is, not where we last left it.
            # A panic hold arriving *after* something else moved the output
            # would otherwise "freeze" it at a value it is no longer at, and
            # then rate-limit its way back up to it -- which is the opposite of
            # freezing.  PID mode has always re-read here; manual had not.
            self.manual_pct = self._where_the_heater_is(default=self.manual_pct)
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
        # sweep to the post-fault approach rate behind their back.
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

    def _disengage(self, why: str) -> None:
        """Stop the loop acting on the heater at all, preserving any lockout.

        ``why`` is remembered and reported until the loop is armed again.  Both
        panic actions land in ``off``/``idle``, which is also where a loop that
        was never armed sits -- and "nobody has started this" and "somebody
        stopped this" are not the same thing to read on a screen.  The mode used
        to carry that distinction, badly (``manual`` meant held); saying which
        action was taken carries it properly.

        Both panic actions go through here, and that is the point: a person
        pressing either has decided the loop should stop deciding, and ``OFF``
        is the only mode that writes nothing whatever.  ``MANUAL`` is not good
        enough -- it still clamps to the authority band and still rate limits,
        which is the software overriding the person one cycle later.

        A lockout survives.  Stopping the heater is not the same as having
        looked at the cryostat, and a panic action is taken precisely when
        nobody has diagnosed anything yet; :meth:`acknowledge` clears a
        lockout and nothing else does.
        """
        was_locked = self.state is SupervisorState.LOCKED_OUT
        self.abort_ramp()
        self.set_mode(LoopMode.OFF)
        if was_locked:
            self.state = SupervisorState.LOCKED_OUT
        self._disengaged_by = why

    def panic_hold(self) -> float:
        """Stop regulating and leave the heater exactly where it is.

        The controller half of ``lschart``'s ``hold`` command, called
        duck-typed by name so ``lschart`` still never imports ``ltspm3``.

        **This disengages the loop -- it does not switch it to manual.**  It
        used to, and manual was not a hold: a manual output is still clamped to
        the authority band, so a `hold` taken while the heater sat outside that
        band moved it *on the next cycle*.  Measured: told to freeze at 20% it
        reported "holding 20.000%" and wrote 62.080%; told to freeze at 68% it
        wrote 64.070%.  It only ever really held when the heater happened
        already to be inside the band.  A freeze that freezes only sometimes,
        and reports a number it is about to leave, is worse than no freeze.

        So: ``abort_ramp()`` to stop any sweep where it stands, then ``OFF``,
        which writes nothing at all, ever.  The heater keeps the value it has.
        The loop's own idea of that value is refreshed from the instrument
        first, because the number this returns is the one the operator is told.

        Note this holds a *power*, not a temperature.  Nothing regulates the
        sample afterwards, so it will drift with the cryostat -- which is the
        opposite of what "hold" means on a 33x loop, and is why the viewer says
        so out loud before doing it.  :meth:`arm` is the way back.

        Returns the percentage being held.
        """
        held = self._where_the_heater_is(
            default=self.output_pct if self.output_pct is not None
            else self.cfg.operating_point_pct
        )
        self._disengage("held by an operator; `arm` to close the loop again")
        # Unlike `panic_off`, the loop still knows where the heater is: nothing
        # moved it, and it is going to stay there.  Keeping `output_pct` is
        # what keeps `heater_pct` in the log truthful through a hold.
        self.output_pct = held
        log.warning("PANIC HOLD: loop disengaged, heater left at %.3f%%", held)
        return held

    def panic_off(self) -> float | None:
        """Let go of the heater entirely, so it can be switched off and stay off.

        The controller half of ``lschart``'s ``heaters_off``, and the second
        seam that package reaches into this one by -- called duck-typed by
        name, exactly like :meth:`panic_hold`.

        Both panic actions disengage the loop; they differ only in what happens
        to the heater afterwards and therefore in what this object may still
        claim to know.  :meth:`panic_hold` leaves the output alone and goes on
        reporting it.  Here the caller zeroes it immediately after, so this
        stops reporting one at all.

        The caller's order matters and is the caller's job: disengage first,
        *then* zero the output.  Nothing may be driving that output at the
        moment the zero lands.

        Returns the output being abandoned, for the message, or ``None`` if
        this loop never commanded one.  :meth:`arm` is the way back, and it is
        not a panic action -- it applies power.
        """
        abandoned = self.output_pct
        self._disengage(
            "heaters off by an operator; `arm` to close the loop again")
        # And stop claiming to know where the heater is.  The caller zeroes the
        # output immediately after this, so `output_pct` would otherwise go on
        # reporting the abandoned value -- into `status.json`'s `control` block
        # and into the CSV's `heater_pct` column, which is the permanent record.
        # It read 63.08 beside an `ls218.aout1` of 0.00 in the same row, which
        # is a log that disagrees with itself about whether the heater is on.
        # A loop that has let go does not have an output; null is the honest
        # answer, and the instrument's own `aout1` still carries the truth.
        self.output_pct = None
        log.warning(
            "PANIC OFF: software loop disarmed at %s%%; nothing is driving the "
            "heater now", "?" if abandoned is None else f"{abandoned:.3f}",
        )
        return abandoned

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

    def _where_the_heater_is(self, *, default: float) -> float:
        """Where the output actually is -- read, not remembered.

        ``self.output_pct`` is what this supervisor last *commanded*, and it is
        authoritative only while this supervisor is the only thing writing to
        the analog output.  It is not.  ``lschart``'s ``heaters_off`` zeroes the
        218 directly and ``analog`` drives it to a number; either can land while
        this loop is holding and writing nothing at all.

        Every *relative* move computes from this value -- the rate limiter's
        step, the fault ramp-down's step, the value a manual hold adopts -- so a
        stale one does not merely mislead.  It re-commands the old output onto a
        heater somebody has just cut.  That is what turned the panic button into
        a four-minute pause: ``heaters_off`` wrote 0%, this loop held for
        ``anomaly_hold_s`` while the sample fell, then began its fault ramp-down
        from a remembered 63.08% and put the heat straight back on.

        Reading costs a transaction, so this only re-reads when the belief could
        have gone stale -- after a cycle in which nothing was written.  While the
        loop is writing, :meth:`_write_output` has just confirmed the value by
        readback and there is nothing better to know.
        """
        if self._wrote_last_cycle and self.output_pct is not None:
            return self.output_pct
        return self._read_output(default=default)

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
        self._learn_cadence(dt)

        # The corner is the closed-loop response time HERE, so it moves with
        # temperature exactly as tau_cl does.
        self.smoother.tau_s = self._smooth_tau_s()
        self.pid.cfg.setpoint = self.smoother.update(t, self.ramp.value(t))
        # EVERY CYCLE, because the band moves with the setpoint now.  It was
        # called once, in __init__, which is what made `operating_point_pct` a
        # window the cryostat could never leave.
        self._apply_band_to_pid()
        s = SupervisorStatus(
            t=t,
            mode=self.mode,
            setpoint_k=self.pid.cfg.setpoint,
            setpoint_target_k=self.ramp.target,
            ramping=self.ramp.ramping or not self.smoother.settled,
            # The gains are a property of the controller, not of this cycle's
            # decision, so they are reported on every cycle -- including the
            # ones that return early.  Set only in the PID branch they read 0.0
            # whenever the loop was off or holding, and a zero gain on screen
            # says "tuned to nothing" rather than "not being used right now".
            kp=self.pid.cfg.kp,
            ti=self.pid.cfg.ti,
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
            if self._disengaged_by:
                s.reason = self._disengaged_by
            self._wrote_last_cycle = False
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
            self._wrote_last_cycle = False
            self.status = s
            return s

        # -- rate limit ------------------------------------------------------
        current = self._where_the_heater_is(
            default=self.clamp(self.cfg.operating_point_pct)
        )

        if self.state is SupervisorState.RAMPING_DOWN:
            # A ramp-down has to be able to leave the authority band -- otherwise
            # it can never reach safe_output_pct.  Its rate is already set by
            # the one rate in _rampdown_target, through the model's inverse
            # curve, so the tracking limiter must not apply on top of it.
            # Only the absolute hard limits still hold.
            target = max(self.cfg.hard_min_pct, min(self.cfg.hard_max_pct, target))
        else:
            target = self._rate_limit(current, target, dt)
            # The band caps heat.  It does not COMPEL heat, and applying it as
            # if it did is what undid the rate limiter completely: `clamp` used
            # to run here and raise anything below the floor straight to it, so
            # a `hold` at 20% wrote 62.08% on the next cycle and an `arm` at 0%
            # went to 62.076% in a single step, past any rate limit at all.
            #
            # Down to the ceiling is still instant: less heat is never the
            # dangerous direction, and the post-quantise re-application below
            # already works this way.  Up to the floor is not, and does not
            # need to be -- the floor still bounds what the PID may *ask* for
            # (`_apply_band_to_pid` sets `out_min`), so the band keeps its
            # meaning as the window this loop operates in.  What it no longer
            # does is force the output into that window in one write.
            _, band_hi = self.band
            target = min(target, band_hi)
            target = max(target, self.cfg.hard_min_pct)
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
        # Only a write that landed leaves `output_pct` worth trusting next
        # cycle.  "Already there, nothing to send" does not: nothing confirmed
        # the output this cycle, so next cycle re-reads.
        self._wrote_last_cycle = bool(s.wrote)
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

        # A ramp-down LATCHES, and this check comes before the health check on
        # purpose.  It used to come after, so once the guard returned to OK the
        # loop fell straight through to normal tracking and quietly abandoned a
        # ramp-down that was started for a reason nobody had looked at yet --
        # only a ramp that ran all the way to safe_output_pct ever locked out.
        #
        # The latch excludes AUTOMATION, and only automation.  A recovering
        # sensor may not resume the loop; an operator still may stop the ramp,
        # because `panic_hold`/`panic_off` disengage through `set_mode(OFF)`,
        # which is the one transition still permitted from here.  A human
        # emergency measure is the final authority (Jeff, 2026-08-28).
        if self.state is SupervisorState.RAMPING_DOWN:
            return self._rampdown_target(
                t, s, self._locked_reason or "ramp-down latched", dt
            )

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
                    rate_k_per_min=self.ramp.cfg.max_rate_k_per_min,
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
                    gap, self.ramp.cfg.max_rate_k_per_min,
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
                # THE SAME NUMBER THE BAND WIDENED BY, so the drive a ramp
                # needs and the authority it is granted cannot disagree.  They
                # did: the cap was a flat 1.00 % against the 4.10 % a 5 K/min
                # sweep needs at 180 K, so the feedforward supplied a quarter
                # of the drive and the integral wound up the rest.
                limit = self.ramp_lead_pct()
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
        """Descend at the one rate, **open loop, through the model's inverse
        curve** -- so it needs no sensor.

        Rule 3 says the fault may BE the sensor, which is what makes a feedback
        ramp-down the wrong shape: the thing it would steer by is the thing
        that is broken.  So a target temperature is walked down from the last
        trusted reading at ``max_rate_k_per_min`` and turned into an output by
        ``percent_for``, which is arithmetic on a fitted table and asks the
        cryostat nothing.

        This replaces three percent-rate fields and a knee at 40 %.  They
        existed because a rate in percent is a different descent at every
        temperature -- 1 %/min is 13 K/min at 140 K and 0.34 K/min at 10 K --
        and the knee was an attempt to patch that by hand.  In kelvin there is
        nothing to patch: 118 K to base takes about 23 minutes and the sample
        falls at the one rate the whole way.

        **It only ever lowers the heater** (rule 1).  A model that is wrong
        high cannot turn a fault response into a heat-up.
        """
        if self.state is not SupervisorState.RAMPING_DOWN:
            log.error("heater RAMPING DOWN (%s)", why)
            self.state = SupervisorState.RAMPING_DOWN
            self._locked_reason = why
            # The last temperature anybody believed, and the clock it falls
            # from.  Captured once, at the fault, because after that the sensor
            # is not trusted -- which is the whole point.
            self._rampdown_from_k = (
                self.filter.value if self.filter.primed else None)
            self._rampdown_t0 = t
        s.alarms.append(f"ramping down: {why}")

        current = self._where_the_heater_is(default=self.cfg.safe_output_pct)
        safe = self.cfg.safe_output_pct
        rate = self.ramp.cfg.max_rate_k_per_min

        if self._rampdown_from_k is not None and self.feedforward.enabled:
            elapsed = max(0.0, t - (self._rampdown_t0 or t))
            target_k = self._rampdown_from_k - rate * (elapsed / 60.0)
            proposed = self.feedforward.percent_for(target_k)
        else:
            # No trusted temperature and no curve: the one rate through the
            # gain at the present output, which is the same conversion the rate
            # limiter uses.
            proposed = current - self._rate_pct_per_min(None) * (dt / 60.0)

        # Never upward.  Rule 1, and the one line that makes a wrong model
        # harmless here.
        proposed = min(proposed, current)
        if proposed <= safe + self.cfg.dac_step_pct / 2:
            # Reached the safe value: hand it back this cycle and lock out on the
            # next one, so step() still writes it before the early-return kicks in.
            self._rampdown_complete = True
            return safe
        return proposed

    def _rate_pct_per_min(self, kelvin: float | None) -> float:
        """**The one rate, converted through the gain.**

        ``max_rate_k_per_min / K(T)``, floored at ``min_rate_pct_per_min``
        where the model has no opinion: 14.6 %/min at 10 K, 0.38 %/min at
        118 K, 0.40 %/min at 180 K.  The same five kelvin a minute at every one
        of them, which is what a rate limit in percent can never be -- the gain
        spans forty-fold across this cryostat.
        """
        floor = self.cfg.min_rate_pct_per_min
        if kelvin is None or not self.tuner.enabled:
            return floor
        gain = self.tuner.schedule.gain_at(kelvin)
        if gain <= 0:
            return floor
        return max(floor, self.ramp.cfg.max_rate_k_per_min / gain)

    def _rate_limit_step(self, dt: float) -> float:
        """The most the output may move this cycle.  Also what the band's
        centre may move, so the window can never open faster than the heater
        could travel into it."""
        if dt <= 0:
            # A cycle with no elapsed time may not move the output at all.
            # There used to be a `max_step_pct` fallback here, and it made the
            # limiter loosest exactly when dt was smallest, which is backwards.
            return 0.0
        here = self.filter.value if self.filter.primed else None
        return self._rate_pct_per_min(here) * (dt / 60.0)

    def _rate_limit(self, current: float, target: float, dt: float) -> float:
        step = self._rate_limit_step(dt)
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
