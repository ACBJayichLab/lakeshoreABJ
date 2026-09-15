# PID Phase 3 — the review fixes

Part of [PID_PLAN.md](../PID_PLAN.md), after [pid-3-loop.md](pid-3-loop.md).
**Goal:** close the defects a code review of steps 5 to 8 found on 2026-09-15,
before phase 4 arms anything. Nothing here is a new feature: every step
restores a property the code already claims to have.

**Status, 2026-09-15: step 1 of ten landed.**  The step-order table at the
bottom carries the rest; a step is landed when its gate is green and its commit
names the rule it touched.

**The rules of phase 3 still apply.** One commit per step, each naming the
safety rule it touches, each with a bench test that FAILS before the change
and passes after it, and a line in `PID_PLAN.md`. The confirmed items each
have a measurement below; the measurement is what the new test asserts.

## The principle the fixes serve — Jeff, 2026-09-15

Two things can go wrong, and the loop must answer them differently:

1. **A steady, typical-looking change the model explains.** The coldplate
   warms, or something else moves the bath, and the loop needs less heat to
   hold the setpoint. Less heat is the broadly safer path: the worst case is a
   sample colder than intended. **This is a WARNING**, however far it goes,
   including all the way to the heater at its floor with the sample above
   setpoint. Nothing a ramp-down could do there helps, and a lockout would only
   stop the loop resuming when the bath recovers.
2. **A sudden, aphysical change.** The watts stop adding up, or the loop rails
   at its ceiling and the sample still will not come up. The system is
   misunderstood, effectively open loop, or mis-driven. **This is a FAULT and a
   ramp-down**, for the sample's safety.

The code already has one detector for each: the watt residual (`dQ` and its
step test) is the primary check, and the kelvin rows exist because at cryo
conditions the residual cannot speak, below `min_output_pct` where it is the
difference of two large numbers and below about 40 K where the plant is faster
than the slope window. The review's finding E is that **both kelvin rows are
unreachable**, so scenario 2 at the cold end today produces nothing.

## 3R.0 What the review found

Five confirmed on the bench, six plausible from reading, and a documentation
sweep. The suite was green throughout (1151 passed, ruff clean), which is the
point: none of these is covered by a test yet.

**A. A transient crash's latch lasts one cycle.** `_step`'s OFF-mode early
return resets the state to IDLE unless it is LOCKED_OUT, and CRASHED is not
excepted (`supervisor.py:1092`). Measured: crash a tracking loop once with a
one-off exception, step one cycle, and `arm` is accepted with no `ack`. The
bench's crash test passes only because its sabotage (`filter = None`)
re-crashes every cycle.

**B. The loop's premise band grows with the calendar.** `_missing_power` calls
`sigma_q_w(..., time.time())`, the full band with the drift term in it, and the
step test at `supervisor.py:1489` scales its threshold by that band. The
monitor judges the same step with `sigma_q_fast_w` (`judge.py:800`), which has
no date in it. Three sigma, settled:

| day | 30 K | 118 K | 180 K | fast band (no date) |
|---|---|---|---|---|
| gauge (2026-09-05) | 4.0 mW | 1.4 mW | 1.6 mW | 4.0 / 1.4 / 1.6 |
| 2026-09-15 | 7.0 | 8.7 | 10.0 | same |
| +30 d | 17.9 | 26.0 | 30.0 | same |
| +60 d | 35.1 | 52.0 | 60.0 | same |

A month after the gauge the loop would not fault on a step five times the
2026-09-10 event; the monitor still would. It also makes the bench depend on
the date it runs, which `CLAUDE.md` forbids: the "wrong on purpose" and "quiet
through a sweep" rows get easier every day, and a genuine-fault row would
eventually stop faulting.

**C. The first ramp-down cycle is not rate limited.** RAMPING_DOWN skips
`_rate_limit` (`supervisor.py:1131`) and the descent begins at
`percent_for(last trusted T)`, which need not be near the present output. With
the last reading 5 K under the setpoint, the shape authority-exhausted
requires, the first write moves the output by:

| hold | one-cycle step | the one-rate step per cycle |
|---|---|---|
| 10 K | 21.6 % | 0.50 % |
| 30 K | 3.2 % | 0.09 % |
| 118 K | 0.38 % | 0.013 % |

The lost-sensor bench row cannot see it: its plant IS the model, so the curve's
answer equals the output. Any model error or any sample off its setpoint at
the fault opens the gap. **There is no separate descent rate** (Jeff,
2026-09-15): the knee rates were superseded by the one rate, and the first
write obeys it like every later one.

