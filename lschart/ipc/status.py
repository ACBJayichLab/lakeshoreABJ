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
with a sharing violation.  There is nothing to do about that and mostly nothing
that needs doing: the next cycle rewrites it a second later.  It is never
raised -- an IPC convenience must not be able to stop the recording it is
reporting on.

But it does need to be *noticeable*.  A write that fails cannot report itself
in the file it failed to write, so what a client sees is a gap in the feed and
nothing else -- indistinguishable from a recorder that hung.  So the **edges**
are logged at WARNING (the first failure, and the recovery) while everything
between them stays at DEBUG, and the counters go into the next file that does
get written: `status_file.failures` will have jumped and `last_error` says why.
One log line per second for as long as a condition lasts is how a real signal
gets buried, which is why it is the edges and not every cycle.

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
#:
#: 2 -- ``links[].loops`` became an array of loop *objects* describing what
#: each loop is bound to, alongside the plain list of loop numbers that was
#: there before (now ``links[].loop_numbers``).  A client written against 1
#: keeps working: ``capabilities()`` in the viewer's source module is the
#: worked example of degrading rather than assuming.
SCHEMA_VERSION = 2


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


def atomic_write_json(path: str | os.PathLike, payload: dict,
                      *, on_error=None) -> bool:
    """Write ``payload`` to ``path`` so no reader can see it half-written.

    Returns True on success.  Never raises: see the module docstring.

    ``on_error`` is called with the exception when the write fails.  It exists
    because "it failed" is not enough to act on and the caller is the only one
    that knows whether this failure is the first or the thousandth -- see
    :meth:`StatusWriter.write`.  Returning the message instead would invert the
    truthiness of the return value, which is the sort of thing that reads fine
    and then gets a ``not`` wrong somewhere.
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
        if on_error is not None:
            on_error(exc)
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
        #: What went wrong the last time, and when.  A durable record and not
        #: a live flag: it is never cleared, because "when did this last fail"
        #: stays worth knowing after it recovers.  Published in the next file
        #: that *does* get written, which is the only place it can surface --
        #: a write that failed cannot report itself in the file it failed to
        #: write.  So the signal a client sees is a gap in the feed followed by
        #: a counter that jumped, and these say when and why.
        self.last_error = ""
        self.last_failure_t = 0.0
        #: Edge detector for the log, and the only thing here that is a live
        #: flag.  Not published: a client cannot act on it -- by the time they
        #: read the file, it is false by construction.
        self._failing = False
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
    def _links(instruments: list, aux: dict | None = None) -> list[dict]:
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
            link["loops"] = StatusWriter._loops(inst, aux or {})
            links.append(link)
        return links

    @staticmethod
    def _loops(inst, aux: dict) -> list[dict]:
        """One entry per loop: what it reads, what it is doing, where it is.

        **An array of uniform objects, not an object keyed by loop number.**
        MATLAB's ``jsondecode`` runs object keys through ``makeValidName``, so
        ``{"1": ...}`` arrives as a field called ``x1``; and an array whose
        elements all carry the same fields is what makes ``jsondecode`` return
        a struct array rather than a cell array of dissimilar structs.  Every
        key below is therefore present on every entry, ``null`` where the
        recorder has nothing to say.

        Two halves joined here and nowhere else.  The instrument supplies what
        it read from ``OUTMODE?`` -- the sensor, the mode, the heater output --
        and the frame's aux block supplies the numbers that move.  Joining them
        in one place is what stops a client reading the setpoint twice and
        getting two answers.

        Duck-typed like everything else in this module: a box with no
        ``loop_rows`` has no loops to report, and says so with an empty list
        rather than with a missing key.
        """
        rows = getattr(inst, "loop_rows", None)
        if not callable(rows):
            return []
        name = getattr(inst, "name", "")
        out = []
        for row in rows():
            loop = int(row.get("loop"))
            heater = row.get("heater_output")
            # A loop with a heater reports HTR? as its output; one whose output
            # is analog-only (a 336's 3 and 4) reports AOUT?.  Both are a
            # percentage of full scale, which is why they share a column.
            pct_key = (f"{name}.heater{heater}" if heater is not None
                       else f"{name}.aout{loop}")
            range_value = (aux.get(f"{name}.range{heater}")
                           if heater is not None else None)
            out.append({
                "loop": loop,
                "sensor": str(row.get("sensor") or ""),
                "input": str(row.get("input") or ""),
                "mode": str(row.get("mode") or ""),
                "mode_code": (None if row.get("mode_code") is None
                              else int(row["mode_code"])),
                "heater_output": None if heater is None else int(heater),
                "setpoint_k": _num(aux.get(f"{name}.setpoint{loop}")),
                "output_pct": _num(aux.get(pct_key)),
                # An enumeration, so an int -- but null rather than 0 where
                # there is no range to have, because 0 is "off" and would read
                # as a fact about a loop that has no ranges at all.
                "range": None if range_value is None else int(range_value),
                "threshold_k": _num(row.get("threshold_k")),
                "ramping": bool(row.get("ramping")),
                # The instrument's own gains, polled on a slow cadence.  Null
                # on a recorder with `read_pid: false`, which a client has to
                # handle anyway -- so no schema bump buys anything here.
                "p": _num(aux.get(f"{name}.p{loop}")),
                "i": _num(aux.get(f"{name}.i{loop}")),
                "d": _num(aux.get(f"{name}.d{loop}")),
            })
        return out

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
            # have a power range.  `loop_numbers` and not `loops`: schema 2
            # gave `loops` to the array of loop *objects*, and one key cannot
            # be two shapes.
            "loop_numbers": [int(n) for n in getattr(caps, "loops", ()) or ()],
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
    def _control(status, controller=None, channel: str | None = None) -> dict | None:
        """A software loop's state, projected generically.

        ``lschart`` must not import ``ltspm3``, so nothing here knows what a
        ``SupervisorStatus`` is -- every field is read by name and defaulted.
        A recorder-only install passes ``None`` and the key is simply absent.

        Two of these come from outside the status object, because the software
        loop's own answers to "what does it read" and "where are its rails" do
        not change from cycle to cycle and so were never put in a per-cycle
        struct.  They are what let a client draw this loop in the same table as
        the instrument's:

        ``sensor``
            The channel the loop controls -- the recorder's ``control_channel``,
            which is the same string the trace and the readout carry.  Without
            it a client can show the loop's temperature only by being told
            separately which one it is.
        ``rail_low_pct`` / ``rail_high_pct``
            The authority band the supervisor actually enforces.  A software
            loop pinned at *its* clamp has run out of authority exactly the way
            a heater at 100% has, but the number is nowhere near 100 -- on this
            cryostat the band is about a percent wide.  Judging it against the
            fixed rails a heater output uses would mean never lighting the mark.
        ``threshold_k``
            The loop's own "this should only ever be a small correction"
            premise (``max_error_k``).  Null where the controller does not
            offer one, which a client must already handle: a loop with no
            configured threshold gets no opinion about being settled.

        All three are read duck-typed and default to ``None``, so a controller
        that does not have them is reported as having nothing to say rather
        than misreported.
        """
        if status is None:
            return None

        def enum_value(name):
            v = getattr(status, name, None)
            return getattr(v, "value", v if v is None else str(v))

        band = getattr(controller, "band", None)
        try:
            rail_low, rail_high = (float(band[0]), float(band[1]))
        except (TypeError, ValueError, IndexError):
            rail_low = rail_high = None
        cfg = getattr(controller, "cfg", None)

        return {
            "state": enum_value("state"),
            "mode": enum_value("mode"),
            "health": enum_value("health"),
            "sensor": str(channel or ""),
            "setpoint_k": _num(getattr(status, "setpoint_k", None)),
            "setpoint_target_k": _num(getattr(status, "setpoint_target_k", None)),
            "ramping": bool(getattr(status, "ramping", False)),
            "error_k": _num(getattr(status, "error_k", None)),
            "output_pct": _num(getattr(status, "output_pct", None)),
            # What the PID asked for before the band clamped it.  This and not
            # `output_pct` is what says the loop has run out of authority: the
            # written value is quantised to a DAC code and the band is
            # re-applied by stepping *down* a code, so a saturated loop writes
            # a number strictly below its own rail.
            "demand_pct": _num(getattr(status, "demand_pct", None)),
            "rail_low_pct": _num(rail_low),
            "rail_high_pct": _num(rail_high),
            # The gains in force *this cycle*, under the same names an
            # instrument loop publishes them, so the loop table's existing P/I
            # columns fill themselves.  A software loop's gains are scheduled
            # -- they move with temperature -- which makes them worth more here
            # than a 33x's fixed pair, and the row was showing blanks.  There
            # is no `d`: this controller takes its derivative from a regressed
            # slope rather than a gain, so a number there would be an invention.
            "p": _num(getattr(status, "kp", None)),
            "i": _num(getattr(status, "ti", None)),
            "threshold_k": _num(getattr(cfg, "max_error_k", None)),
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
        controller=None,
        control_channel: str | None = None,
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
            "links": self._links(instruments or [], frame.aux),
            "recorder": {
                "path": str(getattr(recorder, "path", "") or ""),
                "rows": int(getattr(recorder, "rows_written", 0)),
            },
            "control": self._control(control, controller, control_channel),
            "commands": commands or {},
            # This file's own write history.  A client that finds a gap in the
            # feed can tell a recorder that stalled from one that could not
            # write, which the age alone cannot distinguish -- `failures` will
            # have jumped and `last_error` says why.
            #
            # Necessarily counted from *before* the write that carries it, so
            # `writes` is one behind.  `last_error` and `last_failure_t` are a
            # record rather than a live flag and are never cleared -- by the
            # time anyone reads this file the write plainly succeeded, so a
            # field saying "not failing right now" would be telling them
            # something they can already see.
            "status_file": {
                "writes": int(self.writes),
                "failures": int(self.failures),
                "last_error": self.last_error,
                "last_failure_t": _num(self.last_failure_t) or 0.0,
            },
        }

    def write(self, frame: Frame, **kw) -> bool:
        """One cycle into the file.  Logs the *edges*, not every failure.

        A status write that fails every cycle would otherwise produce one log
        line per second for as long as the condition lasts, which is how a real
        signal gets buried.  So the first failure and the recovery are WARNING
        and everything between them is DEBUG -- and the count goes into the
        next file that succeeds, so a client that saw the gap can find out how
        long it lasted and why.

        This matters most on Windows, where `os.replace` over a file another
        process has open can fail with a sharing violation. That has not been
        reproduced, but "not reproduced" and "would be noticed" are different
        claims, and until this it was only the first.
        """
        payload = self.payload(frame, **kw)
        self.last = payload

        def note(exc: Exception) -> None:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_failure_t = time.time()

        ok = atomic_write_json(self.path, payload, on_error=note)
        if ok:
            self.writes += 1
            if self._failing:
                self._failing = False
                log.warning(
                    "status file %s is writable again after %d failure(s)",
                    self.path, self.failures)
        else:
            self.failures += 1
            if not self._failing:
                self._failing = True
                log.warning(
                    "status file %s could not be written: %s. Clients will see "
                    "this feed stop until it recovers",
                    self.path, self.last_error)
        return ok
