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
.venv/bin/python -m pytest -q                 # everything, 395 tests
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
