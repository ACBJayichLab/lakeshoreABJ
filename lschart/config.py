"""One file decides everything, including whether there is any hardware at all.

The rule from CLAUDE.md is that going live must be a config edit, not a code
change.  So the transport is selected here (``sim`` or ``visa``), and every
threshold in ``control/`` is reachable from the same YAML rather than being a
literal somewhere.

Nothing in this module imports ``pyvisa``.  A ``sim`` deployment must work on a
laptop with no VISA runtime installed, which is where all development currently
happens -- the import is deferred into :func:`build_transport`.

Unknown keys are an error, not a shrug.  A silently-ignored typo in a safety
limit is exactly the failure this file exists to prevent.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any

from .control.coherence import CoherenceConfig
from .control.feedforward import FeedforwardConfig
from .control.health import SensorGuardConfig
from .control.pid import PIDConfig
from .control.ramp import RampConfig
from .control.supervisor import SupervisorConfig

DEFAULT_CONFIG_NAME = "config.yaml"


class ConfigError(ValueError):
    """Raised for anything wrong in the config file, with the path to it."""


# -- sections ---------------------------------------------------------------


@dataclass
class TransportConfig:
    #: "sim" or "visa".  Nothing else in the codebase should branch on hardware.
    backend: str = "sim"
    resource: str = ""
    timeout_ms: int = 3000
    read_termination: str = "\r\n"
    write_termination: str = "\r\n"
    #: Minimum gap between transactions.  At 50 ms a full two-instrument cycle
    #: is ~1.05 s, which does not fit a 1 s poll -- see AcquisitionConfig.
    inter_command_delay: float = 0.05
    visa_library: str = ""
    baud_rate: int | None = None
    data_bits: int | None = None
    parity: str | None = None


@dataclass
class LS218Config:
    enabled: bool = True
    name: str = "ls218"
    transport: TransportConfig = field(
        default_factory=lambda: TransportConfig(resource="GPIB0::15::INSTR")
    )
    #: {input number: display name}.  Only these are read and logged.
    channels: dict[int, str] = field(
        default_factory=lambda: {1: "Sample", 2: "Cold Head", 3: "Shield"}
    )
    #: Which input carries the sample.  This is the channel the PID controls.
    control_input: int = 1
    #: Polling RDGST? per channel costs 8 transactions a cycle and is what makes
    #: a 1 s cadence impossible.  Poll it slowly instead; a sensor fault that
    #: matters shows up in the reading itself within a cycle or two.
    read_status: bool = False
    status_every_n_cycles: int = 15
    analog_output: int = 1
    analog_decimals: int = 3


@dataclass
class LS336Config:
    enabled: bool = True
    name: str = "ls336"
    transport: TransportConfig = field(
        default_factory=lambda: TransportConfig(resource="GPIB0::12::INSTR")
    )
    #: Empty means "ask the instrument for its own labels" (INNAME?).
    channels: dict[str, str] = field(default_factory=dict)
    read_status: bool = False
    read_setpoints: bool = True
    read_heaters: bool = True
    read_analog_outputs: bool = False
    #: Loop 2 independently holds THE CHONKE at 290.6 K.  Disturbing it is a
    #: real hazard, so writes stay off unless someone deliberately turns them on.
    allow_writes: bool = False


@dataclass
class AcquisitionConfig:
    """Poll cadence.

    The reference logs run anywhere from 2 s to 20 s, but that variation was an
    artefact of the 65,536-row Excel limit -- the recorder was slowed down to
    fit longer runs in one file, not because the rig needed it.  A CSV recorder
    has no such limit, so cadence can be chosen on merit.

    1 Hz is the recommendation.  The measured noise is strongly correlated
    (lag-1 autocorrelation 0.51), so it does *not* average down as 1/sqrt(N) --
    sampling faster buys much less than it looks like it should, and 10 Hz would
    hammer the bus for almost nothing.  1 Hz gives the median and reversal tests
    four times the samples they had at 4 s while staying well inside what the
    218 can produce.
    """

    interval_s: float = 1.0
    #: Write every Nth frame to disk.  1 = log everything (the default: there is
    #: no file-size reason not to).
    log_every_n: int = 1
    #: Frames kept in memory for the plot.  Never the log -- see recorder.
    ringbuffer_size: int = 43200


@dataclass
class RecorderConfig:
    enabled: bool = True
    directory: str = "data"
    #: Date-stamped with daily rollover, matching the legacy naming habit.
    filename_prefix: str = "lschart"
    #: Flush every sample.  A power cut must not cost the last hour of data.
    flush_every_sample: bool = True
    #: No cap, deliberately: "no artificial limits" was explicit.
    max_rows: int | None = None


@dataclass
class ControlConfig:
    """The heater loop.  ``enabled: false`` gives a pure chart recorder."""

    enabled: bool = False
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    pid: PIDConfig = field(default_factory=PIDConfig)
    guard: SensorGuardConfig = field(default_factory=SensorGuardConfig)
    coherence: CoherenceConfig = field(default_factory=CoherenceConfig)
    ramp: RampConfig = field(default_factory=RampConfig)
    feedforward: FeedforwardConfig = field(default_factory=FeedforwardConfig)
    filter: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimConfig:
    """Only consulted when a transport backend is ``sim``."""

    start_k: float = 96.0
    seed: int = 0xC01D
    speedup: float = 1.0


@dataclass
class AppConfig:
    ls218: LS218Config = field(default_factory=LS218Config)
    ls336: LS336Config = field(default_factory=LS336Config)
    acquisition: AcquisitionConfig = field(default_factory=AcquisitionConfig)
    recorder: RecorderConfig = field(default_factory=RecorderConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    sim: SimConfig = field(default_factory=SimConfig)
    log_level: str = "INFO"
    source_path: str | None = None

    # -- derived ------------------------------------------------------------

    @property
    def control_channel(self) -> str:
        """Display name of the input the PID controls."""
        try:
            return self.ls218.channels[self.ls218.control_input]
        except KeyError:
            raise ConfigError(
                f"ls218.control_input {self.ls218.control_input} is not in "
                f"ls218.channels {sorted(self.ls218.channels)}"
            ) from None

    @property
    def uses_hardware(self) -> bool:
        return any(
            inst.enabled and inst.transport.backend == "visa"
            for inst in (self.ls218, self.ls336)
        )

    def validate(self) -> None:
        """Catch what a type check cannot: limits that contradict each other."""
        problems: list[str] = []

        for inst in (self.ls218, self.ls336):
            if inst.transport.backend not in ("sim", "visa"):
                problems.append(
                    f"{inst.name}.transport.backend must be 'sim' or 'visa', "
                    f"got {inst.transport.backend!r}"
                )
            if inst.enabled and inst.transport.backend == "visa" and not inst.transport.resource:
                problems.append(f"{inst.name} uses the visa backend but has no resource string")

        if self.ls218.enabled:
            try:
                self.control_channel
            except ConfigError as exc:
                problems.append(str(exc))

        s = self.control.supervisor
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
            problems.append(f"control.supervisor.on_exit must be 'hold' or 'zero', got {s.on_exit!r}")

        g = self.control.guard
        if g.corroborate_slew_k_per_s > g.max_slew_k_per_s:
            problems.append(
                "guard.corroborate_slew_k_per_s must not exceed max_slew_k_per_s"
            )
        if self.acquisition.interval_s <= 0:
            problems.append("acquisition.interval_s must be positive")
        if self.acquisition.log_every_n < 1:
            problems.append("acquisition.log_every_n must be at least 1")

        # A cycle that cannot fit in its own poll interval will silently drift.
        est = self.estimated_cycle_s()
        if est > self.acquisition.interval_s:
            problems.append(
                f"a poll cycle needs about {est:.2f} s "
                f"({self.estimated_transactions()} GPIB transactions at "
                f"{self.ls218.transport.inter_command_delay * 1000:.0f} ms pacing) but "
                f"acquisition.interval_s is {self.acquisition.interval_s} s. "
                "Lower inter_command_delay, set read_status: false, or slow the poll."
            )

        if problems:
            where = f" in {self.source_path}" if self.source_path else ""
            raise ConfigError(
                f"{len(problems)} problem(s){where}:\n  - " + "\n  - ".join(problems)
            )

    # -- budgeting ----------------------------------------------------------

    def estimated_transactions(self) -> int:
        """GPIB transactions per poll cycle, for the interval sanity check."""
        n = 0
        if self.ls218.enabled:
            n += 1                                   # KRDG? 0
            n += 1                                   # AOUT?
            if self.ls218.read_status:
                n += len(self.ls218.channels)        # RDGST? per input
        if self.ls336.enabled:
            n += 1                                   # KRDG? 0
            if self.ls336.read_status:
                n += 4
            if self.ls336.read_setpoints:
                n += 4
            if self.ls336.read_heaters:
                n += 2
            if self.ls336.read_analog_outputs:
                n += 2
        return n

    def estimated_cycle_s(self) -> float:
        if self.uses_hardware:
            delay = max(self.ls218.transport.inter_command_delay,
                        self.ls336.transport.inter_command_delay)
        else:
            delay = 0.0
        return self.estimated_transactions() * delay


# -- loading ----------------------------------------------------------------


def _coerce(cls, value: Any, path: str):
    """Build ``cls`` from a mapping, rejecting unknown keys."""
    if not is_dataclass(cls):
        return value
    if value is None:
        return cls()
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: expected a mapping, got {type(value).__name__}")

    known = {f.name: f for f in fields(cls)}
    unknown = set(value) - set(known)
    if unknown:
        raise ConfigError(
            f"{path}: unknown key(s) {sorted(unknown)}; "
            f"valid keys are {sorted(known)}"
        )

    kwargs = {}
    for name, raw in value.items():
        f = known[name]
        sub = f"{path}.{name}" if path else name
        ftype = f.type
        # dataclass-typed fields recurse; everything else is passed through and
        # left to the dataclass to hold.
        default = (
            f.default_factory() if f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
            else f.default
        )
        if is_dataclass(type(default)) and not isinstance(default, type):
            kwargs[name] = _coerce(type(default), raw, sub)
        elif name == "channels" and isinstance(raw, dict):
            # YAML gives str keys for the 218's integer inputs.
            kwargs[name] = {
                (int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k): v
                for k, v in raw.items()
            }
        else:
            kwargs[name] = raw
        del ftype
    return cls(**kwargs)


def load(path: str | None = None, *, validate: bool = True) -> AppConfig:
    """Read a config file.  With no path, returns validated defaults (sim)."""
    if path is None:
        cfg = AppConfig()
    else:
        import yaml

        if not os.path.exists(path):
            raise ConfigError(f"no such config file: {path}")
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: top level must be a mapping")
        cfg = _coerce(AppConfig, raw, "")
        cfg.source_path = path
    if validate:
        cfg.validate()
    return cfg


def dump(cfg: AppConfig) -> str:
    """Serialise back to YAML -- used to write the annotated starter file."""
    import yaml

    d = dataclasses.asdict(cfg)
    d.pop("source_path", None)
    return yaml.safe_dump(d, sort_keys=False, default_flow_style=False)
