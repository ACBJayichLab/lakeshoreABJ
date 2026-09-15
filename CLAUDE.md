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
| [`docs/ltspm3/`](docs/ltspm3/) | **LTSPM3 only.** [cryostat](docs/ltspm3/cryostat.md) · [safety](docs/ltspm3/safety.md) · [thermal response](docs/ltspm3/thermal-response.md) · [control](docs/ltspm3/control.md) · [running](docs/ltspm3/running.md) · [noise](docs/ltspm3/noise.md) |
| [`matlab/README.md`](matlab/README.md) | MATLAB's half of the file protocol |
| [`README.md`](README.md) | The front door, for a new user |
| [`HANDOFF.md`](HANDOFF.md) | Point-in-time status. Goes stale by design; older ones are archived under their own date |
| [`PID_PLAN.md`](PID_PLAN.md) | The route from here to a working software PID: Jeff's requirements, the software model (where `model/`, `control/` and the monitor live), the **typical band** in watts, and a phase index with one exit gate each. The phases with real work have their own documents under [`plans/`](plans/). [`REFIT_PLAN.md`](REFIT_PLAN.md) is the thermal model it stands on |
| `AUDIT-*.md` | Point-in-time audits, findings with nothing fixed. `AUDIT-2026-09-10-REPLY.md` is the argument for the two parts of that one's finding 2 that were deliberately NOT applied; `AUDIT-2026-09-10-REJOINDER.md` concedes both, and says the live sweep tool is where the fix still has to land |

**Keep the split when you write.** Anything true of any Lake Shore cryostat belongs
in `docs/recorder/`; anything calibrated to LTSPM3 belongs in `docs/ltspm3/`. A
generic document that mentions THE CHONKE is in the wrong file.

## Priorities (Jeff, 2026-09-11)

**One program, two halves: a person watching their cryostat, and a safe
software PID.** Neither outranks the other any more.

Until 2026-09-11 this section said the software PID was complete and off
limits and that the viewer, MATLAB and Windows deployment came first. The
viewer and the MATLAB interface exist and are exercised end to end; the
recorder has run on the cryostat's own Windows machine since 2026-08-24. The
monitoring half is in service and stays in service.

The software PID has never closed a loop on the cryostat, and the numbers it
would run on have been contradicted by the September characterisation.
[`PID_PLAN.md`](PID_PLAN.md) is the route from here to a working one, and it
records Jeff's requirements of 2026-09-11: 4 to 300 K, 5 K/min, one rate
limit, warn at a kelvin and fault at five, a 20 s filter, graceful failure,
and the thermal model used to say whether the cryostat is behaving typically.

**`ltspm3/control/` is therefore open to change — under the eight rules of
[safety](docs/ltspm3/safety.md), one rule-scoped commit at a time, each
reviewed against the rule it touches.** The instinct the old instruction
protected is still right: no "while I am in here" changes, and nothing lands
in `control/` without a test on the virtual-clock harness and a line in
`PID_PLAN.md` saying which step it is.

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
                     `note` is the odd command out: it writes a line into the
                     CSV's Notes column and touches no instrument, so it has no
                     power gate -- but it still passes `accept_commands` and the
                     source policy, and it is NOT a panic kind.  It lands on the
                     NEXT row, because the cycle writes the CSV before it drains
                     the spool.  Nothing else in the recorder records what a
                     PERSON did, and the column was empty across both September
                     2026 events.
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
  tools/noisespec.py   WHERE a channel's noise lives, and therefore whether a
                     filter can touch it.  Bands, a decimate-vs-average test,
                     and the attenuation a single-pole tau actually achieves
                     MEASURED against the sqrt(dt/2tau) a white-noise model
                     promises.  The gap between those two columns is the whole
                     point: 14 mK of hash and 14 mK of slow wander are the same
                     rms and opposite problems.  -> docs/ltspm3/noise.md
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
  tools/recalibrate.py  Re-reads a log through a different sensor curve, when
                     the box turns out to have had the wrong one loaded:
                     kelvin -> resistance -> kelvin through two .340 breakpoint
                     tables, which is exact because it is the instrument's own
                     interpolation run backwards and then forwards. REFUSES to
                     invert a RAILED reading -- past the end of the loaded table
                     the box clamped and the resistance is gone, and the
                     plausible-looking number you get from inverting the clamp
                     is the worst possible output. Never writes over its input.
                     Used once, for the Coldplate; see reference/sensor-curves/.

