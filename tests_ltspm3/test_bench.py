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

from bench_plant import BENCH_TEMPERATURES, FittedHarness, bench_control_config
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

    **The criterion IS two DAC codes a minute**, which is worth saying out loud
    because it means this can only ever return one of three answers.  Before
    step 3 it was one code at all six -- the dither, not the loop.  After it,
    30 K and 180 K sit at two codes and the rest are still at one: ratio tuning
    is faster at the cold end (kp 0.16 against 0.089 at 30 K, ti 9 s against
    333) and it works the heater slightly harder against the same noise.  Two
    codes at 30 K is 37 mK/min commanded, and the hold itself measures **1.66
    mK rms over 12 mK of range** -- the limit being approached, not the hold
    getting worse.
    """
    h = bench(kelvin)
    h.history.clear()
    h.minutes(60)
    out = h.outputs()
    per_minute = max(abs(out[i + 30] - out[i]) for i in range(len(out) - 30))
    assert per_minute <= 2 * h.sup.cfg.dac_step_pct + 1e-9, (
        f"{per_minute:.4f} %/min at {kelvin} K")

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


#: Where a 10 K sweep at 5 K/min lands today.  Step 3 moved 100 K from the
#: second group into this one; steps 4 and 5 are what the other four wait on.
SWEEP_SURVIVES = (60.0, 100.0)


def test_a_5_k_per_min_sweep_fails_BELOW_60_K_AND_ABOVE_100_K(kelvin, bench):
    """RECORDED DEFECT, now two thirds of one -- §3.0.A and §3.4.

    Jeff's rate is 5 K/min.  Before step 3 a 10 K sweep at that rate locked the
    loop out at five of the six bench temperatures and left the sample at base
    temperature.  **Ratio tuning fixed 100 K**, and why is worth keeping: the
    loop's job during a ramp is to keep up, `move_tau_cl` was a fixed 300 s
    against a plant tau of 441 s there, and a ratio makes it 221 s.  The
    tracking error peaks at 2.30 K instead of running away from the allowance.

    The remaining four fail for TWO different reasons and neither is the gains.

    **10 and 30 K cannot be reached at all**: ten kelvin is 29 % of output at
    the cold end's 0.34 K/%, against a band one percent wide.

    **140 and 180 K get all the way there and then fault afterwards.**  Peak
    error 2.8 and 3.2 K, well inside the 7 K the premise check allows a
    commanded ramp.  But the allowance decays once the ramp stops, the loop is
    still railed at the band catching the last kelvin up, and at t = 984 s the
    check sees 2.50 K against a decayed allowance of 1.48 K and calls the
    cryostat broken.  That is the band again -- the window is centred where the
    sweep STARTED -- and not the tuning.
    """
    h = bench(kelvin)
    h.sup.sweep_to(kelvin + 10.0, 5.0)
    h.history.clear()
    h.minutes(120)
    last = h.history[-1]

    if kelvin in SWEEP_SURVIVES:
        assert last.state is SupervisorState.TRACKING
        assert last.filtered_k == pytest.approx(kelvin + 10.0, abs=0.05)
    else:
        assert last.state is SupervisorState.LOCKED_OUT, (
            f"{kelvin} K survived a 5 K/min sweep; if step 4 or 5 has landed, "
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
