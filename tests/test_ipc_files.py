"""The file interface MATLAB and the viewer use: status out, commands in.

Everything here is about the *contract*, because that contract is the only
thing holding a MATLAB script and a Python recorder together.  Nothing checks
it at run time, so it gets checked here.
"""

from __future__ import annotations

import json
import math
import os
import time

import pytest

from lschart.ipc.commands import CommandSpool
from lschart.ipc.status import (
    StatusWriter,
    atomic_write_json,
    read_status,
    status_age_s,
)
from lschart.model import Frame, Reading, Validity


def frame(**kw) -> Frame:
    readings = kw.pop("readings", {"Sample": Reading("Sample", 96.0)})
    return Frame(t_wall=time.time(), t_mono=time.monotonic(),
                 readings=readings, **kw)


# -- status.json -------------------------------------------------------------


def test_a_reader_never_sees_a_half_written_file(tmp_path):
    """The whole file is replaced, so a reader gets one cycle or the other."""
    path = tmp_path / "status.json"
    assert atomic_write_json(path, {"a": 1})
    assert atomic_write_json(path, {"a": 2})
    assert read_status(path) == {"a": 2}
    # And nothing is left lying around from the writes that did land.
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_failed_write_is_reported_not_raised(tmp_path):
    """An IPC convenience must not be able to stop the recording it reports on."""
    # A directory where the file should be: os.replace cannot overwrite it.
    (tmp_path / "status.json").mkdir()
    writer = StatusWriter(tmp_path / "status.json")
    assert writer.write(frame()) is False
    assert writer.failures == 1


def test_a_nan_reading_does_not_make_the_file_unparseable(tmp_path):
    """`json.dumps` emits a bare NaN token, which no strict parser accepts.

    One unusable channel must not cost every other channel its status file --
    and MATLAB's jsondecode would reject the whole thing.
    """
    writer = StatusWriter(tmp_path / "status.json")
    writer.write(frame(readings={
        "Sample": Reading("Sample", math.nan, validity=Validity.COMMS_ERROR),
        "Shield": Reading("Shield", 96.0),
    }))
    text = (tmp_path / "status.json").read_text()
    assert "NaN" not in text
    # parse_constant fires on NaN/Infinity, which is exactly what a strict
    # reader does; this asserts we never write one.
    status = json.loads(text, parse_constant=lambda c: pytest.fail(f"wrote {c}"))
    sample = [c for c in status["channels"] if c["name"] == "Sample"][0]
    assert sample["kelvin"] is None and sample["usable"] is False


def test_channel_names_survive_as_values_not_as_keys(tmp_path):
    """MATLAB mangles object *keys* through makeValidName; values it leaves alone.

    So a channel called "Rad Shield" has to arrive as a value, and every
    element has to carry the same fields or jsondecode returns a cell array of
    dissimilar structs instead of a struct array.
    """
    writer = StatusWriter(tmp_path / "status.json")
    writer.write(frame(readings={
        "Rad Shield": Reading("Rad Shield", 295.0),
        "Stage 2": Reading("Stage 2", 296.0),
    }))
    channels = read_status(tmp_path / "status.json")["channels"]
    assert [c["name"] for c in channels] == ["Rad Shield", "Stage 2"]
    assert len({tuple(sorted(c)) for c in channels}) == 1


def test_age_is_wall_clock_so_another_process_can_compute_it(tmp_path):
    writer = StatusWriter(tmp_path / "status.json")
    writer.write(frame())
    status = read_status(tmp_path / "status.json")
    assert 0.0 <= status_age_s(status) < 1.0
    assert status_age_s(status, now=status["t_wall"] + 42.0) == pytest.approx(42.0)


def test_a_status_without_a_timestamp_has_no_age(tmp_path):
    assert status_age_s({"cycle": 3}) is None


# -- the loop table the status file publishes --------------------------------
#
# Two halves joined in one place: what the instrument read off OUTMODE?, and
# the numbers that move, out of the frame's aux block.  Joining them anywhere
# but here would mean a client reading the setpoint twice and getting two
# answers.


