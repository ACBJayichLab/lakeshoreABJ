# PID Phase 4 — commissioning

Part of [PID_PLAN.md](../PID_PLAN.md). **Goal:** the loop armed on the
cryostat, attended and then not, then walked to 300 K.

**This document replaced `docs/ltspm3/commissioning.md` on 2026-09-15**, which
was retired rather than updated. What it had that was durable went to
[thermal-response](../docs/ltspm3/thermal-response.md) (how long to hold a step,
and why R² will not tell you), [cryostat](../docs/ltspm3/cryostat.md) (the
`AOUT?` flicker) and [running](../docs/ltspm3/running.md) (reading the band off
`check`); what it had that was a plan is here; the rest was a status block three
weeks stale, two superseded ramp-down rates, and gates met months ago.
**Do not put a dated current-state block in this file.** That pattern has been
wrong three times — twice in the config headers, once in the document this
replaces. Current state lives in `HANDOFF.md`, which is point-in-time by design
and archived under its own date.

Read [safety.md](../docs/ltspm3/safety.md) first; the eight rules are not
restated here. **Every stage has an exit gate, and the gate is a thing you
observed, not a thing you believe.** Do not carry an unmet gate into the next
stage — the staging is what bounds the damage the next one can do. Everything
up to and including stage 4 is attended: somebody in the room, watching the
viewer, with a hand on the panic path.

**The monitor runs from stage 3 on and `plant.json` is part of every gate.**

## 4.0 The bench, which costs no cryostat time

There is no excuse for skipping this, because it is free.

```bash
python -m pytest -q
python -m ruff check .
python -m ltspm3.tools.replay "reference/logs/CD*/*.xls"
python -m ltspm3.monitor --replay reference/cooldown-10/
```

`replay` is the only test on genuine data. Then a full armed run in simulation
on the real config with a sim driver, with deliberate fault injection: a sensor
glitch, a comms drop, a sustained fault to completion and lockout, and
`hold` / `arm` / `ack` over the file interface.

| gate | |
|---|---|
| replay | the rejection rate of [safety.md](../docs/ltspm3/safety.md) and **0 samples reaching FAULT** over the reference logs |
| simulated fault | ramps down at the one rate, latches, completes, locks out, and `acknowledge()` is the only way out |
| spool | `hold` and `arm` round-trip |

## 4.1 Stage 3 close-out

| item | do | gate |
|---|---|---|
| W1 | see below | **MET 2026-09-15** — three steps, each read back on readback 1 |
| W2 | `send analog 70.5`; `analog 0` with the gate closed; `heaters_off` | **both refusals MET 2026-09-15**; `heaters_off` outstanding — it costs a hold, so run it when you are ending one |
| circuit | the 09-10 repair on the manifest (Phase 0) | `δQ` inside 3 σ_Q for 72 h; weak evidence, but the evidence there is |

### W1 — is 100 ms enough?  **Answered 2026-09-15: yes.**

Three steps on the real 218 over GPIB, at the default 100 ms:

```
64.007% -> 64.047%  commanded 64.050, verified on readback 1
64.047% -> 63.989%  commanded 63.990, verified on readback 1
63.986% -> 64.007%  commanded 64.010, verified on readback 1
```

Steps of 0.043, 0.057 and 0.021 % — every one above either tolerance, so a
stale reply would have been **rejected**, not believed. And every one agreed on
the **first** readback, which is the one paced at `write_settle_s` and the only
one the armed supervisor gets. `write_settle_s: 0.1` stands; no change needed
before arming. The procedure below is kept for the next link, or the next box.

**This was a twenty-write, six-delay characterisation and it is
over-specified.** `write_settle_s` is not a command cadence: it is the
transport's pacing gap between a write and the next transaction on that link
(`_pace()` applies it only when the last transaction was a write), so a write
and its confirming readback happen back to back *inside* one cycle. Nothing is
issued at sub-cycle intervals and nothing needs to be.

