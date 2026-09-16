# Handoff — 2026-09-16 (the loop closed on the cryostat, twice; the second time it held)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
route to a working loop is [PID_PLAN.md](PID_PLAN.md) and the commissioning
step-by-step is [plans/pid-4-commissioning.md](plans/pid-4-commissioning.md).
This goes stale.

Previous: [HANDOFF-2026-09-15b.md](HANDOFF-2026-09-15b.md) (the monitor soaked;
two gates shrank; a document retired).

> ## THE SOFTWARE PID HAS NOW HELD THIS CRYOSTAT
>
> Armed 14:11:42, setpoint **117.06 K**, holding to **−22 mK with a 13 mK
> scatter** — which is the open-loop noise floor, so the loop is not adding
> any. Output dithering one DAC code, 63.99/64.00. `tracking`, no alarms.
> **The first arm, at 13:35, walked the heater down and cost 350 mK** — cause
> below, and it was a config omission, not a defect in `control/`.
>
> **4a's gate is MET**: one hour, 1750 samples, `tracking` on every one,
> worst error 90 mK against a 1 K warning, monitor and supervisor both clean.
> **W1 and both of W2's refusals are MET** on real hardware too.
>
> ## STATE AT SESSION END, 2026-09-16 15:19
>
> **The loop is still armed and tracking** — `ltspm3 -c
> config-ltspm3-armed.yaml run --arm`, started 14:11:42, setpoint 117.06 K,
> 2025 samples, `tracking` throughout, err −60 mK, output 63.99 %. The viewer
> and the monitor are up on the same config. **Leave it running** — every
> further hour is stage-5 evidence that cannot be reconstructed later.
>
> **`authority_pct` was raised 0.1 → 0.25 at 15:19 and is COMMITTED, but the
> running loop is still on 0.1.** `SupervisorConfig` is read at construction,
> so the new band takes effect on the next `run --arm`. See "the wider fixed
> band" below before restarting.
>
> Nothing else is uncommitted. `data/armed-4a-window.csv` holds the graded
> hour.

## What this session did

### 1. W1 — MET, and the method it was going to use was wrong

Three steps on the real 218 over GPIB, each above the 0.02 % tolerance so a
stale reply would have been *rejected* rather than believed, and **each agreed
on readback 1** — the readback paced at `write_settle_s`, and the only one the
armed supervisor gets. `write_settle_s: 0.1` stands.

The plan said to count `AOUT? readback failed (attempt N)` at DEBUG. That would
have answered "attempt 1" whether the box was prompt or slow: `_confirm` logged
only when a readback *raised*, and a readback that arrived and disagreed — the
stale case, the one W1 is about — was retried in silence. The attempt number is
now on the WARNING line the driver already writes, so it needs no DEBUG at all.
`--log-level DEBUG` also no longer drags pyvisa along (`--bus-trace` does that).

### 2. W2 — both refusals MET

`send analog 70.5` refused by the 70 % ceiling with no bytes reaching the box;
`send analog 0` against `ipc.allow_analog_output: false` refused by the gate.
**Invariant 3's both-directions rule, exercised on real hardware for the first
time**, against a spool that had applied 39 commands and refused none.
`heaters_off` is still outstanding — it costs a hold.

### 3. Jeff worked on the heater wiring, and the model's level went stale

Sample fell **118.31 → 117.19 K at unchanged output**, onset ~11:00. That is
−0.336 % of delivered power, **0.48 of the ±0.7 % `DELTA_P_FRAC` envelope the
model already carries for exactly this event**. The shape did not move; only
the level. It is in the log's Notes column — the first time that column has
carried one of these, after both September events went unrecorded.

Consequence: the model reads **0.107 % of output low** at the operating point,
and `GAUGE_WINDOW` is still `trace-ladder-20260905`.

### 4. The first arm walked the heater down — feedforward, not a bug

