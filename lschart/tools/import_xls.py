"""Reader for the legacy chart-recorder ``.xls`` logs.

Format, as produced by the Lake Shore software (all 24 files in
``reference/logs`` agree):

======  ====================================================================
row 0   ``Model 218 Temperature Monitor - SN:21SABC`` (or the 336 equivalent)
row 1   ``Log Started:`` | ``Thu Jul 23 10:26:48 PDT 2026``
row 2   blank
row 3   header: ``Time`` then channel columns, ending in ``Notes``
row 4+  data.  ``Time`` is **milliseconds since the log started**.
======  ====================================================================

Two things bite:

* **The filename lies about the instrument.**
  ``cd10_7_2026_st2_monitor3.xls`` is a 218 log.  Always sniff row 0.
* **Files stop at 65,004 rows** -- the 65,536-row BIFF limit.  That is why the
  cadence in these logs varies from 2 s to 20 s: the recorder was slowed down
  to fit longer runs in one file, not because the cryostat changed.  A run is
  therefore spread across ``monitor1..monitor7``, and ``Time`` restarts at 0 in
  each.  :func:`load` returns absolute wall-clock timestamps so the pieces can
  be concatenated.

Unpopulated inputs are empty cells, never ``0.0``, so a genuine 0.000 K reading
is unambiguous.
"""

from __future__ import annotations

import datetime as _dt
import glob as _glob
import os
import re
from dataclasses import dataclass, field

TEMPERATURE_336 = ("RAD SHIELD", "THE CHONKE", "1st Stage", "2nd Stage")

#: "Command sent: ANALOG 1, 0, 2, 1, 1,1,1,63.076"
_ANALOG_RE = re.compile(r"ANALOG\s+\d+\s*(?:,\s*[-\d.]+\s*){6},\s*([-\d.]+)", re.I)


@dataclass
class LogNote:
    t_s: float
    text: str

    @property
    def analog_percent(self) -> float | None:
        """The heater value this note commanded, if it is an ANALOG command."""
        m = _ANALOG_RE.search(self.text)
        return float(m.group(1)) if m else None


@dataclass
class ChartLog:
    """One ``.xls`` file: channels by name, plus the Notes column."""

    path: str
    model: str                      # "218" or "336"
    serial: str
    started: _dt.datetime | None
    t_s: list[float]                             # seconds since log start
    channels: dict[str, list[float | None]]      # None where the cell was blank
    notes: list[LogNote] = field(default_factory=list)

    # -- convenience -------------------------------------------------------

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def duration_h(self) -> float:
        return (self.t_s[-1] - self.t_s[0]) / 3600.0 if len(self.t_s) > 1 else 0.0

    @property
    def cadence_s(self) -> float:
        """Median sample interval -- varies 2-20 s across the reference logs."""
        if len(self.t_s) < 3:
            return 0.0
        d = sorted(b - a for a, b in zip(self.t_s, self.t_s[1:]))
        return d[len(d) // 2]

    @property
    def temperature_channels(self) -> list[str]:
        """Channels carrying a temperature, excluding setpoints/heaters/outputs."""
        skip = ("Setpoint", "Heater", "Analog Out")
        return [c for c in self.channels if not c.startswith(skip)]

    @property
    def populated_channels(self) -> list[str]:
        """Temperature channels that actually have data."""
        return [c for c in self.temperature_channels
                if any(v is not None for v in self.channels[c])]

    def wall_clock(self, i: int) -> _dt.datetime | None:
        if self.started is None:
            return None
        return self.started + _dt.timedelta(seconds=self.t_s[i])

    def series(self, channel: str) -> list[tuple[float, float]]:
        """``(t_s, kelvin)`` pairs with blanks dropped."""
        return [(t, v) for t, v in zip(self.t_s, self.channels[channel]) if v is not None]

    def heater_commands(self) -> list[tuple[float, float]]:
        """``(t_s, percent)`` for every ANALOG command in the Notes column."""
        out = []
        for n in self.notes:
            pct = n.analog_percent
            if pct is not None:
                out.append((n.t_s, pct))
        return out


def _parse_started(text: str) -> _dt.datetime | None:
    """``Thu Jul 23 10:26:48 PDT 2026`` -- the zone abbreviation is not parseable
    portably, so it is dropped; every log is local time on the cryostat anyway."""
    parts = str(text).split()
    if len(parts) < 6:
        return None
    try:
        return _dt.datetime.strptime(
            " ".join(parts[:4] + parts[5:6]), "%a %b %d %H:%M:%S %Y"
        )
    except ValueError:
        return None


def load(path: str) -> ChartLog:
    """Read one chart-recorder ``.xls``."""
    import xlrd

    book = xlrd.open_workbook(path, on_demand=True)
    try:
        sheet = book.sheet_by_index(0)
        title = str(sheet.row_values(0)[0])
        # Sniff the model: the filename is not reliable.
        model = "336" if "336" in title else "218"
        serial = title.split("SN:")[-1].strip() if "SN:" in title else ""
        started = _parse_started(sheet.row_values(1)[1]) if sheet.ncols > 1 else None

        header = [str(h).strip() for h in sheet.row_values(3)]
        try:
            notes_col = header.index("Notes")
        except ValueError:
            notes_col = len(header) - 1

        names = [h for i, h in enumerate(header) if i != 0 and i != notes_col and h]
        channels: dict[str, list[float | None]] = {n: [] for n in names}
        t_s: list[float] = []
        notes: list[LogNote] = []

        for r in range(4, sheet.nrows):
            row = sheet.row_values(r)
            types = sheet.row_types(r)
            if types[0] != 2:                    # 2 == number; skip stray rows
                continue
            seconds = row[0] / 1000.0
            t_s.append(seconds)
            for i, h in enumerate(header):
                if i == 0 or i == notes_col or not h:
                    continue
                channels[h].append(row[i] if types[i] == 2 else None)
            note = row[notes_col] if notes_col < len(row) else ""
            if isinstance(note, str) and note.strip():
                notes.append(LogNote(seconds, note.strip()))

        return ChartLog(path, model, serial, started, t_s, channels, notes)
    finally:
        book.release_resources()


def load_dir(pattern: str) -> list[ChartLog]:
    """Load every ``.xls`` matching ``pattern``, oldest log first."""
    logs = [load(p) for p in sorted(_glob.glob(pattern))]
    logs.sort(key=lambda g: (g.started or _dt.datetime.min))
    return logs