What makes it worth checking at all is that the armed config sets
`verify_writes: false` on the instrument — the supervisor confirms its own
writes, and paying twice would put a second transaction in every cycle — so the
218 driver's five-attempt retry loop is off and `_write_output`'s single
readback is the whole verification.

A stale readback returns the **old** value, so for any step larger than
`verify_tol_pct` it fails the comparison and raises rather than passing
quietly. Send three steps comfortably above that tolerance, around wherever the
heater is sitting, and read the WARNING lines:

```bash
python -m lschart -c config-ltspm3-heater.yaml send analog <x+0.04>
python -m lschart -c config-ltspm3-heater.yaml send analog <x-0.06>
python -m lschart -c config-ltspm3-heater.yaml send analog <x>
```

Each logs `old% -> new%, verified on readback N` at WARNING, which is the
evidence. If every readback shows the value just written, 100 ms stands. If any
shows the previous value, **then** characterise the delay properly, and set
`write_settle_s` above it with margin in the `transport:` block — it is a
transport field, not an instrument one, and nothing under `instruments:` accepts
it.

**Send a step that actually moves the box.** A write commanding the value the
output is already holding makes `previous` and `got` the same number, so a stale
readback and a fresh one are identical and the write proves nothing. Read `AOUT?`
first and step from there.

#### And read N, not just the values

`old% -> new%` alone closes only half of W1. The driver retries up to five times
at 100 ms, so it says "verified" whether the box agreed at 100 ms or at 500 ms —
and the armed config sets `verify_writes: false`, where `_write_output` does
**one** readback paced at exactly `write_settle_s` with no retry and turns a
disagreement into an alarm rather than a retry. A box needing 300 ms would pass
this test and then alarm every cycle once armed.

So the WARNING line carries which readback agreed (added 2026-09-15):

- **`verified on readback 1`** on all three steps → the first readback at
  `write_settle_s` was fresh. 100 ms stands, W1 fully closed.
- **anything higher** → the box needs about N × 100 ms. Raise `write_settle_s`
  above that with margin before arming.

**Do not try to answer this by counting DEBUG lines.** Until 2026-09-15 the
retry loop logged only when a readback *raised*; a readback that arrived and
disagreed — the stale case, the one W1 is about — was retried in total silence.
Counting `AOUT? readback failed (attempt N)` counted comms errors and never once
counted staleness, so it read "attempt 1" either way. That branch now logs too,
but the number on the WARNING line is the thing to read, and it needs no DEBUG.

`--log-level DEBUG` is also survivable now: it no longer turns on pyvisa, which
traced three lines per query and made ~26 lines a cycle across the two boxes.
`--bus-trace` is how you ask for that traffic when you want it.

**Two cases this cannot cover, and neither needs it**: a dithered hold and a
ramp at the warm end both move the output by less than `verify_tol_pct` per
cycle, so the check is silent there by arithmetic rather than by passing. It
only becomes discriminating at the cold end. **The arithmetic is in
[docs/ltspm3/running.md](../docs/ltspm3/running.md)**, which is its one home;
knowing which of the two the check is doing is the point.

### W2 — make a ceiling refuse something

**Both refusals met 2026-09-15.** `send analog 70.5` was refused by the 70 %
ceiling with no bytes reaching the 218, and `send analog 0` against a recorder
running `ipc.allow_analog_output: false` was refused by the gate — invariant 3's
both-directions rule exercised on the real system for the first time. The gate
was closed by editing the committed heater config and restored with
`git checkout --`, which is the restore to use: it is exact and cannot be left
half-done. `config-ltspm3-read-only.yaml` cannot stand in for this, because its
`accept_commands: false` refuses one gate earlier and the gate under test is
never reached.

`heaters_off` is the part still outstanding. It genuinely reaches the box, so it
costs the hold and hours of recovery; run it on a day you are ending a hold
anyway, not to tick the box.

