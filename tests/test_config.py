"""Config loading and the checks that make a bad config fail loudly.

Generic only.  The `control:` section belongs to `ltspm`, and its tests live in
`tests_ltspm/test_config_control.py`.
"""

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


def test_partial_config_keeps_defaults(tmp_path):
    cfg = config_mod.load(write(tmp_path, "acquisition:\n  interval_s: 2.5\n"))
    assert cfg.acquisition.interval_s == 2.5
    assert cfg.recorder.flush_every_sample is True


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
        config_mod.load(write(tmp_path, "acquisition:\n  intervl_s: 2.5\n"))


def test_an_unregistered_extension_section_is_unknown(tmp_path):
    """`control:` is only a legal key once `ltspm` has registered it.

    A recorder-only install must refuse a config that asks it to close a heater
    loop, rather than ignoring the section and quietly recording instead.
    """
    with pytest.raises(ConfigError, match="unknown key"):
        config_mod.load(write(tmp_path, "sample_pid:\n  enabled: true\n"))


def test_nested_sections_are_built(tmp_path):
    cfg = config_mod.load(write(tmp_path, """
ls336:
  read_heaters: false
acquisition:
  interval_s: 2.0
  ringbuffer_size: 100
"""))
    assert cfg.ls336.read_heaters is False
    assert cfg.acquisition.ringbuffer_size == 100


def test_round_trip_through_dump(tmp_path):
    cfg = config_mod.load(None)
    cfg.acquisition.interval_s = 3.0
    p = tmp_path / "out.yaml"
    p.write_text(config_mod.dump(cfg))
    assert config_mod.load(str(p)).acquisition.interval_s == 3.0


# -- validation -------------------------------------------------------------

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
