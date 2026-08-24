"""Byte-level links to the instruments.

Every transport is a serialising point: a single :class:`threading.RLock` guards
each physical link so the acquisition thread and the control thread can share one
GPIB board without interleaving a write and somebody else's read.  Lake Shore
controllers also dislike being hammered, so a minimum inter-transaction gap is
enforced here rather than being sprinkled through the drivers.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class TransportError(IOError):
    """Raised for any failure to complete a transaction with an instrument."""


class Transport(ABC):
    """A serialised, terminator-aware command/response link."""

    #: Minimum seconds between the end of one transaction and the start of the next.
    inter_command_delay: float = 0.05

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_txn = 0.0

    def _pace(self) -> None:
        gap = self.inter_command_delay - (time.monotonic() - self._last_txn)
        if gap > 0:
            time.sleep(gap)

    def write(self, cmd: str) -> None:
        with self._lock:
            self._pace()
            try:
                self._write(cmd)
            finally:
                self._last_txn = time.monotonic()

    def query(self, cmd: str) -> str:
        with self._lock:
            self._pace()
            try:
                return self._query(cmd)
            finally:
                self._last_txn = time.monotonic()

    @abstractmethod
    def _write(self, cmd: str) -> None: ...

    @abstractmethod
    def _query(self, cmd: str) -> str: ...

    def close(self) -> None:  # pragma: no cover - trivial default
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class VisaTransport(Transport):
    """pyvisa link.  Works for GPIB (``GPIB0::12::INSTR``), serial and TCPIP alike."""

    def __init__(
        self,
        resource: str,
        *,
        timeout_ms: int = 3000,
        read_termination: str = "\r\n",
        write_termination: str = "\r\n",
        inter_command_delay: float = 0.05,
        visa_library: str = "",
        baud_rate: int | None = None,
        data_bits: int | None = None,
        parity: str | None = None,
    ) -> None:
        super().__init__()
        import pyvisa

        self.resource = resource
        self.inter_command_delay = inter_command_delay
        self._rm = pyvisa.ResourceManager(visa_library)
        self._inst = self._rm.open_resource(resource)
        self._inst.timeout = timeout_ms
        self._inst.read_termination = read_termination
        self._inst.write_termination = write_termination

        # Serial links need the Lake Shore framing (7O1 / 7E1); GPIB resources
        # simply do not expose these attributes.
        if baud_rate is not None and hasattr(self._inst, "baud_rate"):
            from pyvisa.constants import Parity, StopBits

            self._inst.baud_rate = baud_rate
            self._inst.data_bits = data_bits or 7
            self._inst.parity = {
                "odd": Parity.odd,
                "even": Parity.even,
                "none": Parity.none,
            }[(parity or "odd").lower()]
            self._inst.stop_bits = StopBits.one

        log.info("opened VISA resource %s", resource)

    def _write(self, cmd: str) -> None:
        try:
            self._inst.write(cmd)
        except Exception as exc:  # pyvisa raises a zoo of error types
            raise TransportError(f"write {cmd!r} to {self.resource} failed: {exc}") from exc

    def _query(self, cmd: str) -> str:
        try:
            return self._inst.query(cmd).strip()
        except Exception as exc:
            raise TransportError(f"query {cmd!r} to {self.resource} failed: {exc}") from exc

    def close(self) -> None:
        try:
            self._inst.close()
        finally:
            self._rm.close()


class LoopbackTransport(Transport):
    """Drives an in-process fake instrument.  Used by the simulator and the tests."""

    def __init__(self, device, *, inter_command_delay: float = 0.0) -> None:
        super().__init__()
        self.device = device
        self.inter_command_delay = inter_command_delay

    def _write(self, cmd: str) -> None:
        self.device.handle_write(cmd)

    def _query(self, cmd: str) -> str:
        return self.device.handle_query(cmd)
