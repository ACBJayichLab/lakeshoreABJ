"""Programmatic temperature sweeps.

The requirement is to sit at a temperature for hours *and* to move between
temperatures under program control.  The supervisor's premise check treats any
error over max_error_k as evidence the cryostat is broken, so a stepped setpoint
stalls the loop.  Ramping the setpoint is what makes both requirements hold at
once without weakening the check.
"""

import pytest

from ltspm3.control import LoopMode, SupervisorState
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


# -- the panic actions -------------------------------------------------------
#
# Two of the four seams `lschart`'s file interface reaches into this package by,
# called duck-typed by name so `lschart` still never imports `ltspm3`.  Both
# DISENGAGE the loop: a person pressing either has decided the loop should stop
# deciding, and `OFF` is the only mode that writes nothing at all.  A hold is a
# hold of a *power* -- the heater keeps exactly the value it had.


def test_panic_hold_opens_the_loop_and_leaves_the_heater_where_it_was(armed):
    h = armed()
    h.step(20)
    before = h.sup.output_pct

    held = h.sup.panic_hold()
    assert held == pytest.approx(before, abs=1e-9)
    # OFF, not MANUAL.  Manual is not a hold: it still clamps to the authority
    # band and still rate limits, so a hold taken outside that band moved the
    # heater on the next cycle -- see test_a_hold_outside_the_band_still_holds.
    assert h.sup.mode is LoopMode.OFF

    h.step(50)
    assert h.sup.output_pct == pytest.approx(before, abs=1e-9)
    assert h.inst.get_analog_percent() == pytest.approx(before, abs=1e-9)


@pytest.mark.parametrize("where", [20.0, 68.0])
def test_a_hold_outside_the_band_still_holds(armed, where):
    """A person pressing freeze is not asking for the loop's opinion.

    This is why the hold disengages rather than switching to manual.  Told to
    freeze at 20% it used to report "holding 20.000%" and write 62.080% on the
    very next cycle -- the band floor, applied to an output it had just been
    told not to touch.  At 68% it reported 68.000% and wrote the ceiling.  It
    only ever really held when the heater happened already to be inside the
    band, and it reported a number it was about to leave either way.
    """
    h = armed()
    h.step(20)
    h.inst.set_analog_percent(where)      # somebody else put it here
    h.step(1)

    held = h.sup.panic_hold()
    assert held == pytest.approx(where, abs=0.02)

    h.step(200)
    assert h.inst.get_analog_percent() == pytest.approx(where, abs=1e-9)


def test_panic_hold_stops_a_sweep_where_it_stands(armed):
    """A hold that let the ramp carry on holding nothing would be no hold."""
    h = armed()
    h.sup.sweep_to(h.equilibrium_k + 5.0, rate_k_per_min=0.5)
    h.step(100)
    h.sup.panic_hold()
    assert not h.sup.ramp.ramping


def test_manual_does_not_reach_past_the_clamp(armed):
    """Manual goes through the supervisor rather than around it.

    No longer reached by `panic_hold`, which disengages instead -- so the mode
    is entered explicitly here.  The *ceiling* is still hard and still instant:
    the band caps heat even though it no longer compels it.
    """
    h = armed()
    h.step(20)
    h.sup.set_mode(LoopMode.MANUAL)
    h.sup.set_manual_percent(h.sup.cfg.hard_max_pct + 50.0)
    h.step(200)
    assert h.sup.output_pct <= h.sup.cfg.hard_max_pct + 1e-9
    assert h.sup.output_pct <= h.sup.band[1] + 1e-9


def test_arm_is_the_way_back_from_a_panic_hold(armed):
    h = armed()
    h.step(20)
    h.sup.panic_hold()
    h.step(50)

    h.sup.arm(h.sup.status.filtered_k or h.equilibrium_k)
    assert h.sup.mode is LoopMode.PID
    h.step(50)
    assert h.sup.status.output_pct is not None


def test_the_band_caps_heat_without_compelling_it(armed):
    """The floor bounds what the PID may ASK for, not what the DAC must carry.

    `clamp` used to run after the rate limiter and raise anything below the
    floor straight to it, which undid the limiter completely: arming with the
    heater at 0% wrote 62.076% in a single step, past `max_step_pct`, which
    exists for precisely that. Worse, it meant this loop could not hold any
    temperature whose steady-state output was below the band -- it would
    command operating-point power at base temperature and then fault.
    """
    h = armed()
    h.step(20)
    h.inst.set_analog_percent(0.0)
    h.sup.panic_hold()                    # disengage, so nothing is driving
    h.step(2)
    h.sup.arm(h.sup.filter.value)

    biggest, prev = 0.0, h.inst.get_analog_percent()
    for _ in range(300):
        h.step(1)
        now = h.inst.get_analog_percent()
        biggest = max(biggest, abs(now - prev))
        prev = now

    # Two DAC codes of headroom: the dither legitimately moves a code either
    # side of the rate-limited target.
    assert biggest <= h.sup.cfg.max_step_pct + 2 * h.sup.cfg.dac_step_pct
    assert h.inst.get_analog_percent() < h.sup.band[0], "jumped into the band"


# The ceiling needs no test of its own here: only the floor changed, and
# `test_sweep_stays_inside_the_authority_band` above already drives the demand
# past the ceiling and pins the output under it.  Putting the heater above the
# band from *outside* does not test it -- that faults the sensor guard, and a
# fault ramp-down is deliberately allowed to leave the band so it can reach
# `safe_output_pct` at all.


def test_a_held_loop_says_it_was_held(armed):
    """`off`/`idle` is also where a loop that was never armed sits.

    "Nobody has started this" and "somebody stopped this" are different things
    to read on a screen.  The mode used to carry that distinction badly --
    `manual` meant held -- so now the reason carries it properly, and says
    which of the two panic actions was taken.
    """
    h = armed()
    h.step(20)
    h.sup.panic_hold()
    h.step(2)
    assert "held" in h.sup.status.reason

    h.sup.arm(h.sup.filter.value)
    h.step(2)
    assert "held" not in h.sup.status.reason, "arming must clear it"


def test_a_disarmed_loop_says_which_panic_stopped_it(armed):
    h = armed()
    h.step(20)
    h.sup.panic_off()
    h.step(2)
    assert "heaters off" in h.sup.status.reason


def test_a_loop_that_was_never_armed_says_nothing(harness):
    """The other half of the pair: silence means nobody started it."""
    h = harness()
    h.settle_filter(20)
    assert h.sup.mode is LoopMode.OFF
    assert not h.sup.status.reason
