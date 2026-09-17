"""The phase 3 REVIEW's bench -- plans/pid-3-review.md, one section per step.

The same fitted plant and the same six temperatures as `test_bench.py`; what is
new is that the plant can be WRONG.  `FittedHarness.deliver` makes the heater
deliver less power than `P(u)` claims and `FittedHarness.sink_offset` puts the
coldplate above its locus, and between them they are the two scenarios Jeff
separated on 2026-09-15:

1. **a steady, typical-looking change the model explains** -- the bath moves,
   the loop needs less heat, and the worst case is a sample colder than
   intended.  A WARNING however far it goes, including all the way to the
   heater at its floor.
2. **a sudden, aphysical change** -- the watts stop adding up, or the loop
   rails at its ceiling and the sample still will not come up.  A FAULT and a
   ramp-down.

Every test here failed before the step named in its docstring.
"""
from __future__ import annotations

import pytest

from bench_plant import BENCH_TEMPERATURES, STAGE_ENVELOPE, STAGE_FILE, FittedHarness
from ltspm3.control import LoopMode, SupervisorState
from ltspm3.model.fitted_response import DRIFT_T0_UNIX

#: Where the watt residual can speak at all: above `min_output_pct`, so not
#: 10 K (24.2 % of output), and above about 40 K, so not 30 K -- tau there is
#: 9 s against a 30 s slope window.  Between them is where §3R.6's kelvin rows
#: are the only check there is.
WATT_TEMPERATURES = tuple(k for k in BENCH_TEMPERATURES if k >= 60.0)

#: Two months of drift.  The full band at 118 K goes 1.44 -> 52 mW over it.
TWO_MONTHS = 60 * 86400.0


def armed(kelvin, *, prime=60, settle=10, **kw):
    h = FittedHarness(kelvin=kelvin, **kw)
    h.settle_filter(prime)
    h.sup.set_mode(LoopMode.PID)
    h.step(settle)
    return h


def run_until_fault(h, seconds):
    """Step until the loop starts a ramp-down, or give up after ``seconds``."""
    for _ in range(int(seconds / h.DT) + 1):
        h.step(1)
        if h.sup.state in (SupervisorState.RAMPING_DOWN,
                           SupervisorState.LOCKED_OUT):
            return True
    return False


# -- 3R.1, the bench stops depending on the date ----------------------------


def test_the_band_a_level_is_judged_by_is_pinned_to_the_gauge_day():
    """3R.0.B: `sigma_q_w` carries the drift since the level was gauged, so a
    bench reading the real calendar grades a different loop every morning.

    118 K rather than a bench temperature, because that is where the numbers in
    the review's table were measured and where this cryostat has spent its
    life.  1.44 mW is 3 sigma on the gauge day; ten days later the same hold
    allows 8.7 mW and sixty days later 52.
    """
    h = armed(118.0)
    s = h.history[-1]
    assert s.missing_power_w is not None, s.residual_reason
    assert 3e3 * s.sigma_q_w == pytest.approx(1.44, abs=0.005)

    # And the pin is what holds it there: the same hold, two months on.
    old = armed(118.0, wall_t0=DRIFT_T0_UNIX + TWO_MONTHS)
    assert 3e3 * old.history[-1].sigma_q_w > 40.0


