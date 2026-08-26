"""A drop-box for commands from MATLAB, the viewer, or anything else.

The shape is a maildir: a client writes ``<name>.json.tmp``, then renames it to
``<name>.json``.  Rename within a directory is atomic on POSIX and on Windows,
so the recorder never reads a half-written command, and a client that dies
mid-write leaves a ``.tmp`` that nobody will ever pick up.  There is no lock
file, no contention, and no protocol -- which is the point.  The recorder does
not know who wrote a command and cannot be hurt by that client going away.

Four properties this needs that a naive drop-box does not have
--------------------------------------------------------------

**Commands expire.**  Each carries ``issued_at``, and one older than
``ttl_s`` is refused.  Without this, a recorder that was down for an hour comes
back up, finds an hour of queued setpoints, and replays them into a live
cryostat as fast as the bus allows.  The last one would even be "right", which
is what makes it dangerous: the damage is the traversal, not the destination.

**Commands are ordered.**  The filename is prefixed with the issuing
millisecond *and* a per-client sequence number, so a lexicographic sort of the
directory is chronological.  The sequence number is not decoration: Windows
resolves ``time.time()`` to about 15 ms, so a script that queues a setpoint and
a heater range back to back stamps both with the same millisecond, and without
a tie-break they would be applied in whichever order their random ids happened
to sort -- which for those two commands is the difference between heating to
the new setpoint and heating to the old one.

**Commands are acknowledged.**  Each carries an ``id`` that reappears in
``status.json`` with the outcome.  Deleting the file cannot be the
acknowledgement: the file is deleted whether the command succeeded or was
refused, so its absence tells a client nothing about what happened.

**A clock that disagrees is caught, not obeyed.**  A command stamped in the
future by more than the TTL is refused rather than treated as fresh forever.

Nothing here talks to an instrument.  Turning a command into a bus transaction
is :mod:`lschart.ipc.service`'s job, and it happens on the acquisition thread,
because that thread owns the bus.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

#: Keys that describe the envelope rather than the command's arguments.
_ENVELOPE = ("id", "kind", "issued_at", "instrument", "source")

#: Default lifetime.  Long enough to survive a slow cycle and a busy bus,
#: short enough that a command is always about the cryostat as it is now.
DEFAULT_TTL_S = 30.0


@dataclass(frozen=True, slots=True)
class Command:
    """One request, already lifted off disk.  The file is gone by now."""

    id: str
    kind: str
    issued_at: float
    instrument: str = ""
    source: str = ""
    args: dict = field(default_factory=dict)
    #: Set when the file could not be understood.  Such a command is never
    #: executed, but it is still reported, because a client whose JSON is
    #: malformed needs to be told that rather than watching commands vanish.
    error: str = ""

    def age_s(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) - self.issued_at

    def staleness(self, ttl_s: float, now: float | None = None) -> str:
        """``""`` if the command may run, else why it may not."""
        age = self.age_s(now)
        if age > ttl_s:
            return (
                f"issued {age:.1f} s ago, older than the {ttl_s:.0f} s limit; "
                "refused rather than applied to a cryostat that has moved on since"
            )
        if age < -ttl_s:
            return (
                f"issued {-age:.1f} s in the future -- the clocks of the client "
                "and the recorder disagree; refused"
            )
        return ""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What became of one command.  Echoed in ``status.json``."""

    id: str
    kind: str
    ok: bool
    message: str
    t_wall: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "ok": bool(self.ok),
            "message": self.message,
            "t_wall": self.t_wall,
        }


