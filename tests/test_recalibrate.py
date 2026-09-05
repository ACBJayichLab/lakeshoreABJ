"""Re-reading a log through a different calibration curve.

Every test here is about a way the remap could produce a number that *looks*
like a temperature and is not one: an extrapolation off the end of a table, a
railed reading inverted as though it carried information, a log-ohms table
composed with an ohms table as though the units matched.

The two vendored Coldplate curves are used directly rather than mocked.  They
are the reason this module exists, the correction between them is quoted in
`docs/ltspm3/cryostat.md`, and a test that pins it is what stops the docs and
the curve files drifting apart.
"""

from __future__ import annotations

import csv
import gzip
from pathlib import Path

import pytest

from lschart.tools.recalibrate import Curve, Remapper, main, remap_file, report

CURVES = Path(__file__).resolve().parents[1] / "reference" / "sensor-curves"
WRONG = CURVES / "X186276.340"          # what was loaded in the 218
RIGHT = CURVES / "X186279.340"          # the Coldplate's own curve

HEAD = "Timestamp,Time,Sample,Coldplate,ls218.aout1,Validity,State,Notes"


def _log(path, coldplate_values, head=HEAD):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(head + "\n")
        for i, value in enumerate(coldplate_values):
            fh.write(f"2026-07-15T20:5{i}:16.000,{i}.0,300.0000,{value},"
                     f"0.0000,,,note {i}\n")
    return str(path)


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# -- reading a .340 -------------------------------------------------------

def test_the_header_and_the_breakpoints_both_come_off_the_file():
    curve = Curve.read(str(WRONG))
    assert curve.serial == "X186276"
    assert curve.model == "CX-1050-CU-HT-1.4L"
    assert curve.fmt == 4 and curve.quantity == "ohms"
    assert len(curve.units) == 148          # the header says 148 breakpoints
    assert (curve.t_min, curve.t_max) == (1.196, 330.324)
    assert list(curve.units) == sorted(curve.units)


