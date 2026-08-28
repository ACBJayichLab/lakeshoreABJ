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
import logging
import os
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_CONFIG_NAME = "config.yaml"


class ConfigError(ValueError):
    """Raised for anything wrong in the config file, with the path to it."""


# -- sections ---------------------------------------------------------------


@dataclass
class TransportConfig:
    #: Deprecated alias for `InstrumentConfig.driver`, kept because it is what
    #: the old two-section config called it.  `load()` migrates it.
    backend: str = ""
    #: VISA resource string: "GPIB0::15::INSTR", "ASRL10::INSTR", "TCPIP::...".
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

    # -- driver: lakeshore ---------------------------------------------------
    #: Windows COM port ("COM10") or POSIX device ("/dev/ttyUSB0").
    com_port: str = ""
    #: Preferred over com_port where the instrument reports one.  A USB device
    #: that re-enumerates comes back on a different port but the same serial,
    #: so matching on this is what survives a replug.
    serial_number: str = ""
    #: For an Ethernet box; mutually exclusive with com_port.
    ip_address: str = ""
    tcp_port: int = 7777

    #: Hard interlock, one layer below `allow_writes`: refuse to transmit any
    #: command at all.  Use it on a box that must not be touched under any
    #: circumstances, including by a bug in this program.
    read_only: bool = False

    # -- staying connected ---------------------------------------------------
    #: A dropped link is recovered rather than being terminal.  Turn this off
    #: only if you would rather a run stop than continue with a gap.
    reconnect: bool = True
    retry_min_s: float = 1.0
    retry_max_s: float = 30.0
    #: One GPIB timeout is usually a slow instrument, not a dead bus, so the
    #: link is only torn down after this many consecutive failures.
    failures_before_reconnect: int = 3


@dataclass
class InstrumentConfig:
    """Fields common to every box.  Model-specific ones live in the subclasses.

    ``driver`` decides how bytes reach the instrument and is the only thing
    that changes between a bench and a cryostat:

    ``sim``        an in-process fake; no hardware, no VISA, no serial port
    ``visa``       pyvisa -- GPIB, and serial or TCP if you have a VISA runtime
    ``lakeshore``  Lake Shore's own driver: USB/serial and TCP, **no VISA**

    ``lakeshore`` is the right choice for a box on a COM port, because it
    removes the NI-VISA install from the deployment entirely.
    """

    name: str = ""
    model: str = ""
    enabled: bool = True
    driver: str = "sim"
    transport: TransportConfig = field(default_factory=TransportConfig)
    read_status: bool = False
    status_every_n_cycles: int = 15

    def resolved_name(self) -> str:
        return self.name or f"ls{self.model}"


@dataclass
class LS218Config(InstrumentConfig):
    """The 8-input monitor.  Its analog output is the LTSPM3 sample heater.

    ``allow_writes`` gates the one write this box has, and it is off by default
    for a blunter reason than on a 33x.  A 33x setpoint is inert until a range
    is raised, so its two commands can be separately gated; the 218's analog
    output has no inert half.  The percentage *is* the power, so the driver
    policy gate and the ceiling are the whole of the protection.
    """

    model: str = "218"
    name: str = "ls218"
    transport: TransportConfig = field(
        default_factory=lambda: TransportConfig(resource="GPIB0::15::INSTR")
    )
    #: {input number: display name}.  Only these are read and logged.
    channels: dict[int, str] = field(
        default_factory=lambda: {1: "Sample", 2: "Cold Head", 3: "Shield"}
    )
    #: Which input carries the sample.  This is the channel a software loop
    #: controls; leave it 0 on a box that is only being logged.
    control_input: int = 1
    analog_output: int = 1
    analog_decimals: int = 3
    #: Permit ANALOG writes at all.  Off by default; see the class docstring.
    allow_writes: bool = False
    #: Blunt ceiling on the commanded percentage, refused in software.  100 is
    #: the instrument's own full scale and therefore no restriction: what the
    #: ceiling is *worth* depends entirely on the heater on the other end, so
    #: the cryostat's config sets it and this generic default does not guess.
    max_output_pct: float = 100.0
    #: Confirm every write by reading ``AOUT?`` back.  Turn this off for a
    #: software loop that writes every cycle and verifies for itself.
    verify_writes: bool = True
    #: How far a readback may sit from the commanded value and still count.
    #: Must exceed the DAC step plus the readback's display rounding, or a
    #: write that worked perfectly will be reported as a failure.
    readback_tol_pct: float = 0.02


