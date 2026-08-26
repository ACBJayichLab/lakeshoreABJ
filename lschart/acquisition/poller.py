"""The acquisition thread: read every instrument, then feed everyone.

One thread owns the bus and drives the whole cycle::

    read instruments -> Frame -> ring buffer -> recorder -> supervisor

The supervisor is stepped from here rather than from its own thread, because
the control decision must be made against the frame that was just read.  Two
threads polling one GPIB board would also serialise on the transport lock
anyway, so a second thread would buy nothing but a harder concurrency story.

Failure policy follows the rule from CLAUDE.md: a per-channel problem marks
that channel's ``Reading`` and the cycle continues; only a link-level failure
loses the whole cycle.  A lost cycle is still delivered downstream -- as a
Frame carrying the error and no readings -- because the supervisor must *see*
the gap.  Silently skipping a cycle is how a stalled loop looks healthy.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from ..model import Frame, Reading
from ..transport import TransportError

log = logging.getLogger(__name__)


class Poller:
    """Fixed-cadence acquisition loop.

    ``instruments`` is a list of objects with ``read_frame()`` and ``name``.
    """

    def __init__(
        self,
        instruments: list,
        *,
        interval_s: float = 1.0,
        recorder=None,
        ringbuffer=None,
        supervisor=None,
        control_channel: str | None = None,
        log_every_n: int = 1,
        status_every_n_cycles: int = 0,
        on_frame: Callable[[Frame], None] | None = None,
        clock=time.monotonic,
    ) -> None:
        self.instruments = instruments
        self.interval_s = interval_s
        self.recorder = recorder
        self.ring = ringbuffer
        self.supervisor = supervisor
        self.control_channel = control_channel
        self.log_every_n = max(1, log_every_n)
        self.status_every_n_cycles = status_every_n_cycles
        self.on_frame = on_frame
        self.clock = clock

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.cycles = 0
        self.dropped_cycles = 0
        self.last_frame: Frame | None = None
        self.last_error: str | None = None
        #: The supervisor's last answer, kept so the status file can report the
        #: control loop without `lschart` knowing what a supervisor is.  Read
        #: duck-typed by `lschart.ipc.service`; stays None on a plain recorder.
        self.last_control_status = None

    # -- one cycle ---------------------------------------------------------

    def read_once(self) -> Frame:
        """Read every instrument into one Frame.  Never raises."""
        readings: dict[str, Reading] = {}
        aux: dict[str, float] = {}
        errors: dict[str, str] = {}

        want_status = (
            self.status_every_n_cycles > 0
            and self.cycles % self.status_every_n_cycles == 0
        )
        for inst in self.instruments:
            previous = getattr(inst, "read_status", None)
            try:
                if previous is not None and self.status_every_n_cycles > 0:
                    inst.read_status = want_status
                r, a = inst.read_frame()
                readings.update(r)
                aux.update(a)
            except (TransportError, ValueError, OSError) as exc:
                # Link-level: this instrument contributed nothing this cycle.
                errors[getattr(inst, "name", str(inst))] = str(exc)
                log.warning("%s: read failed: %s", getattr(inst, "name", inst), exc)
            finally:
                if previous is not None and self.status_every_n_cycles > 0:
                    inst.read_status = previous

        return Frame(t_wall=time.time(), t_mono=self.clock(),
                     readings=readings, aux=aux, errors=errors)

    def step(self) -> Frame:
        """Read, distribute, and step the control loop once."""
        frame = self.read_once()
        self.cycles += 1
        if frame.errors:
            self.dropped_cycles += 1
            self.last_error = "; ".join(f"{k}: {v}" for k, v in frame.errors.items())

        if self.ring is not None:
            self.ring.append(frame)

        state = ""
        note = ""
        if self.supervisor is not None and self.control_channel:
            # The supervisor sees every cycle including empty ones: a missing
            # reading is information, and swallowing it would let a dead link
            # look like a steady temperature.
            reading = frame.readings.get(self.control_channel)
            try:
                status = self.supervisor.step(frame.t_mono, reading, frame.readings)
                self.last_control_status = status
                state = status.state.value
                if status.wrote and status.output_pct is not None:
                    note = f"heater -> {status.output_pct:.3f}%"
                    frame.aux["heater_pct"] = status.output_pct
                elif status.output_pct is not None:
                    frame.aux["heater_pct"] = status.output_pct
                if status.alarms:
                    note = "; ".join([note, *status.alarms]).strip("; ")
            except Exception:  # pragma: no cover - a control bug must not stop logging
                log.exception("supervisor.step raised; acquisition continues")
                state = "error"

        if self.recorder is not None and self.cycles % self.log_every_n == 0:
            try:
                self.recorder.write(frame, note=note, state=state)
            except OSError:
                log.exception("recorder write failed; acquisition continues")

        self.last_frame = frame
        if self.on_frame is not None:
            try:
                self.on_frame(frame)
            except Exception:  # pragma: no cover - a viewer bug must not stop logging
                log.exception("on_frame callback raised")
        return frame

    # -- the thread --------------------------------------------------------

    def run(self) -> None:
        """Loop until stopped, holding cadence against a fixed schedule.

        The next deadline is computed from the previous *deadline*, not from
        the time the last cycle finished, so a slow cycle does not permanently
        retard the schedule.  If a cycle overruns badly the schedule is reset
        rather than trying to catch up in a burst.
        """
        next_at = self.clock()
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:  # pragma: no cover - never let the thread die
                log.exception("cycle failed")

            next_at += self.interval_s
            now = self.clock()
            delay = next_at - now
            if delay < -self.interval_s:
                log.warning(
                    "poll overran by %.2f s; resetting schedule", -delay
                )
                next_at = now
                delay = 0.0
            if delay > 0:
                self._stop.wait(delay)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("poller already started")
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="lschart-poller", daemon=True)
        self._thread.start()
        log.info("poller started at %.3f s cadence", self.interval_s)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():  # pragma: no cover
                log.error("poller thread did not stop within %.1f s", timeout)
            self._thread = None
        log.info("poller stopped after %d cycles (%d with errors)",
                 self.cycles, self.dropped_cycles)

    def __enter__(self) -> "Poller":
        self.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False
