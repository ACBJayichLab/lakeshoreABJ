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

    # against a recorded run
    python -m ltspm3.tools.steptest --from-csv data/lschart_2026-08-23.csv
"""

from __future__ import annotations

import argparse
import csv
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


def from_csv(path: str, *, channel: str = "Sample",
             heater_column: str = "heater_pct") -> list[OperatingPoint]:
    """Find heater steps in a recorder CSV and identify each one."""
    rows = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                t = float(row["Time"])
                k = float(row[channel])
                h = float(row[heater_column])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append((t, k, h))
    if len(rows) < 50:
        raise ValueError(f"{path}: not enough usable rows")

    out: list[OperatingPoint] = []
    start = 0
    for i in range(1, len(rows)):
        if abs(rows[i][2] - rows[i - 1][2]) < 1e-9:
            continue
        step = rows[i][2] - rows[start][2] if start else rows[i][2] - rows[i - 1][2]
        segment = [(t, k) for t, k, _ in rows[start:i]]
        if start and len(segment) > 50 and abs(step) > 0.05:
            try:
                out.append(analyse_step(segment, step))
            except ValueError:
                pass
        start = i
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
    ap.add_argument("--from-csv", default=None, help="analyse a recorded run instead")
    ap.add_argument("--channel", default="Sample")
    args = ap.parse_args(argv)

    if args.from_csv:
        points = from_csv(args.from_csv, channel=args.channel)
        source = args.from_csv
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
