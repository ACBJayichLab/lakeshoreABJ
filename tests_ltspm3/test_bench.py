"""Phase 3's bench -- the fitted plant, six temperatures, one grader.

plans/pid-3-loop.md §3.7, one test per row: the loop arms and tracks, holds
quietly, moves a setpoint and sweeps at 5 K/min, survives a glitching sensor, a
lost one, a crash and a model that is wrong on purpose, and keeps the authority
band where rule 5 says.  All six temperatures for every row, because tau spans
three decades and the gain forty-fold across them and a loop that works at
118 K says nothing about 10 K.

Nothing here is marked skip or xfail: a skipped test fails this build
(.github/workflows/tests.yml).
"""
from __future__ import annotations

import statistics

import pytest

from bench_plant import BENCH_TEMPERATURES, FittedHarness, bench_control_config
from ltspm3.control import LoopMode, SupervisorState
from ltspm3.control.filters import MeasurementFilter
from ltspm3.model import fitted_response as M

pytestmark = pytest.mark.parametrize("kelvin", BENCH_TEMPERATURES)


def arrival_floor_k(kelvin: float) -> float:
    """How close to the setpoint a settled loop can be asked to land.

    Five sigma of the thermometer's own noise, whose rms in kelvin the model
    gives as `noise_quadratic * T**2`.  A loop cannot sit closer to a setpoint
    than it can see, and the floor is a factor of 300 between 10 and 180 K, so
    a single tolerance in kelvin would be either meaningless at the warm end or
    impossible at the cold one.  Five rather than three because this is one
    sample of a settled mean and not a distribution; the callers take the
    larger of it and their own bound.
    """
    return 5.0 * M.FittedParams().noise_quadratic * kelvin ** 2


# -- the config the bench runs on ------------------------------------------