13:35:12 armed at 117.23 K with the heater at 64.007. Five cycles `frozen`
(filter priming), then `tracking` — and the output walked to **63.92 and stayed
there**, which is the model's stale answer, 63.902. Sample fell 350 mK.

**`_enter_mode` primes bumplessly and is correct.** The cause was that
`config-ltspm3-armed.yaml` ships `feedforward: enabled: true`, and 4a specifies
**no feedforward and no tuning**; only `authority_pct` had been changed. The
feedforward commands the model's answer, and the model is stale, so the loop
drove there and the integral needed hours at `ti = 900 s` to unwind it.

With feedforward off the band centres on `operating_point_pct` (63.960) instead
of the model's 63.902 — much closer to the truth — so **`authority_pct: 0.1`
works as the plan writes it and needs no widening.** Second arm, 14:11:42, held.

### 5. Three tools refused the armed config, one of them the abort

`control:` is registered by `ltspm3`; `lschart` may never import it; unknown
keys are a hard error. So the **viewer**, **`send`** and **`status`** all
refused `config-ltspm3-armed.yaml` — including `send hold`, **with a traceback,
while an armed loop was driving the heater**.

All three now load with `allow_unknown_sections`: the section is kept aside as
a raw mapping and never interpreted, so invariant 1 is untouched. Anything that
opens the port still refuses, and still says to use `python -m ltspm3`.

### 6. 4a — MET, and the loop is quieter than open loop

One hour armed. `analysis/allan.py` on it, the first closed-loop data this
cryostat has produced: floor **8.43 mK** at 10 s, down to **6.83 mK at 264 s**.
Open loop at this temperature floors at 7.38 mK at 130 s and rises after — so
the loop reaches a lower minimum and keeps improving twice as long.

The section-1 hold criterion reports NOT MET at tau = 480 and 874 s, and at one
hour **that is not a finding**: the criterion runs to `L/4`, and `edf` there is
6 and 3. It is a stage-5 gate over seven days for exactly this reason.

It also explains the raw sd: **28.7 mK over the window, against an 8 mK floor.**
That is the long-tau wander, not loop-added noise, and it is why an rms over a
hold says almost nothing about the hold.

### 7. `authority_pct` 0.1 → 0.25 — a wider FIXED band, taken knowingly

Jeff, 2026-09-16, with the trade understood. **It is not about control room**:
4a met its gate at 0.1 having used 6 % of it. It is about setpoint range.

With `feedforward.enabled: false`, `band_centre_pct` returns
`operating_point_pct` as a constant — **rule 5 is suspended while the
feedforward is off** — so the half-width is what bounds how far the setpoint
may move. 0.25 % at ~13.2 K/% is **±3.3 K**: a band of 63.710–64.210 %, about
114.7–121.3 K on the model, against ±1.3 K at 0.1.

A fixed band is the pre-rule-5 behaviour. The trade is a usable setpoint range
today against a model whose level is stale, and it is written into the config
beside the number. Re-gauge and re-enable feedforward at 4c, and the band
follows the setpoint again.

**This is also the first proof that fix B works.** The value moved, and was
committed, and the bench did not notice: 497 tests pass unchanged, because
`bench_plant.py` pins its own envelope. Before B this edit could not have been
committed at all.

### 8. The gauge is now the CRITICAL PATH, not a deferral

4a passing is what exposed this. With feedforward off, `band_centre_pct`
returns the constant `operating_point_pct` — **the band does not follow the
setpoint**. So 4a is pinned at 63.960 +/- 0.1, about 115.7–118.3 K, and the
setpoint cannot move more than ~1.3 K. 4d's 10 K sweep needs 0.76 % of output,
7.6x the whole half-width.

The chain: moving the setpoint needs the band to follow it; the band follows
only with feedforward on; feedforward commands the model's answer, so it needs
a gauge somebody believes. **A gates everything past a fixed hold at one
temperature.**