@pytest.mark.parametrize("kelvin", WATT_TEMPERATURES)
@pytest.mark.parametrize("wall_t0", [0.0, TWO_MONTHS], ids=["gauge", "+60d"])
@pytest.mark.parametrize("frac", [0.88, 0.97], ids=["12 % lost", "3 % lost"])
def test_a_heater_that_stops_delivering_faults_at_either_date(kelvin, wall_t0, frac):
    """3R.1's gate, and §3.7's wrong-on-purpose docstring finally tested.

    That docstring names this case -- "a heater delivering 12 % less power is a
    genuine fault, and must" -- and nothing exercised it: every row of that
    test perturbs the CONTROLLER's model, which is the opposite experiment.

    **The 3 % row is the one that grades this step.**  12 % is 54 to 93 mW and
    is gross enough to fault through the old dated band as well; 3 % is 17 to
    23 mW -- about five times the 2026-09-10 event -- against a full band that
    has grown to 44-60 mW at +60 d and a fast band that is still 1.4-1.6 mW.
    Measured before this step: the 3 % rows faulted at 230-264 s on the gauge
    day and never at all two months on.

    Both rows are a genuine fault by scenario 2's definition: the watts have
    stopped adding up, and the loop cannot make the power back because
    restoring it needs `1/sqrt(frac)` of the output against a band one percent
    wide.
    """
    h = armed(kelvin, wall_t0=DRIFT_T0_UNIX + wall_t0)
    h.deliver(frac)
    cfg = h.sup.cfg
    assert run_until_fault(h, cfg.fault_window_s + cfg.fault_after_s), (
        f"a {100 * (1 - frac):.0f} % power loss at {kelvin} K never faulted")
    assert any("stepped" in a or "authority exhausted" in a
               for x in h.history[-40:] for a in x.alarms)


# -- 3R.2, CRASHED survives an idle cycle -----------------------------------


@pytest.mark.parametrize("kelvin", BENCH_TEMPERATURES)
def test_a_one_off_exception_stays_crashed_until_acknowledged(kelvin):
    """3R.0.A: the latch lasted exactly one cycle.

    `_step`'s OFF-mode early return reset the state to IDLE unless it was
    LOCKED_OUT, and CRASHED was not excepted -- so one cycle after a crash the
    loop reported idle and `arm` was accepted with no `ack`, which is the whole
    point of the latch gone.

    The bench's own crash row could not see it: its sabotage (`filter = None`)
    re-crashes on every cycle, so the latch was being re-set as fast as it was
    being cleared.  This one detonates ONCE and puts the loop back exactly as
    it was -- which is the shape of a real transient, and the shape nobody has
    looked at yet either.
    """
    h = armed(kelvin)
    before = h.sup.output_pct
    real = h.sup._check_premise

    def once(*a, **kw):
        h.sup._check_premise = real           # a transient, not a broken loop
        raise RuntimeError("a one-off lurch")

    h.sup._check_premise = once
    s = h.step(1)
    assert s.state is SupervisorState.CRASHED
    assert h.sup._check_premise is real, "the sabotage did not clear itself"

    # Twenty healthy cycles later it is still latched, still says why, and
    # still has not moved the heater.
    for _ in range(20):
        s = h.step(1)
        assert s.state is SupervisorState.CRASHED
    assert "one-off lurch" in h.sup._locked_reason
    assert "ack" in s.reason
    assert h.sup.output_pct == pytest.approx(before, abs=1e-9)

    with pytest.raises(PermissionError, match="crashed"):
        h.sup.arm(kelvin)
    h.sup.acknowledge()
    assert h.sup.state is SupervisorState.IDLE


# -- 3R.3, a failed read does not finish a ramp-down ------------------------


def ramping_down(h, *, limit=2000):
    """Drop the sensor and step until the open-loop descent has started."""
    h.cryostat.inject(dropout_channels={"218.1"})
    for _ in range(limit):
        h.step(1)
        if h.sup.state is SupervisorState.RAMPING_DOWN:
            return True
    return False


def step_blind(h, n):
    """Cycles with the bus down: the frame read raises, so the loop is handed
    no reading at all -- the shape `test_safety.py` established."""
    from lschart.transport import TransportError

    last = None
    for _ in range(n):
        h.clock.advance(h.DT)
        try:
            reading = h.read().get("Sample")
        except TransportError:
            reading = None
        last = h.sup.step(h.clock.t, reading)
        h.history.append(last)
    return last


