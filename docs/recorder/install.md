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
| `dev` | `pytest`, `xlrd` | tests, and reading legacy `.xls` logs |

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
.venv/bin/python -m pytest -q                    # 246 tests
.venv/bin/python -m lschart init config.yaml     # starter config, driver: sim
.venv/bin/python -m lschart -c config.yaml check
.venv/bin/python -m lschart -c config.yaml run --duration 10
```

**Going live is a config edit, not a code change**: change `driver:` from `sim`
to `lakeshore` (USB/serial/TCP) or `visa` (GPIB). See
[configuration.md](configuration.md).
