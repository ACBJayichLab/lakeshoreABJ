"""Convert the legacy chart-recorder ``.xls`` logs into the recorder's own CSV.

The point is to stop writing one-off readers.  :mod:`lschart.tools.import_xls`
already knows how to read a legacy log; this turns what it reads into the
**same CSV the running recorder writes**, so the viewer, ``steptest
--from-csv`` and every analysis script work on year-old data without knowing
it is old.

Three things this has to reconcile, and they are the whole of the work:

**The 218 and the 336 were logged by two independent programs.**  Each wrote
its own file with its own start time and its own cadence, so there is no shared
row index -- only wall clock.  336 samples are matched to each 218 row by
nearest timestamp within ``--merge-tolerance`` (default 30 s, against cadences
of 4-20 s), and left blank where no 336 sample is near enough.  **A blank here
means the 336 was not logging, not that the shield was warm**: the match rate
is reported per file so a silent non-overlap cannot pass for data.

**The heater is not a channel.**  A 218 log has eight inputs and no record of
its own analog output; the heater setting survives only as ``ANALOG`` commands
in the Notes column.  ``ls218.aout1`` is reconstructed as a zero-order hold
from those commands, carried **across** files in chronological order because
the heater does not reset when a log rolls.  Rows before the first command
anyone recorded are blank rather than guessed.

That reconstruction is only as good as the Notes column is complete.  It is
sound for a cryostat driven entirely through the Lake Shore software, and it
is wrong -- silently, and in a way no fit can detect -- for any period when
somebody turned a knob.  Rows carrying a note keep it, so the provenance of
every step survives into the output.

**The filename lies about the instrument.**  ``cd10_..._st2_monitor3.xls`` is a
218 log.  The model is sniffed from row 0, never from the name.

Usage::

    python -m lschart.tools.xls_to_csv "reference/logs/CD10/*.xls" -o data/cd10
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as _dt
import os
import sys

from .import_xls import load_dir

#: The recorder's column order for a 3-input 218 plus a 336.  Copy an existing
#: recorder CSV with ``--header-from`` when a cryostat differs.
DEFAULT_CHANNELS = ("Sample", "Coldplate", "Magnet",
                    "RAD SHIELD", "THE CHONKE", "1st Stage", "2nd Stage")
DEFAULT_AUX = (
    "ls218.aout1",
    "ls336.setpoint1", "ls336.setpoint2", "ls336.setpoint3", "ls336.setpoint4",
    "ls336.heater1", "ls336.range1", "ls336.heater2", "ls336.range2",
    "ls336.outmode1", "ls336.ramping1", "ls336.outmode2", "ls336.ramping2",
    "ls336.outmode3", "ls336.ramping3", "ls336.outmode4", "ls336.ramping4",
)
#: Legacy 336 column -> modern aux key.  The legacy log has no RANGE, no
#: OUTMODE and no ramp flag, so those columns stay blank: the box was never
#: asked, which is not the same as the answer being zero.
AUX_FROM_336 = {
    "Setpoint 1": "ls336.setpoint1", "Setpoint 2": "ls336.setpoint2",
    "Setpoint 3": "ls336.setpoint3", "Setpoint 4": "ls336.setpoint4",
    "Heater 1": "ls336.heater1", "Heater 2": "ls336.heater2",
}
TEMPS_336 = ("RAD SHIELD", "THE CHONKE", "1st Stage", "2nd Stage")


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{v:.4f}"


def build_336_index(logs) -> tuple[list[float], list[dict]]:
    """One time-sorted table of every 336 sample across every 336 log."""
    rows: list[tuple[float, dict]] = []
    for g in logs:
        if g.model != "336" or g.started is None:
            continue
        base = g.started.timestamp()
        for i, t in enumerate(g.t_s):
            rec = {}
            for name in TEMPS_336:
                if name in g.channels:
                    rec[name] = g.channels[name][i]
            for legacy, key in AUX_FROM_336.items():
                if legacy in g.channels:
                    rec[key] = g.channels[legacy][i]
            rows.append((base + t, rec))
    rows.sort(key=lambda r: r[0])
    return [r[0] for r in rows], [r[1] for r in rows]


def heater_timeline(logs) -> list[tuple[float, float]]:
    """Every ANALOG command in every 218 log, in absolute time order."""
    out: list[tuple[float, float]] = []
    for g in logs:
        if g.model != "218" or g.started is None:
            continue
        base = g.started.timestamp()
        for t, pct in g.heater_commands():
            out.append((base + t, pct))
    out.sort(key=lambda r: r[0])
    return out


def convert(pattern: str, outdir: str, *, channel_map: dict[int, str],
            tolerance: float = 30.0, channels=DEFAULT_CHANNELS,
            aux=DEFAULT_AUX) -> list[str]:
    logs = load_dir(pattern)
    times336, recs336 = build_336_index(logs)
    heater = heater_timeline(logs)
    htimes = [h[0] for h in heater]
    os.makedirs(outdir, exist_ok=True)
    written = []

    print(f"{len(logs)} logs: "
          f"{sum(1 for g in logs if g.model == '218')} x 218, "
          f"{sum(1 for g in logs if g.model == '336')} x 336")
    print(f"336 index: {len(times336)} samples;  "
          f"heater: {len(heater)} ANALOG commands\n")

    for g in logs:
        if g.model != "218":
            continue
        if g.started is None:
            print(f"  SKIP {g.name}: no parseable start time")
            continue
        base = g.started.timestamp()
        stem = os.path.splitext(g.name)[0]
        path = os.path.join(outdir, f"{stem}.csv")
        matched = 0
        notes_by_t = {round(n.t_s, 3): n.text for n in g.notes}

        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Timestamp", "Time"] + list(channels) + list(aux)
                       + ["Validity", "State", "Notes"])
            for i, t in enumerate(g.t_s):
                abs_t = base + t
                stamp = _dt.datetime.fromtimestamp(abs_t)
                row = [stamp.isoformat(timespec="milliseconds"),
                       f"{t - g.t_s[0]:.3f}"]

                vals: dict[str, float | None] = {}
                for num, name in channel_map.items():
                    col = f"Input {num}"
                    vals[name] = g.channels[col][i] if col in g.channels else None

                rec: dict = {}
                if times336:
                    j = bisect.bisect_left(times336, abs_t)
                    cand = [k for k in (j - 1, j) if 0 <= k < len(times336)]
                    if cand:
                        k = min(cand, key=lambda k: abs(times336[k] - abs_t))
                        if abs(times336[k] - abs_t) <= tolerance:
                            rec = recs336[k]
                            matched += 1
                for name in TEMPS_336:
                    vals[name] = rec.get(name)

                row += [_fmt(vals.get(c)) for c in channels]

                pct = None
                if htimes:
                    j = bisect.bisect_right(htimes, abs_t) - 1
                    if j >= 0:
                        pct = heater[j][1]
                for key in aux:
                    row.append(_fmt(pct) if key == "ls218.aout1"
                               else _fmt(rec.get(key)))

                row += ["", "", notes_by_t.get(round(t, 3), "")]
                w.writerow(row)

        pctm = 100.0 * matched / max(len(g.t_s), 1)
        print(f"  {os.path.basename(path):<40} {len(g.t_s):>6} rows  "
              f"336 match {pctm:>5.1f}%  cmds={len(g.heater_commands()):>4}  "
              f"{str(g.started)[:16]}")
        written.append(path)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="legacy .xls -> recorder CSV")
    ap.add_argument("pattern", help="glob for the .xls logs")
    ap.add_argument("-o", "--outdir", default="data/cd10")
    ap.add_argument("--channels", default="1:Sample,2:Coldplate,3:Magnet",
                    help="218 input number to display name")
    ap.add_argument("--merge-tolerance", type=float, default=30.0,
                    help="seconds; how near a 336 sample must be to a 218 row")
    ap.add_argument("--header-from", default=None,
                    help="copy the exact column list from a recorder CSV")
    a = ap.parse_args(argv)

    cmap = {}
    for part in a.channels.split(","):
        num, name = part.split(":", 1)
        cmap[int(num)] = name.strip()

    channels, aux = list(DEFAULT_CHANNELS), list(DEFAULT_AUX)
    if a.header_from:
        with open(a.header_from, newline="", encoding="utf-8") as fh:
            hdr = next(csv.reader(fh))
        body = hdr[2:-3]
        channels = [c for c in body if "." not in c]
        aux = [c for c in body if "." in c]

    convert(a.pattern, a.outdir, channel_map=cmap,
            tolerance=a.merge_tolerance, channels=channels, aux=aux)
    return 0


if __name__ == "__main__":
    sys.exit(main())
