"""``status.json`` -- what the recorder is doing, readable by anyone.

The recorder owns the instrument link exclusively, so every other program that
wants to know the temperature has to be told rather than go and look.  This is
how it is told: one small JSON file, rewritten in full every cycle.

Why a whole file every second, and not a socket
-----------------------------------------------

A socket puts a connection state machine inside the process that must never
die, and its failure mode is quiet -- a dead server thread keeps recording
perfectly while silently ignoring every reader.  A file has no connection
state at all: the recorder never learns that MATLAB or the viewer exists, which
is the strongest available form of "do not crash if the client does".

Rewritten in *full* rather than appended to, because a reader must never see
half a sample.  ``os.replace`` is atomic on both POSIX and Windows, so a reader
either sees the previous cycle or this one and never a torn mixture.

Why the write may fail, and why that is fine
--------------------------------------------

On Windows, replacing a file that another process currently has open can fail
with a sharing violation.  There is nothing to do about that and nothing that
needs doing: the next cycle rewrites it a second later.  So a failed write is
counted and logged at DEBUG, never raised -- an IPC convenience must not be
able to stop the recording it is reporting on.

Why the shape is arrays and not objects
---------------------------------------

Channels are ``[{"name": ..., "kelvin": ...}, ...]`` rather than
``{"Rad Shield": 295.3}``.  MATLAB's ``jsondecode`` passes object *keys*
through ``matlab.lang.makeValidName``, so "Rad Shield" silently becomes some
other string and "Stage 2" collides with nothing predictable.  A name that
lives in a *value* survives verbatim.  Every element carries the same fields,
which is what makes ``jsondecode`` return a struct array rather than a cell
array of dissimilar structs.
"""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import time
from pathlib import Path
from typing import Any

from ..model import Frame

log = logging.getLogger(__name__)

#: Bumped when the meaning of a field changes.  A reader that checks this can
#: say "this recorder is newer than I am" instead of quietly misreading it.
SCHEMA_VERSION = 1


def _num(value: Any) -> Any:
    """JSON cannot carry NaN or infinity, and neither can ``jsondecode``.

    ``json.dumps`` emits a bare ``NaN`` token by default, which is not JSON and
    which every strict parser -- MATLAB's included -- rejects outright.  One
    unusable reading would therefore make the whole status file unparseable, so
    non-finite values become ``null`` and the reader treats them as missing.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def atomic_write_json(path: str | os.PathLike, payload: dict) -> bool:
    """Write ``payload`` to ``path`` so no reader can see it half-written.

    Returns True on success.  Never raises: see the module docstring.
    """
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # allow_nan=False turns a stray NaN into an exception here rather than
        # into an unparseable file at the reader.  _num() should have caught
        # them all; this is the assertion that says so.
        text = json.dumps(payload, indent=1, allow_nan=False)
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError) as exc:
        log.debug("status write to %s failed: %s", path, exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def read_status(path: str | os.PathLike) -> dict | None:
    """Read a status file, or ``None`` if it is absent or mid-rewrite.

    A caller polling this should tolerate ``None`` without comment: on Windows
    the file can briefly be unreadable while it is being replaced, and the
    honest answer for one poll is "ask again".
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def status_age_s(status: dict, *, now: float | None = None) -> float | None:
    """Seconds since the recorder last wrote this file.

    Wall clock, because it is the only clock two processes share -- and so an
    NTP step can make it briefly nonsense.  A reader that wants to be sure the
    recorder is *alive* rather than merely recent should also watch ``cycle``
    advance, which no clock adjustment can fake.
    """
    t = status.get("t_wall")
    if not isinstance(t, (int, float)):
        return None
    return max(0.0, (time.time() if now is None else now) - float(t))


