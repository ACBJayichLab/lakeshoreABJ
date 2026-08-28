# lschart — a Lake Shore chart recorder

Records every thermometer on a Lake Shore 218 / 335 / 336, continuously, to a
CSV with no row limit — and lets MATLAB, a strip chart, or your own script read
those temperatures and move setpoints **while it runs**.

```bash
pip install -e ".[serial,gui]"

python -m lschart init config.yaml            # a starter config (simulated)
python -m lschart -c config.yaml check        # validate; touches no hardware
python -m lschart -c config.yaml probe        # read the instrument, write nothing
python -m lschart -c config.yaml run          # record until Ctrl-C

python -m lschart.gui -c config.yaml          # the strip chart, another terminal
python -m lschart -c config.yaml status       # or a one-shot text digest
python -m lschart -c config.yaml send ping    # prove the command path works
```

**Going live is a config edit, not a code change**: change `driver:` from `sim`
to `lakeshore` (USB/serial/TCP — no VISA runtime) or `visa` (GPIB).

New here? → **[docs/recorder/quickstart.md](docs/recorder/quickstart.md)**

## Two packages, one repo

The dependency runs one way.

| | |
|---|---|
| **`lschart`** | The generic recorder. **Any cryostat.** Records everything continuously and drives the *instrument's own* PID loop by setpoint. This is what a coworker installs. |
| **`ltspm3`** | The LTSPM3 cryostat's **software** PID, on the 218's analog output. Calibrated to one cryostat. Imports `lschart`; nothing in `lschart` may import it. |

If you are not working on LTSPM3, you need only the first — and you can ignore
`ltspm3/` and `docs/ltspm3/` entirely.

## Documentation

| | |
|---|---|
| **[docs/recorder/](docs/recorder/)** | **The chart recorder — generic, any cryostat.** Install, CLI, configuration, instruments, the file interface, the viewer, Windows, troubleshooting |
| **[docs/ltspm3/](docs/ltspm3/)** | **The LTSPM3 software PID — one cryostat only.** The hardware, the measured thermal response, the control loop, the safety rules |
| [matlab/README.md](matlab/README.md) | Driving a running recorder from MATLAB |
| [CLAUDE.md](CLAUDE.md) | Orientation and the load-bearing invariants, for anyone (or anything) changing the code |
| [HANDOFF.md](HANDOFF.md) | Point-in-time status. Goes stale; the docs above do not |

## Two things to know before you start

**The recorder owns the instrument port, exclusively.** A Windows COM port has
exactly one holder, and two processes on one GPIB board interleave transactions
into garbled replies. So nothing else ever opens the instrument — the viewer,
MATLAB and your scripts all go through
[a file interface](docs/recorder/file-interface.md) instead. That single
constraint explains most of the design.

**A setpoint does nothing while the heater range is 0.** Raising the range is
what applies power, and nothing in this software raises one as a side effect of
anything. Writes are off by default at **seven independent layers** — the byte
level, the driver, the file door, one per command that applies power, retuning,
and which *client* is asking. Every one applies in both directions: commanding a
heater to zero needs the same permission as raising it, because cutting a heater
is not automatically the safe direction. The only exemptions anywhere are the
two panic commands. See
[instruments.md](docs/recorder/instruments.md#the-interlocks-in-the-order-they-apply).

## Layout

```
lschart/      the generic recorder    (docs/recorder/)
ltspm3/        the LTSPM3 software PID (docs/ltspm3/)
matlab/       LakeShore.m + selftest  -- copied to the cryostat, not built
examples/     annotated configs for a 335 on COM and a 336 on USB
reference/    legacy MATLAB and 24 .xls logs -- reference only, not built
tests/        generic;  tests_ltspm3/ is the control half
```

## Development

```bash
uv venv --allow-existing .venv
uv pip install --python .venv/bin/python -e ".[dev,serial]"
.venv/bin/python -m pytest -q          # 584 tests, no hardware required
```

Development is macOS; deployment is **Windows** —
see [docs/recorder/windows.md](docs/recorder/windows.md).
