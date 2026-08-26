# lschart / ltspm3 — Lake Shore chart recorder + LTSPM3 software PID

**Two packages, one repo. The dependency runs one way.**

| | |
|---|---|
| `lschart` | Generic Lake Shore recorder. Any cryostat. Records every thermometer continuously and drives the *instrument's own* PID loop by setpoint. This is what a coworker installs. |
| `ltspm3` | The LTSPM3 cryostat's **software** PID on the 218's analog output. Calibrated to one cryostat. Imports `lschart`; nothing in `lschart` may import it. |

## Where the documentation lives

This file is **orientation and invariants**. The detail is split the same way
the code is, and it is the detail that goes stale — edit it there, not here.

| | |
|---|---|
| [`docs/recorder/`](docs/recorder/) | **Generic, any cryostat.** [install](docs/recorder/install.md) · [quickstart](docs/recorder/quickstart.md) · [cli](docs/recorder/cli.md) · [configuration](docs/recorder/configuration.md) · [instruments](docs/recorder/instruments.md) · [file-interface](docs/recorder/file-interface.md) · [gui](docs/recorder/gui.md) · [windows](docs/recorder/windows.md) · [troubleshooting](docs/recorder/troubleshooting.md) |
| [`docs/ltspm3/`](docs/ltspm3/) | **LTSPM3 only.** [cryostat](docs/ltspm3/cryostat.md) · [safety](docs/ltspm3/safety.md) · [thermal response](docs/ltspm3/thermal-response.md) · [control](docs/ltspm3/control.md) · [running](docs/ltspm3/running.md) |
| [`matlab/README.md`](matlab/README.md) | MATLAB's half of the file protocol |
| [`README.md`](README.md) | The front door, for a new user |
| [`HANDOFF.md`](HANDOFF.md) | Point-in-time status. Goes stale by design |

**Keep the split when you write.** Anything true of any Lake Shore cryostat belongs
in `docs/recorder/`; anything calibrated to LTSPM3 belongs in `docs/ltspm3/`. A
generic document that mentions THE CHONKE is in the wrong file.

## Priorities (Jeff, 2026-08-24)

**The GUI and the MATLAB interface are the priority. The software PID is not.**

`ltspm3` is complete and tested and should be left alone unless it breaks. New
effort goes to `lschart`: the strip-chart viewer, the MATLAB file interface,
and Windows deployment. Read that as a standing instruction, not a phase
ordering — resist "while I am in here" improvements to `control/`.

The viewer and the MATLAB interface now exist and are exercised end to end (the
MATLAB half against a real MATLAB R2025b, the viewer against a live recorder).
**Windows deployment is what is left**, and it is untested: development is
macOS.

## The invariants

These are the things a change must not break. The reasoning behind each one is
in the linked document.

1. **`lschart` never imports `ltspm3`.** If you find yourself wanting it to, the
   design is wrong, not the rule.
2. **The recorder owns the port, exclusively.** A COM port has exactly one
   holder; two processes on one GPIB board garble replies. Everything else goes
   through files. → [file-interface](docs/recorder/file-interface.md)
3. **Five write interlocks, all off by default**: `transport.read_only` (byte
   level) · `allow_writes` (driver policy) · `ipc.accept_commands` ·
   `ipc.allow_heater_range` (a 33x range) · `ipc.allow_analog_output` (a 218
   analog output). A command arriving by file passes exactly the gates a command
   typed at the CLI passes. The last two are separate on purpose: different
   commands, different boxes, and a cryostat usually wants one open and not the
   other. Turning a heater **off** needs neither of them.
   → [instruments](docs/recorder/instruments.md)
4. **Nothing raises a heater range as a side effect of anything.** A setpoint
   does nothing while the range is 0; raising it is what applies power.
   **The 218 is the exception and has no inert half** — no loop, no range, one
   `ANALOG` command whose percentage *is* the power. Hence its own
   `allow_writes` gate and a `max_output_pct` ceiling in config, never a
   constant in code.
