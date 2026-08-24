"""The viewer's two data sources.  No Qt here -- that is the point.

The viewer is a separate process reading files another process is writing, so
every awkward case is a normal case: a row caught half-flushed, a file rolled
over at midnight, a status file caught mid-replace.  All three are handled
here rather than being allowed to reach the widgets as exceptions.
"""

from __future__ import annotations

import datetime as _dt
import json
import time

from lschart.gui.source import CsvTail, StatusSource, classify_column

HEADER = "Timestamp,Time,Sample,ls336.setpoint1,ls336.heater1,Validity,State,Notes\n"


def row(t: float, sample: float, setpoint=77.0, heater=12.5) -> str:
    stamp = _dt.datetime.fromtimestamp(t).isoformat(timespec="milliseconds")
    return f"{stamp},0.000,{sample:.4f},{setpoint},{heater},,,\n"


def log(tmp_path, name="lschart_2026-08-24.csv", rows=3, t0=1_700_000_000.0):
    path = tmp_path / name
    path.write_text(HEADER + "".join(row(t0 + i, 96.0 + i) for i in range(rows)))
    return path


# -- which axis a column belongs on ------------------------------------------


def test_a_heater_percent_does_not_go_on_the_kelvin_axis():
    """63% and 63 K are different quantities; one scale invites reading across."""
    channels = {"Sample"}
    assert classify_column("Sample", channels) == "kelvin"
    assert classify_column("ls336.setpoint1", channels) == "kelvin"
    assert classify_column("ls336.heater1", channels) == "percent"
    assert classify_column("heater_pct", channels) == "percent"
    assert classify_column("ls218.aout1", channels) == "percent"


def test_a_heater_range_is_not_plotted_at_all():
    """0..3 is an enumeration; a line through it implies 1.5 means something."""
    assert classify_column("ls336.range1", {"Sample"}) == "other"


# -- tailing the log ---------------------------------------------------------


def test_the_first_read_takes_the_whole_file(tmp_path):
    tail = CsvTail()
    tail.follow(str(log(tmp_path)))
    assert tail.poll() == 3
    assert tail.columns() == ["Sample", "ls336.setpoint1", "ls336.heater1"]
    assert tail.series["Sample"].v == [96.0, 97.0, 98.0]


def test_later_reads_take_only_what_was_appended(tmp_path):
    """A viewer left open all week costs a seek, not a re-parse of 90 MB."""
    path = log(tmp_path, rows=2)
    tail = CsvTail()
    tail.follow(str(path))
    assert tail.poll() == 2
    assert tail.poll() == 0
    with open(path, "a") as fh:
        fh.write(row(1_700_000_002.0, 99.0))
    assert tail.poll() == 1
    assert tail.series["Sample"].v == [96.0, 97.0, 99.0]


def test_half_a_row_is_held_back_until_it_is_complete(tmp_path):
    """The recorder flushes every sample, but a read can still land mid-write.

    Half a row parses into a plausible wrong number, which is worse than a
    missing one -- so a fragment waits for its newline.
    """
    path = log(tmp_path, rows=1)
    tail = CsvTail()
    tail.follow(str(path))
    assert tail.poll() == 1
    with open(path, "a") as fh:
        fh.write("2023-11-14T22:13:21.000,0.000,999.")
    assert tail.poll() == 0
    assert tail.series["Sample"].v == [96.0]
    with open(path, "a") as fh:
        fh.write("5,77.0,12.5,,,\n")
    assert tail.poll() == 1
    assert tail.series["Sample"].v == [96.0, 999.5]


def test_the_daily_rollover_starts_the_new_file_from_the_top(tmp_path):
    """A viewer left open overnight has to notice the recorder moved files."""
    tail = CsvTail()
    tail.follow(str(log(tmp_path, "day1.csv", rows=3)))
    tail.poll()
    assert tail.follow(str(log(tmp_path, "day2.csv", rows=2))) is True
    assert tail.poll() == 2
    assert tail.series["Sample"].v == [96.0, 97.0]


def test_following_the_same_path_again_does_not_reset(tmp_path):
    tail = CsvTail()
    path = str(log(tmp_path))
    tail.follow(path)
    tail.poll()
    assert tail.follow(path) is False
    assert tail.rows == 3


def test_a_file_that_shrank_is_re_read_rather_than_spliced(tmp_path):
    path = log(tmp_path, rows=5)
    tail = CsvTail()
    tail.follow(str(path))
    tail.poll()
    path.write_text(HEADER + row(1_700_000_000.0, 1.0))
    assert tail.poll() == 1
    assert tail.series["Sample"].v == [1.0]


def test_blank_cells_and_text_columns_are_skipped(tmp_path):
    """A channel that missed a cycle leaves an empty cell, not a zero."""
    path = tmp_path / "x.csv"
    path.write_text(
        HEADER
        + "2023-11-14T22:13:20.000,0.000,,77.0,12.5,slew_reject,tracking,note\n"
    )
    tail = CsvTail()
    tail.follow(str(path))
    tail.poll()
    assert "Sample" not in tail.series
    assert "Notes" not in tail.series


def test_a_missing_file_is_not_an_error(tmp_path):
    tail = CsvTail()
    tail.follow(str(tmp_path / "nope.csv"))
    assert tail.poll() == 0


