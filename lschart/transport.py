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


class LakeshoreTransport(Transport):
    """Lake Shore's own driver, used purely as a connection.

    ``lakeshore.Model335(...)`` exposes ``command(str)`` and ``query(str)``,
    which is exactly this interface -- so the vendor package handles the part
    worth not rewriting (USB enumeration, the 7-O-1 serial framing, TCP) while
    the command set stays in :mod:`lschart.instruments.ls33x`, where the
    write gating and readback checks live.

    It speaks pyserial and raw TCP, never GPIB, so **no VISA runtime is
    needed** -- which is the whole reason to prefer it for a box on a COM port.
    Connect by ``serial_number`` rather than ``com_port`` where you can: a USB
    device that re-enumerates comes back on a different port number but the
    same serial, and that is the difference between a logger that survives a
    replug and one that needs a human.
    """

    #: Model name -> the vendor driver class that speaks to it.
    MODELS = {"335": "Model335", "336": "Model336", "224": "Model224", "240": "Model240"}

    def __init__(
        self,
        model: str,
        *,
        com_port: str | None = None,
        serial_number: str | None = None,
        ip_address: str | None = None,
        baud_rate: int = 57600,
        timeout_ms: int = 3000,
        inter_command_delay: float = 0.0,
        tcp_port: int = 7777,
    ) -> None:
        super().__init__()
        import lakeshore

        cls_name = self.MODELS.get(str(model))
        if cls_name is None:
            raise ValueError(
                f"the lakeshore driver has no class for model {model!r}; "
                f"known: {sorted(self.MODELS)}"
            )
        self.model = str(model)
        self.inter_command_delay = inter_command_delay
        self.descriptor = ip_address or com_port or f"serial {serial_number}"
        kwargs: dict = {"timeout": timeout_ms / 1000.0}
        if ip_address:
            kwargs["ip_address"] = ip_address
            kwargs["tcp_port"] = tcp_port
        else:
            # The vendor driver scans for the instrument when com_port is None,
            # which is what makes serial_number-only connection work.
            kwargs["com_port"] = com_port
            kwargs["serial_number"] = serial_number
            kwargs["baud_rate"] = baud_rate
        try:
            self._inst = getattr(lakeshore, cls_name)(**kwargs)
        except Exception as exc:
            raise TransportError(
                f"could not open Model{self.model} at {self.descriptor}: {exc}"
            ) from exc
        log.info("opened Model%s via the lakeshore driver at %s",
                 self.model, self.descriptor)

    def _write(self, cmd: str) -> None:
        try:
            self._inst.command(cmd)
        except Exception as exc:
            # The vendor driver raises InstrumentException, socket.timeout and
            # pyserial errors; all of them mean the same thing to us.
            raise TransportError(
                f"write {cmd!r} to Model{self.model} at {self.descriptor} failed: {exc}"
            ) from exc

    def _query(self, cmd: str) -> str:
        try:
            return self._inst.query(cmd).strip()
        except Exception as exc:
            raise TransportError(
                f"query {cmd!r} to Model{self.model} at {self.descriptor} failed: {exc}"
            ) from exc

    def close(self) -> None:
        try:
            self._inst.disconnect_usb()
        except Exception:  # pragma: no cover - best effort on the way out
            pass


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
