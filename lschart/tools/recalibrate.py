"""Re-read an old log through a different sensor calibration curve.

A Lake Shore box does not record resistance.  It records *kelvin*, computed
from the breakpoint table that was loaded into it at the time, and it keeps no
note of which table that was.  So when the wrong curve turns out to have been
loaded, the log is not merely annotated wrong -- every temperature in it is a
different number from the one the thermometer was actually at.

The reading is still recoverable, because the conversion the box did is
invertible.  The box interpolates linearly between breakpoints in
(sensor units, temperature); run that backwards through the curve that *was*
loaded and you get the sensor units the thermometer actually presented, which
is a physical fact about the run and does not depend on anybody's calibration.
Run those forward through the curve that *should* have been loaded and you get
the temperature.  Kelvin -> resistance -> kelvin::

    T_logged --[curve that was loaded]--> R --[curve that belongs]--> T_true

Two things this deliberately will not do.

**It refuses to invert a railed reading.**  When the thermometer went past the
end of the loaded table the box clamped its output to the end breakpoint, and
every value beyond that point collapsed onto one number.  The resistance is
gone -- 905 rows of CD10 all read 330.3200 -- and there is no inverting a
constant.  Those rows come out blank and are counted in the report.  This
matters more than it sounds: the wrong Coldplate curve rails at 330.32 K, and
putting *that* through the right curve gives 292.6 K, a number indistinguishable
from a plausible room temperature.  A blank is honest; a fabricated 292.6 K is
not.

**It never writes over its input.**  The old log is the only record of what the
box actually said, and a remap is a claim about which curve was loaded -- a
claim that can turn out to be wrong.  Output goes to a separate directory.

Everything else in the file is copied through unchanged, and the remapped
column keeps the number of decimals it had, so the result is still a recorder
CSV: ``lschart-view --csv`` opens it and :mod:`lschart.tools.fit_table` reads
it.  ``.gz`` is transparent on both sides.

Usage::

    # what the correction is, before touching any data
    python -m lschart.tools.recalibrate --report
        --from reference/sensor-curves/X186276.340
        --to   reference/sensor-curves/X186279.340

    # apply it
    python -m lschart.tools.recalibrate --column Coldplate
        --from reference/sensor-curves/X186276.340
        --to   reference/sensor-curves/X186279.340
        "data/cd10/*.csv" -o "data/cd10-recal"
"""

from __future__ import annotations

import argparse
import csv
import glob as _glob
import gzip
import io
import math
import os
import re
import sys
from dataclasses import dataclass

#: Lake Shore curve data formats, as the ``.340`` header spells them, mapped to
#: the physical quantity the breakpoint is in.  Two curves can only be composed
#: when they agree on that quantity: a volts curve and an ohms curve describe
#: different sensors and there is no conversion between them.
FORMATS = {
    1: ("mV", "volts"),
    2: ("V", "volts"),
    3: ("Ohms", "ohms"),
    4: ("Log Ohms", "ohms"),
    5: ("Ohms", "ohms"),
    6: ("Log Ohms", "ohms"),
    7: ("Ohms", "ohms"),
}

#: Formats whose breakpoint is the base-10 logarithm of the quantity.
LOG_FORMATS = (4, 6)

#: How close to an end breakpoint counts as sitting on it.  Relative, because
#: the box reports about six significant figures whatever the magnitude: the
#: 218 answers ``+330.32`` for a table that ends at 330.324, and 0.004 K at
#: 330 K is the same fraction as 0.00002 K at 1.4 K.
RAIL_RTOL = 1e-4

_BREAKPOINT = re.compile(r"^\s*(\d+)\s+([-+\d.Ee]+)\s+([-+\d.Ee]+)\s*$")
_HEADER = re.compile(r"^\s*([A-Za-z ]+?)\s*:\s*(.+?)\s*$")


def _open(path: str, mode: str):
    """Open a file, transparently gzipped when the name says so."""
    if path.endswith(".gz"):
        return gzip.open(path, mode + "t", encoding="utf-8", newline="")
    return open(path, mode, encoding="utf-8", newline="")