As of 2026-09-03 the spool had applied 39 commands and refused **zero**, so no
ceiling in this system has ever been exercised against the real hardware.
`send analog 70.5` is the cheapest possible test: the driver *raises* rather
than clamping, so no bytes reach the 218 and nothing moves. `analog 0` with
`ipc.allow_analog_output: false` proves the gate covers both directions.
`heaters_off` **does** reach the box — it is exempt from the source policy and
both power gates — so run it knowing it takes the sample off its hold.

## 4.2 Stage 4, attended

A **third config**, `config-ltspm3-armed.yaml`: the authority band at the
width the stage is armed on, Phase 3's tables loaded. Arm only when `plant.json` has read typical on all
residuals for the past hour, and from a hold that has actually **settled** —
open loop this cryostat comes to rest in hours, and an output ramping steadily
at a fixed setpoint means something is still moving. Find out what before
arming over it.

`operating_point_pct` no longer needs re-centring when the cryostat moves: the
band follows the setpoint (rule 5), and that field is only the fallback centre
for a loop with no model. What to check is that `check`'s printed band brackets
the output the heater is actually holding.

### "No feedforward" is load-bearing, and 2026-09-16 proved it

**4a's three settings are `authority_pct: 0.1`, tuning OFF and feedforward
OFF. Applying only the first is what cost 350 mK on the first arm.**

With feedforward ON, `_model_reference()` returns the model's steady-state
answer for the setpoint, and that is both the band centre *and* the term the
loop drives toward. On 09-16 the model was 0.107 % of output low after wiring
work, so the loop armed bumplessly at 64.007 %, tracked down to **63.92 — the
model's answer — and stayed there**, with the integral needing hours at
`ti = 900 s` to unwind a persistent error. The sample fell 350 mK.

With feedforward OFF the centre is `operating_point_pct` and the PID carries no
model-derived term at all: it primes at the real output and integrates from
there, so it *cannot* seek a stale answer. Second arm, same cryostat, same
stale gauge: held to **−22 mK with a 13 mK scatter**, which is the open-loop
noise floor.

So the ordering in 4c — widen, then tuning, then feedforward, one per watched
hour — is not caution for its own sake. **Feedforward is the stage that makes
the loop trust the model's level, and it should be the last thing switched on,
after a gauge you believe.**

Two consequences worth carrying:

* **`operating_point_pct` was the band centre whenever feedforward was off** —
  true only until 2026-09-17, when the centre became the model's answer for the
  setpoint whenever a curve exists ([requirements.md](../docs/ltspm3/requirements.md)).
  It is the fallback centre for a loop with no model, and nothing else.
* **The narrow 4a band needed no widening**, even on a stale gauge, precisely
  because the centre was not the stale number.

### The bench reproduces it now — HANDOFF item D, closed 2026-09-16

Three attempts during the arming session could not reproduce the walk-down on
the harness, and the reason was the plant's starting state, not the loop.
`FittedHarness` put the plant at `percent_for(kelvin)` — the **nominal** curve
— so a plant whose heater delivers less than `P(u)` claims began out of
equilibrium *and falling*, and the first minutes were spent catching a
disturbance nothing in `control/` had caused. Feedforward on and off then gave
bit-identical traces, because the feedforward term is referenced at arming and
a fixed setpoint never moves it again.

