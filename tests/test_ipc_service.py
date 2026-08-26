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

from lschart.instruments.ls218 import LS218
from lschart.instruments.ls33x import LS33x
from lschart.instruments.sim import Sim218, Sim33x, SimulatedCryostat
from lschart.ipc.commands import CommandSpool
from lschart.ipc.service import IpcService
from lschart.ipc.status import read_status
from lschart.model import Frame, Reading
from lschart.transport import LoopbackTransport


def instrument(name="ls336", *, allow_writes=True, read_only=False) -> LS33x:
    cryostat = SimulatedCryostat(None, start_k=96.0)
    dev = Sim33x(cryostat, model="336")
    return LS33x(
        LoopbackTransport(dev, inter_command_delay=0.0, read_only=read_only),
        model="336", name=name, allow_writes=allow_writes,
        channels={"A": f"{name}-A"},
    )


def monitor(name="ls218", *, allow_writes=True, read_only=False) -> LS218:
    """A 218: no loop, one analog output, and that output is a heater."""
    cryostat = SimulatedCryostat(None, start_k=96.0)
    dev = Sim218(cryostat)
    return LS218(
        LoopbackTransport(dev, inter_command_delay=0.0, read_only=read_only),
        name=name, allow_writes=allow_writes, max_output_pct=70.0,
        channels={1: f"{name}-Sample"},
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
    """One cycle: drain the spool, write the status file, read it back."""
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
    # The refusal is only half the claim; the other half is that nothing was
    # transmitted.  Asserting the acknowledgement alone would pass on a driver
    # that wrote first and reported the failure afterwards.
    assert inst.transport.device.write_log == [], "a byte reached the instrument"


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


# -- the instrument's own ramp -----------------------------------------------
#
# `LakeShore.m`'s setRamp is the only client of this, and its help text makes a
# promise the handler is what keeps: "a rate of 0 turns ramping off (it does
# NOT mean 'infinitely fast')".  MATLAB never sends `enable`, so that promise
# rests entirely on the handler deriving it from the rate.


def test_a_ramp_rate_written_to_a_file_reaches_the_instrument(tmp_path):
    inst = instrument()
    svc = service(tmp_path, inst)
    cid = svc.spool.submit("ramp", loop=1, rate_k_per_min=2.5)
    assert ack(tick(svc), cid)["ok"]
    assert inst.ramp(1) == (True, pytest.approx(2.5))


def test_a_zero_rate_turns_ramping_off_rather_than_being_refused(tmp_path):
    """The whole subtlety, exactly as MATLAB sends it: loop and rate, no `enable`.

    The driver refuses a 0 K/min ramp outright -- 0 means "infinitely fast" to
    the instrument -- so if the handler passed the rate straight through, the
    documented way to stop a ramp would come back as an error instead.
    """
    inst = instrument()
    svc = service(tmp_path, inst)
    svc.spool.submit("ramp", loop=1, rate_k_per_min=2.5)
    tick(svc)
    assert inst.ramp(1)[0] is True

    cid = svc.spool.submit("ramp", loop=1, rate_k_per_min=0)
    result = ack(tick(svc), cid)
    assert result["ok"], f"stopping a ramp was refused: {result['message']}"
    assert "OFF" in result["message"]
    assert inst.ramp(1)[0] is False


def test_ramping_can_be_disabled_while_keeping_a_nonzero_rate(tmp_path):
    """An explicit `enable` overrides the rate-derived default, both ways."""
    inst = instrument()
    svc = service(tmp_path, inst)
    cid = svc.spool.submit("ramp", loop=1, rate_k_per_min=2.5, enable=False)
    assert ack(tick(svc), cid)["ok"]
    assert inst.ramp(1)[0] is False


def test_a_ramp_defaults_to_loop_one(tmp_path):
    """`loop` is optional here, unlike on setpoint."""
    inst = instrument()
    svc = service(tmp_path, inst)
    cid = svc.spool.submit("ramp", rate_k_per_min=1.5)
    assert ack(tick(svc), cid)["ok"]
    assert inst.ramp(1) == (True, pytest.approx(1.5))


def test_a_ramp_command_needs_its_rate(tmp_path):
    svc = service(tmp_path)
    cid = svc.spool.submit("ramp", loop=1)
    assert "rate_k_per_min" in ack(tick(svc), cid)["message"]


def test_a_ramp_is_refused_on_a_read_only_instrument(tmp_path):
    """A ramp changes what the loop chases, so it passes the same gate as a
    setpoint -- it is not a read dressed up as a write."""
    inst = instrument(allow_writes=False)
    svc = service(tmp_path, inst)
    cid = svc.spool.submit("ramp", loop=1, rate_k_per_min=2.5)
    assert "read-only" in ack(tick(svc), cid)["message"]
    assert inst.transport.device.write_log == []


def test_a_ramp_needs_no_power_gate_of_its_own(tmp_path):
    """Ramping applies no power: the range is still whatever it was.  Gating it
    behind allow_heater_range would make the safe way to move a setpoint harder
    than the abrupt one."""
    inst = instrument()
    svc = service(tmp_path, inst, allow_heater_range=False)
    cid = svc.spool.submit("ramp", loop=1, rate_k_per_min=2.5)
    assert ack(tick(svc), cid)["ok"]
    assert inst.heater_range(1) == 0


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


# -- the 218's analog output -------------------------------------------------
#
# The sample heater on the LTSPM3 cryostat.  It needs its own gate rather than
# reusing `allow_heater_range` because it is a different command on a different
# box -- and because a cryostat that wants its sample heater driven from a file has
# no business also being able to raise a range on a controller holding
# something else.


def test_driving_the_analog_output_is_refused_by_default(tmp_path):
    """The percentage IS the power here; there is no inert half to it."""
    svc = service(tmp_path, monitor(), allow_analog_output=False)
    cid = svc.spool.submit("analog", percent=40.0)
    message = ack(tick(svc), cid)["message"]
    assert "applies power" in message and "ipc.allow_analog_output" in message


def test_driving_the_analog_output_works_once_it_is_allowed(tmp_path):
    inst = monitor()
    svc = service(tmp_path, inst, allow_analog_output=True)
    cid = svc.spool.submit("analog", percent=40.0)
    assert ack(tick(svc), cid)["ok"]
    assert inst.get_analog_percent() == pytest.approx(40.0, abs=0.02)


def test_commanding_the_analog_output_to_zero_never_needs_permission(tmp_path):
    """The direction that removes heat is never the one that needs another key."""
    inst = monitor()
    inst.set_analog_percent(30.0)
    svc = service(tmp_path, inst, allow_analog_output=False)
    cid = svc.spool.submit("analog", percent=0)
    assert ack(tick(svc), cid)["ok"]
    assert inst.get_analog_percent() == 0.0


def test_the_heater_gate_does_not_open_the_analog_one(tmp_path):
    """Two switches, and neither stands in for the other."""
    svc = service(tmp_path, monitor(), allow_heater_range=True,
                  allow_analog_output=False)
    cid = svc.spool.submit("analog", percent=40.0)
    assert ack(tick(svc), cid)["ok"] is False


def test_the_analog_gate_does_not_open_the_heater_one(tmp_path):
    svc = service(tmp_path, instrument(), allow_analog_output=True,
                  allow_heater_range=False)
    cid = svc.spool.submit("range", output=1, value=2)
    assert ack(tick(svc), cid)["ok"] is False


def test_a_read_only_218_refuses_the_analog_command(tmp_path):
    """`allow_writes` gates this door exactly as it gates the CLI."""
    svc = service(tmp_path, monitor(allow_writes=False), allow_analog_output=True)
    cid = svc.spool.submit("analog", percent=40.0)
    assert "read-only" in ack(tick(svc), cid)["message"]


def test_the_transport_interlock_still_refuses_the_analog_command(tmp_path):
    inst = monitor(allow_writes=True, read_only=True)
    svc = service(tmp_path, inst, allow_analog_output=True)
    cid = svc.spool.submit("analog", percent=40.0)
    assert ack(tick(svc), cid)["ok"] is False
    assert inst.transport.device.write_log == [], "a byte reached the heater"


def test_the_ceiling_refuses_a_fat_finger_from_a_file_too(tmp_path):
    """~10 K/% near the operating point: a decimal point is worth tens of K."""
    inst = monitor()
    before = inst.get_analog_percent()
    svc = service(tmp_path, inst, allow_analog_output=True)
    cid = svc.spool.submit("analog", percent=400.0)
    message = ack(tick(svc), cid)["message"]
    # A guard doing its job, reported as a refusal rather than as a crash.
    assert message.startswith("refused:") and "outside" in message
    assert inst.get_analog_percent() == before


def test_an_analog_command_needs_its_percentage(tmp_path):
    svc = service(tmp_path, monitor(), allow_analog_output=True)
    cid = svc.spool.submit("analog")
    assert "percent" in ack(tick(svc), cid)["message"]


def test_an_analog_command_on_a_rig_with_no_analog_output_says_so(tmp_path):
    """Not "several controllers, name one" -- there is no candidate at all."""
    svc = service(tmp_path, instrument(), allow_analog_output=True)
    cid = svc.spool.submit("analog", percent=10.0)
    assert "analog output" in ack(tick(svc), cid)["message"]


def test_the_two_boxes_do_not_compete_to_answer_a_command(tmp_path):
    """The LTSPM3 shape: one 33x and one 218, neither needing to be named."""
    ctl, mon = instrument(), monitor()
    svc = service(tmp_path, ctl, mon,
                  allow_analog_output=True, allow_heater_range=True)
    cid = svc.spool.submit("analog", percent=12.0)
    assert ack(tick(svc), cid)["ok"]
    cid = svc.spool.submit("setpoint", loop=1, kelvin=77.0)
    assert ack(tick(svc), cid)["ok"]
    assert mon.get_analog_percent() == pytest.approx(12.0, abs=0.02)
    assert ctl.setpoint(1) == pytest.approx(77.0)


def test_the_status_file_reports_both_power_gates(tmp_path):
    svc = service(tmp_path, monitor(), allow_analog_output=True)
    commands = tick(svc)["commands"]
    assert commands["allow_analog_output"] is True
    assert commands["allow_heater_range"] is False


# -- the panic button --------------------------------------------------------


def test_heaters_off_kills_the_analog_output_too(tmp_path):
    """A panic button that leaves one heater running is worse than none."""
    ctl, mon = instrument(), monitor()
    ctl.set_heater_range(1, 3)
    mon.set_analog_percent(40.0)
    svc = service(tmp_path, ctl, mon)

    cid = svc.spool.submit("heaters_off")
    assert ack(tick(svc), cid)["ok"]
    assert ctl.heater_range(1) == 0
    assert mon.get_analog_percent() == 0.0


def test_heaters_off_skips_a_box_it_may_not_write_to(tmp_path):
    """The LTSPM3 shape exactly: our 218 is writable, their 336 is not.

    Failing the whole command because somebody else's controller is read-only
    would leave our own heater running.
    """
    theirs, ours = instrument("ls336", allow_writes=False), monitor()
    ours.set_analog_percent(40.0)
    svc = service(tmp_path, theirs, ours)

    cid = svc.spool.submit("heaters_off")
    message = ack(tick(svc), cid)["message"]
    assert ours.get_analog_percent() == 0.0
    assert "ls336" in message and "read-only" in message


def test_heaters_off_says_so_when_there_is_nothing_it_may_turn_off(tmp_path):
    """Silence here would be read as "done"."""
    svc = service(tmp_path, monitor(allow_writes=False))
    cid = svc.spool.submit("heaters_off")
    result = ack(tick(svc), cid)
    assert result["ok"] is False and "writable" in result["message"]
