# lschart / ltspm — Lake Shore chart recorder + LTSPM software PID

**Two packages, one repo.  The dependency runs one way.**

| | |
|---|---|
| `lschart` | Generic Lake Shore recorder. Any rig. Records every thermometer continuously and drives the *instrument's own* PID loop by setpoint. This is what a coworker installs. |
| `ltspm` | The LTSPM3 cryostat's **software** PID on the 218's analog output. Calibrated to one rig. Imports `lschart`; nothing in `lschart` may import it. |

## Priorities (Jeff, 2026-08-24)

**The GUI and the MATLAB interface are the priority. The software PID is not.**

`ltspm` is complete and tested and should be left alone unless it breaks. New
effort goes to `lschart`: the strip-chart viewer, the MATLAB file interface,
and Windows deployment. Read that as a standing instruction, not a phase
ordering — resist "while I am in here" improvements to `control/`.

## The rigs

### LTSPM3 (Jeff's) — the software-PID target

| Item | Detail |
|---|---|
| Lake Shore 336 | `GPIB0::12::INSTR` — 4 inputs: RAD SHIELD, THE CHONKE, 1st Stage, 2nd Stage |
| Lake Shore 218 | `GPIB0::15::INSTR` — 8 inputs, 3 populated; input 1 is the **sample** |
| Sample heater | 218 **analog output 1** → op-amp → heater. The 218 has no heater loop; this software *is* the loop. |
| Poll cadence | **1 Hz** by config. The legacy logs vary 2–20 s, but that was the 65,536-row Excel limit forcing slower polling on long runs, not a rig constraint. |

Addresses come from the legacy MATLAB in `reference/` (`DAQManager.m`), which is
kept for reference only and is not part of the build.

**Nothing on this rig has been talked to yet.** Every LTSPM number below comes
from the reference logs; the GPIB path has never been exercised against
hardware.

### The bench 336 — the only instrument actually connected

A spare 336 from a third system, on USB. Cryo off, at atmosphere.

| Item | Detail |
|---|---|
| Connection | USB → Silicon Labs **CP210x** bridge, VID `0x1FB9` PID `0x0301`, serial **LSA26E0**, firmware 3.1 |
| macOS | Needs the **Silicon Labs CP210x VCP driver**. macOS's built-in support matches SiLabs' own VID `0x10C4`; Lake Shore ships `0x1FB9`, so without the driver the device enumerates but no `/dev/cu.*` appears. Not an instrument setting — no amount of front-panel configuration helps. |
| Inputs | A Coldplate, B Stage 2, C Rad Shield, D Stage 1 — all ~295–297 K |
| State | All four loops **closed-loop**; loops 3/4 have `powerup_enable=1`. All setpoints 275 K, i.e. *below* ambient, so every loop demands zero heat. All ranges 0. Benign **by value, not by configuration**. |
| `TLIMIT` | 330 K on every input |

### A coworker's 335

A 335 on **COM10**, heaters on its own outputs, so its firmware runs the loop.
Needs logging plus setpoint — and no software PID at all. Driven by
`driver: lakeshore`, which needs **no VISA runtime**. See
`examples/config-335-usb.yaml`.

### The LTSPM 336 is read-only

Loop 2 of the 336 independently holds "THE CHONKE" at 290.6 K with heater 2 near
98%. This software must not disturb it. `allow_writes` defaults to `False`
and every write raises `PermissionError` unless it is explicitly enabled.

## Talking to a Lake Shore box: four things that will bite

All four are measured, not inferred. All four cost real time to find.

1. **Writes are applied asynchronously.** A query issued too soon after a write
   overtakes it and answers with the *previous* value. Measured on the 336 over
   USB: at 0 ms every readback was stale; at 50 ms readbacks lagged by exactly
   one write; 80 ms+ was correct. Both wrong regimes *look like success*.
   Hence `Transport.write_settle_s` (100 ms) **and** readback verification in
   `LS33x` — the delay makes it unlikely, the verification makes it detectable,
   and only the second is something to stake a cryostat on.
   **This very likely applies to the 218 on GPIB too, and is unverified there.**
   `SupervisorConfig.verify_readback` reads `AOUT?` after `ANALOG` and may
   therefore be confirming a stale value. It passes in simulation because the
   fake applies writes synchronously. **Check this before the LTSPM rig runs.**
