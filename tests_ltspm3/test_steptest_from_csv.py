"""`from_csv` has to attribute a temperature rise to the step that CAUSED it.

Every failure pinned here produced a confident-looking number rather than an
error, which is the reason they survived: a gain that is wrong by the ratio of
two step sizes still arrives with R^2 = 0.9999 attached, and goes straight into
`TuningConfig.schedule`.
"""

from __future__ import annotations

import csv
import math

import pytest

from ltspm3.tools.steptest import DEADBAND_PCT, from_csv

TAU = 600.0
DT = 2.0


def _write(path, rows, heater_column="ls218.aout1"):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Time", "Sample", heater_column])
        for t, k, h in rows:
            w.writerow([f"{t:.3f}", f"{k:.4f}", f"{h:.3f}"])
    return str(path)


def _hold(rows, t, pct, start_k, gain_k, n):
    """`n` samples of a first-order approach to ``start_k + gain_k``."""
    for i in range(n):
        rows.append((t, start_k + gain_k * (1.0 - math.exp(-(i * DT) / TAU)), pct))
        t += DT
    return t, rows[-1][1]


def test_gain_is_attributed_to_the_step_that_caused_the_rise(tmp_path):
    """Two different steps, back to back, both K = 5.0 K/%.

    Pairing each hold with the step that *ended* it reported 9.92 K/% for the
    first -- exactly 5.0 K over the 0.5% that came next, instead of over the
    1.0% that actually drove it.
    """
    rows = []
    t = 0.0
    for _ in range(300):
        rows.append((t, 100.0, 10.0))
        t += DT
    t, end = _hold(rows, t, 11.0, 100.0, 5.0, 1500)      # +1.0% -> +5 K
    t, end = _hold(rows, t, 11.5, end, 2.5, 1500)        # +0.5% -> +2.5 K

    points = from_csv(_write(tmp_path / "walk.csv", rows), heater_column="ls218.aout1")

    assert len(points) == 2, "the final hold must be analysed, not dropped"
    for p in points:
        assert p.gain_k_per_pct == pytest.approx(5.0, rel=0.02)
        assert p.tau_s == pytest.approx(TAU, rel=0.05)
    assert "+1.000%" in points[0].note
    assert "+0.500%" in points[1].note


def test_readback_flicker_is_not_a_step(tmp_path):
    """`AOUT?` on the 218 dithers ~0.003% with nothing commanded.

    Without a deadband each wobble is a step, and because the spurious changes
    fall inside the coalescing window they push the start of the real hold past
    the entire thermal transient -- the fit then sees the flat tail and returns
    tau in the tens of thousands of seconds.
    """
    rows = []
    t = 0.0
    for i in range(600):                      # settled, flickering
        rows.append((t, 100.0, 10.0 + (0.003 if i % 2 else 0.0)))
        t += DT
    t, end = _hold(rows, t, 11.0, 100.0, 5.0, 2000)
    for i in range(0, len(rows)):             # flicker through the hold too
        if rows[i][2] >= 11.0 and i % 2:
            rows[i] = (rows[i][0], rows[i][1], rows[i][2] + 0.003)

    points = from_csv(_write(tmp_path / "flicker.csv", rows))

    assert len(points) == 1
    assert points[0].gain_k_per_pct == pytest.approx(5.0, rel=0.03)
    assert points[0].tau_s == pytest.approx(TAU, rel=0.05)
    assert 0.003 < DEADBAND_PCT


def test_a_walk_is_one_step_not_several(tmp_path):
    """The heater is walked up in increments over minutes, then held for hours.

    Treating each increment as its own step attributes the whole rise to the
    last one and reports a gain several times too small.
    """
    rows = []
    t = 0.0
    for _ in range(300):
        rows.append((t, 100.0, 10.0))
        t += DT
    for pct in (10.25, 10.5, 10.75):          # the walk: 3 increments, ~1 min apart
        for _ in range(30):
            rows.append((t, 100.0, pct))
            t += DT
    t, end = _hold(rows, t, 11.0, 100.0, 5.0, 2000)   # +1.0% total -> +5 K

    points = from_csv(_write(tmp_path / "burst.csv", rows))

    assert len(points) == 1
    assert "+1.000%" in points[0].note
    assert points[0].gain_k_per_pct == pytest.approx(5.0, rel=0.03)


def test_a_missing_heater_column_says_so(tmp_path):
    """It used to drop every row and then blame the file for being too short."""
    rows = [(i * DT, 100.0, 10.0) for i in range(100)]
    path = _write(tmp_path / "odd.csv", rows, heater_column="something.else")
    with pytest.raises(ValueError, match="no heater column"):
        from_csv(path)


def test_an_explicit_column_that_is_absent_is_an_error(tmp_path):
    rows = [(i * DT, 100.0, 10.0) for i in range(100)]
    path = _write(tmp_path / "ok.csv", rows)
    with pytest.raises(ValueError, match="no column 'heater_pct'"):
        from_csv(path, heater_column="heater_pct")
