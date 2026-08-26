"""The behaviours Jeff specified, stated as executable requirements."""

import pytest

from ltspm3.control import HealthState, LoopMode, SupervisorConfig, SupervisorState, PIDConfig
from ltspm3.control.health import SensorGuardConfig


def armed(harness, **kw):
    h = harness(**kw)
    h.settle_filter(40)
    h.sup.set_mode(LoopMode.PID)
    h.step(10)
    return h


# -- "sample drops to 0 K suddenly -> it shouldn't react" -------------------

def test_single_dropout_to_zero_does_not_move_the_heater(harness):
    h = armed(harness)
    before = h.sup.output_pct

    h.cryostat.inject(dropout_channels={"218.1"})
    st = h.step(1)

    assert st.raw_k == 0.0
    assert st.validity.value == "no_sensor"
    assert st.health is HealthState.SUSPECT
    assert st.state is SupervisorState.HOLDING
    assert h.sup.output_pct == before, "a dropout must not move the heater at all"
    assert not st.wrote


def test_brief_dropout_then_recovery_returns_to_tracking(harness):
    h = armed(harness)
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

def test_sustained_dropout_ramps_down_slowly(harness):
    h = armed(harness)
    start = h.sup.output_pct

    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(160)                                  # >=600 s bad -> FAULT
    assert h.sup.guard.state is HealthState.FAULT
    assert h.sup.state is SupervisorState.RAMPING_DOWN

    h.step(150)                                  # 10 more minutes
    dropped = start - h.sup.output_pct
    assert dropped > 0, "must actually be reducing heat"
    # rampdown_pct_per_min=0.5 over the ~10 min actually spent ramping.
    assert dropped <= 0.5 * 11.5, f"ramped too fast: {dropped:.3f}%"
    outs = [s.output_pct for s in h.history if s.output_pct is not None]
    assert all(b <= a + 1e-9 for a, b in zip(outs, outs[1:])), "ramp must be monotonic down"


def test_ramp_down_reaches_safe_value_and_locks_out(harness):
    cfg = SupervisorConfig(rampdown_pct_per_min=60.0, safe_output_pct=62.5,
                           authority_pct=2.0, require_ack_after_fault=True)
    h = armed(harness, sup_cfg=cfg)
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(200)
    assert h.sup.state is SupervisorState.LOCKED_OUT
    assert h.sup.output_pct == pytest.approx(62.5, abs=0.011)

    with pytest.raises(PermissionError):
        h.sup.set_mode(LoopMode.PID)
    h.sup.acknowledge()
    assert h.sup.state is SupervisorState.IDLE


# -- "sudden multiple percentage point change in power needed -> don't" -----

def test_large_error_is_treated_as_a_broken_premise_not_a_command(harness):
    """A large error that was *not* commanded means the cryostat is wrong, not the loop.

    Stepping the setpoint (``ramp=False``) is the way to manufacture this in a
    test.  In normal use setpoint moves ramp, precisely so that a large error
    keeps its meaning as evidence of a fault -- see test_sweep.py.
    """
    h = armed(harness)
    before = h.sup.output_pct

    h.sup.set_setpoint(140.0, ramp=False)   # 40 K away: outside max_error_k=1.0
    st = h.step(3)

    assert st.state is SupervisorState.HOLDING
    assert any("max_error_k" in a for a in st.alarms)
    assert h.sup.output_pct == before, "must not chase a setpoint this far away"


def test_persistent_anomaly_escalates_to_ramp_down(harness):
    cfg = SupervisorConfig(anomaly_hold_s=60.0)
    h = armed(harness, sup_cfg=cfg)
    h.sup.set_setpoint(140.0, ramp=False)
    h.step(5)
    assert h.sup.state is SupervisorState.HOLDING
    h.step(20)                          # past anomaly_hold_s
    assert h.sup.state in (SupervisorState.RAMPING_DOWN, SupervisorState.LOCKED_OUT)