5. **Writes are applied asynchronously** — a query issued too soon answers with
   the previous value, and both wrong regimes *look like success*. Hence
   `write_settle_s` **and** readback verification. **Unverified on the 218 over
   GPIB; check `verify_readback` before the LTSPM3 cryostat runs armed.**
   → [instruments](docs/recorder/instruments.md)
6. **Availability of the cryostat outranks control quality.** Every ambiguous
   case holds the output and raises an alarm; nothing raises the heater in
   response to a fault. The eight design rules are in
   [safety](docs/ltspm3/safety.md) and are not negotiable.
7. **Never hardcode a limit in `control/`.** It belongs in `SupervisorConfig`,
   `SensorGuardConfig` or `PIDConfig`, visible and auditable in one place.
8. **Backends are config-driven.** Going live is a `driver:` edit, never a code
   change. There is no hardware on the bench and there will not be for a while.
9. **Where a measured number contradicts memory, the number wins.** Re-derive
   from `reference/logs/`, don't trust the prose. → [thermal response](docs/ltspm3/thermal-response.md)

## Layout

```
lschart/                    GENERIC -- any Lake Shore cryostat
  model.py           Reading / Frame / Validity / ReadingStatus. Immutable; crosses threads.
  transport.py       Transport ABC: serialised by an RLock, paced, and
                     RECONNECTING -- opening is lazy, a single failure does not
                     condemn a link, retries back off 1->30 s.  Plus
                     VisaTransport (GPIB), LakeshoreTransport (the vendor
                     driver: USB/serial + TCP, no VISA) and LoopbackTransport.
                     `read_only` is a hard interlock at the byte level.
  instruments/
    base.py          Instrument ABC, Lake Shore number parsing, RDGST? decoding.
    ls218.py         8 inputs + the heater actuator (AnalogOutputConfig).
    ls33x.py         335/336 in one driver, a capability table per model.
                     Every write is confirmed by readback.  Read-only default.
    sim.py           Cryostat-agnostic fakes (Sim218/Sim33x) + FirstOrderResponse, a
                     deliberately boring one-pole default.  The calibrated
                     LTSPM3 thermal response is injected from ltspm3/, not built in here.
  config.py          AppConfig + YAML. Unknown keys are an error. `instruments:`
                     is a list; the class is chosen from `model:`.
                     `register_section()` lets ltspm3 add `control:`.
  ipc/               The file interface. Read `status.py`'s docstring first.
    lock.py          OS-level single-instance lock.
    status.py        status.json, rewritten in full every cycle via os.replace.
                     Arrays, not objects -- MATLAB mangles JSON object *keys*.
    commands.py      The maildir-style command spool. Ordering, expiry,
                     acknowledgement, and clock-skew refusal.
    service.py       Joins the two onto the acquisition cycle, on the
                     acquisition thread, because that thread owns the bus.
  gui/               The strip chart. A SEPARATE PROCESS, not a thread.
    source.py        CsvTail + StatusSource. No Qt -- this is what tests cover.
    window.py        pyqtgraph. The only module in the repo that imports Qt.
                     Left-drag on either panel zooms to exactly that rectangle;
                     that is ZoomViewBox, and `_span` (time, shared) and
                     `_ylim` (per panel) are what it sets.  The X/Y buttons
                     take an axis out of the drag.
    __main__.py      `python -m lschart.gui -c CONFIG` / `lschart-view`.
  app.py             Wires config -> transports -> instruments -> poller.
                     `controller_factory` / `response_factory` are the ltspm3 seams.
  __main__.py        CLI: run / probe / set / check / status / send / init.
  acquisition/       poller (owns the cycle), recorder (CSV, no row limit,
                     flushed per sample), ringbuffer (plotting only).
  tools/import_xls.py  Reads the legacy .xls logs. Sniffs the header:
                     filenames lie (cd10_..._st2_monitor3.xls is a 218 log).

ltspm3/                      LTSPM3 ONLY -- imports lschart, never the reverse
  thermal_response.py The one measured P(pct)/T(P) curve. Shared by the
                     simulator and the feedforward so they cannot drift.
  sim_response.py       Two-pole calibrated model + measured cross-channel coupling.
  config.py          The `control:` section; registers itself on import.
  app.py             build() -- the only module that knows both halves.
  __main__.py        Swaps one BUILDER; everything else is shared with lschart.
  control/           supervisor (the envelope -- read first), health, coherence,
                     pid, tuning, feedforward, ramp, filters, dither.
  tools/             replay.py (the only test on genuine data), steptest.py.

matlab/              LakeShore.m -- MATLAB's half of the file protocol, plus
                     selftest.m and a README. Not built; copied to the cryostat.
docs/                recorder/ (generic) and ltspm3/ (one cryostat). Keep them apart.
examples/            config-335-usb.yaml (coworker), config-336-usb.yaml (bench)
reference/           Legacy MATLAB + 24 .xls chart-recorder logs. Not built.
tests/               Generic. tests_ltspm3/ has the virtual-clock control harness.
```

