"""Where the monitor's samples come from: a live recorder, or the archive.

The judge in :mod:`~ltspm3.monitor.judge` does not know which.  It is handed
:class:`Sample` objects and the two readers here are what make a replay of
2026-09-10 and a Tuesday afternoon the same exercise -- which is the only way
the replay table in ``plans/pid-2-monitor.md`` means anything about the live
monitor rather than about a second implementation that resembles it.

Two file formats, and the reason is history
--------------------------------------------

``RecorderTail`` reads what ``lschart.acquisition.recorder`` writes:
``Timestamp, Time, <channels>, <aux>, Validity, State, Notes``.
``ArchiveReplay`` reads the curated tables in ``reference/cooldown-10/``:
``Timestamp, t_s, segment, <channels>, u_pct, note``, gzipped, and versioned in
the repository so a replay runs from a fresh clone.

**The clock is the monotonic column in both, never the timestamp.**
AUDIT-2026-09-10 finding 4: the recorder stamps naive local time, so on the
morning the clocks go back an hour happens twice and every consumer that parses
the stamp gets a time axis with a fold in it.  A regression slope across a fold
is not wrong in a way anything notices.  So ``t_s`` here is the file's own
relative clock placed at the file's origin, and the origin is the only thing
the stamp is used for.  A new file whose origin would step the clock BACKWARDS
is carried forward from the previous sample instead, and counted.

What a reader must not do
-------------------------

**It must not decimate.**  The viewer's ``CsvTail`` thins in place once a
series outgrows its budget, which is right for drawing a month on a screen and
wrong for every number the judge computes: a thinned window has a different
noise rms and a different regressed slope from the window it came from.  So
these keep a trailing window at full resolution and drop off the back.

**It must tolerate a file another process is writing.**  A read can land
mid-row and the recorder rolls over at midnight.  A partial last line is held
back until the newline arrives rather than parsed as a short row.
"""

from __future__ import annotations

import csv
import datetime as _dt
import glob
import gzip
import io
import os
from collections import deque
from dataclasses import dataclass, field

from .report import PLANT_PREFIX

#: Columns of the recorder's CSV that are not a measurement.
_NON_SERIES = ("Timestamp", "Time", "Validity", "State", "Notes")


@dataclass(frozen=True)
class Sample:
    """One cycle, in the shape the judge wants it.

    ``t_s`` is monotonic and continuous across a file rollover; ``epoch_s`` is
    the wall clock and is used for two things only -- reporting, and
    ``days_since_gauge`` in the band.  Nothing measures an interval with it.
    """

    t_s: float
    epoch_s: float
    sample_k: float | None = None
    coldplate_k: float | None = None
    u_pct: float | None = None
    aux: dict = field(default_factory=dict)
    note: str = ""
    #: Which recording this came from.  Increments at a rollover and at every
    #: gap the archive's own `segment` column marks, and the judge drops its
    #: history when it changes -- an ODE integrated across a 65 h hole
    #: converges on a number anyway, and so does a baseline.
    segment: int = 0


def _parse_epoch(text: str) -> float | None:
    try:
        return _dt.datetime.fromisoformat(text.strip()).timestamp()
    except (ValueError, AttributeError):
        return None


