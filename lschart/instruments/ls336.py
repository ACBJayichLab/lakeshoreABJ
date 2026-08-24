"""Lake Shore Model 336 cryogenic temperature controller.

Read-only by default.  On this cryostat the 336 runs its own closed loop
(loop 2 holds "THE CHONKE" at 290.6 K with heater 2 near full range), so this
software records it and stays out of the way.  Setpoint writes are gated behind
:attr:`allow_writes` so an accidental call cannot disturb a running loop.
"""

from __future__ import annotations

import logging

from ..model import Reading
from ..transport import Transport, TransportError
from .base import Instrument, parse_float, parse_float_list

log = logging.getLogger(__name__)


class LS336(Instrument):
    model = "336"

    ALL_INPUTS = ("A", "B", "C", "D")
    HEATER_LOOPS = (1, 2)
    ANALOG_OUTPUTS = (3, 4)

    def __init__(
        self,
        transport: Transport,
        *,
        name: str = "ls336",
        channels: dict[str, str] | None = None,
        read_status: bool = True,
        read_setpoints: bool = True,
        read_heaters: bool = True,
        read_analog_outputs: bool = False,
        allow_writes: bool = False,
    ) -> None:
        super().__init__(transport, name=name)
        # {input letter: display name}.  Defaults to the instrument's own labels.
        self.channels = dict(channels) if channels else {}
        self.read_status = read_status
        self.read_setpoints = read_setpoints
        self.read_heaters = read_heaters
        self.read_analog_outputs = read_analog_outputs
        self.allow_writes = allow_writes

    def discover_channel_names(self) -> dict[str, str]:
        """Pull the operator-assigned labels ("RAD SHIELD", "1st Stage", ...)."""
        names: dict[str, str] = {}
        for letter in self.ALL_INPUTS:
            try:
                label = self.transport.query(f"INNAME? {letter}").strip()
            except TransportError as exc:
                log.warning("%s: INNAME? %s failed: %s", self.name, letter, exc)
                label = ""
            names[letter] = label or f"Input {letter}"
        return names

    # -- reading ----------------------------------------------------------

    def read_frame(self) -> tuple[dict[str, Reading], dict[str, float]]:
        if not self.channels:
            self.channels = self.discover_channel_names()

        kelvin = parse_float_list(self.transport.query("KRDG? 0"))
        readings: dict[str, Reading] = {}
        for idx, letter in enumerate(self.ALL_INPUTS):
            if letter not in self.channels or idx >= len(kelvin):
                continue
            label = self.channels[letter]
            k = kelvin[idx]
            status = self._status(letter) if self.read_status else self._status_ok()
            readings[label] = Reading(
                channel=label,
                kelvin=k,
                status=status,
                validity=self._classify(k, status),
            )

        aux: dict[str, float] = {}
        if self.read_setpoints:
            for loop in (1, 2, 3, 4):
                self._try_aux(aux, f"{self.name}.setpoint{loop}", f"SETP? {loop}")
        if self.read_heaters:
            for loop in self.HEATER_LOOPS:
                self._try_aux(aux, f"{self.name}.heater{loop}", f"HTR? {loop}")
        if self.read_analog_outputs:
            for out in self.ANALOG_OUTPUTS:
                self._try_aux(aux, f"{self.name}.aout{out}", f"AOUT? {out}")
        return readings, aux

    def _try_aux(self, aux: dict[str, float], key: str, query: str) -> None:
        """Auxiliaries are nice-to-have; one that errors must not kill the frame."""
        try:
            aux[key] = parse_float(self.transport.query(query))
        except (TransportError, ValueError) as exc:
            log.debug("%s: %s failed: %s", self.name, query, exc)

    # -- writing (guarded) -------------------------------------------------

    def set_setpoint(self, loop: int, kelvin: float) -> None:
        if not self.allow_writes:
            raise PermissionError(
                f"{self.name} is configured read-only; set instruments.ls336.allow_writes "
                "to change loop setpoints"
            )
        self.transport.write(f"SETP {loop},{kelvin:.4f}")
        log.warning("%s: SETP %d,%.4f", self.name, loop, kelvin)

    def heater_range(self, loop: int) -> int:
        return int(parse_float(self.transport.query(f"RANGE? {loop}")))

    def pid(self, loop: int) -> tuple[float, float, float]:
        p, i, d = parse_float_list(self.transport.query(f"PID? {loop}"))
        return p, i, d
