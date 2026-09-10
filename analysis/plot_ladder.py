"""The 2026-09-05 programmed ladder, drawn to verify the window is the right one.

There are **two runs in this one export**, which is the thing to see before
anything is fitted to it.  `ltspm3.tools.sweep` was started at 12:22, cancelled
at 13:07 thirteen rungs in, and restarted at 13:18 to run the full thirty.  Both
climbs are good data and they are in the same file, separated by a deliberate
drop to zero output and a cooldown back to 4.8 K.

That drop is what makes the split unambiguous, and it is how the runs are found
here: rungs are maximal spans of near-constant `ls218.aout1`, and a run is a
block of rungs bounded by output at zero.  Nothing is keyed to a clock time, so
the same script tells the truth about a differently-timed ladder.

The aborted run is not the worse of the two.  It dwelt **120 s** a rung against
the completed run's **46 s** -- `--min-dwell 45` was passed the second time --
so between 4.8 K and 19 K it is the better-settled measurement of the pair, and
its 42.7% rung sat for nineteen minutes because that is where the cancellation
caught it.  See HANDOFF.md: three rungs of the completed run were dropped before
grading for falling under `steps.py`'s 60 s floor.

Panel (c) is the check that matters.  Where the two runs overlap in output they
have to land on the same temperature, because the cryostat does not care which
invocation moved the DAC.  If they disagree there, something drifted between
12:22 and 17:35 and neither run means what it says.

No dual axis anywhere: temperature and percent are different scales and get
their own panels on a shared clock.
"""
from __future__ import annotations

import datetime as dt
import math
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, "analysis")
import _data as D  # noqa: E402
import segments as _seg  # noqa: E402

#: The programmed ladder as a MANIFEST WINDOW, not a region export -- same
#: bounds to the second, same rows, and the archive carries the log's own
#: precision rather than the export's.
LADDER_WINDOW = "trace-ladder-20260905"

OUT = sys.argv[1] if len(sys.argv) > 1 else "analysis/ladder_window.png"

#: A rung is "the same rung" while the output stays inside this band.  The 218's
#: readback dithers by about 0.003%, and the closest neighbouring rungs in the
#: plan are 0.6% apart, so there are two orders of magnitude of room here.
U_TOL_PCT = 0.05
#: Below this the heater is off, not on a low rung.  The plan's first rung is
#: 7.19%; a cooldown reads a flat 0.000.
U_OFF_PCT = 1.0
#: Shorter than this and it is the settling between rungs, not a dwell.
MIN_RUNG_S = 20.0
#: Fewer rungs than this and a powered block is somebody adjusting the heater,
#: not a programmed ladder.  The two real runs here are 13 and 30 rungs; the
#: hand-driven material in front of them is 2.
MIN_RUNGS = 3
#: T_end is averaged over the tail of a rung rather than read off the last
#: sample, which is one 30 mK-noisy reading.
TAIL_S = 10.0

# Categorical slots 1 and 2 of the reference palette, plus its light surface and
# ink.  Slots 1-3 are the documented all-pairs-validated subset, so a two-series
# scatter is safe without re-stepping.  Marker SHAPE carries the same identity as
# hue, for the colour-vision case and for print.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#dedcd5"
CONTEXT = "#b4b2a8"
RUNS = [
    {"color": "#2a78d6", "marker": "o", "label": "run 1, cancelled (120 s dwells)"},
    {"color": "#eb6834", "marker": "s", "label": "run 2, complete (46 s dwells)"},
]


def load():
    """Timestamp, sample, coldplate and output, as four parallel lists."""
    w = _seg.load(LADDER_WINDOW)
    ts, sample, coldplate, u = [], [], [], []
    for e, s, c, a in zip(w.epoch, w.T, w.Tc, w.u):
        if math.isnan(s) or math.isnan(c) or math.isnan(a):
            continue  # a marked channel; the recorder still wrote the row
        ts.append(dt.datetime.fromtimestamp(e))
        sample.append(float(s))
        coldplate.append(float(c))
        u.append(float(a))
    return ts, sample, coldplate, u


def rungs(ts, u):
    """Maximal spans of near-constant output, as (i0, i1, u_mean), i1 inclusive."""
    out = []
    i0 = 0
    for i in range(1, len(u) + 1):
        if i < len(u) and abs(u[i] - u[i0]) <= U_TOL_PCT:
            continue
        span = (ts[i - 1] - ts[i0]).total_seconds()
        if span >= MIN_RUNG_S:
            out.append((i0, i - 1, sum(u[i0:i]) / (i - i0)))
        i0 = i
    return out


