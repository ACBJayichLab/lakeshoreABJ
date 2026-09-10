"""Where the fit inputs live, and how to open them.

They live in ``reference/heater-calibration/``, gzipped, and they are in the
repository.  That is a deliberate reversal of this repo's usual rule that
derived data is gitignored, and the reason is what they are derived *from*:

* ``region_*_complete_sweep_even_larger.csv`` is a recorder export of a run
  that happened once -- 43 h, 4.9-192.6 K, 2026-09-02 16:01 to 09-04 11:00.
  Nothing regenerates it.  Losing it means running another sweep on the
  cryostat.  Its ``Coldplate`` column was remapped onto the corrected curve on
  2026-09-05; every other column is as the recorder wrote it.
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
#: The PROGRAMMED ladder, 2026-09-05 -- 30 rungs from 7.19% to 63.70%, run by
#: `ltspm3.tools.sweep` against the live recorder in 4 h 17 min, plus an aborted
#: 13-rung attempt earlier the same day and the material before it.
#:
#: It exists because the 43 h sweep above was walked by hand and dwelt where
#: somebody was watching: between 40 K and 98 K it left **no settled point at
#: all**, and that band is where the local gain runs from 4 to 13 K/%.  This one
#: put 19 graded points into 26-105 K where there had been 8, four of which were
#: one CD10 hold counted four times.
#:
#: What it showed is why it was worth running: the model fitted to the 43 h
#: sweep was **low by up to 4.5 K** through the hole it was interpolating
#: across -- 25 times its own 0.168 K residual -- while tau came back within 3%
#: from 77 K to 114 K.  The dynamics travelled; the steady state did not.
#:
#: Post-cutover, so its Coldplate is on the corrected X186279 curve as written
#: and needs no remap, unlike everything above.
LADDER = "region_20260905-114532_many_tau_steps.csv"
#: Flattened recorder and CD10 logs -- the dwells steps.py extracts from.
#:
#: ``fit_cd10`` is the EARLY part of this same cooldown -- cooldown 10 began on
#: 2026-07-15 and is still running -- logged by the pre-Python chart recorder,
#: so its heater column is reconstructed from the commands in the Notes rather
#: than read back.  It is not a different cooldown, whatever an earlier
#: revision of this file and of analysis/README.md said.  It IS a different
#: STATE: at seven outputs where the two overlap the sample sits 1.8-2.9 K
#: warmer in the September logs than it did in July and August, same sign every
#: time, which is why fit_ode fits it a free power offset.
FIT_RECORDER = "fit_recorder.csv"
FIT_CD10 = "fit_cd10.csv"
#: The recorder's own log from just after the 2026-09-04 12:07 Coldplate
#: recalibration to 2026-09-09 16:14 -- 112.6 h, one segment, 2 s cadence.
#:
#: It is here for three holds that nothing else in this directory has, all of
#: them settled to under 1.4 mK/h and all in the band the 43 h sweep walked
#: straight through:
#:
#:     11.52 h at 62.347%   ->  96.516 K
#:     69.94 h at 63.699%   -> 114.390 K
#:     24.47 h at 64.015%   -> 118.609 K
#:
#: Those are the measurements that condemned the first fit: it reads them
#: 4.35, 4.42 and 4.44 K low.  They also check the 2026-09-05 ladder, whose
#: 114.28 K rung -- rejected by the old end-rate bar -- differs from the 69.9 h
#: hold at the same output by 0.11 K.
#:
#: The 63.699 -> 64.015% step on 2026-09-08 is a clean 4 K relaxation with a
#: full settle after it, which is a tau at 118 K measured rather than inferred.
#:
#: Entirely POST-cutover, so its Coldplate is on the corrected X186279 curve as
#: the recorder wrote it and it needs no remap, unlike everything above.
FIT_RECORDER_POSTCAL = "fit_recorder_postcal.csv"


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
