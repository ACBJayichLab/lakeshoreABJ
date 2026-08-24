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


# -- extension sections ------------------------------------------------------
#
# The generic recorder must not know that a software PID exists, but the config
# file is still one file and unknown keys are still an error.  So a dependent
# package registers its own top-level section and its own validator, and both
# are honoured by `load()` and `AppConfig.validate()` exactly as the built-in
# sections are.  `ltspm.config` registers `control:` this way.

_SECTIONS: dict[str, type] = {}
_VALIDATORS: dict[str, Any] = {}

#: Section name -> the package that provides it.  Purely a diagnostic: it turns
#: "unknown key ['control']" into a sentence that says what to do about it.
#: Nothing here is imported, so the dependency stays one-way.
_SECTION_HINTS = {"control": "ltspm"}


def register_section(name: str, cls: type, *, validator=None) -> None:
    """Attach an extension config section, e.g. ltspm's ``control:``.

    ``validator`` is called as ``validator(section, app_cfg, problems)`` during
    :meth:`AppConfig.validate` and appends strings to ``problems``.  Registering
    the same name twice with the same class is a no-op, so importing the
    extension more than once is harmless.
    """
    if _SECTIONS.get(name) not in (None, cls):
        raise ConfigError(
            f"config section {name!r} is already registered to "
            f"{_SECTIONS[name].__name__}"
        )
    _SECTIONS[name] = cls
    if validator is not None:
        _VALIDATORS[name] = validator


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
    sim: SimConfig = field(default_factory=SimConfig)
    #: Populated from `_SECTIONS` by `load()`; see `register_section`.
    extensions: dict[str, Any] = field(default_factory=dict)
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

    def section(self, name: str, default=None):
        """An extension section, or ``default`` if the file did not carry one.

        Returns a default-constructed section when the extension is registered
        but the file omitted it, so a caller never has to distinguish "absent"
        from "all defaults".
        """
        if name in self.extensions:
            return self.extensions[name]
        cls = _SECTIONS.get(name)
        return cls() if cls is not None else default

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

        for name, validator in _VALIDATORS.items():
            section = self.extensions.get(name)
            if section is not None:
                validator(section, self, problems)

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
    # Neither is settable from the file: `extensions` is populated by `load()`
    # from the registry, `source_path` is stamped on afterwards.  Listing them
    # as valid keys would be an invitation to write one.
    known.pop("extensions", None)
    known.pop("source_path", None)
    unknown = set(value) - set(known)
    if unknown:
        hint = ""
        missing = {k: _SECTION_HINTS[k] for k in sorted(unknown) if k in _SECTION_HINTS}
        if missing:
            pkgs = sorted(set(missing.values()))
            hint = (
                f". Section(s) {sorted(missing)} are provided by "
                f"{' and '.join(repr(p) for p in pkgs)}; this looks like a "
                f"config for {' and '.join(pkgs)} being loaded by a "
                "recorder-only install. Run it with "
                f"`python -m {pkgs[0]}`, or remove the section to record only"
            )
        raise ConfigError(
            f"{path or '<top level>'}: unknown key(s) {sorted(unknown)}; "
            f"valid keys are {sorted(known)}{hint}"
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
        # Registered extension sections are peeled off before AppConfig sees
        # the mapping -- otherwise `control:` reads as an unknown key, which is
        # a hard error by design.
        extensions = {}
        for name, cls in _SECTIONS.items():
            if name in raw:
                extensions[name] = _coerce(cls, raw.pop(name), name)
        cfg = _coerce(AppConfig, raw, "")
        cfg.extensions = extensions
        cfg.source_path = path
    if validate:
        cfg.validate()
    return cfg


def dump(cfg: AppConfig) -> str:
    """Serialise back to YAML -- used to write the annotated starter file."""
    import yaml

    d = dataclasses.asdict(cfg)
    d.pop("source_path", None)
    # Extension sections belong at the top level of the file, where they were
    # read from -- not nested under a key the loader would then reject.
    for name, section in d.pop("extensions", {}).items():
        d[name] = section
    return yaml.safe_dump(d, sort_keys=False, default_flow_style=False)
