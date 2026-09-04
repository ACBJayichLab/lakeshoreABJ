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

**Read terminators differ between Lake Shore models on GPIB, and a wrong one
still appears to work.** Measured on the LTSPM3 board, 2026-08-24, by reading
`*IDN?` with `read_termination` disabled and looking at the raw bytes:

| Box | ends a reply with |
|---|---|
| Model 218 | `LF` |
| Model 335/336 | `CR LF` |

`TransportConfig.read_termination` defaults to `CR LF`, so a **218 on GPIB
needs `read_termination: "\n"` set explicitly**. Getting it wrong is not a
visible failure, which is the trap: GPIB asserts EOI at the end of a reply, so
the read completes regardless and `.strip()` removes the stray byte. The
readings are correct. What it costs is a `pyvisa` warning on every read and a
silent dependence on EOI — a reply that ever arrives without EOI blocks until
the timeout instead of returning.

**`Instrument`** (`lschart/instruments/`) turns transactions into readings.

| Module | |
|---|---|
| `base.py` | the ABC, Lake Shore number parsing, `RDGST?` decoding |
| `ls218.py` | 8 inputs, plus the analog output used as an actuator |
| `ls33x.py` | 335 and 336 in one driver with a capability table per model. **Every write is confirmed by readback.** Read-only by default |
| `sim.py` | cryostat-agnostic fakes (`Sim218`, `Sim33x`) and `FirstOrderResponse`, a deliberately boring one-pole default |

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

...**except on a 218, which has neither.** See below; it is the one place where
the previous paragraph's comfort does not apply.

## The 218's analog output has no inert half

A 33x is safe to reason about in two pieces. `SETP` says where to go and does
nothing on its own; `RANGE` says how much power may be used and is the act that
applies it. You can gate them separately because they *are* separate.

A 218 has no loop, no range and no setpoint. Its analog output is driven in
manual mode by one command:

```
ANALOG 1, 0, 2, 1, 1,1,1,<percent>      out 1, unipolar, manual, kelvin
AOUT? 1                                 readback, in percent
```

Only the trailing value ever changes — the other seven fields are kept
byte-identical to a known-good string rather than recomputed, because a
recomputed field that happened to differ would change the output's *mode*, not
just its level.

The percentage **is** the power, so:

- `LS218.set_analog_percent` is gated by `allow_writes` exactly as a 33x
  `RANGE` is, and defaults off;
- there is a `max_output_pct` ceiling in configuration, because `0 ≤ pct ≤ 100`
  is not a useful bound when the local gain is tens of kelvin per percent (on
  LTSPM3 it is ~10 K/% — see [`../ltspm3/thermal-response.md`](../ltspm3/thermal-response.md)). What
  the ceiling should be depends entirely on the heater on the other end, so the
  generic default is 100 and the cryostat's config supplies the real number;
- every write is confirmed by reading `AOUT?` back. Mind the granularity: the
  DAC steps 0.01% and `AOUT?` answers to two decimals, so `readback_tol_pct`
  must exceed both or a write that worked perfectly is reported as a failure;
- **nothing here ramps.** One command, one step, as fast as the cryostat allows.
  Rate limiting is control policy and lives in the supervisor; duplicating it
  here would give the cryostat two sets of limits that can disagree.

A software loop writing this output every cycle should set `verify_writes:
false` and confirm for itself (`SupervisorConfig.verify_readback`), rather than
paying for a second transaction and a settle in every control cycle.

## How a 218 actually reads a thermometer

