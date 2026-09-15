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


def test_a_3_k_move_lands(kelvin, bench):
    """§3.7's first row, green at all six for the first time (step 6).

    It has taken every step of phase 3 to get here, and each one fixed a
    different thing.  Before the band followed the setpoint this did three
    different things: at 30 K it railed and **silently gave up** 0.883 K short,
    under `max_error_k`, with no alarm and nothing in the log; at 10 K it read a
    legitimate move as a broken premise and ramped the heater to zero.
    """
    h = bench(kelvin)
    h.sup.sweep_to(kelvin + 3.0, 5.0)
    h.history.clear()
    h.minutes(90)
    last = h.history[-1]
    reached = [s.filtered_k for s in h.history if s.filtered_k is not None]

    assert last.state is SupervisorState.TRACKING
    overshoot = (max(reached) - (kelvin + 3.0)) / 3.0
    assert overshoot < 0.05, f"{100 * overshoot:.1f} % overshoot at {kelvin} K"
    settled = statistics.fmean(
        [s.filtered_k for s in h.history[-30:] if s.filtered_k is not None])
    floor = 5e3 * M.FittedParams().noise_quadratic * kelvin ** 2
    assert settled == pytest.approx(kelvin + 3.0, abs=max(0.05, floor))


def test_a_5_k_per_min_sweep_of_10_K_arrives(kelvin, bench):
    """§3.7's second row, and the requirement phase 3 exists for.

    Jeff's rate is 5 K/min.  Before step 3 this locked the loop out at five of
    the six bench temperatures and left the sample at base temperature.  Four
    steps moved four different limits out of the way, one at a time:

    * **step 3**, 100 K -- `move_tau_cl` was a fixed 300 s against a plant tau
      of 441 s, and a ratio makes it 221 s;
    * **step 4**, 140 and 180 K -- they had been ARRIVING and then faulting
      afterwards, as the ramp allowance decayed while the loop was still
      railed at a window centred where the sweep started;
    * **step 5**, the cold end's rate -- ten kelvin is 29 % of output at 10 K
      and a trim rate allowed two and a half hours for a two-minute sweep;
    * **step 6**, the premise itself -- the lag peaked at 8.78 K against a
      kelvin check that allowed 7.0, so the loop was being told that a sweep it
      was executing correctly was evidence the cryostat was broken.

    The tracking lag is genuinely 7.6 K at 10 K and 1.3 K at 140 K, and none of
    it is an anomaly: it is `r*tau`, the lag a ramp commands.  That is the
    whole reason the premise had to move into watts, where `dQ` carries
    `C dT/dt` and a commanded move is not an excursion.
    """
    h = bench(kelvin)
    h.sup.sweep_to(kelvin + 10.0, 5.0)
    h.history.clear()
    h.minutes(120)
    last = h.history[-1]
    reached = [s.filtered_k for s in h.history if s.filtered_k is not None]

    assert last.state is SupervisorState.TRACKING
    floor = 5e3 * M.FittedParams().noise_quadratic * kelvin ** 2
    assert last.filtered_k == pytest.approx(kelvin + 10.0, abs=max(0.08, floor))
    overshoot = (max(reached) - (kelvin + 10.0)) / 10.0
    assert overshoot < 0.05, f"{100 * overshoot:.1f} % overshoot at {kelvin} K"


def test_the_residual_is_quiet_through_a_sweep(kelvin, bench):
    """§3.4: `dQ` is valid at a hold AND during a sweep, which is the whole
    reason the premise moved into watts.

    Where the plant is faster than the slope is measured -- 10 and 30 K, where
    tau is 0.1 and 9 s against a 30 s regression -- it correctly has NO OPINION
    rather than a wrong one.
    """
    h = bench(kelvin)
    h.sup.sweep_to(kelvin + 10.0, 5.0)
    h.history.clear()
    h.minutes(120)

    judged = [s for s in h.history if s.missing_power_w is not None]
    if not judged:
        assert h.history[-1].residual_reason
        return
    worst = max(judged, key=lambda s: abs(s.missing_power_w))
    assert abs(worst.missing_power_w) < max(
        0.010, h.sup.cfg.warn_sigma * worst.sigma_q_w), (
        f"{1e3 * worst.missing_power_w:+.2f} mW at {kelvin} K")
    assert max(s.dq_step_w for s in h.history) < h.sup.cfg.fault_mw * 1e-3


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


# -- §3.0.A / step 4: the band ----------------------------------------------


def test_the_band_is_centred_on_the_output_that_reaches_the_setpoint(kelvin, bench):
    """Rule 5, reworded.  The window follows the setpoint."""
    h = bench(kelvin)
    lo, hi = h.sup.band
    assert h.sup.band_centre_pct() == pytest.approx(h.bench_pct, abs=1e-6)
    assert lo == pytest.approx(h.bench_pct - h.sup.cfg.authority_pct, abs=1e-6)
    assert hi == pytest.approx(
        min(h.bench_pct + h.sup.cfg.authority_pct, h.sup.cfg.hard_max_pct),
        abs=1e-6)

    # And it MOVES with the setpoint, which is the whole change.
    h.sup.set_setpoint(kelvin + 20.0, ramp=False)
    h.step(1)
    assert h.sup.band_centre_pct() > h.bench_pct


def test_the_hard_ceiling_is_the_one_thing_the_band_cannot_move(kelvin, bench):
    """A setpoint nobody has measured must not open the ceiling."""
    h = bench(kelvin)
    h.sup.set_setpoint(1000.0, ramp=False)
    h.minutes(30)
    lo, hi = h.sup.band
    assert hi <= h.sup.cfg.hard_max_pct + 1e-9
    outs = [s.output_pct for s in h.history if s.output_pct is not None]
    assert max(outs) <= h.sup.cfg.hard_max_pct + 1e-9


def test_the_band_widens_only_by_what_the_ramp_needs(kelvin, bench):
    """§3.0.B: sustaining 5 K/min needs `rate*tau/K`, which is 0.02 % at 10 K
    and 4.10 % at 180 K.  The band grants exactly that and nothing else, and
    takes it back when the sweep stops."""
    h = bench(kelvin)
    settled_width = h.sup.band[1] - h.sup.band[0]
    assert h.sup.ramp_lead_pct() == 0.0

    h.sup.sweep_to(kelvin + 10.0, 5.0)
    h.step(5)
    lead = h.sup.ramp_lead_pct()
    assert lead > 0.0
    assert h.sup.band[1] - h.sup.band[0] == pytest.approx(
        settled_width + 2 * lead, abs=1e-6) or h.sup.band[1] >= h.sup.cfg.hard_max_pct

    # `rate * tau / K` at the rate ACTUALLY being commanded, which is the
    # smoother's and not the ramp's.
    #
    # **Those are very different numbers and that is a finding, not a detail.**
    # `smooth_tau_s` is 300 s -- chosen to take the corners off a 0.5 K/min
    # sweep, where a ramp lasts hours.  A 10 K sweep at Jeff's 5 K/min lasts
    # 120 s, so the smoother never gets anywhere near the commanded rate: five
    # cycles in it is at 0.0027 K/s against the ramp's 0.0833, and the band
    # widens by 0.14 % where 5 K/min at 180 K would need 4.23 %.  The loop is
    # not sweeping at 5 K/min; it is sweeping at whatever the smoother lets
    # through.  Step 5's business, with the one rate.
    rate = abs(h.sup.smoother.rate_k_per_s)
    want = rate * M.tau_s(kelvin) / M.gain_k_per_pct(kelvin)
    assert lead == pytest.approx(min(want, h.sup.cfg.max_velocity_ff_pct), rel=0.35)
