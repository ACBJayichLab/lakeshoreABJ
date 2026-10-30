"""A magnet that moves the heater's delivered power -- PID_PLAN.md 4a-xii.

Jeff, 2026-10-29: at high field the heater output the loop needs changes, and
the cause is the heater circuit's magnetoresistance.  The 218 drives a voltage,
so a resistance that moves with the field moves the power delivered at a fixed
output, and the residual steps by `-(1-a) P(u)` -- the exact shape of the
failing connector the step test exists to catch.  `FittedHarness.deliver` is
that change.

What is graded here is that a DECLARED field move is tolerated up to
`field_power_frac` and no further, that the tolerance ends, and that it ends
even when nobody ends it.
"""
from __future__ import annotations

import pytest

from bench_plant import BENCH_TEMPERATURES, FittedHarness
from ltspm3.control import LoopMode, SupervisorState
from ltspm3.model.fitted_response import DRIFT_T0_UNIX

#: Where the watt residual can speak at all -- see test_bench_review.py.
WATT_TEMPERATURES = tuple(k for k in BENCH_TEMPERATURES if k >= 60.0)


def armed(kelvin, **kw):
    h = FittedHarness(kelvin=kelvin, wall_t0=DRIFT_T0_UNIX, **kw)
    h.settle_filter(60)
    h.sup.set_mode(LoopMode.PID)
    h.step(10)
    return h


def faulted(h):
    return h.sup.state in (SupervisorState.RAMPING_DOWN, SupervisorState.LOCKED_OUT)


def run_until_fault(h, seconds):
    for _ in range(int(seconds / h.DT) + 1):
        h.step(1)
        if faulted(h):
            return True
    return False


def ramp_delivery(h, start, end, minutes):
    """The field ramping: the delivered fraction walks from ``start`` to
    ``end`` over ``minutes``, one cycle at a time, as a magnet's does."""
    n = int(round(minutes * 60.0 / h.DT))
    for i in range(1, n + 1):
        h.deliver(start + (end - start) * i / n)
        h.step(1)
        if faulted(h):
            return


@pytest.mark.parametrize("kelvin", WATT_TEMPERATURES)
def test_a_declared_field_move_that_moves_the_heater_does_not_fault(kelvin):
    """**The case this exists for.**  A 3 % change in delivered power is 17 to
    23 mW at these temperatures -- past the 15 mW floor, and an undeclared one
    faults (`test_a_heater_that_stops_delivering_faults_at_either_date` at
    0.97 is exactly this change, arriving with no field to explain it).

    Declared, the loop keeps tracking through the move and for a whole fault
    window after it: the new level is the reference once the field settles.
    """
    h = armed(kelvin)
    h.minutes(5)
    h.sup.set_field("moving")
    ramp_delivery(h, 1.0, 0.97, minutes=4)
    assert not faulted(h), h.history[-1].alarms
    h.sup.set_field("settled")
    cfg = h.sup.cfg
    assert not run_until_fault(h, cfg.fault_window_s + cfg.fault_after_s), (
        h.history[-1].alarms)
    assert h.sup.state is SupervisorState.TRACKING
    assert h.history[-1].field_state == "settled"
    assert not h.history[-1].field_moving


@pytest.mark.parametrize("kelvin", WATT_TEMPERATURES)
def test_a_loss_past_the_allowance_still_faults_during_a_move(kelvin):
    """Widened, not switched off: 15 % is three times `field_power_frac`, and
    the heater circuit failing during a move is still a fault."""
    h = armed(kelvin)
    h.minutes(5)
    h.sup.set_field("moving")
    h.minutes(1)
    h.deliver(0.85)
    cfg = h.sup.cfg
    assert run_until_fault(h, cfg.fault_window_s + cfg.fault_after_s)
    assert any("stepped" in a or "authority exhausted" in a
               for x in h.history[-40:] for a in x.alarms)


@pytest.mark.parametrize("kelvin", WATT_TEMPERATURES)
def test_once_the_field_has_settled_a_loss_is_a_step_again(kelvin):
    """The tolerance belongs to the move.  Five minutes after the field
    settles, a 3 % loss with nothing moving is the fault it always was --
    judged against the NEW level, which is what re-referencing means."""
    h = armed(kelvin)
    h.minutes(5)
    h.sup.set_field("moving")
    ramp_delivery(h, 1.0, 0.97, minutes=2)
    h.sup.set_field("settled")
    h.minutes(5)
    assert not faulted(h)
    h.deliver(0.97 * 0.97)
    cfg = h.sup.cfg
    assert run_until_fault(h, cfg.fault_window_s + cfg.fault_after_s), (
        f"a 3 % loss at a settled field never faulted at {kelvin} K")


