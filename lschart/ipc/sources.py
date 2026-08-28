"""Who may ask, as opposed to what may be done.

The five interlocks in ``CLAUDE.md`` all answer the same question from
different heights: *may this action happen at all*.  ``transport.read_only``
answers it in bytes, ``allow_writes`` in driver policy, ``ipc.accept_commands``
at the door, and the two power gates per command kind.  None of them can
express "the operator at this terminal may drive the cryostat, the analysis
script may not" -- because none of them knows there is more than one client.

This module is the sixth gate, on a new axis: *may this client ask*.

Two layers, and the second may only ever narrow the first
---------------------------------------------------------

``ipc.sources`` in the config file is the **ceiling**, fixed for the life of
the process.  ``sources.json`` in the IPC directory is a **runtime overlay**,
re-read every cycle, and it can only take permission away.  An operator who
wants to say "programmatic control only, for the next twenty minutes" edits one
small file; an operator who wants to *grant* something that the config refuses
has to edit the config and restart, which is the point.  A restart therefore
always returns to the audited ceiling, and no amount of fiddling with the
overlay can leave the recorder more open than its config says.

It is a file and not a command kind, deliberately.  A command that disabled the
viewer would leave the viewer with no way to re-enable itself -- the one client
that needs to undo it is the one it just silenced.  A file can be edited by
anything, including by hand, and never requires stopping the recorder and
making it drop the port.

What it is not
--------------

**This is an interlock against habit and mistake, not against malice.**
``source`` is self-declared in the command file; anything that can write to the
spool can write any label it likes.  That is the accepted trade: the spool is
already a directory on a machine you trust, and the alternative -- keys,
signatures, a handshake -- would buy nothing that the filesystem's own
permissions do not already buy, at the cost of the protocol being readable by
a MATLAB script in forty lines.

Matching is on the part before the first ``/``.  The CLI stamps its pid into
its label (``lschart-cli/12345``) so that the log says which terminal a command
came from, and no fixed key in a config file could ever match that.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

#: The overlay's filename inside the IPC directory.
SOURCES_FILE = "sources.json"

#: Stands in for a command that carried no ``source`` at all.  Displayed rather
#: than silently mapped onto ``default``, so a client that forgot to label
#: itself finds out from the refusal.
UNLABELLED = "(unlabelled)"


def source_key(source: str) -> str:
    """The part of a source label a policy is keyed on.

    ``lschart-cli/12345`` -> ``lschart-cli``.  See the module docstring.
    """
    return (source or "").split("/", 1)[0].strip()


class SourcePolicy:
    """The config ceiling, the runtime overlay, and the answer they agree on."""

    def __init__(
        self,
        configured: dict | None = None,
        *,
        overlay_path: str | os.PathLike | None = None,
    ) -> None:
        raw = dict(configured or {})
        #: ``True`` when the config carried no policy at all.  Then every
        #: source is permitted and the overlay is the only thing that can
        #: narrow -- which keeps every existing config working unchanged.
        self.unconfigured = not raw
        self.default = bool(raw.pop("default", False)) if raw else True
        self.configured = {source_key(k): bool(v) for k, v in raw.items()}
        self.overlay_path = Path(overlay_path) if overlay_path else None
        #: The overlay as last read.  Kept across a failed read rather than
        #: cleared: a hand-edited file caught mid-save is a torn read, and
        #: quietly widening the policy because of one is the wrong direction
        #: to fail in.
        self.overlay: dict[str, bool] = {}
        self._overlay_error = ""
        self._last_signature: tuple | None = None

    # -- the overlay -------------------------------------------------------

    def refresh(self) -> None:
        """Re-read ``sources.json``.  Never raises; must not stop a cycle."""
        if self.overlay_path is None:
            return
        try:
            stat = self.overlay_path.stat()
        except OSError:
            # Absent is a deliberate state and not an error: no overlay, so no
            # narrowing.  Deleting the file is how an operator clears a lockout.
            if self.overlay:
                log.warning("IPC: %s is gone; the source policy is back to "
                            "the config ceiling", self.overlay_path)
            self.overlay = {}
            self._overlay_error = ""
            self._last_signature = None
            return

        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._last_signature:
            return

        try:
            raw = json.loads(self.overlay_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A torn read, or an operator halfway through an edit.  Hold the
            # last good overlay and say so once per distinct message.
            message = f"{type(exc).__name__}: {exc}"
            if message != self._overlay_error:
                self._overlay_error = message
                log.warning("IPC: could not read %s (%s); keeping the previous "
                            "source overlay %s", self.overlay_path, message,
                            self.overlay or "{}")
            return

        if not isinstance(raw, dict):
            self._note_bad(f"expected a JSON object, got {type(raw).__name__}")
            return
        # `{"sources": {...}}` and a bare `{...}` both read, because both are
        # what somebody writes from memory at a cryostat at two in the morning.
        body = raw.get("sources", raw)
        if not isinstance(body, dict):
            self._note_bad("the `sources` key must hold an object")
            return

        overlay = {}
        for key, value in body.items():
            if isinstance(value, bool):
                overlay[source_key(str(key))] = value
            else:
                self._note_bad(
                    f"{key!r} is {value!r}, not true or false; ignoring that entry"
                )
        self._last_signature = signature
        self._overlay_error = ""
        if overlay != self.overlay:
            log.warning("IPC: source overlay from %s: %s", self.overlay_path,
                        ", ".join(f"{k}={'on' if v else 'OFF'}"
                                  for k, v in sorted(overlay.items())) or "empty")
        self.overlay = overlay

    def _note_bad(self, message: str) -> None:
        if message != self._overlay_error:
            self._overlay_error = message
            log.warning("IPC: %s: %s", self.overlay_path, message)

    # -- the answer --------------------------------------------------------

    def ceiling(self, source: str) -> bool:
        """What the config alone permits for this source."""
        if self.unconfigured:
            return True
        return self.configured.get(source_key(source), self.default)

    def allows(self, source: str) -> bool:
        key = source_key(source)
        if not self.ceiling(source):
            return False
        # The overlay narrows and never widens, so only a False in it counts.
        return self.overlay.get(key, True)

    def refusal(self, source: str) -> str:
        """Why a source was refused, in the terms of whichever layer said no."""
        key = source_key(source) or UNLABELLED
        if not self.ceiling(source):
            listed = sorted(k for k, v in self.configured.items() if v)
            return (
                f"commands from {key!r} are not accepted by this recorder's "
                f"configuration (ipc.sources); "
                + (f"accepted sources: {listed}" if listed else
                   "no source is accepted")
                + ". Changing this needs a config edit and a restart"
            )
        return (
            f"commands from {key!r} are currently switched off in "
            f"{self.overlay_path}; delete that entry (or the file) to allow "
            "them again -- no restart needed"
        )

    # -- what the status file publishes ------------------------------------

    def as_status(self) -> list[dict]:
        """Every source either layer names, as an array of uniform objects.

        An array rather than an object keyed by source name, for the reason
        given in :mod:`lschart.ipc.status`: MATLAB's ``jsondecode`` runs object
        keys through ``makeValidName``, and ``lschart-cli`` would arrive as
        ``lschart_cli``.
        """
        names = sorted(set(self.configured) | set(self.overlay))
        return [
            {
                "name": name,
                "allowed": self.allows(name),
                "configured": self.ceiling(name),
                "disabled_at_runtime": self.overlay.get(name, True) is False,
            }
            for name in names
        ]
