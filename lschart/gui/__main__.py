"""``python -m lschart.gui`` -- open the strip chart on a running recorder.

Takes the same config file the recorder takes, and reads exactly two things
from it: where ``status.json`` is, and where the command spool is.  Nothing
else in the file is consulted, and no instrument is opened -- so pointing this
at a hardware config on a machine with no VISA runtime and no serial port is
fine, and pointing two of them at one recorder is fine as well.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .source import COMFORT_STOP_K, COMFORT_STOP_PCT, GAP_FACTOR

log = logging.getLogger("lschart.gui")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lschart-view",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument("-c", "--config", default=None, help="the recorder's config.yaml")
    ap.add_argument("--status", default=None,
                    help="status.json to read, overriding the config")
    ap.add_argument("--csv", default=None,
                    help="open this log instead of following a recorder -- a "
                         "finished run, or a legacy log converted by "
                         "lschart.tools.xls_to_csv.  No recorder need be "
                         "running; the banner will say the status file is "
                         "absent, which it is")
    ap.add_argument("--refresh", type=float, default=1.0,
                    help="seconds between redraws (default 1)")
    ap.add_argument("--max-points", type=int, default=200_000,
                    help="samples kept per trace; past this the history is "
                         "thinned, not truncated")
    ap.add_argument("--gap-factor", type=float, default=GAP_FACTOR,
                    help="draw a gap where consecutive samples are further "
                         "apart than this many sample intervals (default "
                         f"{GAP_FACTOR:g}); the trace is joined across "
                         "anything closer")
    ap.add_argument("--max-kelvin", type=float, default=COMFORT_STOP_K[1],
                    help="where the temperature panel stops zooming and "
                         "panning outward (default "
                         f"{COMFORT_STOP_K[1]:g}); a reading beyond it widens "
                         "the stop to the reading, so a miswired sensor is "
                         "never hidden by it")
    ap.add_argument("--max-percent", type=float, default=COMFORT_STOP_PCT[1],
                    help="the same stop for the output panel (default "
                         f"{COMFORT_STOP_PCT[1]:g})")
    ap.add_argument("--read-only", action="store_true",
                    help="open without the ability to send commands at all")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from .. import config as config_mod

    try:
        # validate=False: the viewer must open against a config it would not
        # itself be able to run -- a hardware config on a laptop with no VISA
        # runtime, say.  It opens no instrument, so nothing it does depends on
        # those parts of the file being satisfiable here.
        cfg = config_mod.load(args.config, validate=False)
    except config_mod.ConfigError as exc:
        print(f"cannot read the config: {exc}", file=sys.stderr)
        return 1

    status_path = args.status or cfg.ipc.status_path()
    if args.csv and not os.path.exists(args.csv):
        print(f"no such log: {args.csv}", file=sys.stderr)
        return 1

    spool = None
    if not args.read_only:
        from ..ipc.commands import CommandSpool

        spool = CommandSpool(cfg.ipc.command_path(), ttl_s=cfg.ipc.command_ttl_s)

    try:
        from PySide6 import QtWidgets

        from .window import ViewerWindow
    except ImportError as exc:
        print(
            f"the viewer needs Qt, which is not installed ({exc}).\n"
            'Install it with:  pip install "lschart[gui]"\n'
            "The recorder itself deliberately does not depend on it.",
            file=sys.stderr,
        )
        return 1

    app = QtWidgets.QApplication(sys.argv[:1])
    window = ViewerWindow(
        status_path,
        spool=spool,
        refresh_ms=int(args.refresh * 1000),
        max_points=args.max_points,
        gap_factor=args.gap_factor,
        max_kelvin=args.max_kelvin,
        max_percent=args.max_percent,
        config_label=cfg.source_path or "",
        csv_path=args.csv,
    )
    window.show()
    log.info("viewing %s", args.csv or status_path)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
