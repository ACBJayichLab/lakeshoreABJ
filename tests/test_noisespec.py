"""The noise-spectrum tool, on noise whose character is known by construction.

The tool exists to answer one question -- *is this noise white enough that a
low-pass filter helps?* -- so the tests feed it noise where the answer is known
in advance and check it says so.  Nothing here reads a reference log, so none of
it depends on the working directory, and the generator is seeded, so none of it
depends on luck.
"""

from __future__ import annotations

import io

import numpy as np
import pytest

from lschart.tools.noisespec import (
    band_rms,
    decimation_test,
    filter_sweep,
    quietest_window,
    report,
    shared_matrix,
    single_pole,
)

DT = 4.0


def _white(n=8192, sigma=1e-2, seed=0):
    return np.random.default_rng(seed).normal(0.0, sigma, n)


def _red(n=8192, sigma=1e-2, tau=400.0, seed=0):
    """Correlated noise: white driven through a pole much slower than dt.

    This is what the cryostat actually produces, and it is the case a
    white-noise model gets wrong.
    """
    w = np.random.default_rng(seed).normal(0.0, sigma, n)
    out = single_pole(w, DT, tau)
    return out / out.std() * sigma


# -- filter_sweep: the headline claim ---------------------------------------


def test_white_noise_matches_the_white_model():
    """On genuinely white input the measured attenuation tracks sqrt(dt/2tau).

    If this drifts, the model column has stopped being a fair comparison and
    every "the model is off by Nx" reading in the reports is wrong.
    """
    rows = [r for r in filter_sweep(_white(), DT) if r[1] is not None]
    assert rows
    for tau, _sd, measured, white in rows:
        assert measured == pytest.approx(white, rel=0.35), tau


def test_correlated_noise_beats_the_white_model_by_a_lot():
    """Red noise filters far worse than white -- and increasingly so with tau.

    This is the whole finding: an rms figure cannot tell you whether filtering
    will help, because these two records have the same rms.
    """
    rows = [r for r in filter_sweep(_red(), DT) if r[1] is not None]
    ratios = {tau: measured / white for tau, _sd, measured, white in rows}
    assert ratios[60.0] > 2.0
    assert ratios[600.0] > ratios[60.0]


def test_a_tau_below_the_cadence_is_refused_rather_than_guessed():
    """A 100 ms filter cannot be evaluated from 4 s samples, and saying so is
    the point -- returning a plausible number here is how the wrong time
    constant gets justified from the wrong data."""
    rows = dict((r[0], r[1]) for r in filter_sweep(_white(), DT))
    assert rows[0.1] is None
    assert rows[1.0] is None
    assert rows[30.0] is not None


# -- the other three blocks --------------------------------------------------


def test_band_rms_puts_white_noise_in_the_fast_band():
    bands = {label: sd for label, _lo, _hi, sd in band_rms(_white(), DT)}
    assert bands["< 20 s"] > bands["600-3600 s"]


def test_band_rms_puts_slow_noise_in_the_slow_bands():
    bands = {label: sd for label, _lo, _hi, sd in band_rms(_red(tau=2000.0), DT)}
    assert bands["600-3600 s"] > bands["< 20 s"]


def test_decimation_finds_broadband_content_and_averaging_removes_it():
    rows = decimation_test(_white(), DT)
    assert len(rows) > 2
    _k, _eff, dec, avg, _extra = rows[-1]
    assert dec > 2 * avg           # decimation keeps it, averaging does not
    assert rows[-1][4] > rows[1][4]  # more octaves folded = more content


def test_decimation_finds_little_to_remove_in_slow_noise():
    rows = decimation_test(_red(tau=2000.0), DT)
    _k, _eff, dec, avg, _extra = rows[-1]
    assert avg > 0.5 * dec


def test_quietest_window_ignores_drift_and_finds_the_floor():
    """A settled hold still drifts, and an undetrended rms reads the drift."""
    n = 8192
    v = _white(n, sigma=1e-3) + np.linspace(0.0, 0.5, n)   # 0.5 K of ramp
    w = quietest_window(v, DT, hours=2.0)
    assert w.std() == pytest.approx(1e-3, rel=0.3)


def test_shared_matrix_separates_a_common_mode_from_independent_channels():
    common = _white(seed=1)
    cols = {
        "a": common + _white(seed=2),
        "b": common + _white(seed=3),
        "c": _white(seed=4),
    }
    names, C = shared_matrix(cols, DT)
    i = {n: k for k, n in enumerate(names)}
    assert C[i["a"], i["b"]] > 0.3
    assert abs(C[i["a"], i["c"]]) < 0.15


# -- the report --------------------------------------------------------------


def test_report_runs_end_to_end_and_names_the_nyquist():
    n = 4096
    t = np.arange(n) * DT
    cols = {"Input 1": 100.0 + _red(n), "Input 2": 8.0 + _white(n)}
    out = io.StringIO()
    report(t, cols, "Input 1", hours=2.0, out=out)
    text = out.getvalue()
    assert "WHERE THE NOISE LIVES" in text
    assert f"{1 / (2 * DT):.3f} Hz" in text
    assert "SHARED BETWEEN CHANNELS" in text


def test_report_refuses_an_unknown_channel_and_says_what_it_has():
    t = np.arange(512) * DT
    with pytest.raises(SystemExit, match="Input 1"):
        report(t, {"Input 1": _white(512)}, "nope", hours=1.0, out=io.StringIO())
