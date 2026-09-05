# The LTSPM3 cryostat

## Instruments

| Item | Detail |
|---|---|
| Lake Shore 336 | `GPIB0::12::INSTR` — 4 inputs: RAD SHIELD, THE CHONKE, 1st Stage, 2nd Stage |
| Lake Shore 218 | `GPIB0::15::INSTR` — 8 inputs, 3 populated: **1 Sample**, 2 Coldplate, 5 Magnet. Inputs 3, 4 and 6–8 are empty |
| Sample heater | 218 **analog output 1** → op-amp → heater. The 218 has no heater loop; this software *is* the loop |
| Poll cadence | **2.0 s** by config. `check` budgets 26 transactions at ~1.30 s per cycle, so 1 Hz does not fit without trimming that |

Addresses come from the legacy MATLAB in `reference/` (`DAQManager.m`), which is
kept for reference only and is not part of the build.

On cadence: the legacy logs vary 2–20 s, but that was the 65,536-row Excel
limit forcing slower polling on long runs, not a cryostat constraint.

> **The GPIB path is exercised; the software loop is not.** Since 2026-08-24 the
> recorder has run against both boxes over GPIB, and the 218's analog output has
> been moved by hand. What has *never* run on this cryostat is the closed loop.
> Numbers here that predate 08-24 still come from the reference logs — see
> [commissioning](commissioning.md) for which ones have since been measured.

## The 218's thermometers, and the seam at 2026-09-04 12:07

| Input | Name | Notes |
|---|---|---|
| 1 | Sample | what the software PID controls (`control_input: 1`) |
| 2 | Coldplate | **recalibrated 2026-09-04**; see below |
| 5 | Magnet | **was input 3** until 2026-09-04 |
| 3, 4, 6–8 | — | empty. Not listed in config, so no column and no query |

The map in the configs is deliberately **non-contiguous**, and nothing in the
code minds: the driver reads `sorted(channels)` rather than 1..N, and the CSV
column is the *name*, never the input number. That is why `Magnet` is one
continuous column across the move — same thermometer, same column, a different
socket. Moving a thermometer costs one line of config.

> The simulator did mind, once. `Sim218` answered `KRDG? 0` from a hardcoded
> three-element list, so a channel on input 5 came back `0.0000` — which is
> exactly what the real box reports for an empty input, and is graded
> `NO_SENSOR`. A fake that manufactures the one failure signature it exists to
> help you distinguish is worse than no fake. It now derives its populated
> inputs from the cryostat's own channel map.

### The Coldplate was reading high, and every old number carries it

A transposed digit in the Coldplate's calibration curve — a 6 where a 9
belonged — had the cold end of this cryostat reading high for as long as that
curve was loaded. It was corrected at the box on **2026-09-04, at the 12:07:16
cutover**.

**Every Coldplate figure timestamped before that cutover is wrong**, including
the dated blocks in the two LTSPM3 configs, the numbers in
[commissioning](commissioning.md), and all of `reference/logs/`. How wrong
depends on temperature — a bad curve point warps the curve locally, it is not a
constant offset — so *no correction is quoted anywhere in this repo, and none
should be guessed*. This is invariant 9 doing its job: where a measured number
contradicts memory the number wins, and right now there is no measured
correction to win with.

> **TODO** — reprocess the pre-cutover logs onto the corrected curve, so the
> archive and the live log can be read on one axis. Until then treat
> 2026-09-04 12:07 as a hard seam **in Coldplate and in nothing else**: Sample,
> the four 336 inputs, the heater output and both 336 loops are unaffected, and
> `Magnet` is comparable across it.

### It reaches the thermal fits, and may resolve a known anomaly

`analysis/` is the one place the Coldplate is more than a monitored channel:
it is `T_c` in `Λ(T_s) = Q + Λ(T_c)`, so every fit in that directory was
computed from pre-cutover values.

More interesting than the error is what it may explain. `analysis/README.md`
carries a standing caveat that **at zero power the sample settles 0.79 K
*below* the coldplate reading** — which is not physics, since a sample cannot
rest colder than its own heat sink. It was attributed to "thermometry plus
stray magnet-side load". A coldplate reading high is precisely that
thermometry, and the correction is of the same order as the anomaly.

That is a hypothesis, not a result — neither has been measured against the
other. It is written up, with the order to re-run things in, at the top of
[`analysis/README.md`](../../analysis/README.md). Nothing in `control/`
depends on any of it, so none of it is urgent.

Nothing derived from Coldplate feeds control — the sample loop does not read
it, so the seam has no bearing on control quality or safety. It is a monitored
channel and one term in the simulator's cross-channel coupling,
`LTSPM3_AUX_COUPLING["218.2"] = 0.0082` K/K. **That number was measured under
the bad curve** (on `cd8_..._sample_monitor7.xls`, where the sample fell 22.4 K
while input 2 fell 0.183 K) and is therefore suspect: a warped curve distorts
the 0.183 K as much as it distorts the absolute reading. It has been left
alone rather than adjusted, because adjusting it would mean inventing the
correction this document has just said not to invent. Re-derive it from
post-cutover data when there is some — it is simulator scenery and the
coherence logic only needs the coupling to be *non-zero and of the right
order*, so this is a fidelity item, not a correctness one.

### The log was broken at the seam on purpose

Because the column names did not change, a restart would have appended
post-cutover rows to `data/ltspm3-heater_2026-09-04.csv` and the viewer would
have drawn a straight line across a step that never physically happened. The
pre-cutover part of that day is therefore archived out of the recorder's
directory:

```
data/pre-recal-2026-09-04/ltspm3-heater_2026-09-04.csv
```

Open it on its own with `lschart-view --csv <path>`. The viewer will not splice
it into the live history — backfill only walks the directory the followed file
lives in — which is the whole point of the move.


## The 336 is read-only, and that matters

Loop 2 of the 336 independently holds **"THE CHONKE" at 290.6 K with heater 2
near 98%**. This software must not disturb it.

`allow_writes` defaults to `False` and every write raises `PermissionError`
unless it is explicitly enabled. On this cryostat, leave it that way.

## Actuating the heater

The only command that moves the heater, verified against the `Notes` column of
the reference logs:

```
ANALOG 1, 0, 2, 1, 1,1,1,<percent>      # out 1, unipolar, manual mode, kelvin
AOUT? 1                                 # readback, in percent
```

**Only the trailing value ever changes.** `AnalogOutputConfig` keeps the other
seven fields byte-identical to that known-good string rather than recomputing
them — a recomputed field that happens to differ would change the output's
*mode*, not just its level.

## The other two cryostats in this repo, for contrast

Neither uses any of this.

**The bench 336** — a spare from a third system, on USB, cryo off, at
atmosphere. The only instrument actually connected. Inputs A Coldplate,
B Stage 2, C Rad Shield, D Stage 1, all ~295–297 K. All four loops closed-loop,
loops 3/4 with `powerup_enable=1`, all setpoints 275 K — i.e. *below* ambient,
so every loop demands zero heat, and all ranges are 0. **Benign by value, not
by configuration.** `TLIMIT` 330 K on every input.
See [`examples/config-336-usb.yaml`](../../examples/config-336-usb.yaml).

**A coworker's 335** on COM10, heaters on its own outputs, so its firmware runs
the loop. Needs logging plus setpoint, and no software PID at all. Driven by
`driver: lakeshore`, which needs no VISA runtime.
See [`examples/config-335-usb.yaml`](../../examples/config-335-usb.yaml).