def test_a_table_that_is_not_invertible_is_refused(tmp_path):
    """Two breakpoints at one temperature means two answers for one reading."""
    bad = tmp_path / "bad.340"
    bad.write_text("Sensor Model:  X\nSerial Number:  S\nData Format:  4\n\n"
                   "  1  2.00000   100.000\n"
                   "  2  2.50000   100.000\n"
                   "  3  3.00000    50.000\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        Curve.read(str(bad))


# -- the two directions ---------------------------------------------------

def test_a_curve_composed_with_itself_is_the_identity():
    """The inverse is the instrument's own interpolation run backwards, so the
    round trip has to be exact, not merely close -- including on the
    breakpoints themselves, where a fencepost error would hide between them."""
    curve = Curve.read(str(WRONG))
    same = Remapper(curve, curve)
    probes = list(curve.temps) + [1.5, 4.2, 77.0, 123.456, 300.0]
    for t in probes:
        if curve.at_rail(t):
            continue
        out, why = same(t)
        assert why == "ok"
        assert out == pytest.approx(t, abs=1e-9)


def test_nothing_is_extrapolated_off_either_end():
    remap = Remapper(Curve.read(str(WRONG)), Curve.read(str(RIGHT)))
    assert remap(400.0) == (None, "off_source")
    assert remap(0.5) == (None, "off_source")


def test_a_railed_reading_is_refused_rather_than_inverted():
    """The 218 clamps at the end breakpoint, so 905 rows of CD10 all read
    330.3200 and the resistance behind them is gone.  Inverting that anyway
    gives 292.6 K -- a number indistinguishable from a plausible room
    temperature, which is exactly what makes it dangerous."""
    remap = Remapper(Curve.read(str(WRONG)), Curve.read(str(RIGHT)))
    assert remap(330.32) == (None, "railed")
    assert remap(1.196) == (None, "railed")
    # ... and a reading just inside the rail is still real data.
    value, why = remap(330.10)
    assert why == "ok" and value == pytest.approx(265.1, abs=30.0)


def test_volts_and_ohms_curves_will_not_compose(tmp_path):
    volts = tmp_path / "v.340"
    volts.write_text("Sensor Model:  DT-670\nSerial Number:  D1\n"
                     "Data Format:  2\n\n"
                     "  1  0.50000   300.000\n"
                     "  2  1.50000    10.000\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        Remapper(Curve.read(str(volts)), Curve.read(str(RIGHT)))


# -- the correction this repository actually applied -----------------------

def test_the_coldplate_read_high_everywhere_and_worse_when_warmer():
    """Pins the claim `docs/ltspm3/cryostat.md` makes.  X186276 is about 12%
    higher in resistance than X186279 at every temperature, so the box read
    high across the whole range and the error grows with T."""
    remap = Remapper(Curve.read(str(WRONG)), Curve.read(str(RIGHT)))
    deltas = {}
    for t, _ohms, out, why in report(remap):
        assert why == "ok"
        deltas[t] = out - t
    assert all(d < 0 for d in deltas.values())
    assert deltas[6] == pytest.approx(-1.068, abs=0.01)
    assert deltas[10] == pytest.approx(-1.890, abs=0.01)
    assert deltas[77] == pytest.approx(-11.674, abs=0.01)
    assert deltas[300] == pytest.approx(-34.896, abs=0.01)
    warmer = sorted(deltas)
    assert all(deltas[a] > deltas[b] for a, b in zip(warmer, warmer[1:]))


# -- rewriting a file -----------------------------------------------------

def test_every_other_column_survives_and_the_decimals_are_kept(tmp_path):
    src = _log(tmp_path / "in.csv", ["8.5250", "330.3200", ""])
    out = tmp_path / "out.csv"
    counts = remap_file(src, str(out), "Coldplate",
                        Remapper(Curve.read(str(WRONG)), Curve.read(str(RIGHT))))
    rows = _read(str(out))
    assert counts == {"rows": 3, "ok": 1, "blank": 1,
                      "railed": 1, "off_source": 0, "off_target": 0}
    assert rows[0]["Coldplate"] == "6.9357"        # four decimals, as it came
    assert rows[1]["Coldplate"] == ""              # railed: no number to give
    assert rows[2]["Coldplate"] == ""
    for row in rows:                               # nothing else moved
        assert row["Sample"] == "300.0000"
        assert row["ls218.aout1"] == "0.0000"
    assert [r["Notes"] for r in rows] == ["note 0", "note 1", "note 2"]


def test_a_missing_column_is_an_error_rather_than_a_file_of_blanks(tmp_path):
    src = _log(tmp_path / "in.csv", ["8.5250"])
    with pytest.raises(SystemExit):
        remap_file(src, str(tmp_path / "out.csv"), "Magnet",
                   Remapper(Curve.read(str(WRONG)), Curve.read(str(RIGHT))))


def test_gzip_is_transparent_on_both_sides(tmp_path):
    """The fit inputs in reference/heater-calibration/ are gzipped, and they
    are the files that most need remapping."""
    src = tmp_path / "in.csv.gz"
    with gzip.open(src, "wt", encoding="utf-8", newline="") as fh:
        fh.write(HEAD + "\n2026-07-15T20:56:16.000,0.0,300.0000,8.5250,"
                 "0.0000,,,\n")
    out = tmp_path / "out.csv.gz"
    remap_file(str(src), str(out), "Coldplate",
               Remapper(Curve.read(str(WRONG)), Curve.read(str(RIGHT))))
    with gzip.open(out, "rt", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["Coldplate"] == "6.9357"


def test_the_cli_refuses_to_write_over_its_input(tmp_path):
    """A remap is a claim about which curve was loaded, and the old log is the
    only record of what the box actually said."""
    src = _log(tmp_path / "in.csv", ["8.5250"])
    code = main([src, "--from", str(WRONG), "--to", str(RIGHT),
                 "--column", "Coldplate", "-o", str(tmp_path)])
    assert code == 1
    assert _read(src)[0]["Coldplate"] == "8.5250"


def test_the_report_alone_writes_nothing(tmp_path):
    src = _log(tmp_path / "in.csv", ["8.5250"])
    out = tmp_path / "elsewhere"
    code = main([src, "--from", str(WRONG), "--to", str(RIGHT), "--report",
                 "--column", "Coldplate", "-o", str(out)])
    assert code == 0
    assert not out.exists()
