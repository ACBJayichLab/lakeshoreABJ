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

from bench_plant import BENCH_TEMPERATURES, FittedHarness
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
    assert s.state is SupervisorState.FROZEN
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


# -- §3.0.C / step 2: the curve the loop stands on ---------------------------


def test_the_loop_reads_the_FITTED_curve_and_not_cd10():
    """Step 2.  What `control/` believes about this cryostat is the shipped fit.

    Before this, `feedforward.py` imported `model/thermal_response.py` -- CD10's
    24 settled points over 64.3-68.5 %, with a power law for everything else.
    The loop's whole idea of "what output holds what temperature" came from a
    curve that had never seen 30 K.
    """
    from ltspm3.control.feedforward import Feedforward, FeedforwardConfig, FittedCurve
    from ltspm3.model import fitted_response as M

    assert FeedforwardConfig().source == "fitted"
    ff = Feedforward()
    assert isinstance(ff.curve, FittedCurve)

    # The loop's answer IS the model's answer, to the last digit.
    for kelvin in BENCH_TEMPERATURES:
        assert ff.percent_for(kelvin) == pytest.approx(
            M.percent_for_power(M.steady_power_w(kelvin)), abs=1e-9)

    # And it clamps rather than extrapolating.  The table ends at 195 K, so a
    # 300 K setpoint gets the top of the table and not an invented number --
    # too little heat, which the integral supplies slowly, rather than a
    # feedforward step into a region nobody has measured.
    assert ff.percent_for(300.0) == pytest.approx(ff.percent_for(M.T_MAX_K))
    assert ff.percent_for(300.0) < 70.0


def test_the_supervisor_regime_check_now_uses_the_fitted_curve():
    """`_check_model` asks the feedforward what this output should settle at.

    On the fitted plant that comparison is now like-for-like, so a settled hold
    reads a model error of about zero.  Under CD10 the same hold at 118 K read
    several kelvin out, and `model_trust_k: 15` was sized around exactly that
    kind of disagreement rather than around a real regime change.
    """
    h = FittedHarness(kelvin=118.0)
    h.settle_filter(60)
    h.sup.set_mode(LoopMode.PID)
    h.minutes(5)
    s = h.sup.status
    assert s.model_error_k is not None, "the check never ran"
    assert abs(s.model_error_k) < 0.5
    assert s.model_trusted


# -- the onset of a fast move is not a spike -----------------------------------

#: THE SAMPLE, as the 218 read it, 2026-09-23 12:56:00-12:59:18: two and a half
#: minutes of hold at 118 K and then the start of a 2 K move up.  Genuine data,
#: copied here because `data/` does not exist on a fresh clone.  On the
#: cryostat the readings at 166 s and 168 s were rejected as spikes -- the
#: prediction, built on a 30 s slope, was ~0.19 K behind a smooth one-way curve
#: against a 0.16 K threshold -- and the heater froze for 14 s.
MOVE_ONSET_2026_09_23 = (
    (0.0, 118.04), (2.0, 118.03), (4.0, 118.03), (6.5, 118.03), (8.0, 118.05), (10.0, 118.05),
    (12.0, 118.03), (14.0, 118.05), (16.0, 118.06), (18.0, 118.05), (20.0, 118.03),
    (22.0, 118.03), (24.0, 118.02), (26.0, 118.01), (28.0, 118.03), (30.0, 118.03),
    (32.0, 118.03), (34.0, 118.04), (36.9, 118.04), (38.0, 118.04), (40.0, 118.04),
    (42.0, 118.04), (44.0, 118.03), (46.0, 118.03), (48.0, 118.02), (50.0, 118.01),
    (52.0, 118.02), (54.0, 118.02), (56.0, 118.00), (58.0, 118.01), (60.0, 118.04),
    (62.0, 118.02), (64.0, 118.00), (66.4, 118.01), (68.0, 118.01), (70.0, 118.01),
    (72.0, 118.01), (74.0, 118.01), (76.0, 118.00), (78.0, 118.01), (80.0, 118.02),
    (82.0, 118.01), (84.0, 118.01), (86.0, 118.01), (88.0, 118.01), (90.0, 118.00),
    (92.0, 118.01), (94.0, 118.00), (96.9, 117.98), (98.0, 117.99), (100.0, 117.99),
    (102.0, 118.00), (104.0, 118.00), (106.0, 118.00), (108.0, 118.00), (110.0, 118.00),
    (112.0, 117.99), (114.0, 117.99), (116.0, 117.99), (118.0, 117.99), (120.0, 118.00),
    (122.0, 118.03), (124.0, 118.04), (126.4, 118.03), (128.0, 118.02), (130.0, 118.02),
    (132.0, 118.02), (134.0, 118.01), (136.0, 118.01), (138.0, 118.00), (140.0, 118.01),
    (142.0, 118.00), (144.0, 118.00), (146.0, 118.00), (148.0, 117.99), (150.0, 117.99),
    (152.0, 117.99), (154.0, 117.99), (156.9, 118.01), (158.0, 118.06), (160.0, 118.12),
    (162.0, 118.20), (164.0, 118.29), (166.0, 118.40), (168.0, 118.51), (170.0, 118.61),
    (172.0, 118.74), (174.0, 118.86), (176.0, 118.97), (178.0, 119.09), (180.0, 119.19),
    (182.0, 119.30), (184.0, 119.38), (186.4, 119.45), (188.0, 119.51), (190.0, 119.55),
    (192.0, 119.58), (194.0, 119.63), (196.0, 119.66), (198.0, 119.70),
)


def _spikes(filt, samples):
    """The supervisor's order: ask, then fold in only what was believed."""
    rejected, last = [], None
    for t, kelvin in samples:
        dt = 2.0 if last is None else t - last
        last = t
        if filt.is_spike(kelvin, dt, t=t):
            rejected.append(t)
        elif filt.is_stale(t):
            filt.reseed(t, kelvin)
        else:
            filt.update(t, kelvin, dt)
    return rejected


def test_the_onset_of_a_fast_move_on_the_cryostat_is_not_a_spike():
    """The armed file's filter believes every reading of the 12:58 move, and
    without `spike_min_k` it rejects the two the cryostat rejected."""
    from bench_plant import bench_control_config

    kwargs = dict(bench_control_config().filter)
    assert _spikes(MeasurementFilter(**kwargs), MOVE_ONSET_2026_09_23) == []
    kwargs["spike_min_k"] = 0.0
    assert _spikes(MeasurementFilter(**kwargs), MOVE_ONSET_2026_09_23) == [166.0, 168.0], (
        "the defect this pins has moved -- re-derive before trusting the fix")


def test_spike_min_k_touches_the_spike_test_and_nothing_else():
    """`spike_floor_k` also sets `acceleration_noise`, which the residual's
    `slope_lag` band is built on; raising IT to quiet the spike test narrowed
    the band (1.52 -> 1.44 mW at a settled 118 K) during exactly the moves it
    exists for.  This one must leave the band alone."""
    plain, floored = MeasurementFilter(), MeasurementFilter(spike_min_k=0.40)
    _spikes(plain, MOVE_ONSET_2026_09_23[:80])
    _spikes(floored, MOVE_ONSET_2026_09_23[:80])
    assert floored.acceleration_noise(2.0) == plain.acceleration_noise(2.0)
    assert floored.acceleration_excess(2.0) == plain.acceleration_excess(2.0)
    assert plain.spike_threshold(2.0) < 0.40 <= floored.spike_threshold(2.0)