def test_anomaly_hold_does_not_wind_up_the_integral(harness):
    h = armed(harness)
    h.sup.set_setpoint(h.equilibrium_k + 1.5, ramp=False)  # outside max_error_k
    h.step(30)
    # Assert on the *contribution* ki*I, not the raw integral: gain scheduling
    # rescales the stored integral whenever ki changes, precisely so that the
    # contribution is preserved.  Rescaling is not charging.
    charge_after_hold = abs(h.sup.pid.cfg.ki * h.sup.pid.integral)
    h.step(30)
    charge_now = abs(h.sup.pid.cfg.ki * h.sup.pid.integral)
    assert charge_now <= charge_after_hold + 1e-6, "integral charged while holding"


# -- hard limits ------------------------------------------------------------

def test_output_can_never_exceed_the_authority_band_upward(harness):
    """The band caps *heat*, unconditionally.

    Going below the band is allowed, but only as a fault ramp-down -- that is
    the one case where leaving the band is the safe direction to leave it in.
    """
    cfg = SupervisorConfig(operating_point_pct=63.0, authority_pct=0.25,
                           max_error_k=1000, anomaly_demand_pct=1000)
    h = armed(harness, sup_cfg=cfg, pid_cfg=PIDConfig(setpoint=300.0, kp=5.0, ti=10.0))
    h.sup.set_setpoint(300.0, ramp=False)
    h.step(400)
    outs = [s.output_pct for s in h.history if s.output_pct is not None]
    assert max(outs) <= 63.25 + 1e-9, "exceeded upward authority"
    # Sitting below the band is fine once a ramp has taken us there (a hold just
    # freezes wherever we are).  What must never happen is *moving* below the
    # band for any reason other than a fault ramp-down.
    for a, b in zip(h.history, h.history[1:]):
        if a.output_pct is None or b.output_pct is None:
            continue
        if b.output_pct < 62.75 - 1e-9 and b.output_pct < a.output_pct - 1e-9:
            assert b.state is SupervisorState.RAMPING_DOWN, (
                f"moved below the band in state {b.state.value}"
            )


def test_per_step_rate_limit_is_respected_while_tracking(harness):
    cfg = SupervisorConfig(max_step_pct=0.02, max_error_k=1000, anomaly_demand_pct=1000)
    h = armed(harness, sup_cfg=cfg, pid_cfg=PIDConfig(setpoint=200.0, kp=5.0, ti=50.0))
    h.sup.set_setpoint(200.0, ramp=False)
    h.step(100)
    pairs = list(zip(h.history, h.history[1:]))
    steps = [abs(b.output_pct - a.output_pct) for a, b in pairs
             if a.output_pct is not None and b.output_pct is not None
             and b.state is SupervisorState.TRACKING]
    assert steps, "test never actually tracked"
    # one dither code (0.01) of slack on top of the 0.02 step limit
    assert max(steps) <= 0.02 + 0.01 / 2 + 1e-9, f"largest step {max(steps):.4f}%"


def test_off_mode_never_writes(harness):
    h = harness()
    h.settle_filter(20)
    n_before = len(h.sim.write_log)
    h.sup.set_setpoint(300.0, ramp=False)
    h.step(50)
    assert len(h.sim.write_log) == n_before


# -- comms ------------------------------------------------------------------

def test_comms_failure_does_not_crash_the_loop(harness):
    h = armed(harness)
    h.cryostat.inject(comms_fail=True)
    for _ in range(5):
        h.clock.advance(4.0)
        try:
            reading = h.read()
        except Exception:
            reading = None
        st = h.sup.step(h.clock.t, reading)
    assert st.health in (HealthState.SUSPECT, HealthState.FAULT)
    h.cryostat.clear_faults()
    h.step(20)
    assert h.sup.guard.state in (HealthState.OK, HealthState.RECOVERING)


def test_instrument_rdgst_fault_is_believed(harness):
    h = armed(harness)
    before = h.sup.output_pct
    h.cryostat.inject(rdgst_channels={"218.1": 32})     # temp overrange
    st = h.step(2)
    assert st.validity.value == "inst_fault"
    assert h.sup.output_pct == before
