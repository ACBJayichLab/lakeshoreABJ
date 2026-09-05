"""Flatten recorder CSVs into one table a fitter can load and integrate.

The recorder's CSV is the right format for *recording*: 29 columns, one file
per day, aux readbacks beside measurements, blanks where an instrument was not
asked.  It is the wrong shape for a fit, which wants one array of time, one of
temperature, one of heater percent, and no stitching.

So this reads any number of recorder CSVs -- live ones, or legacy logs put
through :mod:`lschart.tools.xls_to_csv` -- and writes one table:

``Timestamp``
    absolute local time, unchanged, so a row can always be found again in the
    log it came from.
``t_s``
    seconds from the first row **of this file**.  Monotonic across the whole
    table, including across the day boundaries the recorder rolls on.
``segment``
    an integer that increments at every recording gap.  **This is the column
    that matters.**  CD10 has a 65 h hole and a 187 h hole in it; a fit that
    integrates an ODE straight through one of those is integrating over a week
    the cryostat was not being watched, and will happily converge on a number.
    Fit each segment as its own trajectory.
``u_pct``
    the heater, from ``heater_pct`` if a software loop was running and
    ``ls218.aout1`` otherwise -- the same auto-detection ``steptest`` does.
``note``
    the log's Notes text where there was any, so a step's provenance survives.

Everything else is the measurement channels, in the order the log had them.
The aux readbacks are dropped: a fit uses the heater and the thermometers, and
carrying twenty always-blank ``ls336.*`` columns into a fitting table is how a
loader ends up guessing which ones are real.

Rows with no temperature at all are dropped; rows with no heater are **kept**,
because "the heater was not recorded yet" is a fact about the run and blanking
it silently would make a fit start at the wrong output.

Usage::

    python -m lschart.tools.fit_table "data/cd10/cd10_*.csv" -o out.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import glob as _glob
import os
import sys

from ..gui.source import NON_SERIES_COLUMNS

#: Where a step between consecutive rows stops being jitter and starts being a
#: hole.  The recorder's own chart uses 4x for the same decision; this is
#: looser because a fitting table is read once, and splitting a trajectory that
#: did not need splitting costs only a little statistical power, while joining
#: one that did is a wrong answer.
GAP_FACTOR = 5.0

#: Tried in order.  ``heater_pct`` is what a software loop commanded and only
#: exists when one was running; ``ls218.aout1`` is the analog output itself.
HEATER_COLUMNS = ("heater_pct", "ls218.aout1")


def _stamp(text: str) -> float | None:
    try:
        return _dt.datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


def read_rows(paths):
    """Every row of every log, oldest first, de-duplicated by timestamp."""
    rows: list[tuple[float, dict]] = []
    seen: set[float] = set()
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                t = _stamp(row.get("Timestamp", ""))
                if t is None or t in seen:
                    continue
                seen.add(t)
                rows.append((t, row))
    rows.sort(key=lambda r: r[0])
    return rows


def channel_columns(paths, renames=None) -> list[str]:
    """Measurement channels across every file, in first-seen order.

    A run's columns can change between files -- a channel adopted midway is
    absent from the earlier days -- so the union is taken rather than the first
    header.  Aux readbacks (``instrument.key``) are excluded.
    """
    renames = renames or {}
    out: list[str] = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh), [])
        for name in header:
            if name in NON_SERIES_COLUMNS or "." in name or not name:
                continue
            name = renames.get(name, name)
            if name not in out:
                out.append(name)
    return out


def _apply_renames(row: dict, renames: dict) -> dict:
    """Fold a relabelled column onto the name it is now known by.

    The recorder rolls a new part when a channel's name changes, so one run can
    carry the same physical input under two headings -- ``Cold Head`` became
    ``Coldplate`` on 2026-08-26, values continuous across the boundary to
    2 mK.  Left alone that becomes two half-empty columns in the table and a
    fit that sees a thermometer appear from nowhere halfway through.

    Only ever asked for on the command line: a rename and a re-wiring look
    identical in the header, and only the person who was there knows which it
    was.
    """
    if not renames:
        return row
    out = dict(row)
    for old, new in renames.items():
        value = out.pop(old, "")
        if value and not out.get(new):
            out[new] = value
    return out


def parse_renames(text: str | None) -> dict:
    if not text:
        return {}
    pairs = {}
    for part in text.split(","):
        old, _, new = part.partition("=")
        if not old.strip() or not new.strip():
            raise SystemExit(f"bad --rename entry: {part!r}; want OLD=NEW")
        pairs[old.strip()] = new.strip()
    return pairs


def heater_column(paths) -> str | None:
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh), [])
        for name in HEATER_COLUMNS:
            if name in header:
                return name
    return None


def build(paths, out_path: str, renames=None) -> dict:
    renames = renames or {}
    rows = read_rows(paths)
    if not rows:
        raise SystemExit("no rows in any of those files")
    channels = channel_columns(paths, renames)
    heater = heater_column(paths)
    filled = dict.fromkeys(channels, 0)

    times = [t for t, _ in rows]
    steps = sorted(b - a for a, b in zip(times, times[1:])) if len(times) > 1 else [1.0]
    cadence = steps[len(steps) // 2]
    gap_limit = GAP_FACTOR * cadence

    t0 = times[0]
    segment = 0
    kept = 0
    seg_rows: list[int] = []
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Timestamp", "t_s", "segment"] + channels + ["u_pct", "note"])
        prev = None
        for t, row in rows:
            row = _apply_renames(row, renames)
            values = [row.get(c, "") for c in channels]
            if not any(v for v in values):
                continue                       # no temperature at all
            for name, value in zip(channels, values):
                if value:
                    filled[name] += 1
            if prev is not None and t - prev > gap_limit:
                segment += 1
                seg_rows.append(kept)
            prev = t
            w.writerow(
                [row.get("Timestamp", ""), f"{t - t0:.3f}", segment]
                + values
                + [row.get(heater, "") if heater else "",
                   (row.get("Notes") or "").strip()]
            )
            kept += 1
    seg_rows.append(kept)

    return {
        "rows": kept, "segments": segment + 1, "cadence_s": cadence,
        "channels": channels, "heater": heater,
        "span_h": (times[-1] - times[0]) / 3600.0,
        "first": times[0], "last": times[-1],
        "segment_ends": seg_rows,
        "coverage": {c: (filled[c] / kept if kept else 0.0) for c in channels},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="recorder CSVs -> one table for fitting")
    ap.add_argument("pattern", nargs="+", help="glob(s) for recorder CSVs")
    ap.add_argument("-o", "--out", required=True, help="the table to write")
    ap.add_argument("--rename", default=None,
                    help='fold a relabelled channel onto its current name, '
                         'e.g. "Cold Head=Coldplate,Shield=Magnet"')
    a = ap.parse_args(argv)

    paths: list[str] = []
    for pattern in a.pattern:
        paths.extend(sorted(_glob.glob(pattern)))
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    info = build(paths, a.out, parse_renames(a.rename))
    print(f"{len(paths)} logs -> {a.out}")
    print(f"  {info['rows']} rows, {info['span_h']:.1f} h, "
          f"cadence {info['cadence_s']:.1f} s")
    print(f"  {info['segments']} segment(s) -- fit each as its own trajectory")
    print(f"  heater column: {info['heater']}")
    print("  channels:")
    partial = []
    for name in info["channels"]:
        cover = info["coverage"][name]
        print(f"    {name:<14} {100 * cover:>5.1f}% of rows")
        if 0.0 < cover < 0.98:
            partial.append(name)
    if partial:
        print("\n  NOTE: " + ", ".join(partial) + " cover only part of the run.")
        print("  That is either a channel adopted midway or a channel that was")
        print("  renamed -- the header cannot tell them apart.  If renamed, fold")
        print("  them together with --rename, or a fit will see a thermometer")
        print("  appear from nowhere halfway through.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
