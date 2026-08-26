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

from lschart.gui.source import (
    CsvTail, StatusSource, capabilities, classify_column,
)

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


def test_the_daily_rollover_keeps_the_history_it_already_had(tmp_path):
    """A viewer left open overnight has to notice the recorder moved files --
    without throwing away what it had already plotted of the day before."""
    tail = CsvTail()
    tail.follow(str(log(tmp_path, "day1.csv", rows=3)))
    tail.poll()
    assert tail.follow(str(log(tmp_path, "day2.csv", rows=2))) is True
    assert tail.poll() == 2
    assert tail.series["Sample"].v == [96.0, 97.0, 98.0, 96.0, 97.0]


def test_a_fresh_start_backfills_the_older_logs_in_order(tmp_path):
    """A viewer started mid-day still owes the operator yesterday's cooldown.

    Zooming out must reach every sample the data directory holds, so the
    finished logs that predate today's are read oldest first -- and nothing
    but this run's logs: a different prefix is somebody else's experiment,
    and a later date has not happened yet.
    """
    (tmp_path / "lschart_2026-08-22.csv").write_text(
        HEADER + "".join(row(1_700_000_000.0 + i, 90.0 + i) for i in range(3)))
    (tmp_path / "lschart_2026-08-23.csv").write_text(
        HEADER + "".join(row(1_700_100_000.0 + i, 93.0 + i) for i in range(3)))
    (tmp_path / "lschart_2026-08-23_part2.csv").write_text(
        HEADER + "".join(row(1_700_200_000.0 + i, 94.0 + i) for i in range(2)))
    (tmp_path / "other_2026-08-21.csv").write_text(
        HEADER + "".join(row(1_699_000_000.0 + i, 1.0) for i in range(5)))
    (tmp_path / "lschart_2026-08-25.csv").write_text(
        HEADER + "".join(row(1_700_900_000.0 + i, 2.0) for i in range(5)))

    tail = CsvTail()
    tail.follow(str(log(tmp_path, "lschart_2026-08-24.csv", rows=2)))
    tail.poll()
    # 22nd, then the 23rd, then its part 2 -- each day before today's file.
    assert tail.series["Sample"].v == [90.0, 91.0, 92.0,
                                       93.0, 94.0, 95.0,
                                       94.0, 95.0,
                                       96.0, 97.0]


def test_a_rollover_does_not_re_read_logs_it_already_tailed(tmp_path):
    """Backfill runs once, when there is no history; after that the retained
    history IS the previous days, and re-reading would duplicate them."""
    (tmp_path / "lschart_2026-08-23.csv").write_text(
        HEADER + "".join(row(1_700_000_000.0 + i, 90.0 + i) for i in range(3)))
    tail = CsvTail()
    tail.follow(str(log(tmp_path, "lschart_2026-08-24.csv", rows=3)))
    tail.poll()
    tail.follow(str(log(tmp_path, "lschart_2026-08-25.csv", rows=2,
                        t0=1_700_864_000.0)))
    tail.poll()
    assert tail.series["Sample"].v == [90.0, 91.0, 92.0,
                                       96.0, 97.0, 98.0,
                                       96.0, 97.0]


def test_a_log_with_no_date_in_its_name_has_nothing_to_backfill_from(tmp_path):
    tail = CsvTail()
    tail.follow(str(log(tmp_path, "run.csv", rows=2)))
    tail.poll()   # must not raise, whatever else sits in the directory
    assert tail.series["Sample"].v == [96.0, 97.0]


# -- how much history a fresh start reads ------------------------------------
#
# The whole point of a viewer is what the cryostat is doing *now*; weeks of
# samples nobody asked for are dead weight in memory.  The backfill therefore
# stops once it covers its budget -- and anything older stays reachable,
# because a picked span is answered from disk regardless of what is held.