@pytest.mark.parametrize("kelvin", (100.0, 180.0))
def test_a_move_nobody_ends_ends_itself_with_an_alarm(kelvin):
    """A forgotten `settled` must not leave the step test widened for the rest
    of the run.  After `field_move_max_s` the move is over, the alarm stands,
    and a loss is a step again."""
    h = armed(kelvin)
    h.minutes(5)
    h.sup.set_field("moving")
    h.minutes(h.sup.cfg.field_move_max_s / 60.0 + 1)
    last = h.history[-1]
    assert last.field_state == "settled" and not last.field_moving
    assert any("never declared settled" in a for a in last.alarms)
    h.minutes(2)
    h.deliver(0.97)
    cfg = h.sup.cfg
    assert run_until_fault(h, cfg.fault_window_s + cfg.fault_after_s)


def test_saying_moving_again_restarts_the_clock():
    """A ramp longer than `field_move_max_s` is kept widened by re-declaring
    it, which is what a field-sweep script does at each step."""
    h = armed(140.0)
    h.sup.set_field("moving")
    half = h.sup.cfg.field_move_max_s / 60.0 / 2
    h.minutes(half + 1)
    h.sup.set_field("moving")
    h.minutes(half + 1)
    assert h.history[-1].field_moving
    assert not any("never declared" in a for a in h.history[-1].alarms)


def test_the_step_test_stays_widened_for_the_settle_time():
    h = armed(140.0)
    h.sup.set_field("moving")
    h.minutes(1)
    h.sup.set_field("zero")
    h.step(int(h.sup.cfg.field_settle_s / h.DT) - 2)
    assert h.history[-1].field_moving
    h.step(4)
    assert h.history[-1].field_state == "zero" and not h.history[-1].field_moving


def test_a_field_declared_without_a_move_keeps_the_reference():
    h = armed(140.0)
    h.minutes(5)
    before = len(h.sup._dq_hist)
    msg = h.sup.set_field("settled")
    assert "no move" in msg
    h.step(1)
    assert len(h.sup._dq_hist) >= before
    assert not h.history[-1].field_moving


def test_an_unknown_field_state_is_refused():
    h = armed(140.0)
    with pytest.raises(ValueError, match="moving"):
        h.sup.set_field("ramping")


# -- rule 5: the band carries the field's allowance ---------------------------


def test_a_field_that_is_on_widens_the_band_by_what_its_allowance_needs():
    """`1/sqrt(1 - frac)` of the centre: the output that puts back the power
    `field_power_frac` takes away.  On from the declared move until the field
    is declared zero AND has settled."""
    import math

    h = armed(140.0)
    lo0, hi0 = h.sup.band
    centre = h.sup.band_centre_pct()
    extra = centre * (1.0 / math.sqrt(1.0 - h.sup.cfg.field_power_frac) - 1.0)
    h.sup.set_field("moving")
    h.step(1)
    lo, hi = h.sup.band
    assert hi - hi0 == pytest.approx(extra, abs=0.02)
    assert lo0 - lo == pytest.approx(extra, abs=0.02)
    h.sup.set_field("settled")
    h.minutes(5)
    assert h.sup.band[1] == pytest.approx(hi, abs=0.02)
    h.sup.set_field("moving")
    h.minutes(1)
    h.sup.set_field("zero")
    h.minutes(1)
    assert h.sup.band[1] > hi0 + extra / 2, "narrowed before the field settled"
    h.minutes(2)
    assert h.sup.band[1] == pytest.approx(hi0, abs=0.02)


@pytest.mark.parametrize("kelvin", (140.0, 180.0))
def test_the_loop_makes_the_power_back_inside_the_widened_band(kelvin):
    """**Measured before this step**: with the step test widened and the band
    not, a 3 % change rails the one-point band at 140 and 180 K and the
    sample sits 0.4-1 K low for as long as the field is on.  Restoring 3 %
    needs 1.5 % more output, about one point there.

    It is not fast -- a 3 % change ramped in over four minutes pulls the
    sample about 3 K low and the hold gains take most of an hour to bring it
    back -- but it comes back, which railed it never did."""
    h = armed(kelvin)
    h.minutes(5)
    h.sup.set_field("moving")
    ramp_delivery(h, 1.0, 0.97, minutes=4)
    h.sup.set_field("settled")
    h.minutes(60)
    assert not faulted(h)
    tail = [s for s in h.history[-150:] if s.error_k is not None]
    mean = sum(s.error_k for s in tail) / len(tail)
    assert abs(mean) < 0.1, f"{mean:+.3f} K an hour after the move"
    lo, hi = h.sup.band
    assert max(s.output_pct for s in tail) < hi - 0.2, "still railed"
