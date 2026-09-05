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
| [`docs/ltspm3/`](docs/ltspm3/) | **LTSPM3 only.** [cryostat](docs/ltspm3/cryostat.md) · [safety](docs/ltspm3/safety.md) · [thermal response](docs/ltspm3/thermal-response.md) · [control](docs/ltspm3/control.md) · [running](docs/ltspm3/running.md) · [commissioning](docs/ltspm3/commissioning.md) |
| [`matlab/README.md`](matlab/README.md) | MATLAB's half of the file protocol |
| [`README.md`](README.md) | The front door, for a new user |
| [`HANDOFF.md`](HANDOFF.md) | Point-in-time status. Goes stale by design |

**Keep the split when you write.** Anything true of any Lake Shore cryostat belongs
in `docs/recorder/`; anything calibrated to LTSPM3 belongs in `docs/ltspm3/`. A
generic document that mentions THE CHONKE is in the wrong file.

## Priorities (Jeff, 2026-08-24)

**The viewer and the MATLAB interface are the priority. The software PID is not.**

`ltspm3` is complete and tested and should be left alone unless it breaks. New
effort goes to `lschart`: the strip-chart viewer, the MATLAB file interface,
and Windows deployment. Read that as a standing instruction, not a phase
ordering — resist "while I am in here" improvements to `control/`.

