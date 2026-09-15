"""The behaviours Jeff specified, stated as executable requirements."""

import pytest

from lschart.transport import TransportError
from ltspm3.control import HealthState, LoopMode, SupervisorConfig, SupervisorState, PIDConfig


# -- "sample drops to 0 K suddenly -> it shouldn't react" -------------------

def test_single_dropout_to_zero_does_not_move_the_heater(armed):
    h = armed()
    before = h.sup.output_pct

    h.cryostat.inject(dropout_channels={"218.1"})
    st = h.step(1)

    assert st.raw_k == 0.0
    assert st.validity.value == "no_sensor"
    assert st.health is HealthState.SUSPECT
    assert st.state is SupervisorState.HOLDING
    assert h.sup.output_pct == before, "a dropout must not move the heater at all"
    assert not st.wrote


def test_brief_dropout_then_recovery_returns_to_tracking(armed):
    h = armed()
    before = h.sup.output_pct

    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(5)                       # 20 s of dropout, below fault_after_s=60
    assert h.sup.state is SupervisorState.HOLDING
    assert h.sup.output_pct == before

    h.cryostat.clear_faults()
    h.step(4)
    assert h.sup.guard.state is HealthState.RECOVERING, "recovery must not be instant"
    h.step(3)
    assert h.sup.guard.state is HealthState.OK
    assert h.sup.state is SupervisorState.TRACKING


# -- "if it is heating and it doesn't come back, slowly ramp to zero" -------

def test_sustained_dropout_ramps_down_slowly(armed):
    h = armed()
    start = h.sup.output_pct

    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(160)                                  # >=600 s bad -> FAULT
    assert h.sup.guard.state is HealthState.FAULT
    assert h.sup.state is SupervisorState.RAMPING_DOWN

    h.step(150)                                  # 10 more minutes
    dropped = start - h.sup.output_pct
    assert dropped > 0, "must actually be reducing heat"
    # THE RATE IS IN KELVIN NOW.  Ten minutes at `max_rate_k_per_min` is 50 K
    # of sample, and what that costs in percent is the model's business -- 3.9 %
    # from the 63 K the harness holds.  Asserting a percent here would be
    # asserting the gain.
    minutes = 150 * h.DT / 60.0
    fell_k = (h.sup.feedforward.kelvin_for(start)
              - h.sup.feedforward.kelvin_for(h.sup.output_pct))
    assert fell_k <= h.sup.ramp.cfg.max_rate_k_per_min * (minutes + 1.5), (
        f"ramped too fast: {fell_k:.1f} K in {minutes:.1f} min")
    outs = [s.output_pct for s in h.history if s.output_pct is not None]
    assert all(b <= a + 1e-9 for a, b in zip(outs, outs[1:])), "ramp must be monotonic down"


def test_ramp_down_reaches_safe_value_and_locks_out(armed):
    cfg = SupervisorConfig(safe_output_pct=62.5, authority_pct=2.0,
                           require_ack_after_fault=True)
    h = armed(sup_cfg=cfg)
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(1200)
    assert h.sup.state is SupervisorState.LOCKED_OUT
    assert h.sup.output_pct == pytest.approx(62.5, abs=0.011)

    with pytest.raises(PermissionError):
        h.sup.set_mode(LoopMode.PID)
    h.sup.acknowledge()
    assert h.sup.state is SupervisorState.IDLE


# -- "sudden multiple percentage point change in power needed -> don't" -----

def test_a_stepped_setpoint_becomes_a_RAMP_rather_than_stalling_the_loop(armed):
    """Rule 8, moved to where it belongs -- phase 3 step 6.

    "Move the setpoint by ramping it, never by stepping it" used to be enforced
    by the premise check: a step past `max_error_k` produced an error the check
    read as a broken premise, so the loop froze and eventually ramped down.
    Rule 8 was protecting the cryostat by BREAKING the loop, and it only worked
    because the check could not tell a commanded move from a fault.

    The watt residual can, so the kelvin error is a warning now -- and a
    stepped setpoint would simply be obeyed.  The refusal therefore moves to
    the REQUEST: a step larger than `warn_error_k` becomes a ramp at the one
    rate.  The heater still never lurches, which is what rule 8 is for.
    """
    h = armed()
    before = h.sup.output_pct

    h.sup.set_setpoint(140.0, ramp=False)   # 40 K away
    assert h.sup.ramp.ramping, "a step this large must have become a ramp"
    st = h.step(3)

    assert st.state is SupervisorState.TRACKING
    assert abs(h.sup.output_pct - before) <= 3 * (
        h.sup._rate_limit_step(h.DT) + h.sup.cfg.dac_step_pct)


