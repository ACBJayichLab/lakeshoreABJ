"""Where the fit inputs live, and how to open them.

They live in ``reference/heater-calibration/``, gzipped, and they are in the
repository.  That is a deliberate reversal of this repo's usual rule that
derived data is gitignored, and the reason is what they are derived *from*:

* ``region_*_complete_sweep.csv`` is a recorder export of a run that happened
  once -- 8.8 h, 5-187 K, 2026-09-03.  Nothing regenerates it.  Losing it means
  running another sweep on the cryostat.
* ``fit_recorder.csv`` is flattened from the recorder's own 2026-08/09 logs,
  which are **not** in the repository.  Derived, but from a source that is
  gone as far as a fresh clone is concerned, so it is primary in practice.
* ``fit_cd10.csv`` is the one genuinely regenerable file -- it comes from
  ``reference/logs/CD10/*.xls``, which is versioned.  It is committed anyway,
  because the alternative is a clone where step one of the pipeline fails
  until somebody runs a two-command dance they have to find first.

Gzipped because git stores the same compressed bytes either way, so the only
thing plain CSV would buy is 79 MB in everybody's working tree instead of 13 --
including the coworkers who only ever wanted the strip chart.

``open_table`` is what every reader here goes through.  It accepts a bare name
or a path, transparently opens ``.gz``, and when a file really is missing it
says which one and what to do about it rather than raising FileNotFoundError
from inside csv.DictReader.
"""
from __future__ import annotations

import gzip
import io
import os

#: Versioned, and the default location for everything the fits read.
DATA_DIR = os.path.join("reference", "heater-calibration")

#: The sweep the ODE is fitted to: 2026-09-02 16:01 -> 2026-09-04 11:00, 43 h,
#: 4.9-192.6 K, 2 s cadence, no gap longer than a minute.
#:
#: This is the WIDE export, and it is the one to use.  The 8.8 h cut of the
#: same run that was here before caught only the middle: it saw 2.2 h of the
#: 22.8 h hold at 180 K and 0.4 h of the 13.9 h hold at 192 K, which is what
#: pins the slow bath behaviour.  It ends 67 minutes BEFORE the 12:07:16
#: 2026-09-04 Coldplate recalibration, so it is entirely pre-cutover and
#: internally consistent -- see docs/ltspm3/cryostat.md.
SWEEP = "region_20260903-123832_complete_sweep_even_larger.csv"
#: Flattened recorder and CD10 logs -- the dwells steps.py extracts from.
FIT_RECORDER = "fit_recorder.csv"
FIT_CD10 = "fit_cd10.csv"


def resolve(name: str) -> str:
    """Full path for a table, whether it is stored plain or gzipped."""
    if os.path.sep in name or "/" in name:
        candidates = [name, name + ".gz"]
    else:
        base = os.path.join(DATA_DIR, name)
        candidates = [base + ".gz", base, name, name + ".gz"]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise SystemExit(_missing(name, candidates))


def _missing(name: str, tried) -> str:
    return (
        f"\nanalysis: cannot find the input table {name!r}.\n"
        f"  looked in: {', '.join(tried)}\n\n"
        f"  The fit inputs are versioned in {DATA_DIR}/ and should be present\n"
        f"  in any clone.  If that directory is empty the clone is incomplete;\n"
        f"  if you are pointing at data/ instead, drop the path and pass the\n"
        f"  bare filename -- these are resolved against the repository, not the\n"
        f"  working directory.\n\n"
        f"  See analysis/README.md.\n"
    )


def open_table(name: str):
    """Text handle on a fit input, gzipped or not.  Use as a context manager."""
    path = resolve(name)
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8",
                                newline="")
    return open(path, newline="", encoding="utf-8")
