"""The legacy-log converter, exercised on hand-built logs rather than files.

Everything here is about the three joins that can silently produce a plausible
CSV full of wrong numbers: the heater reconstruction, the 336 time merge, and
the model sniff.  None of them need a real ``.xls``, so none of these tests
need ``xlrd`` or the reference logs -- which also keeps them independent of the
working directory.
"""

from __future__ import annotations

import csv
import datetime as _dt

from lschart.tools.import_xls import ChartLog, LogNote
from lschart.tools.xls_to_csv import (
    build_336_index,
    convert,
    heater_timeline,
)

START = _dt.datetime(2026, 7, 15, 12, 0, 0)


def _log218(name, started, t_s, sample, notes=()):
    return ChartLog(
        path=name, model="218", serial="X", started=started, t_s=list(t_s),
        channels={f"Input {i}": ([None] * len(t_s) if i != 1 else list(sample))
                  for i in range(1, 9)},
        notes=[LogNote(t, txt) for t, txt in notes],
    )


def _log336(name, started, t_s, shield):
    ch = {n: [None] * len(t_s) for n in
          ("RAD SHIELD", "THE CHONKE", "1st Stage", "2nd Stage")}
    ch["RAD SHIELD"] = list(shield)
    ch["Setpoint 1"] = [77.0] * len(t_s)
    ch["Heater 1"] = [12.5] * len(t_s)
    return ChartLog(path=name, model="336", serial="Y", started=started,
                    t_s=list(t_s), channels=ch, notes=[])


def _analog(pct):
    return f"Command sent: ANALOG 1, 0, 2, 1, 1,1,1,{pct}"


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _run(tmp_path, logs, monkeypatch, **kw):
    monkeypatch.setattr("lschart.tools.xls_to_csv.load_dir", lambda _p: logs)
    return convert("ignored", str(tmp_path),
                   channel_map={1: "Sample"}, **kw)


def test_heater_is_held_across_files_and_blank_before_the_first_command(
        tmp_path, monkeypatch):
    """A log that contains no ANALOG command still knows the heater setting,
    because the previous log commanded one and the heater does not reset."""
    first = _log218("a.xls", START, [0.0, 10.0, 20.0], [100.0, 101.0, 102.0],
                    notes=[(10.0, _analog(63.076))])
    second = _log218("b.xls", START + _dt.timedelta(hours=1),
                     [0.0, 10.0], [103.0, 104.0])
    _run(tmp_path, [first, second], monkeypatch)

    a = _rows(tmp_path / "a.csv")
    assert a[0]["ls218.aout1"] == ""          # before any command: not guessed
    assert a[1]["ls218.aout1"] == "63.0760"   # the command lands on its own row
    assert a[2]["ls218.aout1"] == "63.0760"

    b = _rows(tmp_path / "b.csv")
    assert [r["ls218.aout1"] for r in b] == ["63.0760", "63.0760"]


def test_the_note_text_survives_onto_its_row(tmp_path, monkeypatch):
    log = _log218("a.xls", START, [0.0, 10.0], [100.0, 101.0],
                  notes=[(10.0, _analog(63.076))])
    _run(tmp_path, [log], monkeypatch)
    rows = _rows(tmp_path / "a.csv")
    assert rows[0]["Notes"] == ""
    assert "ANALOG" in rows[1]["Notes"]


def test_336_merges_by_wall_clock_not_by_row(tmp_path, monkeypatch):
    """The two boxes were logged by different programs: same instant, different
    row index, different cadence."""
    t218 = _log218("a.xls", START, [0.0, 10.0, 20.0], [100.0, 101.0, 102.0])
    # starts 4 s later and samples every 5 s -- no row lines up exactly
    t336 = _log336("s.xls", START + _dt.timedelta(seconds=4),
                   [0.0, 5.0, 10.0, 15.0], [38.0, 38.1, 38.2, 38.3])
    _run(tmp_path, [t218, t336], monkeypatch)
    rows = _rows(tmp_path / "a.csv")
    # t=0 -> nearest 336 sample is at +4 s; t=10 -> +9 s; t=20 -> +19 s
    assert [r["RAD SHIELD"] for r in rows] == ["38.0000", "38.1000", "38.3000"]
    assert rows[0]["ls336.setpoint1"] == "77.0000"
    assert rows[0]["ls336.heater1"] == "12.5000"


