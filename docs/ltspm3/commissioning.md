# Commissioning the software PID

How the loop gets from "never talked to the hardware" to "armed and holding",
and what to measure once it is there.

Read [safety.md](safety.md) first. This document does not restate the eight
rules; it is the order in which they get *proved* on the real cryostat.

**Every stage has an exit gate.** The gate is a thing you observed, not a thing
you believe. Do not carry an unmet gate into the next stage — the whole point of
the staging is that each one bounds the damage the next one can do.

**Everything up to and including stage 4 is attended.** Somebody is in the room,
watching the viewer, with a hand on the panic path.

## Where this stands, 2026-08-28 14:25

**Stages 2 and 3 are largely already done, by hand, over the past week.** Anyone
reading the older documents will find "nothing on this cryostat has been talked
to yet" — that stopped being true on 2026-08-24 at 17:11.

| | |
|---|---|
| Recorder | live on `config-ltspm3-heater.yaml`, cadence **~2 s** |
| Sample | **148.75 K** |
| `ls218.aout1` | **66.598 %** |
| Coldplate / Magnet | 8.31 K / 6.91 K |
| 336 loop 2 (THE CHONKE) | 289.198 K, heater 2 at **99.8 %, still railed** |
| Data on disk | 163,627 rows, 2026-08-24 → now |
| Manual heater steps | **45**, from the first 0 → 2.0% at 2026-08-24 17:11 |

So the GPIB path is exercised, the write path is exercised, and there is real
data. What that does *not* give you is anything about the closed loop, and it
does not settle W1 — see stage 3.

**Two things in that history the procedure gets for free.** Both come from
segments where the output was genuinely constant — which is the only way to read
this data. Reading temperature at the *moments the heater changed* produces a
monotonically rising staircase that looks like a slow settling curve and is
nothing of the kind; the output was walked from 66.098% to 66.598% across those
four days, and at ~14 K/% that accounts for essentially the entire 140 → 149 K
rise.

**It settles, and it settles in hours.** At constant output, with the coldplate
steady at 8.30 K throughout:

| Held at | For | ΔT over the segment | Rate over the last quarter |
|---|---|---|---|
| 66.235% | 8.7 h | **+0.010 K** | −0.005 K/h |
| 66.287% | 6.3 h | +0.180 K | +0.000 K/h |
| 66.348% | 8.7 h | +0.200 K | +0.005 K/h |
| 66.528% | 21.3 h | +0.890 K | +0.008 K/h |
| 66.598% | 22.3 h | +0.340 K | +0.003 K/h |

**τ ≈ 620 s is holding up.** The one well-conditioned step in the dataset — the
+0.500% step at 08-24 17:31 — identifies **τ = 709 s at R² = 0.9973**. That is
the config's number, measured a second time, on this cryostat, four days ago.
Treat its gain (21.6 K/%) with more caution: that segment starts at 122.89 K
while still relaxing from a 70% excursion, so it is not a clean operating point.

**And a steady-state gain, from the settled ladder.** Seven settled points
between 66.235% / 143.75 K and 66.598% / 148.80 K give
**K ≈ 13.8 K/%** — pleasingly linear across that span, and far steeper than the
10.0 K/% quoted at the 63% operating point. This is what 0.3's warning about
`authority_pct` rests on, and it is a real measurement rather than an
extrapolation.

---

## Stage 0 — three changes before anything is armed

None of this is hardware work. All of it is code and config that the stages
below assume, so it comes first.

### 0.1 The fault ramp-down is too slow

Current: a single `rampdown_pct_per_min: 0.50`. From the 63.076% operating point
to zero that is **126 minutes** — long enough that "slowly reduce heat" stops
being a fault response and starts being a shrug.

Intended (Jeff, 2026-08-28), piecewise on the present output:

| Output | Rate |
|---|---|
| above 40% | **1.0 %/min** |
| at or below 40% | **2.0 %/min** |

63.076% → 40% takes 23 min, 40% → 0% takes 20 min: **~43 minutes end to end**,
about 3x faster than today. Still nothing like an emergency stop — a fault on
this cryostat is not an emergency, and the risk of a fast change remains larger
than the risk of a slow one — but it no longer takes two hours.

