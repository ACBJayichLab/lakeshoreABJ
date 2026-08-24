"""Common behaviour for the Lake Shore monitors/controllers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from ..model import Reading, ReadingStatus, Validity
from ..transport import Transport, TransportError

log = logging.getLogger(__name__)


class InstrumentError(RuntimeError):
    pass


def parse_float(text: str) -> float:
    """Lake Shore pads numbers (``+096.048``); ``float`` copes, blanks do not."""
    text = text.strip()
    if not text:
        raise ValueError("empty numeric response")
    return float(text)


def parse_float_list(text: str) -> list[float]:
    return [parse_float(p) for p in text.split(",") if p.strip()]


class Instrument(ABC):
    """A Lake Shore box exposing named temperature channels."""

    model: str = "?"

    def __init__(self, transport: Transport, *, name: str) -> None:
        self.transport = transport
        self.name = name
        self._idn: str | None = None

    # -- identity ---------------------------------------------------------

    def idn(self) -> str:
        if self._idn is None:
            self._idn = self.transport.query("*IDN?")
        return self._idn

    # -- reading ----------------------------------------------------------

    @abstractmethod
    def read_frame(self) -> tuple[dict[str, Reading], dict[str, float]]:
        """Return ``(readings_by_channel_name, auxiliary_scalars)``.

        Implementations must not raise for a *single* bad channel -- they mark
        that channel's :class:`Reading` instead.  Only a link-level failure
        (which invalidates the whole cycle) may raise :class:`TransportError`.
        """

    def _status(self, channel_id: str | int) -> ReadingStatus:
        """Decode ``RDGST?``.

        A malformed *reply* marks the channel invalid, but a
        :class:`TransportError` propagates: that is a link-level failure, and
        swallowing it here made a dead GPIB link present as eight simultaneous
        sensor faults instead of the comms error it actually is.
        """
        try:
            return ReadingStatus(int(parse_float(self.transport.query(f"RDGST? {channel_id}"))))
        except ValueError as exc:
            log.debug("%s: RDGST? %s unparseable: %s", self.name, channel_id, exc)
            return ReadingStatus.INVALID

    @staticmethod
    def _status_ok() -> ReadingStatus:
        return ReadingStatus.OK

    @staticmethod
    def _classify(kelvin: float, status: ReadingStatus) -> Validity:
        if status.is_fault:
            return Validity.INSTRUMENT_FAULT
        # A disconnected sensor reads a hard zero on both boxes; a real cryostat
        # channel never legitimately reports 0.000 K.
        if kelvin == 0.0:
            return Validity.NO_SENSOR
        return Validity.GOOD
