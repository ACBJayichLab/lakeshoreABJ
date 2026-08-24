# Configuration

One YAML file describes the whole rig. `python -m lschart init config.yaml`
writes a starter; the annotated examples in [`examples/`](../../examples/) are
closer to a real rig.

**Unknown keys are an error.** A misspelled setting fails at load rather than
being silently ignored, so `check` catches typos before hardware does.

**Going live is a config edit, not a code change.** The `driver:` lines are the
whole of the difference between the simulator and a cryostat.

```yaml
log_level: INFO

instruments:      # a LIST; the class is chosen from `model:`
  - name: ls336
    model: "336"
    driver: lakeshore
    transport: {...}
    channels: {...}
    allow_writes: false

acquisition: {...}
recorder:    {...}
runtime:     {...}
ipc:         {...}
sim:         {...}
control:     {...}   # only when `ltspm` is installed -- see ../ltspm/
```

---

## `instruments:`

A list. `model:` picks the config class: `"218"`, `"335"` or `"336"`.

| Key | Default | |
|---|---|---|
| `name` | `ls<model>` | how it appears in the CSV, `status.json` and `--instrument` |
| `model` | — | `"218"`, `"335"`, `"336"` |
| `enabled` | `true` | |
| `driver` | `sim` | see below |
| `read_status` | `false` | poll `RDGST?` for per-channel sensor faults |
| `status_every_n_cycles` | `15` | how often, since it costs one transaction per channel |

### `driver:` — how bytes reach the instrument

| | |
|---|---|
| `sim` | an in-process fake. No hardware, no VISA, no serial port |
| `visa` | `pyvisa` — GPIB, and serial or TCP if a VISA runtime is installed |
| `lakeshore` | Lake Shore's own driver: USB/serial and TCP, **no VISA runtime** |

`lakeshore` is the right choice for a box on a COM port: it removes the NI-VISA
install from the deployment entirely.

### `transport:`

| Key | Default | |
|---|---|---|
| `resource` | — | VISA resource: `GPIB0::15::INSTR`, `ASRL10::INSTR`, `TCPIP::...` |
| `com_port` | `""` | `driver: lakeshore` — `COM10`, `/dev/ttyUSB0` |
| `serial_number` | `""` | **preferred over `com_port`.** A USB box that re-enumerates comes back on a different port but the same serial |
| `ip_address` / `tcp_port` | `""` / `7777` | an Ethernet box; mutually exclusive with `com_port` |
| `baud_rate`, `data_bits`, `parity` | — | a 336 on USB is 57600, 7-O-1 |
| `timeout_ms` | `3000` | |
| `inter_command_delay` | `0.05` | minimum gap between transactions. At 50 ms a two-instrument cycle is ~1.05 s, which does **not** fit a 1 s poll |
| `read_termination` / `write_termination` | `\r\n` | |
| `visa_library` | `""` | |
| **`read_only`** | `false` | **hard interlock**: refuse to transmit any command at all, at the layer where bytes leave |
| `reconnect` | `true` | recover a dropped link rather than ending the run |
| `retry_min_s` / `retry_max_s` | `1.0` / `30.0` | reconnection backoff |
| `failures_before_reconnect` | `3` | one GPIB timeout is usually a slow instrument, not a dead bus |

`read_only` sits one layer *below* `allow_writes`. Use it on a box that must
not be touched under any circumstances, including by a bug in this program.

### 335 / 336 keys

| Key | Default | |
|---|---|---|
| `channels` | `{}` | `{A: Coldplate, B: Stage 2, ...}`. **Empty means ask the instrument** (`INNAME?`). Setting them explicitly means a column cannot silently change meaning if someone relabels the box mid-run |
| `read_setpoints` | `true` | `SETP?` per loop |
| `read_heaters` | `true` | `HTR?` and `RANGE?` |
| `read_analog_outputs` | `false` | |
| **`allow_writes`** | **`false`** | gates every command that changes what the box does |
| `max_setpoint_k` | `350.0` | a blunt guard against a typo'd setpoint, refused in software rather than politely forwarded to a cryostat |

`allow_writes` is off by default because the common case on a shared cryostat is
that some other loop is already holding something important. Turn it on for a
box this software is meant to drive.

### 218 keys

| Key | Default | |
|---|---|---|
| `channels` | `{1: Sample, 2: Cold Head, 3: Shield}` | `{input number: name}`. Only these are read and logged |
| `control_input` | `1` | which input carries the sample. **Leave 0 on a box that is only being logged** — this is the channel a *software* loop would control |
| `analog_output` | `1` | |
| `analog_decimals` | `3` | |

---

## `acquisition:`

| Key | Default | |
|---|---|---|
| `interval_s` | `1.0` | |
| `log_every_n` | `1` | write every Nth frame. 1 = everything; there is no file-size reason not to |
| `ringbuffer_size` | `43200` | frames kept in memory **for plotting only** — never the log |

**1 Hz is the recommendation.** The legacy logs run 2–20 s, but that was the
65,536-row Excel limit forcing slower polling on long runs, not a rig
constraint. Sampling much faster buys little: the measured noise is strongly
correlated (lag-1 autocorrelation 0.51), so it does *not* average down as
1/√N.

`check` prints the estimated transactions and seconds per cycle. If that
exceeds `interval_s`, raise the interval or drop `read_status`.

## `recorder:`

| Key | Default | |
|---|---|---|
| `enabled` | `true` | |
| `directory` | `data` | |
| `filename_prefix` | `lschart` | date-stamped, daily rollover |
| `flush_every_sample` | `true` | a power cut must not cost the last hour |
| `max_rows` | `null` | **no cap, deliberately** |

## `runtime:`

| Key | Default | |
|---|---|---|
| `lock_path` | `data/lschart.lock` | |
| `single_instance` | `true` | |

`run` takes this lock before opening anything, so a second recorder loses
cleanly rather than fighting for the port. Point two genuinely different rigs
at two different paths to run both.

## `ipc:` — the file interface

Full explanation in [file-interface.md](file-interface.md).

| Key | Default | |
|---|---|---|
| `enabled` | `true` | write `status.json` every cycle. Costs one small file write per second and nothing on the bus. **Needed to READ** |
| `directory` | `data` | alongside the CSV, so one directory is the whole interface |
| `status_file` | `status.json` | |
| `command_directory` | `commands` | |
| **`accept_commands`** | **`false`** | read the command spool at all. **Needed to WRITE** |
| `command_ttl_s` | `30.0` | a command older than this is refused, not applied |
| `max_commands_per_cycle` | `4` | bounds how much bus time one cycle spends on commands |
| **`allow_heater_range`** | **`false`** | may a *file* raise a heater range. Turning one **off** is always allowed |
| `ack_history` | `20` | acknowledgements carried in `status.json`. A client polling slower than this fills up may miss its own answer |

## `sim:`

| Key | Default | |
|---|---|---|
| `start_k` | `96.0` | |
| `seed` | `0xC01D` | |
| `speedup` | `1.0` | accelerates the plant but **not** the controller |

## `control:`

Registered by `ltspm` on import; `lschart` alone rejects it as an unknown
section, which is deliberate. See [../ltspm/control.md](../ltspm/control.md).
