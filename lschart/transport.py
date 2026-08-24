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

    #: Minimum seconds after a WRITE before the next transaction.
    #:
    #: Lake Shore boxes apply a command asynchronously, so a query issued too
    #: soon after one overtakes it and answers with the PREVIOUS value.
    #: Measured on a 336 over USB: at 0 ms every readback was stale, and at
    #: 50 ms readbacks lagged by exactly one write -- both of which look like
    #: success while reporting fiction.  100 ms is comfortably past the
    #: observed threshold (~50-80 ms), and costs nothing in practice because
    #: writes are occasional while reads are not.
    #:
    #: This is a floor, not a guarantee.  The verification in the drivers is
    #: what makes correctness independent of it.
    write_settle_s: float = 0.1

    def __init__(
        self,
        *,
        read_only: bool = False,
        reconnect: bool = True,
        retry_min_s: float = 1.0,
        retry_max_s: float = 30.0,
        failures_before_reconnect: int = 3,
        clock=time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._last_txn = 0.0
        self._last_was_write = False
        self._clock = clock
        #: Hard interlock: no byte that could change instrument state leaves
        #: this transport.  Belt to `allow_writes`' braces, and deliberately at
        #: a *lower* layer -- `allow_writes` is a driver policy that a caller
        #: could flip, whereas this refuses at the point where bytes would
        #: actually go out, so a bug anywhere above it still cannot write.
        #: Set by `probe`, and by `read_only: true` on an instrument.
        self.read_only = read_only
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
        required = self.inter_command_delay
        if self._last_was_write:
            required = max(required, self.write_settle_s)
        gap = required - (self._clock() - self._last_txn)
        if gap > 0:
            time.sleep(gap)

    def write(self, cmd: str) -> None:
        if self.read_only:
            raise PermissionError(
                f"{self} is open READ-ONLY; refusing to send {cmd!r}. "
                "Nothing that could change instrument state may be written "
                "while this interlock is set."
            )
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
                self._last_was_write = True

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
                self._last_was_write = False

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

    #: The vendor driver logs *every* transaction at INFO -- two lines per
    #: query.  Measured on the bench 336: 1,114 lines in 60 s at 1 Hz, which is
    #: ~1.6 M lines a day, and this recorder is meant to run for months.  So it
    #: is quietened to WARNING unless someone has deliberately asked for DEBUG,
    #: where per-transaction traffic is exactly what you want to see.
    VENDOR_LOGGER = "lakeshore"

    def __init__(
        self,
        model: str,
        *,
        com_port: str | None = None,
        serial_number: str | None = None,
        ip_address: str | None = None,
        baud_rate: int = 57600,
        timeout_ms: int = 3000,
        inter_command_delay: float = 0.05,
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
            # NOT unconditional: the vendor classes disagree about this.
            # Model335.__init__ takes baud_rate as its first REQUIRED
            # positional argument; Model336.__init__ does not accept it at all
            # (the 336's USB rate is fixed internally) and passing it lands in
            # **kwargs, reaching the parent as a duplicate -- "got multiple
            # values for argument 'baud_rate'".  So the signature decides.
            self._kwargs["baud_rate"] = baud_rate
        self._inst = None
        self._quieten_vendor_logging()

    @classmethod
    def _quieten_vendor_logging(cls) -> None:
        vendor = logging.getLogger(cls.VENDOR_LOGGER)
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            return
        if vendor.level in (logging.NOTSET, logging.INFO):
            vendor.setLevel(logging.WARNING)

    def __str__(self) -> str:
        return f"Model{self.model} at {self.descriptor}"

    def _connect(self) -> None:
        import inspect

        import lakeshore

        cls = getattr(lakeshore, self._cls_name)
        # Keep only what this model's constructor actually names.  Every class
        # also declares **kwargs, so an unknown argument would not be rejected
        # here -- it would be forwarded to the parent and collide there, with a
        # message that names the argument but not the reason.
        accepted = {
            name for name, param in inspect.signature(cls.__init__).parameters.items()
            if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
            and name != "self"
        }
        kwargs = {k: v for k, v in self._kwargs.items() if k in accepted}
        dropped = sorted(set(self._kwargs) - set(kwargs))
        if dropped:
            log.debug("%s: %s does not accept %s; not passing it",
                      self, self._cls_name, ", ".join(dropped))
        self._inst = cls(**kwargs)
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
        # An in-process fake applies a write before the call returns, so there
        # is nothing to settle for.  Leaving the real instrument's 100 ms here
        # would put a real sleep into every simulated write -- which is most of
        # the test suite, and all of the virtual-clock control harness.
        self.write_settle_s = 0.0
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