@dataclass(frozen=True)
class Curve:
    """One Lake Shore breakpoint table, read from a ``.340`` file.

    ``units`` is ascending and ``temps`` runs with it; both are kept exactly as
    the file gives them, and the format says whether a breakpoint is the
    quantity or its logarithm, so a log-ohms table and an ohms table of the
    same sensor still compose.
    """

    serial: str
    model: str
    fmt: int
    units: tuple[float, ...]
    temps: tuple[float, ...]
    path: str = ""

    @property
    def quantity(self) -> str:
        return FORMATS.get(self.fmt, ("?", "unknown"))[1]

    @property
    def units_name(self) -> str:
        return FORMATS.get(self.fmt, ("?", "unknown"))[0]

    @property
    def t_min(self) -> float:
        return min(self.temps)

    @property
    def t_max(self) -> float:
        return max(self.temps)

    @classmethod
    def read(cls, path: str) -> "Curve":
        serial = model = ""
        fmt = 0
        rows: list[tuple[float, float]] = []
        with _open(path, "r") as fh:
            for line in fh:
                point = _BREAKPOINT.match(line)
                if point:
                    rows.append((float(point.group(2)), float(point.group(3))))
                    continue
                head = _HEADER.match(line)
                if not head:
                    continue
                key, value = head.group(1).strip().lower(), head.group(2)
                if key == "serial number":
                    serial = value.strip()
                elif key == "sensor model":
                    model = value.strip()
                elif key == "data format":
                    fmt = int(value.split()[0])
        if len(rows) < 2:
            raise SystemExit(f"{path}: no breakpoint table found")
        if fmt not in FORMATS:
            raise SystemExit(f"{path}: data format {fmt} is not one I can read")
        rows.sort()
        units = tuple(u for u, _ in rows)
        temps = tuple(t for _, t in rows)
        if len(set(units)) != len(units):
            raise SystemExit(f"{path}: repeated breakpoint, table is not invertible")
        rising = temps[-1] > temps[0]
        steps = [b - a for a, b in zip(temps, temps[1:])]
        if not all((step > 0) if rising else (step < 0) for step in steps):
            raise SystemExit(f"{path}: temperature is not strictly monotonic "
                             f"in the table, so it cannot be inverted")
        return cls(serial or "?", model or "?", fmt, units, temps, path)

    # -- the two directions ------------------------------------------------
    #
    # Both are the linear interpolation the instrument itself does, which is
    # what makes the round trip exact rather than approximate.  Neither
    # extrapolates: off the end of the table there is no calibration, and
    # inventing one is the whole failure this module exists to undo.

    def units_at(self, t_k: float) -> float | None:
        """Sensor units at ``t_k``, or ``None`` if it is off the table."""
        return _interp(t_k, self.temps, self.units)

    def temperature_at(self, unit: float) -> float | None:
        """Temperature at a breakpoint value, or ``None`` if off the table."""
        return _interp(unit, self.units, self.temps)

    def at_rail(self, t_k: float, rtol: float = RAIL_RTOL) -> bool:
        """Is ``t_k`` sitting on an end breakpoint -- that is, clamped?"""
        return any(abs(t_k - end) <= rtol * abs(end)
                   for end in (self.t_min, self.t_max))

    def quantity_at(self, t_k: float) -> float | None:
        """Ohms (or volts) at ``t_k``: the breakpoint with the log undone."""
        unit = self.units_at(t_k)
        if unit is None:
            return None
        return 10.0 ** unit if self.fmt in LOG_FORMATS else unit


def _interp(x: float, xs, ys) -> float | None:
    """Linear interpolation, refusing to extrapolate. ``xs`` may run either way."""
    if xs[0] > xs[-1]:
        xs, ys = xs[::-1], ys[::-1]
    if x < xs[0] or x > xs[-1]:
        return None
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    if span == 0:
        return ys[lo]
    return ys[lo] + (x - xs[lo]) / span * (ys[hi] - ys[lo])


@dataclass
class Remapper:
    """``T`` under ``source`` -> ``T`` under ``target``, and why it declined."""

    source: Curve
    target: Curve
    rail_rtol: float = RAIL_RTOL

    def __post_init__(self):
        if self.source.quantity != self.target.quantity:
            raise SystemExit(
                f"cannot compose a {self.source.units_name} curve "
                f"({self.source.serial}) with a {self.target.units_name} one "
                f"({self.target.serial}): different sensors, different physics")

    def __call__(self, t_k: float) -> tuple[float | None, str]:
        if self.source.at_rail(t_k, self.rail_rtol):
            return None, "railed"
        value = self.source.quantity_at(t_k)
        if value is None:
            return None, "off_source"
        unit = math.log10(value) if self.target.fmt in LOG_FORMATS else value
        out = self.target.temperature_at(unit)
        if out is None:
            return None, "off_target"
        return out, "ok"


def _decimals(text: str) -> int:
    _, _, frac = text.partition(".")
    return len(frac) if frac and frac.isdigit() else 4


def remap_file(path: str, out_path: str, column: str, remap: Remapper) -> dict:
    """Copy one CSV, rewriting ``column`` through ``remap``. Returns counts."""
    counts = {"rows": 0, "ok": 0, "blank": 0,
              "railed": 0, "off_source": 0, "off_target": 0}
    with _open(path, "r") as src:
        reader = csv.reader(src)
        header = next(reader, None)
        if header is None:
            raise SystemExit(f"{path}: empty file")
        if column not in header:
            raise SystemExit(f"{path}: no {column!r} column; has {', '.join(header)}")
        index = header.index(column)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(header)
        for row in reader:
            counts["rows"] += 1
            text = row[index].strip() if index < len(row) else ""
            if not text:
                counts["blank"] += 1
                writer.writerow(row)
                continue
            try:
                value = float(text)
            except ValueError:
                counts["blank"] += 1
                row[index] = ""
            else:
                out, why = remap(value)
                counts[why] += 1
                row[index] = "" if out is None else f"{out:.{_decimals(text)}f}"
            writer.writerow(row)
    with _open(out_path, "w") as dst:
        dst.write(buffer.getvalue())
    return counts


