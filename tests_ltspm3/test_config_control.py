"""The `control:` config section, and the contradictions it must refuse.

Importing `ltspm3.config` is what registers the section; without it `control:`
is an unknown key and the file is rejected.  That is deliberate -- see
`lschart.config.register_section`.
"""

from pathlib import Path

import pytest

import ltspm3.config  # noqa: F401  -- registers the `control:` section
from lschart import config as config_mod
from lschart.config import AppConfig, ConfigError


def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def control(cfg) -> "ltspm3.config.ControlConfig":
    return cfg.section("control")


def test_control_never_defaults_to_on():
    cfg = config_mod.load(None)
    assert not control(cfg).enabled, "control must never default to on"


def test_repo_config_is_valid():
    """The committed starter file must actually load.

    Found by its path relative to this file: `config.yaml` is tracked, so it is
    always there, and a cwd-relative lookup only made the test disappear when
    pytest ran from anywhere but the repo root.
    """
    path = Path(__file__).resolve().parents[1] / "config.yaml"
    assert path.exists(), f"the committed starter config is missing from {path}"
    cfg = config_mod.load(str(path))
    assert cfg.source_path == str(path)


def test_omitted_section_still_gives_defaults(tmp_path):
    """A file with no `control:` block reads as all-defaults, not as absent."""
    cfg = config_mod.load(write(tmp_path, "acquisition:\n  interval_s: 2.5\n"))
    assert control(cfg).supervisor.operating_point_pct == 63.076


def test_nested_sections_are_built(tmp_path):
    cfg = config_mod.load(write(tmp_path, """
control:
  enabled: true
  guard:
    fault_after_s: 300.0
  pid:
    kp: 0.05
"""))
    c = control(cfg)
    assert c.guard.fault_after_s == 300.0
    assert c.pid.kp == 0.05
    assert c.enabled


def test_unknown_key_inside_control_is_rejected(tmp_path):
    """A typo in a safety limit must not be silently ignored."""
    with pytest.raises(ConfigError, match="unknown key"):
        config_mod.load(write(tmp_path, "control:\n  supervisor:\n    max_eror_k: 5\n"))


def test_control_survives_a_dump_round_trip(tmp_path):
    """`dump` must put extension sections back at the top level it read them from."""
    cfg = config_mod.load(write(tmp_path, "control:\n  enabled: true\n  pid:\n    kp: 0.07\n"))
    out = tmp_path / "out.yaml"
    out.write_text(config_mod.dump(cfg))
    again = config_mod.load(str(out))
    assert control(again).enabled
    assert control(again).pid.kp == 0.07


# -- validation -------------------------------------------------------------

def _with_control(**kw):
    """An AppConfig carrying an explicit `control:` section.

    Validators only run against sections the *file* supplied, so a test that
    wants one has to put it there -- exactly as a real config does.
    """
    cfg = AppConfig()
    cfg.extensions["control"] = ltspm3.config.ControlConfig(**kw)
    return cfg


def test_empty_authority_band_is_rejected():
    cfg = _with_control()
    cfg.extensions["control"].supervisor.hard_max_pct = 10.0
    with pytest.raises(ConfigError, match="authority band"):
        cfg.validate()


def test_safe_output_above_operating_point_is_rejected():
    """A fault ramp toward a *higher* output would add heat on a fault."""
    cfg = _with_control()
    cfg.extensions["control"].supervisor.safe_output_pct = 70.0
    with pytest.raises(ConfigError, match="ramp would .*raise"):
        cfg.validate()


def test_corroboration_threshold_above_the_hard_slew_limit_is_rejected():
    cfg = _with_control()
    cfg.extensions["control"].guard.corroborate_slew_k_per_s = 99.0
    with pytest.raises(ConfigError, match="corroborate_slew"):
        cfg.validate()


def test_control_without_the_218_is_rejected():
    """The sample heater *is* the 218's analog output; there is nowhere else."""
    cfg = _with_control(enabled=True)
    for inst in cfg.instruments:
        if inst.model == "218":
            inst.enabled = False
    with pytest.raises(ConfigError, match="requires ls218"):
        cfg.validate()


def test_the_band_the_docs_quote_is_the_band_the_config_produces():
    """A stale band in a document is not a cosmetic error.

    `docs/ltspm3/running.md` and `control.md` both show a worked `check` line,
    and both quoted 58.076-68.076% -- five times too wide -- while
    `authority_pct` was 1.0.  The band is what decides whether the output you
    are sitting on is one the loop may keep, so a reader who trusted the page
    would conclude an output was inside the band when it was above the ceiling.
    """
    import re
    from pathlib import Path

    from ltspm3.control import SupervisorConfig

    c = SupervisorConfig()
    lo = max(c.hard_min_pct, c.operating_point_pct - c.authority_pct)
    hi = min(c.hard_max_pct, c.operating_point_pct + c.authority_pct)

    docs = Path(__file__).resolve().parents[1] / "docs" / "ltspm3"
    pattern = re.compile(r"authority band\s*:\s*([\d.]+)%\s*\.\.\s*([\d.]+)%")
    seen = 0
    for page in docs.glob("*.md"):
        for got_lo, got_hi in pattern.findall(page.read_text(encoding="utf-8")):
            seen += 1
            assert (float(got_lo), float(got_hi)) == (lo, hi), (
                f"{page.name} quotes a band of {got_lo}-{got_hi}%, but the "
                f"shipped config produces {lo:.3f}-{hi:.3f}%"
            )
    assert seen, "no worked `check` band found in docs/ltspm3 -- did they move?"