def loops_of(tmp_path, inst, aux=None):
    writer = StatusWriter(tmp_path / "status.json")
    writer.write(frame(aux=aux or {}), instruments=[inst])
    return read_status(tmp_path / "status.json")["links"][0]["loops"]


def sim_336(**kw):
    from lschart.instruments.ls33x import LS33x
    from lschart.instruments.sim import Sim33x, SimulatedCryostat
    from lschart.transport import LoopbackTransport

    sim = Sim33x(SimulatedCryostat(), model="336")
    inst = LS33x(LoopbackTransport(sim), model="336", name="ls336", **kw)
    inst.read_frame()                      # discover labels and bindings
    return inst, sim


def test_loops_are_an_array_of_uniform_objects_not_an_object_per_loop(tmp_path):
    """`jsondecode` runs object *keys* through makeValidName, so {"1": ...}
    arrives as a field called x1.  A name that lives in a value survives, and
    uniform elements are what make jsondecode return a struct array."""
    inst, _ = sim_336()
    loops = loops_of(tmp_path, inst)
    assert isinstance(loops, list) and len(loops) == 4
    assert [lp["loop"] for lp in loops] == [1, 2, 3, 4]
    assert len({tuple(sorted(lp)) for lp in loops}) == 1


def test_a_loop_carries_the_sensor_the_instrument_says_it_reads(tmp_path):
    inst, sim = sim_336()
    sim.outmodes[1] = (1, 3, 0)            # loop 1 reads input C
    inst._loop_cycles = 0                  # force the slow tick
    inst.read_frame()
    assert loops_of(tmp_path, inst)[0]["sensor"] == sim.names["C"]


def test_the_numbers_that_move_come_from_the_frame_not_from_the_driver(tmp_path):
    inst, _ = sim_336()
    loops = loops_of(tmp_path, inst, {
        "ls336.setpoint1": 77.0, "ls336.heater1": 43.25, "ls336.range1": 2,
        "ls336.setpoint3": 290.0, "ls336.aout3": 12.5,
    })
    assert loops[0]["setpoint_k"] == 77.0
    assert loops[0]["output_pct"] == 43.25
    assert loops[0]["range"] == 2
    # A 336's loop 3 has no range and reports AOUT? as its output.
    assert loops[2]["heater_output"] is None
    assert loops[2]["range"] is None
    assert loops[2]["output_pct"] == 12.5


def test_a_loop_the_recorder_could_not_read_is_null_not_zero(tmp_path):
    """0 K is a plausible setpoint and 0 % is a real output.  Absent has to
    look different from both."""
    inst, _ = sim_336()
    loops = loops_of(tmp_path, inst)
    assert loops[0]["setpoint_k"] is None
    assert loops[0]["output_pct"] is None
    assert loops[0]["range"] is None


def test_the_gains_reach_the_status_file_with_the_loop_they_belong_to(tmp_path):
    """So a client never has to reassemble a loop from three aux keys."""
    inst, _ = sim_336(read_pid=True)
    loops = loops_of(tmp_path, inst, {
        "ls336.p2": 60.0, "ls336.i2": 25.0, "ls336.d2": 3.0,
    })
    assert (loops[1]["p"], loops[1]["i"], loops[1]["d"]) == (60.0, 25.0, 3.0)


def test_a_recorder_not_polling_the_gains_says_null_rather_than_zero(tmp_path):
    """0 is a real value for D, so absent has to look different from it."""
    inst, _ = sim_336()
    loops = loops_of(tmp_path, inst)
    assert loops[0]["p"] is None and loops[0]["d"] is None


def test_a_configured_threshold_reaches_the_status_file(tmp_path):
    """Published so the viewer never has to parse config semantics."""
    inst, _ = sim_336(loop_thresholds={1: 0.5})
    loops = loops_of(tmp_path, inst)
    assert loops[0]["threshold_k"] == 0.5
    assert loops[1]["threshold_k"] is None


def test_a_box_with_no_loops_says_so_with_an_empty_array(tmp_path):
    """Absent capabilities are empty, never missing: a client can then tell
    "this box has no loops" from "this recorder is too old to say"."""
    from lschart.instruments.ls218 import LS218
    from lschart.instruments.sim import Sim218, SimulatedCryostat
    from lschart.transport import LoopbackTransport

    inst = LS218(LoopbackTransport(Sim218(SimulatedCryostat())), name="ls218")
    assert loops_of(tmp_path, inst) == []


