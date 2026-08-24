# lschart — Lake Shore 218/336 chart recorder + software PID

Replacement for the Lake Shore chart-recorder software on Jeff's LTSPM cryostat.
Two jobs: **record every thermometer continuously**, and **hold the sample
temperature to a few millikelvin** by software PID on the 218's analog output.

## The rig

| Item | Detail |
|---|---|
| Lake Shore 336 | `GPIB0::12::INSTR` — 4 inputs: RAD SHIELD, THE CHONKE, 1st Stage, 2nd Stage |
| Lake Shore 218 | `GPIB0::15::INSTR` — 8 inputs, 3 populated; input 1 is the **sample** |
| Sample heater | 218 **analog output 1** → op-amp → heater. The 218 has no heater loop; this software *is* the loop. |
| Poll cadence | **1 Hz** by config. The legacy logs vary 2–20 s, but that was the 65,536-row Excel limit forcing slower polling on long runs, not a rig constraint. |

Addresses come from the legacy MATLAB in `reference/` (`DAQManager.m`), which is
kept for reference only and is not part of the build.

### The 336 is read-only

Loop 2 of the 336 independently holds "THE CHONKE" at 290.6 K with heater 2 near
98%. This software must not disturb it. `LS336.allow_writes` defaults to `False`
and `set_setpoint` raises `PermissionError` unless it is explicitly enabled.

### Actuating the heater

The only command that moves the heater, verified against the `Notes` column of
the reference logs:

```
ANALOG 1, 0, 2, 1, 1,1,1,<percent>      # out 1, unipolar, manual mode, kelvin
AOUT? 1                                 # readback, in percent
```

Only the trailing value ever changes. `AnalogOutputConfig` keeps the other seven
fields byte-identical to that known-good string rather than recomputing them.

## Measured plant behaviour

Everything below was extracted from `reference/logs/CD8,CD9,CD10/*.xls` —
**24 files, 1,510 h (63 days), ~1.1 M samples**. These numbers drive every
default in `control/`.

> An earlier version of this file was calibrated on two files only, and was
> wrong in ways the wider set exposed. Where a number here contradicts memory,
> the number won: re-derive from the logs, don't trust the prose.

| Property | Value |
|---|---|
| Sensor noise, sample channel | **quadratic in T**: `rms ~= 1.36e-6 * T**2 K`, floored ~1.8 mK. Measured 1.8 mK @ 18 K, 13.6 mK @ 96 K, 45 mK @ 190 K, **109 mK @ 290 K**. A linear fit from 96 K understates room temperature by ~4x. |
| Fast thermal time constant | ~5–10 min |
| Slow thermal tail | hours (3–12 h; poorly constrained) |
| Steady state | 43% → 18.2 K; 63.076% → ~100 K. Fits `T = T_bath + A·pct^5`. |
| **Local gain at the 63% operating point** | **~7.6 K/%** |
| Largest *legitimate* one-sample ΔT | **6.5 K** (−1.63 K/s, `cd8_…_monitor7`, corroborated on all three inputs); ~2.97 K/s just after a heater cut |
| Normal-operation ΔT, p99 | 0.26 K |
| Practical stability floor | ~2.5–4 mK near 96 K; **~100 mK near 290 K**. Millikelvin control is a low-temperature capability, not a global one. |
| Sensor noise character | **correlated, not white** — lag-1 autocorrelation **+0.51**. Allan deviation 6.1 mK @ 4 s, 4.1 mK @ 60 s, 2.5 mK @ 600 s, i.e. ~2× worse than 1/√N. The measurement, not the DAC, is what limits mK stability. |

### The consequence that shapes the whole design

At ~7.6 K/%, one 0.01% DAC code is **~76 mK** — about eight times the sensor
noise floor and far coarser than the few-mK goal. Rounding to the nearest code
would make millikelvin control impossible regardless of PID tuning. So the
output is **sigma-delta dithered** (`control/dither.py`): the rounding error is
carried forward so the *sequence* of codes averages to the request, and the
plant's ~360 s pole low-passes the dither to sub-mK ripple.

### The sensor glitch — the real failure mode

**It is not a dropout to 0 K.** Searching for zeros finds nothing (true across
all 24 logs) because the fault has a completely different shape. Searching for
*single-channel physically-impossible rates* finds **9 events in 1,510 h**,
about one per 7 days:

| Property | Value |
|---|---|
| Channel | **Input 1 only** — never input 2/3, never any 336 channel |
| Shape | Scatters in *both* directions, e.g. 297 → 151 → 292 → 92 → 175 K |
| Range | 11 K to 298 K observed. **Never 0 K**, never below 11 K |
| Duration | 2 s to 280 s, then resumes exactly on the pre-glitch trend |
| When | Mostly during cooling/warmup; one during a steady hold at 18.5 K |

Consequences that shape `control/health.py`:

1. `valid_min_k` and any zero-check are **useless** against this.
2. A single slew threshold cannot work. Loose enough to pass the real 1.63 K/s
   cooldown also passes half the glitch; tight enough to catch the glitch
   rejects genuine cooldowns. Hence the two-tier limit plus corroboration.
3. **The discriminator is smoothness and corroboration**, not magnitude. A real
   thermal signal is a smooth function of time and moves every channel; the
   glitch reverses direction each sample on one channel alone. See
   `control/coherence.py` and `SensorGuardConfig.curvature_ratio`.
4. `fault_after_s` is **600 s**, not 60 s. The longest observed event healed
   itself in 280 s; escalating at 60 s converts a five-minute sensor burp into
   a ramp-down and a lost cooldown.