The cryostat was in the other state: **settled** on a heater delivering 0.336 %
less, 1.4 K below the model's answer for its own output. `FittedHarness(...,
settled=True)` is that state — `equilibrate()` integrates the plant to its own
fixed point before anything closes a loop — and the walk-down is there:

| feedforward | authority | ΔT at 5 min | ΔT at 40 min | output at 40 min |
|---|---|---|---|---|
| on | 0.1 | −0.52 K | −0.76 K | 63.93 |
| on | 0.25 | −0.52 K | −0.76 K | 63.93 |
| off | 0.1 | +0.02 K | +0.01 K | 63.98 |
| off | 0.25 | +0.02 K | +0.01 K | 63.98 |

Feedforward is the whole mechanism and the band's width is not part of it: at
either width the loop walks toward the model's stale answer and is still on its
way at forty minutes, because unwinding an integral at `ti = 900 s` is slow.
With it off neither half exists.

**`tests_ltspm3/test_stage_4a.py` is the bench's copy of this stage**, and it
is the only thing here that grades the switches the cryostat is armed on:
`stage="file"` takes `authority_pct`, `operating_point_pct`,
`feedforward.enabled` and `tuning.enabled` from `config-ltspm3-armed.yaml`
verbatim, where every other scenario in that directory pins the design envelope
(1.0, on, on). It also pins the two things only visible from this stage —
the fault ramp-down's rate and the output rate limiter's — see below.

### What 4a's two switches cost the rate limits — fixed 2026-09-16

Both were the same conflation, and both were found by reading rather than by a
failing test ([archive/AUDIT-2026-09-16.md](../archive/AUDIT-2026-09-16.md),
findings 2 and 3):

* **the fault ramp-down.** `_rampdown_target` walks a target temperature down
  at the one rate and turns it into an output through the model's inverse
  curve — *when the feedforward was enabled*. At 4a it is not, so the descent
  took the no-curve branch and fell at `min_rate_pct_per_min`: **320 minutes
  from 64 %, measured on the bench, against the 23 minutes
  `docs/ltspm3/control.md` promises.** Slower is the safe side of rule 1 and it
  was not a heater hazard — but a sensor fault at 118 K left the heater at 64 %
  for the rest of the afternoon on a cryostat whose thermometer had just
  stopped being trusted, and the `else` branch's own comment called itself
  unreachable while it was the armed configuration.
* **the output rate limiter.** `_rate_pct_per_min` returned the floor whenever
  the *tuner* was disabled, so 4a's effective output rate was 0.20 %/min —
  **2.6 K/min at 118 K**, not the 5 the file names; 0.6 K/min at 30 K — while
  `set_setpoint` went on ramping in kelvin at the rate the file does name.

`feedforward.enabled` answers "should the loop trust the model's *level*",
which is what 4a correctly says no to; `tuning.enabled` answers "reschedule the
gains". Neither is the question "is there a curve to convert kelvin into
percent with", and the curve's *shape* did not expire when its level did.

**`HeaterSupervisor.has_curve` and `.schedule` are that question**, and the
ramp-down, `_rampdown_step_pct` and the rate conversion are gated on them.
Measured under the file's switches after: the descent is **24.0 minutes** and
the limiter is **0.40 %/min at 118 K**, which is the one rate.

**`ramp_lead_pct` deliberately stays on `tuner.enabled`.** The two above were
failing *slow*; that one widens the authority band, which is the only direction
that hands the loop more heater than it had. So at 4a the band does not widen
during a ramp, and the velocity feedforward it caps is off with it. Switch them
on at 4c with the tuning, not before.

**A move is only as fast as the scheduled gains let it be.** With
`tuning.enabled: false` the loop runs the file's starting fixed gains and a
small move is still short of its setpoint after half an hour — that is what 4c's
tuning step exists to fix, and it is on in the shipped file. Read 4d below for
what the benchmark is: a 2 K move at 118 K in five minutes
([requirements.md](../docs/ltspm3/requirements.md)). The rate ceiling is not
the lever.

### The file ships what is ARMED, and the bench grades the envelope

**Superseded 2026-09-16.** This section used to say `authority_pct` in
`config-ltspm3-armed.yaml` was 1.0, that 1.0 was correct for the file because
the bench loaded its limits from it, and that 4a's 0.1 was therefore a
deliberate edit on arming day to be reverted with `git checkout`. That is no
longer how it works, and the old arrangement is what left the working
configuration uncommitted on the day the loop first closed — one `git checkout`
away from restoring the feedforward that cost 350 mK.

`tests_ltspm3/bench_plant.py` now pins its own `BENCH_AUTHORITY_PCT`,
`BENCH_FEEDFORWARD` and `BENCH_TUNING`, and reads everything that is a property
of the *cryostat* from the file. So:

* **the file ships the commissioning stage the cryostat is armed at**, and it
  is committed. On 2026-09-16 that was `authority_pct: 0.25`, feedforward off,
  tuning off; since 2026-09-17 it is 1.0, off, on, with the retuned ratios of
  [requirements.md](../docs/ltspm3/requirements.md).
* **the bench grades the design envelope** — 1.0, on, on — which is where phase
  3 proved the loop and where 4c ends. Narrowing that would fail 26 scenarios
  by construction, because the 10–180 K sweeps rail against ±0.1 %.
* **`tests_ltspm3/test_stage_4a.py` grades the file's own switches**, which
  nothing did until the audit asked.

Read the band back after any edit:

```bash
python -m ltspm3 -c config-ltspm3-armed.yaml check
```

**Expect the loop to sit off the centre of its band**, because the centre is
the model's number and the model's level has a shelf life. Do not read sitting
high as the §4.3 abort signature — *railing against the ceiling while settled*
means railing with room in the model, not railing because the throttle is
tighter than the calibration offset.

| step | gate |
|---|---|
| 4a — arm on a narrow band, **no tuning, no feedforward** — all three, see below | **MET 2026-09-16** — one hour of `tracking`, monitor and supervisor both clean; the numbers are in "What 4a measured" below |
| 4b — provoked fault at low temperature | ramps down at the one rate through the inverse curve, **does not resume when the sensor comes back**, latches, locks out, `ack` the only way out |
| 4c — widen to 1.0 %, then tuning, then feedforward, one per watched hour | no `frozen` without a named cause. **TUNING STEP TAKEN 2026-09-17** — see below; the widen and the feedforward are still outstanding, and the feedforward waits on the gauge |
| 4d — the move benchmark, **five minutes for 2 K at 118 K** | **bench gate met 2026-09-17, then SUPERSEDED by 4e the same evening** before it ran on the cryostat — see below |
| 4e — **the move benchmark**: a 2 K move at 120 K, 95 % in ~60 s and within 50 mK inside 2-5 min ([requirements.md](../docs/ltspm3/requirements.md) §1b), and a sweep ≥ 10 K on top of it | **bench gate met 2026-09-17** (86 s / 114 s / 11 mK), cryostat pending: arrives inside the benchmark, lag < 2 K on the long sweep, no warning at either end, `δQ` quiet — see below |

### 4c's tuning step, taken out of order and before the widen — 2026-09-17

`tuning.enabled: true`, `hold_speed: 3 -> 12`, feedforward still off.
(**The 12 lasted one day**: see 4d below and
[requirements.md](../docs/ltspm3/requirements.md) — Jeff chose the strong
direction, and the file ships `hold_speed: 0.25`. `move_speed` went 0.5 -> 0.15
at 4d and 0.15 -> 0.03 at 4e.)

Out of order deliberately. The widen and the feedforward both change how much
heater the loop may command; the tuner changes only how it is scheduled, and
**it is the one of the three that does not wait on the gauge** — `feedforward`
commands the model's stale LEVEL while the tuner reads only `K(T)` and `tau(T)`,
the SHAPE. Same distinction `has_curve` already draws for the ramp-down and the
output rate limiter ([archive/AUDIT-2026-09-16.md](../archive/AUDIT-2026-09-16.md)
findings 2 and 3).

It is also what made ramping testable at all: three things are gated on
`tuner.enabled` — the scheduled gains, the velocity feedforward, and
`ramp_lead_pct` — and with all three off a +2 K move arrives about a kelvin
late at **any** commanded rate. `tests_ltspm3/test_stage_4c_tuning.py`.

~~`hold_speed: 12`~~ was a guess in the *weak* direction and is
**superseded 2026-09-17**: asked, Jeff chose the strong direction, and the
shipped ratio is in [requirements.md](../docs/ltspm3/requirements.md) §3 with
the Allan table that graded it. One night on the cryostat, graded by
`analysis/hold_quality.py`, still settles the long-averaging half.

### 4d — the move benchmark is five minutes for 2 K at 118 K — 2026-09-17

**Superseded the same day it was written.** This section said "5 K/min is a
cold-end number" and restated the gate as "a 10 K sweep with lag < 2 K", taking
half an hour, because a 1 K/min sweep at 118 K had been measured running as an
exponential with τ = 260 s. That measurement was right and the conclusion was
not Jeff's: asked, he set the benchmark at **a 2 K move arriving in 5 minutes
at 118 K**, with 5 K/min a safety ceiling only
([docs/ltspm3/requirements.md](../docs/ltspm3/requirements.md)).

That 260 s was `move_speed × tau` at the old ratio, stacked twice — the
trajectory corner and the closed loop are the same number by design — and the
retuned `move_speed` brings the same move inside the benchmark with a few mK of
overshoot and nothing railed ([requirements.md](../docs/ltspm3/requirements.md)
§3 has the ladder). The band had to widen to let it, because the overdrive a
fast move needs exceeded the old half-width, and the band had to follow the
setpoint with the
feedforward off, which it did not: a 2 K move at 140 K faulted on the bench
until `target_band_centre_pct` asked `has_curve`.

**Gate:** superseded by 4e below, the same evening, before it ever ran on the
cryostat. Its bench gate was met (2 K at 100–180 K inside 5–6 min, 10 K inside
20 min, hold noise ratio ≤ 1.05 at 10–300 s) and its known open item — 60 K
overshooting 0.39 K — is closed by 4e rather than by the anti-windup it
expected.

### 4e — the move benchmark is ~60 s for 2 K at 120 K — 2026-09-17

**Jeff armed 4d's numbers, watched a move, and said five minutes was
excessively generous.** His revised requirement is
[requirements.md](../docs/ltspm3/requirements.md) §1b: a fast approach and a
slight adjustment — 95 % of a 2 K move at 120 K in about a minute, overshoot up
to 250 mK, within 50 mK and staying inside 2–5 min. He kept the 2 s cadence,
the median of three, `delay_floor: 4` and 5 K/min as a trajectory ceiling, and
those four choices are what set the 86 s the bench measures.

**One number was doing two jobs.** `max_rate_k_per_min`, divided by the gain,
was also the heater's own slew limit — 0.40 %/min at 120 K, against the 3.5 %
of overdrive a 5 K/min ramp needs. That is the soft approach with the long
tail, and it is why 4d measured *worse* moves when the ceiling was raised: one
number cannot be both a trajectory rate and an actuator slew rate. Splitting it
(`supervisor.max_output_rate_pct_per_min`) is most of 4e, and it took the 10 K
move from 15.9 min to 2.6.

Going fast then found three defects that were never about speed — a spike
predictor that could not follow the file's own rate ceiling, a rejection that
rejected everything after it, and a write decision that compared against memory
instead of against the heater. All three are written up in
[requirements.md](../docs/ltspm3/requirements.md) §3b with what each one cost.

**Gate:** `tests_ltspm3/test_stage_4e_fast_move.py` green (2 K at 30–180 K,
±2 K, 10 K and 0.5 K at 120 K; every overshoot inside 250 mK; hold noise ratio
unchanged), then the same 2 K move on the cryostat from
`send setpoint --software` with the monitor typical throughout. 180 K is graded
separately and loosely on purpose: there `hard_max_pct` itself is what limits
the move.

### The "verdicts agreeing" clause needs rewording

4a's gate asks the monitor and the supervisor to agree. **After a calibration
shift they legitimately will not**, and 09-16 is the case: the monitor read
`missing_power: typical` while the supervisor warned on the same residual.

Both are right, and the difference is designed. The monitor judges the
**change** against a trailing baseline — which is what absorbs a delivered-power
fraction shift, exactly the thing a reseated wire is. The supervisor judges the
**level** against σ, and `bias_q_w` (±4.7 mW at 118 K, the calibration
envelope) is deliberately *not* in that band, so any cryostat sitting anywhere
inside its own declared envelope warns.

So the gate should read: **the two disagree only in ways a named cause
explains.** A standing supervisor warning with the monitor typical, after
somebody has touched the heater wiring, is that. A disagreement nobody can
account for is not. **Do not raise `warn_sigma` to make them agree** — that
warning is the only thing reporting that the gauge is stale.

### What 4a measured, 2026-09-16 — and what an hour cannot grade

1750 samples, `tracking` on every one, worst error 90 mK against a 1 K
warning, output 63.986–63.998 inside a 63.860–64.060 band. Monitor and
supervisor both clean.

`analysis/allan.py` on that window, which is **the first closed-loop data this
cryostat has ever produced**:

```
     tau s   sigma_y mK      edf
         8        8.426      436     <- the floor, sigma_y(10 s)
       146        6.919       23
       264        6.833       12
       480        9.715        6
       874       18.962       3