The knee is where it is because power goes as `pct²`: at 40% the heater delivers
about 40% of the power it was at the operating point, so the thermal shock per
percent is much smaller down there and there is less reason to crawl.

Invariant 7 applies — all three numbers are `SupervisorConfig` fields, not
constants in `control/`:

```yaml
rampdown_pct_per_min: 1.0             # above the knee
rampdown_knee_pct: 40.0
rampdown_below_knee_pct_per_min: 2.0  # at or below it
```

`validate_control` should reject a non-positive rate and a knee outside
`[hard_min_pct, hard_max_pct]`.

The trim rate limiter already does not apply during a ramp-down (only the hard
limits do), so raising these rates needs no change to `max_rate_pct_per_min`.

### 0.2 A ramp-down must latch

**This is a behaviour change, not a tuning change.** Today, if the condition
that started a ramp-down clears before the ramp completes, the loop quietly
abandons it: `_pid_target` checks sensor health first and, once health is back
to `OK`, falls straight through to normal tracking and sets `TRACKING`. Only a
ramp-down that runs all the way to `safe_output_pct` locks out.

Intended: **once a ramp-down begins, automation may not undo it.** A human is
involved, or it continues. Concretely:

- `RAMPING_DOWN` is left only by `acknowledge()`.
- The state check happens *before* the health check in `_pid_target`, so sensor
  recovery cannot re-enter the PID branch.
- `set_mode(PID)` refuses while `RAMPING_DOWN`, exactly as it already refuses
  while `LOCKED_OUT`.
- The ramp-down continues to completion, then locks out as it does now.

**`hold` still wins. A human emergency measure is the final authority** (Jeff,
2026-08-28). `panic_hold()` overrides a ramp-down in progress and freezes the
heater where it is, exactly as it does from any other state.

The latch excludes **automation**, and only automation. A recovering sensor may
not resume the loop; an operator may stop the ramp. That distinction is the
whole content of the rule, so the implementation must not collapse it into "the
ramp-down is uninterruptible" — write the test for the human path as well as the
automatic one.

`acknowledge()` remains the way out of a *completed* ramp-down, and remains the
only thing that clears the latch back to a re-armable state.

### 0.3 Know what the authority band does at both ends

Not a change — a trap to understand before stage 4, because it bites hardest on
exactly the first armed run.

**The band is a two-sided clamp, not a ceiling.** `clamp()` is
`max(lo, min(hi, pct))`, and it is applied to the PID output, to a MANUAL
setting, and to the value the PID primes from. Below-band is reachable *only*
as a fault ramp-down.

Two consequences, and the second is the one that applies today.

**If the present output is *below* the band, arming drives it up.** MANUAL does
not save you — a manual setting is clamped too — and neither does `hold`. Only
mode `OFF` stops writes. This is what would happen arming from a cold start at
0.0%: the loop would climb toward the band floor, rate-limited to a slow march
rather than a step, but climbing.

**If the band is centred somewhere else, the authority is lopsided.** The
cryostat is at 148.75 K on 66.598% (see the state block above). The shipped band
is 58.076–68.076%, centred for a ~99.6 K hold. From 66.598% that leaves:

| Direction | Room | ≈ at ~13 K/% here |
|---|---|---|
| up | 1.48% | ~19 K |
| down | 8.52% | far below anything you would hold |

The loop is not in danger of running away — the ceiling is doing its job — but
it has a very short leash upward and an enormous one downward, which is not an
envelope anybody designed. It is a band commissioned for a different operating
point, still in the config.

> **Re-centre `operating_point_pct` on the output that actually holds the
> temperature you intend to hold, be at that temperature before arming, and
> never arm while the present output is below the band.**

Note the gain is not the 10.0 K/% quoted for the 63% operating point. The
settled ladder in the live data measures **K ≈ 13.8 K/%** between 66.235% and
66.598%. **`authority_pct` of 1.0 therefore buys close to 14 K of authority up
here, against about 10 K at 96 K**, so re-centring the band is not only a matter
of moving it — narrow it too.