def test_a_trim_smaller_than_the_warning_is_still_a_step(armed):
    """`ramp=False` keeps meaning what its docstring says for a small move."""
    h = armed()
    h.sup.set_setpoint(h.equilibrium_k + 0.5, ramp=False)
    assert not h.sup.ramp.ramping
    assert h.sup.status.state is not SupervisorState.RAMPING_DOWN


def test_a_standing_error_WARNS_and_keeps_tracking(armed):
    """§3.4's first row.  An alarm is not a freeze.

    The old check froze the output on any anomaly and escalated on a timer,
    which on a cryostat whose legitimate sweep lag is 43 K meant that freezing
    was the normal outcome of doing what it was told.  Only a FAULT stops the
    loop now, and a kelvin error is not one.
    """
    h = armed()
    h.sup.sweep_to(h.equilibrium_k + 6.0, rate_k_per_min=5.0)
    h.step(30)
    warned = [s for s in h.history
              if any("warn_error_k" in a for a in s.alarms)]
    if warned:
        assert all(s.state is SupervisorState.TRACKING for s in warned), (
            "a warning froze the loop")


def test_the_integral_does_not_charge_while_a_fault_is_held(armed):
    """A fault freezes the output, and the integral must not keep charging
    against a premise nobody believes."""
    h = armed()
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(3)
    assert h.sup.state is SupervisorState.HOLDING
    # Assert on the *contribution* ki*I, not the raw integral: gain scheduling
    # rescales the stored integral whenever ki changes, precisely so that the
    # contribution is preserved.  Rescaling is not charging.
    charge_after_hold = abs(h.sup.pid.cfg.ki * h.sup.pid.integral)
    h.step(30)
    charge_now = abs(h.sup.pid.cfg.ki * h.sup.pid.integral)
    assert charge_now <= charge_after_hold + 1e-6, "integral charged while holding"


# -- hard limits ------------------------------------------------------------

def test_output_can_never_exceed_the_hard_ceiling(armed):
    """Rule 5, as phase 3 step 4 rewords it.

    The band caps heat and now FOLLOWS THE SETPOINT, so "the band" is no longer
    a pair of constants a test can quote.  What is still absolute is
    `hard_max_pct`: whatever the model, the setpoint or the arithmetic says,
    the output cannot go above it.

    This test used to assert `max(outs) <= 63.25` with the setpoint at 300 K --
    and passing that was the bug, not the safety property.  A loop asked for
    300 K and clamped 5 % below the output that reaches it is not being safe,
    it is being unable to do its job, and the same arithmetic made every sweep
    below 60 K impossible.  Here the loop now walks up toward the ceiling and
    stops there, which is what a ceiling is for.
    """
    cfg = SupervisorConfig(operating_point_pct=63.0, authority_pct=0.25,
                           hard_max_pct=68.0,
                           warn_error_k=1000, anomaly_demand_pct=1000)
    h = armed(sup_cfg=cfg, pid_cfg=PIDConfig(setpoint=300.0, kp=5.0, ti=10.0))
    h.sup.set_setpoint(300.0, ramp=False)
    h.step(400)
    outs = [s.output_pct for s in h.history if s.output_pct is not None]
    assert max(outs) <= cfg.hard_max_pct + 1e-9, "exceeded the hard ceiling"
    assert max(outs) > 63.25, (
        "never left the old fixed band -- has step 4 been reverted?")

    # And it gets there at the rate limit, not in one write.  A band that opens
    # is not heat; the rate limiter is what decides how fast the heater travels
    # into it, and that is the whole reason the centre needs no slew limit of
    # its own.
    for a, b in zip(h.history, h.history[1:]):
        if a.output_pct is None or b.output_pct is None:
            continue
        if b.state is not SupervisorState.RAMPING_DOWN:
            allowed = h.sup._rate_limit_step(h.DT) + 2 * cfg.dac_step_pct
            assert b.output_pct - a.output_pct <= allowed, "jumped upward"


