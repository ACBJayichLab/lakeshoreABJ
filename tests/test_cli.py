"""The command line: the surface a coworker actually types at.

Everything here goes through `main()` with a real argv, because the parser is
half the behaviour -- which sub-command owns which flag, what the defaults are,
and what gets turned into a command argument -- and testing the `cmd_*`
functions directly would skip all of it.

The interlocks themselves are tested in `test_readonly.py` and
`test_ipc_service.py`.  What is checked here is that the CLI is the *same*
door: that a refusal arrives as a message and an exit status rather than a
traceback, and that nothing reaches an instrument that should not.
"""

from __future__ import annotations

import json
import pathlib
import time

import pytest

from lschart import __main__ as cli
from lschart.app import Application
from lschart.instruments.sim import Sim33x, SimulatedCryostat
from lschart.ipc import InstanceLock
from lschart.ipc.commands import CommandSpool
from lschart.ipc.service import IpcService
from lschart.model import Frame, Reading

PERMISSIVE = """\
instruments:
  - name: ls336
    model: "336"
    driver: sim
    allow_writes: true
recorder:
  enabled: false
ipc:
  enabled: true
  directory: {ipc}
"""

READ_ONLY = """\
instruments:
  - name: ls336
    model: "336"
    driver: sim
recorder:
  enabled: false
ipc:
  enabled: false
"""


def config(tmp_path, text=PERMISSIVE, **fmt) -> str:
    fmt.setdefault("ipc", str(tmp_path / "ipc"))
    p = tmp_path / "config.yaml"
    p.write_text(text.format(**fmt))
    return str(p)


@pytest.fixture
def built(monkeypatch):
    """Run the CLI against a simulator, and keep the Application it built.

    The CLI closes its transports on the way out, so the only way to ask what
    reached the instrument is to hold on to the object it used.
    """
    made = {}

    def spy(cfg, **kw):
        app = Application(cfg, **kw)
        made["app"] = app
        return app

    monkeypatch.setattr(cli, "BUILDER", spy)
    return made


def device(built, name="ls336"):
    return built["app"].by_name[name].transport.device


class Deaf(Sim33x):
    """Accepts writes and applies none -- an unverifiable write, as measured."""

    def handle_write(self, cmd):
        self.write_log.append(cmd)


# -- check -------------------------------------------------------------------