def split_runs(segments):
    """The ladder runs, and the powered blocks that are not ladder runs.

    Cutting at zero output alone finds three blocks here, not two: the export
    opens 37 minutes before the first sweep with the cryostat sitting at 60% and
    then stepped down to 48% by hand, which is real data but is not a run.  A
    ladder CLIMBS -- `plan_sweep.py` emits rungs in increasing order and the tool
    walks them in that order -- so a block of at least MIN_RUNGS that never steps
    down is a run, and the hand-driven descent in front of it is not.  Both tests
    are on the shape of the block, so neither goes stale at a different clock
    time or a different ladder length.
    """
    blocks, cur = [], []
    for seg in segments:
        if seg[2] < U_OFF_PCT:
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(seg)
    if cur:
        blocks.append(cur)

    runs, other = [], []
    for block in blocks:
        climbs = all(b[2] >= a[2] for a, b in zip(block, block[1:]))
        (runs if len(block) >= MIN_RUNGS and climbs else other).append(block)
    return runs, other


def t_end(ts, sample, i0, i1):
    """Mean sample temperature over the last TAIL_S of a rung."""
    cut = ts[i1] - dt.timedelta(seconds=TAIL_S)
    tail = [sample[i] for i in range(i0, i1 + 1) if ts[i] >= cut] or [sample[i1]]
    return sum(tail) / len(tail)


