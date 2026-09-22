# PID Phase 2 — the monitor

Part of [PID_PLAN.md](../PID_PLAN.md). **Goal:** a judge outside the loop
that reads the recorder's files, applies the model's band, catches both
archive events as warnings, and warns about nothing else.

`ltspm3/monitor/`. A separate process like the viewer: no port, no
commands, ever. Runs whether or not the loop is armed — which is most of the
cryostat's life so far — and is the reference implementation the supervisor's
in-loop check (plan 3 §3.4) is tested against.

**Status: BUILT 2026-09-14.** `ltspm3/monitor/` — `source.py` (a live tail and
an archive replay, one `Sample` shape), `judge.py` (the residuals and the
verdicts), `report.py` (`plant.json` and the daily CSV), `__main__.py`.
37 tests, of which 11 are the replay on genuine data. **The live soak is what
is left, and two defects in §2.6 have to be fixed before one counts** — see the
exit gate;
§2.4 has the replay row by row and §2.5 the two thresholds.

**And the soak WAS blocked on code, which this plan twice said it was not.**
Checked against the cryostat's own `data/` on 2026-09-14 before starting it:
`RecorderTail` took the last `*.csv` by name out of the whole directory, and
that was `sweep-20260905-131753.csv` — the sweep tool's grading table, nine
days stale, no `Sample` column. Worse, `plant_` sorts after `ltspm3-heater_`,
so the first verdict this process wrote moved the tail onto **its own daily
log** and it never read the recorder again. Both are one defect: the reader was
choosing by name in a directory it does not own. It now selects on the
recorder's own `filename_prefix`, and it prints which file it is reading every
time that changes, because a monitor with nothing to read looks exactly like a
monitor with nothing to say. Three tests, one of them the cryostat's real
directory. Nothing else about the soak needs code.

### The one design decision this phase added

**Jeff, 2026-09-14: recalibrate at most once per cooldown, and typical erroring
behaviour is so large as to be unmistakable from these variations.**

That makes the trailing baseline mandatory rather than optional, and it decides
what the baseline is kept in.

`missing_power_w`'s *level* carries two slow things: the calibration
(`bias_q_w`, 4.7 mW at 118 K) and the campaign drift (0.29 mW/day, unmodelled
and therefore unsubtracted). Over a months-long cooldown the full band reaches
52 mW — 31 K — and judges nothing. So the judge alarms on the **departure from
a slow baseline**, against `sigma_q_fast_w`, which does not grow with time at
all. One gauge per cooldown is then enough.

Two rules make that safe: the baseline **freezes** whenever the verdict is not
typical, or it learns the fault it is judging; and it is slow against a fault
and fast against the drift — 6 h, against a fault that declares itself in 30
minutes and a drift that takes days.

**The baseline is kept as a fraction of the delivered power**, and choosing
that variable took two wrong answers first:

| kept in | a 1 % calibration error is | why it fails |
|---|---|---|
| watts | 12 mW at 18 K, 20 mW at 94 K | the baseline chases the sweep |
| kelvin | 0.1 K at 18 K, 2.6 K at 94 K | a factor of **26** — Λ′ falls tenfold while P doubles |
| **fraction of P** | **the same number everywhere** | it is what a series resistance in a voltage-driven heater *is* |

Kelvin is the trap: the model's **shape** error is flat in kelvin (`band.py`
measured 0.135 K), so it looks right up until the thing being absorbed is the
**level** instead. Shape error and level error are different quantities with
different shapes.

## 2.1 Inputs and outputs

| | |
|---|---|
| reads | the recorder's CSV tail (`t` from the monotonic `Time` column, never the timestamp — [archive/AUDIT-2026-09-10.md](../archive/AUDIT-2026-09-10.md) finding 4); `status.json` for `control` |
| writes | `plant.json` beside `status.json`: arrays not objects, `SCHEMA_VERSION`, `os.replace`; `plant_YYYY-MM-DD.csv`, one row per cycle |
| config | its own `monitor:` section: `warn_sigma: 3`, `warn_after_s`, `settle_taus: 3`, `window_s` |

## 2.2 Per cycle

