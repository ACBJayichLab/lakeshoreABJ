"""Lake Shore Model 218 eight-channel temperature monitor.

The 218 is a *monitor*: it has no heater loop.  On this cryostat its analog
output 1 is wired into an op-amp that drives the sample heater, so the 218 is
also the actuator -- driven in ANALOG "manual" mode, where the output percentage
is simply a number we set.

The exact command in use on this rig (verified against the chart-recorder log's
Notes column) is::

    ANALOG 1, 0, 2, 1, 1,1,1,<percent>

i.e. output 1, bipolar disabled, mode 2 (manual), input 1, source 1 (kelvin),
high 1, low 1.  Only the trailing manual value ever changes, so
:class:`AnalogOutputConfig` keeps the other fields byte-identical to the
known-good string rather than recomputing them.

Writing
-------

There is exactly one write on this box, and it *is* the heater.  A 33x
separates "where to go" (``SETP``, inert on its own) from "how much power you
may use" (``RANGE``, the act that applies it).  The 218 has no such separation:
one number goes out and the heater dissipates accordingly.  Every write here is
therefore the equivalent of a 33x heater-range change, which is why it carries
the same shape of gate:

``allow_writes``
    Driver policy, off by default, exactly as on
    :class:`~lschart.instruments.ls33x.LS33x`.  One layer below it,
    ``transport.read_only`` still refuses the bytes.

``max_output_pct``
    A blunt ceiling.  On the LTSPM rig the measured local gain is **~10 K per
    percent** near the operating point (``docs/ltspm/plant.md``), so a fat
    finger is worth tens of kelvin and "0 to 100 is a valid percentage" is not
    a useful bound.  The number is configuration; it is never a constant in
    here.

``verify_writes``
    The write is confirmed by reading ``AOUT?`` back, because these boxes apply
    a command asynchronously and an unverified readback can be a whole write
    behind -- measured on a 336 over USB, and never disproved on the 218 over
    GPIB.  Mind the readback's own granularity: the DAC steps 0.01% and
    ``AOUT?`` answers to two decimals, so this confirms the *code* to within
    ``readback_tol_pct``, not the exact float commanded.

A software PID driving this output every cycle should set ``verify_writes:
false`` and do its own confirmation -- ``HeaterSupervisor`` has
``verify_readback`` for exactly that, and paying for both would add a second
transaction plus a settle to every control cycle.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..model import Reading
from ..transport import Transport, TransportError
from .base import Instrument, InstrumentError, parse_float, parse_float_list

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnalogOutputConfig:
    """The fixed prefix of the ANALOG command, plus write formatting."""

    output: int = 1
    bipolar: int = 0          # 0 = positive only.  Keep 0: the op-amp is unipolar.
    mode: int = 2             # 2 = manual
    input: int = 1
    source: int = 1           # 1 = kelvin
    high_value: float = 1
    low_value: float = 1
    decimals: int = 3         # the rig's own log shows 3 d.p. in use (63.076)

    def command(self, percent: float) -> str:
        return (
            f"ANALOG {self.output}, {self.bipolar}, {self.mode}, {self.input}, "
            f"{self.source},{self.high_value:g},{self.low_value:g},"
            f"{percent:.{self.decimals}f}"
        )


class LS218(Instrument):
    model = "218"

    #: Inputs the 218 can carry, regardless of how many are populated.
    ALL_INPUTS = tuple(range(1, 9))

    def __init__(
        self,
        transport: Transport,
        *,
        name: str = "ls218",
        channels: dict[int, str] | None = None,
        analog: AnalogOutputConfig | None = None,
        read_status: bool = True,
        allow_writes: bool = False,
        max_output_pct: float = 100.0,
        verify_writes: bool = True,
        readback_tol_pct: float = 0.02,
    ) -> None:
        super().__init__(transport, name=name)
        # {input number: display name}.  Only these are read and logged.
        self.channels = dict(channels or {i: f"Input {i}" for i in self.ALL_INPUTS})
        self.analog = analog or AnalogOutputConfig()
        self.read_status = read_status
        self.allow_writes = allow_writes
        self.max_output_pct = float(max_output_pct)
        self.verify_writes = verify_writes
        self.readback_tol_pct = float(readback_tol_pct)

    # -- reading ----------------------------------------------------------

    def read_frame(self) -> tuple[dict[str, Reading], dict[str, float]]:
        """One ``KRDG? 0`` fetches all eight inputs -- far cheaper than 8 queries."""
        kelvin = parse_float_list(self.transport.query("KRDG? 0"))
        readings: dict[str, Reading] = {}
        for inp, label in sorted(self.channels.items()):
            if inp - 1 >= len(kelvin):
                continue
            k = kelvin[inp - 1]
            status = self._status(inp) if self.read_status else self._status_ok()
            readings[label] = Reading(
                channel=label,
                kelvin=k,
                status=status,
                validity=self._classify(k, status),
            )
        # Nice-to-have: a failed readback must not discard eight good
        # temperatures.  The supervisor reads the output itself when it needs
        # to trust it, and treats a failure there as a comms fault.
        aux: dict[str, float] = {}
        try:
            aux[f"{self.name}.aout{self.analog.output}"] = self.get_analog_percent()
        except (TransportError, ValueError) as exc:
            log.debug("%s: AOUT? failed: %s", self.name, exc)
        return readings, aux

    def read_sensor_units_all(self) -> list[float]:
        return parse_float_list(self.transport.query("SRDG? 0"))

    # -- the heater actuator ----------------------------------------------

    def get_analog_percent(self) -> float:
        """Read back what the box is actually outputting, in percent of full scale."""
        return parse_float(self.transport.query(f"AOUT? {self.analog.output}"))

    def _require_writes(self) -> None:
        if not self.allow_writes:
            raise PermissionError(
                f"{self.name} is configured read-only; set allow_writes: true on "
                f"this instrument to drive analog output {self.analog.output}. "
                f"Note that on this rig that output IS the sample heater"
            )

    def set_analog_percent(self, percent: float) -> str:
        """Set the manual analog output value.  Returns the command string sent.

        **This applies power.**  There is no inert half to a 218 analog write
        the way there is to a 33x setpoint, so the guards are here rather than
        somewhere polite further up: the gate, the ceiling, and a readback that
        turns "the box ignored me" into an exception instead of a confident
        report of a number it is not holding.

        What is deliberately *not* here is any notion of a safe operating
        point, a step limit, or a ramp.  Those are control policy, they belong
        to the supervisor, and duplicating them would give the rig two sets of
        limits that can disagree.
        """
        self._require_writes()
        percent = float(percent)
        if not 0.0 <= percent <= self.max_output_pct:
            raise ValueError(
                f"{self.name}: {percent:g}% is outside "
                f"[0, {self.max_output_pct:g}]% for analog output "
                f"{self.analog.output}; raise max_output_pct if this is intended"
            )
        try:
            previous = self.get_analog_percent()
        except (TransportError, ValueError):
            previous = None

        cmd = self.analog.command(percent)
        self.transport.write(cmd)
        got = self._confirm(percent, cmd)

        # WARNING, not INFO: "who moved the heater, and from what" is the first
        # question asked after a surprise, and it should be in the log at the
        # level an operator actually runs at.
        log.warning(
            "%s: %s  (%s%% -> %s%%%s)", self.name, cmd,
            "?" if previous is None else f"{previous:.3f}",
            "?" if got is None else f"{got:.3f}",
            "" if self.verify_writes else ", UNVERIFIED",
        )
        return cmd

    def _confirm(self, percent: float, cmd: str, *, attempts: int = 5,
                 pause_s: float = 0.1) -> float | None:
        """Read ``AOUT?`` back until it agrees, or say plainly that it never did.

        The tolerance is not slack for a sloppy instrument: the DAC quantises to
        its own step and ``AOUT?`` reports two decimals, so an exact float
        comparison would fail every single time on a write that worked.  What it
        still catches is the failure that matters -- a write that did not land
        at all, which leaves the readback a whole commanded step away.
        """
        if not self.verify_writes:
            return None
        got = None
        for attempt in range(attempts):
            try:
                got = self.get_analog_percent()
            except (TransportError, ValueError) as exc:
                log.debug("%s: AOUT? readback failed (attempt %d): %s",
                          self.name, attempt + 1, exc)
                got = None
            if got is not None and abs(got - percent) <= self.readback_tol_pct:
                return got
            time.sleep(pause_s)
        raise InstrumentError(
            f"{self.name}: analog output {self.analog.output} did not take. "
            f"Commanded {percent:g}% with {cmd!r}; the instrument still reports "
            f"{got!r}% after {attempts} readbacks. The write was NOT applied -- "
            f"do not assume the heater is where you asked for it"
        )

    def analog_off(self) -> str:
        """Command the analog output to zero.  The safe direction, always.

        Still needs ``allow_writes`` -- a box this program may not write to is
        one it may not write *zero* to either, because on a shared rig that
        output may be somebody else's.  What it does not need is the extra
        opt-in that *raising* the output needs.
        """
        return self.set_analog_percent(0.0)

    def analog_settings(self) -> list[float]:
        return parse_float_list(self.transport.query(f"ANALOG? {self.analog.output}"))
