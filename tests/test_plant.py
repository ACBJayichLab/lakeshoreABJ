"""The plant model: an exact quadratic actuator and a fitted thermal response.

The 218's analog output is a voltage into a stable 50 ohm heater, so power goes
as pct**2 exactly and temperature-independently.  Everything uncertain lives in
T(P), where heat capacity and conductance both vary with temperature.  Keeping
those separate is the point of `lschart/plant.py`; these tests hold the line.
"""

import math

import pytest

from lschart.control.dither import SigmaDeltaDither
from lschart.control.feedforward import Feedforward, FeedforwardConfig
from lschart.instruments.sim import PlantParams
from lschart.plant import MEASURED_CURVE, SteadyStateCurve, fit_thermal_exponent


# -- the exactly-known half -------------------------------------------------

def test_power_is_quadratic_in_percent():
    """P = V**2 / R with R fixed.  Nothing to fit, nothing T-dependent."""
    c = SteadyStateCurve()
    assert c.relative_power(c.ref_pct) == pytest.approx(1.0)
    assert c.relative_power(2 * c.ref_pct) == pytest.approx(4.0)
    assert c.relative_power(c.ref_pct / 2) == pytest.approx(0.25)
    assert c.relative_power(0.0) == 0.0


def test_power_inverse_round_trips():
    c = SteadyStateCurve()
    for pct in (20.0, 43.0, 63.076, 70.0):
        assert c.percent_for_relative_power(c.relative_power(pct)) == pytest.approx(pct)


# -- the fitted half --------------------------------------------------------

def test_curve_reproduces_every_measured_point():
    c = SteadyStateCurve()
    for pct, kelvin in MEASURED_CURVE:
        assert c.kelvin_for(pct) == pytest.approx(kelvin, abs=0.01)


def test_curve_inverts():
    c = SteadyStateCurve()
    for pct, kelvin in MEASURED_CURVE:
        assert c.percent_for(kelvin) == pytest.approx(pct, abs=0.005)


def test_the_exponent_is_not_constant():
    """The whole reason a single power law was the wrong model.

    A fixed exponent is what the previous n = 5.0 fit assumed; extrapolating
    the high-temperature fit down to 43% predicts 12.8 K against 18.2 K measured.
    """
    c = SteadyStateCurve()
    low = c.local_exponent(45.0)
    high = c.local_exponent(64.5)
    assert low < high - 1.0, f"exponent barely varies: {low:.2f} -> {high:.2f}"
    assert 4.0 < low < 6.0
    assert 6.5 < high < 9.0


def test_fit_recovers_the_thermal_exponent():
    m, r2 = fit_thermal_exponent(MEASURED_CURVE[1:])
    assert 3.0 < m < 3.6, m
    assert r2 > 0.98, r2
    # The lumped exponent is twice the thermal one, because P ~ pct**2.
    assert 2 * m == pytest.approx(6.7, abs=0.5)


def test_fit_rejects_degenerate_input():
    with pytest.raises(ValueError):
        fit_thermal_exponent([(63.0, 100.0)])
    with pytest.raises(ValueError):
        fit_thermal_exponent([(63.0, 100.0), (63.0, 120.0)])


# -- the consequence for the actuator ---------------------------------------

def test_one_dac_code_is_about_100_mK():
    """Was believed to be ~76 mK under the old n = 5 fit.  The corrected model
    makes the code coarser, so dithering matters more, not less."""
    c = SteadyStateCurve()
    per_code = c.gain_at(63.076) * 0.01
    assert 0.08 < per_code < 0.12, f"{per_code*1000:.0f} mK per code"


def test_dithering_voltage_does_not_exactly_average_power():
    """P ~ V**2, so <P> = <V>**2 + Var(V): the mean power is slightly above the
    power at the mean voltage.  Real, but ~2 uK against a ~2.5 mK noise floor.

    Worth a test because it is the kind of bias that would be invisible until
    someone chased a systematic offset for a week.
    """
    d = SigmaDeltaDither(0.01)
    ref = 63.076
    codes = [d.quantise(63.0763) for _ in range(20000)]
    mean_v = sum(codes) / len(codes)
    mean_p = sum((v / ref) ** 2 for v in codes) / len(codes)
    excess = mean_p - (mean_v / ref) ** 2

    assert excess > 0, "variance must add power, not remove it"
    bias_k = 3.158 * excess * 95.6
    assert bias_k < 1e-4, f"dither power bias {bias_k*1e6:.1f} uK is no longer negligible"


# -- simulator and controller must not drift apart --------------------------

def test_simulator_and_feedforward_share_one_curve():
    """They are calibrated from the same measurements on purpose: a mismatch
    between them should be something a test opts into, not an accident."""
    plant = PlantParams()
    ff = Feedforward()
    for pct in (63.076, 64.5, 66.0, 68.0):
        assert plant.steady_state(pct) == pytest.approx(ff.kelvin_for(pct), abs=0.01)


def test_model_mismatch_can_be_injected():
    """Dropping the calibration falls back to the pure power law, which is how
    a controller that is wrong about its plant gets tested."""
    exact = Feedforward()
    wrong = Feedforward(FeedforwardConfig(calibration=()))
    assert wrong.kelvin_for(66.0) != pytest.approx(exact.kelvin_for(66.0), abs=0.5)
    # Still the right shape, though -- it must not be nonsense.
    assert wrong.kelvin_for(66.0) > wrong.kelvin_for(64.0)


def test_gain_rises_steeply_with_output():
    """~1.6 K/% at 43% against ~10 K/% at 63%: the same trim step means very
    different things at different temperatures."""
    c = SteadyStateCurve()
    assert c.gain_at(43.0) < 3.0
    assert c.gain_at(63.076) > 8.0
    assert math.isclose(c.gain_at(0.0), 0.0)