def test_the_backfill_stops_once_its_coverage_is_met(tmp_path):
    base = _dt.datetime.now().replace(hour=12, minute=0, second=0,
                                      microsecond=0)
    day = _dt.timedelta(days=1)

    def day_log(age_days, name):
        t = (base - age_days * day).timestamp()
        (tmp_path / name).write_text(
            HEADER + "".join(row(t + i, 90.0 + age_days + i) for i in range(3)))
        return t

    day_log(3, f"lschart_{(base - 3 * day).date().isoformat()}.csv")
    day_log(2, f"lschart_{(base - 2 * day).date().isoformat()}.csv")
    t_yesterday = day_log(1, f"lschart_{(base - 1 * day).date().isoformat()}.csv")
    tail = CsvTail(backfill_s=30 * 3600.0)     # covers yesterday, not the day before
    tail.follow(str(log(tmp_path,
                        f"lschart_{base.date().isoformat()}.csv",
                        rows=2, t0=base.timestamp())))
    tail.poll()
    # Two days ago was inside the budget only as a *probe*: reading stopped
    # there, so three days ago never left the disk.
    assert tail.series["Sample"].v == [
        92.0, 93.0, 94.0,          # two days ago
        91.0, 92.0, 93.0,          # yesterday
        96.0, 97.0,                # today
    ]
    assert t_yesterday < base.timestamp()


def test_a_picked_span_fetches_logs_the_backfill_never_read(tmp_path):
    base = _dt.datetime.now().replace(hour=12, minute=0, second=0,
                                      microsecond=0)
    day = _dt.timedelta(days=1)
    t_old = (base - 2 * day).timestamp()
    (tmp_path / f"lschart_{(base - 2 * day).date().isoformat()}.csv").write_text(
        HEADER + "".join(row(t_old + i, 80.0 + i) for i in range(4)))
    (tmp_path / f"lschart_{(base - 1 * day).date().isoformat()}.csv").write_text(
        HEADER + "".join(row((base - day).timestamp() + i, 90.0 + i)
                         for i in range(2)))
    tail = CsvTail(backfill_s=3600.0)      # yesterday satisfies this at once
    tail.follow(str(log(tmp_path,
                        f"lschart_{base.date().isoformat()}.csv",
                        rows=2, t0=base.timestamp())))
    tail.poll()
    assert 80.0 not in tail.series["Sample"].v     # two days ago: never read
    tail.prepare_span(t_old, t_old + 4.0)
    _, vv = tail.between("Sample", t_old, t_old + 4.0)
    assert vv == [80.0, 81.0, 82.0, 83.0]           # fetched from disk, whole


def test_recent_returns_only_the_last_seconds(tmp_path):
    tail = CsvTail()
    t0 = 1_700_000_000.0
    tail.follow(str(log(tmp_path, rows=10, t0=t0)))
    tail.poll()
    t, v = tail.recent("Sample", 3.0)
    assert v == [96.0 + i for i in range(6, 10)]
    assert t == [t0 + i for i in range(6, 10)]
    assert tail.recent("nosuch", 10.0) == ([], [])


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


def test_the_cap_decimates_rather_than_amputates(tmp_path):
    """Past the cap, every other sample goes -- not the oldest ones.

    Dropping the oldest would make zooming out quietly lose whole days; a
    decimated trace keeps a representative line across the whole span.
    """
    tail = CsvTail(max_points=20)
    tail.follow(str(log(tmp_path, rows=30)))
    tail.poll()
    assert len(tail.series["Sample"].t) <= 20
    # The newest sample always survives: it is the one being watched.
    assert tail.series["Sample"].v[-1] == 96.0 + 29
    # And so does the beginning of the log, thinned but present: the first
    # survivor after one halving pass is sample 1.
    assert tail.series["Sample"].v[0] == 96.0 + 1


def test_everything_is_what_the_live_view_draws(tmp_path):
    tail = CsvTail()
    tail.follow(str(log(tmp_path, rows=10)))
    tail.poll()
    assert tail.everything("Sample")[1] == [96.0 + i for i in range(10)]
    assert tail.everything("nosuch") == ([], [])


