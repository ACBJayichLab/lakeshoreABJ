# The command line

```
python -m lschart [-c CONFIG] [--log-level LEVEL] <command> ...
```

`-c/--config` is a path to a YAML file; without it the built-in defaults apply
(simulated instruments). `lschart` is also installed as a console script, so
`lschart -c config.yaml run` is the same thing.

## Which commands touch the instrument

This is the distinction to keep straight, because it decides whether a command
works while a recorder is running.

| | Opens the port | Works while a recorder runs |
|---|---|---|
| `run` | **yes** — and holds it | no (the lock refuses a second one) |
| `probe` | **yes**, read-only | no |
| `set` | **yes** | no |
| `check` | no | yes |
| `status` | no | yes — that is its whole job |
| `send` | no | yes — that is its whole job |
| `init` | no | yes |

A port has exactly one holder. `set` and `send` do the same *kinds* of things
by two different routes, and you pick by whether a recorder is up.

---

## `run` — record, until interrupted

```bash
python -m lschart -c config.yaml run
python -m lschart -c config.yaml run --interval 2 --duration 3600
```

| Flag | Effect |
|---|---|
| `--interval S` | override the poll cadence |
| `--duration S` | stop after N seconds (otherwise: until Ctrl-C / SIGTERM) |
| `--arm` | close a **software** control loop — only meaningful for `ltspm`, see [../ltspm/running.md](../ltspm/running.md). Plain `lschart` reports an error rather than ignoring it |
| `--setpoint K` | the target to arm at |

Takes a single-instance lock before opening anything, so a second recorder
loses the race cleanly instead of discovering the port is held halfway through
startup. Logs the row count and the CSV path on exit. `--arm` is never
implicit: a recorder must not start driving a heater because someone ran it
with the wrong config file.

## `probe` — read everything, write nothing

```bash
python -m lschart -c config.yaml probe
```

**The first thing to run against unfamiliar hardware.** Forces every transport
read-only *regardless of the config*, so its safety does not depend on the
config file being correct. Queries only: `*IDN?`, `INNAME?`, `KRDG?`, `SETP?`,
`HTR?`, `RANGE?`, `PID?`, `RAMP?`.

## `set` — command an instrument directly

```bash
python -m lschart -c config.yaml set --loop 1 --setpoint 77
python -m lschart -c config.yaml set --loop 1 --ramp 2.5
python -m lschart -c config.yaml set --heater 1 --range 0
```

| Flag | |
|---|---|
| `--instrument NAME` | required only when several controllers are configured |
| `--loop N` | control loop, default 1 |
| `--heater N` | heater output, default 1 |
| `--setpoint K` | |
| `--range 0..3` | **0 is off. Raising it applies power.** |
| `--ramp K/min` | the instrument's own firmware ramp; 0 disables |
| `--pid P I D` | |

Applies in a fixed order — setpoint, ramp, PID, **then** range — so everything
is in place before the one command that applies power lands. Reads everything
back afterwards and prints it. A refusal exits 1 with `REFUSED:` and the reason.

Needs `allow_writes: true` on the instrument, and fails if a recorder holds the
port. Deliberately a separate one-shot command rather than a flag on `run`:
changing a setpoint is an operator action with a consequence, not a side effect
of starting a recorder.

## `check` — validate a config, touching nothing

```bash
python -m lschart -c config.yaml check
```

Prints the drivers in use (and whether that means hardware), the cadence, the
estimated transactions and seconds per cycle, the status file path, whether
commands are accepted, and which instruments are writable. Exits 1 with
`INVALID:` on a bad config. Unknown keys are errors, so this catches typos.

## `status` — what is a *running* recorder doing

```bash
python -m lschart -c config.yaml status
python -m lschart -c config.yaml status --json
python -m lschart status --file /path/to/status.json
```

Reads `status.json`. Takes no lock and touches no hardware, so it is safe from
any number of terminals at once. Prints pid and host, last update and its age,
cycle count, every channel, link health, the log path and row count, command
counters, and control state if there is one.

**The exit status is the useful part in a script:** 0 while the recorder is
alive and current, 1 if the file is missing, stale, or says it stopped. Stale
means older than three poll intervals (minimum 5 s) — one slow cycle is
normal, three in a row is not.

## `send` — command a *running* recorder

```bash
python -m lschart -c config.yaml send ping
python -m lschart -c config.yaml send setpoint 77 --loop 1
python -m lschart -c config.yaml send ramp 2.5 --loop 1
python -m lschart -c config.yaml send range 0 --output 1
python -m lschart -c config.yaml send heaters_off
```

Writes into the command spool and waits for the acknowledgement
(`--timeout`, default 10 s). `--instrument NAME` when several are configured.

`ping` is the one command that proves the whole path — spool, recorder,
acknowledgement — without touching an instrument. Run it first when setting up
a client.

Requires `ipc.accept_commands: true`, plus the instrument's `allow_writes`, plus
`ipc.allow_heater_range` for `range` above 0. Full rules in
[file-interface.md](file-interface.md).

## `init` — write a starter config

```bash
python -m lschart init config.yaml [--force]
```

Refuses to overwrite without `--force`.

---

## `ltspm`: the same CLI, one thing swapped

```bash
python -m ltspm -c config.yaml check
python -m ltspm -c config.yaml run --arm --setpoint 96.0
```

`ltspm` is a thin shim that swaps what builds the application, so every command,
flag and interlock above is shared and cannot drift. `python -m lschart` on the
same config still works and simply records — it has no controller, so `--arm`
is refused rather than silently ignored. See [../ltspm/](../ltspm/).
