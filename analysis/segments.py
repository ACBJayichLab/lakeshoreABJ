"""The cooldown-10 archive, and the manifest of named windows inside it.

Three tables in ``reference/cooldown-10/`` are the whole of cooldown 10 --
which began 2026-07-15 and is still running -- split at two recording gaps and
overlapping nowhere.  ``reference/cooldown-10/segments.csv`` names the windows
inside them: which stretch of which table is a long settled hold, which is a
driven step worth a time constant, which is a trajectory for the ODE, and which
is excluded and why.

**Curate, do not discover.**  Before this existed the fits scanned five
overlapping tables and *found* their dwells with a rule keyed on a tolerance
constant, so changing the constant changed the dataset with nothing in review
to show it.  Now the dataset is a committed file, a proposal against it is a
diff (:mod:`curate`), and a fit reads windows by name.

Two shapes come out of here and they are not interchangeable:

``read_table(file)``
    a whole archive table, every channel, as arrays.  Cached, because each one
    is 200-460 thousand rows and a caller usually wants several windows out of
    the same file.
``load(id)``
    one manifest window: the same arrays sliced to it, with the window's own
    clock (``t`` starts at zero) and the absolute one (``epoch``) both present.

Everything is validated on load, and the checks are the ones that have gone
wrong here before: a window has to lie inside its file, it has to lie inside a
single ``segment`` -- integrating an ODE across a recording gap converges on a
number anyway -- and two windows of the same kind may not overlap, or an anchor
gets counted twice and its weight silently doubles.
"""
from __future__ import annotations

import csv
import datetime as _dt
import math
import os
from dataclasses import dataclass, field

import numpy as np

from _data import ARCHIVE_DIR, open_table, resolve

#: The manifest.  Lives beside the tables it indexes, so a clone that has the
#: data has the index and cannot have one without the other.
MANIFEST = "segments.csv"

#: The three tables, oldest first.  Named here rather than globbed: a glob
#: would quietly pick up a fourth table somebody dropped in the directory, and
#: the whole point of the archive is that its contents are agreed.
TABLES = (
    "cd10_20260715_prepython.csv",
    "cd10_20260824_recorder.csv",
    "cd10_20260904_recorder.csv",
)

#: What a window can be.  ``kind`` is what the window *is*; ``use`` below is
#: what a fit may do with it, and the two are separate because the second is a
#: judgement that can change without the data changing.
#: ``mask`` is the odd one out and the important one: a window nothing may be
#: fitted from, and an INPUT to :mod:`curate`'s proposal rather than an output
#: of it -- a dwell that would cross a mask is cut at its edge.  It is the one
#: mechanism for "do not use this", so there is no per-row override flag.
KINDS = ("jump", "hold", "trace", "mask")
#: ``tau`` -- believe its time constant (and its steady state).  ``steady`` --
#: believe only where it was heading.  ``ode`` -- integrate it as a trajectory.
#: ``excluded`` -- do not use it, and the ``note`` says why.
USES = ("tau", "steady", "ode", "excluded")

#: The channel every window has and the fit turns on.
SAMPLE, COLDPLATE, HEATER = "Sample", "Coldplate", "u_pct"
#: The rest.  Blank 2026-07-23 -> 08-20 for the four cold-head channels, so
#: anything regressed on them covers at most 28 of the cooldown's 55 days --
#: diagnose with these, do not fit with them.  ``THE CHONKE`` reads
#: 289.999-290.000 throughout: a held setpoint, no signal.
AUX = ("Magnet", "RAD SHIELD", "THE CHONKE", "1st Stage", "2nd Stage")

#: How far a window may fall outside its file's span before that is an error
#: rather than a rounding.  One cadence of the coarsest table.
EDGE_TOL_S = 8.0


def _stamp(text: str) -> float:
    """ISO timestamp -> unix seconds.  The archive's clock is local, naive."""
    return _dt.datetime.fromisoformat(text).timestamp()


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Window:
    """One row of the manifest."""

    id: str
    kind: str
    file: str
    t_start: str
    t_end: str
    u_pct: float
    T_lo: float
    T_hi: float
    use: str
    quality: str
    note: str

    @property
    def start(self) -> float:
        return _stamp(self.t_start)

    @property
    def end(self) -> float:
        return _stamp(self.t_end)

    @property
    def span_s(self) -> float:
        return self.end - self.start

    def overlaps(self, other: "Window") -> bool:
        return (self.file == other.file
                and self.start < other.end and other.start < self.end)


