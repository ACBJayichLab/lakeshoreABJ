"""Stage 0.1 and 0.2 of the commissioning path: the two-rate ramp-down, and
the rule that once one starts, **automation** may not call it off.

Both are exit-gate items in ``docs/ltspm3/commissioning.md``.  The latch is a
behaviour change, not a tuning change: before it, a ramp-down whose cause
cleared was quietly abandoned mid-way and the loop went back to tracking, and
only a ramp that ran all the way to ``safe_output_pct`` ever locked out.
"""

from __future__ import annotations

import pytest

from ltspm3.control import LoopMode, SupervisorState
from ltspm3.control.health import HealthState
from ltspm3.control.supervisor import SupervisorConfig


# -- 0.1  two rates, and the knee between them -----------------------------

def test_the_ramp_down_crosses_the_knee_and_changes_slope(armed):
    """Above the knee 1 %/min, at or below it 2 %/min.

    Power goes as pct**2, so at 40% the heater delivers about 40% of the power
    it had at the operating point: the thermal shock per percent is much
    smaller down there and there is less reason to crawl.
    """
    cfg = SupervisorConfig(rampdown_knee_pct=40.0,
                           rampdown_pct_per_min=1.0,
                           rampdown_below_knee_pct_per_min=2.0,
                           anomaly_hold_s=60.0, authority_pct=30.0)
    h = armed(sup_cfg=cfg)
    h.cryostat.inject(dropout_channels={"218.1"})

    above, below = [], []
    prev = h.sup.output_pct
    for _ in range(2000):
        h.step(1)
        now = h.sup.output_pct
        if h.sup.state is SupervisorState.RAMPING_DOWN and now is not None and prev is not None:
            drop = prev - now
            if drop > 0:
                (above if prev > cfg.rampdown_knee_pct else below).append(drop)
        prev = now
        if h.sup.state is SupervisorState.LOCKED_OUT:
            break

    assert above and below, "the ramp must be seen on both sides of the knee"
    fast = sum(below) / len(below)
    slow = sum(above) / len(above)
    assert fast == pytest.approx(2.0 * slow, rel=0.15), (
        f"below the knee should be twice as fast: {slow:.4f} -> {fast:.4f} %/cycle"
    )


def test_a_non_positive_rampdown_rate_is_refused():
    from lschart.config import AppConfig
    from ltspm3.config import ControlConfig, validate_control

    problems: list[str] = []
    cfg = ControlConfig(supervisor=SupervisorConfig(rampdown_pct_per_min=0.0))
    validate_control(cfg, AppConfig(), problems)
    assert any("rampdown_pct_per_min must be positive" in p for p in problems)


def test_a_knee_outside_the_hard_limits_is_refused():
    from lschart.config import AppConfig
    from ltspm3.config import ControlConfig, validate_control

    problems: list[str] = []
    cfg = ControlConfig(supervisor=SupervisorConfig(rampdown_knee_pct=90.0,
                                                    hard_max_pct=70.0))
    validate_control(cfg, AppConfig(), problems)
    assert any("rampdown_knee_pct" in p for p in problems)


# -- 0.2  the latch, and the human exemption from it -----------------------

def test_a_recovered_sensor_does_not_resume_tracking_mid_ramp_down(armed):
    """The whole content of 0.2.

    `_pid_target` used to check sensor health *first*, so once the guard
    returned to OK it fell through to normal tracking and set TRACKING -- the
    ramp-down abandoned, with nobody told and nothing looked at.
    """
    h = armed()
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(200)                                   # -> FAULT -> RAMPING_DOWN
    assert h.sup.state is SupervisorState.RAMPING_DOWN
    during = h.sup.output_pct

    h.cryostat.clear_faults()                     # the sensor comes back
    h.step(60)
    assert h.sup.guard.state is HealthState.OK, "the guard should have recovered"

    assert h.sup.state is not SupervisorState.TRACKING, (
        "automation resumed a ramp-down it did not start"
    )
    assert h.sup.state in (SupervisorState.RAMPING_DOWN, SupervisorState.LOCKED_OUT)
    assert h.sup.output_pct < during, "the ramp must have kept going"


def test_set_mode_pid_is_refused_while_ramping_down(armed):
    h = armed()
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(200)
    assert h.sup.state is SupervisorState.RAMPING_DOWN

    with pytest.raises(PermissionError, match="ramping the heater down"):
        h.sup.set_mode(LoopMode.PID)
    with pytest.raises(PermissionError):
        h.sup.arm(100.0)


def test_the_ramp_down_completes_and_locks_out_and_only_ack_clears_it(armed):
    cfg = SupervisorConfig(rampdown_pct_per_min=60.0,
                           rampdown_below_knee_pct_per_min=60.0,
                           safe_output_pct=62.5, authority_pct=2.0,
                           require_ack_after_fault=True)
    h = armed(sup_cfg=cfg)
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(200)
    assert h.sup.state is SupervisorState.LOCKED_OUT

    h.cryostat.clear_faults()
    h.step(60)
    assert h.sup.state is SupervisorState.LOCKED_OUT, "a lockout must survive recovery"

    h.sup.acknowledge()
    assert h.sup.state is SupervisorState.IDLE


def test_a_human_hold_still_overrides_a_ramp_down_in_progress(armed):
    """The latch excludes AUTOMATION, and only automation.

    A recovering sensor may not resume the loop; an operator may stop the ramp.
    Collapsing that into "the ramp-down is uninterruptible" would take the
    emergency measure away from the person, which is backwards -- a human
    emergency measure is the final authority.
    """
    h = armed()
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(200)
    assert h.sup.state is SupervisorState.RAMPING_DOWN

    frozen = h.sup.panic_hold()
    assert h.sup.mode is LoopMode.OFF
    assert h.sup.state is not SupervisorState.RAMPING_DOWN

    where = h.inst.get_analog_percent()
    h.step(50)
    assert h.inst.get_analog_percent() == pytest.approx(where, abs=1e-6), (
        "OFF must write nothing at all; the heater stays where the operator froze it"
    )
    assert frozen == pytest.approx(where, abs=0.05)
