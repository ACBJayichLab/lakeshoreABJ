"""Where the viewer's data comes from.  No Qt in this module, deliberately.

The strip chart needs two things that arrive by different routes:

**What the rig is doing now** -- from ``status.json``, which the recorder
rewrites every cycle.  That file carries link health, the sensor validity
flags, and command acknowledgements, none of which reach the CSV.

**What it has been doing** -- by tailing the CSV.  The recorder's own ring
buffer lives in the recorder's memory and the viewer is a different process, so
the only history available to it is the log.  Tailing rather than re-reading
means a viewer left open all week costs one seek and a few hundred bytes per
second, not a re-parse of a 90 MB file.

Why the viewer is a separate process
------------------------------------

Because the recorder is the thing that must stay up for months, and a Qt bug,
a wedged event loop or a closed laptop lid must not be able to take logging
with it.  As a separate process the viewer can be closed and reopened mid-run,
two people can watch at once, and it needs no privileges the recorder has --
it is exactly another client of the same file interface MATLAB uses.

The consequence to keep in mind: **everything here is a snapshot of a file that
another process is writing.**  A read can land mid-rewrite, a row can be half
flushed, and the recorder can roll over to a new file at midnight.  All three
are normal, and all three are handled here rather than being allowed to reach
the widgets as exceptions.
"""

from __future__ import annotations

import bisect
import csv
import datetime as _dt
import io
import logging
import os
from dataclasses import dataclass, field

from ..ipc.status import read_status, status_age_s

log = logging.getLogger(__name__)

#: Columns the recorder writes that are not measurements.  ``Time`` is in here
#: because it is numeric but is a clock: seconds since the file started, which
#: would otherwise accumulate as a perfectly straight "trace" climbing off the
#: top of the kelvin axis.
NON_SERIES_COLUMNS = ("Timestamp", "Time", "Validity", "State", "Notes")


def _parse_time(text: str) -> float | None:
    """The recorder's ISO stamp, as epoch seconds in local time."""
    try:
        return _dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


#: Auxiliary column suffixes that are a temperature, not an output.  A loop's
#: setpoint belongs on the kelvin axis beside the channel it is chasing; a
#: heater percent emphatically does not.
KELVIN_AUX_MARKERS = (".setpoint",)
PERCENT_AUX_MARKERS = (".heater", ".aout", "heater_pct")


def classify_column(name: str, channel_names) -> str:
    """Which axis a CSV column belongs on: ``kelvin``, ``percent`` or ``other``.

    Plotting a heater percent on a kelvin axis is not a cosmetic mistake -- it
    puts a 63 that means "63% of full scale" next to a 96 that means 96 K, and
    invites reading a trend across the two.  So the viewer separates them, and
    this is the one place that decides which is which.
    """
    if name in channel_names:
        return "kelvin"
    lowered = name.lower()
    if any(m in lowered for m in KELVIN_AUX_MARKERS):
        return "kelvin"
    if any(m in lowered for m in PERCENT_AUX_MARKERS):
        return "percent"
    return "other"


@dataclass
class Series:
    """One column's history, as two parallel lists ready for a plot."""

    name: str
    t: list[float] = field(default_factory=list)
    v: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.t)


