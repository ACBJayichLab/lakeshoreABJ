"""Measure the response the only way that actually works: step it and watch.

Why this and not another curve fit
----------------------------------

A steady-state percent-to-temperature curve is underdetermined by the
reference logs.  Two forms fit the 24 settled points to R^2 = 0.9969 and
0.99998 and then disagree by tens of kelvin outside the fitted band::

    dT ~ pct^6.51            43% -> 16.1 K
    dT ~ (pct-56.9)^0.92     43% -> heater is off

43% -> 18.2 K is measured, so the second is wrong, but nothing *in the fit*
says so.  Worse, the whole curve is regime-specific: the sample is a weakly
pinned island, and heating it off a 300 K coldplate is a different thermal response from
heating it off a 4 K one because the available cooling power is different.

What a controller actually needs is local and measurable:

* **gain** ``K = dT/d(pct)`` in K/% -- how far a trim moves us, here;
* **time constant** ``tau`` -- how long it takes, here.

A step test yields both directly, with no functional form assumed and no
extrapolation.  Doing it at a handful of temperatures gives the schedule in
:mod:`ltspm3.control.tuning`, which is what sets the PI gains.

Protocol
--------

At each operating point, with the loop in MANUAL:

1. let the temperature settle;
2. step the heater by ``step_pct`` -- big enough to dominate the noise, small
   enough to stay in the linear region and inside the authority band;
3. hold for at least ``5 * tau`` (about an hour at tau = 620 s);
4. optionally step back and repeat, which also tests for hysteresis.

``K = dT_final / d(pct)`` and ``tau`` comes from the exponential fit.

Usage::

    # against the simulator, right now
    python -m ltspm3.tools.steptest --points 63.0,64.0,65.0

    # against a recorded run (the heater column is auto-detected)
    python -m ltspm3.tools.steptest --from-csv data/ltspm3-heater_2026-09-02.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import sys

from ..control.tuning import OperatingPoint, identify_first_order


def analyse_step(samples, step_pct: float, *, coldplate_k: float | None = None
                 ) -> OperatingPoint:
    """``(t_s, kelvin)`` plus the output step -> one schedule entry."""
    if step_pct == 0:
        raise ValueError("step_pct must be non-zero")
    t_inf, tau, r2 = identify_first_order(samples)
    t0 = samples[0][1]
    gain = (t_inf - t0) / step_pct
    if gain <= 0:
        raise ValueError(
            f"gain came out {gain:.3f} K/% -- the temperature moved against the "
            "step, so this is not a clean step response"
        )
    return OperatingPoint(
        kelvin=(t0 + t_inf) / 2.0,
        gain_k_per_pct=gain,
        tau_s=tau,
        coldplate_k=coldplate_k,
        note=f"step {step_pct:+.3f}%, r2={r2:.4f}",
    )


def run_simulated(points: list[float], *, step_pct: float = 0.5,
                  settle_s: float = 4000.0, dt: float = 4.0) -> list[OperatingPoint]:
    """Run the protocol against the simulator, on a virtual clock.

    Useful as a rehearsal: it exercises exactly the analysis that will be
    applied to real data, so a mistake in the procedure shows up here rather
    than after an hour of cryostat time.
    """
    from lschart.instruments.sim import Sim218, SimulatedCryostat

    from ..sim_response import ResponseParams, ThermalModel

    class Clock:
        t = 0.0

        def __call__(self):
            return self.t

    out: list[OperatingPoint] = []
    for base in points:
        clock = Clock()
        # The calibrated response, explicitly: a rehearsal against the generic
        # one-pole model would identify that model's tau, not this cryostat's.
        cryostat = SimulatedCryostat(ThermalModel(ResponseParams()), time_source=clock)
        sim = Sim218(cryostat)
        cryostat.response.pct = base
        # Start already settled at this operating point, so the step is the
        # only transient in the data.
        settled = cryostat.response.p.steady_state(base) - cryostat.response.p.t_bath
        cryostat.response._fast = cryostat.response._slow = settled
        sim.analog_pct = base

        for _ in range(int(settle_s / dt)):
            clock.t += dt
            sim.handle_query("KRDG? 1")

        sim.handle_write(f"ANALOG 1, 0, 2, 1, 1,1,1,{base + step_pct:.3f}")
        samples = []
        for _ in range(int(settle_s / dt)):
            clock.t += dt
            k = float(sim.handle_query("KRDG? 1"))
            samples.append((clock.t, k))
        try:
            op = analyse_step(samples, step_pct)
        except ValueError as exc:
            print(f"  {base:.3f}% -> could not identify: {exc}", file=sys.stderr)
            continue
        out.append(op)
    return out


#: Heater-output columns a recorder CSV may carry, best first.  ``heater_pct``
#: is what the SOFTWARE loop commanded and exists only when a ``control:``
#: section was running.  ``ls218.aout1`` is the instrument's own readback and is
#: the only heater column a manual stage-3 log has -- which is every
#: ``ltspm3-heater_*.csv`` on this cryostat.  Defaulting to ``heater_pct`` alone
#: meant the documented invocation dropped every row and then blamed the file
#: for being too short.
HEATER_COLUMNS = ("heater_pct", "ls218.aout1")


def _pick_heater_column(fieldnames, requested: str | None, path: str) -> str:
    """Name the heater column, and say what was there when we cannot."""
    have = list(fieldnames or ())
    if requested is not None:
        if requested not in have:
            raise ValueError(
                f"{path}: no column {requested!r}; has {have}"
            )
        return requested
    for candidate in HEATER_COLUMNS:
        if candidate in have:
            return candidate
    raise ValueError(
        f"{path}: no heater column (looked for {list(HEATER_COLUMNS)}); "
        f"has {have} -- pass --heater-column"
    )


def _read_rows(paths, channel: str, heater_column: str | None):
    """``(t_s, kelvin, pct)`` across one or more recorder CSVs, in time order.

    ``Time`` restarts at midnight in every file, so a multi-file read has to go
    by ``Timestamp``; the holds worth identifying on this cryostat routinely run
    past midnight, and stitching on ``Time`` would fold them onto each other.
    """
    if isinstance(paths, str):
        paths = [paths]
    rows: list[tuple[float, float, float]] = []
    for path in paths:
        with open(path, newline="") as fh:
            reader = csv.DictReader(fh)
            have = list(reader.fieldnames or ())
            column = _pick_heater_column(have, heater_column, path)
            if channel not in have:
                raise ValueError(f"{path}: no channel {channel!r}; has {have}")
            for row in reader:
                try:
                    k = float(row[channel])
                    h = float(row[column])
                except (KeyError, TypeError, ValueError):
                    continue
                stamp = row.get("Timestamp")
                if stamp:
                    try:
                        t = _dt.datetime.fromisoformat(stamp).timestamp()
                    except ValueError:
                        continue
                else:
                    try:
                        t = float(row["Time"])
                    except (KeyError, TypeError, ValueError):
                        continue
                rows.append((t, k, h))
    rows.sort(key=lambda r: r[0])
    return rows


#: Output changes smaller than this are readback noise, not a step.  Half a DAC
#: code.
#:
#: ``AOUT?`` on the 218 flickers by 0.003% with nothing commanded, and it does so
#: at SOME commanded values and not others -- at 66.598% it read 66.595 on 3.2%
#: of samples (3,372 excursions over 65 h, almost all one sample long), while at
#: 69.027% it did not flicker once in 46,050 samples over 25.6 h.  0.003% is
#: below one DAC code, so this is the instrument's own formatting sitting on a
#: rounding boundary rather than the output moving.
#:
#: Without a deadband every flicker is a "step".  Over the 2026-08-24 -> 09-03
#: data that is 14,509 apparent changes instead of 99, and 119 of the 134 moves
#: it finds are spurious.  Worse than the noise is where it puts them: they fall
#: inside the coalescing window, so they extend the *real* move until the whole
#: thermal transient has already happened before the hold is judged to begin.
#: The fit then sees only the flat tail and reports tau in the tens of thousands
#: of seconds at R^2 ~ 0.05.
DEADBAND_PCT = 0.005


def from_csv(paths, *, channel: str = "Sample",
             heater_column: str | None = None,
             min_hold_s: float = 600.0,
             deadband_pct: float = DEADBAND_PCT) -> list[OperatingPoint]:
    """Find heater steps in recorder CSVs and identify each one.

    ``paths`` is one path or several; several are stitched in time order, which
    is what it takes to see a hold that ran past midnight.

    A step here is a *move* -- every output change separated from the next by
    less than ``min_hold_s`` is coalesced into one.  That is not a nicety: the
    heater on this cryostat is walked up in a burst of small increments over a
    few minutes and then held for hours, so treating each increment as its own
    step attributes the whole subsequent rise to the last increment alone and
    reports a gain several times too small, with a healthy-looking R^2 on a fit
    that began partway through the walk.
    """
    rows = _read_rows(paths, channel, heater_column)
    if len(rows) < 50:
        raise ValueError(f"{paths}: not enough usable rows")

    # Compare against the last ACCEPTED level, not against the previous sample:
    # hysteresis, so a readback that dithers either side of a boundary does not
    # emit a change on every crossing.
    changed: list[int] = []
    level = rows[0][2]
    for i in range(1, len(rows)):
        if abs(rows[i][2] - level) > deadband_pct:
            changed.append(i)
            level = rows[i][2]
    if not changed:
        return []

    # Group the change indices into moves, then pair each move with the hold
    # that FOLLOWS it.  Pairing a hold with the step that *ended* it -- which is
    # what this did -- attributes a segment's whole rise to the next step and
    # gets the gain wrong by the ratio of the two step sizes, while still
    # reporting a near-perfect R^2.  It also never analysed the final segment,
    # which on a manual log is usually the longest and best settled.
    moves: list[tuple[int, int]] = []          # (first change, first hold index)
    run_start = changed[0]
    for a, b in zip(changed, changed[1:]):
        if rows[b][0] - rows[a][0] >= min_hold_s:
            moves.append((run_start, a + 1))
            run_start = b
    moves.append((run_start, changed[-1] + 1))

    out: list[OperatingPoint] = []
    for n, (first, hold) in enumerate(moves):
        step = rows[hold - 1][2] - rows[first - 1][2]
        if abs(step) <= deadband_pct:
            continue
        end = moves[n + 1][0] if n + 1 < len(moves) else len(rows)
        segment = [(t, k) for t, k, _ in rows[hold:end]]
        if len(segment) > 50 and abs(step) > 0.05:
            try:
                out.append(analyse_step(segment, step))
            except ValueError:
                pass
    return out


def as_schedule_literal(points: list[OperatingPoint]) -> str:
    """Render as a paste-able ``TuningConfig.schedule``."""
    lines = ["schedule = ("]
    for p in sorted(points, key=lambda x: x.kelvin):
        cp = "None" if p.coldplate_k is None else f"{p.coldplate_k:.2f}"
        lines.append(
            f"    OperatingPoint({p.kelvin:.1f}, {p.gain_k_per_pct:.3f}, "
            f"{p.tau_s:.0f}, coldplate_k={cp}, note={p.note!r}),"
        )
    lines.append(")")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--points", default="63.076,65.0,67.0",
                    help="comma-separated heater percents to test (simulated)")
    ap.add_argument("--step-pct", type=float, default=0.5)
    ap.add_argument("--settle-s", type=float, default=4000.0)
    ap.add_argument("--from-csv", default=None, nargs="+",
                    help="analyse recorded run(s) instead; several are stitched in "
                         "time order")
    ap.add_argument("--channel", default="Sample")
    ap.add_argument("--deadband-pct", type=float, default=DEADBAND_PCT,
                    help="output changes smaller than this are readback noise")
    ap.add_argument("--min-hold-s", type=float, default=600.0,
                    help="output changes closer together than this are one step")
    ap.add_argument("--heater-column", default=None,
                    help=f"heater output column (default: first of {list(HEATER_COLUMNS)} "
                         "present in the file)")
    args = ap.parse_args(argv)

    if args.from_csv:
        points = from_csv(args.from_csv, channel=args.channel,
                          heater_column=args.heater_column,
                          min_hold_s=args.min_hold_s,
                          deadband_pct=args.deadband_pct)
        source = ', '.join(args.from_csv)
    else:
        points = run_simulated([float(p) for p in args.points.split(",")],
                               step_pct=args.step_pct, settle_s=args.settle_s)
        source = "simulator"

    if not points:
        print("no usable step responses found", file=sys.stderr)
        return 1

    print(f"{len(points)} operating point(s) from {source}\n")
    print(f"{'T (K)':>9} {'gain K/%':>9} {'tau (s)':>8}  note")
    for p in sorted(points, key=lambda x: x.kelvin):
        print(f"{p.kelvin:9.2f} {p.gain_k_per_pct:9.3f} {p.tau_s:8.0f}  {p.note}")
    print("\nPaste into TuningConfig:\n")
    print(as_schedule_literal(points))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
