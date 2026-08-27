"""Config loading and the checks that make a bad config fail loudly.

Generic only.  The `control:` section belongs to `ltspm3`, and its tests live in
`tests_ltspm3/test_config_control.py`.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lschart import config as config_mod
from lschart.config import AppConfig, ConfigError


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


def test_yaml_integer_loop_keys_survive(tmp_path):
    """Same trap as the 218's channel numbers: YAML hands over string keys,
    and a threshold filed under "1" would never be found for loop 1."""
    cfg = config_mod.load(write(tmp_path, """
instruments:
  - name: ls336
    model: "336"
    loop_thresholds:
      1: 0.5
      2: 2.0
"""))
    assert cfg.instruments[0].loop_thresholds == {1: 0.5, 2: 2.0}


def test_loop_polling_is_counted_in_the_bus_budget(tmp_path):
    """OUTMODE? and RAMPST? land on the bus like anything else, and `check`
    has to predict the worst frame without opening a port."""
    with_loops = config_mod.load(write(tmp_path, """
instruments:
  - name: ls336
    model: "336"
    read_loops: true
"""))
    without = config_mod.load(write(tmp_path, """
instruments:
  - name: ls336
    model: "336"
    read_loops: false
"""))
    assert (with_loops.estimated_transactions()
            - without.estimated_transactions()) == 2 * 4     # 4 loops


def test_unknown_key_is_rejected(tmp_path):
    """A typo in a safety limit must not be silently ignored."""
    with pytest.raises(ConfigError, match="unknown key"):
        config_mod.load(write(tmp_path, "acquisition:\n  intervl_s: 2.5\n"))


def test_an_unregistered_extension_section_is_unknown(tmp_path):
    """The generic mechanism: a section nobody registered is not a valid key.

    Deliberately a name no package claims.  `control:` cannot be used here --
    importing `tests_ltspm3` anywhere in the same session registers it process
    wide, so this would pass or fail depending on test order.  The `control:`
    case gets its own subprocess test below.
    """
    with pytest.raises(ConfigError, match="unknown key"):
        config_mod.load(write(tmp_path, "sample_pid:\n  enabled: true\n"))


def test_a_recorder_only_install_refuses_an_ltspm3_config(tmp_path):
    """The coworker case, and the one CLAUDE.md promises: `lschart` REFUSES it
    and says why.

    This has to run in a fresh interpreter.  `register_section` mutates process
    wide state, so once anything has imported `ltspm3.config` -- which the rest
    of this suite does -- `control:` is a legal key for the remainder of the
    session and the refusal cannot be observed in-process at all.  That is why
    the behaviour went untested despite looking covered.
    """
    cfg = tmp_path / "ltspm3.yaml"
    cfg.write_text("control:\n  enabled: true\n")
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
        from lschart import config
        assert "ltspm3" not in sys.modules, "the point of the subprocess"
        try:
            config.load({str(cfg)!r})
        except config.ConfigError as exc:
            print(exc)
        else:
            print("ACCEPTED")
    """)
    out = subprocess.run([sys.executable, "-c", script],
                         capture_output=True, text=True, check=True).stdout

    assert "ACCEPTED" not in out, "recorded silently instead of refusing"
    assert "unknown key" in out and "control" in out
    # Refusing is not enough on its own: the message has to send the reader
    # somewhere.  This is the error a coworker hits first.
    assert "ltspm3" in out, "refused without naming what provides the section"
    assert "python -m ltspm3" in out, "refused without saying what to run instead"


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
    with pytest.raises(ConfigError, match="cycle needs"):
        cfg.validate()


def test_recommended_cadence_fits_the_budget():
    """1 Hz must be achievable on real hardware, or the default is a lie."""
    cfg = AppConfig()
    for inst in cfg.instruments:
        inst.driver = "visa"
    cfg.validate()
    assert cfg.estimated_cycle_s() < cfg.acquisition.interval_s
