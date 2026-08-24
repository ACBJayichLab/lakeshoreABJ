"""A single-instance lock, so two recorders cannot fight over one instrument.

Why this is not optional
------------------------

A Windows COM port is **exclusive**: exactly one process may hold it.  So a
second recorder started by accident does not merely duplicate work -- it either
fails to open the port, or it takes the port and leaves the *first* one blind.
On GPIB the failure is subtler and worse: two processes can both talk to one
board, interleaving transactions, and the symptom is occasional garbled replies
rather than an honest error.

The same reasoning applies to whatever else may want the instrument: MATLAB
cannot open COM10 while this holds it, which is precisely why MATLAB talks to
this program through files instead of to the instrument through the port.

Why an OS lock and not a PID file
---------------------------------

A lock *file* containing a PID has to be cleaned up, and a process that is
killed or that loses power never gets the chance -- so the next start finds a
stale lock and either refuses (wrongly) or ignores it (pointlessly).  An OS
advisory lock on an open handle is released by the kernel when the process
dies, however it dies.  There is nothing to clean up and no stale state to
reason about, which is the entire point.

``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows.  The file's *contents*
are only ever diagnostic -- who holds it, since when -- and are never trusted
for the decision.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: Every lock currently held by this process.
#:
#: An OS lock lives on an *open file handle*, so it is released the moment that
#: handle is closed -- including when the garbage collector closes it.  That
#: makes `InstanceLock(path).acquire()` without keeping the return value a
#: silent no-op: the lock is taken and then dropped microseconds later, and the
#: next process walks straight in.  Holding a reference here means a held lock
#: survives however the caller chose to store it (or not).
_HELD: set = set()


class AlreadyRunning(RuntimeError):
    """Another process holds the lock.  Carries whatever it said about itself."""

    def __init__(self, path: str, holder: dict | None) -> None:
        self.path = path
        self.holder = holder or {}
        who = ""
        if self.holder:
            started = self.holder.get("started_at")
            when = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started))
                if isinstance(started, (int, float)) else "unknown time"
            )
            who = (
                f" (pid {self.holder.get('pid', '?')} on "
                f"{self.holder.get('host', '?')}, since {when})"
            )
        super().__init__(
            f"another lschart instance already holds {path}{who}. "
            "Two recorders on one instrument will fight over the port -- "
            "stop the other one, or use a different lock_path."
        )


class InstanceLock:
    """Hold an exclusive OS lock for as long as this object is open.

    Use as a context manager::

        with InstanceLock("data/lschart.lock"):
            app.start()
            ...

    The lock is per *path*, so two recorders driving genuinely different
    instruments coexist by pointing at different lock files -- which is also
    the escape hatch when someone really does mean to run two.
    """

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self._fh = None
        self._locked = False

    # -- platform primitives ----------------------------------------------

    @staticmethod
    def _try_lock(fh) -> bool:
        """Non-blocking exclusive lock.  True if acquired."""
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @staticmethod
    def _unlock(fh) -> None:
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:  # pragma: no cover - the kernel frees it on close anyway
            pass

    def _read_holder(self) -> dict | None:
        """Whatever the current holder wrote about itself.  Diagnostic only."""
        try:
            return json.loads(self.path.read_text() or "{}")
        except (OSError, ValueError):
            return None

    # -- lifecycle ---------------------------------------------------------

    def acquire(self) -> "InstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # "a+" so the file is created if absent but never truncated before the
        # lock is decided -- truncating first would wipe the running holder's
        # own diagnostics on a failed attempt.
        self._fh = open(self.path, "a+")
        if not self._try_lock(self._fh):
            holder = self._read_holder()
            self._fh.close()
            self._fh = None
            raise AlreadyRunning(str(self.path), holder)
        self._locked = True
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(json.dumps({
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": time.time(),
            "argv": " ".join(sys.argv),
        }))
        self._fh.flush()
        _HELD.add(self)
        log.info("holding the single-instance lock at %s", self.path)
        return self

    def release(self) -> None:
        _HELD.discard(self)
        if self._fh is not None:
            if self._locked:
                self._unlock(self._fh)
            self._fh.close()
            self._fh = None
        self._locked = False
        # The file is deliberately left behind.  Removing it races with another
        # process that has already opened it and is waiting to lock, which is
        # how a lock file ends up unlinked while somebody holds it.
        log.debug("released the single-instance lock at %s", self.path)

    @property
    def held(self) -> bool:
        return self._locked

    def __enter__(self) -> "InstanceLock":
        return self.acquire()

    def __exit__(self, *exc) -> bool:
        self.release()
        return False