def test_one_failed_read_does_not_finish_the_descent():
    """3R.0.D: `_rampdown_target` read the heater with
    `default=safe_output_pct`, so a failed read said the heater was already at
    zero -- `min(proposed, current)` was zero and the descent declared itself
    finished.  If the write failed too, the loop locked out with the heater
    still near its operating point and a log line saying it had reached base.
    """
    from lschart.transport import TransportError

    h = armed(118.0)
    assert ramping_down(h), "the lost sensor never started a descent"
    h.minutes(5)
    before = h.sup.output_pct
    assert before > 10.0, "the descent is over already; nothing left to test"

    real = h.inst.get_analog_percent
    h.inst.get_analog_percent = lambda: (_ for _ in ()).throw(
        TransportError("simulated read failure"))
    # The cycle has to be one that actually READS.  `_where_the_heater_is`
    # trusts `output_pct` while the previous cycle wrote and was confirmed, and
    # a descent at 118 K writes on most cycles but not all -- 0.013 % a cycle
    # against a 0.01 % code means the quantised value sometimes does not move.
    # This is that cycle, and it is where the defect lived.
    h.sup._wrote_last_cycle = False
    try:
        s = h.step(1)
    finally:
        h.inst.get_analog_percent = real

    assert h.sup.state is SupervisorState.RAMPING_DOWN
    assert not h.sup._rampdown_complete
    # Half a percent is the gap between the two outcomes, not a measurement: a
    # descent at the one rate moves the output by a few hundredths of a percent
    # in a single cycle, while the defect took it to `safe_output_pct` -- tens
    # of percent -- in that same cycle.  Anything in between is a bug either
    # way and should fail here.
    assert s.output_pct == pytest.approx(before, abs=0.5), (
        "the descent jumped on a failed read")
    assert h.sup.output_pct > h.sup.cfg.safe_output_pct + 1.0


def test_a_dead_bus_holds_the_descent_and_says_so():
    """Neither reading nor writing works: the loop stays in RAMPING_DOWN, the
    heater keeps the value it has -- nothing can move it -- and `COMMS LOST`
    appears once `comms_fault_after_s` of it has passed."""
    h = armed(118.0)
    assert ramping_down(h)
    h.minutes(5)
    before = h.sup.output_pct

    h.cryostat.inject(comms_fail=True)
    s = step_blind(h, int(2 * h.sup.cfg.comms_fault_after_s / h.DT))
    assert h.sup.state is SupervisorState.RAMPING_DOWN
    assert not h.sup._rampdown_complete
    assert h.sup.output_pct == pytest.approx(before, abs=1e-9)
    assert any("COMMS LOST" in a for a in s.alarms)

    # And when the bus comes back the descent picks up where it was and
    # finishes -- the latch was never cleared, so there is nothing to re-arm.
    h.cryostat.clear_faults()
    for _ in range(6000):
        h.step(1)
        if h.sup.state is SupervisorState.LOCKED_OUT:
            break
    assert h.sup.state is SupervisorState.LOCKED_OUT
    assert h.sup.output_pct == pytest.approx(h.sup.cfg.safe_output_pct,
                                             abs=h.sup.cfg.dac_step_pct)


def test_a_descent_with_nothing_to_descend_from_holds():
    """The last rung: no readback and no remembered output either.

    There is no number to descend from, so there is no descent -- one cycle
    held, the latch still on, and the alarm says which of the two is missing
    rather than inventing a zero and calling it arrival.
    """
    h = armed(118.0)
    assert ramping_down(h)
    h.minutes(5)

    h.cryostat.inject(comms_fail=True)
    h.sup.output_pct = None                  # nothing remembered either
    s = step_blind(h, 1)
    assert h.sup.state is SupervisorState.RAMPING_DOWN
    assert not h.sup._rampdown_complete
    assert any("RAMP-DOWN HELD" in a for a in s.alarms)


# -- 3R.4, the descent is bounded per cycle ---------------------------------