**D. A failed readback during ramp-down completes it instantly.**
`_rampdown_target` reads the heater with `default=safe_output_pct`
(`supervisor.py:1608`), so `min(proposed, current)` is zero and
`_rampdown_complete` is set. Measured: output 63.96 %, one `TransportError`,
target 0.0, complete. If the write fails too the loop locks out with the
heater still at 64 % and a log line saying the descent finished; if only the
read failed the heater goes to zero in one write. This shape predates step 5.

**E. Both kelvin rows are unreachable, so scenario 2 at the cold end produces
nothing.** The 1 K warning and the 5 K fault are gated on the tuner's `hold`
phase (`supervisor.py:1397` and `1415`), and `update_phase` puts the tuner in
`move` on any error over `move_error_k` (0.25 K). An error of 1 K, let alone
5 K, is therefore by construction in the phase that switches both rows off.
The below-28 % clause rescues the warning at the cold end and rescues nothing
else. Measured, with a delivered-power and a sink term added to the fitted
plant (it has neither; a rising Coldplate reading is scenery to it, which is
why the rising-coldplate bench row hedges with `if faulted` and has never
faulted):

| case | what happened | scenario | what it should have been |
|---|---|---|---|
| 30 K, heater suddenly delivers 50 % | sample 14.3 K LOW, railed at the ceiling, one hour, `tracking`, no fault, no warning (output 53 % is above 28 %) | 2 | fault, ramp-down |
| 10 K, heater suddenly delivers 50 % | sample 2 K low, railed, warning from the below-28 % clause only | 2 | warning (under 5 K), and a fault had it reached 5 K |
| 118 K, sink rising 2 K/h | error 2.5 K for two hours, no alarm of any kind | 1 | warning |
| 30 K, sink rising 20 K/h | band floor gave way, output reached 0 % at 58 min tracking inside 0.4 K, then the sample ran 16 K over setpoint, `tracking` | 1 | warning; the descent to 0 % is the right behaviour and is already there |

The rationale at `supervisor.py:176` says the ceiling check is what a
compressor failure looks like. A compressor failure is scenario 1 and rails the
loop LOW; the ceiling check is scenario 2.

**E2, found by the same runs: the residual is silent below about 40 K at any
hold.** `_missing_power` returns no opinion when `s.slope_k_per_s` is non-zero
and `tau < window`. With a real filter the slope estimate is never exactly
zero, so wherever the plant's tau is under the 30 s slope window the residual
never speaks, settled or not. At a settled 30 K hold `dQ` reads no opinion
from the first cycle. Between the 28 % floor and roughly 40 K there is
therefore no check at all today, because the kelvin rows are dead too.

**Plausible from reading**, not yet measured:

- F. With no trusted temperature or no curve, the descent falls back to
  `min_rate_pct_per_min`: 63 % to zero in over five hours against the 23
  minutes the docs promise. Reachable when the sensor faults before the filter
  primes, which is the state immediately after `ack` resets it. And
  `_rampdown_t0 or t` (line 1613) treats a start time of 0.0 as missing.
- G. `_coldplate_k` is refreshed only on a usable reading and never expires, so
  after the Coldplate channel drops out the residual runs on a stale sink for
  as long as the loop runs.
- H. `model_trusted` defaults True and is assigned only when settled; during a
  ramp or a frozen fault hold the status reports trust with no opinion. The
  PID prime at line 1238 falls back to `s.filtered_k`, a temperature, as a
  percent.
- I. `validate_control` checks none of the supervisor's new thresholds.
- J. Retired knobs are still named where an operator or a coworker reads:
  `window.py:1987` (a tooltip), `docs/recorder/file-interface.md:97`,
  `docs/recorder/gui.md:243`, `ramp.py:5`, `supervisor.py:679`,
  `docs/ltspm3/commissioning.md` (93 to 132, 522, 548, 930),
  `docs/ltspm3/control.md:34`.

## 3R.1 The bench stops depending on the date — rule 4

Goes first because every later gate is "the bench is green", and today the
bench is a different grader on every day it runs.

**Change.** `HeaterSupervisor` takes a `wall_clock` callable (default
`time.time`) beside `clock`, and `_missing_power` uses it. The bench harness
pins it to `DRIFT_T0_UNIX + virtual seconds`, so a bench run on any date is the
gauge-day run. `lschart.app` passes nothing; the recorder keeps real time.