ltspm3/                      LTSPM3 ONLY -- imports lschart, never the reverse
  model/             THE CHARACTERISATION -- every number that describes the
                     cryostat, in one package so "the model" has an address.
                     Read by control/ (feedforward, schedule), the simulator,
                     the tools and the monitor; written only by analysis/.
                     Stdlib only.  -> PID_PLAN.md section 2
    thermal_response.py The one measured P(pct)/T(P) curve. Shared by the
                     simulator and the feedforward so they cannot drift.
    sim_response.py  Two-pole calibrated model + measured cross-channel coupling.
                     ONE tau, 620 s, from a single step at 137 K. Right there and
                     wrong elsewhere; the control harness is calibrated to it.
    fitted_response.py Same seam, driven by the ODE fitted to the 43 h sweep:
                     C(T) dT/dt = Q(u) - [Lambda(T) - Lambda(T_c)], so tau runs
                     from under a second at 10 K to about 580 s at 137 K and the
                     steady state is the measured one. The curves come frozen
                     in _fitted_table.py (GENERATED by
                     analysis/export_response.py; regenerate after a refit).
                     What `sweep --simulate` rehearses against.
                     REFITTED 2026-09-13 and 4-5 K WARMER at a given output than
                     every number written before that date: 60.597% read 70.0 K
                     and reads 75.09. **Its LEVEL is a calibration with a shelf
                     life** -- the header carries a delivered-power gauge and
                     the day it was measured, because handling the heater wiring
                     moves it by up to 0.8%, which is 3 K at 118 K. The SHAPE
                     does not expire. -> REFIT_PLAN.md 7.3
                     Also THE RESIDUAL AND ITS BAND, 2026-09-14:
                     `missing_power_w` is what the monitor and the supervisor
                     judge the cryostat by, and `sigma_q_w` is how wrong it is
                     allowed to be -- ONE source, so the two cannot disagree
                     about what typical means. The band's six terms are frozen
                     into _fitted_table.py beside the curves.
                     **`bias_q_w` is deliberately NOT in the band**: the 0.7%
                     the heater circuit may not deliver is a constant that moves
                     when somebody handles the wiring, and in the band it would
                     make 3 sigma at 118 K 14 mW -- three times the 09-10 fault
                     the monitor has to catch. -> PID_PLAN.md 3
  config.py          The `control:` section; registers itself on import.
  app.py             build() -- the only module that knows both halves.
  __main__.py        Swaps one BUILDER; everything else is shared with lschart.
  monitor/           THE JUDGE, and a SEPARATE PROCESS -- no port, no commands,
                     ever.  Runs armed or not, which is most of this cryostat's
                     life so far.  `judge.py` is where the reasoning is; read it
                     first.  It alarms on the CHANGE in the residual against a
                     slow baseline, not on its level, because the level carries
                     the calibration and the unmodelled drift -- and Jeff
                     recalibrates once per cooldown (2026-09-14), over which the
                     full band would reach 31 K.  **The baseline is a FRACTION
                     OF DELIVERED POWER**, which is what a series resistance in
                     a voltage-driven heater is; in watts or in kelvin the same
                     1% calibration error is a different number at 40 K and at
                     140 K and the baseline chases the sweep instead.  It
                     FREEZES whenever the verdict is not typical, or it learns
                     the fault it is judging.  source.py reads a live recorder
                     CSV or the archive; report.py writes plant.json and a daily
                     plant_*.csv.  -> plans/pid-2-monitor.md
  control/           supervisor (the envelope -- read first), health, coherence,
                     pid, tuning, feedforward, ramp, filters, dither.
                     `panic_hold()`/`panic_off()`/`arm()`/`acknowledge()` are
                     the only METHODS lschart calls
                     here; `status.py` also READS `band` and `cfg.warn_error_k`
                     for the status file's `control` block.  All of it
                     duck-typed by name and defaulted, so invariant 1 holds --
                     and pinned against a real supervisor by
                     tests_ltspm3/test_status_projection.py, because a rename
                     up here would otherwise leave a status file that still
                     parses and is quietly full of nulls.
  tools/             replay.py (the only test on genuine data), steptest.py,
                     sweep.py -- drives a RUNNING recorder up a ladder of heater
                     outputs through the command spool, holding each rung until
                     it would grade by analysis/steps.py's rule rather than for
                     a fixed time.  tau spans 1 s to 500 s over 5-110 K, so a
                     fixed dwell is wrong at both ends.  --simulate rehearses
                     the whole procedure on a virtual clock.  The ladder comes
                     from analysis/plan_sweep.py.

matlab/              LakeShore.m -- MATLAB's half of the file protocol, plus
                     selftest.m and a README. Not built; copied to the cryostat.
.github/workflows/   tests.yml: lint, then the suite, on Linux/Windows/macOS x
                     py3.11/3.13.  A SKIPPED TEST FAILS THE BUILD -- every
                     remaining skip is conditional on something CI provides.
docs/                recorder/ (generic) and ltspm3/ (one cryostat). Keep them apart.
examples/            config-335-usb.yaml (coworker), config-336-usb.yaml (bench)
reference/           Legacy MATLAB + 24 .xls chart-recorder logs, the 218 /
                     335 / 336 vendor manuals, cooldown-10/ -- the ARCHIVE
                     analysis/ fits: three non-overlapping tables that are the
                     whole cooldown plus segments.csv, the manifest naming
                     every window in them, which is the dataset and is reviewed
                     as a diff (analysis/curate.py --propose) -- and
                     sensor-curves/, the vendor .340 breakpoint tables. A log
                     is meaningless
                     without knowing which curve was loaded when it was
                     written, and the 218 had the WRONG ONE on input 2 until
                     2026-09-04: X186276's, where the Coldplate is X186279.
                     Not built. Nothing here regenerates, which is why all of
                     it is in-repo despite the size; see analysis/README.md for
                     why the fit inputs break the derived-data-is-gitignored
                     rule.
