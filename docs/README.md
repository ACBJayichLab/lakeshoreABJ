# Documentation

This repository holds **two programs**, and the documentation is split the same
way they are. Which half you need depends on what you are doing, not on what
you have installed.

## [`docs/recorder/`](recorder/) — the chart recorder (generic)

`lschart`: records every thermometer on any Lake Shore cryostat, continuously, and
can move the *instrument's own* setpoints. Nothing in here is specific to one
cryostat. This is the half a coworker installs.

| | |
|---|---|
| [install.md](recorder/install.md) | Python, extras, the USB/GPIB drivers each connection needs, and CI |
| [quickstart.md](recorder/quickstart.md) | From nothing to a running chart in five commands |
| [cli.md](recorder/cli.md) | Every command: `run` `probe` `set` `check` `status` `send` `init` |
| [configuration.md](recorder/configuration.md) | The config file, section by section |
| [instruments.md](recorder/instruments.md) | Drivers, transports, and four Lake Shore behaviours that will bite |
| [file-interface.md](recorder/file-interface.md) | `status.json` and the command spool — how anything else talks to a running recorder |
| [gui.md](recorder/gui.md) | The strip chart |
| [windows.md](recorder/windows.md) | Deployment on the actual target platform |
| [troubleshooting.md](recorder/troubleshooting.md) | Symptom → cause |

MATLAB has its own guide, kept next to the code it documents:
[`matlab/README.md`](../matlab/README.md).

## [`docs/ltspm3/`](ltspm3/) — the LTSPM3 software PID (one cryostat only)

`ltspm3`: a software PID driving the sample heater on **Jeff's LTSPM3
cryostat**, through the 218's analog output. Every number in here is calibrated
to that cryostat and does not transfer to another one.

| | |
|---|---|
| [cryostat.md](ltspm3/cryostat.md) | The hardware: instruments, addresses, what is wired to what |
| [thermal-response.md](ltspm3/thermal-response.md) | Measured thermal behaviour, and why the model is in two stages |
| [control.md](ltspm3/control.md) | The loop: supervisor, PID, feedforward, ramp, dither |
| [safety.md](ltspm3/safety.md) | The eight design rules, and the sensor glitch that shaped them |
| [running.md](ltspm3/running.md) | `check`, `run --arm`, replay, and the step test |
| [commissioning.md](ltspm3/commissioning.md) | the staged path onto the real cryostat, and the calibration campaign |

**If you are not working on LTSPM3, you do not need any of it.** `lschart` runs
without `ltspm3` installed, configured, or read.

## The dependency runs one way

`ltspm3` imports `lschart`. Nothing in `lschart` may import `ltspm3` — that is
what keeps the recorder generic, and it is the rule to check first if a change
feels like it is fighting the layout.

## Vocabulary

One concept, one word: [style.md](style.md) is the term table both halves
share. If you find yourself writing a synonym, use the table instead.