| residual | computed | band | verdict |
|---|---|---|---|
| `δQ` | `missing_power_w` against a slow baseline, `dT/dt` from a regressed slope | `max(3 × sigma_q_fast_w, warn_mw)` | typical / warn / no opinion |
| `δT_c` | coldplate − locus, `TAU_BATH_S` pole | `3 × TC_RMS_K` | typical / warn |
| cold head | 1st/2nd Stage stepped against their own recent scatter | `stage_sigma`, floored | typical / warn |
| τ ratio | pole fit after a heater move holds, against `tau_s(T)` | 0.85–1.10 | typical / warn / no opinion below 25 K |
| noise | trailing rms over `noise_window_s` vs `1.36e-6·T²`, floor 1.8 mK | < 2× | typical / warn |
| fault-level | `δQ` **stepping** `fault_mw` inside `fault_window_s` — **reported**, never acted on | 10 mW in 30 min | latched flag |

Persistence: a verdict changes only after `warn_after_s` out of band, in **both**
directions, with a Schmitt trigger at `hysteresis_frac` so a residual sitting on
its threshold still declares itself. No opinion within
`max(settle_taus × τ(T), slope_window_s)` of a heater move, outside the table,
below `min_output_pct`, or while `δT_c` is atypical (one cause, one alarm).

## 2.3 The replay — the test on genuine data

`python -m ltspm3.monitor --replay reference/cooldown-10/` prints every
verdict change with its time. Pinned in `tests_ltspm3/test_monitor.py`:

| window | must |
|---|---|
| 2026-09-09 18:06 | `δT_c` warn within 30 min; `δQ` typical or no opinion |
| 2026-09-10 11:33 | `δQ` warn within 30 min, −5 ± 2 mW; **not** fault-level; `δT_c` typical |
| three post-recal holds | typical; `δQ` within ±1 mW of the drift line |
| `trace-ladder-20260905` | no warn; ≥ 27 of 30 rungs return a τ ratio in 0.85–1.10 above 40 K |
| `trace-sweep-20260902` | no warn outside masked windows |
| 2026-09-04 12:07 recalibration | no verdict change |
| **whole archive** | **zero** fault-level flags — this is what sets `fault_mw` |

**False-alarm budget: < 1 warning per week on a settled hold.** Every verdict
change in the replay is read by a person once; that is this phase's pause.

## 2.4 What the replay actually says — 2026-09-14

`python -m ltspm3.monitor --replay reference/cooldown-10/`, 1,063,448 samples,
about 30 s. Pinned in `tests_ltspm3/test_monitor.py`.

| the table's row | result |
|---|---|
| 2026-09-09 18:06: `δT_c` warn within 30 min | **NOT MET, and deliberately** — see below |
| …and `δQ` typical or no opinion | **PASS** |
| 2026-09-10 11:33: `δQ` warn within 30 min, −5 ± 2 mW | **PASS** — warns at **11:46**, fourteen minutes, **−5.08 mW** (−0.76 % of delivered) |
| …and **not** fault-level | **PASS** — it warns and never faults, because the fault is a step and this one is 5.2 mW |
| …and `δT_c` typical | **PASS** — the sink did not move, the heater did |
| 2026-09-04 12:07 recalibration: no verdict change | **PASS** — a *reading* changed and the cryostat did not |
| three post-recal holds: typical | **PASS** — 106 h of settled hold, no warning |
| `trace-sweep-20260902`: no warn | **PASS** — the fit's own training data reads as typical |
| `trace-ladder-20260905`: ≥ 27 of 30 rungs with τ in 0.85–1.10 above 40 K | **NOT MET** — see below |
| whole archive: **zero** fault-level flags | **NOT MET** — three in 57 days, all real steps: 2026-07-17 (21.7 mW, the cooldown from 300 K), 2026-08-28 (17.9 mW) and the 09-10 **reseat** (16.4 mW) |

**The 2026-09-09 row, and it is the most interesting thing this phase found.**
The row asks `δT_c` to warn within 30 minutes. It cannot, and the reason is not
a shortcoming of the check — it is that **the event is not visible in the
channels the monitor has.**

The manifest measured it: at a fixed output the cold-head channels stepped at
18:06:04 by **Coldplate −7.6 mK, 1st Stage −80 mK, 2nd Stage −19 mK**, and the
sample then fell **0.37 K over three hours**. The coldplate step is a quarter
of that channel's own rms and a tenth of its band, so `δT_c` cannot see it and
should not: a bar that saw 7.6 mK would warn continuously.

So the stages were added beside it, as §2.2 asked — `cold_head`, a step
detector on the 1st and 2nd Stage against their own recent scatter. It **does**
catch the event, at 18:17, eleven minutes. And then the replay answered the
question the row had not asked: **a 70–170 mK step on the 1st Stage happens
five times in the four settled days before the event and six times in the
disturbed day and a half after it.** The 09-09 step is 90 mK. It is not
exceptional in the only channel that shows it.