def test_nothing_moves_the_output_down_except_a_ramp_down(armed):
    """Below the band is less heat, which is never the dangerous direction --
    but *moving* there is still only a fault response's business."""
    cfg = SupervisorConfig(operating_point_pct=63.0, authority_pct=0.25,
                           warn_error_k=1000, anomaly_demand_pct=1000)
    h = armed(sup_cfg=cfg, pid_cfg=PIDConfig(setpoint=300.0, kp=5.0, ti=10.0))
    h.sup.set_setpoint(300.0, ramp=False)
    h.step(400)
    for a, b in zip(h.history, h.history[1:]):
        if a.output_pct is None or b.output_pct is None:
            continue
        floor = h.sup.band[0]
        if b.output_pct < floor - 1e-9 and b.output_pct < a.output_pct - 1e-9:
            assert b.state is SupervisorState.RAMPING_DOWN, (
                f"moved below the band in state {b.state.value}"
            )


def test_per_step_rate_limit_is_respected_while_tracking(armed):
    """The limit is DERIVED now -- `max_rate_k_per_min / K(T)` -- so the test
    asks the loop what it is rather than quoting a constant that no longer
    exists."""
    cfg = SupervisorConfig(warn_error_k=1000, anomaly_demand_pct=1000)
    h = armed(sup_cfg=cfg, pid_cfg=PIDConfig(setpoint=200.0, kp=5.0, ti=50.0))
    h.sup.set_setpoint(200.0, ramp=False)
    h.step(100)
    pairs = list(zip(h.history, h.history[1:]))
    steps = [abs(b.output_pct - a.output_pct) for a, b in pairs
             if a.output_pct is not None and b.output_pct is not None
             and b.state is SupervisorState.TRACKING]
    assert steps, "test never actually tracked"
    # Without this the test is vacuous: delete the rate limiter and the output
    # jumps to the band ceiling in one cycle and then sits there, so every step
    # recorded here is exactly zero and the ceiling below passes trivially.
    assert max(steps) > 0, "the output never moved: the limiter was not exercised"
    # one dither code of slack on top of the DERIVED per-cycle step
    allowed = h.sup._rate_limit_step(h.DT) + cfg.dac_step_pct / 2
    assert max(steps) <= allowed + 1e-9, f"largest step {max(steps):.4f}%"


def test_off_mode_never_writes(harness):
    h = harness()
    h.settle_filter(20)
    n_before = len(h.sim.write_log)
    h.sup.set_setpoint(300.0, ramp=False)
    h.step(50)
    assert len(h.sim.write_log) == n_before


# -- comms ------------------------------------------------------------------

def test_comms_failure_does_not_crash_the_loop(armed):
    h = armed()
    h.cryostat.inject(comms_fail=True)
    for _ in range(5):
        h.clock.advance(4.0)
        try:
            # `.step` takes one Reading, not the whole frame.  Passing the
            # frame used to be invisible here because every read raises while
            # comms are down, so the success branch never ran.
            reading = h.read().get("Sample")
        except TransportError:
            reading = None
        st = h.sup.step(h.clock.t, reading)
    assert st.health in (HealthState.SUSPECT, HealthState.FAULT)
    h.cryostat.clear_faults()
    h.step(20)
    assert h.sup.guard.state in (HealthState.OK, HealthState.RECOVERING)


def test_instrument_rdgst_fault_is_believed(armed):
    h = armed()
    before = h.sup.output_pct
    h.cryostat.inject(rdgst_channels={"218.1": 32})     # temp overrange
    st = h.step(2)
    assert st.validity.value == "inst_fault"
    assert h.sup.output_pct == before