2. **The vendor classes disagree about `baud_rate`.** `Model335.__init__`
   requires it as its first positional argument; `Model336.__init__` does not
   accept it at all. Every class also declares `**kwargs`, so a wrong argument
   is not rejected — it is forwarded to the parent and collides there.
   `LakeshoreTransport` filters against each model's real signature.
3. **The vendor driver logs every transaction at INFO**, two lines per query —
   1,114 lines in 60 s at 1 Hz, about 1.6 M lines a day. Quietened to WARNING
   unless the root logger is at DEBUG.
4. **A setpoint does nothing while the heater range is 0.** Raising the range is
   what applies power, so no method raises one as a side effect of anything.

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
| Actuator | The analog output is a **voltage** into a stable 50 Ω heater, so **`P ∝ pct²` exactly** and temperature-independently. |
| Thermal response | `T − T_bath = A·P^m`, **m ≈ 3.16** (lumped `pct^6.32`, R² = 0.9962) from **24 settled heater steps** in `cd10 monitor4/5`. |
| Steady state | 43% → 18.2 K; 63.076% → **99.60 K**; 66.95% → 151.05 K. |
| **Local gain at the 63% operating point** | **~10.0 K/%** |
| Time constant | **~620 s** @ 137 K — but from the *one* clean step response in the logs. Provisional. |
| Largest *legitimate* one-sample ΔT | **6.5 K** (−1.63 K/s, `cd8_…_monitor7`, corroborated on all three inputs); ~2.97 K/s just after a heater cut |
| Normal-operation ΔT, p99 | 0.26 K |
| Practical stability floor | ~2.5–4 mK near 96 K; **~100 mK near 290 K**. Millikelvin control is a low-temperature capability, not a global one. |
| Sensor noise character | **correlated, not white** — lag-1 autocorrelation **+0.51**. Allan deviation 6.1 mK @ 4 s, 4.1 mK @ 60 s, 2.5 mK @ 600 s, i.e. ~2× worse than 1/√N. The measurement, not the DAC, is what limits mK stability. |

### The consequence that shapes the whole design

At ~10.0 K/%, one 0.01% DAC code is **~100 mK** — roughly forty times the
sensor noise floor at 96 K and far coarser than the few-mK goal. Rounding to
the nearest code would make millikelvin control impossible regardless of PID
tuning. So the output is **sigma-delta dithered** (`control/dither.py`): the
rounding error is carried forward so the *sequence* of codes averages to the
request, and the plant's ~620 s pole low-passes the dither to sub-mK ripple.

One subtlety the quadratic actuator introduces: the dither averages *voltage*,
but the plant responds to *power*, and `⟨V²⟩ = ⟨V⟩² + Var(V)`. So the mean
power delivered sits slightly **above** the power at the mean voltage. Measured
at the operating point that bias is **~2 μK** — three orders below the noise
floor, so it is ignorable, but it is a real systematic and is tested for
(`tests/test_plant.py`) so nobody has to rediscover it while chasing an offset.

### Why the model is in two stages

`lschart/plant.py` deliberately keeps `P(pct)` and `T(P)` apart, and both the
simulator and the feedforward import that one curve so they cannot drift.
Lumping them into a single `T ∝ pct^n` fit — the previous model, n = 5 from two
points — hid the fact that only one factor is uncertain, and invited re-fitting
the exponent to absorb error belonging to the fixed quadratic.

**No single exponent spans the range.** The local lumped exponent runs from
~5.0 near 43% to ~7.8 near 64%, which is what changing conductances imply.
Extrapolating the high-temperature fit down to 43% predicts 12.8 K where 18.2 K
was measured. So measured points are interpolated (log-log) where they exist,
and the power law only extrapolates beyond them.

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

`lschart` is generic and must stay that way. The one-way dependency is the
whole point of the split: if you find yourself wanting `lschart` to import
`ltspm`, the design is wrong, not the rule.

