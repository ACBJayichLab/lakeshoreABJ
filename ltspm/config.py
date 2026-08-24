"""The ``control:`` config section, registered onto :mod:`lschart.config`.

Importing this module is what makes ``control:`` a legal key in the YAML file.
The generic recorder deliberately does not know the section exists -- see
:func:`lschart.config.register_section` -- so a config that carries a
``control:`` block is an error under plain ``lschart`` and valid under
``ltspm``.  That is the intended behaviour: it means a config file cannot
quietly ask a recorder-only install to close a heater loop.

Every threshold reachable from here is a safety limit.  The rule from
CLAUDE.md holds: nothing in ``control/`` hardcodes one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lschart.config import AppConfig, register_section

from .control.coherence import CoherenceConfig
from .control.feedforward import FeedforwardConfig
from .control.health import SensorGuardConfig
from .control.pid import PIDConfig
from .control.ramp import RampConfig
from .control.supervisor import SupervisorConfig
from .control.tuning import TuningConfig


@dataclass
class ControlConfig:
    """The heater loop.  ``enabled: false`` gives a pure chart recorder."""

    enabled: bool = False
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    pid: PIDConfig = field(default_factory=PIDConfig)
    guard: SensorGuardConfig = field(default_factory=SensorGuardConfig)
    coherence: CoherenceConfig = field(default_factory=CoherenceConfig)
    ramp: RampConfig = field(default_factory=RampConfig)
    tuning: TuningConfig = field(default_factory=TuningConfig)
    feedforward: FeedforwardConfig = field(default_factory=FeedforwardConfig)
    filter: dict[str, Any] = field(default_factory=dict)


def validate_control(cfg: ControlConfig, app: AppConfig, problems: list[str]) -> None:
    """Limits that contradict each other -- what a type check cannot catch."""
    s = cfg.supervisor
    lo = max(s.hard_min_pct, s.operating_point_pct - s.authority_pct)
    hi = min(s.hard_max_pct, s.operating_point_pct + s.authority_pct)
    if lo > hi:
        problems.append(
            f"empty authority band: operating point {s.operating_point_pct}% is "
            f"outside hard limits [{s.hard_min_pct}, {s.hard_max_pct}]"
        )
    if s.safe_output_pct > s.operating_point_pct:
        problems.append(
            f"safe_output_pct {s.safe_output_pct}% is above the operating point "
            f"{s.operating_point_pct}% -- a fault ramp would *raise* the heater"
        )
    if s.on_exit not in ("hold", "zero"):
        problems.append(
            f"control.supervisor.on_exit must be 'hold' or 'zero', got {s.on_exit!r}"
        )

    g = cfg.guard
    if g.corroborate_slew_k_per_s > g.max_slew_k_per_s:
        problems.append("guard.corroborate_slew_k_per_s must not exceed max_slew_k_per_s")

    # The sample heater *is* the 218's analog output; there is nowhere else for
    # this loop to act.  Catching it here beats failing at wiring time.
    if cfg.enabled and app.ls218 is None:
        problems.append(
            "control.enabled requires ls218.enabled -- the sample heater is the "
            "218's analog output"
        )


register_section("control", ControlConfig, validator=validate_control)
