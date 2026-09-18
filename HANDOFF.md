# Handoff — 2026-09-18, small hours (the judge can judge again, and the log can grade a move)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; **the
loop's requirements are [docs/ltspm3/requirements.md](docs/ltspm3/requirements.md)**,
the route is [PID_PLAN.md](PID_PLAN.md), and the commissioning step-by-step is
[plans/pid-4-commissioning.md](plans/pid-4-commissioning.md). This goes stale.

Previous: [archive/HANDOFF-2026-09-17d.md](archive/HANDOFF-2026-09-17d.md)
(the night: holding 125 K, and the viewer could finally see it).

> ## STATE: ARMED, tracking, holding 118 K. **The restart has NOT been done.**
>
> Jeff moved the setpoint 125 → 118 K at 23:52 and it settled at 23:56. The
> recorder has been up since 20:12 and is **still running the code it started
> from** — so none of tonight's four commits are in the running process, and
> the monitor's clock is still frozen.
>
> ```bash
> python -m lschart -c config-ltspm3-armed.yaml status
> ```
>
> **[§4](#4-what-is-waiting-for-a-restart) is the two commands that finish
> this**, and it is the whole of what is left. Everything below §4 is on
> `main` and on the main checkout's disk; nothing below §4 has reached the
> cryostat.
>
> Stopping is unchanged and always works:
> ```bash
> python -m lschart -c config-ltspm3-armed.yaml send hold
> ```

## 1. The 125 → 118 K move, graded

Jeff commanded it at 23:52. Graded from the recorder's CSV the way
[requirements.md](docs/ltspm3/requirements.md) §2 grades one, `t0` the output
step at 23:52:21:

| | measured | Jeff's bar |
|---|---|---|
| 95 % of the move | **126 s** | — |
| within 100 mK | 194 s | — |
| within 50 mK and staying | **242 s** (4.0 min) | inside 2–5 min |
| overshoot | **50 mK**, undershooting | up to 250 mK |

**Both bars met, and this is the second cryostat move on the shipped
numbers.** The bench arithmetic — about 40 s fixed plus 12 s per kelvin,
[requirements.md](docs/ltspm3/requirements.md) §3b — predicts 124 s to 95 % for
a 7.01 K move against the 126 s measured. **Two seconds.** Tonight's earlier
+5.07 K move came in 26 s *slower* than the same arithmetic predicted (127 s
against 101 s), so two moves now bracket it rather than confirm it, and the
larger move is the one that fits. Worth a third before anyone trusts the
formula.

Note the settle is slower than the +5 K move's (242 s against 193 s) while the
approach is faster. Nobody has looked at why.

**Why 118 K.** It is where the open-loop reference night sits (2026-09-15,
118.31 K at 64.007 %) and where every other number in the tree was measured —
the band, the Allan ladder, §3's whole bench table. The 125 K hold could not be
compared to any of them.

## 2. The monitor had three defects and they are fixed

All three were live, none is reachable from the archive replay, and the third
was found by reading the code to fix the second.
[plans/pid-2-monitor.md §2.6 and §2.7](plans/pid-2-monitor.md) carry the
detail.

**1. A recorder restart froze the clock.** Measured from
`data/plant_2026-09-17.csv`: it froze at **09:04:33**, the day's first restart,
and **26,314 of that day's 46,236 samples** carry the frozen stamp.
`plant.json` has been writing `t_s` **14.7 h behind `epoch`** every two
seconds.

The part worth carrying: **the freeze is silent for as long as nothing moves
the heater.** It froze at 09:04 and the judge went on reporting normally until
the 20:12 arming move latched the transient gate against a clock that could not
advance. Cause and symptom eleven hours apart, which is why four days of
`plant_*.csv` look healthy up to the point they suddenly do not.

`Time` is relative to the *process*, so a restart appends to the same daily file
with the column back at zero. The `max(t, self.last)` ratchet is right for a
daylight-saving fold across a *file* boundary and wrong for this: it pins `t_s`
at the highest value the file ever reached. The two are distinguishable — a
fold has a new file and a backward epoch, a restart a backward relative column
and a forward epoch — and `_Clock` now counts `restarts` beside `rewinds` so
the record says which.

**2. The headline said nothing.** `verdict` read `no opinion` on **every one of
46,236 samples** while `missing_power` read `typical` on 85 % of them, because
`tau` has no step to fit at a hold and its silence outranked four residuals
that had something to say.

**3. And it hid a warning.** The aggregate was over four of the six residuals —
`cold_head` and the latched `fault_level` were published as rows and were not
in the line at all. A compressor going off would have warned in its own row
while the summary read whatever the other four said. That is the unsafe
direction, and it had never been written down anywhere.

Asked, Jeff chose **the worst thing the judge actually knows**: the worst of the
residuals that *have* an opinion, over all six, with `verdict_for` naming them.
`no opinion` still wins when nothing can speak. `plant.json`'s schema goes to
**2** — the field is additive but the same cryostat in the same state now reads
`typical` where it read `no opinion`.

**Deliberately not changed:** a latched `fault_level` reads `warn` in the
headline, because `warn` is the most severe word this monitor has. A fourth
word would change every residual row, the viewer's palette and MATLAB's reader.
**That is a question for Jeff**, not a thing to decide while fixing something
else.

## 3. The CSV can grade a move now

Three columns beside `heater_pct`, filled off the controller by name and
defaulted so `lschart` still imports nothing from `ltspm3`:
`control.setpoint_k` (what it was chasing), `control.setpoint_target_k` (where
it was told to go), `control.filtered_k` (what the loop thought it was
reading — the error is against this, not the raw channel in the same row).

Until now a move had to be timed off the heater's own output step, which is
what §1's `t0` is and why that table is not a number anyone can check against a
command. The `Time` column's contract is written down at last, in
[file-interface.md](docs/recorder/file-interface.md) — it had no home anywhere
in `docs/recorder/`, and reading it as monotonic-per-file is what cost 26,314
samples.

**1400 tests and `ruff` clean**; the Qt half also under
`QT_QPA_PLATFORM=windows`. The archive replay is unmoved — 41 warn
transitions, 3 fault-level, the 09-10 event still at 11:46 and −5.08 mW. The
two clock tests and two of the headline tests were each run against the old
code and **fail there**, which is the only evidence that a regression test
regresses anything.

## 4. What is waiting for a restart

**The header changed, so the recorder will roll to a `_part2` file rather than
append** — which is the right thing here: a new file, a new header, a `Time`
column that restarts for a reason every consumer now handles.

Do it from `C:\Coding\Python\lakeshoreABJ`, which is already fast-forwarded to
`main` and has all four commits on disk. Ctrl-C the recorder's window rather
than killing it, so it logs its row count on the way out; `on_exit: hold`
leaves the heater exactly where it is either way.

```
python -m ltspm3 -c config-ltspm3-armed.yaml check
```
```
python -m ltspm3 -c config-ltspm3-armed.yaml run --arm --setpoint 118.0
```

`--setpoint 118.0` on purpose: with no setpoint it arms to whatever the sample
reads at that instant, and a chosen round number is what the night's
`control.setpoint_target_k` column should say.

Then the monitor, in its own window — **restart it after the recorder**, so it
opens the new file rather than following the old one across the roll:

```
python -m ltspm3.monitor -c config-ltspm3-armed.yaml
```

**What to check once both are up**, and each of these is a thing that has been
wrong before:

| | |
|---|---|
| `plant.json`'s `t_s` within a cycle of its `epoch` | it has been 14.7 h behind |
| `verdict` reading `typical`, with `verdict_for` naming three or four residuals | it has read `no opinion` for four days |
| the CSV's header carrying three `control.*` columns, non-empty | §3 |
| the viewer's detail panel showing numbers rather than `—` | the running process publishes schema 3's old block; a fresh one publishes 39 fields |
| `check`'s printed band bracketing the output the heater is holding | it was 64.08 % inside 62.96–64.96 at 00:24 |

**The monitor's baseline is young for six hours after any restart** — that is
what a restart costs now, instead of costing everything. Read the first six
hours of `plant_2026-09-18.csv` knowing it.

## 5. The night, and what it settles

**This is the measurement the last four handoffs have been waiting for**, and
it needs nothing but leaving the cryostat alone until morning.

```bash
python -m analysis.hold_quality --csv "data/ltspm3-armed_2026-09-1[78]*.csv" \
    --from 2026-09-18T00:30 --to 2026-09-18T12:00 \
    --vs "data/ltspm3-heater_2026-09-1[56].csv" \
    --vs-from 2026-09-15T18:00 --vs-to 2026-09-16T09:00
```

Matched length, and now matched temperature, which is the whole reason the
setpoint moved. **The bar to beat is the 2026-09-16 night on the weak hold:
5.6× worse than open loop at 39 min of averaging, bands 1.63 / 3.34 / 7.59.**
That night ran `hold_speed: 12`; this one runs 0.25, and
[requirements.md](docs/ltspm3/requirements.md) §3a's Allan table says the ratio
should be about 0.71 at 900 s. If it is not, the bench plant's white sensor
noise and absent slow disturbance is the first suspect and the tuning is the
second.

This is the **one ungraded row** in
[requirements.md](docs/ltspm3/requirements.md) §2 — Jeff's answer 2, the long
wander. Everything else in that table has a number against it.

**And 118 → 120 K in the morning is stage 4e's cryostat gate exactly**: a 2 K
move at 120 K, 95 % in about 60 s, within 50 mK inside 2–5 min. With §3's
columns in the log it will be gradeable against the command rather than off
the output step — the first move in this project that is.

## 6. The other open items

* **the diurnal cycle for phase 2's gate** starts when the fixed monitor does.
  It wants a day, not 72 h, and the reasoning is in
  [plans/pid-2-monitor.md](plans/pid-2-monitor.md)'s exit gate.
* **`cold_head` takes 616 s to recover at a daily file roll** and nobody knows
  why — two candidates, neither checked. A test that rolls a file under the
  judge is still unwritten; seven unattended days is seven midnights.
  pid-2-monitor §2.5.
* **a fourth word for a latched fault** — §2. Jeff's call.
* **the positional feedforward** still waits on a delivered-power gauge, and
  when it comes it needs `move_speed` re-graded against §3b rather than §3a.
* **10 K still takes 16 min for a 2 K move**, below `min_output_pct`, in a
  regime nobody has looked at. Not on Jeff's path.
* **requirements.md wants a §3c** once there is enough cryostat data to be the
  cryostat's record rather than one night's. Two graded moves now (§1 and
  09-17's +5.07 K) and a hold coming.
* **MATLAB `plant()`** is phase 5's last outstanding item, and it now has a
  `verdict_for` to read.