def test_the_bench_loads_its_limits_from_the_armed_config(kelvin):
    """§3.7: "tables loaded from `config-ltspm3-armed.yaml`, not built in the
    test".  The file is loaded through `lschart.config.load`, so a typo in it
    is a failure here rather than a surprise on the cryostat."""
    cfg = bench_control_config()
    assert cfg.enabled, "the bench needs the loop built; `run --arm` closes it"
    assert cfg.supervisor.hard_max_pct == 85.0, "the ceiling is the guard"
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
    assert lo <= h.bench_pct <= hi, "the band does not contain its own operating point"


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
    """§3.6: a quiet hold commands almost nothing.

    **The criterion is two DAC codes a minute**, which is worth saying out loud
    because a hold this quiet can only ever return one of a very few answers --
    at one code it is the dither and not the loop that is moving the heater at
    all.  Written as `dac_step_pct` rather than as a percentage so it stays the
    criterion if the 218's resolution is ever restated.
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
    # And the hold is no noisier than the thermometer under it: the model's
    # `noise_quadratic * T**2` is that floor's rms in kelvin, and `3e3` is
    # three sigma of it converted to the millikelvin this comparison is made
    # in.  20 mK is the alternative floor for the cold end, where three sigma
    # of the thermometer is a fraction of a millikelvin and the dither's own
    # quantisation dominates instead.
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
    floor = arrival_floor_k(kelvin)
    assert settled == pytest.approx(kelvin + 3.0, abs=max(0.05, floor))


def test_a_5_k_per_min_sweep_of_10_K_arrives(kelvin, bench):
    """§3.7's second row: a ten kelvin sweep at Jeff's 5 K/min arrives, at all
    six temperatures, and the loop is still tracking at the end of it.

    A ramp of this size lags by `r*tau`, which is several kelvin at the cold
    end, and none of that is an excursion -- it is what a commanded move looks
    like.  That is why the premise check is made in watts, where `dQ` carries
    `C dT/dt`: in kelvin the loop was being told that a sweep it was executing
    correctly was evidence the cryostat was broken.
    """
    h = bench(kelvin)
    h.sup.sweep_to(kelvin + 10.0, 5.0)
    h.history.clear()
    h.minutes(120)
    last = h.history[-1]
    reached = [s.filtered_k for s in h.history if s.filtered_k is not None]

    assert last.state is SupervisorState.TRACKING
    floor = arrival_floor_k(kelvin)
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
    assert s.state is SupervisorState.FROZEN
    assert s.output_pct == pytest.approx(before, abs=1e-9)

    # And it recovers, without the output having moved in between.
    h.cryostat.clear_faults()
    h.history.clear()
    h.minutes(5)
    assert all(s.output_pct == pytest.approx(before, abs=0.05)
               for s in h.history if s.output_pct is not None)


# -- §3.5, the crash --------------------------------------------------------


def test_a_crash_disengages_the_loop_and_needs_an_acknowledge(kelvin, bench):
    """§3.5.  Jeff, 2026-09-11: a crashed PID disengages.

    It did not.  The exception propagated to the poller with the loop still in
    `tracking` and still believing it owned the heater, and what happened next
    depended on how the caller handled it.  The output never moved, which was
    the one mercy, but nothing said so and nothing stopped the next cycle
    trying again.

    The way out is the lockout's, and for the same reason: nobody has looked
    at the cryostat yet.
    """
    h = bench(kelvin)
    before = h.sup.output_pct
    h.sup.filter = None                      # the cheapest possible detonation

    s = h.step(1)                            # and no exception escapes
    assert s.state is SupervisorState.CRASHED
    assert any("CRASHED" in a for a in s.alarms)
    assert h.sup.mode is LoopMode.OFF, "a crashed loop must let go"
    assert h.sup.output_pct == pytest.approx(before, abs=1e-9)
    assert "ack" in s.reason

    # It keeps not writing, rather than retrying into a broken loop.
    h.step(20)
    assert h.inst.get_analog_percent() == pytest.approx(before, abs=1e-9)

    # And `arm` is refused until somebody has looked.  The refusal happens
    # BEFORE the setpoint moves, which it did not used to: `arm` set the
    # setpoint and then discovered it was latched, so a loop that had refused
    # to arm was left carrying a setpoint it had not accepted -- and after a
    # crash the refusal could be pre-empted by the very thing that crashed.
    with pytest.raises(PermissionError, match="crashed"):
        h.sup.arm(kelvin)

    # Acknowledging is what an operator does after fixing the cryostat, so the
    # sabotage is undone first -- `acknowledge` re-primes the filter, and there
    # has to be one.
    h.sup.filter = MeasurementFilter()
    h.sup.acknowledge()
    assert h.sup.state is SupervisorState.IDLE


def test_a_crash_still_lets_the_recorder_write(kelvin, bench):
    """The cryostat whose loop has just crashed is exactly when the log matters
    most, so `step()` must return a frame rather than raising."""
    h = bench(kelvin)
    h.sup.filter = None
    s = h.step(1)
    assert s is not None and s.t > 0
    assert s.output_pct is not None


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
    # smoother's and not the ramp's: the smoother is still accelerating five
    # cycles into a sweep, and asserting against the ramp's nominal rate would
    # be comparing the lead against a rate nothing is yet moving at.
    #
    # A third of slack, which is loose and deliberately so: the lead is
    # recomputed each cycle from a rate that is changing fast here, so the
    # cycle this reads and the cycle the supervisor computed on are not the
    # same point on the acceleration.  What is graded is that the lead is
    # `rate*tau/K` and not some other quantity; the exact value is pinned by
    # the width assertion above, which has no tolerance at all.
    rate = abs(h.sup.smoother.rate_k_per_s)
    want = rate * M.tau_s(kelvin) / M.gain_k_per_pct(kelvin)
    assert lead == pytest.approx(min(want, h.sup.cfg.max_velocity_ff_pct), rel=0.35)


# -- §3.7, the rest of the matrix -------------------------------------------


def test_a_lost_sensor_ramps_down_at_the_one_rate_and_locks_out(kelvin, bench):
    """Rule 3 all the way through: the fault MAY BE the sensor, so the descent
    is open loop through the model's inverse curve and asks it nothing."""
    h = bench(kelvin)
    h.cryostat.inject(dropout_channels={"218.1"})

    for _ in range(6000):
        h.step(1)
        if h.sup.state is SupervisorState.LOCKED_OUT:
            break
    assert h.sup.state is SupervisorState.LOCKED_OUT, "never finished the descent"
    assert h.sup.output_pct == pytest.approx(h.sup.cfg.safe_output_pct,
                                             abs=h.sup.cfg.dac_step_pct)

    # Monotonic down, always.  Rule 1.  One DAC code of tolerance, because the
    # dither quantises either side of the target and a 0.0019 % rounding-up is
    # not a fault response raising the heater.
    outs = [x.output_pct for x in h.history if x.output_pct is not None]
    code = h.sup.cfg.dac_step_pct
    assert all(b <= a + code + 1e-9 for a, b in zip(outs, outs[1:]))

    # `ack` is the only way out, and it disarms rather than resuming.
    with pytest.raises(PermissionError):
        h.sup.arm(kelvin)
    h.cryostat.clear_faults()
    h.sup.acknowledge()
    assert h.sup.state is SupervisorState.IDLE