def test_check_validates_without_opening_anything(tmp_path, built, capsys):
    """`check` is what someone runs before they trust a config, so it must not
    be the thing that first touches the cryostat."""
    assert cli.main(["-c", config(tmp_path), "check"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "transactions" in out
    assert built == {}, "check built an Application"


def test_check_reports_a_bad_config_instead_of_raising(tmp_path, capsys):
    p = tmp_path / "bad.yaml"
    p.write_text("acquisition:\n  intervl_s: 2.5\n")
    assert cli.main(["-c", str(p), "check"]) == 1
    assert "INVALID" in capsys.readouterr().err


def test_check_survives_a_recorder_only_config(tmp_path, capsys):
    """A 335 with no control_input has no control channel, and asking for one
    raises.  Greeting a coworker validating their first config with a traceback
    from `check`, of all commands, is the worst possible moment for it."""
    p = tmp_path / "recorder.yaml"
    p.write_text('instruments:\n  - name: ls335\n    model: "335"\n'
                 "    driver: sim\nrecorder:\n  enabled: false\n")
    assert cli.main(["-c", str(p), "check"]) == 0
    assert "none" in capsys.readouterr().out


def test_check_calls_a_writable_analog_output_a_heater(tmp_path, capsys):
    """The 218's analog output has no inert half.  Anyone reading `check` on a
    config that opened it needs to see that in the word "heater"."""
    p = tmp_path / "heater.yaml"
    p.write_text('instruments:\n  - name: ls218\n    model: "218"\n'
                 "    driver: sim\n    allow_writes: true\n    max_output_pct: 70\n"
                 "recorder:\n  enabled: false\n")
    assert cli.main(["-c", str(p), "check"]) == 0
    out = capsys.readouterr().out
    assert "THIS IS A HEATER" in out
    assert "70%" in out, "the ceiling is the guard; it has to be visible"


def test_check_does_not_call_a_box_writable_when_no_bytes_can_leave(tmp_path, capsys):
    """The half-opened config: `allow_writes` on, `transport.read_only` on.
    Reporting only the first would be a lie in the safe direction, which is
    still a lie -- somebody would go looking for why their setpoint did nothing."""
    p = tmp_path / "half.yaml"
    p.write_text('instruments:\n  - name: ls336\n    model: "336"\n'
                 "    driver: sim\n    allow_writes: true\n"
                 "    transport:\n      read_only: true\n"
                 "recorder:\n  enabled: false\n")
    assert cli.main(["-c", str(p), "check"]) == 0
    assert "no bytes leave" in capsys.readouterr().out


# -- set ---------------------------------------------------------------------


def test_set_with_no_options_reports_and_changes_nothing(tmp_path, built, capsys):
    """Documented behaviour, and the reason `set` is safe to run to look."""
    assert cli.main(["-c", config(tmp_path), "set"]) == 0
    assert device(built).write_log == [], "a bare `set` wrote to the instrument"
    assert "setpoint" in capsys.readouterr().out


def test_set_writes_a_setpoint_and_reads_it_back(tmp_path, built, capsys):
    assert cli.main(["-c", config(tmp_path), "set", "--loop", "1",
                     "--setpoint", "77.35"]) == 0
    assert "77.3500 K" in capsys.readouterr().out
    assert built["app"].by_name["ls336"].setpoint(1) == pytest.approx(77.35, abs=1e-3)


def test_set_applies_the_range_after_everything_else(tmp_path, built):
    """The range is what applies power, so it lands only once the setpoint it
    will chase is already in place.  Ordering here is a safety property, not a
    stylistic one."""
    assert cli.main(["-c", config(tmp_path), "set", "--loop", "1",
                     "--setpoint", "77", "--range", "2"]) == 0
    log = [c for c in device(built).write_log
           if c.startswith(("SETP", "RANGE"))]
    assert [c.split()[0] for c in log] == ["SETP", "RANGE"]


def test_set_never_raises_a_range_it_was_not_asked_to(tmp_path, built):
    assert cli.main(["-c", config(tmp_path), "set", "--setpoint", "200"]) == 0
    assert built["app"].by_name["ls336"].heater_range(1) == 0
    assert not any(c.startswith("RANGE") for c in device(built).write_log)


def test_set_reports_a_refusal_rather_than_a_traceback(tmp_path, built, capsys):
    """A read-only box refuses; the operator gets a line, and exit 1."""
    assert cli.main(["-c", config(tmp_path, READ_ONLY), "set",
                     "--setpoint", "77"]) == 1
    assert "REFUSED" in capsys.readouterr().err
    assert device(built).write_log == [], "a refusal that still transmitted"


def test_set_reports_a_write_that_did_not_take(tmp_path, built, monkeypatch, capsys):
    """Regression.

    `InstrumentError` is a RuntimeError, so it fell through the
    `except (ValueError, OSError)` here and arrived as an unhandled traceback.
    This is the single most important failure on this path -- the instrument is
    not where it was told to be, and the message says not to assume otherwise
    -- so it must not be the one that looks like a crash in the program.
    """
    monkeypatch.setattr(cli, "BUILDER", _deaf_builder(built))
    assert cli.main(["-c", config(tmp_path), "set", "--setpoint", "77"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("FAILED:")
    assert "did not take" in err and "do not assume" in err


def test_set_closes_its_transports_even_when_it_fails(tmp_path, built, monkeypatch):
    """`set` is one-shot and the recorder wants the port back afterwards."""
    monkeypatch.setattr(cli, "BUILDER", _deaf_builder(built))
    assert cli.main(["-c", config(tmp_path), "set", "--setpoint", "77"]) == 1
    assert all(not i.transport.is_up for i in built["app"].instruments)


def _deaf_builder(built):
    def build(cfg, **kw):
        app = Application(cfg, **kw)
        for i in app.instruments:
            i.transport.device = Deaf(SimulatedCryostat(), model="336")
        built["app"] = app
        return app
    return build


# -- status ------------------------------------------------------------------
#
# The exit status is the part scripts depend on, so every case asserts it.


def status_file(tmp_path, **over):
    body = {
        "t_wall": time.time(), "iso": "now", "pid": 1, "host": "here",
        "cycle": 7, "dropped_cycles": 0, "interval_s": 1.0, "running": True,
        "channels": [{"name": "Sample", "kelvin": 96.0, "usable": True}],
        "aux": [], "links": [], "recorder": {}, "commands": {"accepted": True},
    }
    body.update(over)
    p = tmp_path / "status.json"
    p.write_text(json.dumps(body))
    return str(p)


def test_status_exits_nonzero_when_there_is_no_recorder(tmp_path, capsys):
    assert cli.main(["-c", config(tmp_path), "status",
                     "--file", str(tmp_path / "absent.json")]) == 1
    assert "Is the recorder running" in capsys.readouterr().err


def test_status_exits_zero_on_a_live_recorder(tmp_path, capsys):
    assert cli.main(["-c", config(tmp_path), "status",
                     "--file", status_file(tmp_path)]) == 0
    assert "RUNNING" in capsys.readouterr().out


def test_status_exits_nonzero_on_a_stale_file(tmp_path, capsys):
    """A recorder that died leaves its last status behind, so "the file is
    there" cannot be the test.  Three intervals of slack, then it is stale."""
    path = status_file(tmp_path, t_wall=time.time() - 3600)
    assert cli.main(["-c", config(tmp_path), "status", "--file", path]) == 1
    assert "STALE" in capsys.readouterr().out


def test_status_exits_nonzero_when_the_recorder_says_it_stopped(tmp_path, capsys):
    """A clean shutdown rewrites the file, so it is current *and* finished."""
    path = status_file(tmp_path, running=False)
    assert cli.main(["-c", config(tmp_path), "status", "--file", path]) == 1
    assert "STOPPED" in capsys.readouterr().out


def test_status_json_is_the_raw_file(tmp_path, capsys):
    """So a script can read what this program has not thought to print."""
    path = status_file(tmp_path)
    assert cli.main(["-c", config(tmp_path), "status", "--file", path,
                     "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cycle"] == 7


def test_status_falls_back_to_the_configs_own_path(tmp_path, capsys):
    """Without --file it must find the running recorder's file by itself."""
    cfg = config(tmp_path)
    ipc = tmp_path / "ipc"
    ipc.mkdir(exist_ok=True)
    (ipc / "status.json").write_text(pathlib.Path(status_file(tmp_path)).read_text())
    assert cli.main(["-c", cfg, "status"]) == 0
    assert "cycles" in capsys.readouterr().out


# -- send --------------------------------------------------------------------


def test_send_refuses_to_queue_when_no_recorder_is_running(tmp_path, capsys):
    """Otherwise the command sits in the spool until it expires and the
    operator watches nothing happen."""
    assert cli.main(["-c", config(tmp_path), "send", "ping"]) == 1
    err = capsys.readouterr().err
    assert "no recorder is running" in err
    assert "`set`" in err, "refused without saying what to do instead"


def test_send_refuses_to_queue_into_a_stale_recorder(tmp_path, capsys):
    ipc = tmp_path / "ipc"
    ipc.mkdir()
    stale = status_file(tmp_path, t_wall=time.time() - 3600)
    (ipc / "status.json").write_text(pathlib.Path(stale).read_text())
    assert cli.main(["-c", config(tmp_path), "send", "ping"]) == 1
    assert "may never read" in capsys.readouterr().err
    assert CommandSpool(ipc / "commands").pending() == [], "queued anyway"


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    """A running recorder, ticked once per status read.

    `send` writes a file and then watches the status file for its
    acknowledgement, so a test needs something on the other end actually
    consuming the spool.  Driving a real `IpcService` from the read makes this
    the genuine round trip rather than a rehearsal of it.
    """
    from lschart.ipc import status as status_mod
    from lschart.instruments.ls33x import LS33x
    from lschart.transport import LoopbackTransport

    ipc = tmp_path / "ipc"
    inst = LS33x(LoopbackTransport(Sim33x(SimulatedCryostat(), model="336"),
                                   inter_command_delay=0.0),
                 model="336", name="ls336", allow_writes=True)
    svc = IpcService(status_path=ipc / "status.json",
                     spool=CommandSpool(ipc / "commands"),
                     instruments=[inst], accept_commands=True)
    svc.start()

    def tick():
        svc.on_frame(Frame(t_wall=time.time(), t_mono=time.monotonic(),
                           readings={"Sample": Reading("Sample", 96.0)}))

    tick()
    real = status_mod.read_status

    def read_and_tick(path, *a, **kw):
        tick()
        return real(path, *a, **kw)

    monkeypatch.setattr(status_mod, "read_status", read_and_tick)
    svc.instrument = inst
    return svc


def test_send_queues_a_command_and_reports_the_acknowledgement(
        tmp_path, recorder, capsys):
    """The whole point of `send`: the same path MATLAB uses, without MATLAB."""
    assert cli.main(["-c", config(tmp_path), "send", "ping"]) == 0
    out = capsys.readouterr().out
    assert "queued ping" in out and "OK: pong" in out


def test_send_reaches_the_instrument_through_the_running_recorder(
        tmp_path, recorder, capsys):
    assert cli.main(["-c", config(tmp_path), "send",
                     "setpoint", "77.5", "--loop", "1"]) == 0
    assert "OK" in capsys.readouterr().out
    assert recorder.instrument.setpoint(1) == pytest.approx(77.5)


def test_send_exits_nonzero_when_the_recorder_refuses(tmp_path, recorder, capsys):
    """A refusal is not a failure to communicate, and the two must not share an
    exit status with success."""
    assert cli.main(["-c", config(tmp_path), "send", "range", "3"]) == 1
    out = capsys.readouterr().out
    assert "REFUSED" in out and "ipc.allow_heater_range" in out


def test_send_turns_a_sub_command_into_the_right_arguments(tmp_path, recorder):
    """The parser owns this mapping, and getting it wrong would send a
    well-formed command carrying the wrong numbers -- a rate onto the wrong
    loop reads as success everywhere except on the cryostat.
    """
    assert cli.main(["-c", config(tmp_path), "send",
                     "ramp", "2.5", "--loop", "2"]) == 0
    assert recorder.instrument.ramp(2) == (True, pytest.approx(2.5))
    assert recorder.instrument.ramp(1)[0] is False, "landed on the wrong loop"
    assert CommandSpool(tmp_path / "ipc" / "commands").pending() == []


def test_send_requires_a_sub_command(tmp_path):
    """`send` with nothing after it must not be a silent no-op."""
    with pytest.raises(SystemExit):
        cli.main(["-c", config(tmp_path), "send"])


# -- init --------------------------------------------------------------------


def test_init_writes_a_config_that_actually_loads(tmp_path, capsys):
    """A starter file that needs editing before it parses is not a starter."""
    from lschart import config as config_mod

    path = tmp_path / "new.yaml"
    assert cli.main(["init", str(path)]) == 0
    assert "wrote" in capsys.readouterr().out
    config_mod.load(str(path)).validate()


def test_init_will_not_overwrite_without_being_told(tmp_path, capsys):
    """The file it would destroy is the one carrying somebody's heater limits."""
    path = tmp_path / "existing.yaml"
    path.write_text("# mine\n")
    assert cli.main(["init", str(path)]) == 1
    assert "--force" in capsys.readouterr().err
    assert path.read_text() == "# mine\n"


def test_init_overwrites_when_told(tmp_path):
    path = tmp_path / "existing.yaml"
    path.write_text("# mine\n")
    assert cli.main(["init", str(path), "--force"]) == 0
    assert "# mine" not in path.read_text()


# -- run ---------------------------------------------------------------------


def test_run_stands_down_when_another_recorder_holds_the_lock(tmp_path, capsys):
    """A COM port has one holder.  Losing the race has to be quiet and quick --
    exit 2, before anything is opened -- rather than a half-started recorder
    discovering the port is busy."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        'instruments:\n  - name: ls336\n    model: "336"\n    driver: sim\n'
        "recorder:\n  enabled: false\n"
        f"runtime:\n  single_instance: true\n  lock_path: {tmp_path / 'run.lock'}\n")
    with InstanceLock(tmp_path / "run.lock"):
        assert cli.main(["-c", str(cfg_path), "run"]) == 2


# -- the parser itself -------------------------------------------------------


def test_no_sub_command_means_run(tmp_path, monkeypatch):
    """`lschart -c cfg` with nothing else is the documented way to start."""
    seen = []

    def fake_run(args):
        seen.append(args)
        return 0

    monkeypatch.setattr(cli, "cmd_run", fake_run)
    assert cli.main(["-c", config(tmp_path)]) == 0
    (args,) = seen
    assert args.command == "run"
    assert args.arm is False, "a bare invocation must not arm anything"
    assert args.duration is None and args.interval is None


def test_a_heater_range_outside_0_to_3_is_refused_by_the_parser(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["-c", config(tmp_path), "set", "--range", "9"])