def test_a_picked_span_is_re_read_at_full_resolution(tmp_path):
    """Thinning is for the overview, not for answering a question.

    Zoom out far enough that the cap decimates the history, then zoom into a
    few seconds of it: the samples there must come back whole, from the log
    on disk rather than from whatever survived.
    """
    t0 = 1_700_000_000.0
    tail = CsvTail(max_points=8)
    tail.follow(str(log(tmp_path, rows=40, t0=t0)))
    tail.poll()
    assert len(tail.series["Sample"].v) <= 8          # genuinely thinned
    assert 96.0 + 9 not in tail.series["Sample"].v    # detail really is gone
    tail.prepare_span(t0 + 10.0, t0 + 14.0)
    # The span plus one sample either side, all at full resolution again.
    tt, vv = tail.between("Sample", t0 + 10.0, t0 + 14.0)
    assert vv == [96.0 + i for i in range(9, 16)]
    assert tt == [t0 + i for i in range(9, 16)]


def test_a_span_can_reach_back_into_a_rolled_over_file(tmp_path):
    """Yesterday's file was consumed by tailing, not forgotten: a span across
    midnight re-reads it too."""
    t1, t2 = 1_700_000_000.0, 1_700_086_400.0
    tail = CsvTail(max_points=4)
    tail.follow(str(log(tmp_path, "lschart_2026-08-23.csv", rows=5, t0=t1)))
    tail.poll()
    tail.follow(str(log(tmp_path, "lschart_2026-08-24.csv", rows=5, t0=t2)))
    tail.poll()
    tail.prepare_span(t1 + 4.0, t2)     # last sample of day 1 to first of day 2
    _, vv = tail.between("Sample", t1 + 4.0, t2)
    assert vv == [96.0 + 3, 96.0 + 4, 96.0, 96.0 + 1]


def test_a_span_wider_than_any_single_file_is_assembled_in_order(tmp_path):
    (tmp_path / "lschart_2026-08-22.csv").write_text(
        HEADER + "".join(row(1_700_000_000.0 + i, 90.0 + i) for i in range(3)))
    (tmp_path / "lschart_2026-08-23.csv").write_text(
        HEADER + "".join(row(1_700_100_000.0 + i, 93.0 + i) for i in range(3)))
    tail = CsvTail()
    tail.follow(str(log(tmp_path, "lschart_2026-08-24.csv", rows=2,
                        t0=1_700_200_000.0)))
    tail.poll()
    tail.prepare_span(1_700_000_000.0, 1_700_200_001.0)
    _, vv = tail.between("Sample", 1_689_000_000.0, 1_701_000_000.0)
    assert vv == [90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 96.0, 97.0]


def test_a_picked_span_that_has_never_been_prepared_falls_back_to_the_overview(tmp_path):
    """The chart draws the thinned history immediately; full resolution
    arrives a tick later."""
    tail = CsvTail()
    t0 = 1_700_000_000.0
    tail.follow(str(log(tmp_path, rows=10, t0=t0)))
    tail.poll()
    tail.prepare_span(t0, t0 + 2)                     # a different span loaded
    _, vv = tail.between("Sample", t0 + 4, t0 + 6)
    assert vv == [96.0 + 3, 96.0 + 4, 96.0 + 5, 96.0 + 6, 96.0 + 7]


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


# -- what controls a box should get -----------------------------------------
#
# The recorder reports what the instrument it actually opened can do, rather
# than every client keeping its own table of what a model number implies --
# which is the same table going stale in three places.


def test_a_33x_gets_loops_and_heater_ranges_and_no_analog_control():
    caps = capabilities({
        "name": "ls336", "model": "336", "writable": True,
        "loops": [1, 2, 3, 4], "heater_outputs": [1, 2],
        "analog_output": None, "max_output_pct": 100.0,
    })
    assert caps["has_loops"] and caps["has_heater_range"]
    assert caps["has_analog"] is False
    assert caps["loops"] == [1, 2, 3, 4] and caps["heater_outputs"] == [1, 2]