def test_a_rising_coldplate_is_tracked_and_warns_and_never_faults(kelvin, bench):
    """§1: a compressor failure is a COLDPLATE event, and `δT_c` never faults.

    **Nor does its consequence** (Jeff, 2026-09-15).  This row used to say the
    loop would run out of authority and fault, and hedged the assertion with
    `if faulted` -- which was as well, because it never did: the sink was
    SCENERY.  Moving `_aux_base` moved the thermometer and left the plant where
    it was, so the disturbance the loop was supposed to react to did not exist.
    `FittedHarness.sink_offset` moves both.

    With a sink that genuinely rises, this is scenario 1 of
    plans/pid-3-review.md: a steady change the model explains, the loop needs
    less heat, and the worst case is a sample colder than intended.  A warning
    however far it goes -- including all the way to the heater at its floor --
    because a ramp-down does not improve it and a lockout would stop the loop
    resuming when the bath recovers.

    Measured, +2 K/h for two hours: the output falls (24.22 -> 14.68 % at 10 K,
    68.72 -> 64.92 at 180) and the loop holds.  The error stays under a kelvin
    at 10 to 60 K, where the gain is small enough to absorb it, and passes one
    at 100 K and above, where the error row warns.
    """
    h = bench(kelvin)
    h.history.clear()
    for i in range(3600):
        # +2 K over an hour, and then it keeps going.
        h.sink_offset(2.0 * (i * h.DT / 3600.0))
        h.step(1)
        assert h.sup.state is SupervisorState.TRACKING, (
            f"scenario 1 stopped the loop: {h.history[-1].alarms}")

    outs = [x.output_pct for x in h.history if x.output_pct is not None]
    assert max(outs) <= h.sup.cfg.hard_max_pct + 1e-9
    assert outs[-1] < outs[0], "the loop should need LESS heat, not more"
    # **A TREND, not a per-cycle step.**  This used to assert that no single
    # write ever rose by more than two codes, which was true only because the
    # output rate limiter was the trajectory's kelvin rate through the gain and
    # made the heater creep; a loop with real authority answers the
    # measurement's own noise cycle by cycle and rises by a few codes often.
    # What scenario 1 actually claims is that the loop needs steadily LESS
    # heat, so that is what is measured -- on block means, over a block long
    # enough that the noise averages out and far shorter than the disturbance.
    block = max(1, int(60.0 / h.DT))
    means = [sum(outs[i:i + block]) / len(outs[i:i + block])
             for i in range(0, len(outs) - block + 1, block)]
    for a, b in zip(means, means[1:]):
        assert b <= a + 2 * h.sup.cfg.dac_step_pct + 1e-9, (
            f"the output trend rose: {a:.3f} -> {b:.3f} %")

    worst = max(abs(x.error_k) for x in h.history if x.error_k is not None)
    warned = [a for x in h.history for a in x.alarms if "warn_error_k" in a]
    assert bool(warned) == (worst >= h.sup.cfg.warn_error_k), (
        f"worst error {worst:.2f} K, {len(warned)} warnings")
    assert not [a for x in h.history for a in x.alarms
                if "authority exhausted" in a]