@dataclass
class LS33xConfig(InstrumentConfig):
    """A 335 or 336: inputs plus the instrument's own PID loops.

    ``allow_writes`` gates every command that can change what the box does.  It
    is off by default because the common case on a shared cryostat is that some
    other loop is already holding something important -- on the LTSPM3 cryostat, the
    336's loop 2 holds THE CHONKE at 290.6 K, and disturbing it is a real
    hazard.  Turn it on for a box this software is meant to drive.
    """

    model: str = "336"
    name: str = "ls336"
    transport: TransportConfig = field(
        default_factory=lambda: TransportConfig(resource="GPIB0::12::INSTR")
    )
    #: Empty means "ask the instrument for its own labels" (INNAME?).
    channels: dict[str, str] = field(default_factory=dict)
    read_setpoints: bool = True
    read_heaters: bool = True
    read_analog_outputs: bool = False
    #: Ask the instrument what each loop is bound to and what it is doing
    #: (``OUTMODE?``, ``RAMPST?``).  On, because it costs two transactions per
    #: loop every ``loop_every_n_cycles`` frames and it is the only correct
    #: answer to "which sensor does loop 2 read" -- a map of that kept here
    #: could only ever go stale or lie.
    read_loops: bool = True
    #: Ask each loop for its gains (``PID?``) on that same slow cadence.  The
    #: viewer holds no port, so polling is the only way P, I and D reach a
    #: screen at all.  One transaction per loop per slow tick, counted at full
    #: weight in the budget.  **Off by default**, unlike the other slow reads:
    #: the default 218 + 336 config already sits at 19 transactions against a
    #: 1 s cadence at 50 ms pacing, and one more per loop does not fit.  At the
    #: 2 s cadence a cryostat usually runs there is room, and the example
    #: configs turn it on.
    read_pid: bool = False
    #: How often those are re-read.  ``OUTMODE`` changes approximately never;
    #: 30 cycles is a minute at the 2 s cadence a cryostat usually runs.
    loop_every_n_cycles: int = 30
    #: ``{loop: kelvin}`` -- how far a loop may sit from its setpoint and still
    #: count as settled.  Per loop and not global on purpose: 0.5 K is a tight
    #: tolerance at 4 K and a loose one at 300 K, so it is a property of the
    #: loop rather than of this software.  A loop left out has no opinion about
    #: being settled, and no client will claim one for it.
    loop_thresholds: dict[int, float] = field(default_factory=dict)
    allow_writes: bool = False
    #: A blunt guard against a typo'd setpoint.  Refused in software rather
    #: than politely forwarded to a cryostat.
    max_setpoint_k: float = 350.0


#: model number -> the config class that describes that box.
INSTRUMENT_CONFIGS: dict[str, type] = {
    "218": LS218Config,
    "335": LS33xConfig,
    "336": LS33xConfig,
}


def default_instruments() -> list[InstrumentConfig]:
    """The LTSPM3 cryostat, which is what this software was written against."""
    return [LS218Config(), LS33xConfig()]