def test_a_218_gets_an_analog_control_and_neither_of_the_others():
    """No loop to set a setpoint on, and no range to raise."""
    caps = capabilities({
        "name": "ls218", "model": "218", "writable": True,
        "loops": [], "heater_outputs": [], "analog_output": 1,
        "max_output_pct": 70.0,
    })
    assert caps["has_analog"] and caps["analog_output"] == 1
    assert caps["max_output_pct"] == 70.0
    assert not caps["has_loops"] and not caps["has_heater_range"]


def test_analog_output_zero_is_an_output_number_not_an_absence():
    """`None` means "no analog output"; 0 would be a real one."""
    assert capabilities({"analog_output": 0, "loops": []})["has_analog"] is True
    assert capabilities({"analog_output": None, "loops": []})["has_analog"] is False


def test_a_status_file_from_an_older_recorder_degrades_to_the_old_behaviour():
    """A viewer newer than the recorder it is watching must still be usable.

    Before the capability block existed the viewer assumed loops 1-4 and no
    analog control, so that is what an entry without it should still get --
    rather than a window with no controls at all.
    """
    caps = capabilities({"name": "ls336", "up": True, "writable": True})
    assert caps["loops"] == [1, 2, 3, 4] and caps["heater_outputs"] == [1, 2]
    assert caps["has_analog"] is False


def test_the_two_power_gates_are_reported_separately(tmp_path):
    src = StatusSource(status_file(tmp_path, commands={
        "accepted": True, "recent": [],
        "allow_heater_range": False, "allow_analog_output": True,
    }))
    src.poll()
    assert src.accepts_commands() is True
    assert src.allows_heater_range() is False
    assert src.allows_analog_output() is True


def test_a_gate_absent_from_the_status_file_reads_as_shut(tmp_path):
    src = StatusSource(status_file(tmp_path))
    src.poll()
    assert src.allows_heater_range() is False
    assert src.allows_analog_output() is False


def test_only_writable_instruments_are_offered_as_targets(tmp_path):
    """The LTSPM3 shape: our 218 is writable, their 336 is not."""
    src = StatusSource(status_file(tmp_path, links=[
        {"name": "ls218", "up": True, "writable": True, "analog_output": 1},
        {"name": "ls336", "up": True, "writable": False, "loops": [1, 2]},
    ]))
    src.poll()
    assert [ln["name"] for ln in src.writable_links()] == ["ls218"]
    assert src.link_named("ls336")["writable"] is False
    assert src.link_named("nope") == {}


def test_a_foreign_prefix_is_excluded_even_when_it_sorts_below_this_one(tmp_path):
    """"Different recorder" is a question about the prefix, not about ordering.

    One data directory routinely holds several recorders' logs side by side.
    Deciding which are "earlier" by comparing (prefix, date, part) as one
    ordered tuple accepts every prefix that merely sorts below this one --
    so a viewer following `ltspm3-heater_*.csv` backfilled `ls336_*.csv`,
    including the file another recorder was still writing.  The names below
    are the real ones this was found with.
    """
    t0 = 1_700_000_000.0
    (tmp_path / "ls336_2026-08-24.csv").write_text(
        HEADER + "".join(row(t0 + i, 1.0) for i in range(4)))
    (tmp_path / "ls336_2026-08-26.csv").write_text(
        HEADER + "".join(row(t0 + 100 + i, 2.0) for i in range(4)))
    (tmp_path / "lschart_2026-08-23.csv").write_text(
        HEADER + "".join(row(t0 + 200 + i, 3.0) for i in range(4)))
    (tmp_path / "ltspm3-heater_2026-08-25.csv").write_text(
        HEADER + "".join(row(t0 + 300 + i, 90.0 + i) for i in range(3)))

    mine = tmp_path / "ltspm3-heater_2026-08-26.csv"
    mine.write_text(HEADER + "".join(row(t0 + 400 + i, 96.0 + i) for i in range(2)))

    assert CsvTail._older_logs(str(mine)) == [
        str(tmp_path / "ltspm3-heater_2026-08-25.csv")]

    tail = CsvTail()
    tail.follow(str(mine))
    tail.poll()
    # Yesterday's heater log, then today's.  Nothing from the 336.
    assert tail.series["Sample"].v == [90.0, 91.0, 92.0, 96.0, 97.0]