def test_the_plain_loop_number_list_did_not_go_away_it_moved(tmp_path):
    """Schema 2 gave `loops` to the object array; one key cannot be two
    shapes, so the numbers a client picks a command target from live under
    `loop_numbers`."""
    inst, _ = sim_336()
    writer = StatusWriter(tmp_path / "status.json")
    writer.write(frame(), instruments=[inst])
    link = read_status(tmp_path / "status.json")["links"][0]
    assert link["loop_numbers"] == [1, 2, 3, 4]
    assert link["heater_outputs"] == [1, 2]


# -- the command spool -------------------------------------------------------


def test_a_command_is_invisible_until_it_is_complete(tmp_path):
    """The recorder globs *.json; a client writes *.json.tmp and renames."""
    spool = CommandSpool(tmp_path)
    spool.ensure()
    (tmp_path / "0000000000000-0001-half.json.tmp").write_text('{"kind": "setp')
    assert spool.collect() == []


def test_commands_apply_in_the_order_they_were_issued(tmp_path):
    """Windows resolves the clock to ~15 ms, so the millisecond is not enough.

    A script that queues a setpoint and then a heater range inside one tick
    must not have them applied the other way round.
    """
    spool = CommandSpool(tmp_path)
    for i in range(20):
        spool.submit("setpoint", loop=1, kelvin=float(i))
    assert [c.args["kelvin"] for c in spool.collect()] == [float(i) for i in range(20)]


def test_a_command_older_than_the_ttl_is_refused(tmp_path):
    """A recorder that was down for an hour must not replay an hour of setpoints.

    The last one would even be correct, which is what makes it dangerous: the
    hazard is the traversal, not the destination.
    """
    spool = CommandSpool(tmp_path, ttl_s=30.0)
    spool.submit("setpoint", loop=1, kelvin=77.0)
    (cmd,) = spool.collect()
    assert cmd.staleness(30.0) == ""
    assert "older than" in cmd.staleness(30.0, now=cmd.issued_at + 31.0)


def test_a_command_from_the_future_is_refused_as_clock_skew(tmp_path):
    """Otherwise a client whose clock runs fast queues commands that never expire."""
    spool = CommandSpool(tmp_path, ttl_s=30.0)
    spool.submit("ping")
    (cmd,) = spool.collect()
    assert "clocks" in cmd.staleness(30.0, now=cmd.issued_at - 31.0)


def test_an_undated_command_is_stale_rather_than_fresh(tmp_path):
    """Defaulting `issued_at` to now would defeat expiry for anyone who omits it."""
    spool = CommandSpool(tmp_path)
    spool.ensure()
    (tmp_path / "0000000000001-0001-x.json").write_text('{"id": "x", "kind": "ping"}')
    (cmd,) = spool.collect()
    assert "issued_at" in cmd.error
    assert cmd.staleness(30.0) != ""


def test_a_malformed_command_is_reported_not_silently_dropped(tmp_path):
    """A client whose JSON is wrong needs to be told, not left watching nothing."""
    spool = CommandSpool(tmp_path)
    spool.ensure()
    (tmp_path / "0000000000001-0001-abc.json").write_text("{not json")
    (cmd,) = spool.collect()
    assert cmd.error and cmd.id == "abc"
    assert not (tmp_path / "0000000000001-0001-abc.json").exists()


def test_a_command_is_deleted_before_it_is_acted_on(tmp_path):
    """One lost command beats an infinite loop of a poisonous one."""
    spool = CommandSpool(tmp_path)
    spool.submit("ping")
    spool.collect()
    assert spool.pending() == []


def test_the_debris_of_a_crashed_client_is_swept(tmp_path):
    spool = CommandSpool(tmp_path)
    spool.ensure()
    stale = tmp_path / "0000000000001-0001-x.json.tmp"
    stale.write_text("half")
    import os

    os.utime(stale, (time.time() - 3600, time.time() - 3600))
    fresh = tmp_path / "0000000000002-0002-y.json.tmp"
    fresh.write_text("half")
    assert spool.sweep_temporaries(max_age_s=300) == 1
    assert fresh.exists() and not stale.exists()