#: The temperatures the report walks, where the curve covers them.  Cryostat
#: landmarks rather than a uniform grid: 4.2 and 77 are the two baths, the rest
#: are where this cryostat actually sits.
REPORT_POINTS = (1.4, 2, 3, 4.2, 5, 6, 8, 10, 15, 20, 30, 50,
                 77, 100, 150, 200, 250, 300, 325)


def report(remap: Remapper, temperatures=None) -> list[tuple]:
    """The correction as a table, so it can be read before it is applied."""
    if temperatures is None:
        temperatures = [t for t in REPORT_POINTS
                        if remap.source.t_min <= t <= remap.source.t_max]
    out = []
    for t in temperatures:
        value, why = remap(t)
        out.append((t, remap.source.quantity_at(t), value, why))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="re-read a log through a different calibration curve")
    ap.add_argument("pattern", nargs="*", help="glob(s) for recorder CSVs")
    ap.add_argument("--from", dest="source", required=True,
                    help="the .340 curve that WAS loaded in the box")
    ap.add_argument("--to", dest="target", required=True,
                    help="the .340 curve that SHOULD have been")
    ap.add_argument("--column", help="the CSV column to rewrite, e.g. Coldplate")
    ap.add_argument("-o", "--out", help="directory to write the rewritten logs to")
    ap.add_argument("--report", action="store_true",
                    help="print the correction and exit, touching no data")
    ap.add_argument("--rail-rtol", type=float, default=RAIL_RTOL,
                    help="how close to an end breakpoint counts as clamped "
                         f"(relative, default {RAIL_RTOL})")
    a = ap.parse_args(argv)

    source, target = Curve.read(a.source), Curve.read(a.target)
    remap = Remapper(source, target, a.rail_rtol)
    for role, curve in (("was loaded", source), ("belongs", target)):
        print(f"{role:>10}: {curve.serial}  {curve.model}  "
              f"{len(curve.units)} breakpoints, {curve.units_name}, "
              f"{curve.t_min:g}-{curve.t_max:g} K")

    if a.report or not a.pattern:
        unit = "ohms" if source.quantity == "ohms" else "volts"
        print(f"\n{'T logged':>10} {unit:>13} {'T true':>10} {'delta':>9}")
        for t, value, out, why in report(remap):
            shown = f"{out:10.4f}" if out is not None else f"{why:>10}"
            delta = f"{out - t:+9.4f}" if out is not None else " " * 9
            print(f"{t:10.3f} {value:13.4f} {shown} {delta}")
        print(f"\nClamped above {source.t_max:g} K and below {source.t_min:g} K: "
              f"the box railed, the sensor value behind those rows is gone, and "
              f"they come out blank.")
        if not a.pattern:
            print("\nNo files given, so nothing was written.")
        return 0

    if not a.column or not a.out:
        print("--column and -o are both required to rewrite files", file=sys.stderr)
        return 2

    paths: list[str] = []
    for pattern in a.pattern:
        paths.extend(sorted(_glob.glob(pattern)))
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1

    # Check every header before writing anything.  A run that stops halfway
    # leaves a directory that looks like a finished remap and is missing days,
    # and the usual reason to land here -- one file in the glob calls the
    # channel something else -- is spotted in a second and fixed by rerunning.
    for path in paths:
        with _open(path, "r") as fh:
            header = next(csv.reader(fh), [])
        if a.column not in header:
            print(f"{path}: no {a.column!r} column; has {', '.join(header)}",
                  file=sys.stderr)
            return 1

    out_dir = os.path.abspath(a.out)
    os.makedirs(out_dir, exist_ok=True)
    total = dict.fromkeys(
        ("rows", "ok", "blank", "railed", "off_source", "off_target"), 0)
    print(f"\n{a.column}: {len(paths)} file(s) -> {a.out}")
    for path in paths:
        out_path = os.path.join(out_dir, os.path.basename(path))
        if os.path.abspath(path) == out_path:
            print(f"refusing to overwrite {path}", file=sys.stderr)
            return 1
        counts = remap_file(path, out_path, a.column, remap)
        for key in total:
            total[key] += counts[key]
        lost = counts["railed"] + counts["off_source"] + counts["off_target"]
        flag = f"  {lost} unrecoverable" if lost else ""
        print(f"  {os.path.basename(path):<44} {counts['ok']:>7} rewritten{flag}")

    print(f"\n{total['rows']} rows: {total['ok']} rewritten, "
          f"{total['blank']} already blank")
    if total["railed"]:
        print(f"  {total['railed']} were clamped at an end of the loaded curve "
              f"and are now blank -- the sensor value behind them is gone")
    for key, text in (("off_source", "outside the loaded curve"),
                      ("off_target", "outside the correct curve")):
        if total[key]:
            print(f"  {total[key]} fell {text} and are now blank")
    return 0


if __name__ == "__main__":
    sys.exit(main())
