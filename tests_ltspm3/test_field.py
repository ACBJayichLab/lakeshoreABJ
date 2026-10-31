"""A magnet that adds heat to the sample -- PID_PLAN.md 4a-xii and 4a-xiii.

Measured 2026-10-30 with the loop disengaged at 66.497 %: +42.8 mW at
100 mT, +68.4 mW at 150 mT, and -71.0 mW back to 5 mT.  To the loop's watt
residual that is a step of the shape a failing connector makes, so the
operator declares it (`set_field`).  `plant.extra_heat_w` is that heat on the
bench; `FittedHarness.deliver` is the heater failing, which a field must not
be allowed to hide.

What is graded here: a DECLARED field move is tolerated up to
`field_power_frac` and no further; the tolerance ends, even when nobody ends
it; the band's floor and only its floor makes room; and with the field in
tesla the fitted feedforward holds the sample where the PID alone overshoots
by ten to twenty kelvin.
"""
from __future__ import annotations

import math

import pytest

from bench_plant import BENCH_TEMPERATURES, STAGE_FILE, FittedHarness
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
    """The heater's delivered fraction walking from ``start`` to ``end`` over
    ``minutes``, one cycle at a time."""
    n = int(round(minutes * 60.0 / h.DT))
    for i in range(1, n + 1):
        h.deliver(start + (end - start) * i / n)
        h.step(1)
        if faulted(h):
            return


def ramp_heat(h, start_w, end_w, seconds=None):
    """The field ramping: its heat walks from ``start_w`` to ``end_w`` over
    the magnet's ramp, `field_ramp_s` unless told otherwise."""
    seconds = h.sup.cfg.field_ramp_s if seconds is None else seconds
    n = max(1, int(round(seconds / h.DT)))
    for i in range(1, n + 1):
        h.plant.extra_heat_w = start_w + (end_w - start_w) * i / n
        h.step(1)
        if faulted(h):
            return


def errors(h, since=0):
    return [s.error_k for s in h.history[since:] if s.error_k is not None]


# -- rule 4: the step test ---------------------------------------------------


@pytest.mark.parametrize("kelvin", WATT_TEMPERATURES)
def test_a_declared_field_move_does_not_fault(kelvin):
    """**The case this exists for**: 150 mT's measured 68 mW, declared with
    no field value -- so no feedforward, and the residual steps by all of it.
    The loop keeps tracking through the move and for a whole fault window
    after it: the new level is the reference once the field settles."""
    h = armed(kelvin)
    h.minutes(5)
    h.sup.set_field("moving")
    ramp_heat(h, 0.0, h.sup.field_heat_w(0.15))
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
    """Widened, not switched off: 40 % is twice `field_power_frac`, and the
    heater circuit failing during a move is still a fault."""
    h = armed(kelvin)
    h.minutes(5)
    h.sup.set_field("moving")
    h.minutes(1)
    h.deliver(0.60)
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
    h.sup.set_field("moving", 0.15)
    ramp_heat(h, 0.0, h.sup.field_heat_w(0.15))
    h.sup.set_field("settled")
    h.minutes(5)
    assert not faulted(h)
    h.deliver(0.97)
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


# -- rule 5: the floor makes room, the ceiling does not ------------------------


def test_a_field_that_is_on_lowers_the_floor_and_leaves_the_ceiling():
    """`1 - sqrt(1 - frac)` of the centre, off the FLOOR only: a field adds
    heat, so it only ever needs less output.  On from the declared move until
    the field is declared zero AND has settled."""
    h = armed(140.0)
    lo0, hi0 = h.sup.band
    centre = h.sup.band_centre_pct()
    room = centre * (1.0 - math.sqrt(1.0 - h.sup.cfg.field_power_frac))
    h.sup.set_field("moving")
    h.step(1)
    lo, hi = h.sup.band
    assert hi == pytest.approx(hi0, abs=0.02), "the ceiling moved"
    assert lo0 - lo == pytest.approx(room, abs=0.02)
    h.sup.set_field("settled")
    h.minutes(5)
    assert h.sup.band == pytest.approx((lo, hi), abs=0.02)
    h.sup.set_field("moving")
    h.minutes(1)
    h.sup.set_field("zero")
    h.minutes(1)
    assert h.sup.band[0] < lo0 - room / 2, "narrowed before the field settled"
    h.minutes(2)
    assert h.sup.band == pytest.approx((lo0, hi0), abs=0.02)


