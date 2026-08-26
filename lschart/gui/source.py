"""Where the viewer's data comes from.  No Qt in this module, deliberately.

The strip chart needs two things that arrive by different routes:

**What the cryostat is doing now** -- from ``status.json``, which the recorder
rewrites every cycle.  That file carries link health, the sensor validity
flags, and command acknowledgements, none of which reach the CSV.

**What it has been doing** -- by tailing the CSV.  The recorder's own ring
buffer lives in the recorder's memory and the viewer is a different process, so
the only history available to it is the log.  Tailing rather than re-reading
means a viewer left open all week costs one seek and a few hundred bytes per
second, not a re-parse of a 90 MB file -- but the *first* read of a day does
re-read, because a viewer that starts mid-day still owes the operator the
cooldown that ended at midnight.  So :class:`CsvTail` backfills from the
finished logs that came before the current one (same directory, same prefix,
older date), keeps whatever it already holds when the recorder rolls over at
midnight, and answers a zoom-out with everything it has ever seen rather than
only what is in today's file.

Thinning and un-thinning
------------------------

Months of samples cannot stay at full resolution in memory, so each series is
*decimated* in place once it outgrows ``max_points``: every other sample goes,
doubling the span the budget covers.  That would make a zoomed-in look at an
old day quietly wrong -- so every file consumed is remembered, and a
hand-picked span is **re-read from the logs themselves**
(:meth:`CsvTail.prepare_span`), at whatever resolution they hold.  The chart
first draws the thinned overview so the view responds instantly, then swaps in
the real samples a moment later.

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
import re
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
    the daily rollover.  History is kept across a rollover, and a viewer that
    starts fresh backfills from the finished logs that came before the one now
    being written -- so zooming out shows every sample the data directory
    holds, not only today's.  At most ``max_points`` samples per column are
    held; past that a series is decimated rather than truncated, and
    :meth:`prepare_span` recovers the full resolution from disk on demand.
    """

    def __init__(
        self,
        path: str | None = None,
        *,
        max_points: int = 200_000,
        backfill_s: float | None = None,
    ) -> None:
        self.max_points = max_points
        #: How much history a fresh start reads back from the finished logs.
        #:
        #: ``None`` -- the default, and what the tests mostly use -- takes
        #: everything.  The viewer passes its widest live-referenced window
        #: plus a margin instead: there is no reason to hold three weeks of
        #: samples nobody has asked for, and anything older than the cap
        #: remains reachable, at full resolution, by picking a span that
        #: reaches it (:meth:`prepare_span` goes back to disk either way).
        self._backfill_s = backfill_s
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
        #: Every log consumed, as ``(path, bytes-read)`` -- finished days from
        #: the backfill and rollovers, plus the current file at each rollover.
        #: This is what lets a hand-picked span be re-read at full resolution
        #: after thinning has thrown detail away.
        self._history: list[tuple[str, int | None]] = []
        #: Full-resolution samples for the last span given to
        #: :meth:`prepare_span`, keyed by column.  Replaced wholesale by the
        #: next prepare; bounded by the span, not by the age of the log.
        self._overlay: dict[str, Series] = {}
        self._overlay_span: tuple[float, float] | None = None

    # -- following the file ------------------------------------------------

    def follow(self, path: str | None) -> bool:
        """Point at ``path``, restarting the *file* if it is a different one.

        Returns True if the file changed.  The recorder rolls over at midnight
        and whenever it adopts a new channel, so a viewer left open overnight
        has to notice and start the new file from the top -- but starting the
        file is not starting the *history*: whatever has already been read
        stays on the chart, which is what makes a trace cross midnight
        without a gap.  Only a viewer with no history at all backfills from
        the older logs; re-reading files already tailed would duplicate every
        sample in them.
        """
        if not path or path == self.path:
            return False
        log.info("viewer: following %s", path)
        if not any(self.series.values()):
            self._backfill(path)
        elif self.path is not None:
            # The file being left is finished as far as this viewer is
            # concerned; remember it so a zoom back into its span can still be
            # answered at full resolution.
            self._history.append((self.path, self._offset))
        self.path = path
        self.header = []
        self._offset = 0
        self._remainder = ""
        return True

    # -- history from before this file -------------------------------------

    #: The recorder names logs ``{prefix}_{date}.csv`` (plus ``_partN`` when a
    #: channel is adopted mid-day).  A file that matches is a finished day of
    #: some run; the prefix keeps another experiment's logs out.
    _LOG_NAME = re.compile(
        r"^(?P<prefix>.+)_(?P<date>\d{4}-\d{2}-\d{2})(?:_part(?P<part>\d+))?\.csv$"
    )

    def _backfill(self, path: str) -> None:
        """Read finished logs from before ``path``, oldest first.

        Runs once, when the viewer first acquires a log to follow -- typically
        moments after being started, where a second or two of disk I/O costs
        nothing and buys yesterday's cooldown.  Each file carries its own
        header, because columns adopted later are absent from earlier days,
        and the series dict merges them by column name.

        Only enough files are read to cover ``backfill_s`` of wall clock (all
        of them when it is ``None``).  Which ones is decided by probing each
        file's first data row -- one line, not a re-parse -- walking newest to
        oldest until one starts before the cutoff; the chosen set is then
        read in chronological order, because the series must stay
        time-sorted for the bisections.
        """
        older = self._older_logs(path)
        if not older:
            return
        if self._backfill_s is not None:
            cutoff = _dt.datetime.now().timestamp() - self._backfill_s
            selected = []
            for p in reversed(older):
                selected.append(p)
                start = self._file_start(p)
                if start is not None and start <= cutoff:
                    break
            older = list(reversed(selected))
        if not older:
            return
        log.info("viewer: backfilling %d earlier log(s)", len(older))
        for p in older:
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                log.warning("viewer: cannot backfill %s: %s", p, exc)
                continue
            self.header = []             # each file states its own columns
            added = self._consume(text)
            if added:
                self._history.append((p, None))
                log.info("viewer: backfilled %d rows from %s", added, p)

    @staticmethod
    def _file_start(path: str) -> float | None:
        """The timestamp of a log's first data row, from its first two lines."""
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                fh.readline()            # the header
                first = fh.readline()
        except OSError:
            return None
        if not first:
            return None
        return _parse_time(first.split(",", 1)[0])

    @classmethod
    def _older_logs(cls, path: str) -> list[str]:
        """Finished logs in the same directory that predate ``path``, ordered."""
        folder, name = os.path.split(os.path.abspath(path))
        mine = cls._LOG_NAME.match(name)
        if mine is None:
            return []
        prefix = mine.group("prefix")
        key = (mine.group("date"), int(mine.group("part") or 0))
        try:
            entries = os.listdir(folder)
        except OSError:
            return []
        found = []
        for other in entries:
            m = cls._LOG_NAME.match(other)
            if m is None:
                continue
            # Same recorder only.  One directory routinely holds several --
            # `ls336_*.csv` beside `ltspm3-heater_*.csv` -- and they share no
            # columns, no cryostat and no business being spliced into one
            # history.  Comparing the prefix as part of an ordered tuple is
            # not the same test: it accepts every prefix that merely sorts
            # below this one, which is how a viewer following the heater log
            # came to backfill a 336 log that another recorder was still
            # writing.
            if m.group("prefix") != prefix:
                continue
            theirs = (m.group("date"), int(m.group("part") or 0))
            # Strictly earlier only: the current file belongs to poll(), and
            # reading it here too would double every sample of today so far.
            if theirs < key:
                found.append((theirs, os.path.join(folder, other)))
        return [p for _, p in sorted(found)]

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
            # contents onto the old history.  That means dropping every series
            # sample, earlier days included: there is no telling which of them
            # came from the bytes that were just rewritten.  Rare, and cheaper
            # than plotting a number twice.
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

    def _consume(self, text: str, *, sink=None, t_range=None) -> int:
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
            added += self._row(row, sink=sink, t_range=t_range)
        return added

    def _row(self, row: list[str], *, sink=None, t_range=None) -> int:
        if len(row) < 2:
            return 0
        t = _parse_time(row[0])
        if t is None:
            self.errors += 1
            return 0
        if t_range is not None and not (t_range[0] <= t <= t_range[1]):
            return 0
        if sink is None:
            sink = self.series
        for name, cell in zip(self.header[1:], row[1:]):
            if name in NON_SERIES_COLUMNS or cell == "":
                continue
            try:
                value = float(cell)
            except ValueError:
                continue
            s = sink.get(name)
            if s is None:
                s = sink[name] = Series(name)
            s.t.append(t)
            s.v.append(value)
            if sink is self.series and len(s.t) > self.max_points:
                # Decimate rather than amputate.  Dropping the oldest samples
                # would make zooming out quietly lose whole days -- the chart
                # would answer "show me everything" with "everything since
                # Tuesday".  Throwing out every other sample instead halves
                # the length in place, keeps the newest point, and costs an
                # amortised O(1) per append; each pass doubles the span a
                # fixed budget of points can hold, and prepare_span() buys
                # the detail back from disk when someone looks closely.
                del s.t[::2]
                del s.v[::2]
        self.rows += 1
        return 1

    # -- what the plot asks for -------------------------------------------

    def everything(self, name: str) -> tuple[list[float], list[float]]:
        """One column's whole retained history -- what the live view draws."""
        s = self.series.get(name)
        if s is None:
            return [], []
        return s.t, s.v

    def recent(self, name: str, seconds: float) -> tuple[list[float], list[float]]:
        """One column over the last ``seconds`` of what this viewer holds.

        The live-referenced view buttons draw through here: the newest sample
        is one edge, and each redraw rides forward with it.  The series is in
        time order, so a scan from the end finds the cut without touching a
        day of older samples.
        """
        s = self.series.get(name)
        if s is None or not s.t:
            return [], []
        cutoff = s.t[-1] - seconds
        lo = len(s.t)
        while lo > 0 and s.t[lo - 1] >= cutoff:
            lo -= 1
        return s.t[lo:], s.v[lo:]

    #: Rows kept either side of a requested span in a full-resolution reload,
    #: so a trace crossing the edge is drawn leaving it.  Wider than any sane
    #: sample interval; the exact bracketing sample is found by bisect later.
    SPAN_MARGIN_S = 300.0

    def prepare_span(self, t0: float, t1: float) -> int:
        """Re-read ``[t0, t1]`` from the logs on disk, at full resolution.

        The overview decimates as it goes, which keeps months of history
        affordable and close reading impossible in equal measure -- so a
        hand-picked span is answered from the files themselves rather than
        from whatever survived thinning.  Every log this viewer has consumed
        is scanned -- filenames are not trusted enough to skip any, a lesson
        the legacy .xls logs taught -- and rows outside the span are dropped
        by their timestamps as they parse.  Returns the number of rows
        recovered.
        """
        lo, hi = t0 - self.SPAN_MARGIN_S, t1 + self.SPAN_MARGIN_S
        # Every log this run has produced, whether or not this viewer has
        # read it yet -- a picked span may reach back past the backfill cap,
        # and the disk is where the full-resolution answer lives either way.
        # The history entries carry precise byte offsets for files already
        # tailed; discovered ones are read whole.  Current file last.
        sources: dict[str, int | None] = {}
        if self.path:
            for p in self._older_logs(self.path):
                sources[p] = None
        for p, upto in self._history:
            sources[p] = upto
        if self.path:
            sources[self.path] = self._offset
        overlay: dict[str, Series] = {}
        rows = 0
        for path, upto in sources.items():
            text = self._read_prefix(path, upto)
            if not text:
                continue
            # Scan it through the ordinary parser without touching the live
            # state, then fold what came out into the overlay.
            saved = (self.header, self.series, self.rows, self.errors)
            self.header, self.series, self.rows, self.errors = [], {}, 0, 0
            try:
                self._consume(text, sink=overlay, t_range=(lo, hi))
                rows += self.rows
            finally:
                (self.header, self.series, self.rows, self.errors) = saved
        self._overlay = overlay
        self._overlay_span = (t0, t1)
        return rows

    @staticmethod
    def _read_prefix(path: str, upto: int | None) -> str | None:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if upto is not None:
                    upto = min(upto, os.fstat(fh.fileno()).st_size)
                return fh.read(upto if upto is not None else -1)
        except OSError as exc:
            log.warning("viewer: cannot re-read %s: %s", path, exc)
            return None

    def between(self, name: str, t0: float, t1: float) -> tuple[list[float], list[float]]:
        """One column between two absolute times, for a hand-picked window.

        Full resolution when :meth:`prepare_span` has loaded this exact span,
        thinned overview otherwise (the window swaps one for the other a tick
        after the span settles).  Either way one sample beyond each edge is
        included on purpose: a trace that crosses the edge of the window
        should be drawn leaving it, not stop short of the axis with a gap the
        data does not have.  That is also why a window narrower than the
        sample interval still returns the two samples that bracket it -- the
        line does cross the screen.

        A window that lies wholly before or after the log is empty, though:
        there the honest drawing is nothing at all, not the nearest sample
        dragged in from an hour away.
        """
        source = self.series
        if self._overlay_span == (t0, t1):
            source = self._overlay
        s = source.get(name)
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

    def allows_heater_range(self) -> bool:
        """May a *file* raise a 33x heater range on this recorder?

        Not the same question as :meth:`accepts_commands`, and not a reason to
        disable a control either: lowering a range to 0 is always permitted, so
        a widget that greys itself out here would take away the one direction
        that is always available.  This is for saying so, not for refusing.
        """
        cmds = (self.status or {}).get("commands") or {}
        return bool(cmds.get("allow_heater_range"))

    def allows_analog_output(self) -> bool:
        """May a *file* drive a 218 analog output above 0?  Same caveat."""
        cmds = (self.status or {}).get("commands") or {}
        return bool(cmds.get("allow_analog_output"))

    def writable_links(self) -> list[dict]:
        """The instruments a command could actually reach, in order."""
        return [ln for ln in self.links() if ln.get("writable")]

    def link_named(self, name: str) -> dict:
        for link in self.links():
            if str(link.get("name", "")) == name:
                return link
        return {}


def capabilities(link: dict) -> dict:
    """What controls make sense for one instrument, from its status entry.

    A separate function rather than a method because it is pure and is the
    thing worth testing: given what the recorder said about a box, which
    controls should exist and what should their limits be.

    Defaults are chosen so an *older* recorder -- one whose status file predates
    the capability block -- degrades to the previous behaviour (a 1..4 loop
    spinner, no analog control) rather than to a window with nothing in it.
    """
    loops = [int(n) for n in link.get("loops") or ()]
    heaters = [int(n) for n in link.get("heater_outputs") or ()]
    analog = link.get("analog_output")
    known = ("loops" in link) or ("analog_output" in link)
    if not known:
        loops, heaters = [1, 2, 3, 4], [1, 2]
    return {
        "loops": loops,
        "heater_outputs": heaters,
        "analog_output": None if analog is None else int(analog),
        "max_output_pct": float(link.get("max_output_pct") or 100.0),
        "has_loops": bool(loops),
        "has_heater_range": bool(heaters),
        "has_analog": analog is not None,
    }