@dataclass
class Table:
    """A whole archive table as arrays, plus the clock it was logged on."""

    file: str
    epoch: np.ndarray                    # absolute unix seconds
    t: np.ndarray                        # seconds from this table's first row
    segment: np.ndarray                  # increments at every recording gap
    chan: dict = field(default_factory=dict)
    note: list = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.epoch)

    @property
    def T(self) -> np.ndarray:
        return self.chan[SAMPLE]

    @property
    def Tc(self) -> np.ndarray:
        return self.chan[COLDPLATE]

    @property
    def u(self) -> np.ndarray:
        return self.chan[HEATER]

    def slice(self, start: float, end: float) -> slice:
        """Rows with ``start <= epoch <= end``, as a slice."""
        a = int(np.searchsorted(self.epoch, start, "left"))
        b = int(np.searchsorted(self.epoch, end, "right"))
        return slice(a, b)

    def segments(self):
        """(segment, a, b) for each contiguous recording run."""
        out, start = [], 0
        for i in range(1, len(self) + 1):
            if i == len(self) or self.segment[i] != self.segment[start]:
                out.append((int(self.segment[start]), start, i))
                start = i
        return out


@dataclass
class Slice:
    """One manifest window, sliced out of its table."""

    window: Window
    table: Table
    rows: slice

    def __len__(self) -> int:
        return self.rows.stop - self.rows.start

    def col(self, name: str) -> np.ndarray:
        return self.table.chan[name][self.rows]

    @property
    def epoch(self) -> np.ndarray:
        return self.table.epoch[self.rows]

    @property
    def t(self) -> np.ndarray:
        """Seconds from the start of THIS window."""
        e = self.epoch
        return e - e[0]

    @property
    def T(self) -> np.ndarray:
        return self.col(SAMPLE)

    @property
    def Tc(self) -> np.ndarray:
        return self.col(COLDPLATE)

    @property
    def u(self) -> np.ndarray:
        return self.col(HEATER)

    @property
    def segment(self) -> int:
        return int(self.table.segment[self.rows.start])


_CACHE: dict = {}


def read_table(file: str) -> Table:
    """A whole archive table as arrays.  Cached for the process's lifetime.

    Every column is float, blanks become NaN, and no row is dropped -- the
    manifest addresses rows by absolute time, so silently removing some would
    make a window's boundaries mean something different from what was written
    down.  Callers drop their own NaNs.
    """
    if file in _CACHE:
        return _CACHE[file]
    with open_table(file, ARCHIVE_DIR) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"segments: {file} is empty")

    def num(key):
        out = np.empty(len(rows))
        for i, r in enumerate(rows):
            try:
                out[i] = float(r[key])
            except (TypeError, ValueError, KeyError):
                out[i] = math.nan
        return out

    epoch = np.array([_stamp(r["Timestamp"]) for r in rows])
    names = [c for c in (SAMPLE, COLDPLATE, HEATER, *AUX) if c in rows[0]]
    table = Table(
        file=file,
        epoch=epoch,
        t=epoch - epoch[0],
        segment=num("segment"),
        chan={c: num(c) for c in names},
        note=[(r.get("note") or "").strip() for r in rows],
    )
    _CACHE[file] = table
    return table


def manifest_path() -> str:
    return resolve(MANIFEST, ARCHIVE_DIR)


def windows(path: str | None = None) -> list:
    """Every manifest row, in file order, validated as a set."""
    path = path or manifest_path()
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for i, r in enumerate(rows, start=2):        # +2: header, and 1-indexed
        try:
            w = Window(
                id=r["id"].strip(), kind=r["kind"].strip(), file=r["file"].strip(),
                t_start=r["t_start"].strip(), t_end=r["t_end"].strip(),
                u_pct=float(r["u_pct"]), T_lo=float(r["T_lo"]),
                T_hi=float(r["T_hi"]), use=r["use"].strip(),
                quality=r["quality"].strip(), note=(r.get("note") or "").strip(),
            )
        except (KeyError, ValueError) as exc:
            raise SystemExit(f"{path}:{i}: {exc}") from None
        out.append(w)
    _validate(out, path)
    return out


def by_kind(kind: str, path: str | None = None) -> list:
    if kind not in KINDS:
        raise SystemExit(f"segments: no such kind {kind!r}; want {KINDS}")
    return [w for w in windows(path) if w.kind == kind]


def by_use(use: str, path: str | None = None) -> list:
    if use not in USES:
        raise SystemExit(f"segments: no such use {use!r}; want {USES}")
    return [w for w in windows(path) if w.use == use]


