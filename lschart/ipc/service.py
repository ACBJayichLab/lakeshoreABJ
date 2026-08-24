"""The join: commands in, status out, once per poll cycle.

This runs on the **acquisition thread**, from the poller's ``on_frame`` hook,
and that is not an implementation detail.  The acquisition thread owns the bus;
applying a setpoint from any other thread would mean two threads writing to one
instrument, and while the transport lock would serialise them, it would do so
in the middle of somebody's read cycle.  Doing it here means a command lands
cleanly between two cycles, and the very next status file reports the result.

Order within a cycle is: read (done by the poller) -> record -> apply commands
-> write status.  Applying before writing is what makes an acknowledgement
appear in the same file as the reading that follows it.

What a command may do
---------------------

Everything a command can do is something :mod:`lschart.instruments.ls33x`
already exposes, behind the gates that module already has.  Nothing new is
possible through this door -- it is a second way to reach the same guarded
methods, not a way around them.  In particular:

* ``allow_writes`` on the instrument still applies.  A read-only box refuses a
  file command exactly as it refuses a CLI one, with the same message.
* ``transport.read_only`` still applies, one layer lower again.
* raising a heater range is the command that actually applies power, so it
  needs its own opt-in (``ipc.allow_heater_range``) on top of both.  Turning a
  heater **off** never does: the safe direction is always available.

Failure policy: no command, however malformed, may stop the recording.  Every
handler's exceptions are caught and turned into a refusal that the client can
read back in ``status.json``.
"""

from __future__ import annotations

import logging
from collections import deque

from ..model import Frame
from .commands import Command, CommandResult, CommandSpool
from .status import StatusWriter

log = logging.getLogger(__name__)


class CommandError(ValueError):
    """A command that cannot be carried out, with the reason a human needs."""


def _as_float(args: dict, key: str, *, required: bool = True,
              default: float | None = None) -> float | None:
    if key not in args or args[key] is None:
        if required:
            raise CommandError(f"missing required argument {key!r}")
        return default
    try:
        return float(args[key])
    except (TypeError, ValueError):
        raise CommandError(
            f"argument {key!r} must be a number, got {args[key]!r}"
        ) from None


def _as_int(args: dict, key: str, *, required: bool = True,
            default: int | None = None) -> int | None:
    value = _as_float(args, key, required=required,
                      default=None if default is None else float(default))
    if value is None:
        return default
    if value != int(value):
        raise CommandError(f"argument {key!r} must be a whole number, got {value!r}")
    return int(value)


