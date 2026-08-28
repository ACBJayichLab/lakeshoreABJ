# Quickstart

Assumes [install.md](install.md) is done, including the USB or VISA driver.

## 1. Write a config

```bash
python -m lschart init config.yaml
```

That starter file uses `driver: sim`, so it runs with nothing plugged in. To
go live, edit the `driver:` and `transport:` lines — the two annotated examples
are the fastest way there:

| Example | Cryostat |
|---|---|
| [`examples/config-335-usb.yaml`](../../examples/config-335-usb.yaml) | a 335 on a COM port, heaters on its own outputs, commandable from MATLAB |
| [`examples/config-336-usb.yaml`](../../examples/config-336-usb.yaml) | a 336 on USB, **read-only**, for watching |

## 2. Check it before it touches anything

```bash
python -m lschart -c config.yaml check
```

Validates the file, and prints the cadence, the estimated transactions per
cycle, where `status.json` will be, whether commands are accepted, and which
instruments are writable. It opens no port. An unknown key is an error here,
which is the point — a misspelled setting fails loudly instead of silently
keeping its default.

## 3. Probe the hardware

**The first thing to run against an instrument nobody has talked to yet.**

```bash
python -m lschart -c config.yaml probe
```

It opens each instrument, reads everything once, and writes nothing. `probe`
forces every transport read-only **regardless of what the config says**, so its
safety does not depend on your config file being right. The only traffic is
queries: `*IDN?`, `INNAME?`, `KRDG?`, `SETP?`, `HTR?`, `RANGE?`, `PID?`,
`RAMP?`.

Read the output before going further. It tells you the channel names, the
current setpoints, and — the part worth pausing on — **which heater ranges are
non-zero**.

## 4. Record

```bash
python -m lschart -c config.yaml run
```

Runs until Ctrl-C. It writes a CSV under `recorder.directory` with no row limit,
flushed every sample, and rewrites `status.json` every cycle. Only one recorder
may run per cryostat; a second one loses the lock cleanly and exits rather than
fighting for the port.

## 5. Watch it

From another terminal — these read files and never touch the instrument, so any
number of them can run at once:

```bash
python -m lschart.gui -c config.yaml      # the strip chart
python -m lschart -c config.yaml status   # one-shot text digest, exit 1 if stale
```

## 6. Command it

Two different verbs, and the difference matters:

```bash
# the recorder is RUNNING -- go through the file spool
python -m lschart -c config.yaml send ping
python -m lschart -c config.yaml send setpoint 77 --loop 1

# the recorder is NOT running -- open the instrument directly
python -m lschart -c config.yaml set --loop 1 --setpoint 77
```

`send` needs `ipc.accept_commands: true`; both need `allow_writes: true` on the
instrument. Changing a heater range from a file needs
`ipc.allow_heater_range: true` as well — **in either direction**, because
cutting a heater is not automatically the safe direction. See
[file-interface.md](file-interface.md) for why there are that many gates.

What is always available, whatever the gates say, is stopping:

```bash
python -m lschart -c config.yaml send heaters_off   # every writable heater to 0
python -m lschart -c config.yaml send hold          # every loop where it is
```

Those two are the only exemptions in the system. In the viewer they are behind
the **Panic** menu, three clicks by design.

**A setpoint does nothing while the heater range is 0.** Raising the range is
the act that applies power, and nothing in this software raises one as a side
effect of anything else.
