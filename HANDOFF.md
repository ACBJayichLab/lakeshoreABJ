# Handoff — 2026-09-18 (the judge judges, the hold is graded, and the hold rule changed)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; **the
loop's requirements are [docs/ltspm3/requirements.md](docs/ltspm3/requirements.md)**,
the route is [PID_PLAN.md](PID_PLAN.md), and the commissioning step-by-step is
[plans/pid-4-commissioning.md](plans/pid-4-commissioning.md). This goes stale.

Previous: [archive/HANDOFF-2026-09-17d.md](archive/HANDOFF-2026-09-17d.md)
(the night: holding 125 K, and the viewer could finally see it).

> ## STATE: ARMED, tracking, holding 118 K on the restarted stack.
>
> Recorder, monitor and viewer were all restarted by Jeff at about 10:40 on
> 09-18, so **all of yesterday's commits are now in the running processes**.
> The recorder rolled to `ltspm3-armed_2026-09-18_part2.csv` because the header
> grew three columns, which is what a header change is supposed to do.
>
> ```bash
> python -m lschart -c config-ltspm3-armed.yaml status
> ```
>
> Verified live: `plant.json` is `"schema": 2`, `verdict: typical`,
> `verdict_for` naming five residuals, and `t_s` level with `epoch` **after a
> restart** — which is the thing that was broken. The CSV carries all four
> software-loop columns, populated.
>
> **The viewer wants one more restart** for the two fixes in
> [§4](#4-two-viewer-defects-the-screenshot-found). It costs nothing; it holds
> no port.
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
`plant.json` was writing `t_s` **14.7 h behind `epoch`** every two seconds.

**It healed itself at midnight and that is not a reprieve.** The daily file
roll gives the clock a new origin — `open_file` has always handled a new
file — so at 00:30 the unfixed process reads `t_s` level with `epoch` again.
The freeze lasts from a restart until the next midnight, and it will come
back the instant the recorder is restarted. Which is precisely what §4 does:
**restarting the recorder while the old monitor is running re-freezes it on
the spot.** Hence the order.

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

Caught in the act at 00:30, from the still-unfixed process, on a cryostat
behaving perfectly:

```
verdict: no opinion
  missing_power    typical
  coldplate        typical
  cold_head        typical
  tau              no opinion   no move to measure
  noise            typical
  fault_level      typical
```

**Five of six, and the headline is the one word anybody reads.**

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

**1402 tests and `ruff` clean**; the Qt half also under
`QT_QPA_PLATFORM=windows`. The archive replay is unmoved — 41 warn
transitions, 3 fault-level, the 09-10 event still at 11:46 and −5.08 mW. The
two clock tests and two of the headline tests were each run against the old
code and **fail there**, which is the only evidence that a regression test
regresses anything.

## 4. Two viewer defects the screenshot found

Jeff restarted the stack and sent a screenshot of the viewer against it, which
is the only way either of these was going to turn up. Both fixed, `5d8c13c`.
**The viewer needs restarting to pick them up; nothing else does.**

- **`control.filtered_k` was in the CSV and not on the chart** — a gap in §3's
  own change. `classify_column` routes a column to the kelvin axis, the percent
  axis, or *nowhere*; `control.setpoint_k` was drawn because it happens to
  contain `.setpoint`, and `control.filtered_k`, written by the same three
  lines, fell through to `other` and vanished in silence. Units-in-the-name now
  decides the axis when nothing else does — as a **suffix**, so `ls336.range1`
  is still not plotted.
- **`reading` was painted red on a healthy hold** — pre-existing, from the
  viewer work of 09-17. The panel marked `corroborated is False`
  unconditionally, but the guard only escalates on corroboration when the
  reading is also *slewing*. At a hold nothing moves, so nothing corroborates,
  and the mark was permanent while `health` read `ok`. A panel that is always
  red is one nobody reads, and this is the one that has to be believed. The
  mark now goes to the two fields that *are* verdicts, `validity` and `health`.

**One thing that looks alarming and is not.** The panel's slope read
**−95.3 mK/min**, which over the night would be 61 K. It is a short-window
regression on 14.9 mK of noise; the actual trend over those 14 minutes is
**+0.91 mK/min**. It puts about 1.4 mW into the premise residual (−3.86 mW
against σ 4.47, comfortably inside), so it is not a fault — but read it as
noise, not as a drift.

## 5. The hold, graded — and the rule it changed

**The measurement the last five handoffs were waiting for.** 10.5 h armed at
118.00 K, `tracking` on all 19,320 samples, no alarm and no note, mean
117.9985 K, sd 18.0 mK. Graded by `analysis/hold_quality.py` against a matched
open-loop window — same length *and* same clock hours, on a reference where
`ls218.aout1` held one value, 64.0070 %, for the whole of it.

**The loop does not improve the hold.** 1.57 / 1.97 / 1.56× worse by band,
worst 1.67× at τ = 1338 s, better only past an hour where `edf` is 3. The first
cut used mismatched clock hours and read 1.39 / 1.44 / 1.85×; **matching them
made it worse, which is how the diurnal explanation was ruled out.**

**The mechanism is the loop's own natural period, and it is not a defect.**
Ti = τ = 519.4 s exactly and Kp·K = 3.91, so `2π√(τ·Ti/(Kp·K))` is
**1651 s = 27.5 min** and an Allan deviation is most sensitive to it at
τ ≈ 825 s. The excess peaks at 1338 s. Integral action buys drift rejection at
long times — the armed curve falls to 1.4 mK at 2.1 h while open loop turns up
to 4.0 mK — and pays for it near the natural period.

**The bench could not have told us.** §3a predicted a ratio of 0.71 at 900 s;
the cryostat measured 1.60 at 748 s. The bench plant has white sensor noise and
no slow disturbance, which is the caveat `requirements.md` §2 has always
carried and which is now a measured fact.

**Shown the result, Jeff changed the requirement** —
[requirements.md §1c](docs/ltspm3/requirements.md), his words: *"Relaxing the
noise requirement is okay. We can change it to '1.1x or below 10 mK'"*, and
*"One rule for the whole curve"*.

**Against that rule the night PASSES at every averaging time**, because the
armed curve never exceeds **8.97 mK** anywhere. The 1.1× clause carries it to
74 s; the 10 mK clause carries it from 130 s to 40 min. `hold_quality.py`
grades this directly now and **says which clause carried it where**, because a
curve that passes only on the floor is a different animal from one that passes
on the ratio.

**He was told the cost before deciding**, and it is written into §1c: the
open-loop night never exceeds 8.98 mK either, so the 10 mK clause alone is met
by the cryostat with the heater parked. The rule does not distinguish a loop
that helps the hold from no loop at all. That is deliberate — 10 mK is at the
thermometer's own noise, and the loop is bought for the **move**.

**One rule now, and the old ones are gone.** `σ_y(τ) ≤ σ_y(10 s)` is retired
from `PID_PLAN.md` §3, `pid-4-commissioning` §4.3, `pid-3-loop` and
`analysis/allan.py`'s docstring: it compared a window to itself, so the
open-loop night failed it by 2.9× and the armed night by 1.05× while never
exceeding 9 mK. A bar the undriven cryostat fails is not measuring the loop.

## 6. Where this leaves the phases

**Phase 4's hold gate is met and stage 5 is the next thing.** What seven days
adds is not the pass — that is done — but the `edf`: the 09-18 night runs out
of independent samples at about an hour, which is exactly where the interesting
part of the comparison begins. That is now evidence rather than a gate.

**Stage 4e's cryostat gate is still open**: a 2 K move at 120 K, which from
118 K is one command, and it is the first move that will be gradeable from the
log against the command that caused it rather than off the output step.

## 7. The other open items

* **the diurnal cycle for phase 2's gate** is running from the 10:40 restart.
  Read the first six hours knowing the monitor's baseline is young — that is
  what a restart costs now, instead of costing everything.
* **`cold_head` takes ~10 min to recover at a file roll**, seen twice now:
  616 s on 09-15 and again after the 10:40 roll on 09-18, both lining up with
  `stage_baseline_tau_s: 600`. Still undiagnosed, and seven unattended days is
  seven midnights. pid-2-monitor §2.5.
* **a fourth word for a latched fault** — §2. Jeff's call.
* **the 27.5 min resonance** is understood but untouched. Lowering it means a
  slower integral or a smaller Kp, bought with weaker drift rejection. Not
  required any more; worth knowing if the hold ever needs to be better rather
  than merely good enough.
* **a third move** before anybody trusts §3b's 40 s + 12 s/K arithmetic — two
  points bracket it (26 s slow, 2 s fast) rather than confirm it.
* **the positional feedforward** still waits on a delivered-power gauge.
* **10 K still takes 16 min for a 2 K move**, below `min_output_pct`. Not on
  Jeff's path.
* **MATLAB `plant()`** is phase 5's last outstanding item, and it now has a
  `verdict_for` to read.
