# Handoff — 2026-09-17, night (the loop is holding 125 K, and the viewer can finally see it)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; **the
loop's requirements are [docs/ltspm3/requirements.md](docs/ltspm3/requirements.md)**,
the route is [PID_PLAN.md](PID_PLAN.md), and the commissioning step-by-step is
[plans/pid-4-commissioning.md](plans/pid-4-commissioning.md). This goes stale.

Previous: [archive/HANDOFF-2026-09-17c.md](archive/HANDOFF-2026-09-17c.md)
(the evening: five minutes was too generous, and going fast found three bugs).

> ## STATE: ARMED, tracking, holding 125 K on the numbers in this tree.
>
> The recorder was **restarted at 20:12**, so it read this tree's
> `config-ltspm3-armed.yaml` — `hold_speed: 0.25`, `move_speed: 0.03`,
> `max_output_rate_pct_per_min: 20`, `authority_pct: 1.0`. The two previous
> handoffs' "armed on the morning's numbers, nothing here reaches the
> cryostat" caveat is spent.
>
> ```bash
> python -m lschart -c config-ltspm3-armed.yaml status
> ```
>
> **What is NOT running is the viewer work below.** It is eight commits on a
> branch, and the recorder does not need restarting for most of it — the
> viewer is a separate process reading files. One part does; see
> [§3](#3-one-thing-wants-a-restart-and-it-can-wait).
>
> Stopping is unchanged and always works:
> ```bash
> python -m lschart -c config-ltspm3-armed.yaml send hold
> ```

## 1. What the cryostat did tonight

**This is the first run on the shipped numbers**, and it is the measurement
the last three handoffs were waiting for. From the recorder's own CSV,
`data/ltspm3-armed_2026-09-17.csv`:

| | |
|---|---|
| armed | 20:12, at about 130 K |
| moved | down to 120 K, then **+5.07 K to 125 K** |
| holding | 125 K since 20:34 — **2.0 h** by the time this was written |

**The +5 K move, graded the way [requirements.md](docs/ltspm3/requirements.md)
§2 grades one.** `t0` is the output step at 20:31:27 rather than a command time,
because the recorder's CSV does not carry the software loop's setpoint — see
[§4](#4-two-open-items-and-one-new-one):

| | measured | Jeff's bar |
|---|---|---|
| 95 % of the move | **127 s** | — |
| within 100 mK | 173 s | — |
| within 50 mK | **193 s** (3.2 min) | inside 2–5 min |
| overshoot | **+70 mK** | up to 250 mK |

Both requirements met, the second by more than three times over. The bench
arithmetic for a move this size — about 40 s fixed plus 12 s per kelvin, which
is §3b's — predicts 101 s to 95 %; the cryostat took 127 s. **26 s slower than
the rehearsal and the right side of every gate**, which is the first real
number anyone has for how well that arithmetic travels.

**The hold, over 2.05 h at 125 K:** 20.3 mK rms about the setpoint, worst
excursion 90 mK, mean 125.003 K. The output sits near 64.6 %, writing on about
two cycles in three, moving some three DAC codes at a time and wandering over
0.18 % — about 60 codes — across the two hours. That is a loop working, not a
loop dithering: 0.18 % of authority is what holding 125 K to 20 mK cost.

**What this does not yet say** is the thing the hold is *for*: slow wander at
long averaging times. Two hours cannot answer it and neither can the bench, whose
plant has white sensor noise and no slow disturbance. That is still
`analysis/hold_quality.py` against the 2026-09-15 open-loop night — see §4.

These numbers have no durable home yet. If they are to become the cryostat's
record rather than tonight's, they belong in `requirements.md` §3 beside the
bench's, and §3b's "nothing here has run on the cryostat yet" wants striking
when they go in.

## 2. The viewer, which was blind to all of this

Eight commits, not on `main` yet. The loop had been retuned twice and the
viewer reflected none of it: the software loop was one table row, and the one
client that could not move its setpoint was the one open while somebody types
temperatures.

| | |
|---|---|
| **the selector lists loops and outputs, not boxes** | `ls336 loop 1`…`loop 4`, `ls218 analog 1`, `software loop`. One `_target`, with the dropdown and the reading table as two *views* of it — so the software row is clickable now, and a read-only box's loops are not |
| **the software loop takes a setpoint here** | kelvin and an optional rate, ramped by the supervisor. Its gate is the **opposite** of the manual output's: ownership disables that one and enables this one |
| **a detail panel** | what the loop is reading, asked → allowed → written, the band inside its envelope, and the watt residual. Every mark on it is a field the supervisor publishes; the viewer judges nothing |
| **the monitor's verdict** | reads `plant.json` beside the status file. PID_PLAN phase 5 |
| **the status file publishes what the supervisor knows** | 17 fields → 39, additively and duck-typed, so invariant 1 holds and a plain recorder is unaffected |

Two bugs found on the way, both live before tonight:

- **`_queue` had a default instrument.** `IpcService._pick` auto-picks the only
  controller when that field is empty, so once the software loop became a
  selectable target with `instrument == ""`, a defaulted `setpoint`, `range` or
  `pid` would have landed on the 336 — acknowledged OK, with no error
  anywhere. It is a required keyword now.
- **`_reconcile_targets` relied on Qt emitting.** Adding the first item to an
  empty combo makes Qt set the index to 0 itself, so re-aiming *to* index 0 —
  which is what a dropped first loop does — emitted nothing and left the panel
  aimed at a loop that no longer existed.

1387 tests and `ruff` clean; the Qt half also run under `QT_QPA_PLATFORM=windows`,
because offscreen resolves no font and roughly doubles every measured width.

## 3. One thing wants a restart, and it can wait

**The Move control works against the recorder running right now.** It accepts
commands, `ipc.allow_analog_output` is true, and the software branch of
`setpoint` is in the tree the recorder started from.

**The detail panel will be half dashes until the recorder restarts.** It
publishes schema 3, which has none of the fields §2 added — so `phase`,
`filtered_k`, the watt residual and the band's envelope read as `—`. That is
the degrade the projection is built for and there is a test on it
(`test_a_block_from_an_older_recorder_still_draws`), so it is a thing to know
rather than a thing to fix. Pick the restart up whenever the cryostat is next
free; nothing needs it tonight.

## 4. Two open items, and one new one

* **the night armed, then `hold_quality.py`.** Tonight *is* the night, and it
  is 2 h in. The long-averaging half of Jeff's answer 2 needs it run against
  2026-09-15 once there is a night of it.
* **the positional feedforward** still waits on a delivered-power gauge, and
  when it comes it needs `move_speed` re-graded against §3b rather than §3a.
* **10 K still takes 16 min for a 2 K move**, below `min_output_pct`, in a
  regime nobody has looked at. Not on Jeff's path.
* **new: the CSV does not carry the software loop's setpoint**, so a move has
  to be timed off the heater's output step instead — which is what §1's `t0`
  is, and it is why that row is 127 s rather than a number anybody can check
  against a command. Recording the loop's own columns was deliberately left
  out of the viewer work (it changes the log's header and rolls the file), but
  it is what would make a move gradeable from the archive afterwards.