## Conventions

**Vocabulary** — one concept, one word; the full table lives in
[`docs/style.md`](docs/style.md):

| Term | Means |
|---|---|
| **cryostat** | the physical setup; the calibrated one is the LTSPM3 |
| **recorder** | the process that owns the port, polls, writes the CSV |
| **viewer** | the strip-chart GUI process (`lschart-view`), separate process |
| **cycle** | one acquisition pass: read → apply commands → write status |
| **command spool** | the directory clients drop commands into |
| **instrument / driver / transport** | a box; the code behind it; how it is reached |
| **thermal response** | measured heater power → temperature behaviour |
| LTSPM vs **LTSPM3** | LTSPM is the team; LTSPM3 is the cryostat |

- Units are in the name: `_k` kelvin, `_pct` output percent, `_s` seconds.
- Time is `time.monotonic()` for every interval calculation and `time.time()`
  only for the log's absolute clock. Tests inject a `VirtualClock`.
- A per-channel failure marks that channel's `Reading`; only a link-level
  failure may raise.
- Filters are **dt-aware** (`alpha = 1 - exp(-dt/tau)`), never fixed-alpha —
  the bus jitters and a retry can cost a cycle.

## Running

```bash
uv venv --allow-existing .venv
uv pip install --python .venv/bin/python -e ".[dev,serial]"
.venv/bin/python -m pytest -q                          # ~343 tests

# generic recorder -- any cryostat, no control section in the config
.venv/bin/python -m lschart -c examples/config-336-usb.yaml probe   # read all, write nothing
.venv/bin/python -m lschart -c examples/config-336-usb.yaml run
.venv/bin/python -m lschart -c CONFIG set --loop 1 --setpoint 77     # instrument's own loop

# the file interface -- talking to a recorder that is ALREADY RUNNING
.venv/bin/python -m lschart.gui -c CONFIG                  # strip chart, separate process
.venv/bin/python -m lschart -c CONFIG status               # read status.json, exit 1 if stale
.venv/bin/python -m lschart -c CONFIG send ping            # prove the command path
.venv/bin/python -m lschart -c CONFIG send setpoint 77 --loop 1

# LTSPM3, software PID.  Same config file; `lschart` REFUSES it and says why.
.venv/bin/python -m ltspm3 -c config.yaml check
.venv/bin/python -m ltspm3 -c config.yaml run --arm
.venv/bin/python -m ltspm3.tools.replay "reference/logs/CD*/*.xls"
```

`probe` is the first thing to run against unfamiliar hardware: it forces every
transport read-only *regardless of the config*, so its safety does not depend on
the config file being right.
