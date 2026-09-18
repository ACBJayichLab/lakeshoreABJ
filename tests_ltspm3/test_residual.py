"""The residual and its band -- what the monitor and the supervisor judge by.

PID phase 1 section 1.2.  ``missing_power_w`` and ``sigma_q_w`` are the two
functions plan 2's monitor and plan 3's supervisor BOTH call, which is what
stops them disagreeing about what typical means.  So what is pinned here is
their behaviour, not their arithmetic: the sign, the zero, the shape in
temperature, the growth in time, and the widening during a sweep.

The measured constants underneath come from ``analysis/band.py`` and are
asserted separately, in ``test_fitted_table.py``.  Neither file can run the
fit -- that needs scipy and the 43 h sweep -- so the seam between the two is
the generated table, and a refit that moves the band announces itself here.
"""

import math

import pytest

from ltspm3.model import fitted_response as M

#: The gauge's own epoch.  Every band figure below is quoted against it, and
#: NOT against ``time.time()``: a test anchored to today passes every morning
#: and fails every evening, which this repository has been bitten by once.
T0 = M.DRIFT_T0_UNIX

#: Six temperatures across the measured range, at the output that holds each.
POINTS = (10.0, 20.0, 30.0, 60.0, 118.0, 180.0)


def holding(kelvin: float) -> float:
    """The output that holds a temperature, by the table's own reckoning."""
    return M.percent_for_power(M.steady_power_w(kelvin))


# -- the residual ----------------------------------------------------------

@pytest.mark.parametrize("kelvin", POINTS)
def test_the_residual_is_zero_on_the_model_s_own_steady_state(kelvin):
    """The definition of the curve, read back through the residual.

    ``q(T)`` is the power that holds ``T``, so at that power, settled, on the
    locus, there is by construction nothing missing.  If this ever fails the
    grid and the residual have come apart, which is the one way this pair can
    be wrong without anything else noticing.
    """
    dq = M.missing_power_w(kelvin, 0.0, M.coldplate_k(kelvin), holding(kelvin))
    assert dq == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("kelvin", POINTS)
def test_less_heater_than_it_takes_reads_as_power_missing(kelvin):
    """**Negative means missing**, which is the direction every fault went.

    The 2026-09-10 event was -4.9 mW: series resistance appeared, less heat
    arrived than ``P(u)`` claimed, the sample cooled.  A sign flip here would
    invert the monitor's verdict on the one archive event it exists to catch.
    """
    short = holding(kelvin) * 0.99
    dq = M.missing_power_w(kelvin, 0.0, M.coldplate_k(kelvin), short)
    assert dq > 0.0, "the sample is warmer than this output holds"
    over = M.missing_power_w(kelvin, 0.0, M.coldplate_k(kelvin),
                             holding(kelvin) * 1.01)
    assert over < 0.0


def test_a_missing_milliwatt_is_a_missing_milliwatt():
    """Scale, not just sign: take 5 mW out of the heater and 5 mW goes missing."""
    kelvin, held = 118.0, holding(118.0)
    less = M.percent_for_power(M.power_w(held) - 5e-3)
    dq = M.missing_power_w(kelvin, 0.0, M.coldplate_k(kelvin), less)
    assert dq == pytest.approx(5e-3, rel=1e-6)


def test_the_heat_capacity_term_carries_a_sweep():
    """At 5 K/min and 118 K the dynamic term is tens of milliwatts.

    This is the half of the residual a kelvin check cannot do: during a ramp
    the tracking error is the ramp's lag by design, and only the watts say
    whether the cryostat is behaving.
    """
    kelvin, rate = 118.0, 5.0 / 60.0
    dq = M.missing_power_w(kelvin, rate, M.coldplate_k(kelvin), holding(kelvin))
    assert dq == pytest.approx(M.heat_capacity_j_per_k(kelvin) * rate, rel=1e-9)
    assert 50e-3 < dq < 100e-3


