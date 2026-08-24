"""Shared value types.

These are deliberately plain and immutable: they cross thread boundaries
(acquisition -> control -> GUI -> recorder) and must never be mutated in flight.
"""

from __future__ import annotations

import enum
import math
import time
from dataclasses import dataclass, field, replace


class ReadingStatus(enum.IntFlag):
    """Lake Shore ``RDGST?`` bit weighting, shared by the 218 and the 336."""

    OK = 0
    INVALID = 1
    TEMP_UNDERRANGE = 16
    TEMP_OVERRANGE = 32
    UNITS_ZERO = 64
    UNITS_OVERRANGE = 128

    @property
    def is_fault(self) -> bool:
        return bool(self)


class Validity(enum.Enum):
    """Why the pipeline did or did not trust a sample."""

    GOOD = "good"
    NO_SENSOR = "no_sensor"          # channel not populated / reads zero
    INSTRUMENT_FAULT = "inst_fault"  # RDGST? reported a problem
    OUT_OF_RANGE = "out_of_range"    # outside the configured plausible band
    SLEW_REJECT = "slew_reject"      # changed faster than physically possible
    INCOHERENT = "incoherent"        # moved fast, but no other channel saw it
    SPIKE_REJECT = "spike_reject"    # robust outlier test failed
    COMMS_ERROR = "comms_error"      # the read never completed

    @property
    def good(self) -> bool:
        return self is Validity.GOOD


@dataclass(frozen=True, slots=True)
class Reading:
    """One channel, one instant."""

    channel: str
    kelvin: float
    status: ReadingStatus = ReadingStatus.OK
    validity: Validity = Validity.GOOD
    sensor_units: float | None = None

    @property
    def usable(self) -> bool:
        return self.validity.good and math.isfinite(self.kelvin)

    def rejected(self, why: Validity) -> "Reading":
        return replace(self, validity=why)


@dataclass(frozen=True, slots=True)
class Frame:
    """Everything read from every instrument in one poll cycle."""

    t_wall: float                      # time.time(), for the log's absolute clock
    t_mono: float                      # time.monotonic(), for all interval maths
    readings: dict[str, Reading] = field(default_factory=dict)
    aux: dict[str, float] = field(default_factory=dict)   # setpoints, heater %, ...
    errors: dict[str, str] = field(default_factory=dict)  # instrument -> message

    @classmethod
    def now(cls, **kw) -> "Frame":
        return cls(t_wall=time.time(), t_mono=time.monotonic(), **kw)

    def kelvin(self, channel: str) -> float | None:
        r = self.readings.get(channel)
        return r.kelvin if r is not None and r.usable else None
