# Troubleshooting

## Connecting

**The instrument enumerates over USB but there is no serial port.**
On macOS: install the Silicon Labs **CP210x VCP driver**. macOS's built-in
support matches SiLabs' own VID `0x10C4`; Lake Shore ships `0x1FB9`, so without
the driver the device appears in System Information and *no `/dev/cu.*` node
ever exists*. This is not an instrument setting — no amount of front-panel
configuration helps.

```bash
ls /dev/cu.*
system_profiler SPUSBDataType | grep -i -A6 'lake\|CP210'
```

**`pyvisa` cannot see `GPIB0::`.** No VISA runtime. Install NI-VISA (and the
interface card's driver). Or avoid it entirely: a box on a COM port should use
`driver: lakeshore`, which needs no VISA.

**`pyvisa` warns "read string doesn't end with termination characters".**
The configured `read_termination` is not what the box actually sends. A 218
ends a reply with `LF`, a 335/336 with `CR LF`, and the default is `CR LF` --
so a 218 on GPIB needs `read_termination: "\n"`. The readings are still
correct, which is why this is easy to ignore and worth not ignoring; see
[instruments](instruments.md).

**It worked yesterday and now the COM port is wrong.** The device
re-enumerated. Match on `transport.serial_number` instead of `com_port`.

**A `TypeError` about `baud_rate` from the vendor driver.** `Model335` requires
it positionally and `Model336` does not accept it; every class declares
`**kwargs`, so a wrong argument is forwarded to the parent and collides there.
`LakeshoreTransport` filters against each model's real signature — if you see
this, the filtering has been bypassed.

## Starting

**`already running` and exit 2.** Another recorder holds the lock
(`runtime.lock_path`). That is the design: two recorders on one instrument
fight over the port. Two genuinely different rigs need two different lock paths.

**`check` says `INVALID:`.** Unknown keys are errors. Read the message — it
names the key.

**`--arm was given but no controller is configured`.** `--arm` only means
something for `ltspm` with a `control:` section. Plain `lschart` records.

## Running

**Cycles are being dropped / the cadence slips.** `check` prints the estimated
transactions and seconds per cycle; compare it with `acquisition.interval_s`.
The usual culprit is `read_status: true`, which adds one `RDGST?` per channel.
Raise `status_every_n_cycles`, turn it off, or lengthen the interval.

Note the poller schedules from a **fixed deadline**, so one long cycle is
followed by a short one and the median stays on cadence. Measured on the bench
336: a 13-transaction cycle takes ~1.25 s against a 1 s interval, 7 slow cycles
in 119, and **none were dropped**.

**Readings show as `NaN` / `usable: false`.** The sample was rejected, not
missing. A per-channel failure marks that channel and the run continues; only a
link-level failure raises. Check `RDGST?` decoding by turning on `read_status`.

**The log stops but the process is alive.** Check `status.json`'s `links` — a
transport that is down is retrying with backoff from 1 s to 30 s, and
`last_error` says why.

**The vendor driver is flooding the log.** It logs every transaction at INFO,
two lines per query — about 1.6 M lines a day at 1 Hz. It is quietened to
WARNING unless the root logger is at DEBUG; if you see the flood, the root
logger is at DEBUG.

## Commanding

Every refusal names its own fix.

| Message | Fix |
|---|---|
| `this recorder is not accepting commands` | `ipc.accept_commands: true` |
| `... is configured read-only` | `allow_writes: true` on that instrument |
| `raising a heater range applies power ...` | `ipc.allow_heater_range: true` — turning one **off** is always allowed |
| `issued N s ago, older than the 30 s limit` | the recorder was not running when the command was queued. Not a bug: replaying an hour of stale setpoints into a live cryostat is the hazard this prevents |
| `several controllers are configured` | say which, with `--instrument` |

**The setpoint was applied and nothing got warmer.** A setpoint does nothing
while that output's heater range is 0. Raising the range is what applies power,
and nothing does it as a side effect.

**`send` hangs then times out.** Nothing is consuming the spool. Is a recorder
running? `python -m lschart -c CONFIG status` exits 1 if it is missing or stale.

**A command was acknowledged with someone else's answer.** You are matching
acknowledgements by an id that repeats. See the MATLAB RNG note in
[file-interface.md](file-interface.md#one-measured-bite-matlab-reseeds-its-rng-every-session)
— derive ids from something documented-unique, and ignore acknowledgements
stamped before the command was issued.

## Reading the data

**The GUI shows nothing.** `ipc.enabled` must be true, and the viewer's
`-c CONFIG` must point at the *recorder's* config (or `--status` at the file).

**`status` exits 1 with a fresh-looking file.** Stale means older than three
poll intervals (minimum 5 s), or `running: false`.

**A legacy `.xls` log does not match its filename.** The filenames lie —
`cd10_..._st2_monitor3.xls` is a 218 log. `tools/import_xls.py` sniffs row 0
rather than trusting the name.
