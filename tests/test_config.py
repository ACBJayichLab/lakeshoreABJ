"""Config loading and the checks that make a bad config fail loudly.

Generic only.  The `control:` section belongs to `ltspm3`, and its tests live in
`tests_ltspm3/test_config_control.py`.
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
    """`control:` is only a legal key once `ltspm3` has registered it.

    A recorder-only install must refuse a config that asks it to close a heater
    loop, rather than ignoring the section and quietly recording instead.
    """
    with pytest.raises(ConfigError, match="unknown key"):
        config_mod.load(write(tmp_path, "sample_pid:\n  enabled: true\n"))


def test_nested_sections_are_built(tmp_path):
    cfg = config_mod.load(write(tmp_path, """
acquisition:
  interval_s: 2.0
  ringbuffer_size: 100
recorder:
  filename_prefix: run
"""))
    assert cfg.recorder.filename_prefix == "run"
    assert cfg.acquisition.ringbuffer_size == 100


def test_round_trip_through_dump(tmp_path):
    cfg = config_mod.load(None)
    cfg.acquisition.interval_s = 3.0
    p = tmp_path / "out.yaml"
    p.write_text(config_mod.dump(cfg))
    assert config_mod.load(str(p)).acquisition.interval_s == 3.0


# -- validation -------------------------------------------------------------

def test_visa_driver_without_a_resource_is_rejected():
    cfg = AppConfig()
    cfg.instruments[0].driver = "visa"
    cfg.instruments[0].transport.resource = ""
    with pytest.raises(ConfigError, match="no transport.resource"):
        cfg.validate()


def test_unknown_driver_is_rejected():
    cfg = AppConfig()
    cfg.instruments[0].driver = "carrier-pigeon"
    with pytest.raises(ConfigError, match="driver"):
        cfg.validate()


def test_control_input_not_in_channels_is_rejected():
    cfg = AppConfig()
    cfg.instruments[0].control_input = 7
    with pytest.raises(ConfigError, match="control_input"):
        cfg.validate()


def test_duplicate_instrument_names_are_rejected():
    """Names label the log columns, so two boxes cannot share one."""
    cfg = AppConfig()
    cfg.instruments[1].name = cfg.instruments[0].resolved_name()
    with pytest.raises(ConfigError, match="both named"):
        cfg.validate()


def test_a_cadence_the_bus_cannot_sustain_is_rejected():
    """A full status poll at 50 ms pacing cannot happen twice a second."""
    cfg = AppConfig()
    for inst in cfg.instruments:
        inst.driver = "visa"
        inst.read_status = True
    cfg.acquisition.interval_s = 0.5
    with pytest.raises(ConfigError, match="poll cycle needs"):
        cfg.validate()


def test_recommended_cadence_fits_the_budget():
    """1 Hz must be achievable on real hardware, or the default is a lie."""
    cfg = AppConfig()
    for inst in cfg.instruments:
        inst.driver = "visa"
    cfg.validate()
    assert cfg.estimated_cycle_s() < cfg.acquisition.interval_s
