"""The join: commands in, status out, once per cycle.

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
and :mod:`lschart.instruments.ls218` already expose, behind the gates those
modules already have.  Nothing new is possible through this door -- it is a
second way to reach the same guarded methods, not a way around them.  In
particular:

* ``allow_writes`` on the instrument still applies.  A read-only box refuses a
  file command exactly as it refuses a CLI one, with the same message.
* ``transport.read_only`` still applies, one layer lower again.
* a command that *applies power* needs its own opt-in on top of both:
  ``ipc.allow_heater_range`` for a 33x range, ``ipc.allow_analog_output`` for a
  218 analog output.  Two switches and not one, because they are two different
  commands on two different boxes -- a cryostat that wants its sample heater driven
  from a file has no business also being able to raise the range on a
  controller that is holding something else.
* the safe direction is always available.  Turning a heater **off**, or
  commanding an analog output to zero, needs neither extra opt-in.

On a different axis again, ``ipc.sources`` and its runtime overlay ask *who is
asking* rather than *what is being asked* -- see :mod:`lschart.ipc.sources`.
The panic kinds in :data:`PANIC_KINDS` are the only things exempt from it, and
they are exempt from the per-kind power gates too; they are exempt from nothing
else.

Failure policy: no command, however malformed, may stop the recording.  Every
handler's exceptions are caught and turned into a refusal that the client can
read back in ``status.json``.
"""

from __future__ import annotations

import logging
from collections import deque

from ..model import Frame
from .commands import Command, CommandResult, CommandSpool
from .sources import SourcePolicy
from .status import StatusWriter

log = logging.getLogger(__name__)


