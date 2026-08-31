# Running the LTSPM3 loop

`ltspm3` is a thin shim over the `lschart` CLI — it swaps what builds the
application and shares everything else, so the commands, flags and interlocks in
[../recorder/cli.md](../recorder/cli.md) all apply unchanged.

```bash
python -m ltspm3 -c config.yaml check
python -m ltspm3 -c config.yaml run                      # records; loop NOT closed
python -m ltspm3 -c config.yaml run --arm --setpoint 96.0
```

`python -m lschart` still works on the same config file and simply records: it
has no controller, so `--arm` is **refused rather than ignored**.

## Arming is never implicit

`run` records. `run --arm` closes the loop. That separation exists so a recorder
cannot start driving a heater because someone ran it with the wrong config file.

Arming waits for a usable measurement before it happens — up to 30 s — so the
PID and the filter are primed **bumplessly** from what the cryostat is doing now. If
no usable reading arrives, it logs an error and does **not** arm.

## Read these two lines of `check` before arming

```bash
python -m ltspm3 -c config.yaml check
```

```
control        : enabled
authority band : 58.076% .. 68.076%  (on_exit=hold)
```

The band is an unconditional cap on heat (rule 5). `on_exit=hold` means the
heater keeps its last value when the process stops — zeroing a sample heater on
a live cryostat is its own hazard (rule 6).

## After a fault

A completed fault ramp-down **locks out**. Recovery is always the operator's
call (rule 7): `acknowledge()` disarms the loop, and re-arming is a deliberate
act that re-primes the PID and the filter from current conditions.

Nothing raises the heater in response to a fault, ever (rule 1). The only fault
responses are freeze and slow ramp-down.

## Stopping the loop deliberately, from a file

Distinct from a fault: this is an operator asking, not the supervisor deciding.

```bash
python -m ltspm3 -c config.yaml send hold      # loop OPEN, heater frozen
python -m ltspm3 -c config.yaml send arm       # closed again, holding here
```

`hold` reaches `HeaterSupervisor.panic_hold()`, which is `abort_ramp()` plus
`set_mode(MANUAL)` under one name — the loop stops regulating and the heater is
left exactly where it was. **The clamp and the rate limiter still apply**;
manual mode is not raw access to the DAC, which is why this goes through the
supervisor rather than around it. The state reads `idle` / `manual`.

That is a hold of a **power**, not of a temperature. Nothing regulates the
sample afterwards, so it drifts with the cryostat — the opposite of what `hold`
does to a 33x loop, which keeps regulating at the temperature it was at.

`arm` is the way back, and with no kelvin it arms to hold the temperature the
cryostat is at *now*. If it drifted while held, that error is real; the clamp
and rate limiter bound what the output may do about it.

`panic_hold()` is **the one seam `lschart` reaches into this package by**,
called duck-typed by name from `lschart/app.py` — so `lschart` still never
imports `ltspm3` (invariant 1). The same command from a plain recorder finds no
software loop and says so.

## Watching it on screen

The viewer is a separate process and holds no port, so it is safe to open
against a live armed recorder:

```bash
.venv/bin/python -m lschart.gui -c config.yaml
```

The software loop is the **last row of the loop table**, marked `sw`, beneath
whatever loops the 336 has. It carries the channel it controls, that channel's
temperature, the setpoint, the output percent, and the supervisor's own state —
`tracking`, `idle`, `holding`, `ramping down`, `locked out`. The loop mode
(`off` / `manual` / `pid`) is in the hover, because `idle` alone cannot tell a
loop that was never armed from one that was armed and then held.

Two things about that row are specific to this cryostat and worth knowing
before you read the warning marks:

- **It rails against the authority band, not against 99 %.** The band is about
  a percent wide here, so the fixed rails a heater output is judged by could
  never light the mark. On the shipped numbers a *tracking* loop cannot rail
  at all — `max_error_k` is 1.0 K against roughly ±7 K of authority, so the
  anomaly hold fires first and what you see is `holding`.