That is Jeff's ruling of 2026-09-14 arriving from the data rather than from the
instruction: *typical erroring behaviour is so large as to be unmistakable from
these kinds of variation.* A cold-head step of a tenth of a kelvin, several
times a week, **is** these kinds of variation. Tuned to catch 09-09 the check
costs nine warnings a week against a budget of one; set where ordinary steps
are quiet it is silent across the whole archive, and it still catches the
failure it exists for — a compressor degrading moves these channels by
**kelvins**, not by a tenth of one.

So the bar is set where the archive is quiet, and 09-09 is not caught. What
stays on the record instead is the manifest's own conclusion, which this
replay independently confirms: *something moves the sample's steady state at
constant power that `Λ(T_s) − Λ(T_c)` cannot see.* It is REFIT_PLAN §2.5's
open diagnostic, not a monitor bug, and no threshold in this file closes it.

**One thing the cold-head check corroborates on its way past.** Five steps in
four settled days before the fault; six in the one and a half disturbed days
after the reseat — four times the rate.
[archive/HANDOFF-2026-09-13.md](../archive/HANDOFF-2026-09-13.md) measured the
same period as carrying **twice the wander over 300–1200 s**, by a completely
different method. Two independent signatures, same conclusion: reseated is not
repaired.

**The τ row.** Sixteen rungs above 40 K return a ratio at all; twelve are in
band. The shape is not noise and it is worth reading:

```
45-48 K 0.84   57-58 K 0.90   63-64 K 0.97   70-71 K 0.98   77-103 K 1.01
```

Above 60 K it is 0.97–1.01 — better than the row asks for. Between 45 and 58 K
it is systematically **low by 10–16 %**, and waiting longer makes it worse, not
better: at a reach of 5 the same rungs read the same and three of them stop
returning a ratio at all, and at 8 the ladder's dwells are too short to reach it
and **nothing** is measured. So this is not a settling problem. It is the bottom
of where a pole is measurable against a 2–4 s cadence and a dwell sized by
`plan_sweep` for the *steady state* rather than for τ.

It is left as it is rather than tuned into passing. The refit's own graded τ
agree to 6.7 % worst over 40–120 K (`analysis/plot_tau.py`), so the model is not
what is 16 % out here — the monitor's one-pass pole fit on a short dwell is. And
τ is a **secondary** indicator: it never faults, and it is not what catches a
heater event. Widening the band to 0.80 would make the row pass and would mean
nothing.

**The fault-level row, and this one is a finding rather than a defect.** The two
flags are **2026-09-04 11:34 (−21.9 mW)** and **2026-09-10 14:42 (−11.6 mW)** —
the wire reseat and the post-fault reseat. Both are real events on the heater
circuit, both are larger than `fault_mw` = 8 mW, and a monitor that stayed quiet
through them would be the broken one. The row was written expecting the archive
to contain no fault-sized excursions; it contains two, and REFIT_PLAN §7.2
measured them independently at a few tenths of an ohm each.

So **the archive does not set `fault_mw` by being clean.** What it says is that
8 mW is below the size of a connector being reseated, which is the number plan 3
needs before it turns `fault_mw` into a ramp-down: a threshold there would ramp
the cryostat down every time somebody touched the wiring. That is a decision for
Jeff at plan 3 §3.4, with the two measured events in front of it, and it is the
one thing this phase hands forward rather than settles.

## 2.5 The two thresholds — Jeff, 2026-09-14

**5 mW to warn, 10 mW to fault.** At 118 K those are 3.0 K and 6.0 K at the
local gain, which is where §1's "warn at a kelvin, fault at five" lands once the
band under it is measured rather than guessed.

**They are floors under the band, not replacements for it**, because the two
constraints say different things and both have to hold:

| | |
|---|---|
| the **band** | do not alarm inside the model's own uncertainty. 3σ is **1.4 mW** at a settled 118 K but **8.8 mW at 180 K on a 5 K/min sweep**, where the heat-capacity term dominates — a flat 5 mW there warns about every sweep |
| the **floor** | do not alarm about anything smaller than this however confident the model is. Typical erroring behaviour is unmistakable; a 2 mW excursion at a hold is not what this exists to catch |

So the threshold is `max(warn_sigma × σ_fast, warn_mw)`. Neither constraint can
be violated by the other.

### 1 and 2. Two defects the floor exposed, both of them coupling

