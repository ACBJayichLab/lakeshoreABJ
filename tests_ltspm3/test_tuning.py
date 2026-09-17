"""Gain scheduling, IMC tuning, and the HOLD/MOVE split.

The controller is tuned from two *local* numbers -- gain K(T) and time constant
tau(T) -- rather than from a global percent-to-temperature curve; the argument
is in `ltspm3/control/tuning.py`'s module docstring and PID_PLAN.md §3.2.
"""

import pytest

from ltspm3.control import LoopMode, SupervisorConfig
from ltspm3.control.pid import PID, PIDConfig
from ltspm3.control.tuning import (
    ControlPhase,
    OperatingPoint,
    PlantSchedule,
    Tuner,
    TuningConfig,
    identify_first_order,
    imc_pi,
)
from ltspm3.model.sim_response import ResponseParams


# -- the tuning rule --------------------------------------------------------

def test_imc_cancels_the_plant_pole():
    """Ti = tau is what makes the closed loop first order, hence overshoot-free
    at any tau_cl."""
    kp, ti = imc_pi(gain_k_per_pct=10.0, tau_s=620.0, tau_cl_s=300.0)
    assert ti == pytest.approx(620.0)
    assert kp == pytest.approx(620.0 / (10.0 * 300.0))


def test_a_faster_closed_loop_needs_more_gain():
    slow, _ = imc_pi(10.0, 620.0, 1800.0)
    fast, _ = imc_pi(10.0, 620.0, 300.0)
    assert fast > slow
    assert fast / slow == pytest.approx(6.0)


def test_a_higher_plant_gain_needs_less_controller_gain():
    a, _ = imc_pi(5.0, 620.0, 300.0)
    b, _ = imc_pi(20.0, 620.0, 300.0)
    assert a == pytest.approx(4 * b)


def test_imc_rejects_nonsense():
    for args in [(0.0, 620.0, 300.0), (10.0, 0.0, 300.0), (10.0, 620.0, 0.0)]:
        with pytest.raises(ValueError):
            imc_pi(*args)


# -- the schedule -----------------------------------------------------------

TABLE = (
    OperatingPoint(18.2, 1.6, 300.0),
    OperatingPoint(99.6, 10.0, 620.0),
    OperatingPoint(137.3, 13.0, 620.0),
    OperatingPoint(170.7, 13.4, 620.0),
)
"""A four-row table, here as a fixture rather than in `control/` as a default.

`PlantSchedule` is the class a cryostat with a measured table uses and still
needs testing; LTSPM3 itself has no table and takes `FittedSchedule` instead.
"""


def test_schedule_interpolates_between_measured_points():
    s = PlantSchedule(TABLE)
    g = s.gain_at(120.0)
    assert s.gain_at(99.6) < g < s.gain_at(137.3)


def test_schedule_clamps_rather_than_extrapolating_gain():
    """Extrapolating a gain is how a controller ends up violently wrong at a
    temperature nobody measured."""
    s = PlantSchedule(TABLE)
    assert s.gain_at(2.0) == pytest.approx(s.gain_at(18.2))
    assert s.gain_at(500.0) == pytest.approx(s.gain_at(170.7))
    assert s.extrapolating(2.0) and s.extrapolating(500.0)
    assert not s.extrapolating(120.0)


def test_an_empty_schedule_means_the_shipped_fit():
    """§3.2: `K(T)` and `tau(T)` come from the model, not from a paste."""
    from ltspm3.control.tuning import FittedSchedule
    from ltspm3.model import fitted_response as M

    t = Tuner(TuningConfig())
    assert isinstance(t.schedule, FittedSchedule)
    assert t.schedule.key == M.FIT_KEY
    for kelvin in (10.0, 60.0, 118.0, 180.0):
        assert t.schedule.gain_at(kelvin) == pytest.approx(M.gain_k_per_pct(kelvin))
        assert t.schedule.tau_at(kelvin) == pytest.approx(M.tau_s(kelvin))
    # And it clamps at the table's ends, like any other schedule here.
    assert t.schedule.gain_at(1.0) == pytest.approx(t.schedule.gain_at(M.T_MIN_K))
    assert t.schedule.extrapolating(300.0)