data/                GITIGNORED. The recorder writes here; nothing in it is
                     versioned, so NONE OF IT EXISTS ON A FRESH CLONE.
                     Two derived sets, made by the two tools above:
                       data/cd10/                       CD10 as recorder CSV,
                         28 daily files -- this is what the VIEWER opens
                         (-o defaults here):
                         python -m lschart.tools.xls_to_csv "reference/logs/CD10/*.xls"
                       data/heater calibration steps/   the WORKING copies
                         of the pre-archive fit inputs. SUPERSEDED: analysis/
                         reads reference/cooldown-10/ now, and the five
                         overlapping tables these were copies of are deleted.
                         See tools/fit_table.py for how they are made.
                       data/coldplate-recal/             every pre-cutover log
                         re-read on the right Coldplate curve -- cd10/,
                         recorder/ and fit-inputs/. THIS is what to open for
                         anything before 2026-09-04 12:07. Its README.txt has
                         the commands. Made by tools/recalibrate.py.
                     Which logs are usable for what is in
                     docs/ltspm3/thermal-response.md.
tests/               Generic. tests_ltspm3/ has the virtual-clock control harness.
analysis/            EXPLORATORY, not shipped. Fits the thermal model from the
                     logs: Lambda(T) the conductance integral, C(T) the heat
                     capacity, and what they imply for gain, tau and settling.
                     Imports NEITHER package and is imported by neither, so it
                     cannot bend invariant 1 -- it reads CSVs and nothing else.
                     `pip install -e ".[analysis]"` for scipy/matplotlib -- the
                     recorder needs neither. Its inputs ARE versioned, in
                     reference/cooldown-10/, so every step runs from a fresh
                     clone; only the outputs are gitignored. Read
                     analysis/README.md first for the caveats.
                     segments.py    the archive and the manifest: read_table
                       for a whole table, load(id) for one named window.
                     curate.py      proposes the manifest and DIFFS a proposal
                       against the committed one. CURATE, DO NOT DISCOVER --
                       the dataset is a committed file, not something a
                       heuristic rediscovers per run, so a change to the dwell
                       finder or its constants arrives as a diff somebody has
                       to look at. `--propose` prints none on a clean tree.
                     holdout.py     REFIT_PLAN section 1's scoreboard and its
                       gate. `--in-epoch` is the one that matters: fit ONE
                       delivered-power gauge on the ladder, then PREDICT the
                       three long holds from it -- disjoint anchors, one
                       undisturbed epoch. That is what is left to ask once a
                       reseated wire can move the level by 3 K.
                     plot_tau.py    which relaxations a single pole can
                       describe, drawn. Two of eleven cannot, and neither is a
                       model error.
                     measure.py     measures the windows the manifest names and
                       writes measured.csv, WHICH IS WHAT EVERY FIT READS. A
                       jump keeps fit_pole's numbers; a hold gets level, drift,
                       relaxation and a 24 h harmonic, because a single pole on
                       a settled hold fits the cryostat's drift as a relaxation
                       and put four graded anchors 0.4-1.7 K out. Every row
                       carries an error bar with the residual's autocorrelation
                       in it and the measured long-term fluctuation under it.
                       `--verify` is REFIT_PLAN.md 6's exit gate.
                     band.py        HOW WRONG THE RESIDUAL IS ALLOWED TO BE.
                       Measures the six terms of sigma_Q from the same archive
                       the curves were fitted to; export_response freezes them
                       into the shipped table. Two error bars and keeping them
                       apart is the whole design: a NOISE band, which is what
                       dQ does while the cryostat behaves, and a CALIBRATION
                       offset, which is where the level may sit. Also the
                       phase-1 gate -- dQ at every settled anchor, in-epoch and
                       across the campaign, in mW and in K, because a
                       milliwatt gate is a kelvin gate divided by Lambda' and
                       Lambda' runs 24 mW/K at 10 K against 1.7 at 118 K.
                     allan.py       THE HOLD'S FIGURE OF MERIT, and a different
                       question from measure.py's: not where a window was
                       heading but whether averaging for longer HELPS.  Those
                       come apart exactly where it matters -- a stretch can be
                       quiet at ten seconds and wander at an hour, and the rms
                       over the window is the same number for both.  Open loop
                       at 118 K it floors at 7.38 mK at tau = 130 s and rises
                       after: AVERAGING STOPS HELPING AT TWO MINUTES, where
                       the old 1/sqrt(N) prediction had it still
                       improving at ten.  Validated against white noise, a
                       linear drift and a sine.  -> PID_PLAN.md section 1
```

## Conventions

**Vocabulary** — one concept, one word; the full table lives in
[`docs/style.md`](docs/style.md):

| Term | Means |
|---|---|
| **cryostat** | the physical setup; the calibrated one is the LTSPM3 |
| **recorder** | the process that owns the port, polls, writes the CSV |
| **viewer** | the strip-chart GUI process (`lschart-view`), separate process |
| **monitor** | the judge (`python -m ltspm3.monitor`), separate process; reports, never commands |
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
