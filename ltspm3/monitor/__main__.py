"""``python -m ltspm3.monitor`` -- watch a running recorder, or replay the archive.

::

    python -m ltspm3.monitor -c config.yaml              # follow the live log
    python -m ltspm3.monitor --replay reference/cooldown-10/
    python -m ltspm3.monitor --replay reference/cooldown-10/ --since 2026-09-09

The replay is the test on genuine data and it is the same code path as the live
run -- the only difference is where the samples come from.  Plan 2 section 2.3
pins its answers in ``tests_ltspm3/test_monitor.py``; this is how a person looks
at them.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time

from .judge import WARN, Judge, MonitorConfig
from .report import PlantLog, write_json
from .source import RecorderTail, replay


def _stamp(epoch: float | None) -> str:
    if not epoch:
        return "-" * 19
    return _dt.datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def run_replay(directory: str, cfg: MonitorConfig, *, since: float | None,
               quiet: bool) -> int:
    """Every verdict change, with its time.  Returns 0 always -- it is a report.

    A changed verdict and not a changed sample: the archive is a million rows
    and what a person can read is the two dozen moments the monitor changed its
    mind.
    """
    judge = Judge(cfg)
    t0, n = time.time(), 0
    for s in replay(directory):
        judge.step(s)
        n += 1
    shown = [c for c in judge.changes if since is None or c[0] >= since]
    if not quiet:
        print(f"{n} samples, {time.time() - t0:.0f} s, "
              f"{len(judge.changes)} verdict changes "
              f"({len(shown)} shown)\n")
        print(f"{'when':19s}  {'residual':14s} {'from':>10s} -> {'to':<10s}"
              f"{'T K':>8}  why")
        for epoch, _t, name, was, now, reason, temp in shown:
            t = "    none" if temp is None else f"{temp:8.2f}"
            print(f"{_stamp(epoch)}  {name:14s} {was:>10s} -> {now:<10s}{t}  "
                  f"{reason[:72]}")
    warns = [c for c in judge.changes if c[4] == WARN]
    print(f"\n{len(warns)} transitions into `warn` over the whole archive.")
    faults = [c for c in warns if c[2] == "fault_level"]
    print(f"{len(faults)} of them are fault-level -- REPORTED, never acted on.")
    return 0


def run_live(cfg_path: str, cfg: MonitorConfig, *, interval_s: float,
             once: bool) -> int:
    """Follow the recorder's log.  Writes ``plant.json`` and the daily CSV."""
    from lschart.config import load

    import ltspm3.config  # noqa: F401  -- registers `control:` and `monitor:`

    app = load(cfg_path)
    cfg = app.section("monitor", cfg)
    directory = app.recorder.directory
    # Beside status.json, because that is where every other client already
    # looks -- the viewer, `lschart status` and MATLAB all open that directory.
    plant_json = os.path.join(os.path.dirname(app.ipc.status_path()),
                              "plant.json")

    tail = RecorderTail(directory, window_s=max(cfg.window_s * 4, 7200.0))
    judge = Judge(cfg)
    log = PlantLog(directory)
    print(f"monitor: following {directory}")
    print(f"         plant.json -> {plant_json}")
    print("         REPORT ONLY -- this process holds no port and sends no "
          "commands")
    try:
        while True:
            added = tail.poll()
            record = None
            for s in list(tail.samples)[-added:] if added else ():
                record = judge.step(s)
                log.write(record)
            if record is not None:
                write_json(plant_json, record, cfg=cfg,
                           stale_after_s=interval_s * 5)
                while judge.changes:
                    epoch, _t, name, was, now, reason, _T = judge.changes.pop(0)
                    print(f"{_stamp(epoch)}  {name}: {was} -> {now}"
                          + (f"  ({reason})" if reason else ""))
            if once:
                return 0
            time.sleep(interval_s)
    except KeyboardInterrupt:
        return 0
    finally:
        log.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-c", "--config", help="the recorder's config file")
    ap.add_argument("--replay", metavar="DIR",
                    help="replay a curated archive instead of following a log")
    ap.add_argument("--since", help="only show changes at or after this ISO date")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="seconds between polls of the live log")
    ap.add_argument("--once", action="store_true",
                    help="one poll and exit -- for a smoke test")
    ap.add_argument("--quiet", action="store_true",
                    help="counts only, no per-change table")
    args = ap.parse_args(argv)

    cfg = MonitorConfig()
    since = None
    if args.since:
        since = _dt.datetime.fromisoformat(args.since).timestamp()

    if args.replay:
        return run_replay(args.replay, cfg, since=since, quiet=args.quiet)
    if not args.config:
        ap.error("one of -c/--config or --replay is required")
    return run_live(args.config, cfg, interval_s=args.interval, once=args.once)


if __name__ == "__main__":
    sys.exit(main())
