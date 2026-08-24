"""Config loading and the checks that make a bad config fail loudly."""

import os

import pytest

from lschart import config as config_mod
from lschart.config import AppConfig, ConfigError

yaml = pytest.importorskip("yaml")


def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_defaults_are_valid_and_simulated():
    cfg = config_mod.load(None)
    assert not cfg.uses_hardware
    assert cfg.control_channel == "Sample"
    assert not cfg.control.enabled, "control must never default to on"


def test_repo_config_is_valid():
    """The committed starter file must actually load."""
    if not os.path.exists("config.yaml"):
        pytest.skip("no config.yaml in cwd")
    cfg = config_mod.load("config.yaml")
    assert cfg.source_path == "config.yaml"


def test_partial_config_keeps_defaults(tmp_path):
    cfg = config_mod.load(write(tmp_path, "acquisition:\n  interval_s: 2.5\n"))
    assert cfg.acquisition.interval_s == 2.5
    assert cfg.control.supervisor.operating_point_pct == 63.076


def test_yaml_integer_channel_keys_survive(tmp_path):
    """YAML hands back str keys for the 218's numeric inputs."""
    cfg = config_mod.load(write(tmp_path, """
ls218:
  channels:
    1: Sample
    2: Other
  control_input: 1
"""))
    assert cfg.ls218.channels == {1: "Sample", 2: "Other"}
    assert cfg.control_channel == "Sample"


def test_unknown_key_is_rejected(tmp_path):
    """A typo in a safety limit must not be silently ignored."""
    with pytest.raises(ConfigError, match="unknown key"):
        config_mod.load(write(tmp_path, "control:\n  supervisor:\n    max_eror_k: 5\n"))


def test_nested_sections_are_built(tmp_path):
    cfg = config_mod.load(write(tmp_path, """
control:
  enabled: true
  guard:
    fault_after_s: 300.0
  pid:
    kp: 0.05
"""))
    assert cfg.control.guard.fault_after_s == 300.0
    assert cfg.control.pid.kp == 0.05
    assert cfg.control.enabled


def test_round_trip_through_dump(tmp_path):
    cfg = config_mod.load(None)
    cfg.acquisition.interval_s = 3.0
    p = tmp_path / "out.yaml"
    p.write_text(config_mod.dump(cfg))
    assert config_mod.load(str(p)).acquisition.interval_s == 3.0


# -- validation -------------------------------------------------------------

def test_empty_authority_band_is_rejected():
    cfg = AppConfig()
    cfg.control.supervisor.hard_max_pct = 10.0
    with pytest.raises(ConfigError, match="authority band"):
        cfg.validate()


def test_safe_output_above_operating_point_is_rejected():
    """A fault ramp toward a *higher* output would add heat on a fault."""
    cfg = AppConfig()
    cfg.control.supervisor.safe_output_pct = 70.0
    with pytest.raises(ConfigError, match="ramp would .*raise"):
        cfg.validate()


def test_visa_backend_without_a_resource_is_rejected():
    cfg = AppConfig()
    cfg.ls218.transport.backend = "visa"
    cfg.ls218.transport.resource = ""
    with pytest.raises(ConfigError, match="no resource"):
        cfg.validate()


def test_unknown_backend_is_rejected():
    cfg = AppConfig()
    cfg.ls218.transport.backend = "carrier-pigeon"
    with pytest.raises(ConfigError, match="backend"):
        cfg.validate()


def test_control_input_not_in_channels_is_rejected():
    cfg = AppConfig()
    cfg.ls218.control_input = 7
    with pytest.raises(ConfigError, match="control_input"):
        cfg.validate()


def test_a_cadence_the_bus_cannot_sustain_is_rejected():
    """21 transactions at 50 ms pacing cannot happen twice a second."""
    cfg = AppConfig()
    for inst in (cfg.ls218, cfg.ls336):
        inst.transport.backend = "visa"
        inst.read_status = True
    cfg.acquisition.interval_s = 0.5
    with pytest.raises(ConfigError, match="poll cycle needs"):
        cfg.validate()


def test_recommended_cadence_fits_the_budget():
    """1 Hz must be achievable on real hardware, or the default is a lie."""
    cfg = AppConfig()
    for inst in (cfg.ls218, cfg.ls336):
        inst.transport.backend = "visa"
    cfg.validate()
    assert cfg.estimated_cycle_s() < cfg.acquisition.interval_s
