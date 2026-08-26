"""Programmatic temperature sweeps.

The requirement is to sit at a temperature for hours *and* to move between
temperatures under program control.  The supervisor's premise check treats any
error over max_error_k as evidence the cryostat is broken, so a stepped setpoint
stalls the loop.  Ramping the setpoint is what makes both requirements hold at
once without weakening the check.
"""

import pytest

from ltspm3.control import SupervisorState
from ltspm3.control.ramp import RampConfig, SetpointRamp


# -- the ramp generator on its own -----------------------------------------

def test_ramp_moves_at_the_requested_rate():
    r = SetpointRamp(100.0)
    r.start(0.0, 105.0, rate_k_per_min=1.0)
    assert r.value(0.0) == pytest.approx(100.0)
    assert r.value(60.0) == pytest.approx(101.0)
    assert r.value(150.0) == pytest.approx(102.5)
    assert r.ramping


def test_ramp_stops_exactly_on_target_and_clears():
    r = SetpointRamp(100.0)
    r.start(0.0, 102.0, rate_k_per_min=1.0)
    assert r.value(600.0) == pytest.approx(102.0)
    assert not r.ramping
    assert r.target == pytest.approx(102.0)


def test_ramp_downward():
    r = SetpointRamp(100.0)
    r.start(0.0, 90.0, rate_k_per_min=2.0)
    assert r.value(60.0) == pytest.approx(98.0)


def test_ramp_refuses_a_rate_the_loop_cannot_follow():
    r = SetpointRamp(100.0, RampConfig(max_rate_k_per_min=5.0))
    with pytest.raises(ValueError):
        r.start(0.0, 200.0, rate_k_per_min=50.0)


def test_abort_holds_where_it_stands():
    r = SetpointRamp(100.0)
    r.start(0.0, 110.0, rate_k_per_min=1.0)
    held = r.abort(120.0)
    assert held == pytest.approx(102.0)
    assert not r.ramping
    assert r.value(9999.0) == pytest.approx(102.0)


# -- closed loop ------------------------------------------------------------

def test_a_ramped_sweep_keeps_the_error_inside_the_premise_check(armed):
    """The whole point: sweeping must not look like a broken premise."""
    h = armed()
    h.sup.sweep_to(h.equilibrium_k + 3.0, rate_k_per_min=0.5)
    h.step(400)

    window = h.history[-400:]
    # The tracking error during a ramp is genuinely r*tau; what must not happen
    # is the loop reading that as a broken premise.
    assert not any(s.state is SupervisorState.RAMPING_DOWN for s in window)
    assert not any(s.state is SupervisorState.HOLDING for s in window)
    assert h.sup.state is SupervisorState.TRACKING
    assert h.sup.status.filtered_k == pytest.approx(h.equilibrium_k + 3.0, abs=0.25), \
        "sweep never arrived"


def test_a_stepped_setpoint_still_stalls_the_loop(armed):
    """The check keeps its teeth: only ramping is exempt, because only ramping
    keeps the error small."""
    h = armed()
    h.sup.set_setpoint(h.equilibrium_k + 3.0, ramp=False)
    h.step(5)
    assert h.sup.state is SupervisorState.HOLDING


def test_sweep_actually_moves_the_heater_in_the_right_direction(armed):
    h = armed()
    start = h.sup.output_pct
    h.sup.sweep_to(h.equilibrium_k + 2.0, rate_k_per_min=0.5)
    h.step(300)
    assert h.sup.output_pct > start, "a warming sweep must add heat"
    assert h.sup.output_pct <= 63.076 + h.sup.cfg.authority_pct + 1e-9


def test_sweep_stays_inside_the_authority_band(armed):
    """A sweep is not a licence to leave the band -- the band caps heat
    unconditionally, so an out-of-reach target simply saturates."""
    h = armed()
    h.sup.sweep_to(h.equilibrium_k + 40.0, rate_k_per_min=5.0)
    h.step(400)
    outs = [s.output_pct for s in h.history[-400:] if s.output_pct is not None]
    assert max(outs) <= 63.076 + h.sup.cfg.authority_pct + 1e-9


def test_abort_mid_sweep_holds_temperature(armed):
    h = armed()
    h.sup.sweep_to(h.equilibrium_k + 5.0, rate_k_per_min=0.5)
    h.step(100)
    held = h.sup.abort_ramp()
    h.step(50)
    assert h.sup.status.setpoint_k == pytest.approx(held)
    assert not h.sup.ramp.ramping
