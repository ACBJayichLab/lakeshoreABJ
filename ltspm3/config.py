"""The ``control:`` and ``monitor:`` config sections, registered onto lschart.

Importing this module is what makes ``control:`` a legal key in the YAML file.
The generic recorder deliberately does not know the section exists -- see
:func:`lschart.config.register_section` -- so a config that carries a
``control:`` block is an error under plain ``lschart`` and valid under
``ltspm3``.  That is the intended behaviour: it means a config file cannot
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
from .monitor.judge import MonitorConfig


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
    if s.hard_min_pct >= s.hard_max_pct:
        problems.append(
            f"empty output range: hard limits are [{s.hard_min_pct}, "
            f"{s.hard_max_pct}] and nothing can be commanded between them"
        )
    if s.authority_pct <= 0:
        problems.append(
            "control.supervisor.authority_pct must be positive -- a band of "
            "zero width pins the loop to the model's answer and gives the "
            "integral nowhere to go"
        )
    if not s.hard_min_pct <= s.safe_output_pct <= s.hard_max_pct:
        problems.append(
            f"safe_output_pct {s.safe_output_pct}% is outside the hard limits "
            f"[{s.hard_min_pct}, {s.hard_max_pct}] -- the fault ramp-down could "
            "never reach it"
        )
    if cfg.ramp.max_rate_k_per_min <= 0:
        problems.append(
            "control.ramp.max_rate_k_per_min must be positive -- it is the one "
            "rate, and a non-positive one is a sweep that never arrives and a "
            "fault ramp-down that never reaches safe_output_pct"
        )
    if s.min_rate_pct_per_min <= 0:
        problems.append(
            "control.supervisor.min_rate_pct_per_min must be positive -- it is "
            "the floor under the rate where the model has no opinion, and at "
            "zero the output cannot move there at all"
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


def validate_monitor(cfg, app: AppConfig, problems: list[str]) -> None:
    """Limits that contradict each other -- what a type check cannot catch.

    The monitor commands nothing, so nothing here is a safety limit in the
    sense ``control:``'s are.  What it can do is be quietly useless, and these
    are the three ways that has to be caught at load rather than discovered
    from a week of green lights.
    """
    if cfg.baseline_tau_s <= cfg.warn_after_s:
        problems.append(
            f"monitor.baseline_tau_s {cfg.baseline_tau_s} s must be well above "
            f"warn_after_s {cfg.warn_after_s} s -- a baseline that is not slow "
            "against a fault absorbs the fault instead of reporting it")
    if cfg.noise_window_s >= cfg.slope_window_s:
        problems.append(
            f"monitor.noise_window_s {cfg.noise_window_s} s should be shorter "
            f"than slope_window_s {cfg.slope_window_s} s -- the noise is the "
            "scatter about a LINE and a long window has the relaxation's "
            "curvature in it")
    if cfg.warn_sigma <= 0 or cfg.fault_mw <= 0:
        problems.append(
            "monitor.warn_sigma and monitor.fault_mw must be positive")


register_section("control", ControlConfig, validator=validate_control)
register_section("monitor", MonitorConfig, validator=validate_monitor)
