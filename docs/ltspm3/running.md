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
call (rule 7), and it is deliberately two acts:

```bash
python -m ltspm3 -c config.yaml send ack      # clear the latch. Loop stays OFF
python -m ltspm3 -c config.yaml send arm      # close it again
```

`ack` clears the latch and stops there — it disarms the loop rather than
resuming it, because the latch exists to make somebody look at the cryostat,
and a recovery that was one keystroke would not. Re-arming is the separate,
deliberate act that re-primes the PID and the filter from current conditions.

`ack` is **not** a panic command: it is the first step back toward driving the
heater, so it passes `ipc.allow_analog_output` and the source policy exactly as
`arm` does. `acknowledge()` in process does the same thing; until `send ack`
existed it was the *only* way, which meant a locked-out recorder could only be
recovered by restarting it — and with `on_exit: hold` that is precisely what
you do not want to do to a live cryostat.

Nothing raises the heater in response to a fault, ever (rule 1). The only fault
responses are freeze and slow ramp-down.

## Stopping the loop deliberately, from a file

Distinct from a fault: this is an operator asking, not the supervisor deciding.

```bash
python -m ltspm3 -c config.yaml send hold          # loop OPEN, heater frozen
python -m ltspm3 -c config.yaml send heaters_off   # loop DISARMED, heater to 0
python -m ltspm3 -c config.yaml send arm           # closed again, holding here
```

**Both of these disengage the loop.** A person reaching for either has decided
the loop should stop deciding, and the software does not get to override that.
`hold` reaches `HeaterSupervisor.panic_hold()` — `abort_ramp()` plus
`set_mode(OFF)` — and `OFF` writes nothing at all, ever. The heater keeps
exactly the value it had. The state reads `idle` / `off`.

**It used to switch to `MANUAL`, and manual was not a hold.** A manual output is
still clamped to the authority band and still rate limited, so a hold taken
while the heater sat outside that band moved it on the very next cycle. Told to
freeze at 20 % it reported `holding 20.000%` and wrote **62.080 %**; told to
freeze at 68 % it wrote **64.070 %**. It only ever really held when the heater
happened already to be inside the band, and either way the number in the reply
was one it was about to leave. A freeze that freezes only sometimes is worse
than none, because it will be believed.

That is a hold of a **power**, not of a temperature. Nothing regulates the
sample afterwards, so it drifts with the cryostat — the opposite of what `hold`
does to a 33x loop, which keeps regulating at the temperature it was at.

`arm` is the way back, and with no kelvin it arms to hold the temperature the
cryostat is at *now*. If it drifted while held, that error is real; the clamp
and rate limiter bound what the output may do about it.

### `heaters_off` also disarms, and differs only in what happens next

Both panic actions leave the loop in `OFF`. They differ in what becomes of the
heater afterwards, and therefore in what the loop may still claim to know:
`hold` leaves the output alone and goes on reporting it, while `heaters_off`
zeroes it and so stops reporting one at all — `output_pct` goes null, and the
218's own `aout1` carries the truth from there.

`lschart` calls `panic_off()` **before** it zeroes the 218 — nothing may be
driving that output at the moment the zero lands. A lockout survives either
action: stopping the heater is not the same as having looked at the cryostat,
and a panic button is pressed precisely when nobody has diagnosed anything yet.

`arm` is the way back from this one too, and it is the whole way back — there
is no latch to clear unless the loop had *also* faulted.

### The seams

Four methods, and they are the only ones `lschart` reaches into this package
by — called duck-typed by name from `lschart/app.py`, so `lschart` still never
imports `ltspm3` (invariant 1):

| | |
|---|---|
| `panic_hold()` | the `hold` command. Freeze the output, stop regulating |
| `panic_off()` | the `heaters_off` command. Let go of the output entirely |
| `arm()` | the `arm` command. Close the loop |
| `acknowledge()` | the `ack` command. Clear a fault lockout |

Any of these from a plain recorder finds no software loop and says so by name,
rather than quietly succeeding.

## Watching it on screen

The viewer is a separate process and holds no port, so it is safe to open
against a live armed recorder:

```bash
.venv/bin/python -m lschart.gui -c config.yaml
```

The software loop is the **last row of the loop table**, marked `sw`, beneath
whatever loops the 336 has. It carries the channel it controls, that channel's
temperature, the setpoint, the output percent, the gains in force, and the
supervisor's own state — `tracking`, `idle`, `holding`, `ramping down`,
`locked out`. The loop mode (`off` / `manual` / `pid`) is in the hover, because
`idle` alone cannot tell a loop that was never armed from one that was armed
and then held.

**The P and I on that row are not settings.** They are scheduled: the tuner
re-solves them from the measured gain and time constant at the present
temperature, so they move as the cryostat does. There is no D — this controller
takes its derivative from a regressed slope rather than from a gain, so the
column stays empty rather than showing a zero that would read as "tuned to
nothing".

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

### 2. Nothing on this cryostat has been talked to yet

Every number in these documents comes from the reference logs. Start with
`probe`, which forces every transport read-only regardless of the config:

```bash
python -m lschart -c config.yaml probe
```

### 3. A deliberate step test at two or three temperatures

The highest-value hardware measurement available. The current τ ≈ 620 s comes
from the *one* clean step response in the logs and is provisional.
`ltspm3/tools/steptest.py` holds the protocol.

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
