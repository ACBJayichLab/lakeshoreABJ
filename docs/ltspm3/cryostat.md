# The LTSPM3 cryostat

## Instruments

| Item | Detail |
|---|---|
| Lake Shore 336 | `GPIB0::12::INSTR` — 4 inputs: RAD SHIELD, THE CHONKE, 1st Stage, 2nd Stage |
| Lake Shore 218 | `GPIB0::15::INSTR` — 8 inputs, 3 populated; **input 1 is the sample** |
| Sample heater | 218 **analog output 1** → op-amp → heater. The 218 has no heater loop; this software *is* the loop |
| Poll cadence | **1 Hz** by config |

Addresses come from the legacy MATLAB in `reference/` (`DAQManager.m`), which is
kept for reference only and is not part of the build.

On cadence: the legacy logs vary 2–20 s, but that was the 65,536-row Excel
limit forcing slower polling on long runs, not a cryostat constraint.

> **Nothing on this cryostat has been talked to yet.** Every number in these
> documents comes from the reference logs; the GPIB path has never been
> exercised against hardware.

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