@pytest.mark.parametrize("wrong", ("gain_low", "gain_high", "tau_low",
                                  "tau_high", "level"))
def test_the_loop_survives_a_model_that_is_wrong_on_purpose(kelvin, bench, wrong):
    """§3.7's last row: the CRYOSTAT unchanged, the controller's model wrong.

    A fit is a description of a cryostat on one day, and REFIT_PLAN §7.3 says
    the level of this one has a shelf life -- handling the heater wiring moves
    it 0.8 %, which is 3 K at 118 K.  So the loop has to work while it is
    somewhat wrong about the plant, and the failure that matters is a FALSE
    FAULT: ramping the cryostat down over a model error.

    **What is perturbed here is the controller's model, not the plant.**  Those
    are not the same test and the difference is the whole point of the watt
    residual: a controller wrong about K is mistuned, and must not fault; a
    heater delivering 12 % less power is a genuine fault, and must.  Perturbing
    the plant tests the second and calls it the first.
    """
    from ltspm3.control.tuning import FittedSchedule

    h = bench(kelvin)
    sched = h.sup.tuner.schedule
    if wrong.startswith("gain"):
        f = 0.8 if wrong.endswith("low") else 1.2
        base = FittedSchedule()
        h.sup.tuner.schedule = type(
            "Wrong", (), {"gain_at": lambda _s, k, _b=base, _f=f: _b.gain_at(k) * _f,
                          "tau_at": lambda _s, k, _b=base: _b.tau_at(k),
                          "extrapolating": lambda _s, k: False})()
    elif wrong.startswith("tau"):
        f = 0.7 if wrong.endswith("low") else 1.3
        base = FittedSchedule()
        h.sup.tuner.schedule = type(
            "Wrong", (), {"gain_at": lambda _s, k, _b=base: _b.gain_at(k),
                          "tau_at": lambda _s, k, _b=base, _f=f: _b.tau_at(k) * _f,
                          "extrapolating": lambda _s, k: False})()
    else:
        # The curve the feedforward and the band's centre stand on sits 0.3 K
        # warm -- one reseated connector's worth.
        curve = h.sup.feedforward.curve
        h.sup.feedforward.curve = type(
            "Warm", (), {
                "kelvin_for": lambda _s, p, _c=curve: _c.kelvin_for(p) + 0.3,
                "percent_for": lambda _s, k, _c=curve: _c.percent_for(k - 0.3),
                "gain_at": lambda _s, p, _c=curve: _c.gain_at(p),
                "relative_power": lambda _s, p, _c=curve: _c.relative_power(p),
                "local_exponent": lambda _s, p, _c=curve: _c.local_exponent(p),
            })()
    assert sched is not None

    h.history.clear()
    h.minutes(60)
    assert h.sup.state is SupervisorState.TRACKING, (
        f"a {wrong} error of this size faulted the loop")
    assert max(x.output_pct for x in h.history
               if x.output_pct is not None) <= h.sup.cfg.hard_max_pct + 1e-9

    # And it still holds the temperature, which is what being mistuned rather
    # than broken means.
    settled = statistics.fmean(
        [x.filtered_k for x in h.history[-60:] if x.filtered_k is not None])
    assert settled == pytest.approx(kelvin, abs=max(0.2, 0.01 * kelvin))