def test_the_schedule_agrees_with_what_pid_tuning_would_have_pasted():
    """PID_PLAN.md's "pastes rot" trap, closed by not having a paste.

    These are the rows `python analysis/pid_tuning.py --rows` printed, which
    under §3.2 as first written would have been pasted into this package by
    hand.  The model's own functions reproduce them, so the paste is
    unnecessary -- and this test is what would notice if the two ever came
    apart.

    **One per cent, and that is the claim.**  The rows are the analysis
    script's own rounding of the same fit, so the residual here is quantisation
    and not disagreement; `FittedSchedule`'s docstring says "better than 0.5 %",
    which the gain misses at the cold end.  `abs=0.05` on tau because tau(10 K)
    is a tenth of a second and a relative bound on it is a bound on the
    printed decimal place.
    """
    from ltspm3.model import fitted_response as M

    rows = {10: (0.337, 0.1), 20: (0.748, 1.2), 30: (1.877, 9.0),
            60: (8.629, 166.5), 100: (12.488, 441.3), 140: (12.740, 589.8),
            180: (12.085, 611.2), 190: (11.968, 607.2)}
    for kelvin, (gain, tau) in rows.items():
        assert M.gain_k_per_pct(kelvin) == pytest.approx(gain, rel=0.01)
        assert M.tau_s(kelvin) == pytest.approx(tau, rel=0.01, abs=0.05)


def test_gains_are_bounded_even_with_an_absurd_schedule():
    cfg = TuningConfig(schedule=(OperatingPoint(100.0, 1e-6, 1e6),))
    tuner = Tuner(cfg)
    tuner.delay_s = 3.0
    kp, ti = tuner.gains_for(100.0)
    assert cfg.min_kp_pct_per_k <= kp <= cfg.max_kp_pct_per_k
    assert cfg.min_ti_delays * tuner.delay_s <= ti <= cfg.max_ti_s


# -- HOLD vs MOVE -----------------------------------------------------------

def test_holding_is_gentler_than_moving():
    t = Tuner()
    hold_kp, _ = t.gains_for(99.6, ControlPhase.HOLD)
    move_kp, _ = t.gains_for(99.6, ControlPhase.MOVE)
    assert move_kp > hold_kp, "moving must be more responsive than holding"


def test_a_ramp_always_means_move():
    t = Tuner()
    assert t.update_phase(0.0, error_k=0.0, ramping=True) is ControlPhase.MOVE


def test_returning_to_hold_requires_sustained_settling():
    """Chattering between two tunings is worse than either of them."""
    t = Tuner()
    t.update_phase(0.0, error_k=5.0, ramping=False)
    assert t.phase is ControlPhase.MOVE
    assert t.update_phase(10.0, error_k=0.0, ramping=False) is ControlPhase.MOVE
    assert t.update_phase(60.0, error_k=0.0, ramping=False) is ControlPhase.MOVE
    assert t.update_phase(200.0, error_k=0.0, ramping=False) is ControlPhase.HOLD


def test_settling_timer_restarts_if_the_error_grows_again():
    t = Tuner()
    t.update_phase(0.0, error_k=5.0, ramping=False)
    t.update_phase(10.0, error_k=0.0, ramping=False)
    t.update_phase(20.0, error_k=1.0, ramping=False)      # not settled after all
    assert t.update_phase(140.0, error_k=0.0, ramping=False) is ControlPhase.MOVE


# -- bumpless retuning ------------------------------------------------------

