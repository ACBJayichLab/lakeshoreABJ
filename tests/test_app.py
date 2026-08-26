"""Wiring: an `instruments:` list becomes drivers, columns and a poll budget."""

import pytest

from lschart.app import Application
from lschart.config import AppConfig, ConfigError, LS33xConfig, LS218Config
from lschart.instruments.ls33x import LS33x


def cfg_with(*instruments, **kw):
    cfg = AppConfig(instruments=list(instruments), **kw)
    cfg.recorder.enabled = False
    return cfg


def test_a_335_only_rig_needs_no_218():
    """The coworker's case: one box, no analog output, no software loop."""
    app = Application(cfg_with(LS33xConfig(model="335", name="ls335")))
    assert len(app.instruments) == 1
    assert app.ls218 is None
    assert app.supervisor is None


def test_two_boxes_of_the_same_model_coexist():
    """`instruments:` is a list precisely so this is expressible."""
    cfg = cfg_with(
        LS33xConfig(model="335", name="cryostat"),
        LS33xConfig(model="335", name="magnet"),
    )
    app = Application(cfg)
    assert sorted(app.by_name) == ["cryostat", "magnet"]
    assert all(isinstance(i, LS33x) for i in app.instruments)


def test_two_boxes_sharing_a_name_are_rejected():
    """Names label log columns; two of them would collide silently."""
    cfg = cfg_with(
        LS33xConfig(model="335", name="same"),
        LS33xConfig(model="336", name="same"),
    )
    with pytest.raises(ConfigError, match="both named"):
        cfg.validate()


def test_each_box_contributes_its_own_columns():
    cfg = cfg_with(
        LS33xConfig(model="335", name="a"),
        LS33xConfig(model="336", name="b"),
    )
    app = Application(cfg)
    cols = app._aux_columns()
    assert "a.setpoint1" in cols and "b.setpoint4" in cols
    assert "a.setpoint3" not in cols, "a 335 has only two loops"


def test_heater_pct_is_absent_without_a_software_loop():
    """An always-empty column in a months-long CSV is a question, not data."""
    app = Application(cfg_with(LS33xConfig(model="335", name="ls335")))
    assert "heater_pct" not in app._aux_columns()


def test_heater_pct_appears_when_a_controller_is_wired():
    class FakeController:
        def step(self, *a, **kw): ...
        def shutdown(self): ...

    app = Application(
        cfg_with(LS218Config()),
        controller_factory=lambda app: FakeController(),
    )
    assert "heater_pct" in app._aux_columns()


def test_disabled_instruments_are_not_built():
    cfg = cfg_with(
        LS33xConfig(model="335", name="on"),
        LS33xConfig(model="336", name="off", enabled=False),
    )
    app = Application(cfg)
    assert sorted(app.by_name) == ["on"]


def test_channel_columns_come_from_config_not_from_a_frame():
    """The CSV header is written before the first read completes."""
    cfg = cfg_with(LS218Config(channels={1: "Sample", 2: "Shield"}))
    app = Application(cfg)
    assert app._channel_columns() == ["Sample", "Shield"]


def test_the_poll_budget_matches_the_instruments_that_exist():
    one = cfg_with(LS33xConfig(model="335", name="a"))
    two = cfg_with(
        LS33xConfig(model="335", name="a"),
        LS33xConfig(model="335", name="b"),
    )
    assert two.estimated_transactions() == 2 * one.estimated_transactions()


def test_a_sim_rig_costs_no_bus_time():
    """Pacing exists to be kind to a GPIB board, not to an in-process fake."""
    cfg = cfg_with(LS33xConfig(model="335", name="a"))
    assert cfg.estimated_cycle_s() == 0.0


def test_a_control_input_is_not_required():
    """A recorder-only cryostat has no control channel and must not need one."""
    cfg = cfg_with(LS33xConfig(model="335", name="ls335"))
    cfg.validate()
    assert cfg.control_instrument is None
    with pytest.raises(ConfigError, match="no instrument declares"):
        cfg.control_channel


# -- driver selection -------------------------------------------------------

def test_the_lakeshore_driver_needs_a_port_or_a_serial_number():
    cfg = cfg_with(LS33xConfig(model="335", name="a", driver="lakeshore"))
    with pytest.raises(ConfigError, match="names no com_port"):
        cfg.validate()


def test_a_com_port_and_an_ip_address_together_are_rejected():
    inst = LS33xConfig(model="335", name="a", driver="lakeshore")
    inst.transport.com_port = "COM10"
    inst.transport.ip_address = "192.168.0.5"
    with pytest.raises(ConfigError, match="pick one"):
        cfg_with(inst).validate()


def test_the_lakeshore_driver_refuses_a_model_it_has_no_class_for():
    """The 218 is not in the vendor package at all -- it must say so."""
    inst = LS218Config(driver="lakeshore")
    inst.transport.com_port = "COM10"
    with pytest.raises(ConfigError, match="use driver: visa"):
        cfg_with(inst).validate()


def test_a_lakeshore_config_validates_without_the_package_installed():
    """`check` must work on a laptop with no serial port and no vendor driver."""
    inst = LS33xConfig(model="335", name="a", driver="lakeshore")
    inst.transport.com_port = "COM10"
    cfg = cfg_with(inst)
    cfg.validate()
    assert cfg.uses_hardware
