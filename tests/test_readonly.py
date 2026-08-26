"""The read-only interlock.

Two independent guards stop this program changing instrument state:

``allow_writes``           driver policy -- a caller can flip it.
``transport.read_only``    a hard interlock at the layer where bytes leave.

The second exists because the first is policy: a bug anywhere above the
transport still cannot write through the interlock.  These tests are the
evidence for that claim, so they are deliberately literal about *what reached
the instrument* rather than about which exception came back.
"""

import pytest

from lschart.app import Application, build_transport
from lschart.config import AppConfig, LS33xConfig
from lschart.instruments.ls33x import LS33x
from lschart.instruments.sim import Sim33x, SimulatedCryostat
from lschart.transport import LoopbackTransport


def cryostat(read_only=False, allow_writes=False, model="336"):
    sim = Sim33x(SimulatedCryostat(), model=model)
    inst = LS33x(
        LoopbackTransport(sim, read_only=read_only),
        model=model,
        allow_writes=allow_writes,
    )
    return inst, sim


# -- the interlock itself ---------------------------------------------------

def test_reads_are_unaffected():
    """A read-only link is still a working link."""
    inst, _ = cryostat(read_only=True)
    readings, aux = inst.read_frame()
    assert len(readings) == 4
    assert aux["ls336.setpoint2"] == pytest.approx(290.6)


def test_the_interlock_beats_allow_writes():
    """The point of having two: policy defeated, interlock holds."""
    inst, sim = cryostat(read_only=True, allow_writes=True)
    with pytest.raises(PermissionError, match="READ-ONLY"):
        inst.set_setpoint(1, 200.0)
    assert sim.write_log == [], "nothing reached the instrument"


def test_every_write_path_is_blocked():
    inst, sim = cryostat(read_only=True, allow_writes=True)
    for call in (
        lambda: inst.set_setpoint(1, 200.0),
        lambda: inst.set_heater_range(1, 3),
        lambda: inst.set_pid(1, 10, 10, 0),
        lambda: inst.set_ramp(1, 1.0),
        lambda: inst.all_heaters_off(),
    ):
        with pytest.raises(PermissionError):
            call()
    assert sim.write_log == []


def test_the_refusal_names_the_command_it_refused():
    """"Something was blocked" is not a useful thing to read in a log."""
    inst, _ = cryostat(read_only=True, allow_writes=True)
    with pytest.raises(PermissionError, match=r"SETP 1,200\.0000"):
        inst.set_setpoint(1, 200.0)


def test_the_interlock_refuses_before_touching_the_link():
    """It must not depend on the link being up, or on any I/O succeeding."""
    class Exploding:
        def handle_write(self, cmd):
            raise AssertionError("the interlock let a write through")

        def handle_query(self, cmd):
            raise AssertionError("no query expected")

    t = LoopbackTransport(Exploding(), read_only=True)
    with pytest.raises(PermissionError):
        t.write("SETP 1,200")


# -- wiring: the bug this file exists for -----------------------------------

def test_the_sim_driver_honours_read_only():
    """Regression.

    `read_only` was originally wired into the visa and lakeshore branches of
    build_transport but not the sim one, so rehearsing a read-only config
    against the simulator silently wrote.  That is worse than no interlock:
    rehearsal is exactly how someone convinces themselves a config is safe
    before pointing it at a cryostat.
    """
    cfg = LS33xConfig(model="336", name="ls336", driver="sim", allow_writes=True)
    cfg.transport.read_only = True
    inst = Application(
        AppConfig(instruments=[cfg], recorder=_no_recorder())
    ).by_name["ls336"]
    assert inst.transport.read_only is True
    with pytest.raises(PermissionError, match="READ-ONLY"):
        inst.set_setpoint(1, 200.0)


@pytest.mark.parametrize("driver", ["sim", "visa", "lakeshore"])
def test_every_driver_carries_read_only_through(driver):
    """No branch of build_transport may quietly drop the interlock."""
    cfg = LS33xConfig(model="336", name="x", driver=driver)
    cfg.transport.read_only = True
    cfg.transport.resource = "GPIB0::12::INSTR"
    cfg.transport.com_port = "COM10"
    device = Sim33x(SimulatedCryostat(), model="336") if driver == "sim" else None
    # Constructing touches no hardware: opening is lazy, by design.
    t = build_transport(cfg, device=device)
    assert t.read_only is True


def _no_recorder():
    from lschart.config import RecorderConfig

    return RecorderConfig(enabled=False)


# -- probe forces it on regardless of the config ----------------------------

def test_probe_forces_read_only_even_when_the_config_allows_writes(tmp_path, capsys):
    """`probe` is the first thing run against unfamiliar hardware, so its
    safety must not depend on the config file being right.

    Here the config is as permissive as it can be -- writes allowed, interlock
    off -- and probe must still transmit nothing that could change state.
    """
    from lschart import __main__ as cli
    from lschart.app import Application

    cfg_path = tmp_path / "permissive.yaml"
    cfg_path.write_text(
        "instruments:\n"
        "  - name: ls336\n"
        '    model: "336"\n'
        "    driver: sim\n"
        "    allow_writes: true\n"
        "    transport:\n"
        "      read_only: false\n"
        "recorder:\n"
        "  enabled: false\n"
    )

    built = {}

    def spy(cfg, **kw):
        app = Application(cfg, **kw)
        built["app"] = app
        return app

    original, cli.BUILDER = cli.BUILDER, spy
    try:
        class Args:
            config = str(cfg_path)
            log_level = "CRITICAL"

        assert cli.cmd_probe(Args()) == 0
    finally:
        cli.BUILDER = original

    inst = built["app"].by_name["ls336"]
    assert inst.transport.read_only is True, "probe must override the config"
    assert inst.transport.device.write_log == [], "nothing was transmitted"
    out = capsys.readouterr().out
    assert "read-only" in out and "Nothing was written" in out
