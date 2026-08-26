# Installing

Python 3.11 or newer. Development is on macOS; the deployment target is
Windows — see [windows.md](windows.md) for what differs there.

## The package

```bash
uv venv --allow-existing .venv
uv pip install --python .venv/bin/python -e ".[dev,serial]"
```

Plain `pip install -e ".[serial]"` works just as well; `uv` is only faster.

### The extras, and why they are extras

| Extra | Pulls in | Needed for |
|---|---|---|
| *(base)* | `numpy`, `pyyaml`, `pyvisa` | recording, the CLI, the file interface |
| `serial` | `lakeshore` | USB / serial / TCP instruments — **no VISA runtime** |
| `gui` | `pyqtgraph`, `PySide6` | the strip chart |
| `dev` | `pytest`, `xlrd`, `ruff` | tests, reading legacy `.xls` logs, and the lint CI gates |

The recorder is the process that has to stay up for months, so it deliberately
does not depend on Qt. The viewer is a **separate process** and carries its own
dependencies; installing without `gui` costs you the chart and nothing else.

Two console scripts are installed:

```
lschart          the recorder and its CLI
lschart-view     the strip chart
```

Everything below uses `python -m lschart` so it works from a checkout without
the scripts being on `PATH`.

## The driver your connection needs

This is the step that most often stops a first run, and it is never an
instrument setting — no amount of front-panel configuration substitutes for it.

### USB (a 335 or 336 on a Lake Shore USB cable)

Lake Shore's USB is a **Silicon Labs CP210x** bridge, but the box enumerates
under **Lake Shore's** vendor id (`0x1FB9`), not Silicon Labs' (`0x10C4`).

- **macOS** ships CP210x support matched to `0x10C4` only. Without Silicon
  Labs' own **CP210x VCP driver**, the device enumerates, shows up in System
  Information, and *no `/dev/cu.*` node ever appears*. Install the SiLabs VCP
  driver.
- **Windows** wants the vendor driver from Lake Shore (or SiLabs) so the box
  appears as a COM port.

Confirm before blaming the software:

```bash
# macOS
ls /dev/cu.*
system_profiler SPUSBDataType | grep -i -A6 'lake\|CP210'
```

The bench 336 here is VID `0x1FB9` / PID `0x0301`, serial `LSA26E0`, firmware
3.1, at 57600 baud 7-O-1.

### GPIB

`pyvisa` needs a VISA runtime to see `GPIB0::` at all — on Windows that means
the **NI-VISA runtime**, plus the driver for whatever GPIB interface is
installed. `driver: visa` in the config selects this path.

### TCP

Handled by the `lakeshore` extra, same as USB. No VISA runtime.

## Verifying the install without hardware

Everything runs against simulated instruments by default, so this works on a
laptop with nothing plugged in:

```bash
.venv/bin/python -m pytest -q                    # 395 tests, ~13 s
.venv/bin/python -m ruff check .                 # must pass; CI gates on it
.venv/bin/python -m lschart init config.yaml     # starter config, driver: sim
.venv/bin/python -m lschart -c config.yaml check
.venv/bin/python -m lschart -c config.yaml run --duration 10
```

The suite runs from any working directory — if a test skips because it cannot
find `reference/logs/` or `config.yaml`, that is a bug in the test, not in your
checkout.

**Going live is a config edit, not a code change**: change `driver:` from `sim`
to `lakeshore` (USB/serial/TCP) or `visa` (GPIB). See
[configuration.md](configuration.md).

## Continuous integration

`.github/workflows/tests.yml` runs the suite on **Linux, Windows and macOS**,
on Python 3.11 and 3.13, for every push to `main` and every pull request. It
lints first, then tests.

Windows is in the matrix because that is the deployment target while
development is macOS, and because the one Windows-specific bug found so far —
the single-instance lock taken on a byte that moved with the file position, so
a second recorder never collided with the first — had been hidden behind a
`skip`.

**A skipped test fails the build.** That is deliberate rather than fussy: every
remaining skip is conditional on something CI is supposed to provide (`xlrd`,
PySide6, the reference logs), so a skip in CI means CI is not testing what it
claims to. The Linux job installs the Qt system libraries for the same reason —
without them PySide6 will not import and the viewer tests quietly do not run.

The repository is public, so Actions minutes on standard runners are free and
unmetered; nothing here draws down a quota.