def assert_descent_obeys_the_one_rate(h):
    """No write of a ramp-down may take the sample faster than the one rate.

    Two statements, because the rule is written in kelvin and applied in
    percent and the conversion is a curve rather than a constant:

    * **in percent**, every pair: `max_rate_k_per_min` through the gain WHERE
      THE HEATER IS, plus two DAC codes for the dither, which writes either
      side of the exact value so consecutive writes can differ by a code in
      each direction.  Recomputed here from the curve rather than asked of the
      supervisor, so it is a statement about the contract and not an echo of
      the arithmetic.
    * **in kelvin**, which is the variable the rule is written in and the only
      one that means the same thing at both ends of this cryostat -- a percent
      is 0.335 K at 10 K and 12.6 K at 118 K.  A tenth of slack for the curve's
      curvature across a single write, and not asked at all once the modelled
      temperature is within half a kelvin of the bottom of the table: down
      there the whole remaining descent is a fraction of a kelvin and
      `kelvin_for` is nearly flat, so the rate has nothing left to constrain.
    """
    from ltspm3.model import fitted_response as M

    sup = h.sup
    ff = sup.feedforward
    rate = sup.ramp.cfg.max_rate_k_per_min
    dac = sup.cfg.dac_step_pct
    rows = [x for x in h.history
            if x.state is SupervisorState.RAMPING_DOWN and x.output_pct is not None]
    assert len(rows) > 5, "no descent to grade"
    for a, b in zip(rows, rows[1:]):
        dt = b.t - a.t
        gain = ff.gain_at(a.output_pct)
        allowed_pct = max(sup.cfg.min_rate_pct_per_min,
                          rate / gain if gain > 0 else 0.0) * dt / 60.0 + 2 * dac
        assert a.output_pct - b.output_pct <= allowed_pct + 1e-9, (
            f"{a.output_pct:.3f} -> {b.output_pct:.3f} % in one cycle, "
            f"against {allowed_pct:.4f} % allowed")
        # And never upward.  Rule 1.
        assert b.output_pct <= a.output_pct + dac + 1e-9

        if ff.kelvin_for(b.output_pct) > M.T_MIN_K + 0.5:
            fell = ff.kelvin_for(a.output_pct) - ff.kelvin_for(b.output_pct)
            allowed_k = 1.1 * rate * dt / 60.0 + 2 * ff.gain_at(b.output_pct) * dac
            assert fell <= allowed_k + 1e-9, (
                f"{a.output_pct:.3f} -> {b.output_pct:.3f} % took the sample "
                f"{fell:.3f} K in one cycle, against {allowed_k:.3f} K allowed")


@pytest.mark.parametrize("kelvin", WATT_TEMPERATURES)
def test_the_first_write_of_a_descent_obeys_the_one_rate(kelvin):
    """3R.0.C, on the public path.

    A heater delivering 12 % less leaves the sample far below its setpoint, and
    the descent then starts from the curve's answer for THAT temperature rather
    than from anywhere near the present output -- so the first write was a jump
    of a percent or more, one to two orders of magnitude past what the one rate
    allows in a cycle.

    The lost-sensor row cannot see any of this: its plant IS the model and its
    sample is on setpoint, so the curve's answer equals the output it is
    already at.
    """
    h = armed(kelvin)
    h.deliver(0.88)
    cfg = h.sup.cfg
    assert run_until_fault(h, cfg.fault_window_s + cfg.fault_after_s)
    h.history.clear()
    h.minutes(20)
    assert_descent_obeys_the_one_rate(h)


@pytest.mark.parametrize("kelvin", BENCH_TEMPERATURES)
def test_the_lost_sensor_descent_is_bounded_too(kelvin):
    """The same bound on §3.7's own row, which asserted monotone and not
    bounded.  Where the plant is the model this changes nothing, which is the
    point: the descent time is unchanged and the bound is now stated."""
    h = armed(kelvin)
    assert ramping_down(h)
    h.history.clear()
    h.minutes(20)
    assert_descent_obeys_the_one_rate(h)


# -- 3R.5, the descent never lacks a start ----------------------------------