```

**The loop beats open loop everywhere the window can measure.** Open loop at
this temperature floors at 7.38 mK at tau = 130 s and rises after — averaging
stops helping at two minutes; that ladder is C6's table below, and it is the
home of those numbers. Closed loop reaches **6.83 mK at 264 s**: a lower
minimum, and still improving twice as long.

**The section-1 criterion reports NOT MET, and at one hour that means nothing.**
The criterion runs to `L/4`, which is 874 s on a 58-minute window, and `edf`
there is **3**. Three independent samples cannot distinguish wander from
nothing. Note also that the raw sd over the window is 28.7 mK — about the
long-tau sigma_y, not the 8 mK floor — so **quoting an rms over a hold says
almost nothing about it**, which is the whole reason this tool exists.

**So: do not grade the hold criterion on anything short.** It is a stage-5
gate, over seven days, for exactly this reason. A rule of thumb from this run:
`edf` below about 10 means the point is decoration. Read the shape out to where
`edf` is still tens, and ignore the tail.

4b is rules 1 and 7 proved on the cryostat rather than on the bench. The
cleanest provocation is to drop `fault_after_s` to something short and pull the
sensor's plausibility out from under the guard; the safest time is at low
temperature, where the fault response — losing heat — is the benign direction.

At 4d, watch `missing_power_w` against its band rather than the tracking error:
the lag a ramp commands is `r·τ` and is not an excursion, which is the whole
reason the premise moved into watts. Watch the **end** of the ramp specifically
— the band's ramp lead decays with the smoother, and a sweep that has arrived
should leave no standing error behind it.

## 4.3 Stage 5, unattended

Seven days at the operating point, viewer open on another machine.

**Gate:** the hold criterion `σ_y(τ) ≤ σ_y(10 s)` over the run; every warning in
`plant.json` explained; no fault; no `frozen` longer than `warn_after_s` without
a cause. Indefinite is the design; seven days is the proof.

**Abort and drop back a stage if any of these happen:** a readback disagreement
not explained by `write_settle_s`; a ramp-down nobody can account for; the loop
railing against the *ceiling* of the band while settled; `δQ` outside its band
while settled; any `frozen` longer than `fault_after_s` without a cause you can
name.

**Rollback is always the same thing**: `send hold`, then stop the process.
`on_exit: hold` leaves the heater where it is, which is what you want on a live
cryostat.

## 4.4 Stage 6 — the calibration campaign

Only after stage 4's gate. Every one of these produces a number that goes back
into config. Run them roughly in order; C1 is free and C2 gates most of the rest.

**C1 — sensor noise versus temperature.** Comes out of the stage-4 recording at
no cost. Fit rms against temperature and compare to `1.36e-6·T²`, floored
~1.8 mK. **Confirmed at one point, 2026-09-03**: over a settled 25.7 h hold at
180.56 K the sample's rms is 44.1 mK against 44.3 mK predicted. Still
outstanding at low temperature, where the 1.8 mK floor is what is claimed.

**C2 — step tests: local gain and time constant.** The method, the 30-minute
rule and the R² trap are in
[thermal-response](../docs/ltspm3/thermal-response.md#how-long-to-hold-a-step--and-why-r-will-not-tell-you)
— read it before booking cryostat time, because a five-minute hold returns τ
five times too small at R² = 0.95. `ltspm3/tools/steptest.py` holds the
protocol and `--from-csv` scores holds already on disk. The rung list comes from
`analysis/plan_sweep.py`, which marks rungs past the top of the fit as
`predicted_only`; it is not a table anybody should be writing by hand.

**C3 — re-run replay against real armed data.** Feed the stage-4 CSVs through
`replay.py`. The guard thresholds are calibrated against the *legacy* logs at
2–20 s cadence; this is the first chance to check them at the live
cadence with the loop closed. **Updates:** `SensorGuardConfig` —
`max_slew_k_per_s`, `corroborate_slew_k_per_s`, `curvature_ratio`,
`fault_after_s` — and `CoherenceConfig`.

**C4 — does the dither actually deliver sub-code resolution.** Hold a fixed
setpoint ≥ 1 h with `dither: true`, then ≥ 1 h with `dither: false`, same
conditions. Compare rms and Allan deviation at 60 and 600 s. One code is
~100 mK at the operating point and the fast pole should low-pass the dither to
under 1 mK of ripple, so the dithered run should be dramatically quieter. If it
is not, the fast pole is not what we think it is — which is a C2 result, not a
dither result.

**C5 — closed-loop verification.** With the C2 schedule loaded, command a small
setpoint step — inside `warn_error_k`, so 0.5 K — through the ramp. IMC tuning
predicts a first-order closed loop with **no overshoot** and a time constant
equal to `τ_cl`; check both. Overshoot means the schedule's `K` or `τ` is wrong
at that point. Do it in both phases, and confirm the hysteretic switch between
them does not chatter.

**C6 — the stability figure.** The number you actually quote. Hold ≥ 6 h at the
operating point and run `python analysis/allan.py --csv <the run>`.

**Compare against the MEASURED open-loop bar, not against a prediction.** Open
loop at 118 K, 26.3 h at 64.0155 % (`pc-20260908-154814`):

| τ | 4 s | 10 s | 60 s | 130 s | 600 s | 1 h | 6.6 h |
|---|---|---|---|---|---|---|---|
| σ_y, mK | 7.79 | 8.73 | 7.95 | **7.38** | 9.52 | 12.72 | 24.49 |
| edf | 23666 | 9466 | 1577 | 727 | 157 | 25 | 3 |

**Averaging stops helping at about two minutes.** The floor is 7.38 mK at
τ = 130 s and everything past it is drift. So PID_PLAN §1's criterion —
`σ_y(τ) ≤ σ_y(10 s)` out to L/4 — is **not met open loop, by 2.9×**, and
flattening that rise from 130 s outward is precisely what C6 measures. A
closed-loop run that merely matches the table above has not done anything yet.
If the real figure is much better, suspect the measurement; if much worse, go
back to C5.

**C7 — feedforward regime validity.** At each settled point, compare the
measurement against `kelvin_for(output)`. The steady-state curve was measured
with the cooler running and the shields cold, and nothing in a temperature log
distinguishes that regime from a warm one. **Updates:** `max_feedforward_pct`,
and — if the disagreement is large — `feedforward.enabled: false`, letting the
integral do the work. That is slower, and it is always correct.

### The ladder to 300 K

Plan 1 §1.3's pipeline, run: rungs upward by `plan_sweep.py`, `max_output_pct`
raised **one graded rung at a time** toward 100 %, each rung a `jump` window and
a refit input, `δT_c` and the stage channels the watch on the cooler. Stops
where Jeff says or where `δT_c` says the cooler is losing.

**Gate:** every rung graded `steady`; the refit's up-range residual < 0.5 K rms;
the table's `T_MAX_K` at the highest graded rung; the ceiling in config equal
to it.

## Keep a commissioning log

One entry per stage: the date, the config file and its git SHA, what was
observed, and the gate that was met. Every number in `docs/ltspm3/` traces back
to a measurement; these will too, and in a year the log is the only thing that
will say which cryostat state a given number was measured in.

**Where a measured number contradicts memory, the number wins.**