```
lschart/                    GENERIC -- any Lake Shore rig
  model.py           Reading / Frame / Validity / ReadingStatus. Immutable; crosses threads.
  transport.py       Transport ABC: serialised by an RLock, paced, and
                     RECONNECTING -- opening is lazy, a single failure does not
                     condemn a link, retries back off 1->30 s.  Plus
                     VisaTransport (GPIB), LakeshoreTransport (the vendor
                     driver: USB/serial + TCP, no VISA) and LoopbackTransport.
                     `read_only` is a hard interlock at the byte level.
  instruments/
    base.py          Instrument ABC, Lake Shore number parsing, RDGST? decoding.
    ls218.py         8 inputs + the heater actuator (AnalogOutputConfig).
    ls33x.py         335/336 in one driver, a capability table per model.
                     Every write is confirmed by readback.  Read-only default.
    sim.py           Rig-agnostic fakes (Sim218/Sim33x) + FirstOrderPlant, a
                     deliberately boring one-pole default.  The calibrated
                     LTSPM plant is injected from ltspm/, not built in here.
  config.py          AppConfig + YAML. Unknown keys are an error. `instruments:`
                     is a list; the class is chosen from `model:`.
                     `register_section()` lets ltspm add `control:`.
  ipc/
    lock.py          OS-level single-instance lock. A COM port has exactly one
                     holder; two processes on one GPIB board garble replies.
  app.py             Wires config -> transports -> instruments -> poller.
                     `controller_factory` / `plant_factory` are the ltspm seams.
  __main__.py        CLI: run / probe / set / check / init.
  acquisition/       poller (owns the cycle), recorder (CSV, no row limit,
                     flushed per sample), ringbuffer (plotting only).
  tools/import_xls.py  Reads the legacy .xls logs. Sniffs the header:
                     filenames lie (cd10_..._st2_monitor3.xls is a 218 log).

ltspm/                      LTSPM3 ONLY -- imports lschart, never the reverse
  plant.py           The one measured P(pct)/T(P) curve. Shared by the
                     simulator and the feedforward so they cannot drift.
  sim_plant.py       Two-pole calibrated model + measured cross-channel coupling.
  config.py          The `control:` section; registers itself on import.
  app.py             build() -- the only module that knows both halves.
  __main__.py        Swaps one BUILDER; everything else is shared with lschart.
  control/
    supervisor.py    The safety envelope. Read this first.
    health.py        SensorGuard: validity gate + OK/SUSPECT/FAULT/RECOVERING.
    coherence.py     Cross-channel corroboration. Read with health.py.
    pid.py           Derivative on a regressed slope, integral clamped in output
                     units, bumpless prime(), feedforward-aware.
    tuning.py        IMC gain scheduling from measured K and tau.
    feedforward.py   Steady-state output for a temperature.
    ramp.py          SetpointRamp + SetpointSmoother.
    filters.py       Median/exponential/slope, staleness-aware.
    dither.py        SigmaDeltaDither for sub-code resolution.
  tools/
    replay.py        Runs the real pipeline over historical logs. The only test
                     on genuine data; it found the stale-slew-reference bug
                     that no simulated fault would have.
    steptest.py      The protocol for measuring K and tau on real hardware.

examples/            config-335-usb.yaml (coworker), config-336-usb.yaml (bench)
reference/           Legacy MATLAB + 24 .xls chart-recorder logs. Not built.
tests/               Generic. tests_ltspm/ has the virtual-clock control harness.
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
uv pip install --python .venv/bin/python -e ".[dev,serial]"
.venv/bin/python -m pytest -q                          # 194 tests

# generic recorder -- any rig, no control section in the config
.venv/bin/python -m lschart -c examples/config-336-usb.yaml probe   # read all, write nothing
.venv/bin/python -m lschart -c examples/config-336-usb.yaml run
.venv/bin/python -m lschart -c CONFIG set --loop 1 --setpoint 77     # instrument's own loop

# LTSPM3, software PID.  Same config file; `lschart` REFUSES it and says why.
.venv/bin/python -m ltspm -c config.yaml check
.venv/bin/python -m ltspm -c config.yaml run --arm
.venv/bin/python -m ltspm.tools.replay "reference/logs/CD*/*.xls"
```

`probe` is the first thing to run against unfamiliar hardware: it forces every
transport read-only *regardless of the config*, so its safety does not depend on
the config file being right.

**Going live is the `driver:` lines in `config.yaml`** (`sim` -> `visa` for the
LTSPM GPIB boxes, or `lakeshore` for anything on USB/serial). No code changes.

Development is on macOS; deployment is **Windows**, which additionally needs the
NI-VISA runtime for `pyvisa` to see `GPIB0::`.