def test_a_warmer_sink_needs_less_heater():
    """The coldplate is an input, not an assumption.

    Raise the measured coldplate above its locus and the link carries less, so
    the same output is now MORE than it takes -- which is the signature of a
    compressor going unwell, and the reason it must not read as a heater fault.
    """
    kelvin, held = 118.0, holding(118.0)
    locus = M.coldplate_k(kelvin)
    warmer = M.missing_power_w(kelvin, 0.0, locus + 0.5, held)
    assert warmer < 0.0
    # Worth about Lambda'(T_c) per kelvin of sink, which is nine times what a
    # kelvin of SAMPLE is worth -- the asymmetry the band is built around.
    assert abs(warmer) == pytest.approx(
        0.5 * M.lambda_slope_w_per_k(locus + 0.25), rel=0.02)


def test_the_sink_defaults_to_the_locus_the_dwells_measured():
    for kelvin in POINTS:
        assert M.conductance_w(kelvin) == pytest.approx(
            M.conductance_w(kelvin, M.coldplate_k(kelvin)), abs=1e-12)


# -- the band --------------------------------------------------------------

#: ``(T, u, 3sig day 0, 3sig day 10, 3sig sweeping at 5 K/min, bias)``, all mW.
#: **Read off the generated table, not from anybody's memory** -- the same
#: convention as ``test_fitted_response.py``'s eight pinned points, and for the
#: same reason: a refit that moves the band should have to say so in a diff.
#: Regenerate with the table; do not loosen the tolerance.
PINNED_MW = (
    (10.0, 24.2156, 9.6595, 9.7389, 9.6595, 0.6699),
    (30.0, 52.4099, 3.9851, 7.0483, 4.0334, 3.1378),
    (60.0, 59.1548, 1.5381, 7.5642, 3.2298, 3.9974),
    (118.0, 63.9610, 1.4392, 8.7773, 6.6974, 4.6733),
    (180.0, 68.7269, 1.6095, 10.1257, 8.7960, 5.3957),
)


@pytest.mark.parametrize("kelvin,pct,day0,day10,sweep,bias", PINNED_MW)
def test_the_band_is_where_the_export_put_it(kelvin, pct, day0, day10, sweep,
                                             bias):
    assert holding(kelvin) == pytest.approx(pct, abs=1e-3)
    assert 3e3 * M.sigma_q_w(kelvin, pct, T0) == pytest.approx(day0, abs=1e-3)
    assert 3e3 * M.sigma_q_w(kelvin, pct, T0 + 10 * 86400) == pytest.approx(
        day10, abs=1e-3)
    assert 3e3 * M.sigma_q_w(kelvin, pct, T0, 5.0 / 60.0) == pytest.approx(
        sweep, abs=1e-3)
    assert 1e3 * M.bias_q_w(pct) == pytest.approx(bias, abs=1e-3)


def test_three_sigma_settled_is_about_a_kelvin_everywhere():
    """The band arrives at Jeff's 'warn at a kelvin' from the other end.

    Nothing chose this.  The terms are a coldplate residual, a median anchor
    bar, a diurnal amplitude and section 1's own scoreboard, all measured, and
    what they add up to at the local gain is 0.4 to 0.9 K across 10-180 K.
    That agreement is worth a test because losing it means a term has gone
    wrong in a way the range assertions would not catch.
    """
    for kelvin in POINTS:
        band_k = (3.0 * M.sigma_q_w(kelvin, holding(kelvin), T0)
                  / M.lambda_slope_w_per_k(kelvin))
        assert 0.2 < band_k < 1.5, f"{kelvin} K: 3 sigma is {band_k:.2f} K"


def test_the_band_grows_with_the_days_and_never_shrinks():
    """PID_PLAN.md's 'typical drifts' trap, as an assertion.

    Monotone after ``DRIFT_T0`` and CLAMPED before it: a residual computed
    against a window recorded before the gauge is not entitled to a narrower
    band, because the level was no better known then.
    """
    held = holding(118.0)
    was = 0.0
    for day in range(0, 90):
        now = M.sigma_q_w(118.0, held, T0 + day * 86400)
        assert now >= was
        was = now
    assert M.sigma_q_w(118.0, held, T0 - 30 * 86400) == pytest.approx(
        M.sigma_q_w(118.0, held, T0))