class IpcService:
    """Reads the command spool and writes the status file, once per frame."""

    def __init__(
        self,
        *,
        status_path,
        spool: CommandSpool,
        instruments: list | None = None,
        recorder=None,
        accept_commands: bool = False,
        allow_heater_range: bool = False,
        max_commands_per_cycle: int = 4,
        config_path: str | None = None,
        ack_history: int = 20,
        interval_s: float = 0.0,
    ) -> None:
        self.writer = StatusWriter(status_path, config_path=config_path)
        self.spool = spool
        self.instruments = list(instruments or [])
        self.recorder = recorder
        self.accept_commands = accept_commands
        self.allow_heater_range = allow_heater_range
        self.max_commands_per_cycle = max(1, int(max_commands_per_cycle))
        self.interval_s = interval_s
        #: Set by the application once the poller exists; read duck-typed so
        #: this module never needs to know what a supervisor is.
        self.poller = None

        self.applied = 0
        self.refused = 0
        self.last_applied_id = ""
        self._acks: deque[CommandResult] = deque(maxlen=max(1, ack_history))
        self._swept = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Prepare the directories, and say plainly what is switched on.

        A spool nobody is reading looks identical, from MATLAB's side, to one
        whose commands are all being refused -- so which it is gets logged at
        startup rather than left to be discovered.
        """
        self.writer.path.parent.mkdir(parents=True, exist_ok=True)
        if self.accept_commands:
            self.spool.ensure()
            self.spool.sweep_temporaries()
            writable = [i.name for i in self.instruments
                        if getattr(i, "allow_writes", False)]
            log.warning(
                "IPC: status -> %s; accepting commands from %s "
                "(writable instruments: %s; heater range %s)",
                self.writer.path, self.spool.directory,
                ", ".join(writable) or "NONE -- every command will be refused",
                "ALLOWED" if self.allow_heater_range else "refused",
            )
        else:
            log.info("IPC: status -> %s; commands are NOT accepted "
                     "(set ipc.accept_commands: true to enable)", self.writer.path)

    def stop(self) -> None:
        """Leave a final status file saying the recorder exited deliberately.

        Without it, a stopped recorder and a hung one look identical to a
        client: both leave a status file that simply stops getting newer.
        """
        frame = getattr(self.poller, "last_frame", None)
        if frame is None:
            return
        self._write_status(frame, running=False)
        log.info("IPC: final status written to %s", self.writer.path)

    # -- the per-cycle hook ------------------------------------------------

    def on_frame(self, frame: Frame) -> None:
        """Called by the poller after every cycle.  Must never raise."""
        results = self._drain()
        for r in results:
            self._acks.append(r)
            if r.ok:
                self.applied += 1
                self.last_applied_id = r.id
            else:
                self.refused += 1
        self._write_status(frame)

    def _drain(self) -> list[CommandResult]:
        try:
            commands = self.spool.collect(self.max_commands_per_cycle)
        except OSError as exc:  # pragma: no cover - a directory that vanished
            log.warning("IPC: could not read the command spool: %s", exc)
            return []
        return [self._dispatch(c) for c in commands]

    def _dispatch(self, cmd: Command) -> CommandResult:
        if cmd.error:
            log.warning("IPC: rejecting command %s: %s", cmd.id, cmd.error)
            return CommandResult(cmd.id, cmd.kind, False, cmd.error)
        if not self.accept_commands:
            return CommandResult(
                cmd.id, cmd.kind, False,
                "this recorder is not accepting commands; set "
                "ipc.accept_commands: true in its config file to enable them",
            )
        stale = cmd.staleness(self.spool.ttl_s)
        if stale:
            log.warning("IPC: refusing %s command %s: %s", cmd.kind, cmd.id, stale)
            return CommandResult(cmd.id, cmd.kind, False, stale)

        handler = getattr(self, f"_do_{cmd.kind}", None)
        if handler is None:
            kinds = sorted(
                n[4:] for n in dir(self) if n.startswith("_do_")
            )
            return CommandResult(
                cmd.id, cmd.kind, False,
                f"unknown command {cmd.kind!r}; known commands are {kinds}",
            )
        try:
            message = handler(cmd)
        except CommandError as exc:
            return CommandResult(cmd.id, cmd.kind, False, str(exc))
        except PermissionError as exc:
            # An interlock said no.  That is a correct outcome, not a fault.
            log.warning("IPC: %s refused: %s", cmd.kind, exc)
            return CommandResult(cmd.id, cmd.kind, False, f"refused: {exc}")
        except Exception as exc:  # noqa: BLE001 - a bad command must not stop logging
            log.exception("IPC: command %s (%s) failed", cmd.id, cmd.kind)
            return CommandResult(
                cmd.id, cmd.kind, False, f"{type(exc).__name__}: {exc}"
            )
        log.warning("IPC: applied %s from %s: %s",
                    cmd.kind, cmd.source or "?", message)
        return CommandResult(cmd.id, cmd.kind, True, message)

    # -- resolving the target ---------------------------------------------

    def _controllers(self) -> dict:
        """Instruments that have a loop to command, by name."""
        return {
            i.name: i for i in self.instruments
            if hasattr(i, "set_setpoint") and hasattr(i, "caps")
        }

    def _target(self, cmd: Command):
        boxes = self._controllers()
        if not boxes:
            raise CommandError(
                "no controller is configured on this recorder; it can only log"
            )
        if cmd.instrument:
            if cmd.instrument not in boxes:
                raise CommandError(
                    f"no instrument named {cmd.instrument!r}; "
                    f"configured: {sorted(boxes)}"
                )
            return boxes[cmd.instrument]
        if len(boxes) > 1:
            raise CommandError(
                f"several controllers are configured ({sorted(boxes)}); "
                "name one in the command's `instrument` field"
            )
        return next(iter(boxes.values()))

    # -- handlers ----------------------------------------------------------
    #
    # Each returns the sentence that goes back to the client, and each leans on
    # the driver's own guards rather than re-checking limits here.  A limit
    # that exists in two places drifts.

    def _do_ping(self, cmd: Command) -> str:
        """Prove the command path end to end without touching an instrument.

        ``status.json`` going stale tells a client the recorder died; it says
        nothing about whether commands are being read.  This does.
        """
        return "pong"

    def _do_setpoint(self, cmd: Command) -> str:
        inst = self._target(cmd)
        loop = _as_int(cmd.args, "loop", required=False, default=1)
        kelvin = _as_float(cmd.args, "kelvin")
        inst.set_setpoint(loop, kelvin)
        return f"{inst.name} loop {loop} setpoint -> {kelvin:.4f} K"

    def _do_ramp(self, cmd: Command) -> str:
        inst = self._target(cmd)
        loop = _as_int(cmd.args, "loop", required=False, default=1)
        rate = _as_float(cmd.args, "rate_k_per_min")
        enable = cmd.args.get("enable")
        enable = (rate > 0) if enable is None else bool(enable)
        inst.set_ramp(loop, rate, enable=enable)
        if not enable:
            return f"{inst.name} loop {loop} ramping OFF"
        return f"{inst.name} loop {loop} ramp -> {rate:.3f} K/min"

    def _do_range(self, cmd: Command) -> str:
        """The command that applies power.  Gated once more than the others."""
        inst = self._target(cmd)
        output = _as_int(cmd.args, "output", required=False, default=1)
        value = _as_int(cmd.args, "value")
        if value > 0 and not self.allow_heater_range:
            raise CommandError(
                "raising a heater range applies power to the rig, and this "
                "recorder does not accept that from a file; set "
                "ipc.allow_heater_range: true if a remote client really should "
                "be able to turn a heater on. Turning one OFF (value 0) is "
                "always allowed"
            )
        inst.set_heater_range(output, value)
        return f"{inst.name} heater {output} range -> {value}"

    def _do_heaters_off(self, cmd: Command) -> str:
        """Always available: lowering power is the safe direction."""
        inst = self._target(cmd)
        inst.all_heaters_off()
        return f"{inst.name}: all heater ranges 0"

    # -- status ------------------------------------------------------------

    def _commands_block(self) -> dict:
        return {
            "accepted": bool(self.accept_commands),
            "directory": str(self.spool.directory),
            "ttl_s": self.spool.ttl_s,
            "allow_heater_range": bool(self.allow_heater_range),
            "queued": len(self.spool.pending()) if self.accept_commands else 0,
            "applied": self.applied,
            "refused": self.refused,
            "last_applied_id": self.last_applied_id,
            # Oldest first, so a client that keeps the last id it saw can walk
            # forward from it.
            "recent": [r.as_dict() for r in self._acks],
        }

    def _write_status(self, frame: Frame, *, running: bool = True) -> None:
        poller = self.poller
        self.writer.write(
            frame,
            cycles=int(getattr(poller, "cycles", 0)),
            dropped_cycles=int(getattr(poller, "dropped_cycles", 0)),
            interval_s=self.interval_s or float(getattr(poller, "interval_s", 0.0)),
            instruments=self.instruments,
            recorder=self.recorder,
            control=getattr(poller, "last_control_status", None),
            commands=self._commands_block(),
            running=running,
        )

    # -- convenience for clients in this process ---------------------------

    def result_for(self, command_id: str) -> CommandResult | None:
        """The outcome of one command, if it is still in the ack window."""
        for r in reversed(self._acks):
            if r.id == command_id:
                return r
        return None