def test_a_fault_before_the_filter_primes_still_descends_at_the_one_rate():
    """3R.0.F: with no trusted temperature the descent fell back to
    `min_rate_pct_per_min`, which is hours rather than the tens of minutes a
    fault descent at the one rate takes (docs/ltspm3/control.md).

    The state that reaches it is not exotic: `acknowledge` resets the filter,
    so the cycles immediately after an `ack` and a re-arm have no primed
    measurement, and a sensor that drops out there has never given the loop a
    temperature to descend from.  The model has one -- `kelvin_for(current)`
    is what an open-loop descent stands on anyway.
    """
    h = armed(118.0)
    h.sup.acknowledge()
    h.sup.arm(118.0)
    assert not h.sup.filter.primed, "the ack should have emptied the filter"
    h.cryostat.inject(dropout_channels={"218.1"})

    t0 = h.clock.t
    for _ in range(20000):
        h.step(1)
        if h.sup.state is SupervisorState.LOCKED_OUT:
            break
    assert h.sup.state is SupervisorState.LOCKED_OUT, "never finished the descent"
    minutes = (h.clock.t - t0) / 60.0
    # Forty minutes separates the two branches rather than grading the descent:
    # the one rate finishes in roughly half that from 118 K
    # (`test_stage_4a.py`'s `RAMPDOWN_MINUTES`), and the floor rate this defect
    # fell back to takes hours.  Nothing in between is reachable.
    assert minutes < 40.0, f"the descent took {minutes:.0f} min"


def test_a_descent_that_starts_at_t_zero_still_descends():
    """3R.0.F's second half: `self._rampdown_t0 or t` reads a start time of
    exactly 0.0 as "not captured", so every cycle recomputed `elapsed` as zero
    and the target temperature never moved.

    A virtual clock starts at 0.0, and so does a monotonic one on some
    platforms.  `_rampdown_target` is driven directly here because the harness
    advances its clock before the first cycle, which is exactly the shape that
    hid this.
    """
    from ltspm3.control.supervisor import SupervisorStatus

    h = armed(118.0)
    sup = h.sup
    first = sup._rampdown_target(0.0, SupervisorStatus(t=0.0), "test", h.DT)
    later = sup._rampdown_target(120.0, SupervisorStatus(t=120.0), "test", h.DT)
    assert sup._rampdown_t0 == 0.0
    assert later < first, (
        f"two minutes of descent moved the target {first:.3f} -> {later:.3f} %")


# -- 3R.6, the kelvin rows are reachable, and they answer the two scenarios --


def alarms_matching(h, needle):
    return [a for x in h.history for a in x.alarms if needle in a]


def test_a_heater_at_half_power_at_10_k_warns_and_never_faults():
    """Scenario 2 at the coldest bench point -- and it stops at a warning
    because the consequence is small, not because nothing noticed.

    The residual cannot speak here at all: 24.2 % of output is below
    `min_output_pct`, so `dQ` has no opinion whatever the heater does.  The
    kelvin rows are the only check there is, and before this step BOTH were
    gated on the tuner's `hold` phase, which any error over 0.25 K leaves.

    Measured: the sample settles 2.03 K low, railed at the ceiling, and warns
    on every cycle for two hours.  Past five kelvin it would fault; two is not
    five, and a loop that ramps the cryostat down over two kelvin is worse than
    one that says so and keeps holding.
    """
    h = armed(10.0)
    h.deliver(0.5)
    h.history.clear()
    h.minutes(120)

    assert h.sup.state is SupervisorState.TRACKING
    assert h.history[-1].missing_power_w is None, "the residual should be silent"
    assert alarms_matching(h, "warn_error_k"), "nothing warned at 2 K of error"
    assert alarms_matching(h, "below min_output_pct"), (
        "the warning should say why kelvin is the only check here")
    worst = max(abs(x.error_k) for x in h.history if x.error_k is not None)
    assert worst < h.sup.cfg.fault_error_k, f"{worst:.2f} K -- this should fault"


def test_a_heater_at_half_power_at_30_k_faults_as_authority_exhausted():
    """Scenario 2 where the consequence is real, and the case the review
    measured: an hour at 14.3 K low, railed at the ceiling, in `tracking`, with
    no alarm of any kind.

    30 K is above `min_output_pct` and below the 40 K where the plant is slower
    than the slope window, so this is the gap where the watt residual has no
    opinion and the kelvin rows are the whole safety case.
    """
    h = armed(30.0)
    h.deliver(0.5)
    h.history.clear()
    assert run_until_fault(h, 3600.0), "14 K low and railed, and it never faulted"

    assert alarms_matching(h, "warn_error_k")
    assert alarms_matching(h, "authority exhausted")
    assert not alarms_matching(h, "at its floor"), "railed HIGH, not low"

    # And it goes the whole way: descend, lock out, refuse `arm`, clear on ack.
    for _ in range(6000):
        h.step(1)
        if h.sup.state is SupervisorState.LOCKED_OUT:
            break
    assert h.sup.state is SupervisorState.LOCKED_OUT
    with pytest.raises(PermissionError):
        h.sup.arm(30.0)
    h.sup.acknowledge()
    assert h.sup.state is SupervisorState.IDLE


