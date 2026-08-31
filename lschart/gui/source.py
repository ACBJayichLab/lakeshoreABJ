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

That re-read is bounded twice over, because a cryostat logs for months and
both bounds were learned the hard way.  It **skips any log whose own first and
last rows lie outside the span** -- without that the cost was every byte ever
recorded, and a one-hour zoom went from under a second to over ten as the
archive grew.  And past ``CsvTail.SPAN_READ_BUDGET_BYTES`` it **reads the span
at a stride** -- one row in n, chosen so the parse stays bounded -- because a
span covering the whole experiment covers the whole archive however cleverly
the files are chosen.

The second bound must never turn into *not reading the span at all*, and the
first version of it did exactly that: it refused, on the understanding that
the overview would answer instead.  The overview is not a thinned picture of
every log.  It is the last ``backfill_s`` of them, two days by default, and a
span reaching further back fell between the two bounds and was drawn as
nothing at all -- a Friday that was sitting on the disk, missing from a chart
whose status bar said it was showing an overview.  A stride costs resolution,
which is visible and is said out loud.  Refusing cost the day.

What the budget still does *not* do is cap a span that fits.  Decimating an
overlay drops samples inside the span as readily as outside, so a narrow
window came back thinned and the one promise this path exists to keep -- a
picked span is answered from the log, whole -- quietly stopped holding.  A
span that fits the budget comes back whole; only a span that cannot be read
whole is strided, and :meth:`CsvTail.overlay_is_full_resolution` says which
happened.

Measuring a region
------------------

Two cursors on the chart ask a question about the *log*, not about the
drawing: the mean, spread and change of every trace between them.  Answering
it from what is on screen would answer it from whatever survived decimation,
so :meth:`CsvTail.samples_in` gets the samples at full resolution -- from
memory while nothing has been thinned, from the files once something has --
and :func:`region_stats` reduces them.  :func:`write_region_csv` writes the
same samples out.  All three are here rather than in the window because they
are arithmetic on the log, and because this is the module that has no Qt in
it and can therefore be tested without one.

Holes
-----

A log has holes in it -- the recorder was stopped, the machine rebooted, the
lid was closed -- and joining across one with a straight line invents a
temperature history at exactly the place nothing can contradict it.
:func:`connect_flags` is where that is decided, and it is the one part of this
module that looks at the *spacing* of the samples rather than their values.

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
import math
import os
import re
from dataclasses import dataclass, field

import numpy as np

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


#: How many times the sample interval a step between two samples must exceed
#: before the chart draws a gap instead of a line.  A recorder that missed a
#: cycle or two -- a retry on a jittering bus, a slow instrument -- is still
#: recording, and joining across it is the honest drawing.  A recorder that
#: was *off* is not, and a straight line across the hour it was off is an
#: hour of temperature the cryostat never had.  4 is the first multiple that
#: is unambiguously the second case: it needs three consecutive cycles gone.
GAP_FACTOR = 4.0

#: When a loop counts as pinned at its rail.  Fixed rather than configurable
#: and not per loop: "the output has run out of authority" is the same fact on
#: every heater, and a per-loop knob here would be a knob whose only use is
#: turning the warning off.  Both ends, because a loop stuck at zero has run
#: out of authority in the other direction.
SATURATED_HIGH_PCT = 99.0
SATURATED_LOW_PCT = 1.0


#: Where a viewer's value axis stops when the data does not ask for more:
#: ``(floor, ceiling)`` for the temperature panel and for the output panel.
#: Zoom and pan are held inside these unless a sample lies outside them, in
#: which case the stop widens to the data -- a comfort stop, not a clamp.  A
#: miswired sensor reading 1400 K is exactly the reading somebody has to be
#: able to look at, and an axis that refused to go there would be hiding the
#: measurement in favour of a number this file guessed.
#:
#: Here rather than in ``window`` because ``lschart.gui.__main__`` builds its
#: ``--max-kelvin`` / ``--max-percent`` defaults from them, and it has to be
#: able to print its help on a machine with no Qt installed.
COMFORT_STOP_K = (0.0, 350.0)
COMFORT_STOP_PCT = (0.0, 100.0)


#: Intervals actually looked at when estimating a series' spacing.  The
#: spacing of a series is uniform (one recorder, one interval, and decimation
#: applies to the whole of it), so a few thousand of them settle the median as
#: well as two hundred thousand would, and do it in constant time on a redraw
#: that happens every second.
_SPACING_SAMPLE = 4096


def connect_flags(t, *, factor: float = GAP_FACTOR):
    """Which of ``t``'s samples should be joined to the next by a line.

    Returns pyqtgraph's ``"all"`` when every sample follows on from the one
    before it, and a 0/1 array marking the breaks when some do not -- the
    literal ``connect=`` argument ``setData`` takes.  An array rather than
    NaNs punched into the values because a NaN would have to be spliced into
    a copy of two 200 000-element lists on every redraw, and because a break
    expressed this way leaves the autoscale looking at the real data.

    The threshold is a multiple of the series' own median interval, not a
    number of seconds, and that is the whole point: the same series is drawn
    at full resolution when a span is picked and decimated by 2, 4, 16 when it
    is not, so a fixed threshold would either break a decimated overview into
    confetti or miss every gap in a fresh one.  Deriving it from the samples
    in hand also survives a log written at a different interval, or a
    recorder whose interval was changed between one file and the next.

    Below three samples there is no spacing to have an opinion about -- one
    interval cannot be compared with anything -- so the line is drawn.
    """
    n = len(t)
    if n < 3:
        return "all"
    steps = np.diff(np.asarray(t, dtype=float))
    stride = max(1, steps.size // _SPACING_SAMPLE)
    sample = steps[::stride]
    # Positive only: a clock step or two rows sharing a timestamp would drag
    # the median to zero, and a zero nominal interval would call every step a
    # gap and draw the trace as dust.
    sample = sample[sample > 0.0]
    if sample.size == 0:
        return "all"
    nominal = float(np.median(sample))
    if nominal <= 0.0:
        return "all"
    breaks = steps > factor * nominal
    if not breaks.any():
        return "all"
    flags = np.ones(n, dtype=np.uint8)
    flags[:-1][breaks] = 0
    return flags


@dataclass
class Series:
    """One column's history, as two parallel lists ready for a plot."""

    name: str
    t: list[float] = field(default_factory=list)
    v: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.t)