- **When health goes bad both marks go quiet**, because the loop has stopped
  trying. The row is coloured red instead. An unhealthy loop is not a loop
  failing to reach a setpoint; it is a loop that has stopped chasing one.

The row is **read, not clicked**: the software loop takes no setpoint, range or
PID command, only the `hold` and `arm` above, so it is the one row that will
not select. Everything it shows comes from `status.json`, which any number of
readers may open — see
[file-interface](../recorder/file-interface.md#control--the-software-loop-where-there-is-one).

## Before the first armed run

Three things are outstanding, in priority order.

### 1. `verify_readback` on the 218 may be confirming a stale value

**The highest-value parked item.** Writes on a Lake Shore box are applied
asynchronously: a query issued too soon after a write overtakes it and answers
with the *previous* value. Measured on the 336 over USB — at 0 ms every readback
was stale, at 50 ms readbacks lagged by exactly one write, 80 ms+ was correct.
**Both wrong regimes look like success.**

This very likely applies to the 218 on GPIB too and is **unverified** there.
`SupervisorConfig.verify_readback` reads `AOUT?` after `ANALOG` and may
therefore be confirming a stale value. It passes in simulation only because the
fake applies writes synchronously.

**Check this before the LTSPM3 cryostat runs armed.**

### 2. The closed loop has never run on this cryostat

The GPIB path and the write path both have, since 2026-08-24 — but by hand, with
no controller in the way. Arming is the step nothing has rehearsed on this
hardware. [commissioning.md](commissioning.md) is the staged way in; start with
`probe`, which forces every transport read-only regardless of the config:

```bash
python -m lschart -c config.yaml probe
```

### 3. A deliberate step test at two or three temperatures

Still the highest-value hardware measurement available, but no longer from
scratch. The live data now gives **τ = 709 s at R² = 0.9973** (the +0.500% step
of 2026-08-24) and **K ≈ 13.8 K/%** across seven settled points at 66.2–66.6%.
The first confirms the provisional τ ≈ 620 s; the second is a genuinely new
number, and much steeper than the 10.0 K/% quoted at the 63% operating point.

What is missing is *other temperatures* — everything on disk sits between 143 K
and 149 K, which is one schedule point rather than a schedule.
`ltspm3/tools/steptest.py` holds the protocol; see
[commissioning.md](commissioning.md) for the two rules the existing hand data
teaches about step size and doublets.

## Replay: the only test on genuine data

```bash
python -m ltspm3.tools.replay "reference/logs/CD*/*.xls"
```

Runs the real pipeline over 63 days of historical logs. Currently **12.8
rejections/day and 0 samples ever reaching FAULT**. It found the
stale-slew-reference bug that no simulated fault would have.

`reference/logs` is ~110 MB and deliberately not gitignored.

## Tests

```bash
.venv/bin/python -m pytest -q                 # everything
.venv/bin/python -m pytest -q tests_ltspm3     # the control half
```

`tests_ltspm3/conftest.py` carries the virtual-clock harness: time is injected,
so a 12-hour fault escalation is tested in milliseconds. `tests/` is generic and
must stay that way.

## Parked, but do not lose

- **Sweep scheduler.** `sweep_to()` exists and is tested; a *sequence* of
  setpoints with dwell times does not. The file interface now makes this
  reasonable to write **in MATLAB** instead, which may be the better home for
  it: it is an experiment protocol, not a safety mechanism.
- **Is the noise model right?** The bench 336 reads 0.44–3.03 mK rms at ~296 K
  where [thermal-response.md](thermal-response.md) claims 109 mK at 290 K for the 218 sample channel.
  Three things differ at once, so neither number is wrong yet. The clean
  resolution is to record the 218 under the same quiet conditions.
- **Does `read_status: true` earn its cost?**
