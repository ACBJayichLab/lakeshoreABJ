"""The fitting table: the shape the ODE fit loads, and the traps it removes.

Every test here is about something that would otherwise reach a fitter looking
like data -- a hole integrated straight through, a renamed thermometer arriving
from nowhere, a duplicated row from overlapping logs.
"""

from __future__ import annotations

import csv
import datetime as _dt

from lschart.tools.fit_table import build, heater_column, parse_renames

START = _dt.datetime(2026, 8, 8, 12, 0, 0)
HEAD = ("Timestamp,Time,Sample,Coldplate,ls218.aout1,ls336.range1,"
        "Validity,State,Notes")


def _log(path, rows, head=HEAD):
    """``rows`` is (offset_s, sample, coldplate, heater, note)."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(head + "\n")
        for off, sample, cold, heat, note in rows:
            stamp = (START + _dt.timedelta(seconds=off)).isoformat(
                timespec="milliseconds")
            fh.write(f"{stamp},{off}.0,{sample},{cold},{heat},0.0000,,,{note}\n")
    return str(path)


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_a_recording_gap_starts_a_new_segment(tmp_path):
    """CD10 has a 65 h hole and a 187 h hole in it.  A fit that integrates an
    ODE through one of those is integrating over a week nobody was watching,
    and will converge on a number regardless."""
    src = _log(tmp_path / "a.csv",
               [(i * 10, 100.0 + i, 8.0, 63.0, "") for i in range(10)]
               + [(50_000 + i * 10, 120.0, 8.0, 63.0, "") for i in range(10)])
    out = str(tmp_path / "fit.csv")
    info = build([src], out)
    assert info["segments"] == 2
    segs = [int(r["segment"]) for r in _read(out)]
    assert segs[:10] == [0] * 10
    assert segs[10:] == [1] * 10


def test_ordinary_cadence_jitter_does_not_split_a_segment(tmp_path):
    src = _log(tmp_path / "a.csv",
               [(0, 100.0, 8.0, 63.0, ""), (10, 100.1, 8.0, 63.0, ""),
                (22, 100.2, 8.0, 63.0, ""), (32, 100.3, 8.0, 63.0, "")])
    info = build([src], str(tmp_path / "fit.csv"))
    assert info["segments"] == 1


def test_a_renamed_channel_folds_onto_one_column(tmp_path):
    """`Cold Head` became `Coldplate` mid-run, values continuous to 2 mK.
    Left alone that is two half-empty columns and a thermometer that appears
    from nowhere halfway through."""
    old = _log(tmp_path / "a.csv", [(0, 100.0, 8.0, 63.0, "")],
               head=("Timestamp,Time,Sample,Cold Head,ls218.aout1,"
                     "Validity,State,Notes").replace(
                         ",Validity", ",ls336.range1,Validity"))
    new = _log(tmp_path / "b.csv", [(10, 101.0, 8.1, 63.0, "")])
    out = str(tmp_path / "fit.csv")
    info = build([old, new], out, {"Cold Head": "Coldplate"})
    assert info["channels"] == ["Sample", "Coldplate"]
    assert [r["Coldplate"] for r in _read(out)] == ["8.0", "8.1"]
    assert info["coverage"]["Coldplate"] == 1.0


def test_partial_coverage_is_reported_rather_than_hidden(tmp_path):
    """The 336 stopped logging on 07-23, so its columns are blank for most of
    CD10.  That is real and must be visible, not quietly filled."""
    a = _log(tmp_path / "a.csv", [(0, 100.0, 8.0, 63.0, "")])
    b = _log(tmp_path / "b.csv", [(10, 101.0, "", 63.0, "")])
    info = build([a, b], str(tmp_path / "fit.csv"))
    assert info["coverage"]["Sample"] == 1.0
    assert info["coverage"]["Coldplate"] == 0.5


def test_rows_with_no_heater_are_kept_but_rows_with_no_temperature_are_not(
        tmp_path):
    """A blank heater is a fact about the run -- nothing had been commanded
    yet -- and dropping it would make a fit start at the wrong output.  A row
    with no temperature at all carries nothing."""
    src = _log(tmp_path / "a.csv",
               [(0, 100.0, 8.0, "", ""), (10, "", "", 63.0, ""),
                (20, 101.0, 8.0, 63.0, "")])
    out = str(tmp_path / "fit.csv")
    build([src], out)
    rows = _read(out)
    assert len(rows) == 2
    assert rows[0]["u_pct"] == ""
    assert rows[0]["Sample"] == "100.0"


def test_overlapping_logs_do_not_duplicate_a_sample(tmp_path):
    """Files are handed over by glob and a run's parts can overlap; a repeated
    timestamp would weight that instant twice in the fit."""
    a = _log(tmp_path / "a.csv", [(0, 100.0, 8.0, 63.0, ""),
                                  (10, 101.0, 8.0, 63.0, "")])
    b = _log(tmp_path / "b.csv", [(10, 101.0, 8.0, 63.0, ""),
                                  (20, 102.0, 8.0, 63.0, "")])
    out = str(tmp_path / "fit.csv")
    info = build([a, b], out)
    assert info["rows"] == 3
    assert [r["t_s"] for r in _read(out)] == ["0.000", "10.000", "20.000"]


def test_the_note_survives_so_a_step_can_be_traced(tmp_path):
    src = _log(tmp_path / "a.csv",
               [(0, 100.0, 8.0, 63.0, ""),
                (10, 101.0, 8.0, 65.0, "Command sent: ANALOG 1")])
    out = str(tmp_path / "fit.csv")
    build([src], out)
    assert [r["note"] for r in _read(out)] == ["", "Command sent: ANALOG 1"]


def test_a_software_loop_column_wins_over_the_raw_analog_output(tmp_path):
    """`heater_pct` is what a loop commanded and only exists when one ran."""
    both = _log(tmp_path / "a.csv", [(0, 100.0, 8.0, 63.0, "")],
                head=("Timestamp,Time,Sample,Coldplate,heater_pct,"
                      "ls218.aout1,ls336.range1,Validity,State,Notes"))
    assert heater_column([both]) == "heater_pct"
    only_raw = _log(tmp_path / "b.csv", [(0, 100.0, 8.0, 63.0, "")])
    assert heater_column([only_raw]) == "ls218.aout1"


def test_aux_readbacks_are_left_out_of_the_table(tmp_path):
    """A fit uses the heater and the thermometers.  Twenty always-blank
    `ls336.*` columns are how a loader ends up guessing which ones are real."""
    src = _log(tmp_path / "a.csv", [(0, 100.0, 8.0, 63.0, "")])
    out = str(tmp_path / "fit.csv")
    build([src], out)
    with open(out, newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header == ["Timestamp", "t_s", "segment", "Sample", "Coldplate",
                      "u_pct", "note"]


def test_parse_renames_rejects_a_malformed_pair():
    assert parse_renames("A=B,C=D") == {"A": "B", "C": "D"}
    assert parse_renames(None) == {}
    try:
        parse_renames("A")
    except SystemExit:
        return
    raise AssertionError("a rename with no '=' should not be accepted")