class _StrideCounter:
    """Keeps one row in ``stride`` across a whole scan of several logs.

    A counter rather than a slice because the rows arrive file by file and
    the spacing has to be even across the joins: restarting the count at each
    midnight rollover would put two kept rows next to each other there and
    nowhere else, which draws as a kink in a trace that has none.

    ``stride`` of 1 keeps everything and is the ordinary case; the object
    still exists then so the caller has one code path.
    """

    __slots__ = ("stride", "_n")

    def __init__(self, stride: int = 1) -> None:
        self.stride = max(1, int(stride))
        self._n = 0

    def take(self) -> bool:
        """True if the next row is one of the ones being kept."""
        if self.stride == 1:
            return True
        keep = self._n % self.stride == 0
        self._n += 1
        return keep


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
        #: Rows kept per row read when the overlay was built: 1 when the span
        #: was read whole, higher when it was too wide to be and had to be
        #: strided.  What tells a full-resolution overlay from a thinned one,
        #: since both are simply "the overlay" to everything that draws.
        self._overlay_stride: int = 1
        #: Mean bytes per data row, measured from whatever has been read.
        #: A row is one number per channel, so a 218 with eight inputs and a
        #: 336 with four write rows of very different lengths and no constant
        #: here could be right for both.  Only ever used to turn a byte
        #: budget into a row count, so a rough figure is a good one; the
        #: default stands in until the first chunk has been consumed.
        self._row_bytes: float = 200.0
        #: ``(path, bytes) -> (first, last)`` timestamps, so deciding whether a
        #: log can hold any of a span costs two short reads per file once
        #: rather than a parse per zoom.  Keyed by the byte extent read, which
        #: is what makes it self-invalidating: a finished file's entry stands
        #: for good, and the file still being appended to re-probes as it grows.
        self._span_cache: dict[tuple[str, int | None], tuple[float, float] | None] = {}
        #: True once any series has been decimated.  Until it is, what is held
        #: in memory *is* the full resolution of the logs read so far, and a
        #: question that needs every sample -- cursor statistics, an export --
        #: can be answered from memory instead of from disk.  It never goes
        #: back to False: a series thinned once has lost those samples for
        #: good, and the file is the only place left holding them.
        self.thinned = False

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
                self._row_bytes = len(text) / added
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

    #: How much of a log's tail to read looking for its last complete row.
    #: A row of this recorder is a few hundred bytes; 64 KiB is hundreds of
    #: them, and the read is one seek rather than a scan of the file.
    _TAIL_PROBE_BYTES = 65536

    def _file_span(self, path: str, upto: int | None = None
                   ) -> tuple[float, float] | None:
        """The first and last timestamps in ``path``, without parsing it.

        Two short reads -- the head for the first data row, the tail for the
        last -- which is what lets :meth:`_read_span` skip a log entirely
        instead of reading every byte of every day ever recorded to find the
        hour somebody zoomed into.  That scan is O(everything logged) and a
        cryostat that runs for months logs a great deal.

        This reads the *rows*, not the filename.  The distinction matters:
        the reason the scan trusted nothing was that filenames lie -- a lesson
        the legacy .xls logs taught, and `tools/import_xls.py` still carries.
        A timestamp read out of the file is evidence of the same kind the
        parse would have produced, just two lines of it instead of a day's.

        ``None`` when the extent cannot be established, and the caller then
        reads the file: refusing to guess is what keeps a skip from ever
        losing a sample that was really there.
        """
        key = (path, upto)
        if key in self._span_cache:
            return self._span_cache[key]
        span = self._probe_span(path, upto)
        self._span_cache[key] = span
        return span

    @classmethod
    def _probe_span(cls, path: str, upto: int | None
                    ) -> tuple[float, float] | None:
        first = cls._file_start(path)
        if first is None:
            return None
        try:
            with open(path, "rb") as fh:
                end = os.fstat(fh.fileno()).st_size
                if upto is not None:
                    end = min(upto, end)
                start = max(0, end - cls._TAIL_PROBE_BYTES)
                fh.seek(start)
                blob = fh.read(end - start)
        except OSError:
            return None
        # From the end backwards: the final line may be a half-flushed row
        # and, when the probe window began mid-file, the first may be a
        # fragment.  The first line that yields a timestamp is the answer.
        for raw in reversed(blob.split(b"\n")):
            stamp = raw.split(b",", 1)[0].decode("utf-8", "replace").strip()
            last = _parse_time(stamp)
            if last is not None:
                return (first, last) if last >= first else (last, first)
        return None

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
            self.thinned = False
            # The overlay was folded forward from bytes that are no longer
            # there.  Extending it with the re-read would count them twice,
            # so it goes; the window asks for the span again and gets a
            # clean one.
            self._overlay = {}
            self._overlay_span = None
            self._overlay_stride = 1
            # The file was rewritten under us, so what was probed about its
            # extent describes bytes that are gone.
            self._span_cache = {}
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
        added = self._consume(text)
        if added:
            self._row_bytes = len(text) / added
            self._extend_overlay(text)
        return added

    def _extend_overlay(self, text: str) -> None:
        """Fold newly-tailed rows into a loaded overlay.

        A hand-picked span that reaches the live edge keeps growing, and the
        overlay :meth:`prepare_span` built for it was a snapshot of the file
        as it stood.  Leaving it alone means `between` keeps answering from
        that snapshot and the newest samples never appear -- a fixed zoom
        window frozen at the moment it was drawn.

        Dropping the overlay instead would be worse than it looks: nothing
        would rebuild it until the span changed, so the view would fall back
        to the decimated overview and silently lose the resolution the span
        was picked to see.  Rebuilding it every tick is not the answer
        either, because `prepare_span` rescans every log on disk and the
        live edge grows once a cycle.

        So the rows that were just appended to the live series are folded
        into the overlay as well, bounded by the span it was built for.
        They arrive newest-last, which is the order the overlay is already
        in, and they cost no disk I/O because they have just been read.
        """
        if self._overlay_span is None:
            return
        t0, t1 = self._overlay_span
        # The same margin prepare_span read, so the overlay keeps bracketing
        # its span the way `between` expects.
        window = (t0 - self.SPAN_MARGIN_S, t1 + self.SPAN_MARGIN_S)
        # _consume keeps its tallies on self; the live series has already
        # counted these rows and must not count them twice.
        saved = (self.rows, self.errors)
        try:
            self._consume(text, sink=self._overlay, t_range=window)
        finally:
            (self.rows, self.errors) = saved

    def _consume(self, text: str, *, sink=None, t_range=None,
                 stride: "_StrideCounter | None" = None) -> int:
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
            # The skip goes here rather than inside `_row`, and the rows are
            # still walked by the csv reader rather than by a line split: a
            # Notes cell carrying a driver's error message can hold a quoted
            # newline, and a split would turn that one row into two malformed
            # ones.  What a skipped row saves is the part that actually costs
            # -- a float() per channel and an append into two dozen columns.
            if stride is not None and not stride.take():
                continue
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
                self.thinned = True
        self.rows += 1
        return 1

    # -- what the plot asks for -------------------------------------------

    def everything(self, name: str) -> tuple[list[float], list[float]]:
        """One column's whole retained history -- what the live view draws."""
        s = self.series.get(name)
        if s is None:
            return [], []
        return s.t, s.v

    def newest(self) -> float | None:
        """The time of the newest sample this viewer holds, over all columns.

        ``None`` before anything has been read.  Used to tell a region that is
        finished from one that is still filling: a cursor pair whose right
        edge sits beyond this is a region the recorder has not caught up with
        yet, and its statistics will change.
        """
        latest = None
        for s in self.series.values():
            if s.t and (latest is None or s.t[-1] > latest):
                latest = s.t[-1]
        return latest

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

    #: How many bytes of log a single full-resolution re-read may parse.
    #:
    #: Skipping logs that cannot hold any of the span made a zoom cost the
    #: span rather than the whole history, which is the right complexity but
    #: not a bound: a span that genuinely covers three months genuinely covers
    #: every byte of them, and parsing those on the GUI thread is a minute the
    #: viewer spends not responding.  Past this budget the overview is drawn
    #: instead and the status bar says so, which is the same bargain the
    #: overview already makes -- a picture of the log rather than the log --
    #: only now it is stated rather than merely slow.
    #:
    #: This is the *only* bound on a re-read, deliberately.  Capping the
    #: overlay by points as well looked reasonable and was not: decimating it
    #: throws away samples inside the span as readily as outside, so a narrow
    #: window came back thinned and the promise this method exists to keep --
    #: that a picked span is answered from the log, whole -- quietly stopped
    #: holding.  Bounding the bytes read bounds the work without ever
    #: degrading an answer that is given at all.
    #:
    #: 32 MiB is a bit over three days of this recorder's 2 s logging, and
    #: about 1.8 s at the measured rate of roughly 55 ms/MB for rows that
    #: land inside the span.  Rows outside one are four times cheaper -- they
    #: are parsed and dropped rather than appended to two dozen columns --
    #: which is why the budget counts bytes offered rather than bytes kept.
    SPAN_READ_BUDGET_BYTES = 32 * 1024 * 1024

    #: Rows a span read at a stride aims to come back with, per column.
    #:
    #: The byte budget alone picks a stride far too gentle to matter.  Most
    #: of the cost of a scan is walking the csv, which a stride cannot avoid,
    #: so doubling the stride does not halve the time -- measured over the
    #: whole archive, stride 1 to 16 went 2.74 s -> 1.91 -> 1.16 -> 0.87 ->
    #: 0.73, an asymptote at about a quarter.  What a bigger stride does buy
    #: is everything downstream: a tenth of the samples to hold, to slice on
    #: every redraw, to scan for gaps and to stroke.
    #:
    #: 20 000 is roughly a dozen points per pixel on a wide window, which is
    #: past the point where any more can be seen -- and the trace is drawn
    #: with peak downsampling on top of it, so a spike inside one of those
    #: intervals still reaches the screen.
    #:
    #: Applied **only** to a span already past the byte budget.  A span that
    #: fits still comes back whole however few or many points that is; the
    #: version of this that capped every span by points is what thinned a
    #: four-second window, and that lesson stands.
    SPAN_POINT_BUDGET = 20_000

    def prepare_span(self, t0: float, t1: float) -> int:
        """Re-read ``[t0, t1]`` from the logs on disk, as fully as it can.

        The overview decimates as it goes, which keeps months of history
        affordable and close reading impossible in equal measure -- so a
        hand-picked span is answered from the files themselves rather than
        from whatever survived thinning.  Every log this viewer has consumed
        is scanned -- filenames are not trusted enough to skip any, a lesson
        the legacy .xls logs taught -- and rows outside the span are dropped
        by their timestamps as they parse.  Returns the number of rows
        recovered.

        Past ``SPAN_READ_BUDGET_BYTES`` the span is read at a **stride**
        instead: every n-th row, chosen so the parse stays inside the budget.
        It is not read at full resolution and it is not refused, and the
        difference matters more than it sounds.  Refusing was the first
        attempt and it was wrong in a way that only showed up on a real
        archive: the fallback was "let the overview answer", and the overview
        is not a thinned picture of every log -- it is what ``backfill_s``
        chose to hold, two days of it.  A span reaching further back than
        that fell between the two and was drawn as **nothing at all**, while
        the status bar said it was showing an overview.  Blank is the one
        answer a chart must never give for a day that is sitting on the disk.

        A stride keeps the promise the budget exists to keep -- bounded work
        on the GUI thread -- without ever emptying the window, and
        :meth:`overlay_is_full_resolution` says which of the two happened.
        The earlier lesson still holds and is why the stride is computed from
        the budget rather than from a point count: a span that fits comes
        back whole, every time, and only a span that cannot be read whole is
        thinned.  Statistics and exports never come through here.
        """
        lo, hi = t0 - self.SPAN_MARGIN_S, t1 + self.SPAN_MARGIN_S
        budget = self._span_read_bytes(lo, hi)
        stride = 1
        if budget > self.SPAN_READ_BUDGET_BYTES:
            rows_offered = budget / max(1.0, self._row_bytes)
            stride = max(math.ceil(budget / self.SPAN_READ_BUDGET_BYTES),
                         math.ceil(rows_offered / self.SPAN_POINT_BUDGET))
            log.info("viewer: span covers %.0f MB of log; reading 1 row in %d",
                     budget / 1e6, stride)
        overlay, rows = self._read_span(lo, hi, stride=stride)
        self._overlay = overlay
        self._overlay_span = (t0, t1)
        self._overlay_stride = stride
        return rows

    def _span_sources(self) -> dict[str, int | None]:
        """Every log a span could be answered from, as ``path -> bytes-read``.

        Every log this run has produced, whether or not this viewer has read
        it yet -- a picked span may reach back past the backfill cap, and the
        disk is where the full-resolution answer lives either way.  The
        history entries carry precise byte offsets for files already tailed;
        discovered ones are read whole.  Current file last.
        """
        sources: dict[str, int | None] = {}
        if self.path:
            for p in self._older_logs(self.path):
                sources[p] = None
        for p, upto in self._history:
            sources[p] = upto
        if self.path:
            sources[self.path] = self._offset
        return sources

    def _span_read_bytes(self, lo: float, hi: float) -> int:
        """How many bytes a full-resolution read of ``[lo, hi]`` would parse."""
        total = 0
        for path, upto in self._span_sources().items():
            extent = self._file_span(path, upto)
            if extent is not None and (extent[1] < lo or extent[0] > hi):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            total += size if upto is None else min(upto, size)
        return total

    def overlay_is_full_resolution(self, t0: float, t1: float) -> bool:
        """Is the drawing of ``[t0, t1]`` every sample, or a thinned picture?

        A span can be picked that no screen could draw whole -- three months
        at 1 Hz is eight million samples per trace -- and the honest answer
        there is a decimated one, because the alternative is not a better
        picture but no picture for the better part of a minute.  What must
        not happen is the decimated one passing for the log, so the window
        asks this and says which it is showing.

        Only ever about the *drawing*.  Cursor statistics and the region
        export go through :meth:`samples_in`, which is never capped.
        """
        return self._overlay_span == (t0, t1) and self._overlay_stride == 1

    def overlay_stride(self) -> int:
        """Rows kept per row read in the overlay now loaded.  1 when whole."""
        return self._overlay_stride

    def _read_span(self, lo: float, hi: float, *, stride: int = 1
                   ) -> tuple[dict[str, Series], int]:
        """Every sample between ``lo`` and ``hi`` that the logs on disk hold.

        ``stride`` above 1 keeps one row in n instead, which is how
        :meth:`prepare_span` answers a span too wide to parse whole.  The
        count runs across the whole scan rather than restarting per file, so
        the spacing does not jump at a midnight rollover.

        The scan itself, without the opinion about what it is for: whether the
        result becomes the drawing overlay (:meth:`prepare_span`) or answers a
        question about a region (:meth:`samples_in`) is the caller's business,
        and the two must not share one slot -- a cursor region and a zoom
        window are different spans, and letting either overwrite the other's
        samples would make the chart and the statistics disagree about what
        was measured.

        Returns ``(series by column, rows recovered)``.  The live state is
        borrowed and put back: the parser keeps its tallies on ``self``.
        """
        sources = self._span_sources()
        out: dict[str, Series] = {}
        rows = 0
        counter = _StrideCounter(stride)
        for path, upto in sources.items():
            # A log whose own first and last rows fall wholly outside the span
            # cannot contribute a sample to it, and reading it costs the same
            # as reading one that can.  Skipping it here is what keeps the
            # cost of a zoom proportional to the span rather than to every
            # byte the cryostat has ever logged.
            extent = self._file_span(path, upto)
            if extent is not None and (extent[1] < lo or extent[0] > hi):
                continue
            text = self._read_prefix(path, upto)
            if not text:
                continue
            # Scan it through the ordinary parser without touching the live
            # state, then fold what came out into the result.
            saved = (self.header, self.series, self.rows, self.errors)
            self.header, self.series, self.rows, self.errors = [], {}, 0, 0
            try:
                self._consume(text, sink=out, t_range=(lo, hi), stride=counter)
                rows += self.rows
            finally:
                (self.header, self.series, self.rows, self.errors) = saved
        return out, rows

    def samples_in(self, t0: float, t1: float) -> dict[str, Series]:
        """Every column's **full-resolution** samples in ``[t0, t1]``.

        What a cursor region is measured from, and what an export writes out.
        Never the decimated overview: a mean taken over every other sample is
        a different number from the mean of the measurement, and the whole
        reason to draw two cursors is to ask what the cryostat actually did
        between them.

        Answered from memory while nothing has been thinned -- which is the
        common case, and the difference between a statistic that costs a
        slice and one that costs a scan of every log in the directory.  Once
        decimation has thrown samples away, memory can no longer answer and
        the files are re-read.

        Unlike :meth:`between` this takes the span literally: no bracketing
        sample from beyond the edge, because a sample outside the region is
        not part of what happened inside it.
        """
        if not self.thinned:
            out: dict[str, Series] = {}
            for name, s in self.series.items():
                lo = bisect.bisect_left(s.t, t0)
                hi = bisect.bisect_right(s.t, t1)
                if hi > lo:
                    out[name] = Series(name, s.t[lo:hi], s.v[lo:hi])
            return out
        return self._read_span(t0, t1)[0]

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


