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
authority band : +/-<half>% AROUND THE SETPOINT -- the model's output for whatever it is chasing ...  (on_exit=hold)
```

**`check` prints the band the shipped config will actually arm on. Read it
there, never off a document.** The band is *not* a fixed pair of numbers: it is
centred on the model's output for the current setpoint and follows the setpoint
as it moves ([requirements.md](requirements.md)), with `authority_pct` as the
half-width. A band quoted in prose was wrong by five-fold once already, and a
stale band is not a cosmetic error — it decides whether the output you are
sitting on is one the loop may keep.

The band is a cap on heat (rule 5) and it is **two-sided**. The ceiling is hard
and immediate; the floor bounds what the PID may ask for. Both matter before
arming: an output *above* the ceiling is cut to it on the first cycle, which is
a commanded step down of however far above it sat.

`on_exit=hold` means the heater keeps its last value when the process stops —
zeroing a sample heater on a live cryostat is its own hazard (rule 6).

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
python -m ltspm3 -c config.yaml send arm           # closed again, holds here
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

**Arming an armed loop is refused**, from the spool as well as from the
viewer's button. `set_mode` no-ops when the mode is already `pid` but the
setpoint change above it does not, so it was "step the setpoint now, no ramp"
and a dumped trajectory. Rule 8 bounded the step, so it was a surprise rather
than a hazard — and a surprise behind a word that says something else is its
own kind of unsafe. **`send setpoint <K> --software` is how an armed loop's
setpoint moves** (since 2026-09-17); `hold` then `arm` is the pair for
*resuming* a loop, and is bumpless.

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
supervisor's own state — `tracking`, `idle`, `frozen`, `ramping down`,
`locked out`, `crashed`. (`frozen` was `holding` until 2026-09-14; it collided
with the `hold` phase and the `hold` command, which are different things.) The loop mode (`off` / `manual` / `pid`) is in the hover, because
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
  at all while the setpoint is steady. A *sweeping* loop rails routinely and
  legitimately — that is what the velocity feedforward produces — which is why
  `fault_error_k` only applies while the setpoint is not moving.
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

### 1. `verify_readback` on the 218 — what it can and cannot tell you

Writes on a Lake Shore box are applied asynchronously: a query issued too soon
after a write overtakes it and answers with the *previous* value. Measured on
the 336 over USB — at 0 ms every readback was stale, at 50 ms readbacks lagged
by exactly one write, 80 ms+ was correct. It is **unverified on the 218 over
GPIB**.

What that is worth is narrower than it was once written, and the narrowing is
the useful part. `write_settle_s` is the transport's pacing gap between a write
and the next transaction on that link — `_pace()` applies it only when the last
transaction was a write — so a write and its confirming readback sit back to
back inside one cycle. Nothing is issued at sub-cycle intervals.

**A stale readback announces itself whenever the step is big enough.** The
comparison is against the value *just commanded*, so a stale reply returns the
old value, misses by the whole step and is caught. It is only when the step is
smaller than the tolerance that stale and fresh are indistinguishable —
and no settle time fixes that, because the two values are the same number.

Two tolerances exist and they are different knobs: the driver's
`readback_tol_pct` (the 218's own write check, `verify_writes`, off in the
armed file) and the supervisor's `verify_tol_pct` (the armed loop's readback
check, `verify_readback`, on). The arithmetic below is the supervisor's.

Which means, on this cryostat:

- **at a hold the check is vacuous by design.** `dither: true` moves one 0.01 %
  code at a time, deliberately below `verify_tol_pct`. Worst case is one
  code;
- **at 118 K it is vacuous while ramping too.** At the local gain there
  ([thermal-response.md](thermal-response.md)) a full 5 K/min ramp is
  0.36 %/min, or **0.012 % per 2 s cycle — under `verify_tol_pct`**;
- **at the cold end it bites.** The gain falls by more than an order of
  magnitude, the same rate is ~0.48 % per cycle, and there the readback is
  doing real work.

So `verify_readback` will be silent through the whole of stage 4a, and it will
be silent because of the arithmetic rather than because it is passing. Knowing
that is the point. The check that 100 ms is enough is three `send analog` steps
above the tolerance with the recorder running —
[plans/pid-4-commissioning.md](../../plans/pid-4-commissioning.md) W1 — not a
characterisation, and not a reason to stop.

### 2. The first move on the retuned numbers is Jeff's to command

The loop closed on 2026-09-16 and moved its first setpoint on 2026-09-17 —
slowly, on the numbers it shipped with. The same day it was retuned to
[requirements.md](requirements.md) (five minutes for 2 K at 118 K, a hold with
real authority, a band that follows the setpoint) and graded on the bench. It
has not run on the cryostat on those numbers. Arm with the viewer open, move
2 K, and compare the recorder's CSV with the bench figure in
[requirements.md](requirements.md) §3 before believing either.

```bash
python -m ltspm3 -c config-ltspm3-armed.yaml check      # read the band and the ratios
python -m ltspm3 -c config-ltspm3-armed.yaml run --arm
python -m lschart -c config-ltspm3-armed.yaml send setpoint 120 --software
```

### 3. A deliberate step test at two or three temperatures

Still the highest-value hardware measurement available, but no longer from
scratch: the live data has already given a confirmed fast pole and a local
gain, and the settled ladder has given the gain a *shape* over the top of the
range. Those numbers, and how far they are to be trusted, are in
[thermal-response.md](thermal-response.md).

What is missing is *other temperatures* for τ, which still rests on a single
step, because every heater move since has been an up-down doublet thrashed
within minutes rather than a step held. The descending staircase in
[plans/pid-4-commissioning.md](../../plans/pid-4-commissioning.md) is what
fixes it, with the rung list from `analysis/plan_sweep.py`.

**Hold each point about 3τ — roughly 30 minutes up here, and do not fit
anything held under 20 minutes.** A short window does not give a noisy answer,
it gives a confident wrong one, and R² will not warn you.
`ltspm3/tools/steptest.py` holds the protocol; see
[thermal-response.md](thermal-response.md#how-long-to-hold-a-step--and-why-r-will-not-tell-you)
for the table behind that rule, and for what the existing hand data teaches
about step size and doublets.

## The monitor: a judge that never commands

**Built 2026-09-14, soaked 2026-09-15** beside the live recorder. Run it when
you want it; it can do nothing.

```bash
python -m ltspm3.monitor -c config-ltspm3-heater.yaml     # follow the live log
python -m ltspm3.monitor --replay reference/cooldown-10/  # 57 days of archive
```

A separate process, like the viewer: **no port, no commands, ever** — it never
builds a transport and never writes into the command spool, so there is no code
path from a verdict to a heater. It tails the recorder's CSV, applies the
model's error band, and writes `plant.json` beside `status.json` plus a daily
`plant_*.csv`. It runs whether or not the loop is armed, which is most of this
cryostat's life so far.

What it judges, and what makes it usable across a whole cooldown:

| | |
|---|---|
| `δQ` | do the watts add up — `missing_power_w` against a **slow baseline**, not against its own level. The level carries the calibration and the campaign drift, and over a months-long cooldown they swamp it |
| `δT_c`, cold head | the coldplate against its locus, and the 1st/2nd Stage against their own recent scatter. **Never fault** — a cold head going off is a compressor question |
| τ, noise | the plant's time constant after a move, and the thermometer's trailing rms |

It warns at **5 mW** and flags fault-level at a **step of 10 mW inside half an
hour** (Jeff, 2026-09-14), both as floors under the model's own 3σ band. A
fault is a step rather than a level on purpose: a change in delivered power
moves the residual immediately, so anything that creeps to a threshold over
hours is not what that flag is for, and the fault a slow degradation eventually
causes is authority exhausted instead.

On the archive it catches the 2026-09-10 heater-circuit event **fourteen minutes
after it happened**, as a warning, and flags two genuine steps in 57 days.
[plans/pid-2-monitor.md](../../plans/pid-2-monitor.md) §2.4 is the replay row by
row, including the two rows it does not meet and why.

## The cryostat's own machine

The recorder runs on the LTSPM3 machine itself — Windows 10 Pro 19045, Python
3.10.0 installed with `--ignore-requires-python`, an NI PXI-GPIB board, the 218
at `GPIB0::15` and the 336 at `GPIB0::12`, 2 s cadence. First deployed
2026-08-24, recording only; a commanding config has been run there and
commanded successfully since. The generic Windows findings that deployment
produced — the single-instance lock, `os.replace` over an open `status.json`,
clock resolution and `movefile` — are in
[../recorder/windows.md](../recorder/windows.md).

## Replay: the only test on genuine data

```bash
python -m ltspm3.tools.replay "reference/logs/CD*/*.xls"
```

Runs the real pipeline over the reference logs. The rejection rate it reports,
and what counts as acceptable, are in [safety.md](safety.md). It found the
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