**The step test uses the fast band.** `fault_at = max(fault_mw,
warn_sigma * widest)` with `widest` taken from `sigma_q_fast_w`, and the
history tuple carries that band. A step is a CHANGE and the monitor already
judges changes against the fast band; this is the loop agreeing with it, which
`_missing_power`'s docstring says it does and it did not. The LEVEL warning
keeps the full band: a level genuinely does drift with the gauge's age, the
warning keeps tracking, and the monitor's baseline is the judge of drift. Say
so in the two docstrings.

**The plant gets two knobs**, because 3R.1, 3R.4 and 3R.6 all need them:
`delivered_frac` (applied to `power_w` before the ODE) and `sink_offset_k`
(applied as `conductance_w(t, coldplate_k(t) + offset)`, with the Coldplate
reading following it). The sink correction is linearised and good to a few
kelvin of offset; tests stay inside that.

**Tests, written first.**

- The harness fixture pins the wall clock. A test asserts
  `s.sigma_q_w` at a settled 118 K hold is 1.44 mW / 3 to three figures,
  whatever the date.
- **A genuine fault at +60 days.** At each bench temperature above
  `min_output_pct` and above 40 K, arm, settle, cut delivered power by 12 %
  (the case §3.7's wrong-on-purpose docstring names and never tests), and
  assert the loop reaches RAMPING_DOWN inside `fault_window_s +
  fault_after_s`, with the wall clock pinned to the gauge AND to the gauge
  plus 60 days. It faults on the gauge day today and does not at +60 d; after
  the change both pass. The cold rows of the same case are 3R.6's.
- The existing "wrong on purpose" rows still do not fault at either date.

**Gate.** Bench green with the clock pinned to both dates; plan 2's replay
unchanged (the monitor's band did not move).

## 3R.2 CRASHED survives an idle cycle — rule 7

**Change.** The OFF-mode early return preserves every latched state, not just
LOCKED_OUT: a module-level `LATCHED = (LOCKED_OUT, CRASHED)` used there, in
`_disengage`'s `was_locked`, and in `_refuse_if_latched`, so the three cannot
drift apart again.

**Test, written first.** Bench, tracking; monkeypatch `_check_premise` to raise
once; restore it; step 20 cycles. State is CRASHED on every one, `s.reason`
still names the crash, `arm` raises `PermissionError` matching "crashed",
`acknowledge` clears it. The existing crash test stays as it is; it covers the
persistent sabotage.

**Gate.** That test, at all six temperatures.

## 3R.3 A failed read does not finish a ramp-down — rules 1 and 3

**Change.** In `_rampdown_target`, read with `default=self.output_pct`. If that
is None too, append a `COMMS` alarm and return None: hold this cycle, stay
RAMPING_DOWN, try again next cycle. Completion requires a KNOWN output at or
below `safe_output_pct`.

**Tests, written first.** At 118 K, lost sensor, ramp-down running for five
minutes. Then:

- one cycle where `get_analog_percent` raises `TransportError`: state still
  RAMPING_DOWN, `_rampdown_complete` False, `output_pct` unchanged, no
  LOCKED_OUT that cycle;
- five cycles where both read and write raise: same, plus the `COMMS LOST`
  alarm after `comms_fault_after_s`;
- comms restored: the descent resumes from where it was and completes.

**Gate.** Those three, and the lost-sensor row still locks out.

## 3R.4 The descent is bounded per cycle — rule 1

**Change.** In `_rampdown_target`, after `proposed = min(proposed, current)`,
also `proposed = max(proposed, current - step)` with `step =
_rate_pct_per_min(target_k) * dt / 60`: the one rate through the gain at the
temperature the descent is at, the same conversion the tracking limiter uses.
The curve still sets the path; this only says no single write may jump along
it. No second rate field: the one rate is the descent rate (Jeff, 2026-09-15).

**Tests, written first.**

- The measurement above, through the public path: a plant delivering 12 % less
  so the sample settles low and authority is exhausted (reachable once 3R.6
  has landed; until then the test drives `_rampdown_target` directly as the
  review did); from the first RAMPING_DOWN cycle onward, every consecutive
  pair of outputs differs by at most `_rate_pct_per_min(T) * dt / 60 +
  dac_step_pct`.
- The lost-sensor row gains the same bound. It asserts monotone today and
  not bounded.

**Gate.** Both, at all six temperatures. The lost-sensor descent time is
unchanged where the plant is the model.

## 3R.5 The descent never lacks a start — rule 1

Same function as 3R.4, separate commit: a descent that starts too gently is a
different defect from one that starts too hard.

**Change.** When the filter is not primed at the fault and the curve exists,
`_rampdown_from_k = feedforward.kelvin_for(current)`: the model's own
temperature for the present output, which is what an open-loop descent stands
on anyway. The floor-rate fallback is then only for a cryostat with no curve,
and the docstring says so. `_rampdown_t0 or t` becomes an `is None` test.

**Tests, written first.**

- `acknowledge`, re-arm, and drop the sensor on the first cycle, before the
  filter has primed. At 118 K the loop locks out in under 40 simulated minutes.
  Today it takes over five hours.
- A `VirtualClock` starting at 0.0 with a fault on the first cycle: the target
  descends.

**Gate.** Both.

## 3R.6 The kelvin rows become reachable, and they answer the two scenarios — rule 4

The kelvin rows are the temperature-based check cryo conditions require
(Jeff, 2026-09-15), and today neither can fire. Three changes, one rule, one
commit, because they are one sentence: *while the setpoint is not moving, the
error warns at a kelvin; at five kelvin it warns if the heater is at its floor
and faults if it is at its ceiling.*

**Change 1: gate on the TRAJECTORY, not the tuner.** "In `hold` only" meant
"while the setpoint is not moving", and the tuner's phase was a proxy for that
until the error itself started driving the phase. Both rows are gated on
`not self.ramp.ramping and self.smoother.settled` instead. The tuner's phase
stays a tuning choice and decides nothing about alarms. `_check_premise`'s
docstring and `safety.md` rule 4 say "while the setpoint is not moving"
instead of "in `hold`".

**Change 2: the floor is a warning, the ceiling is a fault.** `railed_low =
demand < band[0] + dac_step_pct`. Past `fault_error_k` railed LOW the loop
appends a warning that names the edge and says why it is not a fault: *less
heat than the model expects for this setpoint; the safe direction; the bath
has changed or the model is wrong high.* Past `fault_error_k` railed HIGH the
fault stands as it is: *the ceiling is not enough; the heater, the wiring or
the model is not what the loop believes.* The docstring at `supervisor.py:176`
describes both edges this way and drops the compressor sentence.

**Change 3: the residual speaks at a settled cold hold.** `_missing_power`'s
"the plant is faster than the slope window" applies when `|slope| >
model_check_slope_k_per_s`, the existing settled threshold, instead of when the
slope is non-zero. Below 40 K the residual then has an opinion at a hold and
none during a move, which is what the comment above it says it does. **This
one is measured before it is kept**: the test asserts that a settled hold at
10 and 30 K on the unperturbed plant sits inside the band for an hour. If the
estimator noise at tau = 9 s puts it outside, the change is dropped and the
kelvin rows carry the cold end alone, which is the arrangement Jeff described.

**Tests, written first, at every bench temperature.**

- **Scenario 2, cold.** Delivered power cut to 50 % at 10 and 30 K. Warning
  once the error passes 1 K; FAULT once it passes 5 K railed at the ceiling;
  RAMPING_DOWN after `fault_after_s`; LOCKED_OUT; `arm` refused; `ack` clears.
  Today: an hour at 14 K low, `tracking`, silent.
- **Scenario 2, warm.** The same cut at 60 K and above faults through the
  watt step inside `fault_window_s` (3R.1's row) and the kelvin fault is the
  backstop that fires if it does not.
- **Scenario 1.** The rising-coldplate row loses its `if faulted` hedge and
  asserts: the output falls, the warning appears once the error passes 1 K and
  stays, the floor warning appears once it passes 5 K at the hard minimum, the
  state is `tracking` throughout, the output never rises, and no fault ever
  fires. The 118 K case at 2 K/h warns from the cycle the error crosses 1 K.
- **A commanded sweep** still produces no kelvin warning and no fault, so the
  trajectory gate is not the old ramp allowance back under another name; and
  the 3 K move and 10 K sweep rows are unchanged.

**Gate.** All four.

## 3R.7 A stale sink is no opinion — rule 4

**Change.** Remember when the Coldplate was last read usable. Past
`sink_stale_s` (a `SupervisorConfig` field, default a few cycles; invariant 7,
never a constant in code) `_missing_power` returns None with the reason
"coldplate stale". No opinion already breaks the step history at line 1434, so
a sink that comes back cannot read as a step. Falling back to the model's locus
instead was considered and rejected: the switch itself is a step of
`Λ(T_c,model) − Λ(T_c,measured)`, which is exactly the false fault 3R.0.B is
about.

**Test, written first.** At 118 K, hold; drop `218.2` for ten minutes; the
loop keeps TRACKING, `residual_reason` says stale, `missing_power_w` is None,
no fault when the channel returns.

## 3R.8 Two small truths — rules 4 and 2

One commit, two lines, each with an assertion:

- `model_trusted` becomes `bool | None`, default None, and `_check_model` is
  the only writer. The status projection test pins that a ramping loop
  reports None, not True.
- The prime fallback at line 1238 is `operating_point_pct`, never a
  temperature. A unit test constructs the state and asserts the bias.

## 3R.9 The supervisor's thresholds are validated

`validate_control` gains, with a test per line in `test_config_control.py`:
`warn_sigma > 0`, `fault_mw > 0`, `fault_window_s > 0`, `fault_after_s >= 0`,
`warn_error_k > 0`, `fault_error_k >= warn_error_k`, `hard_min_pct <=
min_output_pct <= hard_max_pct`, and `sink_stale_s > 0` from 3R.7. The
monitor's `move_k > 0` joins `validate_monitor`.

## 3R.10 The documentation sweep

No code. Every place in 3R.0.J gets the current name or the current
mechanism, and the viewer's tooltip at `window.py:1987` names `warn_error_k`.
`commissioning.md`'s knee ramp-down section becomes a two-line note that it
was replaced by the one rate in step 5, pointing at `control.md`. `HANDOFF*`
and `AUDIT*` files are history and are not edited. Gate: `grep` for
`max_error_k`, `max_step_pct`, `rampdown_pct_per_min`, `smooth_tau_s`,
`anomaly_hold_s` finds nothing outside those archives and the "it used to be"
sentences in `control/`.

## The step order

| # | commit | rule | gate |
|---|---|---|---|
| 1 | 3R.1 wall clock injected; step test on the fast band; the plant's two knobs; genuine-fault row | 4 | bench green at gauge and gauge + 60 d |
| 2 | 3R.2 CRASHED survives an idle cycle | 7 | transient crash stays latched |
| 3 | 3R.3 a failed read holds the descent | 1, 3 | three comms tests |
| 4 | 3R.4 the descent is bounded per cycle | 1 | no write exceeds the one-rate step |
| 5 | 3R.5 the descent never lacks a start | 1 | lock-out in < 40 min from an unprimed filter |
| 6 | 3R.6 the kelvin rows reachable; floor warns, ceiling faults; residual at a cold hold | 4 | scenario 2 cold faults, scenario 1 never does |
| 7 | 3R.7 a stale sink is no opinion | 4 | no fault across a Coldplate outage |
| 8 | 3R.8 two small truths | 4, 2 | two assertions |
| 9 | 3R.9 thresholds validated | — | one test per line |
| 10 | 3R.10 the docs | — | the grep is empty |

Steps 1 and 2 are independent; 3, 4 and 5 touch one function and land in that
order; 6 needs 1's plant knobs. Nothing after step 1 may be merged on a bench
that is not pinned. If the first arm on the cryostat cannot wait for all ten,
**1, 2 and 6 are the three that change what the loop does to a real sample.**

## Decisions taken — Jeff, 2026-09-15

1. **Two scenarios, two answers.** A steady, model-explained change that
   reduces the heat needed is a warning however far it goes; a sudden,
   aphysical change is a fault and a ramp-down. That supersedes the earlier
   "warn, then ramp down" for the floor case: the descent to 0 % is the loop
   doing the right thing, and the floor is a warning, not a fault.
2. **The kelvin rows are the cryo check.** The watt residual is the primary
   check; the temperature rows exist because the plant leaves the residual
   nothing to say at the cold end. They stay, they become reachable, and the
   5 K ceiling fault is the temperature-based detector of scenario 2 there.
3. **There is no separate descent rate.** The knee rates were superseded by
   the one rate, and the first write of a descent obeys it. That is 3R.4 as
   written.

## Exit gate

- Bench green with the wall clock pinned to the gauge day AND to the gauge plus
  60 days, at all six temperatures.
- A heater delivering 12 % less power faults at both dates; the five
  wrong-on-purpose rows fault at neither.
- No cycle of any ramp-down in the bench moves the output by more than the
  one-rate step at that temperature plus one code.
- A one-off exception stays CRASHED until `ack`; a one-off `TransportError`
  during a descent changes nothing but an alarm.
- Scenario 2 at 10 and 30 K warns at 1 K and faults at 5 K; scenario 1 at every
  temperature warns and never faults; a commanded sweep does neither.
- Suite green, `ruff` clean, the 3R.10 grep empty, and `PID_PLAN.md`'s phase 3
  row points here.