@pytest.mark.parametrize("kelvin", (100.0, 180.0))
def test_without_feedforward_the_loop_gets_there_inside_the_lowered_floor(kelvin):
    """250 mT's extrapolated heat, ~18 % of the heater, declared with no
    field value: the PID alone has to find an output 5-6 points down.  The
    one-point band rails it; the lowered floor lets it arrive.  It is not
    fast -- this is the case the feedforward exists for -- but it gets there,
    and nothing about it lifts the ceiling."""
    h = armed(kelvin)
    h.minutes(5)
    _, hi0 = h.sup.band
    h.sup.set_field("moving")
    ramp_heat(h, 0.0, h.sup.field_heat_w(0.25))
    h.sup.set_field("settled")
    h.minutes(60)
    assert not faulted(h)
    tail = errors(h)[-150:]
    assert abs(sum(tail) / len(tail)) < 0.3, "never got back to the setpoint"
    assert h.sup.band[1] == pytest.approx(hi0, abs=0.02)


# -- the fitted feedforward -----------------------------------------------------


@pytest.mark.parametrize("kelvin", WATT_TEMPERATURES)
@pytest.mark.parametrize("tesla", (0.15, 0.25))
def test_the_feedforward_holds_the_sample_through_a_field_move(kelvin, tesla):
    """With the field in tesla, the output goes down by the fitted heat as the
    field brings it in, so the PID has nothing to correct.  The same move
    declared without a value overshoots by ten to twenty kelvin."""
    h = armed(kelvin)
    h.minutes(5)
    start = len(h.history)
    h.sup.set_field("moving", tesla)
    ramp_heat(h, 0.0, h.sup.field_heat_w(tesla))
    h.sup.set_field("settled")
    h.minutes(30)
    assert not faulted(h)
    worst = max(abs(e) for e in errors(h, start))
    assert worst < 0.5, f"{worst:.2f} K off with the feedforward"
    assert h.history[-1].field_t == pytest.approx(tesla)


@pytest.mark.parametrize("scale", (0.7, 1.3), ids=["field 30 % cooler", "30 % hotter"])
def test_a_fit_that_is_wrong_by_a_third_costs_kelvins_and_not_a_fault(scale):
    """250 mT is an extrapolation from 150.  If the field turns out 30 %
    hotter or cooler than the fit, the feedforward gets most of it and the
    PID the rest: a few kelvin, back to the setpoint inside the hour."""
    h = armed(150.0)
    h.minutes(5)
    h.sup.set_field("moving", 0.25)
    ramp_heat(h, 0.0, scale * h.sup.field_heat_w(0.25))
    h.sup.set_field("settled")
    h.minutes(60)
    assert not faulted(h)
    assert max(abs(e) for e in errors(h)) < 10.0
    tail = errors(h)[-150:]
    assert abs(sum(tail) / len(tail)) < 0.2


def test_going_back_to_zero_gives_the_output_back():
    h = armed(150.0)
    h.minutes(5)
    before = h.history[-1].output_pct
    heat = h.sup.field_heat_w(0.25)
    h.sup.set_field("moving", 0.25)
    ramp_heat(h, 0.0, heat)
    h.sup.set_field("settled")
    h.minutes(20)
    assert h.history[-1].output_pct < before - 3.0
    start = len(h.history)
    h.sup.set_field("moving", 0.0)
    ramp_heat(h, heat, 0.0)
    h.sup.set_field("zero")
    h.minutes(20)
    assert not faulted(h)
    assert max(abs(e) for e in errors(h, start)) < 0.5
    assert h.history[-1].output_pct == pytest.approx(before, abs=0.1)


def test_settled_with_a_field_records_it_and_moves_nothing():
    """After a restart at field the output already answers the field, so
    declaring it must not step anything -- only `moving` acts."""
    h = armed(150.0)
    h.minutes(2)
    bias = h.sup.pid.bias
    h.sup.set_field("settled", 0.15)
    h.minutes(2)
    assert h.sup.pid.bias == bias
    assert h.history[-1].field_t == pytest.approx(0.15)


