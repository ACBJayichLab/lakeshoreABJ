"""``lschart`` entry point.

Records by default and controls only if the config says so.  Arming the heater
is never implicit: a chart recorder that silently starts driving a heater
because someone ran it with the wrong config file is precisely the failure this
project is trying not to have.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from . import config as config_mod
from .app import Application
from .ipc import AlreadyRunning, InstanceLock

log = logging.getLogger("lschart")

#: How to build the Application.  `ltspm.__main__` swaps this for its own
#: builder, which adds the heater loop and the calibrated plant.  Everything
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
    print(f"  backend        : {'visa (HARDWARE)' if cfg.uses_hardware else 'sim'}")
    print(f"  control        : {'enabled' if enabled else 'disabled'}")
    print(f"  control channel: {cfg.control_channel}")
    print(f"  cadence        : {cfg.acquisition.interval_s} s")
    print(f"  budget         : {cfg.estimated_transactions()} transactions "
          f"~ {cfg.estimated_cycle_s():.2f} s per cycle")
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
    except (ValueError, OSError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        for i in app.instruments:
            i.transport.close()
    return 0


def cmd_init(args) -> int:
    """Write a starter config file."""
    import os

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

    ini = sub.add_parser("init", help="write a starter config.yaml")
    ini.add_argument("path", nargs="?", default=config_mod.DEFAULT_CONFIG_NAME)
    ini.add_argument("--force", action="store_true")
    ini.set_defaults(func=cmd_init)

    args = ap.parse_args(argv)
    if args.command is None:
        args.command = "run"
        args.func = cmd_run
        for name, default in (("arm", False), ("setpoint", None),
                              ("interval", None), ("duration", None)):
            setattr(args, name, default)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
