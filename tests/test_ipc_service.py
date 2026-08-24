"""Commands arriving by file, and the interlocks they still have to pass.

The point of these tests is that the file door is not a back door.  Every gate
that refuses a command at the CLI refuses it here too, for the same reason and
with the same message -- and the one command that applies power needs a gate of
its own on top.
"""

from __future__ import annotations

import json
import time

import pytest

from lschart.instruments.ls33x import LS33x
from lschart.instruments.sim import Sim33x, SimulatedRig
from lschart.ipc.commands import CommandSpool
from lschart.ipc.service import IpcService
from lschart.ipc.status import read_status
from lschart.model import Frame, Reading
from lschart.transport import LoopbackTransport


def instrument(name="ls336", *, allow_writes=True, read_only=False) -> LS33x:
    rig = SimulatedRig(None, start_k=96.0)
    dev = Sim33x(rig, model="336")
    return LS33x(
        LoopbackTransport(dev, inter_command_delay=0.0, read_only=read_only),
        model="336", name=name, allow_writes=allow_writes,
        channels={"A": f"{name}-A"},
    )


def service(tmp_path, *instruments, **kw) -> IpcService:
    kw.setdefault("accept_commands", True)
    svc = IpcService(
        status_path=tmp_path / "status.json",
        spool=CommandSpool(tmp_path / "commands"),
        instruments=list(instruments) or [instrument()],
        **kw,
    )
    svc.start()
    return svc


def tick(svc: IpcService) -> dict:
    """One poll cycle: drain the spool, write the status file, read it back."""
    svc.on_frame(Frame(t_wall=time.time(), t_mono=time.monotonic(),
                       readings={"Sample": Reading("Sample", 96.0)}))
    return read_status(svc.writer.path)


def ack(status: dict, cid: str) -> dict:
    for entry in status["commands"]["recent"]:
        if entry["id"] == cid:
            return entry
    raise AssertionError(f"no acknowledgement for {cid} in {status['commands']}")


# -- the happy path ----------------------------------------------------------


def test_a_setpoint_written_to_a_file_reaches_the_instrument(tmp_path):
    inst = instrument()
    svc = service(tmp_path, inst)
    cid = svc.spool.submit("setpoint", loop=1, kelvin=77.5)
    status = tick(svc)
    assert ack(status, cid)["ok"]
    assert inst.setpoint(1) == pytest.approx(77.5)
    assert status["commands"]["last_applied_id"] == cid


def test_ping_proves_the_command_path_without_touching_an_instrument(tmp_path):
    """isAlive() says status is being written; this says commands are being read."""
    svc = service(tmp_path)
    cid = svc.spool.submit("ping")
    assert ack(tick(svc), cid)["message"] == "pong"


def test_turning_a_heater_off_never_needs_permission(tmp_path):
    """The safe direction is always available -- see the design rules."""
    inst = instrument()
    svc = service(tmp_path, inst, allow_heater_range=False)
    cid = svc.spool.submit("heaters_off")
    assert ack(tick(svc), cid)["ok"]
    cid = svc.spool.submit("range", output=1, value=0)
    assert ack(tick(svc), cid)["ok"]


# -- the interlocks ----------------------------------------------------------


def test_raising_a_heater_range_is_refused_by_default(tmp_path):
    """Raising the range is the act that applies power, so it is gated again."""
    svc = service(tmp_path, allow_heater_range=False)
    cid = svc.spool.submit("range", output=1, value=3)
    message = ack(tick(svc), cid)["message"]
    assert "applies power" in message and "ipc.allow_heater_range" in message


def test_raising_a_heater_range_works_once_it_is_allowed(tmp_path):
    inst = instrument()
    svc = service(tmp_path, inst, allow_heater_range=True)
    cid = svc.spool.submit("range", output=1, value=2)
    assert ack(tick(svc), cid)["ok"]
    assert inst.heater_range(1) == 2


def test_a_read_only_instrument_refuses_a_file_command_too(tmp_path):
    """`allow_writes` is not bypassed by coming in through a different door."""
    inst = instrument(allow_writes=False)
    svc = service(tmp_path, inst)
    cid = svc.spool.submit("setpoint", loop=1, kelvin=77.0)
    assert "read-only" in ack(tick(svc), cid)["message"]


def test_the_transport_interlock_still_refuses_at_the_byte_level(tmp_path):
    """One layer lower again: no command byte leaves, whatever policy says."""
    inst = instrument(allow_writes=True, read_only=True)
    svc = service(tmp_path, inst)
    cid = svc.spool.submit("setpoint", loop=1, kelvin=77.0)
    assert ack(tick(svc), cid)["ok"] is False


def test_a_recorder_that_does_not_accept_commands_says_so(tmp_path):
    """Silence would be indistinguishable from a spool nobody is reading."""
    svc = service(tmp_path, accept_commands=False)
    cid = svc.spool.submit("setpoint", loop=1, kelvin=77.0)
    status = tick(svc)
    assert "ipc.accept_commands" in ack(status, cid)["message"]
    assert status["commands"]["accepted"] is False