@dataclass
class AcquisitionConfig:
    """Poll cadence.

    The reference logs run anywhere from 2 s to 20 s, but that variation was an
    artefact of the 65,536-row Excel limit -- the recorder was slowed down to
    fit longer runs in one file, not because the cryostat needed it.  A CSV recorder
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
class IpcConfig:
    """Talking to MATLAB and the viewer without sharing the instrument.

    The recorder holds the port exclusively -- a Windows COM port has exactly
    one holder -- so MATLAB *cannot* open the instrument while this is running.
    Files are therefore not a stylistic preference but the only shape that
    works, and this section is how it is switched on.

    ``enabled`` writes ``status.json`` every cycle.  It costs one small file
    write per second and nothing on the bus, and it is what the viewer reads, so
    it is on by default.

    ``accept_commands`` is the other direction and is **off** by default, for
    the same reason ``allow_writes`` is: the common case is a shared cryostat
    where something else is already holding something important.  Turning it on
    is not by itself enough -- the instrument's own ``allow_writes`` still
    gates every write, and ``transport.read_only`` still gates every byte.
    """

    enabled: bool = True
    #: Where ``status.json`` and the command spool live.  Alongside the CSV by
    #: default, so one directory is the whole interface.
    directory: str = "data"
    status_file: str = "status.json"
    command_directory: str = "commands"

    #: Read the command spool at all.  See the class docstring.
    accept_commands: bool = False
    #: A command older than this is refused, not applied.  A recorder that was
    #: down for an hour must not come back and replay an hour of setpoints
    #: into a live cryostat -- the traversal is the hazard, not the target.
    command_ttl_s: float = 30.0
    #: A bound on how much bus time one cycle may spend on commands.  Each
    #: write costs a settle plus a verifying readback, so an unbounded backlog
    #: could overrun the poll interval.
    max_commands_per_cycle: int = 4
    #: Raising a heater range is the act that applies power.  A remote client
    #: may only do it if this says so -- in EITHER direction. Commanding a
    #: range to 0 needs this too: cutting a heater stops heating and can also
    #: crash the stage, so it is a change of state and not a retreat to safety.
    #: The panic kinds are what stay reachable with this shut.
    allow_heater_range: bool = False
    #: The same permission for the 218's analog output, which needs its own
    #: switch because it is a different command with the same consequence:
    #: there is no range on a 218, so the percentage is the power.  A remote
    #: client may only move it if this says so -- 0 included, for the same
    #: reason as above.
    allow_analog_output: bool = False
    #: May a file retune a loop?  Its own gate rather than riding on
    #: `allow_writes`, because changing P, I and D is a different kind of act
    #: from moving a setpoint: a setpoint moves a cryostat somewhere, gains
    #: change *how it gets anywhere at all*, and a badly-tuned loop misbehaves
    #: for the rest of the run rather than visibly at the moment of the command.
    #: It applies no power on its own, which is why it is not one of the two
    #: gates above -- a loop with range 0 stays inert however it is tuned.
    allow_pid: bool = False

    #: Per-client policy: which *sources* may ask at all.  The five interlocks
    #: above all answer "may this action happen"; this one answers "may this
    #: client ask for it", which none of the others can express.
    #:
    #: A mapping of source label to bool, plus a `default:` for labels not
    #: listed.  Matched on the part before the first "/", because the CLI
    #: stamps its pid into its label.  Left empty -- the default -- there is no
    #: policy and every source may ask, which is what every existing config
    #: means.  Written non-empty, `default:` is **false** unless it says
    #: otherwise: naming your clients is how you say you have thought about the
    #: list, and a typo in one of those names must fail closed.
    #:
    #: Narrowed at runtime, never widened, by `sources.json` in the IPC
    #: directory -- see :mod:`lschart.ipc.sources`.
    sources: dict = field(default_factory=dict)

    #: How many acknowledgements ``status.json`` carries.  A client that polls
    #: slower than this fills up may miss its own answer.
    ack_history: int = 20

    def status_path(self) -> str:
        return os.path.join(self.directory, self.status_file)

    def command_path(self) -> str:
        return os.path.join(self.directory, self.command_directory)

    def sources_path(self) -> str:
        """The runtime source overlay -- see :mod:`lschart.ipc.sources`.

        Beside the status file rather than inside the command spool: the spool
        is a queue whose files are consumed and deleted, and a file that must
        persist has no business living in one.
        """
        from .ipc.sources import SOURCES_FILE

        return os.path.join(self.directory, SOURCES_FILE)


@dataclass
class RuntimeConfig:
    """Process-level concerns: being the only recorder on this instrument."""

    #: Path to the single-instance lock.  Two recorders on one instrument fight
    #: over the port, so `run` takes this before opening anything.  Point two
    #: genuinely-different cryostats at two different paths to run both.
    lock_path: str = "data/lschart.lock"
    single_instance: bool = True


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
# sections are.  `ltspm3.config` registers `control:` this way.

_SECTIONS: dict[str, type] = {}
_VALIDATORS: dict[str, Any] = {}

#: Section name -> the package that provides it.  Purely a diagnostic: it turns
#: "unknown key ['control']" into a sentence that says what to do about it.
#: Nothing here is imported, so the dependency stays one-way.
_SECTION_HINTS = {"control": "ltspm3"}


def register_section(name: str, cls: type, *, validator=None) -> None:
    """Attach an extension config section, e.g. ltspm3's ``control:``.

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
    instruments: list = field(default_factory=default_instruments)
    acquisition: AcquisitionConfig = field(default_factory=AcquisitionConfig)
    recorder: RecorderConfig = field(default_factory=RecorderConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    ipc: IpcConfig = field(default_factory=IpcConfig)
    sim: SimConfig = field(default_factory=SimConfig)
    #: Populated from `_SECTIONS` by `load()`; see `register_section`.
    extensions: dict[str, Any] = field(default_factory=dict)
    log_level: str = "INFO"
    source_path: str | None = None

    # -- derived ------------------------------------------------------------

    def instrument(self, name: str):
        """One instrument by name, or ``None``."""
        for inst in self.instruments:
            if inst.resolved_name() == name:
                return inst
        return None

    @property
    def ls218(self):
        """The first enabled 218, or ``None``.

        The software heater loop acts on a 218's analog output and there is
        only ever one of those, so this stays a convenience.  Everything that
        merely reads instruments should iterate `self.instruments` instead.
        """
        for inst in self.instruments:
            if inst.model == "218" and inst.enabled:
                return inst
        return None

    @property
    def control_instrument(self):
        """The instrument carrying the channel a software loop would control."""
        for inst in self.instruments:
            if inst.enabled and getattr(inst, "control_input", 0):
                return inst
        return None

    @property
    def control_channel(self) -> str:
        """Display name of the input a software loop controls."""
        inst = self.control_instrument
        if inst is None:
            raise ConfigError(
                "no instrument declares a control_input, so there is no control "
                "channel; set control_input on the box carrying the sample"
            )
        try:
            return inst.channels[inst.control_input]
        except KeyError:
            raise ConfigError(
                f"{inst.resolved_name()}.control_input {inst.control_input} is not "
                f"in channels {sorted(inst.channels)}"
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
    def enabled_instruments(self) -> list:
        return [i for i in self.instruments if i.enabled]

    @property
    def uses_hardware(self) -> bool:
        return any(i.driver != "sim" for i in self.enabled_instruments)

    def validate(self) -> None:
        """Catch what a type check cannot: limits that contradict each other."""
        problems: list[str] = []

        seen: set[str] = set()
        for inst in self.instruments:
            who = inst.resolved_name()
            if who in seen:
                problems.append(
                    f"two instruments are both named {who!r}; names label the log "
                    "columns, so they have to be unique -- set `name:` explicitly"
                )
            seen.add(who)

            if inst.model not in INSTRUMENT_CONFIGS:
                problems.append(
                    f"{who}: unsupported model {inst.model!r}; "
                    f"known models are {sorted(INSTRUMENT_CONFIGS)}"
                )
            if inst.driver not in ("sim", "visa", "lakeshore"):
                problems.append(
                    f"{who}.driver must be 'sim', 'visa' or 'lakeshore', "
                    f"got {inst.driver!r}"
                )
            if not inst.enabled:
                continue

            t = inst.transport
            if inst.driver == "visa" and not t.resource:
                problems.append(f"{who} uses the visa driver but has no transport.resource")
            if inst.driver == "lakeshore":
                if inst.model not in ("335", "336", "224", "240"):
                    problems.append(
                        f"{who}: the lakeshore driver has no class for model "
                        f"{inst.model} -- use driver: visa for that box"
                    )
                if not (t.com_port or t.serial_number or t.ip_address):
                    problems.append(
                        f"{who} uses the lakeshore driver but names no com_port, "
                        "serial_number or ip_address"
                    )
                if t.com_port and t.ip_address:
                    problems.append(
                        f"{who} names both a com_port and an ip_address; pick one"
                    )

        if self.control_instrument is not None:
            try:
                self.control_channel
            except ConfigError as exc:
                problems.append(str(exc))

        if self.acquisition.interval_s <= 0:
            problems.append("acquisition.interval_s must be positive")
        if self.acquisition.log_every_n < 1:
            problems.append("acquisition.log_every_n must be at least 1")

        if self.ipc.enabled:
            if self.ipc.command_ttl_s <= 0:
                problems.append(
                    "ipc.command_ttl_s must be positive; it is what stops a "
                    "backlog of stale commands being replayed into a live cryostat"
                )
            if self.ipc.max_commands_per_cycle < 1:
                problems.append("ipc.max_commands_per_cycle must be at least 1")
            if not self.ipc.status_file:
                problems.append("ipc.status_file must be a filename")
            if self.ipc.command_directory == "":
                problems.append(
                    "ipc.command_directory must be a directory name; commands "
                    "cannot share a directory with the status file they answer"
                )
            for key, value in self.ipc.sources.items():
                if not isinstance(value, bool):
                    problems.append(
                        f"ipc.sources.{key} must be true or false, got "
                        f"{value!r}; a source policy that cannot be read as a "
                        "yes or a no is not a policy"
                    )
            if self.ipc.sources and not any(
                v for k, v in self.ipc.sources.items() if k != "default"
            ) and not self.ipc.sources.get("default", False):
                problems.append(
                    "ipc.sources permits nothing at all, so every command "
                    "would be refused; remove the section to permit every "
                    "source, or name the ones you mean"
                )
        elif self.ipc.accept_commands:
            problems.append(
                "ipc.accept_commands is true but ipc.enabled is false, so "
                "nothing would ever read the spool -- and a client would have "
                "no status file in which to see its command refused"
            )

        # A cycle that cannot fit in its own poll interval will silently drift.
        est = self.estimated_cycle_s()
        if est > self.acquisition.interval_s:
            problems.append(
                f"a cycle needs about {est:.2f} s "
                f"({self.estimated_transactions()} transactions at "
                f"{self.max_pacing_s() * 1000:.0f} ms pacing) but "
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
        """Bus transactions per cycle, for the interval sanity check.

        Counted from configuration rather than from live instruments, because
        `check` has to answer this without opening anything.
        """
        n = 0
        for inst in self.enabled_instruments:
            if inst.model == "218":
                n += 2                                    # KRDG? 0, AOUT?
                if inst.read_status:
                    n += len(inst.channels)               # RDGST? per input
            else:
                caps = _caps_for(inst.model)
                n += 1                                    # KRDG? 0
                if inst.read_status:
                    n += len(inst.channels) or len(caps.inputs)
                if inst.read_setpoints:
                    n += len(caps.loops)
                if inst.read_heaters:
                    n += 2 * len(caps.heater_outputs)     # HTR? and RANGE?
                if inst.read_analog_outputs:
                    n += len(caps.analog_outputs)
                if inst.read_loops:
                    # OUTMODE? and RAMPST? per loop.  Counted at full weight
                    # even though they only land every Nth cycle: this is the
                    # worst frame, and the worst frame is what has to fit.
                    n += 2 * len(caps.loops)
                if inst.read_pid:
                    n += len(caps.loops)                  # PID? per loop
        return n

    def max_pacing_s(self) -> float:
        """Slowest inter-command delay among instruments that touch a real bus."""
        delays = [
            i.transport.inter_command_delay
            for i in self.enabled_instruments if i.driver != "sim"
        ]
        return max(delays) if delays else 0.0

    def estimated_cycle_s(self) -> float:
        return self.estimated_transactions() * self.max_pacing_s()


def _caps_for(model: str):
    """Capability table for a 33x, without importing the driver at module scope."""
    from .instruments.ls33x import CAPS

    return CAPS[model]


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
        if name == "instruments" and isinstance(raw, list):
            kwargs[name] = [
                _coerce_instrument(item, f"{sub}[{i}]") for i, item in enumerate(raw)
            ]
        elif is_dataclass(type(default)) and not isinstance(default, type):
            kwargs[name] = _coerce(type(default), raw, sub)
        elif name in ("channels", "loop_thresholds") and isinstance(raw, dict):
            # YAML gives str keys for the 218's integer inputs, and for the
            # 33x's loop numbers.
            kwargs[name] = {
                (int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k): v
                for k, v in raw.items()
            }
        else:
            kwargs[name] = raw
        del ftype
    return cls(**kwargs)


def _coerce_instrument(value: Any, path: str) -> InstrumentConfig:
    """Pick the config class from ``model:``, then coerce strictly into it.

    Choosing the class by model is what keeps "unknown keys are an error"
    meaningful for instruments: a `control_input` on a 336, or a `max_setpoint_k`
    on a 218, is a mistake about what the box *is*, and it gets caught here
    rather than being carried around as a field that does nothing.
    """
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: expected a mapping, got {type(value).__name__}")
    model = str(value.get("model", "")).strip()
    if not model:
        raise ConfigError(
            f"{path}: every instrument needs a `model:` "
            f"(one of {sorted(INSTRUMENT_CONFIGS)})"
        )
    cls = INSTRUMENT_CONFIGS.get(model)
    if cls is None:
        raise ConfigError(
            f"{path}: unsupported model {model!r}; "
            f"known models are {sorted(INSTRUMENT_CONFIGS)}"
        )
    return _coerce(cls, value, path)


def _migrate_legacy_instruments(raw: dict, path: str) -> None:
    """Fold the old `ls218:`/`ls336:` sections into `instruments:`.

    The two-section layout could only ever describe one 218 and one 336, which
    is exactly the assumption the list exists to remove.  Migrating rather than
    rejecting means an existing config keeps working, but it is announced --
    silently rewriting someone's instrument configuration is not a favour.
    """
    legacy = [(k, m) for k, m in (("ls218", "218"), ("ls336", "336")) if k in raw]
    if not legacy:
        return
    if "instruments" in raw:
        raise ConfigError(
            f"{path}: config has both `instruments:` and the old "
            f"{[k for k, _ in legacy]} section(s); keep only `instruments:`"
        )
    migrated = []
    for key, model in legacy:
        item = dict(raw.pop(key) or {})
        item.setdefault("model", model)
        item.setdefault("name", key)
        # `backend:` moved out of the transport and became `driver:`.
        transport = dict(item.get("transport") or {})
        backend = transport.pop("backend", None)
        if backend is not None:
            item.setdefault("driver", backend)
        if transport or "transport" in item:
            item["transport"] = transport
        migrated.append(item)
    raw["instruments"] = migrated
    log.warning(
        "%s: migrated the old %s section(s) into `instruments:`. Run "
        "`lschart init` for the current format -- this shim will not last.",
        path, ", ".join(k for k, _ in legacy),
    )


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
        _migrate_legacy_instruments(raw, path)
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