# -- a status write that fails ----------------------------------------------
#
# On Windows `os.replace` over a file another process has open can fail with a
# sharing violation.  Nothing can be done about that and nothing needs to be:
# the next cycle rewrites it.  What was missing was any way to *notice* -- a
# write that fails cannot report itself in the file it failed to write, so a
# client saw a gap and nothing else, which looks exactly like a hung recorder.


def failing_writer(tmp_path, monkeypatch):
    """A writer whose `os.replace` fails, the way Windows can make it."""
    from lschart.ipc import status as status_mod

    writer = StatusWriter(tmp_path / "status.json")
    broken = {"on": True}

    real = os.replace

    def maybe(src, dst):
        if broken["on"]:
            raise PermissionError(32, "The process cannot access the file")
        return real(src, dst)

    monkeypatch.setattr(status_mod.os, "replace", maybe)
    return writer, broken


def test_a_failed_write_is_counted_and_says_why(tmp_path, monkeypatch, caplog):
    writer, broken = failing_writer(tmp_path, monkeypatch)
    with caplog.at_level("WARNING"):
        assert writer.write(frame()) is False
    assert writer.failures == 1
    assert "cannot access the file" in writer.last_error
    # The edge is a WARNING: at DEBUG it was invisible at the default level.
    assert any("could not be written" in r.message for r in caplog.records)


def test_a_failing_write_logs_the_edge_and_not_every_cycle(tmp_path, monkeypatch,
                                                           caplog):
    """One line a second for as long as it lasts is how a signal gets buried."""
    writer, broken = failing_writer(tmp_path, monkeypatch)
    with caplog.at_level("WARNING"):
        for _ in range(5):
            writer.write(frame())
    assert writer.failures == 5
    assert sum("could not be written" in r.message for r in caplog.records) == 1


def test_the_recovery_is_logged_once(tmp_path, monkeypatch, caplog):
    writer, broken = failing_writer(tmp_path, monkeypatch)
    writer.write(frame())
    broken["on"] = False
    with caplog.at_level("WARNING"):
        assert writer.write(frame()) is True
        writer.write(frame())
    assert sum("writable again" in r.message for r in caplog.records) == 1


def test_the_last_error_is_a_record_and_not_a_live_flag(tmp_path, monkeypatch):
    """By the time anyone reads the file, the write plainly succeeded. A field
    saying "not failing right now" would restate what they can already see;
    "this failed at 14:02 because X" is the part they cannot."""
    writer, broken = failing_writer(tmp_path, monkeypatch)
    writer.write(frame())
    broken["on"] = False
    writer.write(frame())
    assert "cannot access the file" in writer.last_error
    assert writer.last_failure_t > 0.0


def test_the_next_good_file_carries_the_failures_that_preceded_it(
        tmp_path, monkeypatch):
    """The only place it can surface: the gap plus a counter that jumped."""
    writer, broken = failing_writer(tmp_path, monkeypatch)
    for _ in range(3):
        writer.write(frame())
    broken["on"] = False
    writer.write(frame())

    published = read_status(tmp_path / "status.json")["status_file"]
    assert published["failures"] == 3
    assert published["last_failure_t"] > 0.0
    # The file that recovers carries the diagnosis of what it recovered from.
    # That is the useful ordering: this is the first file a client can read,
    # and a count with no reason would send them to a log they have not got.
    assert "cannot access the file" in published["last_error"]

    # `writes` is one behind, necessarily: the payload is rendered before the
    # write it describes.
    assert published["writes"] == 0
    writer.write(frame())
    assert read_status(tmp_path / "status.json")["status_file"]["writes"] == 1


def test_a_healthy_recorder_publishes_no_failures(tmp_path):
    writer = StatusWriter(tmp_path / "status.json")
    writer.write(frame())
    published = read_status(tmp_path / "status.json")["status_file"]
    assert published["failures"] == 0
    assert published["last_error"] == ""