def test_the_oldest_samples_are_dropped_once_the_cap_is_reached(tmp_path):
    tail = CsvTail(max_points=20)
    tail.follow(str(log(tmp_path, rows=30)))
    tail.poll()
    assert len(tail.series["Sample"].t) <= 20
    # The newest sample always survives: it is the one being watched.
    assert tail.series["Sample"].v[-1] == 96.0 + 29


def test_the_time_window_returns_only_the_tail(tmp_path):
    tail = CsvTail()
    tail.follow(str(log(tmp_path, rows=10)))
    tail.poll()
    t, v = tail.window("Sample", 3.0)
    assert v == [96.0 + 6, 96.0 + 7, 96.0 + 8, 96.0 + 9]
    assert tail.window("Sample", None)[1] == [96.0 + i for i in range(10)]
    assert tail.window("nosuch", 10.0) == ([], [])


def test_a_hand_picked_window_returns_only_that_span(tmp_path):
    """What a drag on the chart asks for: two absolute times, not a duration."""
    tail = CsvTail()
    t0 = 1_700_000_000.0
    tail.follow(str(log(tmp_path, rows=10, t0=t0)))
    tail.poll()
    t, v = tail.between("Sample", t0 + 4, t0 + 6)
    # One sample either side of the span, so a trace that crosses the edge is
    # drawn leaving the window instead of stopping short of the axis.
    assert v == [96.0 + 3, 96.0 + 4, 96.0 + 5, 96.0 + 6, 96.0 + 7]
    assert t == [t0 + 3, t0 + 4, t0 + 5, t0 + 6, t0 + 7]


def test_a_window_off_the_end_of_the_log_is_empty_not_wrong(tmp_path):
    """Zoom past the last sample and the answer is nothing, never the tail."""
    tail = CsvTail()
    t0 = 1_700_000_000.0
    tail.follow(str(log(tmp_path, rows=10, t0=t0)))
    tail.poll()
    assert tail.between("Sample", t0 + 500, t0 + 600) == ([], [])
    assert tail.between("Sample", t0 - 600, t0 - 500) == ([], [])
    assert tail.between("nosuch", t0, t0 + 10) == ([], [])


def test_a_window_narrower_than_the_sample_interval_still_draws_the_line(tmp_path):
    """Zoomed in between two samples, the trace crosses the screen anyway."""
    tail = CsvTail()
    t0 = 1_700_000_000.0
    tail.follow(str(log(tmp_path, rows=10, t0=t0)))
    tail.poll()
    assert tail.between("Sample", t0 + 4.2, t0 + 4.8)[1] == [96.0 + 4, 96.0 + 5]


# -- watching status.json ----------------------------------------------------


def status_file(tmp_path, **kw):
    payload = {
        "t_wall": time.time(), "cycle": 1, "running": True, "interval_s": 1.0,
        "channels": [{"name": "Sample", "kelvin": 96.0, "usable": True}],
        "links": [{"name": "ls336", "up": True, "writable": True}],
        "recorder": {"path": "/tmp/x.csv", "rows": 5},
        "commands": {"accepted": True, "recent": []},
    }
    payload.update(kw)
    (tmp_path / "status.json").write_text(json.dumps(payload))
    return str(tmp_path / "status.json")


def test_an_absent_status_file_says_what_to_check(tmp_path):
    src = StatusSource(str(tmp_path / "status.json"))
    src.poll()
    state, message = src.health()
    assert state == "absent"
    assert "ipc.enabled" in message and src.ever_seen is False


def test_a_current_status_file_is_healthy(tmp_path):
    src = StatusSource(status_file(tmp_path))
    src.poll()
    assert src.health()[0] == "ok"
    assert src.accepts_commands() and src.log_path() == "/tmp/x.csv"


def test_a_stale_status_file_is_not_healthy(tmp_path):
    src = StatusSource(status_file(tmp_path, t_wall=time.time() - 600))
    src.poll()
    state, message = src.health()
    assert state == "stale" and "hung" in message


def test_a_recorder_that_stopped_cleanly_says_so_rather_than_looking_hung(tmp_path):
    src = StatusSource(status_file(tmp_path, running=False))
    src.poll()
    assert src.health()[0] == "stopped"


def test_a_read_that_loses_the_race_keeps_the_last_good_status(tmp_path):
    """On Windows a replace can briefly make the file unreadable.

    Blanking every readout because one read was unlucky would make the display
    flicker; the age still advances, so a file that stays unreadable does go
    stale rather than sitting at the age it had when the reads started failing.
    """
    path = status_file(tmp_path)
    src = StatusSource(path)
    src.poll()
    (tmp_path / "status.json").write_text("{ half")
    assert src.poll() is not None
    assert src.health()[0] == "ok"
    assert src.age_s is not None


def test_a_cycle_counter_that_stops_advancing_is_noticed(tmp_path):
    """A clock step can make the age lie; a cycle counter cannot."""
    src = StatusSource(status_file(tmp_path, cycle=7))
    src.poll()
    src.poll()
    src.poll()
    assert src.stalled_polls == 2
    status_file(tmp_path, cycle=8)
    src.poll()
    assert src.stalled_polls == 0


def test_an_acknowledgement_is_found_by_its_own_id(tmp_path):
    """Several clients may be commanding; "a command was applied" is not "yours"."""
    src = StatusSource(status_file(tmp_path, commands={
        "accepted": True,
        "recent": [{"id": "aaa", "ok": True, "message": "one"},
                   {"id": "bbb", "ok": False, "message": "two"}],
    }))
    src.poll()
    assert src.ack_for("bbb")["message"] == "two"
    assert src.ack_for("ccc") is None