def main() -> None:
    ts, sample, coldplate, u = load()
    runs, other = split_runs(rungs(ts, u))

    print(f"{LADDER_WINDOW}  ({D.resolve(_seg.load(LADDER_WINDOW).table.file, D.ARCHIVE_DIR)})")
    print(f"  {len(ts)} rows, {ts[0]:%Y-%m-%d %H:%M:%S} -> {ts[-1]:%H:%M:%S} "
          f"({(ts[-1] - ts[0]).total_seconds() / 3600:.2f} h)")
    print(f"  sample {min(sample):.2f} - {max(sample):.2f} K, "
          f"output {min(u):.3f} - {max(u):.3f} %")

    def describe(label, block):
        a, b = block[0], block[-1]
        dwells = [(ts[i1] - ts[i0]).total_seconds() for i0, i1, _ in block]
        lo = t_end(ts, sample, a[0], a[1])
        hi = t_end(ts, sample, b[0], b[1])
        print(f"  {label}: {len(block)} rungs, "
              f"{ts[a[0]]:%H:%M:%S} -> {ts[b[1]]:%H:%M:%S} "
              f"({(ts[b[1]] - ts[a[0]]).total_seconds() / 3600:.2f} h), "
              f"{a[2]:.2f} -> {b[2]:.2f} %, {lo:.2f} -> {hi:.2f} K, "
              f"dwell {min(dwells):.0f}-{max(dwells):.0f} s")

    for n, run in enumerate(runs, 1):
        describe(f"run {n}", run)
    for block in other:
        describe("not a run", block)

    if len(runs) != 2:
        print(f"\nplot_ladder: expected 2 ladder runs in {LADDER_WINDOW}, "
              f"found {len(runs)}.")
        print("  The window may be wrong, or the export may be a different run.")

    fig, (ax_t, ax_u, ax_c, ax_d) = plt.subplots(
        4, 1, figsize=(11.0, 13.5), height_ratios=[2.1, 1.0, 1.9, 1.2],
        facecolor=SURFACE,
    )
    for ax in (ax_t, ax_u, ax_c, ax_d):
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, linewidth=0.7, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.tick_params(colors=INK_2, labelsize=9)

    # (a) and (b) share the clock; (c) has its own abscissa, so the link is made
    # by hand rather than by sharex=, which would drag (c) along with them.
    ax_u.sharex(ax_t)

    ax_t.plot(ts, sample, color=CONTEXT, linewidth=1.4, zorder=2,
              label="not part of either run")
    ax_u.plot(ts, u, color=CONTEXT, linewidth=1.4, zorder=2)
    ax_t.plot(ts, coldplate, color=CONTEXT, linewidth=1.0, linestyle=(0, (4, 3)),
              zorder=2)

    for n, (run, style) in enumerate(zip(runs, RUNS)):
        i0, i1 = run[0][0], run[-1][1]
        sl = slice(i0, i1 + 1)
        ax_t.plot(ts[sl], sample[sl], color=style["color"], linewidth=2.0,
                  zorder=4, label=style["label"])
        ax_u.plot(ts[sl], u[sl], color=style["color"], linewidth=2.0, zorder=4)
        # Run 1 goes on top as an open ring so run 2's filled square shows
        # through it.  The two curves coincide to a few hundred mK, which is the
        # result -- drawn as two filled series the upper one simply erases the
        # lower and the panel reads as though a run were missing.
        ring = n == 0
        ax_c.plot([r[2] for r in run],
                  [t_end(ts, sample, r[0], r[1]) for r in run],
                  color=style["color"], marker=style["marker"],
                  markersize=11.0 if ring else 6.0,
                  markerfacecolor="none" if ring else style["color"],
                  markeredgecolor=style["color"] if ring else SURFACE,
                  markeredgewidth=1.8 if ring else 1.4,
                  linewidth=0 if ring else 1.6,
                  zorder=6 if ring else 4,
                  label=f"{style['label']} -- {len(run)} rungs")

    ax_t.set_ylabel("temperature (K)", color=INK_2, fontsize=10)
    ax_t.set_title(
        f"(a)  the ladder window: {ts[0]:%Y-%m-%d %H:%M} to {ts[-1]:%H:%M}, "
        f"two runs in one export",
        color=INK, fontsize=12, loc="left", pad=10,
    )
    ax_t.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK_2)
    mid = len(ts) // 2
    ax_t.annotate("Coldplate", xy=(ts[mid], coldplate[mid]),
                  xytext=(0, 8), textcoords="offset points",
                  color=INK_2, fontsize=9, ha="center")
    ax_t.tick_params(labelbottom=False)

    ax_u.set_ylabel("218 analog out (%)", color=INK_2, fontsize=10)
    ax_u.set_title("(b)  the actuator, on the same clock -- zero between the runs",
                   color=INK, fontsize=12, loc="left", pad=8)
    ax_u.xaxis.set_major_locator(mdates.HourLocator())
    ax_u.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_u.set_xlabel(f"wall clock, {ts[0]:%Y-%m-%d}", color=INK_2, fontsize=10)

    ax_c.set_ylabel("settled sample temperature (K)", color=INK_2, fontsize=10)
    ax_c.set_title("(c)  the two runs on one curve -- rings are run 1, over run 2",
                   color=INK, fontsize=12, loc="left", pad=10)
    ax_c.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK_2)
    ax_c.sharex(ax_d)
    ax_c.tick_params(labelbottom=False)

    # (d) The check, made a number.  The two ladders walked the SAME planned
    # rungs, so the overlap is a rung-for-rung comparison and needs no
    # interpolation: pair them on output and subtract.
    pairs = []
    for r1 in runs[0]:
        for r2 in runs[1]:
            if abs(r1[2] - r2[2]) <= U_TOL_PCT:
                pairs.append((r1[2],
                              t_end(ts, sample, r1[0], r1[1]),
                              t_end(ts, sample, r2[0], r2[1])))
                break
    if pairs:
        worst = max(pairs, key=lambda p: abs(p[1] - p[2]))
        print(f"  overlap: {len(pairs)} of {len(runs[0])} run-1 rungs matched "
              f"run-2 rungs within {U_TOL_PCT} %")
        print(f"  worst disagreement {worst[1] - worst[2]:+.3f} K at "
              f"{worst[0]:.2f} % ({worst[1]:.2f} K)")
        ax_d.axhline(0.0, color=CONTEXT, linewidth=1.2, zorder=2)
        ax_d.plot([p[0] for p in pairs], [(p[1] - p[2]) * 1e3 for p in pairs],
                  color=INK_2, marker="D", markersize=6.0,
                  markeredgecolor=SURFACE, markeredgewidth=1.4, linewidth=1.6,
                  zorder=4)
        # Selective direct labels, not a number on every point -- and deduped,
        # because the worst rung is usually also the last one and two annotates
        # at one coordinate render as fuzz.
        for p in dict.fromkeys((pairs[0], worst, pairs[-1])):
            ax_d.annotate(f"{(p[1] - p[2]) * 1e3:+.0f}", xy=(p[0], (p[1] - p[2]) * 1e3),
                          xytext=(0, 9), textcoords="offset points",
                          color=INK_2, fontsize=9, ha="center")
    ax_d.set_xlabel("218 analog out (%)", color=INK_2, fontsize=10)
    ax_d.set_ylabel("run 1 - run 2 (mK)", color=INK_2, fontsize=10)
    ax_d.set_title("(d)  and the same thing as a residual, rung for rung",
                   color=INK, fontsize=12, loc="left", pad=10)

    fig.tight_layout()
    fig.savefig(OUT, dpi=140, facecolor=SURFACE)
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
