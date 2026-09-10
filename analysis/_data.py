"""Where the fit inputs live, and how to open them.

Two directories, and the second is where new work goes.

``reference/cooldown-10/`` -- **the archive.**  Three non-overlapping tables
that between them are the whole of cooldown 10, plus ``segments.csv``, the
manifest naming the windows inside them.  Everything that reads data reads it
from here now, by window rather than by file: see :mod:`segments`.

``reference/heater-calibration/`` -- **one derived file, and it is the odd one
out.**  ``sweep_decimated.csv.gz`` is the 43 h sweep window adaptively thinned
by ``decimate.py``: 4,968 rows against 77,374, and it is what the expensive fit
actually reads.  Committed because the alternative is a clone where the pipeline
has a twenty-second preparation step nobody documents.  It is the last thing in
that directory -- the five overlapping tables it used to sit beside are gone,
replaced by the archive -- so the directory itself is a candidate for
retirement, deliberately not done in passing.

Both are versioned, which reverses this repository's usual rule that derived
data is gitignored, and the reason is what they are derived *from*: recorder
logs in the gitignored ``data/``, which a fresh clone does not have.  Gzipped
because git stores the same compressed bytes either way, so plain CSV would
only buy 79 MB in everybody's working tree instead of 13 -- including the
coworkers who wanted the strip chart and nothing else.

``open_table`` is what every reader here goes through.  It accepts a bare name
or a path, resolves it against both directories and against the repository root
as well as the working directory, transparently opens ``.gz``, and when a file
really is missing it says which one and what to do about it rather than raising
FileNotFoundError from inside csv.DictReader.
"""
from __future__ import annotations

import gzip
import io
import os

#: Down to ``sweep_decimated.csv.gz`` -- see the module docstring.  Still the
#: first place a bare name is looked up, because that is where the one file in
#: it lives.
DATA_DIR = os.path.join("reference", "heater-calibration")

#: The cooldown-10 archive: three non-overlapping tables that between them are
#: the whole cooldown, with ``reference/cooldown-10/segments.csv`` naming the
#: windows inside them.  It replaced five overlapping tables that used to live
#: in ``DATA_DIR`` -- two region exports and three flattened logs, in which
#: every dwell appeared two or three times -- see :mod:`segments` and
#: ``REFIT_PLAN.md`` §5.
ARCHIVE_DIR = os.path.join("reference", "cooldown-10")

#: The repository root, taken from this file's own location and not from the
#: working directory.  Both directories above are relative, and a relative path
#: run from anywhere but the repository root resolves to nothing at all -- which
#: has already cost this repository seven tests that announced themselves as
#: "reference logs not present".
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def resolve(name: str, where: str = DATA_DIR) -> str:
    """Full path for a table, whether it is stored plain or gzipped.

    ``where`` is tried first; the other versioned directory is tried after it,
    because the two hold disjoint names and a caller that gets the argument
    wrong should get the file rather than a lecture.  Each is tried relative to
    the working directory and relative to ``REPO_ROOT``, so a script works from
    the repository root and from anywhere else.
    """
    if os.path.sep in name or "/" in name:
        candidates = [name, name + ".gz",
                      os.path.join(REPO_ROOT, name),
                      os.path.join(REPO_ROOT, name + ".gz")]
    else:
        candidates = []
        for directory in (where, *(d for d in (DATA_DIR, ARCHIVE_DIR)
                                   if d != where)):
            for base in (os.path.join(directory, name),
                         os.path.join(REPO_ROOT, directory, name)):
                candidates += [base + ".gz", base]
        candidates += [name, name + ".gz"]
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


def open_table(name: str, where: str = DATA_DIR):
    """Text handle on a fit input, gzipped or not.  Use as a context manager."""
    path = resolve(name, where)
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8",
                                newline="")
    return open(path, newline="", encoding="utf-8")