The viewer and the MATLAB interface now exist and are exercised end to end (the
MATLAB half against a real MATLAB R2025b, the viewer against a live recorder).
**Windows deployment is what is left.** The test suite now runs there on every
push — see [install](docs/recorder/install.md#continuous-integration) — which
is not the same thing as the deployment being tested: CI proves the code runs
on Windows, not that a recorder survives a week on the cryostat's own machine
with its own COM port, its own drivers and its own power management.

## The invariants

These are the things a change must not break. The reasoning behind each one is
in the linked document.

1. **`lschart` never imports `ltspm3`.** If you find yourself wanting it to, the
   design is wrong, not the rule.
2. **The recorder owns the port, exclusively.** A COM port has exactly one
   holder; two processes on one GPIB board garble replies. Everything else goes
   through files. → [file-interface](docs/recorder/file-interface.md)
3. **Seven write interlocks, all off by default**: `transport.read_only` (byte
   level) · `allow_writes` (driver policy) · `ipc.accept_commands` ·
   `ipc.allow_heater_range` (a 33x range) · `ipc.allow_analog_output` (a 218
   analog output) · `ipc.allow_pid` (retuning a loop) · `ipc.sources` (**which
   client** may ask, narrowed at runtime by `sources.json` and never widened).
   A command arriving by file passes exactly the gates a command typed at the
   CLI passes. The two power gates are separate on purpose: different commands,
   different boxes, and a cryostat usually wants one open and not the other.
   **Every gate applies in both directions** — commanding a range or an output
   to *zero* needs the same permission as raising it, because cutting a heater
   stops heating and can also crash the stage. The only exemptions anywhere are
   the panic kinds `heaters_off` and `hold`, which bypass the source policy and
   the two power gates and nothing else; the exemption belongs to the command
   kind, so MATLAB gets it too.
   → [instruments](docs/recorder/instruments.md) ·
   [file-interface](docs/recorder/file-interface.md)
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
                     `OUTMODE?` on a slow cadence says which input each loop
                     reads and whether it is closed loop -- the instrument's
                     answer, never a map kept in config.
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
                     SCHEMA_VERSION 2 adds `links[].loops`, the loop table.
    commands.py      The maildir-style command spool. Ordering, expiry,
                     acknowledgement, and clock-skew refusal.
    service.py       Joins the two onto the acquisition cycle, on the
                     acquisition thread, because that thread owns the bus.
  gui/               The strip chart. A SEPARATE PROCESS, not a thread.
    source.py        CsvTail + StatusSource, plus the arithmetic the window is
                     not allowed to hold: region statistics, hover lookup, the
                     region export, and the table projections.  `reading_rows`
                     is the join behind the ONE table -- every thermometer is a
                     row and its loop is a set of columns on it, which is what
                     keeps an 8-input 218 from collapsing to however many loops
                     it has.  No Qt -- this is what the tests cover.
    theme.py         Colours, resolved from the Qt PALETTE at call time.  Never
                     paint the normal case: ordinary text has no colour of its
                     own, and a hardcoded black is a bug on a dark desktop
                     exactly as a hardcoded white is on a light one.  Every
                     exceptional pair is contrast-checked by
                     tests/test_gui_theme.py rather than by eye.
    window.py        pyqtgraph, and everything else that draws.
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
  tools/xls_to_csv.py  Legacy .xls -> the recorder's OWN CSV, so a year-old
                     cooldown reads like today's. Merges the 336 by wall clock
                     (two programs, two files, no shared row index) and
                     reconstructs ls218.aout1 as a zero-order hold on the
                     Notes column's ANALOG commands -- the 218 never logged
                     its own output.
  tools/fit_table.py   Recorder CSVs -> ONE table a fitter loads: Timestamp,
                     t_s, segment, the thermometers, u_pct, note. `segment`
                     increments at every recording gap and is the column that
                     matters -- CD10 has a 65 h and a 187 h hole, and an ODE
                     integrated through one of those converges on a number
                     anyway. Aux readbacks dropped; --rename folds a
                     relabelled channel (Cold Head -> Coldplate, 08-26).

ltspm3/                      LTSPM3 ONLY -- imports lschart, never the reverse
  thermal_response.py The one measured P(pct)/T(P) curve. Shared by the
                     simulator and the feedforward so they cannot drift.
  sim_response.py       Two-pole calibrated model + measured cross-channel coupling.
  config.py          The `control:` section; registers itself on import.
  app.py             build() -- the only module that knows both halves.
  __main__.py        Swaps one BUILDER; everything else is shared with lschart.
  control/           supervisor (the envelope -- read first), health, coherence,
                     pid, tuning, feedforward, ramp, filters, dither.
                     `panic_hold()`/`panic_off()`/`arm()`/`acknowledge()` are
                     the only METHODS lschart calls
                     here; `status.py` also READS `band` and `cfg.max_error_k`
                     for the status file's `control` block.  All of it
                     duck-typed by name and defaulted, so invariant 1 holds --
                     and pinned against a real supervisor by
                     tests_ltspm3/test_status_projection.py, because a rename
                     up here would otherwise leave a status file that still
                     parses and is quietly full of nulls.
  tools/             replay.py (the only test on genuine data), steptest.py.

matlab/              LakeShore.m -- MATLAB's half of the file protocol, plus
                     selftest.m and a README. Not built; copied to the cryostat.
.github/workflows/   tests.yml: lint, then the suite, on Linux/Windows/macOS x
                     py3.11/3.13.  A SKIPPED TEST FAILS THE BUILD -- every
                     remaining skip is conditional on something CI provides.
docs/                recorder/ (generic) and ltspm3/ (one cryostat). Keep them apart.
examples/            config-335-usb.yaml (coworker), config-336-usb.yaml (bench)
reference/           Legacy MATLAB + 24 .xls chart-recorder logs. Not built.
data/                GITIGNORED. The recorder writes here; nothing in it is
                     versioned, so NONE OF IT EXISTS ON A FRESH CLONE.
                     Two derived sets, made by the two tools above:
                       data/cd10/                       CD10 as recorder CSV,
                         28 daily files -- this is what the VIEWER opens
                         (-o defaults here):
                         python -m lschart.tools.xls_to_csv "reference/logs/CD10/*.xls"
                       data/heater calibration steps/   what the FITS read:
                         fit_cd10.csv, fit_recorder.csv, plus Jeff's region
                         exports. See tools/fit_table.py for the two commands.
                     Which logs are usable for what is in
                     docs/ltspm3/thermal-response.md.
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
- `ruff check .` must pass; CI gates on it. The rule set is deliberately narrow
  (`F`, `E9`, `E501` at 100 columns) — it catches dead imports and unused
  locals, and leaves the house layout alone.
- **A test must not depend on the working directory or on the time of day.**
  Both have bitten: relative paths made seven tests vanish outside the repo
  root, announcing themselves as "reference logs not present", and a viewer
  test anchored to "today at noon" passed every morning and failed every
  evening.

## Running

```bash
uv venv --allow-existing .venv
uv pip install --python .venv/bin/python -e ".[dev,serial]"
.venv/bin/python -m pytest -q                          # the whole suite
.venv/bin/python -m ruff check .                       # gated in CI

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