def _num(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class _Clock:
    """Monotonic seconds from a per-file relative column and a file origin.

    The one place AUDIT-2026-09-10 finding 4 is paid.  ``rewinds`` counts the
    times a new file's origin would have stepped the clock backwards, which is
    what a daylight-saving fold looks like from here.
    """

    def __init__(self) -> None:
        self.origin: float | None = None
        self.last: float = float("-inf")
        self.rewinds = 0
        self.segment = 0

    def open_file(self, first_epoch: float | None) -> None:
        if first_epoch is None:
            return
        if self.origin is not None:
            self.segment += 1
        # Place the new file just after the last sample if its own stamp would
        # put it earlier -- a fold, or a clock somebody set by hand.
        if self.last > float("-inf") and first_epoch <= self.last:
            self.rewinds += 1
            self.origin = self.last
        else:
            self.origin = first_epoch

    def at(self, relative_s: float | None, epoch_s: float | None) -> float:
        if self.origin is None:
            self.origin = epoch_s if epoch_s is not None else 0.0
        if relative_s is None:
            # A file with no relative column at all: fall back to the stamp and
            # keep it monotonic by hand.
            t = epoch_s if epoch_s is not None else self.last
        else:
            t = self.origin + relative_s
        self.last = t = max(t, self.last)
        return t


class RecorderTail:
    """Follow the live recorder's CSV, keeping a trailing window in memory.

    ``window_s`` is how much history the judge may look at.  It bounds memory
    on a recorder that runs for months, and nothing here decides what the judge
    does with it.
    """

    #: How much to re-read at the very first poll.  A monitor started mid-run
    #: owes its baseline a running start, and the baseline is the slowest thing
    #: it has -- six hours by default.  Read the tail of the current file
    #: rather than the whole archive: this is a judge, not a viewer.
    BACKFILL_BYTES = 64 << 20

    def __init__(self, path: str, *, window_s: float = 86400.0,
                 prefix: str | None = None,
                 sample: str = "Sample", coldplate: str = "Coldplate",
                 output: str = "ls218.aout1") -> None:
        self.path = path
        self.window_s = float(window_s)
        self.prefix = prefix
        self.names = {"sample": sample, "coldplate": coldplate,
                      "output": output}
        self.samples: deque[Sample] = deque()
        self.header: list[str] = []
        self._pos = 0
        self._pending = ""
        self._open_path: str | None = None
        self._clock = _Clock()
        self.rows = 0
        self.errors = 0

    # -- following ---------------------------------------------------------

    @property
    def following(self) -> str | None:
        """The log this is actually reading, or ``None`` if it found none.

        A monitor with nothing to read looks exactly like a monitor with
        nothing to say, which is the whole reason this is reportable rather
        than internal.
        """
        return self._open_path

    def _current_path(self) -> str | None:
        """The newest log matching the configured path or directory.

        ``prefix`` IS THE RECORDER'S OWN ``filename_prefix`` and leaving it out
        is how this followed the wrong file.  "Newest" here means last by name,
        because the names are date-stamped -- but a recorder's data directory is
        not full of only its own logs, and every other thing that writes a CSV
        beside it sorts wherever its name happens to sort.  Measured on the
        cryostat, 2026-09-14: 28 files, and the last by name was
        ``sweep-20260905-131753.csv``, the sweep tool's grading table from nine
        days earlier.  Its header has no ``Sample`` column, so the monitor
        would have followed it in silence for the whole soak.

        Worse, THIS PROCESS IS ONE OF THOSE WRITERS.  ``plant_`` sorts after
        ``ltspm3-heater_``, so the first verdict written moved the tail onto the
        monitor's own daily log and it never read the recorder again.  That one
        is why the fallback below still excludes :data:`PLANT_PREFIX` when no
        prefix is configured: following your own output is not a
        misconfiguration a caller should be able to make.
        """
        if os.path.isdir(self.path):
            pattern = f"{self.prefix}_*.csv" if self.prefix else "*.csv"
            logs = sorted(glob.glob(os.path.join(self.path, pattern)))
            if not self.prefix:
                logs = [p for p in logs if not os.path.basename(p).startswith(
                    PLANT_PREFIX + "_")]
            return logs[-1] if logs else None
        return self.path if os.path.exists(self.path) else None

    def poll(self) -> int:
        """Read whatever is new.  Returns how many samples were added."""
        path = self._current_path()
        if path is None:
            return 0
        if path != self._open_path:
            self._start(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            return 0
        if size < self._pos:
            # Truncated or replaced under us.  Start again rather than read
            # from an offset that now means something else.
            self._start(path)
            size = os.path.getsize(path)
        if size == self._pos:
            return 0
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self._pos)
            text = fh.read()
            self._pos = fh.tell()
        return self._consume(text)

    def _start(self, path: str) -> None:
        self._open_path = path
        self._pending = ""
        self.header = []
        size = os.path.getsize(path)
        self._pos = max(0, size - self.BACKFILL_BYTES)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            self.header = next(csv.reader([fh.readline()]), [])
            if self._pos <= fh.tell():
                self._pos = fh.tell()
            else:
                fh.seek(self._pos)
                fh.readline()          # discard a partial row
                self._pos = fh.tell()
        self._clock.open_file(self._first_epoch(path))

    @staticmethod
    def _first_epoch(path: str) -> float | None:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            fh.readline()
            row = next(csv.reader([fh.readline()]), [])
        return _parse_epoch(row[0]) if row else None

    def _consume(self, text: str) -> int:
        text = self._pending + text
        cut = text.rfind("\n")
        if cut < 0:
            self._pending = text
            return 0
        self._pending = text[cut + 1:]
        added = 0
        for row in csv.reader(io.StringIO(text[:cut])):
            if self._row(row):
                added += 1
        self._trim()
        return added

    def _row(self, row: list[str]) -> bool:
        if len(row) < 2 or not self.header:
            return False
        epoch = _parse_epoch(row[0])
        if epoch is None:
            self.errors += 1
            return False
        cells = dict(zip(self.header, row))
        t = self._clock.at(_num(cells.get("Time", "")), epoch)
        aux = {k: v for k, v in
               ((k, _num(cells.get(k, ""))) for k in self.header
                if k not in _NON_SERIES)
               if v is not None}
        self.samples.append(Sample(
            t_s=t, epoch_s=epoch,
            sample_k=aux.get(self.names["sample"]),
            coldplate_k=aux.get(self.names["coldplate"]),
            u_pct=aux.get(self.names["output"]),
            aux=aux, note=cells.get("Notes", "") or "",
            segment=self._clock.segment,
        ))
        self.rows += 1
        return True

    def _trim(self) -> None:
        if not self.samples:
            return
        floor = self.samples[-1].t_s - self.window_s
        while self.samples and self.samples[0].t_s < floor:
            self.samples.popleft()


#: The archive's own column names, which are the fit's and not the recorder's.
ARCHIVE_SAMPLE = "Sample"
ARCHIVE_COLDPLATE = "Coldplate"
ARCHIVE_OUTPUT = "u_pct"


def archive_tables(directory: str) -> list[str]:
    """The curated tables, oldest first.  Their names sort chronologically."""
    return sorted(glob.glob(os.path.join(directory, "*.csv.gz"))
                  + glob.glob(os.path.join(directory, "*.csv")))


def replay(directory: str):
    """Yield every :class:`Sample` in the archive, in order.

    A generator rather than a list: the three tables are 1.8 million rows and
    the judge only ever looks at a trailing window of them.

    The archive's ``segment`` column is carried through, which is what stops a
    baseline being integrated across CD10's 65 h and 187 h holes.  It is
    offset per table so two tables' segment 0 are not the same segment.
    """
    base = 0
    for path in archive_tables(directory):
        opener = gzip.open if path.endswith(".gz") else open
        highest = base
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            origin: float | None = None
            for row in reader:
                epoch = _parse_epoch(row.get("Timestamp", ""))
                rel = _num(row.get("t_s", ""))
                if epoch is None and rel is None:
                    continue
                if origin is None:
                    origin = (epoch - (rel or 0.0)) if epoch is not None else 0.0
                seg = base + int(_num(row.get("segment", "")) or 0)
                highest = max(highest, seg)
                aux = {k: v for k, v in
                       ((k, _num(row.get(k, ""))) for k in reader.fieldnames
                        if k not in ("Timestamp", "t_s", "segment", "note"))
                       if v is not None}
                yield Sample(
                    t_s=origin + (rel if rel is not None else 0.0),
                    epoch_s=epoch if epoch is not None else origin + (rel or 0.0),
                    sample_k=aux.get(ARCHIVE_SAMPLE),
                    coldplate_k=aux.get(ARCHIVE_COLDPLATE),
                    u_pct=aux.get(ARCHIVE_OUTPUT),
                    aux=aux, note=row.get("note", "") or "", segment=seg,
                )
        base = highest + 1