def test_changing_gains_does_not_step_the_output():
    """The integral is stored in kelvin-seconds but contributes ki*I percent,
    so a retune must rescale it or every schedule change kicks the heater."""
    pid = PID(PIDConfig(kp=0.02, ti=900.0))
    pid.prime(63.0)
    for _ in range(50):
        pid.update(measurement=99.0, slope=0.0, dt=4.0)
    before = pid.update(measurement=99.0, slope=0.0, dt=4.0).output

    pid.set_gains(0.2, 620.0)
    after = pid.update(measurement=99.0, slope=0.0, dt=0.0).output
    assert after == pytest.approx(before, abs=0.02), f"{before:.4f} -> {after:.4f}"


# -- step-response identification -------------------------------------------

def test_identify_recovers_a_known_first_order_response():
    import math
    tau, t_inf, t0 = 620.0, 137.0, 130.0
    samples = [(t, t_inf - (t_inf - t0) * math.exp(-t / tau)) for t in range(0, 3000, 10)]
    fitted_inf, fitted_tau, r2 = identify_first_order(samples)
    assert fitted_tau == pytest.approx(tau, rel=0.05)
    assert fitted_inf == pytest.approx(t_inf, abs=0.5)
    assert r2 > 0.999


def test_identify_rejects_data_with_no_step():
    samples = [(float(t), 100.0) for t in range(0, 1000, 10)]
    with pytest.raises(ValueError):
        identify_first_order(samples)


# -- closed loop ------------------------------------------------------------

def test_a_sweep_arrives_without_meaningful_overshoot(harness):
    """Smoothing the trajectory plus velocity feedforward: without either this
    plant overshoots by hundreds of millikelvin.

    **150 mK, and not the 100 mK the phase 3 gate was written at.**  The corner
    is scheduled as ``move_speed * tau(T)`` now, where the gate was measured
    against a flat 300 s, and this harness's plant is the two-pole model with
    ONE tau everywhere -- so the scheduled corner is shorter here than the
    constant it replaced and the loop is correspondingly more aggressive.  The
    cryostat's own plant is the fitted one, and a 3 K move on it is graded by
    `test_bench.py` at all six bench temperatures.
    """
    cfg = SupervisorConfig()
    h = harness(response=ResponseParams(tau_fast=620.0), sup_cfg=cfg)
    h.settle_filter(60)
    h.sup.set_mode(LoopMode.PID)
    target = h.equilibrium_k + 3.0
    h.sup.sweep_to(target, rate_k_per_min=0.6)
    h.step(2500)

    peak = max(s.filtered_k for s in h.history[-2500:] if s.filtered_k is not None)
    assert peak - target < 0.15, f"overshoot {1000*(peak-target):.0f} mK"


def test_overshoot_does_not_depend_on_sweep_rate(harness):
    """The signature of a trajectory the loop can actually follow."""
    peaks = []
    for rate in (0.3, 1.2):
        cfg = SupervisorConfig()
        h = harness(response=ResponseParams(tau_fast=620.0), sup_cfg=cfg)
        h.settle_filter(60)
        h.sup.set_mode(LoopMode.PID)
        target = h.equilibrium_k + 3.0
        h.sup.sweep_to(target, rate_k_per_min=rate)
        h.step(2500)
        peaks.append(max(s.filtered_k for s in h.history[-2500:]
                         if s.filtered_k is not None) - target)
    assert abs(peaks[0] - peaks[1]) < 0.05, f"overshoot varies with rate: {peaks}"


def test_the_loop_switches_phase_over_a_sweep(harness):
    h = harness(response=ResponseParams(tau_fast=620.0))
    h.settle_filter(60)
    h.sup.set_mode(LoopMode.PID)
    h.sup.sweep_to(h.equilibrium_k + 2.0, rate_k_per_min=0.6)
    h.step(60)
    assert h.sup.status.phase == "move"
    h.step(1200)
    assert h.sup.status.phase == "hold", "never settled back to the quiet tuning"