@dataclass(frozen=True)
class RegionStats:
    """What one trace did between the cursors.

    ``delta`` is last minus first, not max minus min: the question a cursor
    region answers on a strip chart is "how far did it move between there and
    there", and a trace that wandered out and came back moved nowhere.  The
    spread is what ``std`` is for, and the two together say which of the
    two happened.
    """

    name: str
    n: int
    mean: float
    std: float
    delta: float
    first: float
    last: float


def region_stats(samples: dict[str, Series], names=None) -> dict[str, RegionStats]:
    """Summarise each column of ``samples`` -- what :meth:`CsvTail.samples_in`
    returned for a cursor region.

    Columns with nothing in the region are absent from the result rather than
    present with zeros: a mean of no samples is not 0, and a panel that says
    0.000 K where it means "nothing was recorded here" is the kind of number
    somebody acts on.

    ``std`` is the population standard deviation of the samples in hand.  These
    are a whole population -- every sample the recorder took between the
    cursors -- not a draw from a larger one, so there is no correction to make.
    A single sample has a spread of exactly zero, and reports it.
    """
    out: dict[str, RegionStats] = {}
    for name, series in samples.items():
        if names is not None and name not in names:
            continue
        if not series.v:
            continue
        v = np.asarray(series.v, dtype=float)
        out[name] = RegionStats(
            name=name,
            n=int(v.size),
            mean=float(v.mean()),
            std=float(v.std()),
            delta=float(v[-1] - v[0]),
            first=float(v[0]),
            last=float(v[-1]),
        )
    return out


