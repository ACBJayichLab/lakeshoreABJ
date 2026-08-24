"""Behaviour when the calibration does not describe the present regime.

The steady-state curve was measured with the cryocooler running and the shields
cold.  Real operation also includes the cooler being off -- room temperature
before a cooldown, after a warmup, during service -- where the same heater
percent produces a completely different temperature because there is no cooling
power to fight.  A temperature log records none of that, so the software cannot
detect the regime from the data it is given; it has to be robust to being wrong
about it, and it has to say so.
"""

import pytest

from lschart.control import LoopMode, SupervisorConfig, SupervisorState
from lschart.control.feedforward import Feedforward, FeedforwardConfig
from lschart.control.pid import PID, PIDConfig
from lschart.instruments.sim import PlantParams


def cooler_off(**kw):
    """Cooler off: bath sits at room temperature, the heater adds on top."""
    params = dict(t_bath=295.0, ref_rise=20.0, calibration=(), tau_fast=1800.0)
    params.update(kw)
    return PlantParams(**params)


# -- the structural protection ---------------------------------------------

def test_feedforward_is_governed_by_local_slope_not_absolute_level():
    """Feedforward is a *difference* of percent_for() between two setpoints, so
    what reaches the output is the curve's local slope over the interval
    actually traversed -- i.e. 1/gain -- not where the curve sits absolutely.

    That is the whole reason a regime-specific calibration is tolerable: a
    sweep traverses a few kelvin, and only the gain across those few kelvin
    matters.  It is a weak dependence, not no dependence.
    """
    ff = Feedforward()
    pid = PID(PIDConfig(setpoint=100.0), feedforward=ff)
    pid.prime(63.076)

    delta = pid._ff(101.0)                       # a 1 K move
    gain = ff.gain_at(ff.percent_for(100.5))     # K/% midway
    assert delta == pytest.approx(1.0 / gain, rel=0.15)


def test_errors_that_are_flat_across_the_traversed_interval_largely_cancel():
    """The physically common case: a parasitic load that shifts the percent
    needed by roughly the same amount over the few kelvin a sweep covers."""
    base = Feedforward().cfg.calibration
    # A curve needing ~2% more heater everywhere, built so the interpolant is
    # genuinely offset rather than merely having offset knots.
    ff_a = Feedforward()
    shifted = tuple((ff_a.percent_for(k) + 2.0, k) for _, k in base)
    ff_b = Feedforward(FeedforwardConfig(calibration=shifted, max_pct=80.0))

    a = PID(PIDConfig(setpoint=100.0), feedforward=ff_a)
    b = PID(PIDConfig(setpoint=100.0), feedforward=ff_b)
    a.prime(ff_a.percent_for(100.0))
    b.prime(ff_b.percent_for(100.0))

    da, db = a._ff(103.0), b._ff(103.0)
    assert da == pytest.approx(db, rel=0.10), f"{da:.4f} vs {db:.4f}"


def test_feedforward_contribution_is_bounded():
    """What a wrong regime actually costs is a wrong local slope.  Cap it."""
    pid = PID(PIDConfig(setpoint=100.0), feedforward=Feedforward(), ff_limit_pct=0.4)
    pid.prime(63.076)
    assert pid._ff(400.0) == pytest.approx(0.4)
    assert pid.ff_clamped
    assert pid._ff(5.0) == pytest.approx(-0.4)
    pid._ff(100.5)
    assert not pid.ff_clamped, "a small, sane move must not report clamping"


def test_disabling_feedforward_leaves_a_working_feedback_loop():
    """The right choice in a regime the curve was never measured in."""
    pid = PID(PIDConfig(setpoint=100.0),
              feedforward=Feedforward(FeedforwardConfig(enabled=False)))
    pid.prime(63.0)
    assert pid._ff(200.0) == 0.0


# -- closed loop in the wrong regime ---------------------------------------

def test_regime_mismatch_is_detected_and_announced(harness):
    plant = cooler_off()
    h = harness(plant=plant, start_k=plant.steady_state(63.076))
    h.settle_filter(60)
    h.sup.set_mode(LoopMode.PID)
    h.sup.set_setpoint(h.equilibrium_k, ramp=False)
    st = h.step(80)

    assert st.model_error_k is not None
    assert abs(st.model_error_k) > 100.0
    assert st.model_trusted is False
    assert any("does not describe this regime" in a for a in st.alarms)


def test_the_loop_still_holds_in_the_wrong_regime(harness):
    """Detecting the mismatch must not mean giving up: the integral is slower
    than feedforward but it is always correct."""
    plant = cooler_off()
    h = harness(plant=plant, start_k=plant.steady_state(63.076))
    h.settle_filter(60)
    h.sup.set_mode(LoopMode.PID)
    h.sup.set_setpoint(h.equilibrium_k, ramp=False)
    start = h.sup.output_pct
    h.step(120)

    assert h.sup.state is SupervisorState.TRACKING
    outs = [s.output_pct for s in h.history[-120:] if s.output_pct is not None]
    assert max(outs) - min(outs) < 0.1, "output wandered on a bad model"
    assert abs(h.sup.output_pct - start) < 0.1


def test_a_sweep_in_the_wrong_regime_stays_inside_the_band(harness):
    """The authority band is the backstop and it is absolute -- it is expressed
    in percent, so it holds regardless of what percent means thermally."""
    plant = cooler_off()
    cfg = SupervisorConfig(max_error_k=1000.0, anomaly_demand_pct=1000.0)
    h = harness(plant=plant, start_k=plant.steady_state(63.076), sup_cfg=cfg)
    h.settle_filter(60)
    h.sup.set_mode(LoopMode.PID)
    h.sup.sweep_to(plant.steady_state(63.076) + 10.0, rate_k_per_min=2.0)
    h.step(300)

    lo, hi = h.sup.band
    outs = [s.output_pct for s in h.history[-300:] if s.output_pct is not None]
    assert max(outs) <= hi + 1e-9, f"left the band upward: {max(outs):.3f} > {hi:.3f}"
    assert min(outs) >= lo - 1e-9


def test_model_check_is_silent_while_ramping(harness):
    """During a ramp the measurement is *supposed* to lag the model, so the
    check must not fire -- otherwise every legitimate sweep raises an alarm."""
    h = harness()
    h.settle_filter(40)
    h.sup.set_mode(LoopMode.PID)
    h.step(10)
    h.sup.sweep_to(h.equilibrium_k + 2.0, rate_k_per_min=0.5)
    st = h.step(20)
    assert st.model_error_k is None
    assert st.model_trusted is True


def test_model_check_passes_in_the_regime_it_was_measured_in(harness):
    """The corollary: no false alarm when the calibration does apply."""
    h = harness()
    h.settle_filter(40)
    h.sup.set_mode(LoopMode.PID)
    st = h.step(60)
    assert st.model_trusted is True
    assert not any("does not describe" in a for a in st.alarms)