`tools/replay.py` measures all of this against the real logs: currently
**12.8 rejections/day and 0 samples ever reaching FAULT** across 63 days.

## Design rules

**Availability of the cryostat outranks control quality.** Jeff's stated
priority is "I don't want to add in big risk of massive failure". Every
ambiguous case resolves to *hold the output and raise an alarm*, never to
correct aggressively.

1. **Nothing raises the heater in response to a fault.** Ever. The only
   fault responses are freeze and slow ramp-down.
2. **The PID proposes; the supervisor disposes.** `HeaterSupervisor` owns the
   output. Nothing else may write to the analog output.
3. **A single doubtful reading freezes the output.** Escalation to a ramp-down
   takes 60 s of sustained failure by default.
4. **Premise checks.** This loop is specified for mK trim. An error above
   `max_error_k`, or a PID demand that jumps by more than
   `anomaly_demand_pct`, means something is wrong *with the rig*, not with the
   control — so hold, and ramp down only if it persists.
5. **The authority band caps heat unconditionally.** Output can never exceed
   `operating_point + authority_pct`. It may go *below* the band, but only as a
   fault ramp-down — the one direction where leaving the band is the safe one.
6. **On exit, hold.** Zeroing a sample heater on a live cryostat is its own
   hazard. `on_exit: hold` is the default; `zero` is opt-in.
7. **Recovery is always the operator's call.** A completed fault ramp-down
   locks out; `acknowledge()` disarms the loop, and re-arming is a deliberate
   act that re-primes the PID and the filter from what the rig is doing *now*.
8. **Move the setpoint by ramping it, never by stepping it.** A step of more
   than `max_error_k` is indistinguishable from a broken premise, so it stalls
   the loop rather than moving it. Sweeps and post-fault approaches both go
   through `control/ramp.py`; the premise check is widened only by the lag the
   ramp itself commands (`rate × plant_lag_s`), decaying once it stops.

## Layout

```
lschart/
  model.py           Reading / Frame / Validity / ReadingStatus. Immutable; crosses threads.
  transport.py       Transport ABC + VisaTransport + LoopbackTransport.
                     Each link is serialised by an RLock and paced.
  instruments/
    base.py          Instrument ABC, Lake Shore number parsing, RDGST? decoding.
    ls218.py         8 inputs + the heater actuator (AnalogOutputConfig).
    ls336.py         4 inputs + setpoints/heaters. Read-only by default.
    sim.py           Two-pole plant calibrated to the reference logs, plus
                     Sim218/Sim336 and injectable faults. No hardware exists yet,
                     so this is the primary development target.
  config.py          AppConfig + YAML loading. Unknown keys are an error.
  app.py             Wires config -> transports -> instruments -> poller.
  __main__.py        CLI: run / check / init.
  control/
    filters.py       MedianFilter, ExponentialFilter, SlopeEstimator,
                     MeasurementFilter (test-then-commit, staleness-aware).
    health.py        SensorGuard: validity gate + OK/SUSPECT/FAULT/RECOVERING.
    coherence.py     Cross-channel corroboration. Read with health.py.
    pid.py           PID: derivative on a regressed slope, integral clamped in
                     output units, bumpless prime(), feedforward-aware.
    feedforward.py   Steady-state output for a temperature, from the log fit.
    ramp.py          SetpointRamp — sweeps and post-fault approaches.
    dither.py        SigmaDeltaDither for sub-code resolution.
    supervisor.py    The safety envelope. Read this first.
  acquisition/
    poller.py        The acquisition thread; owns the cycle.
    recorder.py      Continuous CSV, no row limit, flushed every sample.
    ringbuffer.py    Bounded, for plotting only — never the log.
  tools/
    import_xls.py    Reader for the legacy .xls logs. Sniffs the header:
                     filenames lie (cd10_..._st2_monitor3.xls is a 218 log).
    replay.py        Runs the real pipeline over historical logs. The only
                     test that uses genuine data; it found the stale-slew-
                     reference bug that no simulated fault would have.
reference/           Legacy MATLAB + the two .xls chart-recorder logs. Not built.
tests/               conftest.py provides a virtual-clock closed-loop Harness.
```

## Conventions

- **Never hardcode a limit in `control/`.** It belongs in `SupervisorConfig`,
  `SensorGuardConfig` or `PIDConfig` so it is visible and auditable in one place.
- **Backends are config-driven.** There is no hardware on the bench and there
  will not be for a while; going live must be a config edit, not a code change.
- Units are in the name: `_k` kelvin, `_pct` output percent, `_s` seconds.
- Time is `time.monotonic()` for every interval calculation and `time.time()`
  only for the log's absolute clock. Tests inject a `VirtualClock`.
- A per-channel failure marks that channel's `Reading`; only a link-level
  failure may raise.
- Filters are **dt-aware** (`alpha = 1 - exp(-dt/tau)`), never fixed-alpha —
  the bus jitters and a retry can cost a cycle.

## Running

```bash
uv venv --allow-existing .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q

.venv/bin/python -m lschart -c config.yaml check      # validate, touch nothing
.venv/bin/python -m lschart -c config.yaml run        # record (simulated)
.venv/bin/python -m lschart -m lschart.tools.replay "reference/logs/CD*/*.xls"
```

**Going live is the two `backend:` lines in `config.yaml`** (`sim` -> `visa`).
No code changes.

Development is on macOS; deployment is **Windows**, which additionally needs the
NI-VISA runtime for `pyvisa` to see `GPIB0::`.