def test_the_drift_vanishes_with_the_heater():
    """A fraction of the delivered heat, which is zero with the heater off.

    REFIT_PLAN.md section 7.2: the cold end refuses a constant parasitic watt
    three ways, and the mechanism the measurement implies -- a series
    resistance in the heater circuit -- only matters when current flows.
    """
    far = T0 + 365 * 86400
    assert M.sigma_q_terms(4.8, 0.0, far)["drift"] == 0.0
    assert M.sigma_q_terms(118.0, 64.0, far)["drift"] > 0.0


def test_a_sweep_widens_the_band_and_a_hold_does_not():
    """Section 3: the band must widen during a sweep or every sweep warns."""
    held = holding(118.0)
    settled = M.sigma_q_w(118.0, held, T0)
    sweeping = M.sigma_q_w(118.0, held, T0, 5.0 / 60.0)
    assert sweeping > 4.0 * settled
    assert M.sigma_q_terms(118.0, held, T0)["dynamic"] == 0.0
    # And it does not care which way the ramp goes.
    assert M.sigma_q_w(118.0, held, T0, -5.0 / 60.0) == pytest.approx(sweeping)


def test_the_09_10_fault_is_a_warning_on_the_day_it_was_gauged():
    """Plan 2's replay requires -4.9 mW to warn and not to fault.

    The band is what decides that, so it is pinned here as well as in the
    replay: at 3 sigma the event has to be OUT of band, and it has to stay
    under the fault seed of 8 mW.  This is the single number that says whether
    putting ``DELTA_P_FRAC`` in the band would have been survivable -- it would
    not: the bias alone is 4.7 mW at 118 K, and 3 sigma of it is 14 mW.
    """
    held, event = holding(118.0), -4.9e-3
    assert abs(event) > 3.0 * M.sigma_q_w(118.0, held, T0)
    assert abs(event) < 8e-3
    assert 3.0 * M.bias_q_w(held) > abs(event), "the bias alone would hide it"


def test_the_terms_are_the_band():
    """``sigma_q_terms`` is the same number split up, and the monitor reports it."""
    for kelvin in POINTS:
        held = holding(kelvin)
        terms = M.sigma_q_terms(kelvin, held, T0 + 5 * 86400, 1.0 / 60.0)
        assert set(terms) == {"sink", "thermometry", "diurnal", "model",
                              "drift", "dynamic", "slope_lag"}
        assert math.sqrt(sum(v * v for v in terms.values())) == pytest.approx(
            M.sigma_q_w(kelvin, held, T0 + 5 * 86400, 1.0 / 60.0))


def test_the_sink_is_the_largest_settled_term_at_the_warm_end():
    """And that is the coldplate's Lambda', not the sample's.

    Worth an assertion because it is the counter-intuitive half of the band:
    the sample's own thermometry is 27 uW at 118 K and the SINK is 0.42 mW,
    fifteen times larger, because Lambda' at 6.6 K is nine times Lambda' at
    118 K.  A reader who assumes the sample dominates will size the wrong term.
    """
    terms = M.sigma_q_terms(118.0, holding(118.0), T0)
    assert terms["sink"] > 10.0 * terms["thermometry"]
    assert terms["sink"] > terms["model"]


def test_the_bias_is_not_in_the_band():
    """The design decision, as an assertion rather than as a paragraph.

    If somebody adds ``DELTA_P_FRAC`` to ``sigma_q_terms`` this fails, and the
    docstring it fails into says why that is not a tidy-up.
    """
    held = holding(118.0)
    terms = M.sigma_q_terms(118.0, held, T0)
    bias = M.bias_q_w(held)
    assert bias > 4e-3
    assert all(v < bias for v in terms.values())
    assert M.sigma_q_w(118.0, held, T0) < bias
