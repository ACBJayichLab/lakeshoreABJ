"""What the judge writes down: ``plant.json`` and ``plant_YYYY-MM-DD.csv``.

The same file interface the recorder uses, for the same reasons -- see
``lschart/ipc/status.py``'s docstring, which is the long form of all of this.
Two rules carry over verbatim and are not restated there because they are not
obvious from the code:

**Arrays, not objects.**  MATLAB's ``jsondecode`` passes object *keys* through
``makeValidName``, so ``{"missing_power": ...}`` survives and
``{"1st Stage": ...}`` does not.  A name that lives in a *value* survives
verbatim, and every element carrying the same fields is what makes
``jsondecode`` return a struct array instead of a cell array of dissimilar
structs.

**Rewritten in full, atomically.**  A reader must never see half a verdict.

And one rule that does not carry over: **there is no schema negotiation with
the recorder.**  ``plant.json`` sits beside ``status.json`` and is written by a
different process; a client reads whichever it wants and the two are versioned
separately.  The monitor never writes ``status.json`` and never reads the
command spool -- it has no way to ask the recorder for anything, which is the
point of it being a separate process with no port.
"""

from __future__ import annotations

import csv
import datetime as _dt
import math
import os

from lschart.ipc.status import atomic_write_json

#: Bumped when the meaning of a field changes.  Separate from the recorder's
#: ``status.json`` version: the two files are written by different processes
#: and a client may well be reading one and not the other.
SCHEMA_VERSION = 1

#: One row per cycle, and the column order is the file's contract.
CSV_COLUMNS = (
    "Timestamp", "t_s", "segment", "Sample", "Coldplate", "u_pct",
    "dT_dt_k_per_s", "missing_power_w", "missing_power_abs_w", "sigma_q_w",
    "baseline_frac", "baseline_age_s", "verdict",
    "missing_power", "coldplate", "tau", "noise", "fault_level",
)


def _finite(x):
    """JSON has no NaN.  ``None`` is the honest spelling of "no number"."""
    if x is None:
        return None
    try:
        return x if math.isfinite(x) else None
    except TypeError:
        return None


def payload(record: dict, *, cfg=None, stale_after_s: float | None = None) -> dict:
    """One cycle as ``plant.json``'s whole content."""
    verdicts = list(record["verdicts"]) + [record["fault_level"]]
    return {
        "schema": SCHEMA_VERSION,
        "written": _dt.datetime.now().isoformat(timespec="milliseconds"),
        "epoch": _finite(record.get("epoch_s")),
        "t_s": _finite(record.get("t_s")),
        "segment": record.get("segment"),
        "stale_after_s": stale_after_s,
        "verdict": record["verdict"],
        "sample_k": _finite(record.get("sample_k")),
        "coldplate_k": _finite(record.get("coldplate_k")),
        "u_pct": _finite(record.get("u_pct")),
        "dT_dt_k_per_s": _finite(record.get("dT_dt_k_per_s")),
        # The two numbers a person actually asks for: how far the residual has
        # moved since the baseline, and where the LEVEL has got to since the
        # gauge -- which is what says when the next recalibration is due.
        "missing_power_abs_w": _finite(record.get("missing_power_abs_w")),
        "baseline_frac": _finite(record.get("baseline_w")),
        "baseline_age_s": _finite(record.get("baseline_age_s")),
        "residuals": [
            {"name": v.name, "state": v.state, "value": _finite(v.value),
             "sigma": _finite(v.sigma), "reason": v.reason,
             "out_of_band_s": _finite(v.out_of_band_s)}
            for v in verdicts
        ],
        "config": None if cfg is None else {
            "warn_sigma": cfg.warn_sigma, "warn_after_s": cfg.warn_after_s,
            "fault_mw": cfg.fault_mw, "fault_after_s": cfg.fault_after_s,
            "baseline_tau_s": cfg.baseline_tau_s,
            "settle_taus": cfg.settle_taus,
            "min_output_pct": cfg.min_output_pct,
        },
    }


def write_json(path, record: dict, **kw) -> bool:
    """``plant.json``, in full, atomically.  Never raises."""
    return atomic_write_json(path, payload(record, **kw))


#: The daily log's filename prefix.  Named here rather than left as a default
#: argument because :mod:`ltspm3.monitor.source` has to know it: the reader and
#: the writer share a directory, and the reader must not pick up the writer's
#: output.  See ``RecorderTail._current_path``.
PLANT_PREFIX = "plant"


class PlantLog:
    """``plant_YYYY-MM-DD.csv``: one row per cycle, rolled at midnight.

    The same shape as the recorder's own log and for the same reason -- what
    the monitor thought at the time is evidence, and an alarm nobody can go
    back and look at is an alarm nobody believes the second time.
    """

    def __init__(self, directory: str, prefix: str = PLANT_PREFIX) -> None:
        self.directory = directory
        self.prefix = prefix
        self.path: str | None = None
        self._day: _dt.date | None = None
        self._fh = None
        self._writer: csv.DictWriter | None = None
        self.rows_written = 0

    def _open_for(self, day: _dt.date) -> None:
        os.makedirs(self.directory, exist_ok=True)
        path = os.path.join(self.directory, f"{self.prefix}_{day.isoformat()}.csv")
        exists = os.path.exists(path) and os.path.getsize(path) > 0
        self.close()
        # Line buffered and flushed: the point is that the file on disk is
        # current, not that the writes are cheap.
        self._fh = open(path, "a", newline="", buffering=1)
        self._writer = csv.DictWriter(self._fh, fieldnames=list(CSV_COLUMNS),
                                      extrasaction="ignore")
        if not exists:
            self._writer.writeheader()
            self._fh.flush()
        self.path, self._day = path, day

    def write(self, record: dict) -> None:
        epoch = record.get("epoch_s")
        when = (_dt.datetime.fromtimestamp(epoch) if epoch
                else _dt.datetime.now())
        if self._writer is None or when.date() != self._day:
            self._open_for(when.date())
        states = {v.name: v.state
                  for v in list(record["verdicts"]) + [record["fault_level"]]}
        power = next((v for v in record["verdicts"]
                      if v.name == "missing_power"), None)
        self._writer.writerow({
            "Timestamp": when.isoformat(timespec="milliseconds"),
            "t_s": f"{record['t_s']:.3f}",
            "segment": record.get("segment", 0),
            "Sample": _fmt(record.get("sample_k"), 4),
            "Coldplate": _fmt(record.get("coldplate_k"), 4),
            "u_pct": _fmt(record.get("u_pct"), 4),
            "dT_dt_k_per_s": _fmt(record.get("dT_dt_k_per_s"), 8),
            "missing_power_w": _fmt(power.value if power else None, 8),
            "missing_power_abs_w": _fmt(record.get("missing_power_abs_w"), 8),
            "sigma_q_w": _fmt(power.sigma if power else None, 8),
            "baseline_frac": _fmt(record.get("baseline_w"), 8),
            "baseline_age_s": _fmt(record.get("baseline_age_s"), 1),
            "verdict": record["verdict"],
            **states,
        })
        self._fh.flush()
        self.rows_written += 1

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
        self._fh = self._writer = None


def _fmt(value, places: int) -> str:
    v = _finite(value)
    return "" if v is None else f"{v:.{places}f}"