def test_no_feedforward_while_the_loop_is_not_armed():
    """A disengaged loop's output is the operator's; a move declared then
    queues nothing for the next arm to inherit."""
    h = armed(150.0)
    h.sup.panic_hold()
    h.sup.set_field("moving", 0.25)
    assert h.sup._field_ff_left == 0.0
    assert h.sup._field_t == pytest.approx(0.25)


# -- 4a-xiv: the curve's low end, and the verdict through a move ---------------

#: Measured 2026-10-30 at 150 K, heat relative to 5 mT, in watts: loop
#: disengaged for 100 and 150 mT, armed through Scan94's sweep for the rest.
MEASURED = ((0.0025, 0.0000), (0.010, 0.0009), (0.022, 0.0038),
            (0.100, 0.0428), (0.150, 0.0684))


def test_the_shipped_curve_goes_through_every_measured_point():
    """Within 0.3 mW of the low points and a milliwatt of the high ones.
    The power law it replaced was 2.4 mW over at 22 mT, and every
    low-field move dipped ~150 mK for twenty minutes on that over-step."""
    h = armed(150.0)
    for tesla, heat in MEASURED:
        model = h.sup.field_heat_w(tesla) - h.sup.field_heat_w(0.005)
        tol = 0.0003 if tesla < 0.05 else 0.0011
        assert model == pytest.approx(heat, abs=tol), f"{1e3 * tesla:g} mT"


def minute_means(errs, n=30):
    """One-minute means of the error, at the 2 s cadence: the bench Sample
    carries 50-90 mK of reading noise at 150 K, and a dip is a mean."""
    return [sum(errs[i:i + n]) / n for i in range(0, len(errs) - n + 1, n)]


def low_field_sweep(truth):
    """Scan94's start, on the cryostat's own stage: `settled 0.005` declared
    as the script does, then 2.5 -> 10 -> 22 mT, each a 24 s ramp and ten
    minutes of hold, with ``truth(h, tesla)`` the heat the plant gets."""
    h = armed(150.0, stage=STAGE_FILE)
    h.sup.set_field("settled", 0.005)
    h.minutes(5)
    start, previous = len(h.history), 0.0
    for tesla in (0.0025, 0.010, 0.022):
        h.sup.set_field("moving", tesla)
        ramp_heat(h, previous, truth(h, tesla), seconds=24.0)
        previous = truth(h, tesla)
        h.sup.set_field("settled")
        h.minutes(10)
    return max(abs(m) for m in minute_means(errors(h, start)))


def test_scan94s_low_field_moves_do_not_dip():
    """With the MEASURED heat in the plant the moves stay well inside the
    100 mK gate (bench noise alone is ~18 mK).  The same sweep against a
    feedforward that over-steps the way the power law did -- the plant
    getting 60 % of what the loop expects -- dips 140 mK, which is the
    cryostat's 160 mK on 2026-10-30: the bench sees the failure it fixes."""
    measured = dict(MEASURED)
    assert low_field_sweep(lambda h, tesla: measured[tesla]) < 0.06
    over = low_field_sweep(
        lambda h, tesla: 0.6 * (h.sup.field_heat_w(tesla) - h.sup.field_heat_w(0.005)))
    assert over > 0.10


def test_a_field_move_clears_settled_and_leaves_the_gains():
    """The sample answers a field over minutes after the magnet stops, so a
    hold is not certified through a move: `settled` drops when the move is
    declared and needs a full dwell after the move is over.  The gains stay
    on HOLD -- forcing MOVE gains through a field move only delayed the next
    settle."""
    h = armed(150.0, stage=STAGE_FILE)
    for _ in range(int(1800 / h.DT)):
        h.step(1)
        if h.history[-1].settled:
            break
    assert h.history[-1].settled
    h.sup.set_field("moving", 0.0025)
    h.step(1)
    assert not h.history[-1].settled
    assert h.history[-1].phase == "hold"
    h.minutes(1)
    h.sup.set_field("settled")
    cfg, tun = h.sup.cfg, h.sup.tuner.cfg
    h.step(int((cfg.field_settle_s + tun.hold_settle_s) / h.DT) - 3)
    assert not any(s.settled for s in h.history[-60:]), "certified too early"
    for _ in range(int(1200 / h.DT)):
        h.step(1)
        if h.history[-1].settled:
            break
    assert h.history[-1].settled, "never settled again after the move"