class StatusWriter:
    """Renders one :class:`~lschart.model.Frame` per cycle into ``status.json``."""

    def __init__(self, path: str | os.PathLike, *, config_path: str | None = None) -> None:
        self.path = Path(path)
        self.config_path = config_path
        self.started_at = time.time()
        self.writes = 0
        self.failures = 0
        #: The most recent payload, so the CLI and tests can inspect what was
        #: written without re-reading (and re-parsing) the file.
        self.last: dict | None = None

    # -- rendering ---------------------------------------------------------

    @staticmethod
    def _channels(frame: Frame) -> list[dict]:
        out = []
        for name, r in frame.readings.items():
            out.append({
                "name": name,
                "kelvin": _num(r.kelvin),
                "sensor_units": _num(r.sensor_units),
                "validity": r.validity.value,
                "usable": bool(r.usable),
                "status": int(r.status),
            })
        return out

    @staticmethod
    def _pairs(mapping: dict, value_key: str = "value") -> list[dict]:
        """``{k: v}`` as ``[{"name": k, value_key: v}]`` -- see the docstring."""
        return [{"name": str(k), value_key: v} for k, v in mapping.items()]

    @staticmethod
    def _links(instruments: list) -> list[dict]:
        links = []
        for inst in instruments:
            t = getattr(inst, "transport", None)
            link = {
                "name": getattr(inst, "name", str(inst)),
                "model": str(getattr(inst, "model", "")),
                "up": bool(getattr(t, "is_up", False)),
                "consecutive_failures": int(getattr(t, "consecutive_failures", 0)),
                "reconnects": int(getattr(t, "reconnects", 0)),
                "last_error": str(getattr(t, "last_error", "") or ""),
                "writable": bool(getattr(inst, "allow_writes", False)),
            }
            link.update(StatusWriter._capabilities(inst))
            links.append(link)
        return links

    @staticmethod
    def _capabilities(inst) -> dict:
        """What this box can be *asked to do*, for a client building controls.

        A viewer that offers a loop selector on a box with no loops, or a
        heater-range control on a 218, is a viewer that generates refusals for
        a living.  Rather than have every client keep its own table of what a
        model number implies -- which is the same table going stale in three
        places -- the recorder says what the instrument it actually opened can
        do.

        Read duck-typed and defaulted, like everything else in this module, so
        an instrument class that does not exist yet does not have to be known
        here to be reported correctly.  Absent capabilities are empty, never
        missing: a client can then tell "this box has no loops" from "this
        recorder is too old to say".
        """
        caps = getattr(inst, "caps", None)
        analog = getattr(inst, "analog", None)
        return {
            # 33x: the loops it will accept a setpoint on, and the outputs that
            # have a power range.
            "loops": [int(n) for n in getattr(caps, "loops", ()) or ()],
            "heater_outputs": [
                int(n) for n in getattr(caps, "heater_outputs", ()) or ()
            ],
            # 218: the one output that is a heater, and the ceiling on it.
            # `None` means this box has no settable analog output at all --
            # distinct from output 0, which is a real output number.
            "analog_output": (
                int(getattr(analog, "output", 1))
                if hasattr(inst, "set_analog_percent") else None
            ),
            "max_output_pct": float(getattr(inst, "max_output_pct", 100.0)),
        }

    @staticmethod
    def _control(status) -> dict | None:
        """A software loop's state, projected generically.

        ``lschart`` must not import ``ltspm3``, so nothing here knows what a
        ``SupervisorStatus`` is -- every field is read by name and defaulted.
        A recorder-only install passes ``None`` and the key is simply absent.
        """
        if status is None:
            return None

        def enum_value(name):
            v = getattr(status, name, None)
            return getattr(v, "value", v if v is None else str(v))

        return {
            "state": enum_value("state"),
            "mode": enum_value("mode"),
            "health": enum_value("health"),
            "setpoint_k": _num(getattr(status, "setpoint_k", None)),
            "setpoint_target_k": _num(getattr(status, "setpoint_target_k", None)),
            "ramping": bool(getattr(status, "ramping", False)),
            "error_k": _num(getattr(status, "error_k", None)),
            "output_pct": _num(getattr(status, "output_pct", None)),
            "alarms": [str(a) for a in getattr(status, "alarms", []) or []],
            "reason": str(getattr(status, "reason", "") or ""),
        }

    def payload(
        self,
        frame: Frame,
        *,
        cycles: int = 0,
        dropped_cycles: int = 0,
        interval_s: float = 0.0,
        instruments: list | None = None,
        recorder=None,
        control=None,
        commands: dict | None = None,
        running: bool = True,
    ) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "generator": "lschart",
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "config": self.config_path or "",
            "started_at": self.started_at,
            # Wall clock, so another process can compare it with its own.
            "t_wall": frame.t_wall,
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(frame.t_wall)),
            "uptime_s": max(0.0, frame.t_wall - self.started_at),
            # A counter no clock adjustment can fake -- the liveness signal
            # that survives an NTP step.  See status_age_s().
            # False in the final file a clean shutdown leaves behind.  A
            # client can then tell "the recorder stopped" from "the recorder
            # is hung", which an age alone cannot distinguish.
            "running": bool(running),
            "cycle": int(cycles),
            "dropped_cycles": int(dropped_cycles),
            "interval_s": float(interval_s),
            "channels": self._channels(frame),
            "aux": self._pairs({k: _num(v) for k, v in frame.aux.items()}),
            "errors": self._pairs({k: str(v) for k, v in frame.errors.items()},
                                  value_key="message"),
            "links": self._links(instruments or []),
            "recorder": {
                "path": str(getattr(recorder, "path", "") or ""),
                "rows": int(getattr(recorder, "rows_written", 0)),
            },
            "control": self._control(control),
            "commands": commands or {},
        }

    def write(self, frame: Frame, **kw) -> bool:
        payload = self.payload(frame, **kw)
        self.last = payload
        ok = atomic_write_json(self.path, payload)
        if ok:
            self.writes += 1
        else:
            self.failures += 1
        return ok
