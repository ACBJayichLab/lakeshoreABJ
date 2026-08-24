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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..model import Reading
from ..transport import Transport, TransportError
from .base import Instrument, parse_float, parse_float_list

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
    ) -> None:
        super().__init__(transport, name=name)
        # {input number: display name}.  Only these are read and logged.
        self.channels = dict(channels or {i: f"Input {i}" for i in self.ALL_INPUTS})
        self.analog = analog or AnalogOutputConfig()
        self.read_status = read_status

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

    def set_analog_percent(self, percent: float) -> str:
        """Set the manual analog output value.  Returns the command string sent.

        No clamping happens here -- policy lives in the supervisor.  This method
        is the last, dumbest step so that what gets logged is exactly what went
        onto the wire.
        """
        cmd = self.analog.command(percent)
        self.transport.write(cmd)
        log.info("%s: %s", self.name, cmd)
        return cmd

    def analog_settings(self) -> list[float]:
        return parse_float_list(self.transport.query(f"ANALOG? {self.analog.output}"))