def load(which, path: str | None = None) -> Slice:
    """One window, by id or by ``Window``, sliced out of its table."""
    if isinstance(which, Window):
        w = which
    else:
        found = [x for x in windows(path) if x.id == which]
        if not found:
            raise SystemExit(f"segments: no window with id {which!r}")
        w = found[0]
    table = read_table(w.file)
    rows = table.slice(w.start, w.end)
    if rows.stop - rows.start < 2:
        raise SystemExit(f"segments: {w.id} selects {rows.stop - rows.start} rows")
    seg = table.segment[rows]
    # A mask is allowed to span a gap: a transient does not stop happening
    # because the recorder dropped out in the middle of it.  Nothing is fitted
    # from one, so there is no integration to protect.
    if w.kind != "mask" and seg[0] != seg[-1]:
        raise SystemExit(
            f"segments: {w.id} spans a recording gap "
            f"(segment {int(seg[0])} -> {int(seg[-1])}).  Fit each segment as "
            f"its own trajectory; an ODE integrated across a gap converges on "
            f"a number anyway.")
    return Slice(window=w, table=table, rows=rows)


def _validate(ws: list, path: str) -> None:
    """Everything that can be checked without reading 900 thousand rows."""
    seen = set()
    for w in ws:
        if w.id in seen:
            raise SystemExit(f"{path}: duplicate id {w.id!r}")
        seen.add(w.id)
        if w.kind not in KINDS:
            raise SystemExit(f"{path}: {w.id}: kind {w.kind!r} not in {KINDS}")
        if w.use not in USES:
            raise SystemExit(f"{path}: {w.id}: use {w.use!r} not in {USES}")
        if w.file not in TABLES:
            raise SystemExit(f"{path}: {w.id}: {w.file!r} is not an archive table")
        if w.span_s <= 0:
            raise SystemExit(f"{path}: {w.id}: t_end is not after t_start")
    # Same kind twice over the same seconds means the same evidence counted
    # twice.  Different kinds may overlap on purpose: a hold sits inside the
    # trace that contains it, and only one of them is in any given fit.
    for i, a in enumerate(ws):
        for b in ws[i + 1:]:
            if a.kind == b.kind and a.overlaps(b):
                raise SystemExit(
                    f"{path}: {a.id} and {b.id} are both {a.kind!r} and overlap "
                    f"in {a.file} -- one dwell, two rows, double weight")


def check(path: str | None = None) -> list:
    """Load and slice every window.  Returns the report rows; raises on error.

    This is the expensive half of validation, and the half that catches a
    window written down against the wrong table: it opens all three tables and
    proves every window selects rows, inside one segment, within its file.
    """
    ws = windows(path)
    report = []
    for w in ws:
        table = read_table(w.file)
        lo, hi = table.epoch[0], table.epoch[-1]
        if w.start < lo - EDGE_TOL_S or w.end > hi + EDGE_TOL_S:
            raise SystemExit(
                f"segments: {w.id} is {w.t_start}..{w.t_end} but {w.file} runs "
                f"{_iso(lo)}..{_iso(hi)}")
        s = load(w)
        T, u = s.T, s.u
        report.append({
            "id": w.id, "kind": w.kind, "use": w.use, "file": w.file,
            "segment": s.segment, "n": len(s), "span_h": w.span_s / 3600.0,
            "u_pct": float(np.nanmean(u)),
            "T_lo": float(np.nanmin(T)), "T_hi": float(np.nanmax(T)),
            "quality": w.quality, "note": w.note,
        })
    return report


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="validate the cooldown-10 manifest and print it")
    ap.add_argument("-m", "--manifest", default=None)
    ap.add_argument("--tables", action="store_true",
                    help="print the archive's segment structure instead")
    a = ap.parse_args()

    if a.tables:
        for name in TABLES:
            table = read_table(name)
            print(f"\n{name}   {len(table)} rows, "
                  f"{(table.epoch[-1] - table.epoch[0]) / 3600:.2f} h")
            for seg, lo, hi in table.segments():
                print(f"  seg {seg}  {_iso(table.epoch[lo])} -> "
                      f"{_iso(table.epoch[hi - 1])}  "
                      f"{(table.epoch[hi - 1] - table.epoch[lo]) / 3600:8.2f} h  "
                      f"{hi - lo:7d} rows")
        raise SystemExit(0)

    rows = check(a.manifest)
    print(f"{os.path.relpath(a.manifest or manifest_path())}: "
          f"{len(rows)} windows, all valid\n")
    print(f"{'id':<28}{'kind':<7}{'use':<9}{'span h':>8}{'u%':>8}"
          f"{'T lo':>8}{'T hi':>8}{'n':>8}  quality")
    for r in rows:
        print(f"{r['id']:<28}{r['kind']:<7}{r['use']:<9}{r['span_h']:>8.2f}"
              f"{r['u_pct']:>8.3f}{r['T_lo']:>8.2f}{r['T_hi']:>8.2f}"
              f"{r['n']:>8d}  {r['quality']}")
    for kind in KINDS:
        n = sum(1 for r in rows if r["kind"] == kind)
        h = sum(r["span_h"] for r in rows if r["kind"] == kind)
        print(f"\n{kind:<7}{n:>4} windows{h:>10.1f} h", end="")
    print()