@pytest.mark.parametrize("kelvin,per_hour,minutes,stage,keeps_up", [
    (118.0, 2.0, 180, STAGE_ENVELOPE, False),
    (118.0, 2.0, 180, STAGE_FILE, True),
    (30.0, 20.0, 90, STAGE_ENVELOPE, False),
])
def test_a_rising_sink_warns_however_far_it_goes(kelvin, per_hour, minutes, stage,
                                                  keeps_up):
    """**Scenario 1, and it never faults** (Jeff, 2026-09-15).

    The bath moves, the loop needs less heat, and the worst case is a sample
    colder than intended -- the safe direction.  A ramp-down would not improve
    it and a lockout would stop the loop resuming when the bath recovers.

    Three cases.  At 118 K and 2 K/h on the design ENVELOPE (`STAGE_ENVELOPE`,
    the speeds `bench_plant` pins) the loop cannot keep up: the error passes
    `warn_error_k`, the error row warns, and nothing else happens.  The same
    disturbance on the FILE the cryostat is armed with (`STAGE_FILE`, a
    stronger hold -- docs/ltspm3/requirements.md §3) is **kept up with**, so
    nothing warns because nothing is wrong.  A loop that tracks a slow bath
    drift instead of reporting it is the point of the retune, and this is where
    that is graded.  At 30 K and 20 K/h the output reaches the hard minimum
    partway through and the sample then runs well over setpoint: the FLOOR
    warning is what says so, and it is a warning precisely because there is
    nothing left for the loop to do about it.  Ninety minutes for that one: the
    sink correction is linear in `Lambda'` at the midpoint, and twenty kelvin
    an hour leaves the range that is honest in soon after.

    Before this step the sink was scenery -- `_aux_base` moved the thermometer
    and left the plant where it was -- so the disturbance the loop was supposed
    to react to did not exist, and the old row hedged with `if faulted`.
    """
    h = armed(kelvin, stage=stage)
    h.history.clear()
    worst_error_k = 0.0
    for i in range(int(minutes * 60 / h.DT)):
        h.sink_offset(per_hour * (i * h.DT / 3600.0))
        st = h.step(1)
        assert h.sup.state is SupervisorState.TRACKING, (
            f"scenario 1 must never stop the loop: {h.history[-1].alarms}")
        if st.error_k is not None:
            worst_error_k = max(worst_error_k, abs(st.error_k))

    outs = [x.output_pct for x in h.history if x.output_pct is not None]
    assert outs[-1] < outs[0], "the loop should need LESS heat, not more"
    for a, b in zip(outs, outs[1:]):
        assert b <= a + 2 * h.sup.cfg.dac_step_pct + 1e-9, "the output rose"
    if keeps_up:
        # The strong loop's whole point: a slow bath drift is corrected, not
        # reported.  If this starts failing the hold has been weakened again.
        assert worst_error_k < h.sup.cfg.warn_error_k, worst_error_k
        assert not alarms_matching(h, "warn_error_k")
    else:
        assert alarms_matching(h, "warn_error_k"), "a kelvin of error and no warning"
    if min(outs) <= h.sup.cfg.hard_min_pct + h.sup.cfg.dac_step_pct:
        assert alarms_matching(h, "at its floor"), (
            "the heater reached its floor and nothing said so")
    assert not alarms_matching(h, "authority exhausted")