#: The kinds that get out from under the source policy and the per-kind power
#: gates.  Deliberately a property of the **command kind** and not of the
#: client: the recorder cannot tell a menu press from a script, it sees the
#: kind, and an automated abort is a large part of why a panic command exists.
#: What they do *not* bypass is `ipc.accept_commands`, `allow_writes` or
#: `transport.read_only` -- a box configured read-only stays read-only, and is
#: named in the reply rather than silently skipped.
PANIC_KINDS = frozenset({"heaters_off"})


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
        allow_analog_output: bool = False,
        allow_pid: bool = False,
        sources: dict | None = None,
        sources_path: str | None = None,
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
        self.allow_analog_output = allow_analog_output
        self.allow_pid = allow_pid
        self.sources = SourcePolicy(sources, overlay_path=sources_path)
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
                "(writable instruments: %s; heater range %s; analog output %s; "
                "PID %s)",
                self.writer.path, self.spool.directory,
                ", ".join(writable) or "NONE -- every command will be refused",
                "ALLOWED" if self.allow_heater_range else "refused",
                "ALLOWED" if self.allow_analog_output else "refused",
                "ALLOWED" if self.allow_pid else "refused",
            )
            if not self.sources.unconfigured:
                log.warning("IPC: source policy: %s (default %s)",
                            ", ".join(f"{k}={'on' if v else 'OFF'}" for k, v
                                      in sorted(self.sources.configured.items()))
                            or "nothing named",
                            "on" if self.sources.default else "OFF")
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
        self.sources.refresh()
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

        if cmd.kind not in PANIC_KINDS and not self.sources.allows(cmd.source):
            refusal = self.sources.refusal(cmd.source)
            log.warning("IPC: refusing %s command %s: %s",
                        cmd.kind, cmd.id, refusal)
            return CommandResult(cmd.id, cmd.kind, False, refusal)

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
        except ValueError as exc:
            # A driver limit said no: a setpoint past `max_setpoint_k`, a
            # percentage past `max_output_pct`, a loop the box does not have.
            # Same category as the above -- the guard worked -- so it must not
            # come out as an ERROR with a traceback.  On a live cryostat those
            # tracebacks are what an operator's typo would look like in the
            # log, and they would bury the real ones.
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

    def _analog_boxes(self) -> dict:
        """Instruments carrying a settable analog output, by name."""
        return {
            i.name: i for i in self.instruments if hasattr(i, "set_analog_percent")
        }

    def _target(self, cmd: Command):
        return self._pick(cmd, self._controllers(), "controller", "controllers")

    def _analog_target(self, cmd: Command):
        """A 218, or whatever else grows an analog output.

        Resolved separately from :meth:`_target` rather than by widening it,
        because on the LTSPM3 cryostat *both* boxes are present and they take
        different commands.  "Several controllers are configured, name one"
        would be a confusing answer to an analog command on a cryostat that has
        exactly one box with an analog output.
        """
        return self._pick(
            cmd, self._analog_boxes(),
            "instrument with an analog output", "instruments with analog outputs",
        )

    def _pick(self, cmd: Command, boxes: dict, singular: str, plural: str):
        if not boxes:
            raise CommandError(
                f"no {singular} is configured on this recorder; it can only log"
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
                f"several {plural} are configured ({sorted(boxes)}); "
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
                "raising a heater range applies power to the cryostat, and this "
                "recorder does not accept that from a file; set "
                "ipc.allow_heater_range: true if a remote client really should "
                "be able to turn a heater on. Turning one OFF (value 0) is "
                "always allowed"
            )
        inst.set_heater_range(output, value)
        return f"{inst.name} heater {output} range -> {value}"

    def _do_analog(self, cmd: Command) -> str:
        """Drive a 218 analog output directly.  Manual control, in one number.

        The 218's equivalent of ``range`` and ``setpoint`` at once: there is no
        inert half, so the percentage is the power and it is gated like a range
        rather than like a setpoint.  Zero is exempt, as it is everywhere else
        here -- the direction that removes heat is never the one that needs
        another permission.
        """
        inst = self._analog_target(cmd)
        percent = _as_float(cmd.args, "percent")
        if percent > 0 and not self.allow_analog_output:
            raise CommandError(
                "driving a 218 analog output above zero applies power to the "
                "cryostat, and this recorder does not accept that from a file; set "
                "ipc.allow_analog_output: true if a remote client really "
                "should be able to move the heater. Commanding it to 0 is "
                "always allowed"
            )
        inst.set_analog_percent(percent)
        return (f"{inst.name} analog output {inst.analog.output} -> "
                f"{percent:.3f}% (verified)" if inst.verify_writes else
                f"{inst.name} analog output {inst.analog.output} -> "
                f"{percent:.3f}% (NOT verified)")

    def _do_pid(self, cmd: Command) -> str:
        """Retune a loop.  Gated on its own, and not because it applies power.

        It does not: a loop with range 0 stays inert however it is tuned, so
        this is not a third power gate.  It is gated because gains are a
        different *kind* of act from a setpoint.  A setpoint moves the cryostat
        somewhere and you can watch it go; gains change how it gets anywhere at
        all, and a badly-tuned loop misbehaves quietly for the rest of the run
        rather than visibly at the moment of the command.  Somebody who wants a
        remote client to be able to move a setpoint has not thereby said they
        want it retuning the loop.

        All three gains are required together.  ``PID`` is one command on the
        instrument and the driver verifies all three by readback; accepting one
        of them would mean reading the other two back and re-sending them,
        which is a read-modify-write against a box somebody else may be
        touching.
        """
        inst = self._target(cmd)
        if not self.allow_pid:
            raise CommandError(
                "retuning a loop is not accepted from a file on this recorder; "
                "set ipc.allow_pid: true if a remote client really should be "
                "able to change P, I and D"
            )
        loop = _as_int(cmd.args, "loop", required=False, default=1)
        p = _as_float(cmd.args, "p")
        i = _as_float(cmd.args, "i")
        d = _as_float(cmd.args, "d")
        inst.set_pid(loop, p, i, d)
        return f"{inst.name} loop {loop} PID -> {p:.1f}, {i:.1f}, {d:.1f} (verified)"

    def _do_heaters_off(self, cmd: Command) -> str:
        """The panic button.  Always available: lowering power is the safe direction.

        Deliberately *not* routed through :meth:`_target`.  Every other handler
        acts on one box because it needs an argument that only means something
        on one box; this one takes no arguments and means "stop heating", which
        on a two-box cryostat had better include the box carrying the sample heater.
        A panic button that leaves one heater running is worse than no panic
        button, because it will be believed.

        Instruments this recorder may not write to are skipped rather than
        failed on: on a shared cryostat a read-only box is somebody else's, and
        refusing the whole command because of it would leave *our* heaters on.
        """
        done: list[str] = []
        skipped: list[str] = []
        for inst in self.instruments:
            if not getattr(inst, "allow_writes", False):
                if hasattr(inst, "all_heaters_off") or hasattr(inst, "analog_off"):
                    skipped.append(inst.name)
                continue
            if hasattr(inst, "all_heaters_off"):
                inst.all_heaters_off()
                done.append(f"{inst.name}: all heater ranges 0")
            elif hasattr(inst, "analog_off"):
                inst.analog_off()
                done.append(f"{inst.name}: analog output 0%")
        if not done:
            raise CommandError(
                "nothing to turn off: no instrument on this recorder is "
                "writable"
                + (f" (read-only here: {', '.join(skipped)})" if skipped else "")
            )
        message = "; ".join(done)
        if skipped:
            message += f"; left alone (read-only): {', '.join(skipped)}"
        return message

    # -- status ------------------------------------------------------------

    def _commands_block(self) -> dict:
        return {
            "accepted": bool(self.accept_commands),
            "directory": str(self.spool.directory),
            "ttl_s": self.spool.ttl_s,
            "allow_heater_range": bool(self.allow_heater_range),
            "allow_analog_output": bool(self.allow_analog_output),
            "allow_pid": bool(self.allow_pid),
            # An array of uniform objects, never an object keyed by source
            # name: `lschart-cli` would reach MATLAB as `lschart_cli`.
            "sources": self.sources.as_status(),
            "source_policy": not self.sources.unconfigured,
            # What an unlisted source gets.  Published because a source the
            # policy never names appears nowhere in the array above, and a
            # client cannot otherwise tell "not mentioned, therefore fine" from
            # "not mentioned, therefore refused".
            "source_default": self.sources.unconfigured or self.sources.default,
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