**1. The baseline must freeze on the BAND, not on the warning.** Keyed to the
warning, the moment a 5 mW floor went in the 2026-09-10 event **disappeared
entirely**: its residual is −5.08 mW against a 5 mW floor, so the verdict stayed
typical, so the baseline kept learning, so it walked onto the fault within a few
hours — and the fault-level flag never fired either, because by the time the
excursion reached 11.6 mW the baseline had moved most of the way to meet it.

Reporting and learning are different decisions. The floor says *do not bother me
about small things*; the band says *this is outside what the model calls
ordinary, do not absorb it*. **Being told to ignore small things must not teach
a monitor that a large thing is normal.**

**2. A residual sitting on its threshold needs hysteresis.** The 09-10 residual
crosses 5 mW **four minutes** after the event and then hovers there. Under a
strict continuous-600-s rule every dip back under reset the timer and the
warning did not land for **104 minutes**. With a Schmitt trigger —
`hysteresis_frac: 0.8` — it lands at **14**. The alarm was not slow because the
cryostat was subtle.

### 3. A fault is a STEP, not a level — and that resolved the §1 conflict

**Jeff, 2026-09-14: it should trigger fairly quickly or not at all.**

Under a *level* test at 10 mW the 09-10 event faulted at **+190 min**, which
looked like a slow creep to a threshold and contradicted PID_PLAN §1's "the
09-10 event is a warning". The archive says it is neither:

```
11:30   +0.13 mW      11:35   -2.95      11:40   -5.18      11:45   -5.16
12:00   -5.04         13:00   -4.88      14:00   -5.31      14:30   -5.36
14:40  -10.72   <- the connector being reseated, a different event
```

**The residual is a step.** It reaches full size in **seven minutes** and then
sits flat at −5 mW for three hours. That is not an accident of this event, it is
identically what the physics does: with `C dT/dt = a·P(u) − [Λ(T_s) − Λ(T_c)]`,
the residual is exactly `−(1 − a)·P(u)` from the instant the delivered fraction
`a` moves, whatever the sample then does. The 14.11 mW at +190 min was never the
fault developing — it is Jeff handling the connector at 14:40.

So the fault condition is now **a step of `fault_mw` within `fault_window_s`**
(30 min), measured as the range of the residual inside a trailing window. A
residual that takes hours to reach a level did not step, and faulting on it is
the worst available outcome: a ramp-down, hours late, for something that was
never sudden. **Slow degradation has its own fault and it is a different one** —
authority exhausted, railed at the band with the error past `fault_error_k`
(PID_PLAN §1), which no window here gates.

This resolves the conflict rather than trading one number against another: at
10 mW the 09-10 event is a **warning at +14 min and never a fault**, which is
what §1 said all along. Across 57 days the fault level fires **three times**,
all genuine steps: 2026-07-17 (21.7 mW, the cooldown from room temperature),
2026-08-28 (17.9 mW) and the 09-10 reseat (16.4 mW).

**And across 106 hours of settled hold it says nothing at all** — the three long
post-recalibration holds and the 32 h after the reseat produce **zero**
warnings of any kind, against a budget of one a week.

Three things the step test needed before it behaved:

- **the range, not a departure from the band crossing.** The reseat landed three
  hours into an existing warning; a test anchored to the last band crossing
  would have been blind for exactly the window in which the connector was
  handled.
- **the history breaks at every no-opinion sample**, rather than merely not
  growing. A range taken across a commanded move measures the command: on the
  09-05 ladder that read as a 21 mW step and flagged fault-level nine times.
- **the transient gate is floored at the judge's own slope window.** Below 30 K
  the plant settles in under ten seconds while the slope is still being
  regressed over five minutes, so the gate expired while every number
  downstream of it was still half made of the previous regime — a 99 mW step on
  the ladder's cold end. Two settling times, and the longer one governs.

### What the live soak found — 2026-09-15

**Two residuals go blind at the daily file roll, and one of them for ten
minutes.** At exactly `2026-09-15T00:00:00`, where the recorder rolls to a new
daily CSV:

```
00:00:00  cold_head: typical -> no opinion  (no cold-head reading)
00:00:00  noise:     typical -> no opinion  (not enough samples)
00:00:15  noise:     no opinion -> typical
00:10:16  cold_head: no opinion -> typical
```

**It fails safe** — `no opinion` is the honest answer and rule 4 already says it
must never read as green — so this is not a false alarm and not a defect in the
verdicts. What makes it worth a row here is stage 5: seven unattended days is
seven midnights, each with `cold_head` unable to speak for ten minutes.