Widening `authority_pct` instead would buy setpoint range by running a wide
*fixed* band, which is the pre-rule-5 behaviour the band-follows-setpoint work
replaced. Wrong trade. For the hold itself 0.1 is generous: the loop used
63.986–63.998, **6 % of its authority**, and never neared a rail.

## What needs fixing, in the order I would take it

### A. The gauge is stale — the only item that costs cryostat time

Nothing else here removes it, and it is what made the first arm misbehave.
**~2–3 h**, rehearsed and green on the virtual clock:

```bash
python -m ltspm3.tools.sweep -c config-ltspm3-heater.yaml --percents "63.5,62.8,62.0,61.2" --order down
```

Downward deliberately: the rehearsal found that 64.5 % crosses `sweep`'s 120 K
ceiling and aborts, and down is the benign direction. 4/4 rungs graded `tau` in
28–34 min each. With the 117.19 K hold that is **five anchors over 82–117 K** —
enough to fit the gauge *and* hold one out, which is what `holdout.gauge`'s
docstring demands. Then re-export; `FittedSchedule` reads the model live and
there are no pasted keys in `tuning.py`, so the tuning does not rot.

Correction to something said earlier in the session: a re-gauge is **not** one
number off one anchor. `measure_gauge` fits it on a ladder.

### B. ~~The working 4a config is UNCOMMITTED~~ — FIXED 2026-09-16

`config-ltspm3-armed.yaml` currently carries `feedforward: false`,
`tuning: false`, `authority_pct: 0.1` — the three changes that made the arm
work — **and none of it is in git**. A fresh clone, or an idle
`git checkout --`, restores feedforward and reproduces the 350 mK walk.

It is uncommitted because `tests_ltspm3/bench_plant.py` loads its limits from
that file, so the file is both *what the cryostat runs* and *what the harness
grades*. Those are different things for `authority_pct` (a commissioning
throttle) and for `feedforward`/`tuning` (stage switches), though not for
`hard_max_pct` or the rates, which really are cryostat properties.

**Fixed.** `tests_ltspm3/bench_plant.py` pins `BENCH_AUTHORITY_PCT` and
`BENCH_FEEDFORWARD` -- the design envelope it grades -- and reads everything
else from the file, so the config now ships its 4a stage committed.
`tests_ltspm3/test_bench_envelope.py` pins the split both ways: the stage
switches come from the harness, `hard_max_pct` and the rates still come from
the file, and a test passing its own `sup_cfg` still wins.

### C. ~~The viewer offers controls that fight an armed loop~~ — FIXED 2026-09-16

`analog_ok = self.source.allows_analog_output()` gates on the IPC permission
only — nothing asks whether a software loop owns that output. The arm button
has no gating at all.

* **Set output…** is overwritten by the supervisor within one 2 s cycle:
  a blip, then silently undone.
* **Arm software loop…** is the worse one. `arm()` runs
  `set_setpoint(x, ramp=False)` *before* `set_mode()`, and `set_mode` no-ops
  when already PID — but the setpoint change does not. Pressing it while armed
  is **"step the setpoint now, no ramp"**, behind a button that says otherwise.

Panic Menu is unaffected and remains the abort.

**Fixed.** `StatusSource.software_loop_owns_output()` asks the control block's
`mode` -- ownership, which is a different question from the permission gates.
Both controls disable while it is `pid`, each with a note saying why and
pointing at `hold`; `hold` and `heaters_off` put the loop in `off` and hand
the output straight back.

### D. The bench cannot reproduce a stale-gauge arm

Three attempts, none of which reproduced the walk-down — the harness converges
with feedforward on *or* off, so it is not modelling whatever sustains it on
the real cryostat. Until it can, the bench cannot protect against this class of
problem, and the diagnosis above rests on the real trace plus code reading
rather than on a bench demonstration. **This is the gap that let 4 happen.**

### E. Viewer history is fragmented by the filename prefix