def value_at(t, v, x: float) -> float | None:
    """``v`` interpolated linearly at time ``x``, or ``None`` outside the data.

    Outside rather than clamped: a trace that ends at 10:00 has no value at
    10:30, and answering with its last one would put a number under the mouse
    that the cryostat never had.  Exactly on a sample returns that sample,
    which matters when the pointer is parked on the newest one.
    """
    n = len(t)
    if n == 0:
        return None
    if x < t[0] or x > t[-1]:
        return None
    i = bisect.bisect_left(t, x)
    if i == 0:
        return float(v[0])
    if i >= n:
        return float(v[-1])
    t0, t1 = t[i - 1], t[i]
    if t1 == t0:
        return float(v[i])
    frac = (x - t0) / (t1 - t0)
    return float(v[i - 1]) + frac * (float(v[i]) - float(v[i - 1]))


def nearest_series(
    traces: dict[str, tuple],
    x: float,
    y: float,
    *,
    tolerance: float,
) -> tuple[str, float] | None:
    """Which trace the pointer is on, and what it reads there.

    ``traces`` is ``{name: (t, v)}`` -- whatever is currently drawn on one
    panel.  Each is interpolated at ``x`` and the closest in ``y`` wins,
    provided it is within ``tolerance`` of the pointer.  The tolerance is in
    data units and is the caller's business, because a panel's units are: a
    few pixels' worth of a percent axis is not a few pixels' worth of a
    kelvin axis, and a fixed number here would identify a trace three
    screen-inches away on one panel and nothing at all on the other.

    ``None`` when nothing is near enough, which is most of the panel.
    """
    best: tuple[str, float] | None = None
    best_gap = float("inf")
    for name, (t, v) in traces.items():
        got = value_at(t, v, x)
        if got is None:
            continue
        gap = abs(got - y)
        if gap <= tolerance and gap < best_gap:
            best, best_gap = (name, got), gap
    return best