@pytest.mark.parametrize("kelvin", BENCH_TEMPERATURES)
def test_a_commanded_sweep_raises_no_kelvin_alarm(kelvin):
    """The trajectory gate is not the old ramp allowance under another name.

    A 10 K sweep at Jeff's 5 K/min lags by `r*tau` -- 7.6 K at 10 K and 1.3 K
    at 140 K -- and every bit of that is commanded.  The kelvin rows must be
    silent for all of it, which is what gating them on the trajectory rather
    than on the size of the error buys.
    """
    h = armed(kelvin)
    h.sup.sweep_to(kelvin + 10.0, 5.0)
    h.history.clear()
    h.minutes(20)

    assert h.sup.state is SupervisorState.TRACKING
    moving = [x for x in h.history if x.ramping]
    assert moving, "the sweep was over before it started"
    for x in moving:
        assert not [a for a in x.alarms if "warn_error_k" in a or "at its floor" in a
                    or "authority exhausted" in a], x.alarms


# -- 3R.7, a stale sink is no opinion ---------------------------------------


def test_a_coldplate_that_drops_out_takes_the_residual_with_it():
    """3R.0.G: `_coldplate_k` was refreshed only on a usable reading and never
    expired, so after the channel dropped out the residual ran on a remembered
    sink for as long as the loop ran.

    `Λ(T_s) − Λ(T_c)` is most of the residual and `Λ'` at 6.6 K is nine times
    `Λ'` at 118 K, so 100 mK of sink is worth 1.5 mW against a 1.44 mW band --
    a coldplate that moves while nobody can see it lands in `dQ` as if it were
    the sample's problem.

    No opinion, then, and no fault when the channel comes back either: the step
    history breaks on a no-opinion cycle, so the sink's return is not a step.
    """
    h = armed(118.0)
    assert h.history[-1].missing_power_w is not None

    h.cryostat.inject(dropout_channels={"218.2"})
    h.minutes(10)
    assert h.sup.state is SupervisorState.TRACKING
    assert h.history[-1].missing_power_w is None
    assert "coldplate stale" in h.history[-1].residual_reason

    # The loop itself is unaffected -- this is the JUDGE going quiet, not the
    # controller losing its sensor.
    assert h.history[-1].filtered_k == pytest.approx(118.0, abs=0.1)

    h.cryostat.clear_faults()
    h.history.clear()
    h.minutes(40)
    assert h.sup.state is SupervisorState.TRACKING
    assert h.history[-1].missing_power_w is not None, "never spoke again"
    assert not alarms_matching(h, "stepped"), "the sink's return read as a step"


# -- 3R.8, two small truths -------------------------------------------------


def test_a_ramping_loop_has_no_opinion_about_its_model():
    """3R.0.H: `model_trusted` defaulted to True and `_check_model` is the only
    thing that writes it -- and that only runs at a settled hold.

    So every status written during a ramp, or while a fault was frozen, carried
    a trust nothing had established.  None is the honest answer, and it is the
    default now: no opinion is not trust.
    """
    h = armed(118.0)
    assert h.history[-1].model_trusted is True, "a settled hold should judge"

    h.sup.sweep_to(128.0, 5.0)
    h.history.clear()
    h.minutes(1)
    assert h.history[-1].ramping
    assert h.history[-1].model_trusted is None


def test_a_loop_that_has_never_read_an_output_primes_on_a_percent():
    """3R.0.H's other half: the prime fallback was `s.filtered_k`.

    `prime` takes the output the loop starts from, so at 118 K that primed the
    PID's bias at 118 % of output -- clamped to the band's ceiling, which is a
    loop opening at full authority for no reason at all.  The operating point
    is what a loop with no idea where the heater is has to assume, and it is
    what `_enter_mode` already assumed one line away.
    """
    h = armed(118.0)
    h.sup.acknowledge()
    h.sup.arm(118.0)
    h.sup.output_pct = None                  # nothing read, nothing remembered
    h.sup._pending_approach = True
    h.sup.ramp.jump_to(108.0)                # a gap big enough to re-approach
    # Long enough for the filter and the guard to come back after the `ack`,
    # which is what the deferred approach is waiting for.
    h.step(40)
    assert h.sup.pid.bias == pytest.approx(
        h.sup.clamp(h.sup.cfg.operating_point_pct), abs=1e-9)
    assert h.sup.pid.bias <= h.sup.cfg.hard_max_pct
