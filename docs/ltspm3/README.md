# LTSPM3: the software PID

**Everything in this directory is specific to one cryostat.** Every number is
calibrated to Jeff's LTSPM3 cryostat and does not transfer. If you are running a
Lake Shore box on any other system, you want
[the chart recorder docs](../recorder/) and nothing here.

## What it is, and why it exists at all

The LTSPM3 sample heater hangs off the **218's analog output**, and a 218 has
no heater loop of its own. There is no firmware PID to command. So this
software *is* the loop.

That is the whole reason `ltspm3` exists as a separate package. Every other cryostat
in this project drives the instrument's *own* PID by setpoint, which is a much
smaller and much safer thing to do.

```
lschart   generic recorder     drives the INSTRUMENT'S loop by setpoint
ltspm3     LTSPM3 only          IS the loop, on the 218's analog output
```

`ltspm3` imports `lschart`. Nothing in `lschart` may import `ltspm3`.

## Read in this order

1. **[cryostat.md](cryostat.md)** — the hardware, the addresses, what is wired to what,
   and which box must not be touched.
2. **[safety.md](safety.md)** — the eight design rules and the sensor glitch
   that shaped them. **Read this before running anything armed.**
3. **[thermal-response.md](thermal-response.md)** — what the cryostat actually does, measured over
   1,510 hours of logs.
4. **[control.md](control.md)** — how the loop is built out of those numbers.
5. **[running.md](running.md)** — `check`, `run --arm`, replay, step test.

## Status

`ltspm3` is **complete, tested and parked**. The stated priority is the recorder,
the viewer and the MATLAB interface; resist "while I am in here" improvements to
`control/`.

Two things are worth knowing before it runs against the real cryostat:

- **Nothing on this cryostat has been talked to yet.** Every number in these
  documents comes from the reference logs. The GPIB path has never been
  exercised against hardware.
- **`verify_readback` on the 218 may be confirming a stale value.** See
  [running.md](running.md#before-the-first-armed-run) — this is the highest-value
  parked item.
