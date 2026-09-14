"""Phase 3 §3.1 -- the filter chain, and the dead time derived from it.

Rule 3: a single doubtful reading freezes the output.  What changed here is
which readings are *doubtful enough to notice*.  A median-5 silently swallows a
two-sample glitch; a median-3 lets it through to the sensor guard, which
rejects it by slew and says so in the log.  Rejecting quietly and rejecting
loudly are not the same safety property -- the archive's glitches are the only
warning this cryostat gives that input 1 is misbehaving.
"""
from __future__ import annotations

import pytest

from conftest import BENCH_TEMPERATURES, FittedHarness
from ltspm3.control import LoopMode, SupervisorState
from ltspm3.control.filters import ExponentialFilter, MeasurementFilter


# -- the chain itself --------------------------------------------------------


def test_the_low_pass_is_off_and_the_class_is_still_there():
    """`tau: 0` is pass-through, not a deleted stage (Jeff, 2026-09-11).

    The distinction is the whole reason the class stayed: the day this
    cryostat's noise moves into the band where a pole would help, it is a
    config edit, and the loop retunes itself because the group delay below
    carries `tau / 2`.
    """
    f = MeasurementFilter()
    assert f.lowpass.tau == 0.0
    assert f.median.window == 3

    pole = ExponentialFilter(0.0)
    for v in (10.0, 20.0, 15.0):
        assert pole.update(v, 2.0) == v
    assert pole.noise_gain(2.0) == 1.0, "a filter that does nothing attenuates nothing"

    with pytest.raises(ValueError):
        ExponentialFilter(-1.0)


def test_the_dead_time_is_derived_from_the_chain_not_typed_in():
    """3.0 s at the cadence and median this cryostat runs, and it MOVES."""
    f = MeasurementFilter()
    assert f.group_delay_s(2.0) == pytest.approx(3.0)

    # Every term is a lag something really has, so every term responds.
    assert MeasurementFilter(median_window=5).group_delay_s(2.0) == pytest.approx(5.0)
    assert MeasurementFilter(tau=60.0).group_delay_s(2.0) == pytest.approx(33.0)
    assert f.group_delay_s(4.0) == pytest.approx(6.0)


def test_the_derived_delay_is_the_chain_s_MEASURED_step_delay():
    """Not an algebraic claim: put a step in and watch it come out.

    The median's group delay is observable; the zero-order hold is not, because
    it is the sampling rather than the filter.  So this measures the first and
    asserts the derived number is it plus the half cycle.
    """
    cadence = 2.0
    f = MeasurementFilter()
    t = 0.0
    for _ in range(10):                      # settle at 100 K
        f.update(t, 100.0, cadence)
        t += cadence

    step_t, out = None, None
    for i in range(6):                       # step to 110 K at t
        out, _ = f.update(t, 110.0, cadence)
        if out > 105.0 and step_t is None:
            step_t = i * cadence
        t += cadence

    assert step_t is not None, "the step never came out of the filter"
    assert f.group_delay_s(cadence) == pytest.approx(step_t + cadence / 2.0)


# -- rule 3, through the whole loop -----------------------------------------


@pytest.mark.parametrize("kelvin", BENCH_TEMPERATURES)
@pytest.mark.parametrize("n_bad", (1, 2))
def test_a_glitch_of_EITHER_length_freezes_the_output_and_moves_nothing(
        kelvin, n_bad):
    """Rule 3, and it does not depend on the median window at all.

    §3.1 predicted that a two-sample glitch would newly "reach the guard",
    which a median of 5 had been absorbing.  **It does not work that way and
    never did**: `guard.update()` is handed the RAW sample and runs before the
    filter, so a rejected reading never enters the median in the first place.
    The window decides what happens to samples the guard ACCEPTS, not what the
    guard sees.  Measured, at 118 K, median 3 and median 5, one bad sample and
    two, +0.6 K and +3.0 K: all eight cases suspect, frozen, output unmoved to
    1e-9.

    So the property worth pinning is the one that is real and is what rule 3
    actually promises -- a doubtful reading freezes the heater, and the length
    of the doubt does not change that.
    """
    h = FittedHarness(kelvin=kelvin)
    h.settle_filter(60)
    h.sup.set_mode(LoopMode.PID)
    h.step(10)
    before = h.sup.output_pct

    h.cryostat.inject(dropout_channels={"218.1"},
                      dropout_value=kelvin + max(1.0, 0.03 * kelvin))
    h.step(n_bad)
    s = h.sup.status

    assert s.health.value in ("suspect", "fault"), "the guard never saw it"
    assert s.state is SupervisorState.HOLDING       # step 7 renames it FROZEN
    assert h.sup.output_pct == pytest.approx(before, abs=1e-9), (
        "rule 3: a doubtful reading freezes the output, it does not move it")

    # And it recovers on its own once the sensor does.
    h.cryostat.clear_faults()
    h.minutes(3)
    assert h.sup.state is SupervisorState.TRACKING
    assert h.sup.output_pct == pytest.approx(before, abs=0.05)


def test_the_measurement_no_longer_lags_the_cryostat_by_half_a_kelvin():
    """What switching the pole off actually bought, measured on the fitted plant.

    A single pole lags a ramp by exactly `rate * tau`.  Through a commanded
    approach at 118 K the old 60 s chain put the measurement **595 mK** behind
    the cryostat; the median-3 puts it **57 mK** behind.  Ten times, and it is
    the reason the premise check needed a `max_ramp_error_k` allowance in the
    first place -- a good part of what that allowance was excusing was the
    loop's own filter.
    """
    def worst_lag(tau, window):
        h = FittedHarness(kelvin=118.0,
                          filter_kwargs={"tau": tau, "median_window": window})
        h.settle_filter(60)
        h.sup.set_mode(LoopMode.PID)
        h.step(10)
        h.sup.sweep_to(121.0, 5.0)
        lags = []
        for _ in range(600):
            s = h.step(1)
            if s.filtered_k is not None:
                lags.append(h.plant.temperature - s.filtered_k)
        return max(lags, key=abs), h.sup.delay_s

    was, was_delay = worst_lag(60.0, 5)
    now, now_delay = worst_lag(0.0, 3)
    assert abs(was) > 0.4, "the old chain was not lagging -- has something else changed?"
    assert abs(now) < 0.1
    assert abs(now) < abs(was) / 5.0
    assert (was_delay, now_delay) == (pytest.approx(35.0), pytest.approx(3.0))


def test_the_loop_measures_its_own_cadence_rather_than_trusting_the_config():
    """The config seeds it; the bus decides it.

    `acquisition.interval_s` is a floor on the period, not a promise: the real
    logs jitter between 3.92 and 4.07 s at a nominal 4, and a retry costs a
    whole cycle.  A dead time derived from the asked-for number would be wrong
    in the direction that matters -- too short, so `tau_cl`'s floor is too low
    and the loop is tuned faster than it can see.
    """
    h = FittedHarness(kelvin=118.0, cadence_s=2.0)
    assert h.sup.delay_s == pytest.approx(3.0)

    h.settle_filter(20)
    h.sup.set_mode(LoopMode.PID)
    h.step(200, dt=6.0)                      # the bus went slow and stayed slow
    assert h.sup.delay_s > 3.0, "the delay never noticed"
    assert h.sup.delay_s == pytest.approx(h.sup.tuner.delay_s)
    assert h.sup.delay_s < 9.0, "...and it must not chase a single slow cycle"