def test_a_stale_command_is_refused_by_the_service(tmp_path):
    svc = service(tmp_path)
    svc.spool.ensure()
    old = time.time() - 3600
    (svc.spool.directory / "0000000000001-0001-old.json").write_text(json.dumps(
        {"id": "old", "kind": "setpoint", "issued_at": old, "loop": 1, "kelvin": 5.0}
    ))
    assert "older than" in ack(tick(svc), "old")["message"]


# -- saying what is wrong ----------------------------------------------------


def test_an_unknown_command_lists_the_known_ones(tmp_path):
    svc = service(tmp_path)
    cid = svc.spool.submit("explode")
    message = ack(tick(svc), cid)["message"]
    assert "unknown command" in message and "setpoint" in message


def test_a_missing_argument_names_itself(tmp_path):
    svc = service(tmp_path)
    cid = svc.spool.submit("setpoint", loop=1)
    assert "kelvin" in ack(tick(svc), cid)["message"]


def test_a_non_numeric_argument_is_refused_not_coerced(tmp_path):
    svc = service(tmp_path)
    cid = svc.spool.submit("setpoint", loop=1, kelvin="warm")
    assert "must be a number" in ack(tick(svc), cid)["message"]


def test_naming_an_instrument_that_is_not_there_lists_the_ones_that_are(tmp_path):
    svc = service(tmp_path, instrument("cryostat"))
    cid = svc.spool.submit("setpoint", instrument="magnet", loop=1, kelvin=77.0)
    message = ack(tick(svc), cid)["message"]
    assert "magnet" in message and "cryostat" in message


def test_with_two_controllers_the_command_must_say_which(tmp_path):
    """Guessing would be the wrong kind of helpful."""
    svc = service(tmp_path, instrument("cryostat"), instrument("magnet"))
    cid = svc.spool.submit("setpoint", loop=1, kelvin=77.0)
    assert "several controllers" in ack(tick(svc), cid)["message"]

    cid = svc.spool.submit("setpoint", instrument="magnet", loop=1, kelvin=77.0)
    assert ack(tick(svc), cid)["ok"]


def test_a_bad_loop_number_is_refused_by_the_driver(tmp_path):
    svc = service(tmp_path)
    cid = svc.spool.submit("setpoint", loop=9, kelvin=77.0)
    assert "no loop 9" in ack(tick(svc), cid)["message"]


# -- keeping the cycle bounded -----------------------------------------------


def test_one_cycle_applies_at_most_max_commands_per_cycle(tmp_path):
    """Each write costs a settle plus a verifying readback, so it is bounded."""
    svc = service(tmp_path, max_commands_per_cycle=2)
    for k in (10.0, 11.0, 12.0, 13.0, 14.0):
        svc.spool.submit("setpoint", loop=1, kelvin=k)
    status = tick(svc)
    assert status["commands"]["applied"] == 2
    assert status["commands"]["queued"] == 3
    tick(svc)
    tick(svc)
    assert svc.spool.pending() == []


def test_the_acknowledgement_ring_is_bounded(tmp_path):
    """A client polling slower than this fills up may miss its own answer."""
    svc = service(tmp_path, ack_history=3)
    for _ in range(6):
        svc.spool.submit("ping")
        tick(svc)
    assert len(read_status(svc.writer.path)["commands"]["recent"]) == 3


# -- the whole application ---------------------------------------------------


def test_a_running_recorder_applies_a_command_and_reports_it(tmp_path):
    """End to end through the real poller, exactly as `run` wires it."""
    from lschart.app import Application
    from lschart.config import AppConfig, IpcConfig, LS33xConfig, RecorderConfig

    cfg = AppConfig(
        instruments=[LS33xConfig(driver="sim", allow_writes=True,
                                 channels={"A": "Coldplate"})],
        recorder=RecorderConfig(enabled=False),
        ipc=IpcConfig(directory=str(tmp_path), accept_commands=True),
    )
    app = Application(cfg)
    app.ipc.start()
    app.poller.step()

    cid = app.ipc.spool.submit("setpoint", loop=2, kelvin=123.25)
    app.poller.step()

    status = read_status(tmp_path / "status.json")
    assert ack(status, cid)["ok"]
    assert status["running"] is True and status["cycle"] == 2
    assert [c["name"] for c in status["channels"]] == ["Coldplate"]
    assert app.by_name["ls336"].setpoint(2) == pytest.approx(123.25)

    app.ipc.stop()
    assert read_status(tmp_path / "status.json")["running"] is False


def test_a_recorder_with_ipc_disabled_builds_no_service(tmp_path):
    from lschart.app import Application
    from lschart.config import AppConfig, IpcConfig, LS33xConfig, RecorderConfig

    cfg = AppConfig(
        instruments=[LS33xConfig(driver="sim")],
        recorder=RecorderConfig(enabled=False),
        ipc=IpcConfig(enabled=False, directory=str(tmp_path)),
    )
    app = Application(cfg)
    assert app.ipc is None
    app.poller.step()
    assert not (tmp_path / "status.json").exists()
