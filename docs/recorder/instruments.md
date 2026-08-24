# Instruments, transports, and what will bite you

## The layers

```
config  ->  Transport  ->  Instrument  ->  Poller  ->  Frame  ->  CSV + status.json
```

**`Transport`** (`lschart/transport.py`) moves bytes. It is serialised by an
RLock, paced by `inter_command_delay`, and *reconnecting*: opening is lazy, a
single failure does not condemn a link, and retries back off from 1 s to 30 s.
`read_only` is enforced here — at the byte level, below any policy, so a bug
anywhere above it still cannot write.

| Class | `driver:` | |
|---|---|---|
| `VisaTransport` | `visa` | GPIB via `pyvisa`; needs a VISA runtime |
| `LakeshoreTransport` | `lakeshore` | the vendor driver: USB/serial and TCP, no VISA |
| `LoopbackTransport` | `sim` | in-process fake |

**`Instrument`** (`lschart/instruments/`) turns transactions into readings.

| Module | |
|---|---|
| `base.py` | the ABC, Lake Shore number parsing, `RDGST?` decoding |
| `ls218.py` | 8 inputs, plus the analog output used as an actuator |
| `ls33x.py` | 335 and 336 in one driver with a capability table per model. **Every write is confirmed by readback.** Read-only by default |
| `sim.py` | rig-agnostic fakes (`Sim218`, `Sim33x`) and `FirstOrderPlant`, a deliberately boring one-pole default |

A per-channel failure marks that channel's `Reading` — only a **link-level**
failure may raise. One bad thermometer does not stop a run.

## Four Lake Shore behaviours that will bite

All four are measured, not inferred. All four cost real time to find.

### 1. Writes are applied asynchronously

A query issued too soon after a write overtakes it and answers with the
*previous* value.

Measured on the 336 over USB:

| Delay after write | Readback |
|---|---|
| 0 ms | every readback stale |
| 50 ms | lagged by exactly one write |
| 80 ms+ | correct |

**Both wrong regimes look like success.** So there are two defences, not one:
`Transport.write_settle_s` (100 ms) makes it unlikely, and readback
verification in `LS33x` makes it *detectable*. Only the second is something to
stake a cryostat on.

> This very likely applies to a 218 on GPIB too, and is **unverified** there.

### 2. The vendor classes disagree about `baud_rate`

`Model335.__init__` requires it as its first positional argument;
`Model336.__init__` does not accept it at all. Every class also declares
`**kwargs`, so a wrong argument is not rejected — it is forwarded to the parent
and collides there. `LakeshoreTransport` filters against each model's real
signature.

### 3. The vendor driver logs every transaction at INFO

Two lines per query: 1,114 lines in 60 s at 1 Hz, about 1.6 M lines a day. It
is quietened to WARNING unless the root logger is at DEBUG.

### 4. A setpoint does nothing while the heater range is 0

Raising the range is what applies power. **No method in this software raises a
range as a side effect of anything.** `set` applies setpoint, ramp and PID
first and range last, so everything is in place before the one command that
delivers heat.

## The interlocks, in the order they apply

Four gates. The one that applies power needs all four.

| Gate | Where | Default |
|---|---|---|
| `transport.read_only` | byte level — refuses to transmit at all | off |
| `allow_writes` | per-instrument driver policy | **off** |
| `ipc.accept_commands` | is this recorder listening to files at all | **off** |
| `ipc.allow_heater_range` | may a *file* raise a heater range | **off** |

Turning a heater **off** never needs the fourth: the safe direction is always
available.

`probe` forces `read_only` on for every transport regardless of the config, so
its safety does not depend on the config file being right.

## The log

CSV, no row limit, flushed every sample, date-stamped with daily rollover. The
ring buffer (`acquisition/ringbuffer.py`) is for plotting only and is never the
log.

`tools/import_xls.py` reads the legacy `.xls` chart-recorder logs. It sniffs
the header row, because **the filenames lie** —
`cd10_..._st2_monitor3.xls` is a 218 log.
