"""Byte-level links to the instruments, and staying connected to them.

Every transport is a serialising point: a single :class:`threading.RLock` guards
each physical link so the acquisition thread and the control thread can share one
GPIB board without interleaving a write and somebody else's read.  Lake Shore
controllers also dislike being hammered, so a minimum inter-transaction gap is
enforced here rather than being sprinkled through the drivers.

Reconnection
------------

A recorder is expected to run for months unattended, so *losing a link must not
be terminal*.  Links do drop: an instrument gets power-cycled, a GPIB cable is
nudged, and a USB device that re-enumerates comes back on a different COM port
under the same serial number.  Recovery therefore lives in this base class, so
every transport gets it and no driver has to think about it:

* **opening is lazy.**  Constructing a transport never touches hardware, so a
  recorder starts, records what it can, and keeps trying for the rest.  A rig
  that is half-powered-on at boot converges instead of failing.
* **a single failed transaction does not condemn the link.**  It takes
  ``failures_before_reconnect`` consecutive failures, because one GPIB timeout
  is usually a slow instrument rather than a dead bus, and tearing the
  connection down on every slow reply is its own kind of outage.
* **retries back off** from ``retry_min_s`` to ``retry_max_s``, so a box that
  is off for a weekend is retried every 30 s, not every 50 ms.
* **the state is observable.**  :attr:`is_up`, :attr:`last_error` and
  :attr:`consecutive_failures` are what the status file and the GUI report;
  a link that is down should be visible, not merely absent from the log.

Nothing here ever raises anything but :class:`TransportError`, and a link that
is down raises promptly rather than blocking a poll cycle waiting on a timeout
that has already been proven to expire.
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
    """A serialised, terminator-aware command/response link that reconnects."""

    #: Minimum seconds between the end of one transaction and the start of the next.
    inter_command_delay: float = 0.05

    def __init__(
        self,
        *,
        reconnect: bool = True,
        retry_min_s: float = 1.0,
        retry_max_s: float = 30.0,
        failures_before_reconnect: int = 3,
        clock=time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._last_txn = 0.0
        self._clock = clock
        self.reconnect = reconnect
        self.retry_min_s = retry_min_s
        self.retry_max_s = retry_max_s
        self.failures_before_reconnect = max(1, failures_before_reconnect)

        self._opened = False
        self._backoff = retry_min_s
        self._next_retry_at = 0.0
        self.consecutive_failures = 0
        self.last_error: str | None = None
        #: Counts full reconnections, so "this link has flapped 40 times today"
        #: is answerable.  A link that keeps coming back is a different problem
        #: from one that never does.
        self.reconnects = 0

    # -- connection state --------------------------------------------------

    @property
    def is_up(self) -> bool:
        return self._opened

    def _connect(self) -> None:
        """Open the underlying link.  Raises on failure."""

    def _disconnect(self) -> None:
        """Close it, best effort.  Must not raise."""

    def open(self) -> None:
        """Connect now if not already connected.  Idempotent.

        Called for its side effect by the first transaction; worth calling
        explicitly at startup so a misconfigured resource is reported then
        rather than one poll cycle later.
        """
        with self._lock:
            self._ensure_open()

    def _ensure_open(self) -> None:
        if self._opened:
            return
        now = self._clock()
        if not self.reconnect and self.last_error is not None:
            raise TransportError(f"{self} is down and reconnect is disabled: {self.last_error}")
        if now < self._next_retry_at:
            raise TransportError(
                f"{self} is down; next reconnect attempt in "
                f"{self._next_retry_at - now:.1f} s (last error: {self.last_error})"
            )
        try:
            self._connect()
        except Exception as exc:
            self.last_error = str(exc)
            self._next_retry_at = now + self._backoff
            # Widen the gap *after* scheduling, so the first retry is prompt.
            self._backoff = min(self._backoff * 2.0, self.retry_max_s)
            raise TransportError(f"could not open {self}: {exc}") from exc
        self._opened = True
        self._backoff = self.retry_min_s
        self._next_retry_at = 0.0
        self.consecutive_failures = 0
        if self.reconnects or self.last_error:
            self.reconnects += 1
            log.warning("%s: link re-established after %s", self, self.last_error)
        self.last_error = None

    def _mark_failure(self, exc: Exception) -> None:
        """One transaction failed.  Decide whether the link is actually gone."""
        self.consecutive_failures += 1
        self.last_error = str(exc)
        if self._opened and self.consecutive_failures >= self.failures_before_reconnect:
            log.warning(
                "%s: %d consecutive failures; dropping the link and reconnecting",
                self, self.consecutive_failures,
            )
            self._teardown()

    def _teardown(self) -> None:
        if self._opened:
            try:
                self._disconnect()
            except Exception:  # pragma: no cover - best effort
                pass
        self._opened = False
        self.reconnects += 1
        self._backoff = self.retry_min_s
        self._next_retry_at = self._clock() + self._backoff

    # -- transactions ------------------------------------------------------

    def _pace(self) -> None:
        gap = self.inter_command_delay - (self._clock() - self._last_txn)
        if gap > 0:
            time.sleep(gap)

    def write(self, cmd: str) -> None:
        with self._lock:
            self._ensure_open()
            self._pace()
            try:
                self._write(cmd)
            except Exception as exc:
                self._mark_failure(exc)
                raise TransportError(f"write {cmd!r} to {self} failed: {exc}") from exc
            else:
                self.consecutive_failures = 0
            finally:
                self._last_txn = self._clock()

    def query(self, cmd: str) -> str:
        with self._lock:
            self._ensure_open()
            self._pace()
            try:
                reply = self._query(cmd)
            except Exception as exc:
                self._mark_failure(exc)
                raise TransportError(f"query {cmd!r} to {self} failed: {exc}") from exc
            else:
                self.consecutive_failures = 0
                return reply
            finally:
                self._last_txn = self._clock()

    @abstractmethod
    def _write(self, cmd: str) -> None: ...

    @abstractmethod
    def _query(self, cmd: str) -> str: ...

    def close(self) -> None:
        with self._lock:
            if self._opened:
                try:
                    self._disconnect()
                except Exception:  # pragma: no cover - best effort
                    pass
            self._opened = False

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
        **kw,
    ) -> None:
        super().__init__(**kw)
        self.resource = resource
        self.inter_command_delay = inter_command_delay
        self.timeout_ms = timeout_ms
        self.read_termination = read_termination
        self.write_termination = write_termination
        self.visa_library = visa_library
        self.baud_rate = baud_rate
        self.data_bits = data_bits
        self.parity = parity
        self._rm = None
        self._inst = None

    def __str__(self) -> str:
        return f"VISA {self.resource}"

    def _connect(self) -> None:
        # Imported here, not at module scope, so a sim deployment runs on a
        # machine with no VISA runtime at all.
        import pyvisa

        self._rm = pyvisa.ResourceManager(self.visa_library)
        self._inst = self._rm.open_resource(self.resource)
        self._inst.timeout = self.timeout_ms
        self._inst.read_termination = self.read_termination
        self._inst.write_termination = self.write_termination

        # Serial links need the Lake Shore framing (7O1 / 7E1); GPIB resources
        # simply do not expose these attributes.
        if self.baud_rate is not None and hasattr(self._inst, "baud_rate"):
            from pyvisa.constants import Parity, StopBits

            self._inst.baud_rate = self.baud_rate
            self._inst.data_bits = self.data_bits or 7
            self._inst.parity = {
                "odd": Parity.odd,
                "even": Parity.even,
                "none": Parity.none,
            }[(self.parity or "odd").lower()]
            self._inst.stop_bits = StopBits.one

        log.info("opened VISA resource %s", self.resource)

    def _disconnect(self) -> None:
        for obj in (self._inst, self._rm):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # pragma: no cover - best effort
                pass
        self._inst = self._rm = None

    def _write(self, cmd: str) -> None:
        self._inst.write(cmd)

    def _query(self, cmd: str) -> str:
        return self._inst.query(cmd).strip()


class LakeshoreTransport(Transport):
    """Lake Shore's own driver, used purely as a connection.

    ``lakeshore.Model335(...)`` exposes ``command(str)`` and ``query(str)``,
    which is exactly this interface -- so the vendor package handles the part
    worth not rewriting (USB enumeration, the 7-O-1 serial framing, TCP) while
    the command set stays in :mod:`lschart.instruments.ls33x`, where the write
    gating and readback checks live.

    It speaks pyserial and raw TCP, never GPIB, so **no VISA runtime is
    needed** -- which is the whole reason to prefer it for a box on a COM port.

    Give it a ``serial_number`` where you can.  A USB device that re-enumerates
    -- after a power cycle, a replug, or a driver hiccup -- comes back on a
    *different* COM port under the same serial number, so a reconnection keyed
    to the port number fails exactly when it is most needed.  With no
    ``com_port`` the vendor driver scans for the instrument, which is what
    makes recovery from re-enumeration automatic.
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
        **kw,
    ) -> None:
        super().__init__(**kw)
        cls_name = self.MODELS.get(str(model))
        if cls_name is None:
            raise ValueError(
                f"the lakeshore driver has no class for model {model!r}; "
                f"known: {sorted(self.MODELS)}"
            )
        self.model = str(model)
        self._cls_name = cls_name
        self.inter_command_delay = inter_command_delay
        self.descriptor = ip_address or com_port or f"serial {serial_number}" or "scan"
        self._kwargs: dict = {"timeout": timeout_ms / 1000.0}
        if ip_address:
            self._kwargs["ip_address"] = ip_address
            self._kwargs["tcp_port"] = tcp_port
        else:
            self._kwargs["com_port"] = com_port
            self._kwargs["serial_number"] = serial_number
            self._kwargs["baud_rate"] = baud_rate
        self._inst = None

    def __str__(self) -> str:
        return f"Model{self.model} at {self.descriptor}"

    def _connect(self) -> None:
        import lakeshore

        self._inst = getattr(lakeshore, self._cls_name)(**self._kwargs)
        log.info("opened %s via the lakeshore driver", self)

    def _disconnect(self) -> None:
        try:
            if self._inst is not None:
                self._inst.disconnect_usb()
        except Exception:  # pragma: no cover - best effort on the way out
            pass
        self._inst = None

    def _write(self, cmd: str) -> None:
        self._inst.command(cmd)

    def _query(self, cmd: str) -> str:
        return self._inst.query(cmd).strip()


class LoopbackTransport(Transport):
    """Drives an in-process fake instrument.  Used by the simulator and the tests.

    There is no link to lose, so it is open from the start and reconnection is
    off: a simulated comms failure should surface as the error it is, not be
    smoothed over by a retry the real rig would not get either.
    """

    def __init__(self, device, *, inter_command_delay: float = 0.0, **kw) -> None:
        kw.setdefault("reconnect", False)
        super().__init__(**kw)
        self.device = device
        self.inter_command_delay = inter_command_delay
        self._opened = True

    def __str__(self) -> str:
        return f"loopback to {type(self.device).__name__}"

    def _connect(self) -> None:
        self._opened = True

    def _mark_failure(self, exc: Exception) -> None:
        # Never tear down: the fake is always there, and dropping it would turn
        # an injected one-cycle fault into a multi-cycle outage that the test
        # did not ask for.
        self.consecutive_failures += 1
        self.last_error = str(exc)

    def _write(self, cmd: str) -> None:
        self.device.handle_write(cmd)

    def _query(self, cmd: str) -> str:
        return self.device.handle_query(cmd)