`check` prints the band. Read it every time the config changes.

### Exit gate for stage 0

- `pytest -q` and `ruff check .` clean.
- A test that a recovered sensor does **not** resume tracking mid-ramp-down.
- A test that the two-rate ramp-down crosses the knee and changes slope.
- `check` prints the band and the ramp-down rates.

---

## Stage 1 — the bench: no hardware at all

Everything here runs against the simulator and the historical logs. It costs no
cryostat time, so there is no excuse for skipping it.

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ltspm3.tools.replay "reference/logs/CD*/*.xls"
.venv/bin/python -m ltspm3.tools.steptest --points 63.076,65.0,67.0
```

`replay` is the only test on genuine data. `steptest` against the simulator is a
**rehearsal of the stage-6 protocol**: it exercises the same analysis that will
be applied to real cryostat data, so a mistake in the procedure shows up here
instead of after an hour of cryostat time.

Then a full armed run in simulation, on the real config with a sim driver, and
deliberate fault injection: a sensor glitch, a comms drop, a sustained fault to
completion and lockout, `hold` / `arm` / `acknowledge` over the file interface.

### Exit gate

- Replay: still ~12.8 rejections/day and **0 samples reaching FAULT** over 63 days.
- A simulated ramp-down runs at the new rates, latches, completes, locks out, and
  `acknowledge()` is the only way out.
- `hold` and `arm` round-trip through the command spool.

---

## Stage 2 — read-only against the real GPIB

**Mostly already done** — see the state block above. Keep this section for the
next cryostat, for a fresh install, and as the checklist to run against the data
you already have. `probe` forces every transport read-only regardless of the
config, so its safety does not depend on the config file being right.

```bash
python -m lschart -c config-ltspm3-read-only.yaml check
python -m lschart -c config-ltspm3-read-only.yaml probe
python -m lschart -c config-ltspm3-read-only.yaml run
python -m lschart.gui -c config-ltspm3-read-only.yaml
```

Then **record continuously for at least 72 hours**, ideally a week. This is not
a formality — it is the only way to get the one thing the reference logs cannot
give you, which is what these two boxes do *now*, on this GPIB board, at the
cadence you actually run. **Five days of exactly this already exist.** Analyse
it rather than starting a new run:

What to get out of it:

| | |
|---|---|
| Comms stability | dropped replies, timeouts, terminator handling (the 218 ends a reply with bare LF, the 336 with CRLF) |
| Glitch rate on Sample | the reference logs say ~1 event per 7 days on this input. Does that hold at 2 s? |
| Real sensor noise vs temperature | feeds calibration **C1** below — and this data spans 4.7 K to 149 K, which is most of the working range |
| Cadence | the run is at ~2 s, not the 1 Hz the config nominally asks for. Find out which is limiting: the bus, both boxes, or `read_status` |
| Readback resolution | `AOUT?` flickers between 66.595 and 66.598 with nothing commanded — 0.003%, well inside `readback_tol_pct` of 0.015%, but know it is there before W1 |
| The 336's loop 2 | heater 2 is **railed at 99.8%**. Watch it; do not touch it. It has no headroom, so anything adding heat to THE CHONKE simply wins |
| Column names moved mid-run | files up to `2026-08-26` carry `Cold Head` / `Shield`; `2026-08-26_part2` onward carry `Coldplate` / `Magnet`, for the same two physical inputs. Any analysis spanning the rename must accept both spellings or it silently drops five days down to two |

### Exit gate

- ≥72 h recorded with no unexplained comms fault. **Met.**
- `replay` over the *new* CSV shows no sample reaching FAULT. **Not yet run.**
- The glitch rate is consistent with the logs, or you understand why it is not.
  **Not yet checked.**

---

## Stage 3 — the write path, manual, no loop closed

`config-ltspm3-heater.yaml`: the 218's analog output is writable, there is no
`control:` section, and the loop is not involved. This stage proves the *write*
is trustworthy before anything automatic depends on it.

### W1 — measure the write settle time. This is the highest-value item.

**`verify_readback` on the 218 over GPIB is unverified and may be confirming a
stale value.** Measured on the 336 over USB: at 0 ms every readback was stale,
at 50 ms readbacks lagged by exactly one write, 80 ms+ was correct. **Both wrong
regimes look like success**, which is why this cannot be left to inference.

**This is the one part of stage 3 the past week did *not* settle.** Manual steps
prove the write lands eventually; they say nothing about whether the readback
that confirmed it was fresh.

Do it as a tight cluster around the present output, **not** by dropping to zero —
the sample is at 148.75 K on 66.598% and was walked there in steps. Values
0.01–0.08% apart are distinct on the DAC grid, and the whole test runs in
seconds against a 620 s fast pole, so the thermal excursion is nil. It is a far
smaller disturbance than the ±0.4% steps already done by hand on 08-26.

1. Write `ANALOG ... 66.630`, query `AOUT? 1` after 0, 25, 50, 80, 150, 300 ms.
2. Repeat with a *different* value each time — 66.63, 66.57, 66.65, 66.59,
   66.62 — so a lagged reply is distinguishable from a correct one. A constant
   value cannot tell you anything, which is precisely why a week of manual steps
   has not answered this.
3. Find the delay at which the readback matches the value **just written**, for
   twenty consecutive writes.
4. Set `write_settle_s` above it, with margin. Return the output to 66.598%.

Watch for the 0.003% readback flicker noted in stage 2: it is smaller than one
DAC code, so it cannot be confused with a stale value, but it means "readback
equals what I wrote" needs a tolerance, not equality.

Repeat at the poll cadence you will actually run, with the recorder polling both
boxes — bus contention is part of what you are measuring.

### W2 — prove the ceilings

- `max_output_pct: 70.0` rejects a write above it.
- `ipc.allow_analog_output: false` rejects `send analog` **in both directions**;
  `analog 0` needs the same permission as `analog 60`.
- `send heaters_off` reaches the 218's analog output and bypasses the source
  policy and the two power gates, and nothing else.
- `send ping` round-trips.

### Exit gate

- A measured `write_settle_s` for the 218 on GPIB, written into the config, with
  the twenty-write evidence recorded.
- Every ceiling above refused what it should refuse.
- The heater is back at the value it started the stage on, and the sample is
  back on the trend it was on.

---

## Stage 4 — first armed run, deliberately crippled

Now the loop closes, at a temperature you chose, with an authority band so
narrow the loop can barely do anything.

**Preconditions, all of them:**

1. Stage 0.2 landed — a ramp-down latches.
2. `write_settle_s` measured in stage 3.
3. The cryostat is **at** the temperature you intend to hold, with the cooler
   running and the shields cold — the regime the calibration curve was measured
   in.
4. `operating_point_pct` re-centred on the output that is actually holding that
   temperature right now (stage 0.3).
5. `check` printed a band that brackets the present output.

Then, in this order:

**4a — barely any authority.** `authority_pct: 0.1` (≈1 K of ultimate authority
at 10 K/%), `tuning.enabled: false`, `feedforward.enabled: false`, setpoint =
present temperature. Arm with no `--setpoint`.

```bash
python -m ltspm3 -c config.yaml check
python -m ltspm3 -c config.yaml run --arm
```

Watch for **at least 5× the fast pole (about an hour)**. You are looking for: the
loop stays `tracking`, the output moves in the right direction, readback agrees
every cycle, and nothing rails against the band.

**Give it a settled starting point.** Open loop, this cryostat comes to rest in
hours — an 8.7 h hold at 66.235% moved 10 mK end to end. So arm from a hold that
has actually settled, not from one still relaxing off a recent step, and the
loop's first job is trim rather than chasing a transient it did not cause.
An output ramping steadily at a fixed setpoint means something is still moving;
find out what before widening anything.

**4b — a deliberate ramp-down drill, attended.** Provoke one for real. The
cleanest provocation is to drop `fault_after_s` to something short and pull the
sensor's plausibility out from under the guard; the safest time is at low
temperature, where the fault response — losing heat — is the benign direction.

Confirm, on the real hardware: it ramps at 1 %/min, changes to 2 %/min crossing
40%, **does not resume when the sensor comes back**, completes, locks out, and
`acknowledge()` is the only way out. That is rules 1 and 7 proved on the
cryostat rather than in a test.

**4c — widen.** `authority_pct` to 1.0, then enable `tuning` and `feedforward`
one at a time, each with its own watched hour. Enabling both at once means a
surprise has two possible causes.

**4d — a commanded sweep.** 0.5 K/min over a few kelvin, through the ramp. Watch
the tracking error against `max_error_k` plus the ramp allowance, and watch the
**end** of the ramp specifically — the decaying allowance is what stops a
completed sweep from becoming an anomaly hold.

### Exit gate

- ≥1 h tracking at 4a, with readback agreeing every cycle.
- A real ramp-down that latched, completed and locked out.
- A completed sweep with no anomaly hold at either end.
- `model_error_k` inside `model_trust_k` at settled points, or a written
  explanation of why the curve does not describe this regime.

---

## Stage 5 — online

Unattended, at the intended operating point, with the viewer open on another
machine and `status.json` being read.

Run for a week before treating it as routine. What ends the stage: a week with
no unexplained hold, no ramp-down, and a stability figure you are willing to
quote.

**Abort and drop back a stage if any of these happen:** a readback disagreement
that is not explained by `write_settle_s`; a ramp-down nobody can account for;
the loop railing against either end of the band; `model_error_k` drifting past
`model_trust_k` while settled; any hold lasting longer than `anomaly_hold_s`
without a cause you can name.

**Rollback is always the same thing**: `send hold`, then stop the process.
`on_exit: hold` leaves the heater where it is, which is what you want on a live
cryostat.

---

## Stage 6 — the calibration campaign

Only after stage 4's gate. Every one of these produces a number that goes back
into config, and several of them supersede numbers that are currently derived
from a single step response in the historical logs.

Run them roughly in this order — C1 is free, C2 gates most of the rest.

### C1 — sensor noise versus temperature

**Costs nothing**: it comes out of the stage-2 recording.

Fit rms against temperature and compare to the claimed `rms ≈ 1.36e-6·T²`,
floored ~1.8 mK. This settles the parked question: the bench 336 reads
0.44–3.03 mK at ~296 K where the 218 sample channel is claimed at 109 mK at
290 K. Three things differ at once, so neither number is currently wrong —
recording the 218 under quiet conditions is the clean resolution.

**Updates:** the noise table in [thermal-response.md](thermal-response.md), the
filter's noise model, and whether millikelvin control really is a
low-temperature-only capability.

### C2 — step tests: local gain and time constant

**The highest-value hardware measurement available**, and the one the tuning
actually runs on. `τ ≈ 620 s` today comes from *one* clean step response.

Protocol (`ltspm3/tools/steptest.py`), in MANUAL, at each of three operating
points:

1. Let the temperature settle.
2. Step the heater by `step_pct` — large enough to dominate the noise, small
   enough to stay linear **and inside the authority band** (a MANUAL setting is
   clamped, so re-centre `operating_point_pct` for each point).
3. Hold for **at least 5× the fast pole** — about an hour at τ = 620 s.
4. Step back and repeat, which also tests for hysteresis.

`K = ΔT_final / Δpct`; τ from the exponential fit.

**Budget ~2 h of cryostat time per point**, plus settle. Three points is the
minimum that gives a schedule rather than a single number.

**Part of this is already done — check what the existing data gives before
booking cryostat time.** The 08-24 17:31 step yields τ = 709 s at R² = 0.9973,
and the settled ladder gives K ≈ 13.8 K/% around 66.2–66.6% / 144–149 K. What is
missing is *other temperatures*: everything on disk sits between 143 K and
149 K, which is one point on a schedule, not a schedule.

```bash
python -m ltspm3.tools.steptest --from-csv data/ltspm3-heater_2026-08-27.csv
```

**Read the R², every time, before you believe a τ.** A 0.061% step held for
21 hours in this dataset "identifies" τ = 137,345 s — 38 hours — at **R² =
0.162**. There is no exponential in it; the fit is describing noise, and the
number it produces is confidently, catastrophically wrong. `analyse_step`
already returns the R² in `OperatingPoint.note` and refuses a gain of the wrong
sign, but nothing stops a poor fit with a plausible-looking gain from being
pasted into a schedule.

Two practical rules follow:

- **Step big enough to be seen.** At ~14 K/% and a few tens of mK of noise, a
  0.06% step is ~0.85 K of signal spread over an hour and the drift wins. The
  0.5% step that identified cleanly moved 10.8 K. Somewhere in between is the
  right answer for a trim-sized test; below ~0.2% is not worth the hour.
- **Step once and hold.** Several of the hand steps in the existing data are
  up-down doublets held for tens of seconds, and every one of them fails
  identification with "the temperature moved against the step" — the segment
  begins after a *down* step while the cryostat is still rising from the
  preceding *up* step. Doublets test hysteresis only after each leg has settled.

**Updates:** `TuningConfig.schedule` — paste the literal `steptest` prints —
and `SupervisorConfig.response_lag_s`. Both `K` and `τ` vary with temperature
*and* with what the coldplate is doing, so record the coldplate temperature with
each point (`OperatingPoint.coldplate_k` exists for this).

### C3 — re-run replay against real armed data

Feed the stage-4 CSVs through `replay.py`. The guard thresholds are currently
calibrated against 63 days of *legacy* logs at 2–20 s cadence; this is the first
chance to check them at the live cadence with the loop closed.

**Updates:** `SensorGuardConfig` — `max_slew_k_per_s`, `corroborate_slew_k_per_s`,
`curvature_ratio`, `fault_after_s` — and `CoherenceConfig`.

### C4 — does the dither actually deliver sub-code resolution

Hold a fixed setpoint for ≥1 h with `dither: true`, then ≥1 h with
`dither: false`, same conditions. Compare rms and the Allan deviation at 60 and
600 s.

Prediction: one code is ~100 mK at the operating point; the fast thermal pole
should low-pass the dither to under 1 mK of ripple, so the dithered run should be
dramatically quieter. If it is not, the fast pole is not what we think it is —
which is a C2 result, not a dither result.

### C5 — closed-loop verification

With the C2 schedule loaded, command a small setpoint step — inside
`max_error_k`, so 0.5 K — through the ramp and watch the response.

IMC tuning predicts a **first-order closed loop with no overshoot** and a time
constant equal to `τ_cl`. Check both. Overshoot means the schedule's `K` or `τ`
is wrong at that point; a settling time that is not `τ_cl` means the same thing.

Do it in both phases: HOLD's long `τ_cl` and MOVE's short one, and confirm the
hysteretic switch between them does not chatter.

### C6 — the stability figure

The number you actually quote. Hold for ≥6 h at the operating point and compute
the Allan deviation. The logs predict 6.1 mK @ 4 s, 4.1 mK @ 60 s, 2.5 mK @
600 s — about 2x worse than 1/√N, because the noise is correlated (lag-1 +0.51).

If the real figure is much better than that, the loop is doing more than the
measurement can justify and you should suspect the measurement. If much worse,
go back to C5.

### C7 — feedforward regime validity

At each settled point, compare the measurement against `kelvin_for(output)`.

The steady-state curve was measured **with the cooler running and the shields
cold**, and nothing in a temperature log distinguishes that regime from a warm
one. This is the check that says whether the curve describes today's cryostat.

**Updates:** `model_trust_k`, `max_feedforward_pct`, and — if the disagreement
is large — `feedforward.enabled: false`, letting the integral do the work. That
is slower, and it is always correct.

---

## Keep a commissioning log

One entry per stage: the date, the config file and its git SHA, what was
observed, and the gate that was met. Every number in `docs/ltspm3/` traces back
to a measurement; these will too, and in a year the log is the only thing that
will say which cryostat state a given number was measured in.

**Where a measured number contradicts memory, the number wins.**
