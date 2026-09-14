"""The judge, outside the loop.  ``python -m ltspm3.monitor -c CONFIG``.

PID phase 2.  A separate process, like the viewer: **no port, no commands,
ever.**  It tails the recorder's CSV, applies the model's band, and writes what
it thinks into ``plant.json`` and a daily ``plant_*.csv`` beside the
recorder's own files.  It runs whether or not the loop is armed -- which is
most of this cryostat's life so far -- and it is the reference implementation
plan 3's in-loop check is tested against.

Why it cannot command anything
------------------------------

Not because it is configured not to: because it never opens a transport and
never writes into the command spool.  The recorder owns the port exclusively
(invariant 2) and this holds no instrument object at all, so there is no code
path from a verdict to a heater.  That is what "report only" means here --
PID_PLAN.md section 4, settled 2026-09-11.

Read :mod:`~ltspm3.monitor.judge` first; it is where the reasoning is.
"""

from .judge import (
    NO_OPINION,
    TYPICAL,
    WARN,
    Judge,
    MonitorConfig,
    Verdict,
)
from .report import PlantLog, payload, write_json
from .source import RecorderTail, Sample, replay

__all__ = [
    "Judge", "MonitorConfig", "Verdict", "TYPICAL", "NO_OPINION", "WARN",
    "PlantLog", "payload", "write_json",
    "RecorderTail", "Sample", "replay",
]
