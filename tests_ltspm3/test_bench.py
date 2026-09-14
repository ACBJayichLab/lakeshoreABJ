"""Phase 3's bench -- the fitted plant, six temperatures, one grader.

plans/pid-3-loop.md §3.7.  **This file is written BEFORE the loop changes, on
purpose**: every step of phase 3 is gated on "the bench is green", and a grader
written after the thing it grades is not one.

So what is asserted here is what the loop does TODAY, measured, with the step
that changes each row named in the docstring.  Three of the rows are defects
recorded as assertions -- the sweep cannot be done at all, a 3 K move locks the
cryostat out at 10 K, and an exception in `step()` escapes.  When a step lands,
the assertion it invalidates is REPLACED by the passing one; a test that has to
be edited is the point, not an accident.  Nothing here is marked skip or xfail:
a skipped test fails this build (.github/workflows/tests.yml).

The numbers come from the production fit, key 24e2736fe6c28ba503e53546b19acd92.
"""
from __future__ import annotations

import statistics

import pytest

from conftest import BENCH_TEMPERATURES, FittedHarness, bench_control_config
from ltspm3.control import LoopMode, SupervisorState
from ltspm3.model import fitted_response as M

pytestmark = pytest.mark.parametrize("kelvin", BENCH_TEMPERATURES)


# -- the config the bench runs on ------------------------------------------


def test_the_bench_loads_its_limits_from_the_armed_config(kelvin):
    """§3.7: "tables loaded from `config-ltspm3-armed.yaml`, not built in the
    test".  The file is loaded through `lschart.config.load`, so a typo in it
    is a failure here rather than a surprise on the cryostat."""
    cfg = bench_control_config()
    assert cfg.enabled, "the bench needs the loop built; `run --arm` closes it"
    assert cfg.supervisor.hard_max_pct == 70.0, "the ceiling is the guard"
    assert cfg.supervisor.on_exit == "hold"
    assert cfg.supervisor.safe_output_pct == 0.0


def test_the_heater_that_holds_this_temperature_is_the_models(kelvin):
    """The bench's operating point is the model's own answer, not a guess.

    24.22 % at 10 K and 68.73 % at 180 K -- a span of 44 points of output,
    against an authority band one point wide.  §3.0.A in one assertion.
    """
    h = FittedHarness(kelvin=kelvin)
    assert h.bench_pct == pytest.approx(
        M.percent_for_power(M.steady_power_w(kelvin)), abs=1e-6)
    lo, hi = h.sup.band
    assert lo <= h.bench_pct <= hi, "the bench re-centred the band; step 4 makes this free"


def test_the_loop_arms_and_tracks_on_the_fitted_plant(kelvin):
    h = FittedHarness(kelvin=kelvin)
    h.settle_filter(60)
    h.sup.set_mode(LoopMode.PID)
    s = h.step(10)
    assert s.state is SupervisorState.TRACKING
    assert s.filtered_k == pytest.approx(kelvin, abs=0.05)
    assert s.output_pct == pytest.approx(h.bench_pct, abs=0.05)


# -- §3.6, the quiet hold ---------------------------------------------------


def test_a_settled_hold_is_already_inside_the_criterion(kelvin, bench):
    """§3.6: ≤ 0.02 %/min commanded over an hour.

    Today it is 0.0100 %/min at every one of the six -- ONE DAC CODE per
    minute, which is the dither and not the loop.  Worth having as a baseline
    before the gains change: step 3 makes the loop faster at the cold end and
    this is the number that says whether that cost anything.
    """
    h = bench(kelvin)
    h.history.clear()
    h.minutes(60)
    out = h.outputs()
    per_minute = max(abs(out[i + 30] - out[i]) for i in range(len(out) - 30))
    assert per_minute <= 0.02, f"{per_minute:.4f} %/min at {kelvin} K"

    temps = [s.filtered_k for s in h.history if s.filtered_k is not None]
    rms_mk = 1e3 * statistics.pstdev(temps)
    # The measured thermometer floor, 1.36e-6*T^2 rms -- 0.014 mK at 10 K and
    # 44 mK at 180 K.  The loop may not be the thing that dominates it.
    assert rms_mk < max(20.0, 3e3 * M.FittedParams().noise_quadratic * kelvin ** 2)


# -- §3.7, the setpoint move ------------------------------------------------