class CommandSpool:
    """The directory itself, with the two halves of the protocol.

    :meth:`submit` is the client half -- used by the viewer, by the tests, and
    mirrored line for line by ``matlab/LakeShore.m``.  :meth:`collect` is the
    recorder half.
    """

    def __init__(self, directory: str | os.PathLike, *, ttl_s: float = DEFAULT_TTL_S) -> None:
        self.directory = Path(directory)
        self.ttl_s = ttl_s
        #: Per-client tie-break within one millisecond -- see the module
        #: docstring.  Wraps at 10000, which is four orders of magnitude more
        #: commands per millisecond than anything here can issue.
        self._seq = 0
        #: Clamps the filename prefix monotonic even if the wall clock steps
        #: backwards under us.  `issued_at` inside the file is left alone: the
        #: expiry check wants the real clock, however wrong it is.
        self._last_ms = 0

    def ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    # -- the client half ---------------------------------------------------

    def submit(self, kind: str, *, instrument: str = "", source: str = "",
               command_id: str | None = None, **args) -> str:
        """Queue one command.  Returns the id to watch for in ``status.json``.

        Writes to ``.json.tmp`` and renames, so the recorder can only ever see
        a complete file -- and so the glob the recorder uses (``*.json``)
        cannot match a partial one.
        """
        self.ensure()
        cid = command_id or uuid.uuid4().hex[:12]
        issued = time.time()
        payload = {
            "id": cid,
            "kind": kind,
            "issued_at": issued,
            "instrument": instrument,
            "source": source,
            **args,
        }
        # Millisecond prefix plus a sequence: a lexicographic sort of the
        # directory is then chronological, within a client exactly and across
        # clients as well as their wall clocks agree.
        ms = max(int(issued * 1000), self._last_ms)
        self._last_ms = ms
        self._seq = (self._seq + 1) % 10000
        stem = f"{ms:013d}-{self._seq:04d}-{cid}"
        final = self.directory / f"{stem}.json"
        tmp = self.directory / f"{stem}.json.tmp"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, final)
        log.debug("queued command %s (%s) at %s", cid, kind, final)
        return cid

    # -- the recorder half -------------------------------------------------

    def pending(self) -> list[Path]:
        try:
            return sorted(self.directory.glob("*.json"))
        except OSError:
            return []

    def collect(self, max_n: int | None = None) -> list[Command]:
        """Take up to ``max_n`` commands off the spool, oldest first.

        Each file is deleted as it is read, *before* it is acted on.  That
        ordering is deliberate: a command that somehow crashes the executor
        must not be found again on the next cycle and crash it again.  One
        lost command is a client's problem to notice, via the acknowledgement
        it never sees; an infinite loop of a poisonous one is everybody's.
        """
        out: list[Command] = []
        for path in self.pending():
            if max_n is not None and len(out) >= max_n:
                break
            out.append(self._take(path))
        return out

    def _take(self, path: Path) -> Command:
        raw: dict = {}
        error = ""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                error = f"expected a JSON object, got {type(raw).__name__}"
                raw = {}
        except (OSError, ValueError) as exc:
            error = f"unreadable command file: {exc}"
        finally:
            try:
                path.unlink()
            except OSError:  # pragma: no cover - another reader got there first
                pass

        kind = str(raw.get("kind", "")).strip().lower()
        if not error and not kind:
            error = "no `kind` field: nothing says what this command is"
        issued = raw.get("issued_at")
        if not isinstance(issued, (int, float)):
            # Undated commands are treated as maximally stale rather than as
            # fresh.  Guessing "now" would defeat the expiry rule entirely.
            if not error:
                error = "no numeric `issued_at`: cannot tell whether this is stale"
            issued = 0.0

        # An id lets a client be told what happened.  Falling back to the
        # filename keeps that possible even for a malformed file.
        cid = str(raw.get("id") or path.stem.rsplit("-", 1)[-1])
        args = {k: v for k, v in raw.items() if k not in _ENVELOPE}
        return Command(
            id=cid,
            kind=kind,
            issued_at=float(issued),
            instrument=str(raw.get("instrument", "") or ""),
            source=str(raw.get("source", "") or ""),
            args=args,
            error=error,
        )

    def sweep_temporaries(self, max_age_s: float = 300.0) -> int:
        """Remove ``.tmp`` files a dead client left behind.  Returns the count.

        Nothing reads these, so they are harmless -- but a directory that
        slowly fills with the debris of every crash is its own small bug.
        """
        removed = 0
        cutoff = time.time() - max_age_s
        try:
            candidates = list(self.directory.glob("*.json.tmp"))
        except OSError:
            return 0
        for path in candidates:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
        return removed
