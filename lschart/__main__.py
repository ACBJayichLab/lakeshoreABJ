"""``lschart`` entry point.

Records by default and controls only if the config says so.  Arming the heater
is never implicit: a chart recorder that silently starts driving a heater
because someone ran it with the wrong config file is precisely the failure this
project is trying not to have.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

from . import config as config_mod
from .app import Application
from .instruments import InstrumentError
from .ipc import AlreadyRunning, InstanceLock

log = logging.getLogger("lschart")

#: How to build the Application.  `ltspm3.__main__` swaps this for its own
#: builder, which adds the heater loop and the calibrated thermal response.  Everything
#: else in this module is shared, so the two CLIs cannot drift apart.
BUILDER = Application


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(args) -> int:
    cfg = config_mod.load(args.config)
    if args.interval:
        cfg.acquisition.interval_s = args.interval
        cfg.validate()
    _setup_logging(args.log_level or cfg.log_level)

    # Taken before anything is opened: the point is to lose the race cleanly,
    # rather than to discover halfway through startup that the port is held.
    lock = None
    if cfg.runtime.single_instance:
        try:
            lock = InstanceLock(cfg.runtime.lock_path).acquire()
        except AlreadyRunning as exc:
            log.error("%s", exc)
            return 2

    app = BUILDER(cfg)
    # Whether a controller exists is the builder's answer, not the config's:
    # a recorder-only install has no `control:` section to consult.
    controlled = app.supervisor is not None
    mode = "hardware" if cfg.uses_hardware else "SIMULATION"
    log.warning(
        "starting in %s; control %s; %.3f s cadence",
        mode,
        "ENABLED" if controlled else "disabled",
        cfg.acquisition.interval_s,
    )
    if controlled and not args.arm:
        log.warning("control is configured but NOT armed; pass --arm to close the loop")
    if args.arm and not controlled:
        log.error("--arm was given but no controller is configured; recording only")

    stopping = False

    def _handle(signum, _frame):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        log.warning("signal %s received, shutting down", signum)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    app.start()
    try:
        if controlled and args.arm:
            _arm_when_ready(app, args.setpoint, cfg.acquisition.interval_s)
        while not stopping:
            time.sleep(0.25)
            if args.duration and app.poller.cycles * cfg.acquisition.interval_s >= args.duration:
                break
    finally:
        app.stop()
        if app.recorder is not None:
            log.warning("wrote %d rows to %s", app.recorder.rows_written, app.recorder.path)
        if lock is not None:
            lock.release()
    return 0


def _arm_when_ready(app, setpoint, interval_s, timeout_s: float = 30.0) -> None:
    """Arm once there is a real measurement to be bumpless against."""
    deadline = time.monotonic() + max(timeout_s, interval_s * 10)
    while time.monotonic() < deadline:
        if app.current_temperature() is not None:
            app.arm(setpoint)
            log.warning("heater loop ARMED, target %.4f K", app.supervisor.ramp.target)
            return
        time.sleep(min(0.25, interval_s))
    log.error("no usable reading within %.0f s; NOT arming the loop", timeout_s)


def cmd_check(args) -> int:
    """Validate a config and print the transaction budget without touching hardware."""
    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"config: {cfg.source_path or '<defaults>'}")
    control = cfg.section("control")
    enabled = bool(getattr(control, "enabled", False))
    drivers = sorted({i.driver for i in cfg.enabled_instruments})
    print(f"  driver(s)      : {', '.join(drivers) or 'none'}"
          f"{'  (HARDWARE)' if cfg.uses_hardware else ''}")
    print(f"  control        : {'enabled' if enabled else 'disabled'}")
    # A recorder-only cryostat -- the coworker's 335, say -- declares no
    # control_input, and asking for the control channel then raises.  Reporting
    # that as a traceback from `check`, of all commands, is no way to greet
    # somebody validating their first config file.
    if cfg.control_instrument is not None:
        print(f"  control channel: {cfg.control_channel}")
    else:
        print("  control channel: none (no instrument declares a control_input; "
              "no SOFTWARE loop runs here)")
    print(f"  cadence        : {cfg.acquisition.interval_s} s")
    print(f"  budget         : {cfg.estimated_transactions()} transactions "
          f"~ {cfg.estimated_cycle_s():.2f} s per cycle")
    ipc = cfg.ipc
    if ipc.enabled:
        print(f"  status file    : {ipc.status_path()}")
        allowed = []
        if ipc.accept_commands and ipc.allow_heater_range:
            allowed.append("heater range")
        if ipc.accept_commands and ipc.allow_analog_output:
            allowed.append("analog output")
        print(f"  commands       : "
              f"{'accepted from ' + ipc.command_path() if ipc.accept_commands else 'not accepted'}"
              f"{'  (' + ' and '.join(allowed) + ' ALLOWED from a file)' if allowed else ''}")
        if ipc.sources:
            named = ", ".join(f"{k}={'on' if v else 'OFF'}"
                              for k, v in sorted(ipc.sources.items())
                              if k != "default")
            print(f"  source policy  : {named or 'nothing named'}; "
                  f"anything else: "
                  f"{'on' if ipc.sources.get('default', False) else 'OFF'}")
            print(f"  runtime overlay: {ipc.sources_path()} "
                  "(may narrow this, never widen it)")
    else:
        print("  status file    : disabled (ipc.enabled: false) -- the viewer and "
              "MATLAB have nothing to read")

    # Two gates, reported together, because "writable" is a lie about a box
    # whose transport still refuses the bytes -- and that combination is what a
    # half-opened config looks like.
    writable = []
    for i in cfg.enabled_instruments:
        if not getattr(i, "allow_writes", False):
            continue
        note = " (but transport.read_only: no bytes leave)" if i.transport.read_only else ""
        writable.append(f"{i.resolved_name()}{note}")
    print(f"  writable       : {', '.join(writable) if writable else 'nothing (read-only)'}")
    for i in cfg.enabled_instruments:
        if i.model == "218" and getattr(i, "allow_writes", False):
            print(f"  {i.resolved_name() + ' analog':<15}: output {i.analog_output}, "
                  f"ceiling {i.max_output_pct:g}%, "
                  f"{'verified by readback' if i.verify_writes else 'UNVERIFIED writes'}"
                  f"  <-- THIS IS A HEATER")
    if enabled:
        s = control.supervisor
        lo = max(s.hard_min_pct, s.operating_point_pct - s.authority_pct)
        hi = min(s.hard_max_pct, s.operating_point_pct + s.authority_pct)
        print(f"  authority band : {lo:.3f}% .. {hi:.3f}%  (on_exit={s.on_exit})")
    print("OK")
    return 0


def cmd_probe(args) -> int:
    """Open each instrument, read everything, write nothing.

    The first thing to run against hardware that has never been talked to.
    Every transport is forced READ-ONLY regardless of what the config says, so
    this cannot alter instrument state even if the config enables writes and
    even if there is a bug above the transport layer.  The commands issued are
    queries only: *IDN?, INNAME?, KRDG?, SETP?, HTR?, RANGE?, PID?, RAMP?.
    """
    cfg = config_mod.load(args.config)
    _setup_logging(args.log_level or cfg.log_level)

    # Force the interlock on before anything is constructed.
    for inst_cfg in cfg.instruments:
        inst_cfg.transport.read_only = True

    app = BUILDER(cfg)
    print("PROBE -- read-only: no command that can change instrument state "
          "will be sent.\n")
    failures = 0
    try:
        for inst in app.instruments:
            print(f"{inst.name}:")
            try:
                inst.transport.open()
            except OSError as exc:
                print(f"  LINK DOWN: {exc}\n")
                failures += 1
                continue
            try:
                print(f"  *IDN?          : {inst.idn()}")
                verify = getattr(inst, "verify_model", None)
                if verify is not None:
                    verify()
                    print("  model check    : OK")
                readings, aux = inst.read_frame()
                print(f"  channels       : {len(readings)}")
                for name, r in readings.items():
                    flag = "" if r.validity.name == "GOOD" else f"  <-- {r.validity.name}"
                    print(f"    {name:<24} {r.kelvin:10.4f} K{flag}")
                if aux:
                    print("  auxiliary:")
                    for k in sorted(aux):
                        print(f"    {k:<24} {aux[k]:10.4f}")
            except (OSError, ValueError) as exc:
                print(f"  READ FAILED: {exc}")
                failures += 1
            print()
    finally:
        for inst in app.instruments:
            inst.transport.close()

    if failures:
        print(f"{failures} instrument(s) could not be read.")
        return 1
    print("All instruments read successfully.  Nothing was written.")
    return 0


def _one_controller(app, want: str | None):
    """Pick the instrument a `set`/`status` command is about.

    With one controller configured there is nothing to choose, so choosing is
    not demanded.  With several, guessing would be the wrong kind of helpful.
    """
    from .instruments.ls33x import LS33x

    boxes = {n: i for n, i in app.by_name.items() if isinstance(i, LS33x)}
    if not boxes:
        raise SystemExit("no 33x controller is configured; nothing to set")
    if want:
        if want not in boxes:
            raise SystemExit(
                f"no instrument named {want!r}; configured: {sorted(boxes)}"
            )
        return boxes[want]
    if len(boxes) > 1:
        raise SystemExit(
            f"several controllers are configured ({sorted(boxes)}); "
            "say which with --instrument"
        )
    return next(iter(boxes.values()))


def cmd_set(args) -> int:
    """Change what an instrument's own PID loop is doing, then read it back.

    Deliberately a *separate*, one-shot command rather than a flag on `run`:
    setting a setpoint is an operator action with a consequence, and it should
    not be something that happens as a side effect of starting a recorder.
    """
    cfg = config_mod.load(args.config)
    _setup_logging(args.log_level or cfg.log_level)
    app = BUILDER(cfg)
    inst = _one_controller(app, args.instrument)
    try:
        inst.verify_model()
        if args.setpoint is not None:
            inst.set_setpoint(args.loop, args.setpoint)
        if args.ramp is not None:
            inst.set_ramp(args.loop, args.ramp, enable=args.ramp > 0)
        if args.pid is not None:
            inst.set_pid(args.loop, *args.pid)
        # Range last: it is the command that actually applies power, so
        # everything else is already in place by the time it lands.
        if args.range is not None:
            inst.set_heater_range(args.heater, args.range)

        print(f"{inst.name} (model {inst.model})")
        print(f"  loop {args.loop} setpoint : {inst.setpoint(args.loop):.4f} K")
        on, rate = inst.ramp(args.loop)
        print(f"  loop {args.loop} ramp     : "
              f"{'on, %.3f K/min' % rate if on else 'off'}")
        p_, i_, d_ = inst.pid(args.loop)
        print(f"  loop {args.loop} PID      : P={p_:.1f} I={i_:.1f} D={d_:.1f}")
        for out in inst.caps.heater_outputs:
            from .instruments.ls33x import HEATER_RANGE_NAMES
            r = inst.heater_range(out)
            print(f"  heater {out}          : {inst.heater_output(out):.1f}% "
                  f"of range {r} ({HEATER_RANGE_NAMES.get(r, r)})")
    except PermissionError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    except InstrumentError as exc:
        # A write that did not read back.  This is the one failure here that
        # says something about the *instrument's* state rather than about the
        # command, so it must not arrive as a traceback: `InstrumentError` is a
        # RuntimeError and was falling straight through the clause below.
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        for i in app.instruments:
            i.transport.close()
    return 0


def cmd_status(args) -> int:
    """Report what a *running* recorder is doing, by reading its status file.

    Touches no hardware and takes no lock, so it is safe to run at any time and
    from any number of terminals.  Exit status is the useful part in a script:
    0 while the recorder is alive and current, 1 when the file is missing,
    stale, or says the recorder has stopped.
    """
    from .ipc.status import read_status, status_age_s

    cfg = config_mod.load(args.config)
    path = args.file or cfg.ipc.status_path()
    status = read_status(path)
    if status is None:
        print(f"no readable status at {path}. Is the recorder running, and is "
              "ipc.enabled true in its config?", file=sys.stderr)
        return 1
    if args.json:
        import json

        print(json.dumps(status, indent=2))

    age = status_age_s(status) or 0.0
    interval = float(status.get("interval_s") or cfg.acquisition.interval_s)
    # Three intervals of slack: one slow cycle is normal, three in a row is not.
    stale = age > max(3 * interval, 5.0)
    running = bool(status.get("running", True))

    if not args.json:
        print(f"{path}")
        print(f"  recorder   : pid {status.get('pid')} on {status.get('host')}"
              f" -- {'RUNNING' if running else 'STOPPED'}")
        print(f"  last update: {status.get('iso')}  ({age:.1f} s ago)"
              f"{'  <-- STALE' if stale else ''}")
        print(f"  cycles     : {status.get('cycle')} "
              f"({status.get('dropped_cycles')} with errors) "
              f"at {interval:g} s")
        for ch in status.get("channels", []):
            k = ch.get("kelvin")
            flag = "" if ch.get("usable") else f"   <-- {ch.get('validity')}"
            print(f"    {ch.get('name', '?'):<24} "
                  f"{'   n/a' if k is None else format(k, '10.4f')} K{flag}")
        # Heaters, setpoints and analog outputs.  Printed rather than left to
        # the raw JSON because on a cryostat where this program can move a heater,
        # "what is the heater doing" is the question `status` is being asked.
        for entry in status.get("aux", []):
            value = entry.get("value")
            print(f"    {entry.get('name', '?'):<24} "
                  f"{'   n/a' if value is None else format(value, '10.4f')}")
        for link in status.get("links", []):
            state = "up" if link.get("up") else "DOWN"
            extra = f" -- {link['last_error']}" if link.get("last_error") else ""
            print(f"  link {link.get('name'):<10}: {state}, "
                  f"{link.get('reconnects', 0)} reconnect(s){extra}")
        rec = status.get("recorder") or {}
        if rec.get("path"):
            print(f"  log        : {rec['path']} ({rec.get('rows', 0)} rows)")
        cmds = status.get("commands") or {}
        print(f"  commands   : "
              f"{'accepted' if cmds.get('accepted') else 'NOT accepted'}, "
              f"{cmds.get('applied', 0)} applied / {cmds.get('refused', 0)} refused")
        if cmds.get("source_policy"):
            # Only printed when there is one.  A line saying "every source may
            # ask" on every recorder that has never heard of the policy would
            # be noise on the common case.
            entries = cmds.get("sources") or []
            print("  sources    : "
                  + ", ".join(
                      f"{e.get('name')}="
                      + ("on" if e.get("allowed") else
                         "OFF (runtime)" if e.get("configured") else "OFF (config)")
                      for e in entries)
                  + f"{'; ' if entries else ''}anything else: "
                  + ("on" if cmds.get("source_default") else "OFF"))
        # Only when there is something to say.  A "0 failures" line on every
        # healthy recorder is a line nobody reads, and this one has to be
        # noticed on the day it is not zero.
        sf = status.get("status_file") or {}
        if sf.get("failures"):
            print(f"  status file: {sf['failures']} failed write(s), "
                  f"{sf.get('writes', 0)} good"
                  + (f" -- last: {sf['last_error']}" if sf.get("last_error")
                     else " -- writing again now"))
        control = status.get("control")
        if control:
            print(f"  control    : {control.get('state')} "
                  f"setpoint {control.get('setpoint_k')} K "
                  f"output {control.get('output_pct')}%")
            for alarm in control.get("alarms", []):
                print(f"    ALARM: {alarm}")

    return 0 if (running and not stale) else 1


def cmd_send(args) -> int:
    """Command a *running* recorder through its file spool, and wait for the ack.

    The difference from `set` matters and is not cosmetic.  `set` opens the
    instrument itself, so it only works when no recorder holds the port.
    `send` writes a file that the running recorder picks up on its next cycle,
    so it only works when one *is* running -- and it is the same path MATLAB
    uses, which makes this the way to test that path without MATLAB.
    """
    import time as _time

    from .ipc.commands import CommandSpool
    from .ipc.status import read_status, status_age_s

    cfg = config_mod.load(args.config)
    spool = CommandSpool(cfg.ipc.command_path(), ttl_s=cfg.ipc.command_ttl_s)
    status_path = cfg.ipc.status_path()

    # Refuse to queue into a spool nobody is reading.  Otherwise the command
    # sits there until it expires and the operator watches nothing happen.
    status = read_status(status_path)
    if status is None:
        print(f"no recorder is running here: {status_path} is absent or "
              "unreadable. Use `set` to talk to the instrument directly.",
              file=sys.stderr)
        return 1
    age = status_age_s(status) or 0.0
    if age > max(3 * cfg.acquisition.interval_s, 5.0) or not status.get("running", True):
        print(f"the recorder's status file is {age:.0f} s old"
              f"{'' if status.get('running', True) else ' and says it has stopped'}"
              " -- not queueing a command it may never read.", file=sys.stderr)
        return 1

    kwargs = dict(args.args)
    cid = spool.submit(args.kind, instrument=args.instrument or "",
                       source=f"lschart-cli/{os.getpid()}", **kwargs)
    print(f"queued {args.kind} {kwargs or ''} as {cid}")

    deadline = _time.monotonic() + args.timeout
    while _time.monotonic() < deadline:
        status = read_status(status_path) or {}
        for ack in (status.get("commands") or {}).get("recent", []):
            if ack.get("id") == cid:
                print(("OK: " if ack.get("ok") else "REFUSED: ") + str(ack.get("message")))
                return 0 if ack.get("ok") else 1
        _time.sleep(0.2)
    print(f"no acknowledgement within {args.timeout:g} s. The command may still "
          "be applied; check `lschart status`.", file=sys.stderr)
    return 1


def cmd_init(args) -> int:
    """Write a starter config file."""
    if os.path.exists(args.path) and not args.force:
        print(f"{args.path} exists; pass --force to overwrite", file=sys.stderr)
        return 1
    cfg = config_mod.load(None)
    with open(args.path, "w") as fh:
        fh.write(config_mod.dump(cfg))
    print(f"wrote {args.path}")
    return 0


def main(argv: list[str] | None = None, *, prog: str = "lschart") -> int:
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.splitlines()[0])
    ap.add_argument("-c", "--config", default=None, help="path to config.yaml")
    ap.add_argument("--log-level", default=None)
    sub = ap.add_subparsers(dest="command")

    run = sub.add_parser("run", help="record (and optionally control) until interrupted")
    run.add_argument("--arm", action="store_true",
                     help="close the heater loop (requires control.enabled)")
    run.add_argument("--setpoint", type=float, default=None, help="kelvin")
    run.add_argument("--interval", type=float, default=None, help="override poll cadence")
    run.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    run.set_defaults(func=cmd_run)

    chk = sub.add_parser("check", help="validate a config file and exit")
    chk.set_defaults(func=cmd_check)

    prb = sub.add_parser(
        "probe",
        help="open each instrument and read everything; writes nothing",
        description="Forces every transport read-only regardless of config. "
                    "Safe to run against hardware that has never been touched.",
    )
    prb.set_defaults(func=cmd_probe)

    st = sub.add_parser(
        "set",
        help="read or change an instrument's own PID loop (setpoint, range, gains)",
        description="With no options, reports the loop's present state and "
                    "changes nothing.",
    )
    st.add_argument("--instrument", default=None,
                    help="which box, if more than one is configured")
    st.add_argument("--loop", type=int, default=1, help="control loop (default 1)")
    st.add_argument("--heater", type=int, default=1,
                    help="heater output for --range (default 1)")
    st.add_argument("--setpoint", type=float, default=None, help="kelvin")
    st.add_argument("--range", type=int, default=None, choices=[0, 1, 2, 3],
                    help="heater range: 0=off 1=low 2=medium 3=high. "
                         "THIS IS WHAT APPLIES POWER")
    st.add_argument("--ramp", type=float, default=None,
                    help="instrument setpoint ramp in K/min; 0 turns ramping off")
    st.add_argument("--pid", type=float, nargs=3, default=None,
                    metavar=("P", "I", "D"), help="the loop's own gains")
    st.set_defaults(func=cmd_set)

    stat = sub.add_parser(
        "status",
        help="report what a running recorder is doing (reads status.json)",
        description="Reads the status file only: no hardware, no lock, safe at "
                    "any time. Exits nonzero if the recorder is stale or stopped.",
    )
    stat.add_argument("--file", default=None,
                      help="status file to read, overriding the config")
    stat.add_argument("--json", action="store_true", help="dump the raw file")
    stat.set_defaults(func=cmd_status)

    snd = sub.add_parser(
        "send",
        help="command a RUNNING recorder through its file spool",
        description="The same path MATLAB uses. Use `set` instead when no "
                    "recorder is running and this should open the instrument "
                    "itself.",
    )
    snd.add_argument("--instrument", default=None,
                     help="which box, if more than one is configured")
    snd.add_argument("--timeout", type=float, default=10.0,
                     help="seconds to wait for the acknowledgement")
    snd_sub = snd.add_subparsers(dest="kind", required=True)

    sp = snd_sub.add_parser("setpoint", help="move a loop's setpoint")
    sp.add_argument("kelvin", type=float)
    sp.add_argument("--loop", type=int, default=1)

    rp = snd_sub.add_parser("ramp", help="set the instrument's setpoint ramp")
    rp.add_argument("rate_k_per_min", type=float,
                    help="K/min; 0 turns ramping off")
    rp.add_argument("--loop", type=int, default=1)

    rg = snd_sub.add_parser(
        "range", help="heater range 0..3 -- 1 and above APPLY POWER")
    rg.add_argument("value", type=int, choices=[0, 1, 2, 3])
    rg.add_argument("--output", type=int, default=1)

    an = snd_sub.add_parser(
        "analog",
        help="218 analog output percent -- ANYTHING ABOVE 0 APPLIES POWER",
        description="Manual control of a 218 analog output: one number, "
                    "straight to the DAC. There is no inert half to this the "
                    "way there is to a 33x setpoint, so it is gated like a "
                    "heater range and refused above 0 unless the recorder's "
                    "config sets ipc.allow_analog_output.",
    )
    an.add_argument("percent", type=float)

    pid = snd_sub.add_parser(
        "pid",
        help="the instrument's own P, I and D on one loop",
        description="Retune a loop on the instrument itself -- nothing to do "
                    "with any software loop. All three gains go together, "
                    "because PID is one command on the box and the driver "
                    "verifies all three by readback. Refused unless the "
                    "recorder's config sets ipc.allow_pid.",
    )
    pid.add_argument("p", type=float)
    pid.add_argument("i", type=float)
    pid.add_argument("d", type=float)
    pid.add_argument("--loop", type=int, default=1)

    snd_sub.add_parser(
        "hold",
        help="stop every loop where it is: setpoints to present temperature, "
             "software loop frozen",
        description="The second panic action. Every closed 33x loop has its "
                    "ramping switched off (the rate is kept) and its setpoint "
                    "moved to its own sensor's present temperature; a software "
                    "loop has its output frozen and stops regulating. Note "
                    "hold is not a synonym for less power: a ramp heading DOWN "
                    "sits below the temperature the cryostat has reached, so "
                    "holding demands more heat than the ramp was. It never "
                    "raises a range, so it stays inside the power already "
                    "permitted.",
    )

    arm = snd_sub.add_parser(
        "arm",
        help="close the software loop again -- the way back from `hold`",
        description="Not a panic action and exempt from nothing: arming starts "
                    "the loop driving the heater, which is the power-applying "
                    "direction, so it needs ipc.allow_analog_output like any "
                    "other write. With no kelvin it arms to hold the "
                    "temperature the cryostat is at now, which is what avoids "
                    "handing the PID a step to chase.",
    )
    arm.add_argument("kelvin", type=float, nargs="?", default=None)

    snd_sub.add_parser(
        "heaters_off",
        help="every writable heater to zero: 33x ranges AND 218 analog outputs")
    snd_sub.add_parser("ping", help="prove the command path works, touching nothing")

    def _collect_send_args(parsed) -> list[tuple]:
        """Turn the chosen sub-parser's options into the command's arguments."""
        keys = {
            "setpoint": ("kelvin", "loop"),
            "ramp": ("rate_k_per_min", "loop"),
            "range": ("value", "output"),
            "analog": ("percent",),
            "pid": ("p", "i", "d", "loop"),
            "hold": (),
            "arm": ("kelvin",),
            "heaters_off": (),
            "ping": (),
        }[parsed.kind]
        return [(k, getattr(parsed, k)) for k in keys]

    snd.set_defaults(func=cmd_send, _collect=_collect_send_args)

    ini = sub.add_parser("init", help="write a starter config.yaml")
    ini.add_argument("path", nargs="?", default=config_mod.DEFAULT_CONFIG_NAME)
    ini.add_argument("--force", action="store_true")
    ini.set_defaults(func=cmd_init)

    args = ap.parse_args(argv)
    if getattr(args, "_collect", None) is not None:
        args.args = args._collect(args)
    if args.command is None:
        args.command = "run"
        args.func = cmd_run
        for name, default in (("arm", False), ("setpoint", None),
                              ("interval", None), ("duration", None)):
            setattr(args, name, default)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
