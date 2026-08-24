"""Bounded in-memory history, for plotting only.

Deliberately separate from the recorder.  The log is the record of record and
has no cap; this exists so the GUI can draw a strip chart without holding a
day of samples per curve.  Nothing here is ever the source of truth, and
nothing downstream should read it expecting completeness.
"""

from __future__ import annotations

import threading
from collections import deque

from ..model import Frame


class RingBuffer:
    """Fixed-length frame history with cheap per-channel series extraction."""

    def __init__(self, size: int = 43200) -> None:
        if size < 1:
            raise ValueError("ring buffer size must be positive")
        self.size = size
        self._frames: deque[Frame] = deque(maxlen=size)
        self._lock = threading.RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)

    @property
    def full(self) -> bool:
        with self._lock:
            return len(self._frames) == self.size

    def append(self, frame: Frame) -> None:
        with self._lock:
            self._frames.append(frame)

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def latest(self) -> Frame | None:
        with self._lock:
            return self._frames[-1] if self._frames else None

    def frames(self) -> list[Frame]:
        """Snapshot copy -- callers must not hold the lock while plotting."""
        with self._lock:
            return list(self._frames)

    def channels(self) -> list[str]:
        with self._lock:
            seen: dict[str, None] = {}
            for f in self._frames:
                for name in f.readings:
                    seen.setdefault(name, None)
            return list(seen)

    def series(self, channel: str, *, usable_only: bool = True) -> tuple[list[float], list[float]]:
        """``(t_mono, kelvin)`` for one channel.

        Rejected samples are dropped by default: plotting a glitch as if it were
        a measurement is how an operator ends up chasing a sensor fault.
        """
        ts: list[float] = []
        ks: list[float] = []
        with self._lock:
            for f in self._frames:
                r = f.readings.get(channel)
                if r is None:
                    continue
                if usable_only and not r.usable:
                    continue
                ts.append(f.t_mono)
                ks.append(r.kelvin)
        return ts, ks

    def aux_series(self, key: str) -> tuple[list[float], list[float]]:
        ts: list[float] = []
        vs: list[float] = []
        with self._lock:
            for f in self._frames:
                if key in f.aux:
                    ts.append(f.t_mono)
                    vs.append(f.aux[key])
        return ts, vs
