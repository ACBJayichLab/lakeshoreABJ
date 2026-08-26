"""Continuous CSV logging.  No artificial limits.

The legacy chart recorder wrote ``.xls``, which capped every file at 65,536
rows.  That is why the reference logs jump between 2 s and 20 s cadence: the
poll was slowed down to make a long run fit, and a single cooldown ended up
smeared across ``monitor1..monitor7``.  CSV has no such limit, so cadence can
be chosen for control quality and a run stays in one file per day.

Column order deliberately mirrors the legacy layout -- time first, then one
column per channel, ``Notes`` last -- so old analysis scripts keep working.
Two differences, both deliberate:

* time is written as **both** an ISO wall-clock stamp and seconds since the
  file started.  The legacy format had only the latter, which made stitching
  ``monitor1..7`` back together unnecessarily painful;
* the heater column records what the supervisor actually commanded, so the log
  is self-contained evidence of what the software did.

Every row is flushed.  A power cut on a cryostat should cost one sample, not an
hour of buffered data.
"""

from __future__ import annotations

import csv
import datetime as _dt
import logging
import os
import threading

from ..model import Frame

log = logging.getLogger(__name__)


class Recorder:
    """Append-only CSV writer with daily rollover.

    Thread-safe: the poller writes, the viewer may ask for :attr:`path`.
    """

    def __init__(
        self,
        directory: str = "data",
        *,
        prefix: str = "lschart",
        channels: list[str] | None = None,
        aux_keys: list[str] | None = None,
        flush_every_sample: bool = True,
        clock=None,
    ) -> None:
        self.directory = directory
        self.prefix = prefix
        self.channels = list(channels or [])
        self.aux_keys = list(aux_keys or [])
        self.flush_every_sample = flush_every_sample
        self._clock = clock or _dt.datetime.now

        self._lock = threading.RLock()
        self._fh = None
        self._writer: csv.writer | None = None
        self._day: _dt.date | None = None
        self._part = 0
        self._t0: float | None = None
        self.path: str | None = None
        self.rows_written = 0

    # -- lifecycle ---------------------------------------------------------

    def _header(self) -> list[str]:
        return (
            ["Timestamp", "Time"]
            + self.channels
            + self.aux_keys
            + ["Validity", "State", "Notes"]
        )

    def _open_for(self, day: _dt.date, *, part: int = 0) -> None:
        os.makedirs(self.directory, exist_ok=True)
        stem = f"{self.prefix}_{day.isoformat()}"
        if part:
            stem = f"{stem}_part{part + 1}"
        path = os.path.join(self.directory, f"{stem}.csv")
        exists = os.path.exists(path) and os.path.getsize(path) > 0
        if exists and self._header_of(path) != self._header():
            # Never append rows a file's own header cannot describe.
            return self._open_for(day, part=part + 1)
        # Line buffering plus an explicit flush: the point is that the file on
        # disk is always current, not that writes are cheap.
        self._fh = open(path, "a", newline="", buffering=1)
        self._writer = csv.writer(self._fh)
        if not exists:
            self._writer.writerow(self._header())
            self._fh.flush()
        self._day = day
        self._part = part
        self.path = path
        self._t0 = None
        log.info("recording to %s (%d columns)", path, len(self._header()))

    @staticmethod
    def _header_of(path: str) -> list[str]:
        try:
            with open(path, newline="") as fh:
                return next(csv.reader(fh))
        except (OSError, StopIteration):
            return []

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.flush()
                self._fh.close()
                self._fh = None
                self._writer = None

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False

    # -- writing -----------------------------------------------------------

    def write(self, frame: Frame, *, note: str = "", state: str = "") -> None:
        """Append one frame.  Never raises on a per-channel problem."""
        with self._lock:
            stamp = _dt.datetime.fromtimestamp(frame.t_wall)
            day = stamp.date()
            if self._writer is None or day != self._day:
                self.close()
                # Adopt this frame's channels before the header is committed.
                for name in frame.readings:
                    if name not in self.channels:
                        self.channels.append(name)
                self._open_for(day)

            # Channels the caller did not declare would otherwise be silently
            # dropped.  The 336's names arrive from INNAME? on the first read,
            # so this fires on cycle one in a normal run -- which is why the
            # file is not opened until the first frame is in hand.  If it fires
            # later, roll to a new part rather than appending rows the existing
            # header cannot describe.
            missing = [c for c in frame.readings if c not in self.channels]
            if missing:
                self.channels.extend(missing)
                log.info("recorder: adopting channels %s", missing)
                rolled = self.rows_written > 0
                self.close()
                self._open_for(day, part=(self._part + 1) if rolled else 0)

            # After every path that may have (re)opened a file: a new file
            # restarts its own relative clock at zero.
            if self._t0 is None:
                self._t0 = frame.t_mono

            row: list[object] = [stamp.isoformat(timespec="milliseconds"),
                                 f"{frame.t_mono - self._t0:.3f}"]
            bad = []
            for name in self.channels:
                r = frame.readings.get(name)
                if r is None:
                    row.append("")
                else:
                    row.append(f"{r.kelvin:.4f}")
                    if not r.validity.good:
                        bad.append(f"{name}:{r.validity.value}")
            for key in self.aux_keys:
                v = frame.aux.get(key)
                row.append("" if v is None else f"{v:.4f}")

            errors = "; ".join(f"{k}:{v}" for k, v in frame.errors.items())
            row.append("; ".join(bad))
            row.append(state)
            row.append("; ".join(x for x in (note, errors) if x))

            assert self._writer is not None
            self._writer.writerow(row)
            self.rows_written += 1
            if self.flush_every_sample and self._fh is not None:
                self._fh.flush()