`noise` recovering in 15 s is `RollingFit(noise_window_s = 60)` refilling, which
is expected. `cold_head` taking **616 s** is not explained by that: it needs
every channel in `stage_channels` to return `None` from `s.aux`, or every
`RollingFit` to be short, for the whole stretch. `stage_baseline_tau_s` is 600 s
and lines up suspiciously well. **Not diagnosed** — the two candidates are the
336's aux columns being absent from the new file's opening rows, and the stage
fits or baselines resetting across the rollover. It wants a test that rolls a
file underneath the judge and asserts what each residual says on the far side.

Do it before stage 5, not before arming: nothing here bears on whether the loop
may close.

## 2.6 Two defects the live path had, and the archive cannot show

**Both FIXED 2026-09-17**, and fixing the second turned up a third in the same
line of code — §2.7. Both were found by displaying the verdict in the viewer,
which is the first time anything put it where a person would see it. **Neither
is reachable from the replay**: the archive is read once, front to back, from a
file whose relative-time column only ever increases — so each fix arrives with
a test that feeds the tail a file the replay could never have been, and the
replay itself is unmoved by either (41 warn transitions, 3 fault-level, the
09-10 event still at 11:46 and −5.08 mW).

### 1. A recorder restart stopped the clock, and it never started again

The recorder resets its CSV's `Time` column to zero when it restarts, while the
*same* daily file carries on — so one day's file can contain several ascending
runs rather than one.

`_Clock.at` builds `origin + relative_s` and ratchets the result with
`max(t, self.last)`. That ratchet pays AUDIT-2026-09-10 finding 4: a
daylight-saving fold, where a *new file* opens at a stamp earlier than the last
sample, and `open_file` is what handles it. **A reset inside one file is not a
fold.** It is a new origin, and the ratchet turns it into a permanent freeze:
`t_s` pins at the highest value the file ever reached and nothing later can
exceed it.

Every judgement is in that timebase — `in_transient` compares
`s.t_s - self._move_t`, `_Persist.update` waits `after_s` of it, the baseline
ages by it — so once it stops, **no gate ever expires**. The visible symptom is
every residual stuck at `no opinion` reading `within 3 tau of a heater move`,
through a hold that has been settled for hours.

**Measured, before it was fixed.** From `data/plant_2026-09-17.csv`: the clock
froze at **09:04:33**, the day's first restart, and **26,314 of that day's
46,236 samples** carry the frozen stamp. It went on judging for eleven hours
after that, because a freeze is silent until something moves the heater — the
20:12 arming move then latched the gate against a clock that could not advance,
and `plant.json` was writing `t_s` **14.7 h behind `epoch`** every two seconds.
So the symptom and the cause are eleven hours apart, which is what made this
one hard to see and is worth remembering about any frozen clock.

**The fix keeps the fold protection, because the two cases are
distinguishable** — a fold arrives with a *new file* and a backward **epoch**,
an in-file restart arrives with a backward **relative** column and a forward
epoch. `_Clock` remembers the previous `Time` cell and, when it goes backwards
inside one file, takes a new origin from that row's own stamp; `restarts`
counts those beside `rewinds`, so which one happened is on the record. The
`max(t, self.last)` ratchet stays as the last word, for a fold and a restart
landing together. A restart also **bumps `segment`**, which is the only channel
into `Judge._reset_history` — and it is the right event to drop measured
history on, since the loop was re-armed and the heater moved. The cost is that
the six-hour baseline restarts too, which is what a new daily file has always
done.

Two tests, and **both were checked against the old code and fail there**:
`test_a_restart_inside_one_file_is_a_new_origin_and_not_a_fold` (the clock
advances 120 s across the seam where it advanced 0) and
`test_the_judge_goes_on_judging_after_the_recorder_restarts` (the transient
gate expires). The second needs a **long first run**: the freeze only lasts
until the new run's relative column overtakes the old one's high-water mark,
and the real reset was 9 h in. A short first run passes against the bug, which
is the trap for whoever edits these next.

### 2. `tau` pinned the overall verdict to `no opinion`

The top-level `verdict` was `max` by rank over `q, tc, tau, noise`, and `tau`
answers `no move to measure` unless there is a step to fit. At a hold there
never is — so the headline read `no opinion` on **every one of 2026-09-17's
46,236 samples** while `missing_power` read `typical` on 85 % of them.

This is not a wrong answer, which is what makes it easy to miss: §7's rule is
that no opinion must never read as green, and it did not. It is an
*uninformative* one, permanently, and a reader who checks only the headline
learns nothing from it.

