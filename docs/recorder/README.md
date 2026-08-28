# The chart recorder

`lschart` is a **generic Lake Shore chart recorder**. Point it at a 218, a 335
or a 336 on GPIB, USB or TCP; it reads every thermometer on a fixed cadence,
writes a CSV that has no row limit, and publishes what it is doing to a file
that anything else can read.

It can also *command* an instrument — setpoint, ramp rate, heater range, the
loop's own PID gains — but only through interlocks that are off by default, and
which apply in **both** directions: commanding a heater to zero needs the same
permission as raising it. Recording is what it does; writing is what it must be
told to do.

The two exceptions are the panic commands, `heaters_off` and `hold`. Stopping
the cryostat is the one thing that must never wait on a config edit.

Nothing in this directory is specific to one cryostat.

## Start here

1. **[install.md](install.md)** — including the USB driver that has to be
   installed before macOS will show a Lake Shore box at all.
2. **[quickstart.md](quickstart.md)** — `init` → `check` → `probe` → `run`.
3. **[cli.md](cli.md)** — the commands, and which of them touch hardware.

## Then, as needed

- **[configuration.md](configuration.md)** — the YAML, section by section.
  Unknown keys are an error, so a typo fails loudly rather than being ignored.
- **[instruments.md](instruments.md)** — what each driver does, and four
  measured Lake Shore behaviours that cost real time to discover.
- **[file-interface.md](file-interface.md)** — how MATLAB, the viewer, or your own
  script reads temperatures and sends setpoints **while the recorder runs**.
- **[gui.md](gui.md)** — the strip chart, which is a separate process.
- **[windows.md](windows.md)** — the deployment target.
- **[troubleshooting.md](troubleshooting.md)**.

## The one idea worth having up front

**The recorder owns the instrument port, exclusively.** A Windows COM port has
exactly one holder, and two processes sharing one GPIB board interleave
transactions into garbled replies. So no second program ever opens the
instrument. Everything else — the viewer, MATLAB, your script, the recorder's
own `status` and `send` commands — goes through
[the file interface](file-interface.md) instead.

That constraint explains most of the design, so it is worth reading
[file-interface.md](file-interface.md) before deciding the layout is odd.