class CsvTail:
    """Incremental reader for the recorder's CSV.

    Follows whichever file the recorder says it is writing, including across
    the daily rollover, and keeps at most ``max_points`` samples per column.
    """

    def __init__(self, path: str | None = None, *, max_points: int = 200_000) -> None:
        self.max_points = max_points
        self.path: str | None = None
        self.header: list[str] = []
        self.series: dict[str, Series] = {}
        self.rows = 0
        self.errors = 0
        self._offset = 0
        #: Bytes read that did not end in a newline.  The recorder flushes
        #: every sample, but a read can still land between the write and the
        #: flush, and half a row parses into a plausible wrong number.
        self._remainder = ""

    # -- following the file ------------------------------------------------

    def follow(self, path: str | None) -> bool:
        """Point at ``path``, restarting if it is a different file.

        Returns True if the history was reset.  The recorder rolls over at
        midnight and whenever it adopts a new channel, so a viewer left open
        overnight has to notice and start the new file from the top.
        """
        if not path or path == self.path:
            return False
        log.info("viewer: following %s", path)
        self.path = path
        self.header = []
        self.series = {}
        self.rows = 0
        self._offset = 0
        self._remainder = ""
        return True

    def poll(self) -> int:
        """Read whatever has been appended since last time.  Never raises."""
        if not self.path:
            return 0
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return 0
        if size < self._offset:
            # Truncated or replaced under us -- the only honest response is to
            # read it again from the beginning rather than to splice the new
            # contents onto the old history.
            log.warning("viewer: %s shrank; re-reading from the start", self.path)
            self._offset = 0
            self._remainder = ""
            self.series = {}
            self.header = []
            self.rows = 0
        if size == self._offset:
            return 0

        try:
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
                self._offset = fh.tell()
        except OSError as exc:
            log.debug("viewer: cannot read %s: %s", self.path, exc)
            return 0

        text = self._remainder + chunk
        # Keep the trailing fragment: a complete row always ends in a newline.
        if text.endswith("\n"):
            self._remainder = ""
        else:
            text, _, self._remainder = text.rpartition("\n")
        if not text:
            return 0
        return self._consume(text)

    def _consume(self, text: str) -> int:
        added = 0
        for row in csv.reader(io.StringIO(text)):
            if not row:
                continue
            if not self.header:
                # The first line of the file is its header.  A file that was
                # rolled mid-run has its own, which is exactly why the header
                # is taken from the file rather than from the config.
                if row[0] == "Timestamp":
                    self.header = row
                    continue
                log.warning("viewer: %s has no header row; ignoring it", self.path)
                self.header = []
                return 0
            added += self._row(row)
        return added

    def _row(self, row: list[str]) -> int:
        if len(row) < 2:
            return 0
        t = _parse_time(row[0])
        if t is None:
            self.errors += 1
            return 0
        for name, cell in zip(self.header[1:], row[1:]):
            if name in NON_SERIES_COLUMNS or cell == "":
                continue
            try:
                value = float(cell)
            except ValueError:
                continue
            s = self.series.get(name)
            if s is None:
                s = self.series[name] = Series(name)
            s.t.append(t)
            s.v.append(value)
            if len(s.t) > self.max_points:
                # Drop the oldest tenth at a time: trimming one sample per row
                # turns every append into an O(n) memmove once the cap is hit.
                cut = max(1, self.max_points // 10)
                del s.t[:cut]
                del s.v[:cut]
        self.rows += 1
        return 1

    # -- what the plot asks for -------------------------------------------

    def window(self, name: str, seconds: float | None) -> tuple[list[float], list[float]]:
        """One column over the last ``seconds``, or all of it for ``None``."""
        s = self.series.get(name)
        if s is None or not s.t:
            return [], []
        if seconds is None:
            return s.t, s.v
        cutoff = s.t[-1] - seconds
        # The series is in time order, so a scan from the end finds the first
        # index in the window without touching the rest of a day of samples.
        lo = len(s.t)
        while lo > 0 and s.t[lo - 1] >= cutoff:
            lo -= 1
        return s.t[lo:], s.v[lo:]

    def between(self, name: str, t0: float, t1: float) -> tuple[list[float], list[float]]:
        """One column between two absolute times, for a hand-picked window.

        One sample beyond each edge is included on purpose: a trace that
        crosses the edge of the window should be drawn leaving it, not stop
        short of the axis with a gap the data does not have.  That is also why
        a window narrower than the sample interval still returns the two
        samples that bracket it -- the line does cross the screen.

        A window that lies wholly before or after the log is empty, though:
        there the honest drawing is nothing at all, not the nearest sample
        dragged in from an hour away.
        """
        s = self.series.get(name)
        if s is None or not s.t:
            return [], []
        lo = bisect.bisect_left(s.t, t0)
        hi = bisect.bisect_right(s.t, t1)
        if lo >= hi and not 0 < lo < len(s.t):
            return [], []
        return s.t[max(0, lo - 1):hi + 1], s.v[max(0, lo - 1):hi + 1]

    def columns(self) -> list[str]:
        return [c for c in self.header[1:] if c not in NON_SERIES_COLUMNS]


class StatusSource:
    """Polls ``status.json`` and remembers enough to spot it going stale."""

    def __init__(self, path: str, *, stale_after_s: float | None = None) -> None:
        self.path = path
        self.stale_after_s = stale_after_s
        self.status: dict | None = None
        self.age_s: float | None = None
        #: True once a status file has ever been read.  Distinguishes "the
        #: recorder has not started" from "the recorder has stopped", which
        #: deserve different words on screen.
        self.ever_seen = False
        self.last_cycle = -1
        #: Cycles seen with no advance.  A clock step can make `age_s` lie in
        #: either direction; a cycle counter that stops moving cannot.
        self.stalled_polls = 0

    def poll(self) -> dict | None:
        status = read_status(self.path)
        if status is None:
            # Absent, or caught mid-replace.  Keep the last good one: blanking
            # the readouts because one read lost a race would make the display
            # flicker every time Windows is unlucky.  The age is recomputed
            # from the retained status rather than frozen, so a file that has
            # stopped being readable still goes stale on the banner instead of
            # sitting at whatever age it had when the reads started failing.
            if self.status is not None:
                self.age_s = status_age_s(self.status)
            return self.status
        self.ever_seen = True
        self.status = status
        self.age_s = status_age_s(status)
        cycle = int(status.get("cycle", -1))
        self.stalled_polls = 0 if cycle != self.last_cycle else self.stalled_polls + 1
        self.last_cycle = cycle
        return status

    @property
    def stale_limit_s(self) -> float:
        if self.stale_after_s is not None:
            return self.stale_after_s
        interval = float((self.status or {}).get("interval_s") or 1.0)
        # Three cycles: one slow cycle is routine, three in a row is not.
        return max(3 * interval, 5.0)

    def health(self) -> tuple[str, str]:
        """``(state, sentence)`` for the banner.  State is ok/stale/stopped/absent."""
        if self.status is None:
            return "absent", (
                f"no status file at {self.path} -- is the recorder running, "
                "and is ipc.enabled true in its config?"
            )
        if not self.status.get("running", True):
            return "stopped", "the recorder stopped cleanly; this is its last update"
        age = self.age_s or 0.0
        if age > self.stale_limit_s:
            return "stale", (
                f"status is {age:.0f} s old (limit {self.stale_limit_s:.0f} s): "
                "the recorder has hung or been killed"
            )
        return "ok", (
            f"cycle {self.status.get('cycle', 0)}, "
            f"{self.status.get('dropped_cycles', 0)} with errors"
        )

    # -- convenience projections ------------------------------------------

    def channels(self) -> list[dict]:
        return list((self.status or {}).get("channels", []) or [])

    def links(self) -> list[dict]:
        return list((self.status or {}).get("links", []) or [])

    def log_path(self) -> str | None:
        return ((self.status or {}).get("recorder") or {}).get("path") or None

    def ack_for(self, command_id: str) -> dict | None:
        for ack in ((self.status or {}).get("commands") or {}).get("recent", []):
            if ack.get("id") == command_id:
                return ack
        return None

    def accepts_commands(self) -> bool:
        return bool(((self.status or {}).get("commands") or {}).get("accepted"))
