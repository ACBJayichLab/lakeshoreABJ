"""The safety envelope around the sample-heater PID.

Everything in this file exists to answer one question: *is it safe to move the
heater right now, and by how much?*  The PID only ever proposes; the supervisor
disposes.

The layers, outermost first -- a proposal must survive all of them:

1. **Mode.**  ``OFF`` writes nothing at all, ever.
2. **Sensor health.**  A single doubtful reading freezes the output.  Sustained
   failure ramps down.  Nothing raises the heater in response to a fault.
3. **Premise checks: THE WATTS ADD UP.**  ``dQ = C dT/dt + [Lambda(T) -
   Lambda(T_c)] - P(u)``, the same residual the monitor judges by and from the
   same two model functions.  Beyond 3 sigma it ALARMS AND KEEPS TRACKING; a
   STEP of ``fault_mw`` inside ``fault_window_s`` faults, as does an error past
   ``fault_error_k`` with the demand railed at the CEILING (authority
   exhausted); railed at the floor with the same error is a warning, because
   less heat than the model expects is the safe direction.  While the setpoint
   is not moving the tracking error in kelvin warns as well, and under about
   40 K -- below ``min_output_pct``, or where the plant is faster than the
   slope is measured -- it is the only check there is.
   A one-cycle lurch in the PID's own feedback terms still means a bad reading
   is driving the loop, and still freezes it.
4. **Authority band.**  A window ``authority_pct`` wide either side of THE
   OUTPUT THAT HOLDS THE PRESENT SETPOINT, widened while a ramp is running by
   exactly the lead that ramp needs, and intersected with an absolute
   never-exceed range.  A window fixed by config constants cannot span 4 to
   300 K.  See :meth:`HeaterSupervisor.band`.
   The **ceiling** is hard and immediate: however wrong everything else goes,
   the heater cannot go above this window, and ``hard_max_pct`` is the part of
   it that nothing moves.
   The **floor** bounds what the PID may *ask* for, not what the DAC must
   carry -- enforced on the output too, the clamp runs after the rate limiter
   and undoes it, so a frozen heater moves anyway on the next cycle.  Below the
   band is less heat, which is never the dangerous direction.
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
from collections import deque
from dataclasses import dataclass, field

from lschart.model import Reading, Validity
from lschart.transport import TransportError
from ..model import fitted_response as _M
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
    #: Output frozen pending clarity.  **It was `HOLDING`**, and that collided
    #: with the `hold` PHASE and with the `hold` COMMAND, which are three
    #: different things: the phase is a tuning, the command is an operator
    #: disengaging the loop, and this is the loop declining to act on a reading
    #: it does not believe.  Jeff asked for "frozen" (2026-09-11).
    FROZEN = "frozen"
    RAMPING_DOWN = "ramping_down"  # sustained fault -> slowly back off the heat
    LOCKED_OUT = "locked_out"      # ramp complete; needs an operator acknowledge
    #: An exception escaped `step()`.  The output is held exactly where it was,
    #: the loop is disengaged, and `arm` is refused until `ack` -- the same way
    #: out as a lockout, because the same thing is true: nobody has looked yet.
    CRASHED = "crashed"


#: **THE STATES A CYCLE MAY NOT CLEAR.**  Both mean the same thing -- the loop
#: stopped itself and nobody has looked at the cryostat yet -- and only
#: :meth:`HeaterSupervisor.acknowledge` clears either.
#:
#: One tuple, because every place that asks has to ask the same question: as
#: separate literals they drifted, and a crash was cleared without an `ack`.
#:
#: RAMPING_DOWN is deliberately NOT here.  It refuses automation as well, but
#: it is a descent in progress rather than a latch, and an operator's `hold` is
#: meant to be able to stop it where it stands.
LATCHED = (SupervisorState.LOCKED_OUT, SupervisorState.CRASHED)


@dataclass
class SupervisorConfig:
    """All limits are in output percent unless the name says kelvin.

    **The band is not centred here.**  It is centred on the model's answer for
    the present setpoint, so there is nothing to re-centre by hand and nothing
    that goes stale when the cryostat is moved to a new temperature.  What is
    left in this class is the WIDTH of the window and the absolute limits
    around it.

    The width is in percent and the gain is not constant, so one percent of
    authority is worth a fraction of a kelvin at the cold end and many kelvin
    at the warm one.  That asymmetry is correct rather than unfortunate: what
    the band is protecting against is the loop wandering away from the output
    the model says is right, and a percent of output is a percent of output
    wherever it happens.
    """

    #: The band's centre when there is NO model to ask -- a cryostat with no
    #: fitted curve.  It is not the band itself: see
    #: `HeaterSupervisor.band_centre_pct`, which asks the model what output
    #: holds the present setpoint.  Also the output a loop adopts when it has
    #: never read one.
    operating_point_pct: float = 63.076
    #: Half-width of the band, around whatever the centre is, PLUS the lead a
    #: commanded ramp needs (`ramp_lead_pct`).
    #:
    #: It is control room, and it is not arbitrary: it has to cover the level
    #: error the model is allowed to carry -- the heater circuit may fail to
    #: deliver `DELTA_P_FRAC` of the POWER, and u goes as sqrt(P), so half of
    #: that lands on the output -- plus the overdrive a fast move needs.  A
    #: half-width below the level error cuts the heater at arming.  What the
    #: bench measured of each width is in docs/ltspm3/requirements.md 3.
    authority_pct: float = 1.0
    hard_min_pct: float = 0.0
    #: **The one cap nothing moves.**  Whatever the model, the setpoint or the
    #: arithmetic says, the output cannot exceed this.
    hard_max_pct: float = 70.0

    #: **THE OUTPUT RATE FLOOR.**  The heater's percent rate is
    #: `max_rate_k_per_min / K(T)` -- the one rate converted through the gain,
    #: so 5 K/min means 5 K/min everywhere -- and this is what it falls back to
    #: where there is no model to ask.
    min_rate_pct_per_min: float = 0.20

    # -- premise checks: THE WATTS ADD UP -----------------------------------
    #
    # Rule 4 is checked in WATTS, not in kelvin.  A kelvin threshold cannot
    # serve a cryostat whose gain spans forty-fold: the same 1 K is a trim at
    # the warm end and the whole range at the cold one.
    #
    # The premise is the residual the monitor judges by -- the SAME two
    # functions, so the two cannot disagree about what typical means:
    #
    #     dQ = C(T) dT/dt + [Lambda(T) - Lambda(T_c)] - P(u)
    #
    # which is valid at a hold AND during a sweep, which is exactly what a
    # kelvin check is not.

    #: Tracking error that raises an alarm and keeps going.  **While the
    #: setpoint is not moving** -- during a commanded move the error IS the
    #: ramp's lag by design.  It is the check cryo conditions require (Jeff,
    #: 2026-09-15): the watt residual is the primary one, and it has no opinion
    #: below `min_output_pct` or where the plant is faster than the slope is
    #: measured, which between them is everything under about 40 K.
    warn_error_k: float = 1.0
    #: Alarm when `dQ` is this many sigma out, with `sigma_q_w` from the model.
    warn_sigma: float = 3.0
    #: **A fault is a STEP, not a level** -- inherited from the monitor, not
    #: re-decided here.  A change in delivered power puts `-(1-a)P(u)` into the
    #: residual immediately; anything that takes hours to reach a level did not
    #: step, and the fault slow degradation eventually causes is
    #: authority-exhausted below.  10 mW is Jeff's, 2026-09-14, and it is a
    #: FLOOR under the 3 sigma band rather than a replacement for it -- the
    #: FAST band, `sigma_q_fast_w`, which carries no drift and no calibration
    #: because neither can move inside the window a step is measured over.
    fault_mw: float = 10.0
    #: The window the step is measured over, as a range.
    fault_window_s: float = 1800.0
    #: Tracking error at which the two edges of the band part company.
    #:
    #: **Railed at the CEILING is a fault**: the loop is asking for everything
    #: it is allowed to give and the sample still will not come up, so the
    #: heater, the wiring or the model is not what the loop believes.  Nothing
    #: about that is understood, and an open-loop descent is safer than
    #: continuing to drive blind.  Authority exhausted; no window gates it.
    #:
    #: **Railed at the FLOOR is a warning, however far it goes** (Jeff,
    #: 2026-09-15).  Less heat than the model expects for this setpoint means
    #: the bath has changed or the model is wrong high, and the worst case is a
    #: sample colder than intended -- the safe direction.  A ramp-down would
    #: not improve it and a lockout would stop the loop resuming when the bath
    #: recovers.  A rising coldplate is exactly this, and `delta T_c` never
    #: faults.
    fault_error_k: float = 5.0
    #: How long any of the fault conditions must hold before the ramp-down.
    fault_after_s: float = 180.0
    #: Below this output the heater has no authority and the residual is the
    #: difference of two large numbers.  **The same number the monitor uses**,
    #: and the reason the kelvin check stays on underneath it.
    min_output_pct: float = 28.0
    #: The channel the sink temperature is read from.  `Lambda(T) - Lambda(T_c)`
    #: needs it; the model's own settled locus is the fallback for a cryostat
    #: that never reports one at all.
    coldplate_channel: str = "Coldplate"
    #: How long a coldplate reading stays usable after it arrives.  Past this
    #: the residual has NO OPINION rather than running on a remembered sink:
    #: `_coldplate_k` used to be refreshed only on a usable reading and never
    #: to expire, so after the channel dropped out the residual ran on a stale
    #: number for as long as the loop ran, and a coldplate that moved while
    #: nobody could see it would have been read as the sample's problem.
    #:
    #: Falling back to the model's locus instead was considered and rejected:
    #: the switch itself is a step of `Lambda(T_c,model) - Lambda(T_c,measured)`
    #: in the residual, which is exactly the false fault this is meant to
    #: avoid.  A few cycles, because the sink is read on every one of them.
    sink_stale_s: float = 30.0

    #: A one-cycle lurch in the *feedback* terms (P+I+D) this large means a bad
    #: reading is driving the loop, not that the cryostat needs the output.
    #: Kept: it is about the READING, not about the cryostat.
    anomaly_demand_pct: float = 0.50

    #: Hard cap on the feedforward contribution.  The steady-state curve is
    #: calibrated for one regime -- cooler running, shields cold.  With the
    #: cooler off (before a cooldown, after a warmup) the same percent produces
    #: a very different temperature, and a temperature log alone cannot tell
    #: those regimes apart.  Feedforward enters incrementally so a whole-curve
    #: offset cancels; this bounds what a wrong local *slope* can do.
    max_feedforward_pct: float = 0.40
    #: **A ceiling on the DERIVED velocity feedforward, not the value itself.**
    #: What a ramp needs is `rate*tau/K` and the loop computes it
    #: (`HeaterSupervisor.ramp_lead_pct`, which is where that lead is derived);
    #: this is the most it may ever come to, for a rate nobody meant to
    #: command.  It sits well above what the fastest commanded rate needs at
    #: the worst temperature and far below `hard_max_pct` -- a ceiling set at
    #: the lead itself is not a ceiling but a throttle, and leaves the integral
    #: to supply the rest of the drive and overshoot when the ramp ends.
    max_velocity_ff_pct: float = 6.00
    #: "Settled" for the reported model error: slope below this and no ramp.
    model_check_slope_k_per_s: float = 0.002
    # Fault response.  THE SAME ONE RATE, in kelvin, through the model's
    # inverse curve -- `_rampdown_target`, which is where the descent and how
    # long it takes are described.  Descending at `max_rate_k_per_min` by the
    # model's reckoning is the same descent at every temperature, which a rate
    # in percent is not, and it needs no sensor.
    #
    # Still nothing like an emergency stop.  A fault on this cryostat is not an
    # emergency, and the risk of a fast change remains larger than the risk of a
    # slow one (invariant 6).
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
    #: Has the residual an opinion about the model, and is it inside the band?
    #: **None means no opinion**, which is neither trust nor distrust, and it
    #: is the DEFAULT -- `_check_model` is the only thing that ever writes it,
    #: and it only runs at a settled hold.  It defaulted to True, so a status
    #: written during a ramp or a frozen fault hold reported trust that nothing
    #: had established: PID_PLAN.md section 7's trap, one field along.
    model_trusted: bool | None = None
    #: THE PREMISE, in watts.  `None` means no opinion, which is not the same
    #: as typical -- `residual_reason` says which.
    missing_power_w: float | None = None
    sigma_q_w: float = 0.0
    #: The range of the residual over `fault_window_s`.  A fault is a STEP.
    dq_step_w: float = 0.0
    residual_reason: str = ""
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
        wall_clock=time.time,
    ) -> None:
        self.inst = instrument
        self.channel = channel
        self.cfg = config or SupervisorConfig()
        self.guard = SensorGuard(guard_config, name=channel)
        self.coherence = CoherenceMonitor(coherence_config)
        self.filter = MeasurementFilter(**(filter_kwargs or {}))
        self.clock = clock
        #: **The second clock, and it is not interchangeable with the first.**
        #: `clock` is monotonic and every interval in this file is measured on
        #: it.  This one is unix seconds, and exactly one thing needs it: the
        #: band's DRIFT term is dated, so `sigma_q_w` has to know how long ago
        #: the level was gauged.
        #:
        #: It is injected because a bench that reads the real calendar is a
        #: different grader on every day it runs -- 3 sigma at 118 K is 1.4 mW
        #: on the gauge day and 52 mW two months later, so a fault row would
        #: eventually stop faulting and a "quiet through a sweep" row would get
        #: easier every morning.  CLAUDE.md: a test must not depend on the time
        #: of day.  The recorder passes nothing and keeps real time.
        self.wall_clock = wall_clock

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
        #: The residual's trailing window, for the STEP test, and the slow
        #: average that feeds it.
        self._dq_hist: deque = deque()
        self._dq_slow: float | None = None
        #: The sink, from this cycle's frame, and WHEN it was read.  The
        #: model's settled locus is the fallback for a cryostat that never
        #: reports one; a reading that has gone stale is no opinion instead,
        #: because a remembered sink and a real one are the same number right
        #: up until they are not.  See `SupervisorConfig.sink_stale_s`.
        self._coldplate_k: float | None = None
        self._coldplate_t: float | None = None
        #: Edge-triggered logging for a standing warning.
        self._warned = False
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

        Derived from the chain that produces it
        (:meth:`MeasurementFilter.group_delay_s`, where the terms of the sum
        are set out) so it cannot come to disagree with the filter it
        describes.
        """
        return self.filter.group_delay_s(self._cadence_s or 0.0)

    def _smooth_tau_s(self, kelvin: float | None = None) -> float:
        """How long the corner of a commanded ramp is rounded over.

        **The corner IS ``tau_cl``**: ``move_speed * tau(T)``, the closed-loop
        response time in `move`, floored on the loop's dead time.

        That is not a tuning knob dressed up, and it has to be this number.  A
        setpoint trajectory with corners sharper than the closed loop's own
        response is one the loop cannot follow by construction, and what comes
        out is lag going in and overshoot coming out, which no retuning fixes.
        Rounding over exactly ``tau_cl`` asks for the fastest trajectory that
        IS followable -- shorter and the loop lags the corner, longer and the
        trajectory, not the loop, is what the move waits on.

        A FLAT corner length can only be right at one temperature, because
        ``tau`` spans three orders of magnitude over this cryostat's range;
        the floor (`RampConfig.smooth_delays` dead times) covers the cold end,
        where ``tau`` is shorter than the loop's own delay.  What the bench
        measured of moves under this rule is in
        docs/ltspm3/requirements.md section 3.
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

    # -- is there a model to ask? ------------------------------------------
    #
    # **TWO SEAMS, AND NEITHER OF THEM IS A COMMISSIONING SWITCH.**
    #
    #   `feedforward.enabled`  should the loop drive to the model's LEVEL?
    #   `tuner.enabled`        should the gains be rescheduled with T?
    #
    # Neither is "is there a curve to convert kelvin into percent with".  Ask
    # one of them that question -- as the fault ramp-down and the output rate
    # limiter did (AUDIT-2026-09-16 findings 2 and 3) -- and with both switches
    # off the descent and the rate limit both fall back to the floor rate and
    # the one rate never reaches the heater.  Both are properties of the
    # CRYOSTAT's model, and a stale level does not make a curve's shape
    # unusable.

    @property
    def has_curve(self) -> bool:
        """Is there a steady-state curve to walk, whatever the stage trusts?"""
        ff = self.feedforward
        return ff is not None and ff.has_curve

    @property
    def schedule(self):
        """The gain/tau schedule, or ``None`` where nothing has measured this
        cryostat.  `Tuner` builds one from the config's table or from the
        shipped fit; a controller with neither has no opinion about K(T) and
        the rate limiter falls back to its floor."""
        tuner = self.tuner
        return getattr(tuner, "schedule", None) if tuner is not None else None

    # -- authority band ----------------------------------------------------

    def target_band_centre_pct(self) -> float:
        """The output that holds the setpoint the loop is chasing RIGHT NOW.

        **The band follows the setpoint** (rule 5).  A centre fixed for the
        life of the process cannot serve a cryostat asked to run from 4 to
        300 K: the outputs that hold the two ends of that range are tens of
        points apart, against a window about one point wide, so any setpoint
        far from where the loop was armed rails against a window that never
        moved.

        This is where the centre is HEADING.  :meth:`band_centre_pct` is where
        it has got to, and the difference between the two is the whole safety
        argument -- see there.

        The centre is the MODEL's answer, not the loop's opinion of itself, so
        a loop that has wandered cannot drag its own window along behind it.

        **Whenever a curve EXISTS -- `has_curve`, not `feedforward.enabled`.**
        Asking the feedforward switch is the conflation described under "two
        seams" above: a question about whether the model exists, asked of a
        flag that says whether a commissioning stage trusts its LEVEL.  Jeff's
        requirement is that the band follows the setpoint -- a different
        setpoint trivially needs a different power, and whether the POWER is
        wrong is the watt residual's question, not the band's.  See
        docs/ltspm3/requirements.md.

        The stale level that switch was protecting against is the positional
        feedforward TERM's problem, not the band's: the band caps, it does not
        drive, and `authority_pct` is sized to cover the level error the model
        is allowed to carry.
        """
        if self.has_curve:
            return self.feedforward.percent_for(self.pid.cfg.setpoint)
        return self.cfg.operating_point_pct

    #: An alias, because the centre is deliberately NOT slew limited: opening
    #: the window applies no heat, and what bounds a stepped setpoint is
    #: `hard_max_pct`, the OUTPUT rate limiter and rule 8's refusal of a step
    #: larger than `warn_error_k`.  Limiting it also breaks arming -- a loop
    #: armed with the heater at 0 % would crawl toward its setpoint for hours.
    band_centre_pct = target_band_centre_pct

    def ramp_lead_pct(self) -> float:
        """The output lead a commanded ramp genuinely needs, in percent.

        **This docstring is the one home for that number.**  A first-order
        plant following a ramp of rate ``r`` sits at an output ``r*tau/K``
        above the one that would HOLD where it is -- that is what makes it
        move.  At 5 K/min it is 0.02 % at 10 K and **4.23 % at 180 K**, several
        times the authority band, which is why a flat ceiling on the velocity
        feedforward is a throttle rather than a ceiling: it caps the drive, the
        integral supplies the rest, and it then overshoots when the ramp ends.

        So the band widens by exactly this while a ramp is running, and by
        nothing when one is not.  The authority is granted to the TRAJECTORY,
        which is a commanded thing with a rate limit on it, rather than to the
        loop in general.

        Taken from the smoother rather than the ramp, so it decays with the
        smoother's own time constant when a sweep ends instead of falling off
        a cliff while the cryostat is still catching up.

        **Still gated on `tuner.enabled`, deliberately, where the ramp-down and
        the output rate limiter are not.**  Those two fail SLOW, and slow is
        the safe side of rule 1.  This one widens the authority band, which is
        the one direction that grants the loop more heater than it had -- so it
        stays off until a stage asks for it, and a stage with the tuning off
        runs a band that does not widen during a ramp (rule 5).
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
        # demand.  With a centre that follows the setpoint, a regime the model
        # does not describe puts the centre somewhere the cryostat is not, and
        # a floor above the present output then makes `out_min` compel heat the
        # loop never asked for: invariant 4 broken by the safety layer itself.
        #
        # And it must not be pinned TO it either, which is what `min(lo, here)`
        # does and it is a RATCHET: `out_min` equal to the present output means
        # the PID can never ask for less than it is already producing, so every
        # upward wiggle is locked in and the output walks up on noise alone.
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

        **The refusal is checked FIRST**, before anything is moved: otherwise a
        loop that refused to arm is left carrying a setpoint it never accepted,
        and the refusal itself can be pre-empted by whatever `set_setpoint`
        touched on the way -- after a crash, by the very thing that crashed.

        **ARMING AN ARMED LOOP IS REFUSED**, and that belongs here rather than
        at whichever button somebody pressed: the CLI and MATLAB reach this
        through the spool whatever the viewer greys out (AUDIT-2026-09-16
        finding 4).  On a tracking loop it would step the setpoint, clear
        `_pending_approach` and reset the smoother -- a dumped trajectory
        behind a command that says something else.  Rule 8 bounds the step, so
        it is not a hazard; it is a surprise, which is its own kind of unsafe.

        The way to move an armed loop's setpoint is `hold` and then `arm`,
        which is what the viewer's own tooltip says, and it is bumpless: `hold`
        freezes the heater where it is and `arm` primes from there.
        """
        self._refuse_if_latched(LoopMode.PID)
        if self.mode is LoopMode.PID:
            raise PermissionError(
                f"the loop is already armed at {self.pid.cfg.setpoint:.4f} K "
                "and its setpoint is unchanged. Arming again would step the "
                "setpoint with no ramp and dump the trajectory it is on; if "
                "that is what you want, `send hold` first (it freezes the "
                "heater where it is) and then `arm` at the new temperature"
            )
        self.set_setpoint(setpoint_k, ramp=False)
        self.set_mode(LoopMode.PID)

    def set_mode(self, mode: LoopMode) -> None:
        self._refuse_if_latched(mode)
        if mode is self.mode:
            return
        log.warning("heater mode %s -> %s", self.mode.value, mode.value)
        self.mode = mode
        self._enter_mode(mode)

    def _refuse_if_latched(self, mode: LoopMode) -> None:
        """Every latch that stops automation resuming the loop, in one place
        so `arm` can ask before it changes anything."""
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
        if self.state in LATCHED and mode is not LoopMode.OFF:
            if self.state is SupervisorState.CRASHED:
                raise PermissionError(
                    f"the heater loop crashed ({self._locked_reason}). Look at "
                    "the cryostat and the log, then clear the latch with `send "
                    "ack`; `arm` again after that")
            # Name the way out, the way every other refusal in this system
            # does.  This used to say "acknowledge() first", which is a Python
            # method an operator at a terminal has no way to call -- a signpost
            # pointing at a wall.
            raise PermissionError(
                f"supervisor is locked out ({self._locked_reason}). Look at the "
                "cryostat, then clear the latch with `send ack` (or "
                "acknowledge() in process); `arm` again after that"
            )

    def _enter_mode(self, mode: LoopMode) -> None:
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
            self._break_residual_history()
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

        Ramps by default.  A step change of more than ``warn_error_k`` is
        refused as a step and becomes a ramp at the one rate -- a move that
        large is a typo as often as it is an instruction, and rule 8 is
        enforced here rather than by the loop's distress.  ``ramp=False`` is
        for trims smaller than that.
        """
        # An explicit setpoint command takes charge of the trajectory: the
        # deferred post-fault approach must not silently re-rate an operator's
        # sweep to the post-fault approach rate behind their back.
        self._pending_approach = False
        here = self.filter.value if self.filter.primed else None
        big = (here is not None
               and abs(kelvin - here) > self.cfg.warn_error_k)
        if not ramp and big:
            # **RULE 8, ENFORCED WHERE IT BELONGS.**  "Move the setpoint by
            # ramping it, never by stepping it" cannot be left to the premise
            # check: that check can tell a commanded move from a fault (the
            # watt residual), so a stepped setpoint is simply obeyed, and a
            # typo of 300 K would walk the sample to the top of the table at
            # the rate ceiling.  So the refusal lives here, where it is a
            # statement about the REQUEST rather than a consequence of the
            # loop's distress: a step this large becomes a ramp at the one
            # rate.  `ramp=False` still means what its docstring says it means,
            # for a trim smaller than `warn_error_k`.
            log.warning(
                "setpoint step of %+.3f K is past warn_error_k %.2f K; "
                "ramping at %.1f K/min instead (rule 8)",
                kelvin - here, self.cfg.warn_error_k,
                self.ramp.cfg.max_rate_k_per_min)
            self.ramp.start(self.clock(), kelvin, from_k=here)
        elif not ramp:
            # A trim means a trim: bypass the smoother too, so a small
            # deliberate step is not silently turned into a gentle approach.
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
        was_locked = self.state in LATCHED
        was_state = self.state
        self.abort_ramp()
        self.set_mode(LoopMode.OFF)
        if was_locked:
            self.state = was_state
        self._disengaged_by = why

    def panic_hold(self) -> float:
        """Stop regulating and leave the heater exactly where it is.

        The controller half of ``lschart``'s ``hold`` command, called
        duck-typed by name so ``lschart`` still never imports ``ltspm3``.

        **This disengages the loop -- it does not switch it to manual.**
        Manual is not a hold: a manual output is still clamped to the authority
        band, so a `hold` taken while the heater sat outside that band would
        move it on the next cycle, and only ever really hold when the heater
        happened already to be inside the band.  A freeze that freezes only
        sometimes, and reports a number it is about to leave, is worse than no
        freeze.

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
        self._break_residual_history()

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

    def _where_the_heater_is(self, *, default: float | None) -> float:
        """Where the output actually is -- read, not remembered.

        ``default`` may be ``None``, and then a failed read RAISES rather than
        inventing a number.  Every caller that has something to fall back on
        passes it; the one that does not is the fault ramp-down, where a made-up
        current output is worse than no answer at all -- see
        :meth:`_rampdown_target`.

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
        """Advance one control cycle, and **fail gracefully if it cannot**.

        Jeff, 2026-09-11: a crashed PID disengages.  It did not -- an exception
        propagated to the poller with the loop still in `tracking` and still
        believing it owned the heater, and what happened next depended on how
        the caller handled it.  The output never moved, which was the one
        mercy, but nothing said so and nothing stopped the next cycle trying
        again.

        Now: `panic_hold()`, state `CRASHED` with the exception's first line,
        and `arm` refused until `ack`.  The recorder keeps writing, because a
        cryostat whose loop has crashed is exactly when the log matters most.

        Deliberately broad.  The distinction that matters here is not which
        exception it was -- it is that this thread owns a heater and must not
        leave it owned by nobody.
        """
        try:
            return self._step(t, reading, readings)
        except Exception as exc:                       # noqa: BLE001
            return self._crash(t, exc)

    def _crash(self, t: float, exc: BaseException) -> SupervisorStatus:
        first = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        reason = f"{exc.__class__.__name__}: {first}"
        log.exception("HEATER LOOP CRASHED (%s); disengaging", reason)
        try:
            held = self.panic_hold()
        except Exception:                              # noqa: BLE001
            # Even the disengage failed.  Say so and stop; there is nothing
            # left this object can do that is safer than doing nothing.
            log.exception("could not disengage after the crash")
            held = self.output_pct
        self.state = SupervisorState.CRASHED
        self._locked_reason = reason
        self._disengaged_by = (
            f"the loop crashed ({reason}); `ack` then `arm` to resume")
        s = SupervisorStatus(t=t, mode=self.mode, state=self.state,
                             output_pct=held, reason=self._disengaged_by,
                             alarms=[f"CRASHED: {reason}"])
        self._wrote_last_cycle = False
        self.status = s
        return s

    def _step(
        self,
        t: float,
        reading: Reading | None,
        readings: dict[str, Reading] | None = None,
    ) -> SupervisorStatus:
        """One cycle.  ``t`` is a monotonic timestamp in seconds.

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
            sink = readings.get(self.cfg.coldplate_channel)
            if sink is not None and getattr(sink, "usable", False):
                self._coldplate_k = sink.kelvin
                self._coldplate_t = t
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

        if self.mode is LoopMode.OFF or self.state in LATCHED:
            # **A LATCH SURVIVES THE CYCLE THAT FINDS IT.**  This used to name
            # LOCKED_OUT twice and say nothing about CRASHED, so a crash that
            # happened once -- a transient, which is the shape most of them
            # are -- was cleared to IDLE by the very next cycle and `arm` was
            # accepted with nobody having looked at anything.
            if self.state not in LATCHED:
                self.state = SupervisorState.IDLE
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
            self.state = SupervisorState.FROZEN
            s.alarms.append(f"holding: sensor {health.value}")
            return None

        assert s.filtered_k is not None

        if self._pending_approach:
            # First trustworthy measurement since arming: walk the setpoint in
            # from here rather than presenting the loop with the whole gap.
            self._pending_approach = False
            gap = self.ramp.target - s.filtered_k
            if abs(gap) > self.cfg.warn_error_k:
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
                # **A PERCENT, NEVER A TEMPERATURE.**  `prime` takes the
                # output the loop is starting from, and the fallback here was
                # `s.filtered_k` -- 118 would have primed the PID's bias at
                # 118 % of output, clamped to the band's ceiling, which is the
                # loop opening at full authority for no reason.  The operating
                # point is the answer for a loop that does not know where the
                # heater is; it is what `_enter_mode` already falls back to.
                self.pid.prime(self.clamp(
                    self.output_pct if self.output_pct is not None
                    else self.cfg.operating_point_pct
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
                # needs and the authority it is granted cannot disagree.  See
                # `ramp_lead_pct` for what that lead is and why a flat cap on
                # it is not a ceiling but a throttle.
                limit = self.ramp_lead_pct()
                vel = max(-limit, min(limit, vel))
        self.pid.velocity_ff_pct = vel
        s.velocity_ff_pct = vel

        terms = self.pid.update(s.filtered_k, s.slope_k_per_s, dt)
        s.demand_pct = terms.unclamped
        s.error_k = terms.error

        anomalies: list[str] = []
        faults: list[str] = []

        # THE PREMISE IS THAT THE WATTS ADD UP -- `_check_premise`.  There is
        # no ramp allowance here and there is nothing for one to do: a kelvin
        # allowance existed only because one threshold had to cover a hold and
        # a sweep, and the residual is valid during both.
        self._check_premise(t, s, terms, anomalies, faults, dt)

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

        # A WARNING KEEPS TRACKING.  That is the whole shape of rule 4 now: an
        # alarm says something is worth looking at, and only a FAULT stops the
        # loop.  The old check froze the output on any anomaly and escalated on
        # a timer, which on a cryostat whose legitimate sweep lag is 43 K meant
        # freezing was the normal outcome of doing what it was told.
        s.alarms.extend(anomalies)
        if anomalies and not self._warned:
            self._warned = True
            log.warning("heater premise: %s", "; ".join(anomalies))
        elif not anomalies:
            self._warned = False

        if faults:
            # A fault must PERSIST.  `fault_after_s` of it, and the output is
            # frozen for that time rather than tracking on a premise nobody
            # believes -- the integral must not charge while the loop is
            # refusing to act on it either.
            if self._anomaly_since is None:
                self._anomaly_since = t
                log.warning("heater FAULT pending, holding: %s", "; ".join(faults))
            held = t - self._anomaly_since
            s.alarms.extend(faults)
            if held >= self.cfg.fault_after_s:
                return self._rampdown_target(t, s, faults[0], dt)
            self.pid.integral, self.pid.cfg.kp, self.pid.cfg.ti = state_before
            self._last_feedback = feedback
            self.state = SupervisorState.FROZEN
            s.reason = f"fault held {held:.0f}/{self.cfg.fault_after_s:.0f} s"
            return None

        self._anomaly_since = None
        self._check_model(s)
        self.state = SupervisorState.TRACKING
        return terms.output

    def _break_residual_history(self) -> None:
        """Forget the trailing window.  Called wherever the loop stops being
        comparable with itself: arming, acknowledging, disengaging."""
        self._dq_hist.clear()
        self._dq_slow = None

    # -- rule 4, in watts --------------------------------------------------

    #: How long the residual is averaged over before the STEP test sees it.
    #: Not a threshold -- it is the timescale the question is asked on, and the
    #: fault it serves is specified as a step within thirty minutes.
    STEP_AVERAGE_S = 60.0

    def _check_premise(self, t, s, terms, anomalies: list, faults: list,
                       dt: float = 0.0) -> None:
        """Is the cryostat behaving?  Warnings into ``anomalies``, the two
        fault conditions into ``faults``.

        Four rows, and which of them apply depends on **whether the setpoint
        is moving**:

        * **the watt residual**, at a hold and during a sweep alike, because
          `dQ` carries `C dT/dt` and a kelvin error does not;
        * **the tracking error in kelvin**, while the setpoint is NOT moving --
          during a commanded move the error IS the ramp's lag by design.  This
          is the check cryo conditions require (Jeff, 2026-09-15): under about
          40 K the residual has no opinion at all, either because the output is
          below `min_output_pct` or because the plant is faster than the slope
          is measured, and then this is the only check there is;
        * **a step in the residual**, which is the fault: `fault_mw` inside
          `fault_window_s`, measured as the range of a trailing window;
        * **authority exhausted**: error past `fault_error_k` with the demand
          AND the output railed at the CEILING.  Railed at the FLOOR with the
          same error is a warning instead -- the two scenarios of plans/pid-3-review.md, and
          they are not symmetrical: too little heat leaves a cold sample, and
          too little authority to warm one is not something a ramp-down helps.

        **The gate on the kelvin rows is the TRAJECTORY, never the tuner's
        phase.**  The phase looks like a proxy for "the setpoint is not moving"
        and is not one: `update_phase` enters `move` on any error past
        `move_error_k`, so an error large enough to alarm is by construction in
        the phase that would switch the alarm off.  The phase is a tuning
        choice and decides nothing about alarms.
        """
        cfg = self.cfg
        band_lo, band_hi = self.band
        # **RAILED MEANS THE OUTPUT IS THERE TOO, not just the demand.**  A
        # loop still travelling up to its window at the rate limit has not
        # exhausted anything -- it has authority it has not applied yet -- and
        # the demand rails at the ceiling for the whole of that traverse
        # because that is what a PI controller with a standing error does.
        #
        # This mattered the moment the gate stopped being the tuner's phase:
        # measured on the armed-at-0 % case, where the heater is cut out from
        # under a loop and it climbs back at the rate limit.  The sample falls
        # several kelvin during the climb, the demand is railed high
        # throughout, and on the demand alone the loop declared authority
        # exhausted and ramped down the recovery it was in the middle of.
        here = self.output_pct
        railed_high = (s.demand_pct is not None and here is not None
                       and s.demand_pct > band_hi - cfg.dac_step_pct
                       and here >= band_hi - cfg.dac_step_pct)
        railed_low = (s.demand_pct is not None and here is not None
                      and s.demand_pct < band_lo + cfg.dac_step_pct
                      and here <= band_lo + cfg.dac_step_pct)

        # -- the kelvin rows ------------------------------------------------
        #
        # **THE GATE IS THE TRAJECTORY, not the tuner.**  The setpoint is not
        # moving: no ramp running, and the smoother has stopped moving the
        # value the PID is actually chasing.  (The smoother approaches
        # exponentially, so `settled` arrives a few time constants after a
        # sweep ends -- minutes at the cold end, where these rows are the only
        # check, and an hour at 118 K, where the watt rows are doing the work.)
        holding = not self.ramp.ramping and self.smoother.settled
        cold_end = (self.output_pct is not None
                    and self.output_pct < cfg.min_output_pct)
        if holding and abs(terms.error) >= cfg.warn_error_k:
            anomalies.append(
                f"error {terms.error:+.3f} K is past warn_error_k "
                f"{cfg.warn_error_k} K"
                + (" (below min_output_pct, where dQ has no opinion)"
                   if cold_end else ""))
        # **AUTHORITY EXHAUSTED IS GATED ON THE TRAJECTORY** -- the same
        # `holding` as the row above, i.e. while the setpoint is not moving --
        # and that is not a detail.  Railed with a large error is the NORMAL
        # state of a loop following a ramp the plant cannot keep up with; it is
        # what the velocity feedforward exists to produce.  Judged while the
        # setpoint is moving, this would ramp the heater down and lock out over
        # a sweep the loop was executing correctly.
        #
        # What makes exhaustion a fault is that it persists once the setpoint
        # has STOPPED moving: the cryostat is then asking for more than this
        # loop is allowed to give, which is what a compressor failure becomes.
        if holding and abs(terms.error) >= cfg.fault_error_k:
            if railed_high:
                faults.append(
                    f"authority exhausted: {terms.error:+.3f} K of error with "
                    f"the demand railed at the ceiling, {band_hi:.3f} % -- the "
                    "heater, the wiring or the model is not what this loop "
                    "believes")
            elif railed_low:
                # **AND THIS ONE IS NOT A FAULT** (Jeff, 2026-09-15).  The loop
                # is asking for less heat than the model says this setpoint
                # needs and cannot ask for less; the bath has changed, or the
                # model is wrong high.  The worst case is a sample colder than
                # intended, which is the safe direction, and neither a
                # ramp-down nor a lockout improves it -- a lockout would stop
                # the loop resuming when the bath recovers.
                anomalies.append(
                    f"heater at its floor, {band_lo:.3f} %, with "
                    f"{terms.error:+.3f} K of error: less heat than the model "
                    "expects for this setpoint.  The safe direction -- the "
                    "bath has changed, or the model is wrong high")

        # -- the watt rows --------------------------------------------------
        #
        # **TWO BANDS, AND KEEPING THEM APART IS THE POINT.**  `sigma` is the
        # whole band, drift and all, and it is what a LEVEL is judged against:
        # a level genuinely does get less well known as the gauge ages, the
        # warning keeps tracking, and the monitor's trailing baseline is what
        # judges the drift itself.  `fast` has no date in it and it is what a
        # STEP is judged against, because a step is a CHANGE and the drift term
        # grows too slowly to happen inside thirty minutes.
        #
        # The monitor judges its step the same way (`sigma_q_fast_w`,
        # judge.py).  Judging a step against the whole band instead means the
        # loop goes deaf to real steps as the gauge ages while the monitor,
        # looking at the same residual, still calls them.
        dq, sigma, fast, why = self._missing_power(s)
        s.missing_power_w, s.sigma_q_w, s.residual_reason = dq, sigma, why
        if dq is None:
            # **THE STEP HISTORY BREAKS HERE**, and clearing it is the point
            # rather than merely not appending.  A range taken across a gap is
            # not a step in the residual, it is the difference between two
            # residuals that were never comparable.  The gap that matters most
            # here is ARMING: without this, the window reaches back into the
            # pre-tracking samples and reads their offset as a step.
            self._dq_hist.clear()
            self._dq_slow = None
            return

        threshold = max(cfg.warn_sigma * sigma, 0.0)
        if abs(dq) > threshold:
            anomalies.append(
                f"missing power {1e3 * dq:+.2f} mW is past "
                f"{cfg.warn_sigma:.0f} sigma at {1e3 * threshold:.2f} mW")

        # **THE STEP TEST SEES AN AVERAGED RESIDUAL**, and it has to.
        #
        # `dQ` carries `C dT/dt`, and dT/dt is a regression over a window of a
        # noisy measurement, so milliwatts of pure estimator noise reach the
        # residual at the warm end.  A RANGE statistic over half an hour is
        # about six sigma of whatever noise it is fed, so unaveraged it faults
        # on the estimator with nothing whatever happening.  A LEVEL check does
        # not have this problem, which is why the range needed its own answer
        # rather than a bigger threshold.
        #
        # A minute of averaging costs the test nothing it was measuring: the
        # fault it is specified to catch is a step within thirty minutes, and
        # the 2026-09-10 event reached its level in seven.
        if dt > 0:
            alpha = 1.0 if self._dq_slow is None else 1.0 - math.exp(
                -dt / self.STEP_AVERAGE_S)
            self._dq_slow = dq if self._dq_slow is None else (
                self._dq_slow + alpha * (dq - self._dq_slow))
        if self._dq_slow is None:
            return
        self._dq_hist.append((t, self._dq_slow, fast))
        floor_t = t - cfg.fault_window_s
        while len(self._dq_hist) > 1 and self._dq_hist[0][0] < floor_t:
            self._dq_hist.popleft()
        recent = [x[1] for x in self._dq_hist]
        step = max(recent) - min(recent)
        s.dq_step_w = step
        # A FLOOR UNDER THE BAND, not a replacement for it: at a settled hold
        # the floor binds and during a sweep at the warm end the band does, and
        # a flat floor there would fault every sweep.
        #
        # **The band is the WIDEST it was anywhere in the window**, and it is
        # the FAST band -- no drift, no calibration -- because the range is a
        # statement about a change over the whole window, not about this
        # instant.  Evaluated at the instant, a window that contains a sweep
        # gets judged against the settled band and faults on motion that was
        # inside the band the whole time it was moving.
        widest = max(x[2] for x in self._dq_hist)
        fault_at = max(cfg.fault_mw * 1e-3, cfg.warn_sigma * widest)
        if step >= fault_at:
            faults.append(
                f"the residual stepped {1e3 * step:.2f} mW inside "
                f"{cfg.fault_window_s / 60:.0f} min, past {1e3 * fault_at:.1f} mW")

    def _missing_power(self, s):
        """``(dQ, sigma, sigma_fast, why)``.  ``dQ`` is None for no opinion.

        The same two model functions the monitor calls, so the loop and the
        judge cannot disagree about what typical means -- only about what to do
        about it.

        **Two bands come back, and they answer different questions.**  ``sigma``
        is :func:`~ltspm3.model.fitted_response.sigma_q_w`, the whole band, and
        it carries the drift since the level was gauged -- the band for a
        LEVEL.  ``sigma_fast`` is
        :func:`~ltspm3.model.fitted_response.sigma_q_fast_w`, which has no date
        in it at all -- the band for a CHANGE, and the one the monitor judges
        its own step test by.

        **No transient gate here, unlike the monitor's.**  The monitor holds its
        opinion for 3 tau after any heater move because it is fitting poles and
        measuring scatter, and a relaxation is a curve.  This is only the
        residual, which carries `C dT/dt` explicitly and is valid while the
        cryostat is moving -- and a closed loop moves the heater every cycle,
        so a gate keyed to that would mean no opinion, ever.
        """
        cfg = self.cfg
        if s.filtered_k is None or self.output_pct is None:
            return None, 0.0, 0.0, "no reading"
        if self.output_pct < cfg.min_output_pct:
            return None, 0.0, 0.0, f"output below {cfg.min_output_pct:.0f} %"
        if not _M.T_MIN_K <= s.filtered_k <= _M.T_MAX_K:
            return None, 0.0, 0.0, "sample outside the table"
        # **NO OPINION WHERE THE PLANT IS FASTER THAN THE SLOPE IS MEASURED.**
        # `dQ` carries `C dT/dt`, and dT/dt here is a regression over the slope
        # window.  Where tau is shorter than that window, a transient is over
        # before the window has seen it, the estimate is an average of two
        # regimes, and what comes out is tens of milliwatts of residual that is
        # about the estimator rather than the cryostat.  The monitor's own
        # transient gate is floored at its slope window for the same reason
        # (plans/pid-2-monitor.md 2.5).
        #
        # **MOVING means moving, not "the estimate is not exactly zero".**  At
        # a settled hold the dynamic term is zero and the question does not
        # arise, so the gate is the slope this file already calls settled
        # (`model_check_slope_k_per_s`).  Keyed to a bare non-zero slope -- and
        # with a real filter it is never exactly zero -- the residual has no
        # opinion at a settled hold either, and below about 40 K there is then
        # no check of any kind.
        window_s = self.filter.slope.window * (self._cadence_s or 0.0)
        if (abs(s.slope_k_per_s) > cfg.model_check_slope_k_per_s
                and _M.tau_s(s.filtered_k) < window_s):
            return None, 0.0, 0.0, "the plant is faster than the slope window"
        sink = self._coldplate_k
        if sink is None:
            # Never read one at all -- a cryostat with no coldplate channel.
            # The model's settled locus is the documented assumption there, and
            # there is no staleness to speak of because there is no reading.
            sink = _M.coldplate_k(s.filtered_k)
        elif (self._coldplate_t is not None
              and s.t - self._coldplate_t > cfg.sink_stale_s):
            # **A STALE SINK IS NO OPINION.**  `Lambda(T_s) - Lambda(T_c)` is
            # most of the residual and `Lambda'` at 6.6 K is nine times
            # `Lambda'` at 118, so 100 mK of sink is 1.5 mW -- a coldplate that
            # moves while the channel is down is invisible and lands in the
            # residual as if it were the sample's problem.  No opinion already
            # breaks the step history, so a sink that comes back cannot read as
            # a step either.
            return None, 0.0, 0.0, "coldplate stale"
        dq = _M.missing_power_w(s.filtered_k, s.slope_k_per_s, sink,
                                self.output_pct)
        sigma = _M.sigma_q_w(s.filtered_k, self.output_pct, self.wall_clock(),
                             dt_dt_k_per_s=s.slope_k_per_s)
        fast = _M.sigma_q_fast_w(s.filtered_k, self.output_pct,
                                 dt_dt_k_per_s=s.slope_k_per_s)
        return dq, sigma, fast, ""

    def _check_model(self, s: SupervisorStatus) -> None:
        """Report how far the settled measurement is from the curve.

        **It no longer judges.**  `model_trust_k: 15.0` was a second premise
        check in kelvin, and the watt residual subsumes it: a regime the
        calibration does not describe is a cryostat whose watts do not add up,
        which `dQ` says in the variable that means the same thing at 10 K and
        at 180 K.  Fifteen kelvin was also a strange number to have to choose,
        being simultaneously far too loose at the cold end and tighter than the
        13 K CD10 and the fit disagreed by at 59 %.

        What is left is the number itself, on the status line, because "what
        should this output be settling at" is a question an operator asks.
        Only meaningful once settled: during a ramp the measurement is
        *supposed* to lag the model.
        """
        if self.ramp.ramping or self.output_pct is None or s.filtered_k is None:
            return
        if abs(s.slope_k_per_s) > self.cfg.model_check_slope_k_per_s:
            return
        s.model_error_k = s.filtered_k - self.feedforward.kelvin_for(self.output_pct)
        # **NO OPINION IS NOT TRUST.**  `model_trusted` is True only when the
        # residual HAS an opinion and that opinion is inside the band.  Written
        # the other way round -- trusted unless the residual objects -- it
        # reports a green light wherever the residual is silent, which is
        # exactly PID_PLAN.md section 7's trap: a green light outside the table
        # is a lie, and the cooler-off regime at 315 K is outside the table.
        s.model_trusted = (s.missing_power_w is not None
                           and abs(s.missing_power_w)
                           <= self.cfg.warn_sigma * s.sigma_q_w)

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

        In kelvin there is nothing to patch, where a rate in percent is a
        different descent at every temperature: the sample falls at the one
        rate the whole way, and **118 K to base takes about 23 minutes**.
        This docstring is the one home for that figure.

        **It only ever lowers the heater** (rule 1).  A model that is wrong
        high cannot turn a fault response into a heat-up.
        """
        if self.state is not SupervisorState.RAMPING_DOWN:
            log.error("heater RAMPING DOWN (%s)", why)
            self.state = SupervisorState.RAMPING_DOWN
            self._locked_reason = why
            self._rampdown_from_k = None
            self._rampdown_t0 = None
        s.alarms.append(f"ramping down: {why}")

        # **A FAILED READ MUST NOT FINISH THE DESCENT.**  Defaulting `current`
        # to `safe_output_pct` makes one `TransportError` look like a completed
        # descent: the target goes to zero, `_rampdown_complete` is set, and
        # either the loop locks out with the heater still high or the heater
        # goes to zero in a single write -- the one thing rule 1 says a fault
        # response may not do.
        #
        # So: fall back to what we last commanded, and if there is not even
        # that, HOLD.  A descent that has to guess where it is descending from
        # is not a descent, and staying in RAMPING_DOWN costs nothing but a
        # cycle -- the latch is still on, and the next cycle tries again.
        try:
            current = self._where_the_heater_is(default=self.output_pct)
        except (TransportError, ValueError) as exc:
            self._note_comms_failure(s, f"ramp-down cannot read the heater: {exc}")
            s.alarms.append(
                "RAMP-DOWN HELD: nothing knows where the heater is, so there is "
                "nothing to descend from")
            return None
        safe = self.cfg.safe_output_pct
        rate = self.ramp.cfg.max_rate_k_per_min

        # **WHERE THE DESCENT FALLS FROM, AND WHEN IT STARTED.**  Captured
        # once, on the first cycle that knows where the heater is, because
        # after the fault the sensor is not trusted -- which is the whole
        # point -- and because a descent whose read failed has not started yet.
        #
        # The model's answer for the present output is the fallback, and it is
        # not a guess: `kelvin_for(current)` is what an OPEN-LOOP descent
        # stands on anyway, so a descent that starts from it is the same
        # descent, just with the first number coming from the curve instead of
        # from a filter that has not primed.  Without it the descent falls back
        # to `min_rate_pct_per_min`, which is hours, whenever the sensor faults
        # before the filter primes -- the state immediately after an `ack` and
        # a re-arm.
        if self._rampdown_t0 is None:
            self._rampdown_from_k = (
                self.filter.value if self.filter.primed
                else (self.feedforward.kelvin_for(current)
                      if self.has_curve else None))
            self._rampdown_t0 = t

        target_k = None
        if self._rampdown_from_k is not None and self.has_curve:
            # `or t` here read a start time of exactly 0.0 as "never
            # captured", so a descent that began at t = 0 -- which is where a
            # virtual clock starts, and where a monotonic one can -- recomputed
            # `elapsed` as zero on every cycle and never moved its target.
            t0 = self._rampdown_t0 if self._rampdown_t0 is not None else t
            elapsed = max(0.0, t - t0)
            target_k = self._rampdown_from_k - rate * (elapsed / 60.0)
            if target_k <= _M.T_MIN_K:
                # **THE INVERSE CURVE BOTTOMS OUT ABOVE ZERO**, and the descent
                # has to know that.  `percent_for` clamps at the table's cold
                # end, at a small but non-zero output, so a ramp-down that only
                # ever asked the curve would stop there and never reach
                # `safe_output_pct`, never complete, and never lock out.
                #
                # Once the target is at the bottom of the table there is
                # nothing left to ramp: the sample is at base temperature and
                # what remains is worth a fraction of a kelvin.
                proposed = safe
            else:
                proposed = self.feedforward.percent_for(target_k)
        else:
            # **No curve at all** -- a cryostat with no fitted response.  The
            # floor rate is all there is to descend at, and it is slow enough
            # to be measured in hours, which is why the gate above is
            # `has_curve`: whether there is a curve, not whether the present
            # stage drives to its level (AUDIT-2026-09-16 finding 2).
            proposed = current - self._rate_pct_per_min(None) * (dt / 60.0)

        # Never upward.  Rule 1, and the one line that makes a wrong model
        # harmless here.
        proposed = min(proposed, current)
        # **AND NEVER FASTER THAN THE ONE RATE, INCLUDING ON THE FIRST CYCLE.**
        # RAMPING_DOWN skips `_rate_limit` -- it has to, or the descent could
        # never leave the authority band -- so the one rate has to be enforced
        # here instead, including on the FIRST write.  Without this, that first
        # write jumps straight to `percent_for(last trusted T)`, which is only
        # near the present output when the sample is on its setpoint and the
        # model is right; where it is not, the jump is orders of magnitude
        # larger than the one rate allows.
        #
        # There is no separate descent rate: the curve sets the PATH, and this
        # only says no single write may jump along it.
        proposed = max(proposed, current - self._rampdown_step_pct(current, dt))
        # `current` is known by now -- read, or the value we last commanded and
        # confirmed.  That is what makes this a statement about the heater
        # rather than about a failed transaction.
        if proposed <= safe + self.cfg.dac_step_pct / 2:
            # Reached the safe value: hand it back this cycle and lock out on the
            # next one, so step() still writes it before the early-return kicks in.
            self._rampdown_complete = True
            return safe
        return proposed

    def _rampdown_step_pct(self, current: float, dt: float) -> float:
        """The most one write of a descent may move, in percent.

        **The one rate, through the gain WHERE THE HEATER IS** -- on the curve
        the descent is walking down, not on the tuner's schedule, even though
        on this cryostat those are the same table.  Two reasons for each half:

        * the gain at the present OUTPUT, because that is what converts a step
          in percent into the kelvin the rule is written in.  The target's gain
          is the wrong one whenever the output is still above the curve's
          answer -- which is exactly the case this bound exists for, a descent
          catching up with a target that started far below it.  It is the same
          choice :meth:`_rate_limit` makes, which converts at where the sample
          is rather than where it is going.
        * the steady-state CURVE rather than the schedule, because a controller
          whose two disagree (the legacy harness, CD10 against the fit) would
          otherwise be throttled by one curve while following another and
          arrive late by the difference between them.

        Falls back to :meth:`_rate_pct_per_min` where there is no curve to ask,
        which is the same conversion the tracking rate limiter uses and carries
        the same `min_rate_pct_per_min` floor.  **`has_curve`, not
        `feedforward.enabled`** -- see the seam above.
        """
        if self.has_curve:
            gain = self.feedforward.gain_at(current)
            if gain > 0:
                rate = max(self.cfg.min_rate_pct_per_min,
                           self.ramp.cfg.max_rate_k_per_min / gain)
                return rate * (dt / 60.0)
        return self._rate_pct_per_min(None) * (dt / 60.0)

    def _rate_pct_per_min(self, kelvin: float | None) -> float:
        """**The one rate, converted through the gain.**

        ``max_rate_k_per_min / K(T)``, floored at ``min_rate_pct_per_min``
        where there is no schedule to ask.  The same kelvin per minute at every
        temperature, which is what a rate limit in percent can never be -- the
        gain spans forty-fold across this cryostat.

        **The conversion needs the SCHEDULE, not the scheduler.**  Gated on
        `tuner.enabled` instead, a stage with the tuning switched off runs the
        output limiter on its floor while `set_setpoint` goes on ramping in
        kelvin at the rate the config file names, and a loop told one rate and
        delivering another is one somebody will "tune" without knowing why
        (AUDIT-2026-09-16 finding 3).
        """
        floor = self.cfg.min_rate_pct_per_min
        schedule = self.schedule
        if kelvin is None or schedule is None:
            return floor
        gain = schedule.gain_at(kelvin)
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
