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

log = logging.getLogger("lschart")


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

    app = Application(cfg)
    mode = "hardware" if cfg.uses_hardware else "SIMULATION"
    log.warning(
        "starting in %s; control %s; %.3f s cadence",
        mode,
        "ENABLED" if cfg.control.enabled else "disabled",
        cfg.acquisition.interval_s,
    )
    if cfg.control.enabled and not args.arm:
        log.warning("control is configured but NOT armed; pass --arm to close the loop")

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
        if cfg.control.enabled and args.arm:
            _arm_when_ready(app, args.setpoint, cfg.acquisition.interval_s)
        while not stopping:
            time.sleep(0.25)
            if args.duration and app.poller.cycles * cfg.acquisition.interval_s >= args.duration:
                break
    finally:
        app.stop()
        if app.recorder is not None:
            log.warning("wrote %d rows to %s", app.recorder.rows_written, app.recorder.path)
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
    print(f"  backend        : {'visa (HARDWARE)' if cfg.uses_hardware else 'sim'}")
    print(f"  control        : {'enabled' if cfg.control.enabled else 'disabled'}")
    print(f"  control channel: {cfg.control_channel}")
    print(f"  cadence        : {cfg.acquisition.interval_s} s")
    print(f"  budget         : {cfg.estimated_transactions()} transactions "
          f"~ {cfg.estimated_cycle_s():.2f} s per cycle")
    if cfg.control.enabled:
        s = cfg.control.supervisor
        lo = max(s.hard_min_pct, s.operating_point_pct - s.authority_pct)
        hi = min(s.hard_max_pct, s.operating_point_pct + s.authority_pct)
        print(f"  authority band : {lo:.3f}% .. {hi:.3f}%  (on_exit={s.on_exit})")
    print("OK")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lschart", description=__doc__.splitlines()[0])
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
