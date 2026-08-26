"""Push a historical log through the real filter/guard pipeline.

This is the only test that uses genuine data rather than a simulation, and it
is what the guard thresholds are actually accountable to.  It answers two
questions that the simulator structurally cannot:

1. **False positives.**  Over 1,510 h of real cryostat behaviour -- cooldowns,
   warmups, heater steps, week-long holds -- how often does the guard reject a
   sample that was fine?  Every rejection freezes the heater, so a few per day
   is a nuisance and a few per hour is unusable.
2. **True positives.**  Does it catch the 9 known glitch events?

Run it directly::

    python -m ltspm3.tools.replay reference/logs/CD8/*.xls
    python -m ltspm3.tools.replay --channel "Input 1" reference/logs/CD*/*.xls

The pipeline under test is exactly the production one -- ``SensorGuard``,
``MeasurementFilter`` and ``CoherenceMonitor`` -- fed from the log instead of
from an instrument.  Nothing is stubbed.
"""

from __future__ import annotations

import argparse
import glob
import sys
from dataclasses import dataclass, field

from ..control.coherence import CoherenceConfig, CoherenceMonitor
from ..control.filters import MeasurementFilter
from ..control.health import HealthState, SensorGuard, SensorGuardConfig
from lschart.model import Reading
from lschart.tools.import_xls import ChartLog, load

#: The 218 input carrying the sample, and the 336 channel names.
DEFAULT_CONTROL_CHANNEL = "Input 1"


@dataclass
class RejectionRun:
    """A consecutive stretch of rejected samples -- i.e. one candidate event."""

    start_t: float
    end_t: float
    n: int
    validity: str
    reached: str                  # worst health state entered
    k_min: float
    k_max: float
    true_k: float                 # last trusted value before it began

    @property
    def duration_s(self) -> float:
        return self.end_t - self.start_t


@dataclass
class ReplayResult:
    log: str
    channel: str
    n_samples: int
    hours: float
    cadence_s: float
    n_rejected: int = 0
    runs: list[RejectionRun] = field(default_factory=list)
    reached_fault: int = 0
    by_validity: dict[str, int] = field(default_factory=dict)

    @property
    def rejects_per_day(self) -> float:
        return self.n_rejected / (self.hours / 24.0) if self.hours else 0.0

    @property
    def events(self) -> list[RejectionRun]:
        """Rejection runs long enough to be an event rather than a lone sample."""
        return [r for r in self.runs if r.n >= 2]


def replay(
    log: ChartLog,
    *,
    channel: str = DEFAULT_CONTROL_CHANNEL,
    guard_config: SensorGuardConfig | None = None,
    coherence_config: CoherenceConfig | None = None,
    filter_kwargs: dict | None = None,
) -> ReplayResult:
    """Feed one log through the guard and report what it would have done."""
    if channel not in log.channels:
        raise KeyError(f"{log.name} has no channel {channel!r}; has {log.populated_channels}")

    guard = SensorGuard(guard_config, name=channel)
    coherence = CoherenceMonitor(coherence_config)
    filt = MeasurementFilter(**(filter_kwargs or {}))

    others = [c for c in log.populated_channels if c != channel]
    result = ReplayResult(log.name, channel, len(log.t_s), log.duration_h, log.cadence_s)

    run: RejectionRun | None = None
    last_good = None
    last_t = None

    for i, t in enumerate(log.t_s):
        value = log.channels[channel][i]
        if value is None:
            continue
        dt = 0.0 if last_t is None else t - last_t
        last_t = t

        frame = {}
        for c in [channel] + others:
            v = log.channels[c][i]
            if v is not None:
                frame[c] = Reading(channel=c, kelvin=v)
        coherence.update(t, frame)
        corroborated, why = coherence.corroboration(channel, t)

        reading = frame.get(channel)
        spike = filt.is_spike(value, dt, t=t) if reading is not None else False
        res = guard.update(t, reading, spike=spike,
                           corroborated=corroborated, corroboration_why=why,
                           noise_k=filt.noise_estimate(), dt=dt)

        if res.validity.good and res.kelvin is not None:
            if filt.is_stale(t):
                filt.reseed(t, res.kelvin)
            else:
                filt.update(t, res.kelvin, dt)
            last_good = res.kelvin
            if run is not None:
                result.runs.append(run)
                run = None
        else:
            result.n_rejected += 1
            key = res.validity.value
            result.by_validity[key] = result.by_validity.get(key, 0) + 1
            if guard.state is HealthState.FAULT:
                result.reached_fault += 1
            if run is None:
                run = RejectionRun(t, t, 1, key, guard.state.value,
                                   value, value, last_good if last_good is not None else value)
            else:
                run.end_t = t
                run.n += 1
                run.k_min = min(run.k_min, value)
                run.k_max = max(run.k_max, value)
                run.reached = guard.state.value
    if run is not None:
        result.runs.append(run)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help=".xls files or globs")
    ap.add_argument("--channel", default=None,
                    help=f"control channel (default: {DEFAULT_CONTROL_CHANNEL} on a 218)")
    ap.add_argument("--verbose", "-v", action="store_true", help="list every rejection run")
    args = ap.parse_args(argv)

    paths: list[str] = []
    for p in args.paths:
        paths.extend(sorted(glob.glob(p)) or [p])

    total_h = total_rej = total_events = total_fault = 0.0, 0, 0, 0
    total_h, total_rej, total_events, total_fault = 0.0, 0, 0, 0
    print(f"{'log':<42} {'ch':<11} {'hours':>7} {'dt':>5} {'rej':>6} {'/day':>7} {'events':>7}")
    print("-" * 92)

    for path in paths:
        log = load(path)
        channel = args.channel or (
            DEFAULT_CONTROL_CHANNEL if log.model == "218" else log.populated_channels[0]
        )
        if channel not in log.channels:
            print(f"{log.name:<42} -- no channel {channel!r}, skipped")
            continue
        r = replay(log, channel=channel)
        total_h += r.hours
        total_rej += r.n_rejected
        total_events += len(r.events)
        total_fault += r.reached_fault
        print(f"{r.log:<42} {r.channel:<11} {r.hours:7.1f} {r.cadence_s:5.1f} "
              f"{r.n_rejected:6d} {r.rejects_per_day:7.2f} {len(r.events):7d}")
        for run in r.events if args.verbose else []:
            print(f"      {run.start_t:9.0f}s  {run.n:4d} samples  {run.duration_s:6.0f}s  "
                  f"{run.validity:<12} range=[{run.k_min:.2f},{run.k_max:.2f}]K "
                  f"true~{run.true_k:.2f}K -> {run.reached}")

    print("-" * 92)
    print(f"TOTAL {total_h:.0f} h ({total_h/24:.1f} days): {total_rej} rejected samples, "
          f"{total_events} multi-sample events, {total_fault} samples in FAULT")
    if total_h:
        print(f"      false-positive budget: {total_rej/(total_h/24):.2f} rejections/day")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
