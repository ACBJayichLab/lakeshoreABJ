# Handoff — 2026-08-27 (eighth session: phases 1 and 2 of the feature plan land)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; this goes stale.

**Phases 1 and 2 of [`FEATURE_PLAN.md`](FEATURE_PLAN.md) are implemented, tested and
exercised against the bench 336 on USB. Phase 3 — the write path — is deliberately
not started. 472 tests passing (from 408), ruff clean.**

## Start here

`FEATURE_PLAN.md` now carries a phase table at the top saying what is done. **Phase 3
is next**: A1 → A2 → P1 (with S3, S4) → K1, K2, K3, K4 → A3 (with S6). Every item in
it touches the write path, and A3 removes the last exemption that is not a panic
action. Read invariants 3, 4 and 5 in `CLAUDE.md` first — invariant 3's last sentence
is one of the things A3 makes false.

## What landed

### Phase 1 — the viewer, alone (commit `f2efe07`)

- **V1** the `View` row is live-referenced windows and nothing else, opening on 24 h.
  `All` is gone: "whatever this viewer happens to hold" is a different span on a
  viewer opened an hour ago and one left up since Tuesday.
- **V2** a comfort stop on the value axis, 0–450 K and 0–100 %, **widened to the data
  wherever the data goes outside it**. `--max-kelvin` / `--max-percent`.
- **C1** two cursors and, between them, mean / sd / Δvalue per trace plus Δt once.
  From full-resolution samples (`CsvTail.samples_in`) — memory while nothing has been
  thinned, the logs once something has. While the cursors are up the left button
  places them instead of drawing a zoom rectangle. With no cursors the legend carries
  the live value; with cursors it goes back to names.
- **C2** hover names the nearest trace and reads it there, interpolated at the pointer.
- **E1** `Export region…` writes the cursor region out as a CSV, every column the log
  carries.

### Phase 2 — the loop table (this commit)

- **S2** `OUTMODE?` and `RAMPST?` per loop on a slow cadence (`read_loops`,
  `loop_every_n_cycles`), cached on the driver and emitted into aux every frame.
  `Sim33x` answers both, so the sim-backed tests cover the path.
- **S1** `links[].loops` in `status.json`, an array of uniform objects.
  `SCHEMA_VERSION` → 2. The plain loop-number list moved to `links[].loop_numbers` —
  one key cannot be two shapes.
- **S5** `loop_thresholds: {loop: kelvin}` per instrument, republished in S1.
- **L1/L2/L3/R1** a loop table under the readouts (not instead of them). Clicking a
  row selects instrument *and* loop; the loop spin box and the heater-output combo
  are gone. A 336 loop 3/4 shows `n/a` for range and hides the range control.
- **W1/W2** two separate marks, `Rail` and `Off SP`, both suppressed while the loop is
  not trying.

## Two things worth knowing before touching this

**The bench 336's loops 3 and 4 sit with both marks lit, and that is correct.**
They are in closed loop asking for 276 K while the stage is at 295.7 K with the
output pinned at 0 %, and — unlike loops 1 and 2 — they have no heater range to be
switched off by, so none of W1's suppression conditions apply. It is literally true
(a loop asking for a temperature it cannot reach, with no authority left) and it
follows the spec's suppression list exactly. If it turns out to read as noise on the
real cryostat, the place to change it is `loop_marks` in `lschart/gui/source.py`, and
the change is a spec decision rather than a bug fix.

**The example 336 configs now set `read_analog_outputs: true`.** Without it the loop
table's output column is blank for loops 3 and 4, because `AOUT?` is the only thing
that says what they are doing. Costs two transactions per cycle. The budget for
`config-336-usb-writable.yaml` is now 23 transactions ≈ 1.15 s against a 2 s cadence.

## What was verified against hardware

The bench 336 (`LSA26E0`, USB, `examples/config-336-usb-writable.yaml`), **read path
only — nothing was written to the instrument and every heater range stayed 0
throughout.**

- `probe` clean; all four inputs at ambient, all ranges 0.
- The recorder publishes the loop table from the box's own `OUTMODE?`: loops 1–4 on
  inputs A–D, all closed loop, loops 3/4 with `heater_output: null`.
- The viewer draws it, the row selection drives the whole command panel, and
  selecting loop 3 hides the range control and says why.
- **MATLAB R2025b ran `selftest.m` against the live recorder** and read the new loop
  table through `ls.loops('ls336')` — sensor names with spaces ("Rad Shield") survive
  verbatim, which is the whole reason the shape is an array of objects.

Two defects the hardware run found and this commit fixes: the loop table was sized
before its cells were filled (the last loop sat behind a scrollbar) and eight
full-width headings made it scroll sideways in a 380 px panel.

## State of the tree

- `472 passed`, `ruff` clean, on `Feature-Implementation`.
- **No recorder is running.** The one this session started was stopped cleanly, so
  `data/status.json` carries `running: false` and every heater range in it is 0.
  Bring it back with:

  ```
  .venv/bin/python -m lschart -c examples/config-336-usb-writable.yaml run
  ```

  `data/ls336.lock` is left behind by a kill rather than a clean stop; the lock
  module checks whether the pid is alive, so a stale one does not block a restart.