**Fixed as Jeff chose it (2026-09-17): the worst thing the judge actually
knows, and it says who it speaks for.** The headline is the worst of the
residuals that *have* an opinion, `verdict_for` names them, and `no opinion`
still wins when nothing can speak — so silence is never dressed up as green,
and the honesty moved into saying what the word covers rather than into
refusing to say one. `Judge._headline` is the whole rule, five tests on it, and
`plant.json`'s `SCHEMA_VERSION` goes to **2**: `verdict_for` is additive but
the same cryostat in the same state now reads `typical` where it read
`no opinion`, and that is a meaning moving.

## 2.7 And a third, in the same line: a warning that reached nobody

Found by reading that line in order to fix §2.6's second defect, not by a test.

`max` was taken over `(q, tc, tau, noise)` — **four of the six residuals.**
`cold_head` and the latched `fault_level` were published as rows and were not
in the headline at all. So a compressor going off moved the cold head by
kelvins, `cold_head` warned in its own row, and the summary line went on
reading whatever the other four happened to say. That is §7's *a green light
outside the table is a lie*, and it is the opposite direction from §2.6's
defect: not uninformative but wrong, and wrong the unsafe way.

Both are gone with one rule, since the headline now aggregates all six.
`test_a_cold_head_warning_reaches_the_headline` is the assertion, and against
the old line it reads `no opinion` where it should read `warn`.

**One thing deliberately NOT changed.** A latched `fault_level` reads `warn` in
the headline, because `warn` is the most severe word this monitor has —
`TYPICAL, NO_OPINION, WARN` is the whole vocabulary and the fault residual has
always reported `WARN`. Giving it a fourth word would change what every
residual row says, what the viewer's palette must resolve, and what MATLAB's
reader will expect, which is a separate commit and a question for Jeff. The
`fault_level` row is what distinguishes a step from a level meanwhile.

## Exit gate

- The replay table green in `pytest`; `fault_mw` and `warn_after_s` written
  into config from what the replay required. **PARTLY MET 2026-09-14** -- seven
  of nine rows green, and the two that are not are §2.4's, one of them a finding
  rather than a defect. `warn_after_s` is 600 s, which with the Schmitt trigger
  is what puts the 09-10 event at fourteen minutes rather than at 104.
  `warn_mw: 5` and `fault_mw: 10` are Jeff's, and the fault is a **step inside
  `fault_window_s`** rather than a level -- §2.5.
- **One diurnal cycle** beside the live recorder, `plant.json` current, the
  post-repair residual inside the band throughout, every warning explained.
  **NOT MET, and now unblocked.** A cycle was run on 2026-09-15, but §2.6's
  first defect meant a soak was only judging until the recorder next restarted,
  and the second meant the headline carried no information at any point in it.
  **Both fixed 2026-09-17**, so the cycle is worth running and is what starts
  when the fixed monitor is next started. Read it knowing the baseline is
  young for the first six hours after any restart, which is now what a restart
  costs instead of everything.

  **The length was 72 h and nothing justified it.** What establishes the
  false-alarm rate is the replay across 63 days of archive, already green in
  `pytest`. What the live run adds is that the tail path works on that machine,
  that `plant.json` stays current, and that it survives without crashing. The
  defensible length is a day: a 16 mK diurnal term is in the band and
  `measure.py` fits a 24 h harmonic, so one diurnal cycle is the longest
  measured timescale short of the campaign drift, and no soak of any length
  covers that one.

  **The judge's state is reconstructed from the recorder's log, not from this
  process's uptime** — started on 09-15 it read the 09-14 file from the top and
  caught up in seconds. So the evidence is in the CSV the recorder has been
  writing all along, which is what makes the shorter gate defensible rather
  than merely convenient. The recorder does NOT need restarting — the monitor
  is a separate process that tails the CSV, holds no port and sends no
  commands. It needs the file-selection defect above (fixed 2026-09-14) and one
  command in a second window:

  ```
  cd /d C:\Coding\Python\lakeshoreABJ && git pull && .venv\Scripts\python.exe -m ltspm3.monitor -c config-ltspm3-heater.yaml
  ```
- `lschart status` does not read `plant.json` yet; that is phase 5's work in
  the viewer and does not block the soak, which only needs the file to exist
  and be current. **MATLAB's `plant()` landed 2026-09-22** and reports the
  file's age and staleness rather than hiding either, because the judge is a
  separate process and can be stopped while the recorder is perfectly healthy.
