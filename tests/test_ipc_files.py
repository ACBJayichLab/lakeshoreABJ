"""The file interface MATLAB and the viewer use: status out, commands in.

Everything here is about the *contract*, because that contract is the only
thing holding a MATLAB script and a Python recorder together.  Nothing checks
it at run time, so it gets checked here.
"""

from __future__ import annotations

import json
import math
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