def test_a_3_k_move_lands_above_60_k_and_does_THREE_THINGS_below_it(kelvin, bench):
    """RECORDED DEFECT -- §3.0.A and §3.4.  Steps 4 and 6 replace this.

    One move, three behaviours, and the band is the only thing that differs.

    **Above 60 K it works**: 0.1 to 7.7 % overshoot, on target, because 3 K is
    0.23 % of output up there and the band is a full percent wide.

    **At 30 K it silently gives up.**  3 K needs +1.53 % at a gain of 1.96 K/%,
    the band is ±1 %, so the loop rails: demand 53.412 % against a ceiling of
    53.410, output 53.400, and it settles **0.883 K short** of the setpoint --
    under `max_error_k`, so there is no alarm, no state change and nothing in
    the log.  Authority exhausted with no opinion about it.  PID_PLAN §1 gives
    that condition a fault (railed AND error past `fault_error_k`); today it
    does not even warn.

    **At 10 K it crashes the sample.**  3 K is +8.8 % at 0.342 K/%, the error
    never closes, and an error standing past `max_error_k` for `anomaly_hold_s`
    is by rule 4's present wording a broken premise: tracking, `holding` at
    922 s, ramping down at 1102 s, locked out at 1858 s, heater 0.000 %, sample
    at base temperature 4.700 K.

    None of this is a bug.  The loop reasoned correctly from two limits that
    are wrong for this cryostat.
    """
    h = bench(kelvin)
    h.sup.sweep_to(kelvin + 3.0, 5.0)
    h.history.clear()
    h.minutes(90)
    last = h.history[-1]
    reached = [s.filtered_k for s in h.history if s.filtered_k is not None]
    _, band_hi = h.sup.band

    if kelvin >= 60.0:
        assert last.state is SupervisorState.TRACKING
        # The MEAN of the last minute, against a tolerance that scales with the
        # thermometer.  A single final sample was fine while a 60 s low pass
        # was doing the averaging; with the pole switched off (§3.1) the
        # measurement carries its own 1.36e-6*T^2 rms -- 44 mK at 180 K -- and
        # a fixed 50 mK tolerance on one sample is a coin toss up there.
        settled = statistics.fmean(
            [s.filtered_k for s in h.history[-30:] if s.filtered_k is not None])
        floor = 5e3 * M.FittedParams().noise_quadratic * kelvin ** 2
        assert settled == pytest.approx(kelvin + 3.0, abs=max(0.05, floor))
        overshoot = (max(reached) - (kelvin + 3.0)) / 3.0
        assert overshoot < 0.10, f"{100 * overshoot:.1f} % overshoot at {kelvin} K"
    elif kelvin == 30.0:
        # Railed, short, and quiet about it.  Fixing the band is what breaks
        # this; until then the silence is the finding.
        assert last.state is SupervisorState.TRACKING
        assert last.demand_pct > band_hi, "not railed -- has step 4 landed?"
        assert 0.5 < abs(last.error_k) < 1.0
        assert last.alarms == [], "if this now warns, §3.4's row has landed"
    else:
        assert last.state is SupervisorState.LOCKED_OUT
        assert last.output_pct == pytest.approx(0.0, abs=1e-9)
        assert last.filtered_k < kelvin, "the sample fell instead of rising"


def test_a_5_k_per_min_sweep_CANNOT_BE_DONE_by_this_loop(kelvin, bench):
    """RECORDED DEFECT -- the requirement phase 3 exists for, measured failing.

    Jeff's rate is 5 K/min.  A 10 K sweep at that rate locks the loop out at
    FIVE of the six bench temperatures and leaves the sample at base
    temperature; only 60 K survives, and by luck rather than design.

    The arithmetic is not subtle.  A first-order plant following a ramp of rate
    r settles at a tracking error of exactly r*tau, and at 118 K that is
    5/60 * 519 s = **43 K**.  `max_ramp_error_k` caps the allowance the premise
    check grants a commanded ramp at **6 K**.  So the loop is required to read
    every legitimate 5 K/min sweep as a broken cryostat, and it does.

    Three things fix it together and none of them alone: velocity feedforward
    sized from the one rate (§3.3, the cap is 1.00 % against the 3.28 % that
    118 K needs), a band that follows the setpoint (§3.0.A), and a premise
    check in watts that does not have a kelvin cap to exceed (§3.4).
    """
    h = bench(kelvin)
    h.sup.sweep_to(kelvin + 10.0, 5.0)
    h.history.clear()
    h.minutes(120)
    last = h.history[-1]

    if kelvin == 60.0:
        assert last.state is SupervisorState.TRACKING
        assert last.filtered_k == pytest.approx(kelvin + 10.0, abs=0.05)
    else:
        assert last.state is SupervisorState.LOCKED_OUT, (
            f"{kelvin} K survived a 5 K/min sweep; if step 3/4/5 has landed, "
            "this test is what should be rewritten")
        assert last.filtered_k < kelvin


# -- §3.7, the sensor ------------------------------------------------------


def test_a_glitching_sensor_freezes_the_output_and_moves_nothing(kelvin, bench):
    """Rule 3, and it already holds on the fitted plant.

    Input 1 only, scattering in both directions -- the shape measured in the
    archive, 9 events in 1,510 h.  `docs/ltspm3/safety.md`.  Step 7 renames the
    state to `FROZEN`; the behaviour asserted here does not change.
    """
    h = bench(kelvin)
    before = h.sup.output_pct
    h.cryostat.inject(glitch_channels={"218.1"},
                      glitch_low_k=0.7 * kelvin, glitch_high_k=1.2 * kelvin)
    s = h.step(1)
    assert s.state is SupervisorState.HOLDING
    assert s.output_pct == pytest.approx(before, abs=1e-9)

    # And it recovers, without the output having moved in between.
    h.cryostat.clear_faults()
    h.history.clear()
    h.minutes(5)
    assert all(s.output_pct == pytest.approx(before, abs=0.05)
               for s in h.history if s.output_pct is not None)


# -- §3.5, the crash --------------------------------------------------------


def test_an_exception_in_step_ESCAPES_the_supervisor(kelvin, bench):
    """RECORDED DEFECT -- §3.5.  Step 7 replaces this whole test.

    Jeff asked for graceful failure: a crashed PID disengages.  Today the
    exception propagates to the poller with the loop still in `tracking` and
    still believing it owns the heater.  The output does not move, which is the
    one mercy -- nothing writes on the way out.
    """
    h = bench(kelvin)
    before = h.sup.output_pct
    h.sup.filter = None                      # the cheapest possible detonation
    with pytest.raises(AttributeError):
        h.step(1)
    assert h.sup.state is SupervisorState.TRACKING, "step 7 makes this `crashed`"
    assert h.sup.output_pct == pytest.approx(before, abs=1e-9)