def test_a_336_sample_beyond_the_tolerance_is_blank_not_nearest(
        tmp_path, monkeypatch):
    """The failure this guards is a non-overlapping 336 log being smeared
    across a whole file as if it were data."""
    t218 = _log218("a.xls", START, [0.0, 10.0], [100.0, 101.0])
    t336 = _log336("s.xls", START + _dt.timedelta(hours=6), [0.0], [38.0])
    _run(tmp_path, [t218, t336], monkeypatch, tolerance=30.0)
    rows = _rows(tmp_path / "a.csv")
    assert [r["RAD SHIELD"] for r in rows] == ["", ""]


def test_columns_with_no_legacy_equivalent_stay_blank(tmp_path, monkeypatch):
    """The legacy 336 log has no RANGE and no OUTMODE.  Blank says 'never
    asked'; a zero would say 'the heater was off', which is a different claim."""
    t218 = _log218("a.xls", START, [0.0], [100.0])
    t336 = _log336("s.xls", START, [0.0], [38.0])
    _run(tmp_path, [t218, t336], monkeypatch)
    row = _rows(tmp_path / "a.csv")[0]
    assert row["ls336.range1"] == ""
    assert row["ls336.outmode1"] == ""
    assert row["ls336.ramping1"] == ""


def test_the_model_is_sniffed_not_taken_from_the_filename(tmp_path, monkeypatch):
    """``cd10_..._st2_monitor3.xls`` is a 218 log.  A converter that trusted the
    name would index it as the 336 and emit a file with no Sample column."""
    mislabelled = _log218("st2_monitor3.xls", START, [0.0], [100.0])
    written = _run(tmp_path, [mislabelled], monkeypatch)
    assert len(written) == 1
    assert _rows(tmp_path / "st2_monitor3.csv")[0]["Sample"] == "100.0000"


def test_a_336_only_set_produces_no_output(tmp_path, monkeypatch):
    """Nothing to anchor a row on: the 218 is what carries the time base."""
    assert _run(tmp_path, [_log336("s.xls", START, [0.0], [38.0])],
                monkeypatch) == []


def test_index_and_timeline_order_by_absolute_time(tmp_path):
    """Files are handed over in whatever order the glob produced; both joins
    depend on absolute ordering, so they must sort rather than assume."""
    late = _log218("b.xls", START + _dt.timedelta(hours=2), [0.0], [100.0],
                   notes=[(0.0, _analog(70.0))])
    early = _log218("a.xls", START, [0.0], [100.0],
                    notes=[(0.0, _analog(60.0))])
    assert [pct for _, pct in heater_timeline([late, early])] == [60.0, 70.0]

    s_late = _log336("d.xls", START + _dt.timedelta(hours=2), [0.0], [39.0])
    s_early = _log336("c.xls", START, [0.0], [38.0])
    times, recs = build_336_index([s_late, s_early])
    assert times == sorted(times)
    assert [r["RAD SHIELD"] for r in recs] == [38.0, 39.0]


def test_header_matches_the_requested_columns(tmp_path, monkeypatch):
    log = _log218("a.xls", START, [0.0], [100.0])
    _run(tmp_path, [log], monkeypatch,
         channels=["Sample"], aux=["ls218.aout1"])
    with open(tmp_path / "a.csv", newline="", encoding="utf-8") as fh:
        assert next(csv.reader(fh)) == [
            "Timestamp", "Time", "Sample", "ls218.aout1",
            "Validity", "State", "Notes"]


def test_time_column_restarts_at_zero_per_file(tmp_path, monkeypatch):
    """The recorder's ``Time`` is relative to the file; ``Timestamp`` is what
    stitches files together."""
    log = _log218("a.xls", START, [1000.0, 1010.0], [100.0, 101.0])
    _run(tmp_path, [log], monkeypatch)
    rows = _rows(tmp_path / "a.csv")
    assert [r["Time"] for r in rows] == ["0.000", "10.000"]
    assert rows[0]["Timestamp"].startswith("2026-07-15T12:16:40")