`source.py` stitches prior logs only when the prefix matches, and the configs
use `ltspm3-heater` and `ltspm3-armed` deliberately — so six months from now
the filename still says whether software or a person moved the heater. The cost
is that you see continuous history *or* the armed window, never both, which is
what Jeff hit. **A design decision, not a patch.** `--csv` points the viewer at
one log in the meantime.

### F. `status.json` write fails intermittently on Windows

`PermissionError: [WinError 5]` on the `os.replace`, recovered after one
failure. Cause is a reader — the viewer and the monitor both poll it — holding
the file at the instant of the rename. The recorder logs, carries on and
recovers, which is invariant 6 behaving. **Fix:** readers open with
share-delete semantics. Costs one stale cycle; not urgent.

### G. Phase 5 — the viewer shows the loop but not its judgement

The control row works: `Sample | sw | SP | Out | Rng n/a | tracking`. Missing
are error, the band, health, alarms and the monitor's verdict. Known, tracked,
gate not met.

### H. Startup poll overrun — benign, and it recurs

`poll overran by ~2.8 s` at 2026-09-15 16:26:54, 2026-09-16 13:35:11 and
14:11:42. Always the first cycle after both VISA resources open, never while
running. Worth a note, not a fix.

## Picking this up next session

**The loop is armed and should stay that way.** Read it without touching it:

```bash
python -m lschart -c config-ltspm3-armed.yaml status
python -m analysis.allan --csv data/armed-4a-window.csv --column Sample
```

`status` and the viewer both accept the armed config now; so does `send`, which
is the abort path:

```bash
python -m lschart -c config-ltspm3-armed.yaml send hold
```

`hold` freezes the heater where it is and puts the loop in `off`, which also
hands the viewer's manual controls back. `heaters_off` is the harder stop and
is exempt from every gate.

**To pick up the 0.25 band**, the armed run has to be restarted — the config is
read at construction. That is `Ctrl-C` (which leaves the heater where it is,
`on_exit: hold`) then `run --arm` again, which re-arms at the present
temperature. It costs the continuity of the tracking record, not the hold.

**Do not run the ladder without asking** — Jeff declined it on 2026-09-16 and
it ends the hold.

## Then, in order

1. **B and C** — neither touches a running process, and B is the one that will
   otherwise bite somebody from a clean checkout.
2. **A**, the ladder, when a couple of hours of cryostat time are affordable.
3. Re-arm at 4a with `authority_pct: 0.1`, and let the hour run.
4. **D**, before trusting the bench on anything arming-shaped again.

## Traps this session added

* **4a's "no feedforward" is load-bearing, not conservatism.** With a stale
  gauge, feedforward *is* the failure. Do not switch it on to "help".
* **`operating_point_pct` is vestigial only while feedforward is on.** With it
  off, that field becomes the band centre and its value matters.
* **The premise alarm is a WARNING, not a fault** (`warn_sigma: 3.0` against
  `fault_mw: 10.0`, and a fault is a *step*). A known calibration offset inside
  `bias_q_w` (±4.7 mW at 118 K) will warn every cycle and keep tracking. **Do
  not raise `warn_sigma` to silence it** — that is the only thing telling you
  the gauge is stale.
* **The monitor and the supervisor legitimately disagree** about the residual:
  the monitor judges the *change* against a trailing baseline, the supervisor
  the *level* against σ. After a calibration shift the monitor reads typical
  while the supervisor warns, and both are right. 4a's "verdicts agreeing" gate
  needs rewording.
* **A settled hold looks like a trend if you difference bucket means.** Block
  means scattered 13.5 mK against 16.1 mK within-block — the same number, which
  is wander. `analysis/allan.py` exists for this and was not consulted.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ltspm3 -c config-ltspm3-armed.yaml check
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m lschart.gui -c config-ltspm3-armed.yaml
```

`analysis/` still needs `measure.py` run once (11 s) before anything that fits.