Worth knowing before anyone proposes to change what arrives at its terminals.
From the [Model 218 user's manual][218man] and its specification table:

| | |
|---|---|
| Inputs | 8, in **two groups of four**; every input in a group must be the same sensor type |
| Measurement | **four-lead differential** — one twisted pair carries the excitation, the other carries the sense voltage |
| Excitation | **eight dedicated constant-current sources, one per input, always on**. Diode inputs get 10 µA ±0.05% |
| A/D | two converters, one per group of four |
| Update rate | **16 readings/s total → 2 readings/s per input** |
| Diode, 0–2.5 V range | measurement resolution **20 µV**; electronic accuracy ±160 µV ±0.01% of reading |
| Diode, 0–7.5 V range | measurement resolution 100 µV; accuracy ±160 µV ±0.02% of reading |
| Digital filter | `FILTER <input>,<off/on>,<points>,<window>` — a running average of **2 to 64 readings**, with a window percentage that discards the average on a step so the filter does not smear a real transient |

Two consequences matter, and they pull in opposite directions.

**The excitation is never switched, which makes the input forgiving.** Most
scanning monitors multiplex one current source across their channels; the
manual makes a point of the 218 not doing that, because not waiting for a
current source to settle is how it gets to 16 rdg/s. So a capacitor at a 218
input sits across a sensor that is being driven continuously, and the
multiplexer only moves the voltmeter. On a switched-excitation instrument the
same capacitor would be a serious bug — it could not settle inside a channel
dwell, and every reading would carry the previous channel's charge.

**Two readings per second per input is the ceiling on everything downstream.**
The 218's own output stream is band-limited to 1 Hz per input no matter how
often you poll it. Anything faster than that has already aliased inside the
218's A/D before the recorder ever sees a number, and no amount of polling,
averaging or filtering *after* the GPIB cable separates it out again. An
anti-alias filter for this box therefore needs a corner well below 1 Hz — a
time constant of seconds, not milliseconds.

### Ask it in the sensor's own units, not in kelvin

`read_sensor_units: true` adds one `SRDG? 0` per cycle and logs every input in
the units the sensor actually works in — volts on a diode range, ohms on an RTD
— as `{name}.sensor{n}` columns beside the kelvin ones.

It is off by default because it costs a transaction, and on for exactly one
reason: **kelvin is the wrong domain to ask a noise question in.** A sensor's
sensitivity varies by orders of magnitude across its range, so a noise floor
that is *constant in ohms* — which is what an instrument or a wiring fault
produces — appears in kelvin as a figure that grows with temperature and looks
convincingly like physics. Logging both columns settles that by subtraction
rather than by datasheet, which matters because sensitivity tables are the
thing nobody has to hand at the moment they need them.

It also identifies the sensor by inspection, which is why there is no
`INTYPE?` here: ~1 V is a diode, tens to thousands of ohms is an RTD.

`FILTER` is not implemented in `ls218.py`. It is a per-input instrument setting
rather than something the recorder needs each cycle, and the same averaging is
better done in software where the raw data survives — but it is worth querying
by hand before trusting a noise measurement, because a record taken with it on
and a record taken with it off do not describe the same instrument.

`docs/ltspm3/noise.md` works through what all this meant for one cryostat that
had 10–15 mK of jitter and a proposal to fix it with an RC network.

[218man]: https://www.lakeshore.com/docs/default-source/product-downloads/manuals/218_manual.pdf

## The interlocks, in the order they apply

A command that applies power needs four gates: the three common ones plus
whichever per-kind gate matches the box it is aimed at.

| Gate | Where | Default |
|---|---|---|
| `transport.read_only` | byte level — refuses to transmit at all | off |
| `allow_writes` | per-instrument driver policy | **off** |
| `ipc.accept_commands` | is this recorder listening to files at all | **off** |
| `ipc.allow_heater_range` | may a *file* raise a **33x** heater range | **off** |
| `ipc.allow_analog_output` | may a *file* raise a **218** analog output | **off** |
| `ipc.allow_pid` | may a *file* retune a loop | **off** |

The two power gates are deliberately not one switch. They are different
commands on different boxes, and a cryostat that wants its own sample heater
driven from a file has no business also being able to raise a range on a
controller that is holding something else.

`allow_pid` is a per-kind gate but **not** a power gate. Retuning applies no
power — a loop with its range at 0 stays inert however it is tuned — and it has
no always-allowed direction either, because there is no such thing as a gain
that removes heat. It is separate because gains are a different *kind* of act:
a setpoint moves the cryostat somewhere and you watch it go, gains change how
it gets anywhere at all, quietly, for the rest of the run.

**Both gates apply in both directions.** A range to 0, or an analog output to
0%, needs the same permission as raising it. Cutting a heater is not
automatically the safe direction — it stops heating, and on a cryostat where the
sample heater also holds the stage it can crash it. A client not trusted to move
a heater is not trusted to move it to zero either.

The way to stop the cryostat now is the panic kinds `heaters_off` and `hold`,
which bypass both gates and the source policy — and nothing else. They still
need `allow_writes`, because a box this program may not write to is one whose
output may belong to somebody else.

On a different axis again, `ipc.sources` asks *which client* may ask at all —
see [file-interface](file-interface.md#a-sixth-gate-on-a-different-axis).

`probe` forces `read_only` on for every transport regardless of the config, so
its safety does not depend on the config file being right.

## The log

CSV, no row limit, flushed every sample, date-stamped with daily rollover. The
ring buffer (`acquisition/ringbuffer.py`) is for plotting only and is never the
log.

`tools/import_xls.py` reads the legacy `.xls` chart-recorder logs. It sniffs
the header row, because **the filenames lie** —
`cd10_..._st2_monitor3.xls` is a 218 log.