def write_region_csv(path, samples: dict[str, Series], *, columns=None) -> int:
    """Write a cursor region out as a CSV, in the recorder's own shape.

    Rows, not columns-side-by-side: every series in ``samples`` was read from
    the same log rows, so their timestamps line up, and pivoting them back
    into rows gives a file that opens in the same place the recorder's own
    logs do.  A column with no sample at some timestamp is left empty rather
    than filled in -- that is what the recorder writes for a channel that
    failed a cycle, and inventing a value here would be worse than the hole.

    Returns the number of data rows written.
    """
    names = [n for n in (columns if columns is not None else sorted(samples))
             if n in samples]
    by_time: dict[float, dict[str, float]] = {}
    for name in names:
        series = samples[name]
        for t, v in zip(series.t, series.v):
            by_time.setdefault(t, {})[name] = v
    stamps = sorted(by_time)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Timestamp", "Time", *names])
        t0 = stamps[0] if stamps else 0.0
        for t in stamps:
            row = by_time[t]
            writer.writerow([
                _dt.datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
                f"{t - t0:.3f}",
                *[("" if n not in row else f"{row[n]:.6g}") for n in names],
            ])
    return len(stamps)


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
        sentence = (
            f"cycle {self.status.get('cycle', 0)}, "
            f"{self.status.get('dropped_cycles', 0)} with errors"
        )
        # A recorder that cannot write status.json still reads "ok" here, and
        # correctly so: everything visible is current.  It is just not the
        # newest there is.  Without this the file silently lags with nothing
        # anywhere saying why -- which is the same "a gap in the feed looks
        # exactly like a hung recorder" problem the counter exists for, seen
        # from the viewer instead of from the CLI.  `lschart status` has said
        # this since 961bf96; the viewer had not.
        failures = int((self.status.get("status_file") or {}).get("failures") or 0)
        if failures:
            sentence += (f", {failures} failed status write(s) -- this file "
                         f"may be behind the recorder")
        return "ok", sentence

    # -- convenience projections ------------------------------------------

    def channels(self) -> list[dict]:
        return list((self.status or {}).get("channels", []) or [])

    def links(self) -> list[dict]:
        return list((self.status or {}).get("links", []) or [])

    def control(self) -> dict | None:
        """The software loop's block, or ``None`` on a recorder that has none.

        Absent and not empty on a plain recorder, which is the distinction
        :func:`control_row` turns into "draw no row" rather than "draw a row
        with nothing in it".
        """
        control = (self.status or {}).get("control")
        return control if isinstance(control, dict) else None

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
        """May a *file* change a 33x heater range on this recorder?

        In either direction: 0 is gated like every other value, because cutting
        a heater is not automatically the safe direction.  So unlike
        :meth:`allows_pid` this *is* a reason to disable the control -- a live
        one could only produce a refusal.  What stays reachable when this is
        shut is the panic menu, which is exempt from the gate.
        """
        cmds = (self.status or {}).get("commands") or {}
        return bool(cmds.get("allow_heater_range"))

    def allows_analog_output(self) -> bool:
        """May a *file* drive a 218 analog output?  Same, 0 included."""
        cmds = (self.status or {}).get("commands") or {}
        return bool(cmds.get("allow_analog_output"))

    def allows_pid(self) -> bool:
        """May a *file* retune a loop on this recorder?

        Unlike the two power gates this one has no always-allowed direction --
        there is no such thing as a gain that removes heat -- but it is still
        not a reason to disable the control: the boxes are worth reading even
        where they cannot be written, and a greyed-out field is not a legible
        way to say "you may look".
        """
        cmds = (self.status or {}).get("commands") or {}
        return bool(cmds.get("allow_pid"))

    def source_allowed(self, name: str = "lschart-gui") -> bool:
        """Is this client's own label switched on at the recorder?

        Unlike the two power gates above, this one *is* a reason to disable a
        control: it does not spare a direction.  A recorder that has switched
        this viewer off will refuse everything it sends except the panic kinds,
        so a live-looking setpoint box would be a lie.

        Degrades open for a recorder too old to publish a policy, the same way
        :func:`capabilities` degrades: an absent key means the question had not
        been invented yet, not that the answer is no.
        """
        cmds = (self.status or {}).get("commands") or {}
        if not cmds.get("source_policy"):
            return True
        for entry in cmds.get("sources") or []:
            if str(entry.get("name", "")) == name:
                return bool(entry.get("allowed"))
        return bool(cmds.get("source_default", True))

    def source_configured(self, name: str = "lschart-gui") -> bool:
        """Does the recorder's *config* permit this source at all?

        The distinction the runtime toggle needs: a source the config refuses
        cannot be un-muted from here at any price, because the overlay may only
        narrow. One the config permits but the overlay has muted is one click
        away.
        """
        cmds = (self.status or {}).get("commands") or {}
        if not cmds.get("source_policy"):
            return True
        for entry in cmds.get("sources") or []:
            if str(entry.get("name", "")) == name:
                return bool(entry.get("configured"))
        return bool(cmds.get("source_default", True))

    def source_note(self, name: str = "lschart-gui") -> str:
        """One sentence on why this viewer is locked out, or ``""``."""
        if self.source_allowed(name):
            return ""
        cmds = (self.status or {}).get("commands") or {}
        for entry in cmds.get("sources") or []:
            if str(entry.get("name", "")) == name:
                if entry.get("disabled_at_runtime"):
                    return (f"Commands from {name!r} are switched off at the "
                            "recorder (sources.json in its IPC directory). "
                            "Delete that entry to allow them again — no "
                            "restart needed.")
                break
        return (f"Commands from {name!r} are not permitted by this recorder's "
                "configuration (ipc.sources). Changing that needs a config "
                "edit and a restart.")

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

    Two schemas of loop numbers are accepted for the same reason.  Schema 2
    gave ``loops`` to the array of loop *objects* and moved the plain list to
    ``loop_numbers``; schema 1 recorders still write a list of integers under
    ``loops``, and a viewer pointed at one should offer their loops rather than
    decide the box has none.
    """
    heaters = [int(n) for n in link.get("heater_outputs") or ()]
    analog = link.get("analog_output")
    known = ("loop_numbers" in link) or ("loops" in link) or ("analog_output" in link)
    if "loop_numbers" in link:
        loops = [int(n) for n in link.get("loop_numbers") or ()]
    else:
        # Schema 1: a bare list of integers.  Anything else under this key is
        # schema 2's object array, which says nothing about which loops the
        # box will accept a setpoint on that `loop_rows` does not.
        raw = link.get("loops") or ()
        loops = [int(n) for n in raw if isinstance(n, (int, float))]
        if not loops:
            loops = [int(r["loop"]) for r in raw
                     if isinstance(r, dict) and r.get("loop") is not None]
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


#: What a loop entry looks like when the recorder is too old to publish one.
#: Every key present, so a client can read it without asking whether it is
#: there -- which is the same promise the status file itself makes.
EMPTY_LOOP = {
    "loop": 0, "sensor": "", "input": "", "mode": "", "mode_code": None,
    "heater_output": None, "setpoint_k": None, "output_pct": None,
    "range": None, "threshold_k": None, "ramping": False,
}


def loop_rows(link: dict) -> list[dict]:
    """The loop table for one instrument, from its status entry.

    Empty for a recorder too old to publish one (schema 1, where ``loops`` was
    a list of integers) -- the caller shows no table rather than inventing
    rows, which is the same degrade `capabilities` makes.

    Every returned row carries every key, filled from :data:`EMPTY_LOOP` where
    the recorder said nothing, so no caller has to guard each lookup.
    """
    rows = []
    for raw in link.get("loops") or ():
        if not isinstance(raw, dict):
            continue                     # schema 1's list of loop numbers
        row = dict(EMPTY_LOOP)
        row.update({k: v for k, v in raw.items() if k in EMPTY_LOOP})
        rows.append(row)
    return rows


#: The row a software loop is drawn as when it is closed and healthy.  Not a
#: loop number on any instrument -- there is no `SETP 5` to send -- so the `#`
#: column says what kind of loop it is instead.  A digit there would collide
#: with a real loop in the same table, which is the one reading that must not
#: be possible.
SOFTWARE_LOOP_LABEL = "sw"


def control_row(control: dict | None) -> dict | None:
    """The software loop as one more row of the loop table, or ``None``.

    ``None`` on a plain recorder, which is most of them: `lschart` runs the
    instrument's own loops and has no controller at all, and the `control` key
    is then simply absent.  A viewer that drew an empty software row there
    would be claiming a loop exists.

    **Why it belongs in the same table.** Until this, a viewer pointed at a
    running `ltspm3` showed the heater percent as a trace and said nothing
    whatever about the loop driving it -- not its setpoint, not its health, and
    not that it had locked itself out after a fault.  The loop that most needs
    watching was the one loop with no row.

    Three columns need an answer that an instrument loop gets for free, and the
    honest answers are not all the same shape:

    ``#``
        :data:`SOFTWARE_LOOP_LABEL`.  It has no loop number, and inventing one
        would put it in the same namespace as loops that can be commanded.
    ``Sensor``
        It *does* have one, and the recorder publishes it: the control channel,
        by the same name the trace and the readout carry.  So the kelvin column
        fills itself by the same lookup every other row uses.
    ``Rng``
        It genuinely has none.  The 218 has no inert half -- no loop, no range,
        one `ANALOG` command whose percentage *is* the power -- so this is a
        fact about the loop and not a gap in what the recorder knows.  The
        caller shows ``n/a``, the same word a 336's loops 3 and 4 already get,
        for the same reason.

    ``mode_code`` is set to 1 -- closed loop, the code an instrument uses --
    only when the supervisor is both in PID mode *and* tracking.  Idle, manual,
    holding, ramping down and locked out are all "not trying", which is what
    suppresses both warning marks, exactly as a range of 0 does on a heater.
    """
    if not isinstance(control, dict) or not control:
        # Empty and absent mean the same thing here.  A block with nothing in
        # it describes no loop, and a row of dashes for it would be inventing
        # one -- the same degrade `loop_rows` makes for a schema-1 recorder.
        return None
    mode = str(control.get("mode") or "")
    state = str(control.get("state") or "")
    row = dict(EMPTY_LOOP)
    row.update({
        "loop": SOFTWARE_LOOP_LABEL,
        "sensor": str(control.get("sensor") or ""),
        "mode": state or mode,
        "mode_code": 1 if (mode == "pid" and state == "tracking") else 0,
        "heater_output": None,
        "range": None,
        "setpoint_k": control.get("setpoint_k"),
        "output_pct": control.get("output_pct"),
        "threshold_k": control.get("threshold_k"),
        "ramping": bool(control.get("ramping")),
        # Not in EMPTY_LOOP: no instrument loop has any of these, and a key
        # that is null on every row but one is a column nobody can read.
        "demand_pct": control.get("demand_pct"),
        "rails": (control.get("rail_low_pct"), control.get("rail_high_pct")),
        "state": state,
        # The LoopMode -- off / manual / pid.  Kept under its own name because
        # `mode` in a loop row is what the State column reads, and for a
        # software loop the *state* is the informative half of the pair.
        "mode_name": mode,
        "health": str(control.get("health") or ""),
        "reason": str(control.get("reason") or ""),
        "alarms": [str(a) for a in control.get("alarms") or []],
        "setpoint_target_k": control.get("setpoint_target_k"),
        "error_k": control.get("error_k"),
        # The scheduled gains, under the same keys an instrument loop uses, so
        # the P and I columns need no special case for this row.  `d` stays
        # absent rather than zero: this controller has no derivative gain to
        # report, and a 0 there would read as "tuned to zero" instead of "not
        # a thing this loop has".
        "p": control.get("p"),
        "i": control.get("i"),
    })
    return row


def reading_rows(channels, links, control=None) -> list[dict]:
    """Every thermometer, carrying the loop bound to it where there is one.

    **One table, not two.** The viewer used to draw a per-channel readouts
    table and a loop table beneath it, and on a 33x-only cryostat those are the
    same four lines twice: every channel is a loop's sensor, so the second
    table repeated the first with more columns.

    The reason there were two is still real and is what this function has to
    respect. `FEATURE_PLAN.md` records it: a loop-centric table *replacing* the
    channel list turns an eight-input monitor into however many loops it has,
    and recording every thermometer continuously is the recorder's whole job.
    The generalisation that gets one table without paying that price is to make
    the **channel** the row and the loop a set of columns on it:

    - every channel gets a row, always, bound to a loop or not;
    - a loop fills the loop columns of the row whose sensor it reads;
    - a loop whose sensor is not among the channels -- an unresolved binding,
      or a second loop on a channel that already has one -- gets a row of its
      own rather than being dropped or overwriting the first;
    - the software loop is just another loop here, and merges into the row for
      the channel it controls.

    So a 218 with eight inputs and no loops draws eight rows with the loop
    columns empty, which is exactly the table it had before, and a 336 draws
    four rows instead of eight lines.

    Returns rows carrying every key in :data:`EMPTY_LOOP` plus ``channel``,
    ``kelvin``, ``usable``, ``validity``, ``instrument`` and ``has_loop``.
    """
    by_sensor: dict[str, list[dict]] = {}
    order: list[dict] = []
    for link in links or ():
        name = str(link.get("name", ""))
        for row in loop_rows(link):
            row = dict(row, instrument=name)
            by_sensor.setdefault(str(row.get("sensor") or ""), []).append(row)
            order.append(row)
    software = control_row(control)
    if software is not None:
        software = dict(software, instrument="")
        by_sensor.setdefault(str(software.get("sensor") or ""), []).append(software)
        order.append(software)

    rows: list[dict] = []
    claimed: list[int] = []
    for channel in channels or ():
        name = str(channel.get("name", ""))
        waiting = by_sensor.get(name) or []
        first = waiting[0] if waiting else None
        rows.append(_joined(channel, first))
        if first is not None:
            claimed.append(id(first))
        # A second loop reading the same thermometer keeps its own row: two
        # loops on one sensor is unusual but legal on a 336, and silently
        # showing one of them would be worse than an extra line.
        for extra in waiting[1:]:
            rows.append(_joined(None, extra))
            claimed.append(id(extra))

    # Loops whose sensor matched no channel: an OUTMODE binding the recorder
    # could not resolve to a label. The loop is still real and still driving a
    # heater, so it is shown with no temperature rather than not shown.
    for row in order:
        if id(row) not in claimed:
            rows.append(_joined(None, row))
    return rows


def _joined(channel: dict | None, loop: dict | None) -> dict:
    """One table row from a channel, a loop, or both."""
    row = dict(EMPTY_LOOP)
    row.update({
        "channel": "", "kelvin": None, "usable": False, "validity": "",
        "instrument": "", "has_loop": loop is not None,
        "rails": None, "state": "", "health": "", "reason": "",
        "alarms": [], "mode_name": "", "setpoint_target_k": None,
        "error_k": None, "demand_pct": None,
    })
    if loop is not None:
        row.update(loop)
    if channel is not None:
        row["channel"] = str(channel.get("name", ""))
        row["kelvin"] = channel.get("kelvin")
        row["usable"] = bool(channel.get("usable"))
        row["validity"] = str(channel.get("validity", "") or "")
    else:
        # No channel of its own: the loop names the sensor it believes it
        # reads, which is the only label available for it.
        row["channel"] = str((loop or {}).get("sensor") or "")
    return row


def loop_marks(row: dict, kelvin: float | None, *, rails=None) -> dict:
    """The two warning marks for one loop row: ``saturated`` and ``unsettled``.

    **Two marks and never one.** OR-ing them together makes an icon that is lit
    through every cooldown, and an icon that is always lit is an icon nobody
    reads. They also mean different things: a loop pinned at its rail has run
    out of authority, while a loop far from its setpoint may simply be on its
    way there.

    Both are suppressed while the loop is **not trying** -- range 0, a mode
    other than closed loop, or a ramp still traversing. A loop that was never
    going to the setpoint is not failing to reach it, and a ramp that has not
    arrived is a ramp doing exactly what it was asked to.

    ``unsettled`` needs a threshold, and a loop with none configured has no
    opinion about being settled: the mark stays off rather than being decided
    by a number this software picked. See ``loop_thresholds`` in the config.

    ``rails`` is ``(low_pct, high_pct)`` and defaults to the fixed pair above.
    It is **not** a per-loop knob reintroduced by the back door: an instrument
    loop never passes it, because "the output has run out of authority" is the
    same fact on every heater. What passes it is :func:`control_row`, whose
    clamp is not a display preference but the band the supervisor is actually
    enforcing -- about a percent wide on this cryostat, so judging it against
    99% would mean never lighting the mark on the one loop whose authority is
    genuinely scarce.

    The percentage judged against those rails is ``demand_pct`` where the row
    has one and ``output_pct`` otherwise -- what the loop *asked for*, in
    preference to what it *wrote*. An instrument never says what its PID
    wanted, so a heater at 100% is the only evidence available there. A
    software loop does say, and its written output is the wrong thing to test:
    the value is quantised to a DAC code and the band is re-applied by stepping
    *down* a code, so a fully saturated loop writes a value strictly below its
    own rail and would never compare equal to it.
    """
    trying = (
        row.get("mode_code") == 1                # closed loop, and only that
        and not row.get("ramping")
        and (row.get("range") is None or int(row.get("range") or 0) > 0)
    )
    if not trying:
        return {"trying": False, "saturated": False, "unsettled": False}
    pct = row.get("demand_pct")
    if pct is None:
        pct = row.get("output_pct")
    setpoint = row.get("setpoint_k")
    threshold = row.get("threshold_k")
    low, high = SATURATED_LOW_PCT, SATURATED_HIGH_PCT
    if rails is not None and rails[0] is not None and rails[1] is not None:
        low, high = float(rails[0]), float(rails[1])
    saturated = pct is not None and (
        float(pct) >= high or float(pct) <= low)
    unsettled = (
        kelvin is not None and setpoint is not None and threshold is not None
        and abs(float(kelvin) - float(setpoint)) > float(threshold)
    )
    return {"trying": True, "saturated": saturated, "unsettled": unsettled}
