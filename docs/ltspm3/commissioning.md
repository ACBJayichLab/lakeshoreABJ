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

## Where this stands, 2026-09-03 14:25

**Stage 0 is done. Stages 2 and 3 are largely done, by hand.** Anyone reading
the older documents will find "nothing on this cryostat has been talked to yet"
— that stopped being true on 2026-08-24 at 17:11.

| | |
|---|---|
| Recorder | live on `config-ltspm3-heater.yaml`, cadence **2.0 s**, **6.0 days uptime, 0 dropped cycles** |
| Sample | **180.57 K**, settled — 69.027% held **25.7 h**, +0.019 K/h |
| `ls218.aout1` | **69.027 %** — and the ceiling is 70.0%, so **0.97% of headroom** |
| Coldplate / Magnet | 8.47 K / 7.04 K |
| RAD SHIELD / 1st / 2nd Stage | 40.02 K / 28.61 K / 4.08 K |
| 336 loop 2 (THE CHONKE) | 289.06 K, heater 2 at **100 %, still railed** |
| Data on disk | 422,852 rows over 237 h, 2026-08-24 → now |
| Commands applied | 39, **0 refused** — no ceiling has ever actually been exercised |

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

**Extended on 2026-09-03, and it holds.** Four more settled points, now
spanning 155–181 K:

| Held at | For | Settled T | Local K |
|---|---|---|---|
| 66.998% | 11.5 h | 154.28 K | — |
| 67.517% | 10.7 h | 161.16 K | 13.3 K/% |
| 68.359% | — | 172.10 K | 13.0 K/% |
| 69.027% | 25.7 h | 180.63 K | 12.7 K/% |

**K ≈ 13.0 K/% across 155–181 K**, easing gently as it climbs. The simulator,
asked the same question, says 12.2–12.6 K/% — agreement to about 5%, which is
the first independent check the calibrated response model has had at this
temperature.

---

## Stage 0 — three changes before anything is armed

None of this is hardware work. All of it is code and config that the stages
below assume, so it comes first.

### 0.1 The fault ramp-down is too slow — **LANDED 2026-09-03**

Was: a single `rampdown_pct_per_min: 0.50`. From the 63.076% operating point
to zero that is **126 minutes** — long enough that "slowly reduce heat" stops
being a fault response and starts being a shrug.

Now, piecewise on the present output (Jeff, 2026-08-28):

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
constants in `control/`. The rate is chosen on where the heater is *now*, so a
long ramp changes slope as it crosses the knee rather than being fixed when the
fault began:

```yaml
rampdown_pct_per_min: 1.0             # above the knee
rampdown_knee_pct: 40.0
rampdown_below_knee_pct_per_min: 2.0  # at or below it
```

`validate_control` rejects a non-positive rate and a knee outside
`[hard_min_pct, hard_max_pct]`.

The trim rate limiter already does not apply during a ramp-down (only the hard
limits do), so raising these rates needs no change to `max_rate_pct_per_min`.

> One test had quietly depended on that not being true.
> `test_the_band_caps_heat_without_compelling_it` bounded *every* single-cycle
> output change by `max_step_pct`, and passed only because the old 0.5 %/min
> over one cycle happened to fall under that bound. Its loop ends in a fault
> ramp-down, where the limiter is bypassed by design; at 2.0 %/min the step is
> 0.14% and the assertion fired. It now measures only the arming march, which
> is what it was always about.

### 0.2 A ramp-down must latch — **LANDED 2026-09-03**

**This is a behaviour change, not a tuning change.** Before it, if the condition
that started a ramp-down cleared before the ramp completed, the loop quietly
abandoned it: `_pid_target` checked sensor health first and, once health was back
to `OK`, fell straight through to normal tracking and set `TRACKING`. Only a
ramp-down that ran all the way to `safe_output_pct` locked out.

Now: **once a ramp-down begins, automation may not undo it.** A human is
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
only thing that clears the latch back to a re-armable state — reachable as
`send ack`, the CLI verb, MATLAB's `ack()`, or the viewer's **Clear lockout**.

Pinned by `tests_ltspm3/test_rampdown_latch.py`, which covers both halves: a
recovered sensor does not resume tracking, and `panic_hold()` still stops the
ramp dead and freezes the heater where it stands.

### 0.3 Know what the authority band does at both ends

Not a change — a trap to understand before stage 4, because it bites hardest on
exactly the first armed run.

**The band caps heat; it does not compel it.** The **ceiling** is hard and
immediate — `min(target, band_hi)` after the rate limiter, so coming down onto
it is never rate limited, because less heat is never the dangerous direction.
The **floor** bounds what the PID may *ask* for (`_apply_band_to_pid` sets
`out_min`), not what the DAC must carry.

> Both halves of that changed on 2026-08-31 and this section described the old
> behaviour. `clamp()` used to run on the output *after* the rate limiter and
> so undid it from below: arming at 0% wrote 62.076% in a single step, past
> `max_step_pct`. It also meant this loop could not hold any temperature whose
> steady-state output lay below the band — at base temperature it commanded
> operating-point power and then faulted.

Two consequences, and the second is the one that applies today.

**If the present output is *below* the band, arming still drives it up** — the
PID may not ask for less than the floor — but as a genuinely rate-limited march
now rather than a step. From a cold start at 0.0% that is about five hours to
the floor, which is the same order as the `approach_rate_k_per_min` the
setpoint would take to cross the same range.

**`hold` does now save you.** `panic_hold()` is `abort_ramp()` + `set_mode(OFF)`,
and `OFF` writes nothing at all, ever — the heater keeps exactly the value it
has, wherever that is. It used to switch to `MANUAL`, and a manual setting is
clamped, so a hold taken outside the band moved the heater on the next cycle:
told to freeze at 20% it reported "holding 20.000%" and wrote 62.080%. Both
panic actions disengage the loop now. A **MANUAL** setting is still clamped,
which is why the panic path no longer uses it.

**If the band is centred somewhere else, you may be outside it entirely.** The
cryostat is at 180.57 K on 69.027% (see the state block above). The shipped band
is **62.076–64.076%** — `authority_pct` is 1.0, centred for a ~99.6 K hold.

This section first said 58.076–68.076%, copied from a worked `check` example in
`running.md` that was five times too wide, and concluded that the output sat
inside the band. **It does not, and the gap has since grown.** At 69.027% the
output is now **4.95% above the ceiling**, which at the measured 13.0 K/% is
about **64 K** of heat the loop would take out on its first cycle. Simulated
from the older 66.598% state, arming looked like this — and every line of it is
worse from where the cryostat sits today:

| cycle | | |
|---|---|---|
| 1 | heater 66.600 → **64.070%** | the ceiling, applied at once |
| 5 | `holding` | the loop cannot reach 148.75 K from 64.076% |
| 300 | `ramping_down` | the anomaly hold expired |
| 2000 | heater 1.14%, sample ~13 K | ~2.2 h later, then locked out |

**And there is a second trap up here that did not exist at 63%: headroom.**
`hard_max_pct` is 70.0 and the output is 69.027%, so the band has **0.97%**
of room above it — about 12.6 K. Any band centred on the present output is
clipped by the ceiling on one side, so it is lopsided by construction and the
loop has far less authority to *add* heat than to remove it. That is the safe
asymmetry, but know it is there before you read a `holding` as a fault.

The ceiling is doing its job. The point is that doing its job, from here, is a
35 K step down followed by a fault — so this is not a lopsided envelope to work
within, it is one to leave before arming.

> **Re-centre `operating_point_pct` on the output that actually holds the
> temperature you intend to hold, be at that temperature before arming, and
> never arm while the present output is outside the band — in *either*
> direction.**

Above the ceiling is the worse of the two, and the asymmetry is deliberate:
clamping **down** is instant, because less heat is never the dangerous
direction, while climbing **up** to the floor is rate limited. Being below the
band costs you a slow march; being above it costs you a step.

**Read the band from `check`, never from a page** — including this one. That is
what the number above being wrong for a week cost, and a test now pins every
worked band in `docs/ltspm3/` against what `SupervisorConfig` actually produces.

Note the gain is not the 10.0 K/% quoted for the 63% operating point. The
settled ladder in the live data measures **K ≈ 13.8 K/%** between 66.235% and
66.598%, and **≈ 13.0 K/%** over the wider 155–181 K span added on 09-03.
**`authority_pct` of 1.0 therefore buys close to 13–14 K of authority up here,
against about 10 K at 96 K**, so re-centring the band is not only a matter of
moving it — narrow it too.

`check` prints the band. Read it every time the config changes.

### Exit gate for stage 0 — **MET 2026-09-03**

- `pytest -q` and `ruff check .` clean. **768 passing.**
- A test that a recovered sensor does **not** resume tracking mid-ramp-down.
  **`test_a_recovered_sensor_does_not_resume_tracking_mid_ramp_down`.**
- A test that the two-rate ramp-down crosses the knee and changes slope.
  **`test_the_ramp_down_crosses_the_knee_and_changes_slope`.**
- `check` prints the band and the ramp-down rates. **Only when
  `control.enabled` is true** — with control disabled it says `control:
  disabled` and prints neither, which is worth knowing before you go looking
  for a band that is not there.

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
| Cadence | 2.0 s, which is what the config asks for and what `check` reports — a budget of 26 transactions, ~1.30 s per cycle. [cryostat.md](cryostat.md)'s "1 Hz by config" was stale. Note the headroom is thin: 1 Hz would not fit without trimming the transaction budget |
| Readback resolution | `AOUT?` flickers by 0.003% with nothing commanded — but **only at some commanded values**. See below |
| The 336's loop 2 | heater 2 is **railed at 99.8%**. Watch it; do not touch it. It has no headroom, so anything adding heat to THE CHONKE simply wins |
| Column names moved mid-run | files up to `2026-08-26` carry `Cold Head` / `Shield`; `2026-08-26_part2` onward carry `Coldplate` / `Magnet`, for the same two physical inputs. Any analysis spanning the rename must accept both spellings or it silently drops five days down to two |

#### The `AOUT?` flicker, characterised

Worth its own note, because the one-line version above was true and misleading.

| held at | samples | flicker |
|---|---|---|
| 66.598% | 117,000 over 65 h | reads **66.595 on 3.2%** of samples — 3,372 excursions, ~104/h, almost all exactly one sample long |
| 69.027% | 46,050 over 25.6 h | **none at all.** 46,050 identical readings |

So it is **not a general property of the box** — it is specific to certain
commanded values. At 0.003% it is smaller than one DAC code (0.01%), which puts
it in the instrument's own formatting of that code rather than in the output.

**Nothing is done about it, and nothing needs to be**, but know where it lands:

- **Write verification is unaffected.** `readback_tol_pct` is 0.02% here and
  0.015% in `SupervisorConfig`; both are far larger, so `_confirm` accepts it.
- **`_where_the_heater_is()` sees it**, and that is the only live-loop exposure.
  It re-reads `AOUT?` after any cycle that wrote nothing — which is exactly the
  steady holding regime where the flicker occurs — and that value is the base
  for the rate limiter's step, the fault ramp-down's step, and the value a
  manual hold adopts. Against `max_step_pct` of 0.02% a 0.003% error is 15% of
  one cycle's step; it is zero-mean and one sample long, so it does not
  accumulate, and it can only shift the quantised code when the target already
  sits within 0.003% of a code boundary. Bounded at one code, ~0.13 K.
- **Analysis of the CSV must allow for it.** `steptest` has a
  0.005% deadband for exactly this; anything else reading `ls218.aout1` and
  looking for steps needs its own. Without one, the 08-24 → 09-03 data shows
  14,509 apparent output changes instead of 99.

The recorder logs the flicker faithfully, and that is correct — it should
record what the instrument said, not what we think it meant.

### Exit gate — **MET 2026-09-03**

- ≥72 h recorded with no unexplained comms fault. **Met.** 422,852 rows over
  237 h, **zero** non-empty `Validity` cells, and no cadence gap over 6 s since
  the 08-28 restart.
- `replay` over the *new* CSV shows no sample reaching FAULT. **Met.** 237.3 h,
  189 rejections, **0 reaching FAULT**; every one `suspect` and self-healing.
- The glitch rate is consistent with the logs, or you understand why it is not.
  **Understood.** 19.1 rejections/day against ~12.8 in the legacy logs — but
  that is the wrong comparison, because these 237 h contain a 4.7 K → 180 K
  warm-up with 45+ manual heater moves and the legacy figure is mostly quiet
  holds. **Over the settled 25.7 h hold at 69.027% the rate is zero.** All 189
  fell in the dynamic stretch, in bursts of 14–15 samples right after a
  commanded step, which is the filter re-priming rather than the sensor
  misbehaving.

`replay.py` takes `.xls`; to run it over a recorder CSV, build a `ChartLog`
from the CSV and call `replay()` directly — the pipeline under test is the same
either way.

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
the sample is at 180.57 K on 69.027% and was walked there in steps. Values
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
4. Set `write_settle_s` above it, with margin. Return the output to 69.027%.

**`write_settle_s` is a `Transport` field (default 100 ms), not an instrument
one** — `lschart/transport.py`, applied to every write on that link. Nothing
under `instruments:` accepts it; put it in the `transport:` block.

**And the retry loop already covers most of this.** `_confirm` re-reads `AOUT?`
up to five times, 100 ms apart, comparing against the value *just* commanded —
so a stale readback shows the **old** value, fails the comparison, and is
retried rather than accepted. The one case that could still slip through is a
step smaller than `readback_tol_pct` (0.02%), where old and new are
indistinguishable. That is why step 2 above insists on values far enough apart,
and it is why a cluster of ±0.4% hand steps never answered this.

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
- The heater is back at the value it started the stage on (**69.027%**), and the
  sample is back on the trend it was on.
- **`max_output_pct` has actually refused something.** As of 2026-09-03 the
  spool has applied 39 commands and refused **0**, so no ceiling in this system
  has ever been exercised against the real hardware. `send analog 70.5` is the
  cheapest possible test: the driver *raises* rather than clamping, so no bytes
  reach the 218 and nothing moves.

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

**Done at one point, 2026-09-03, and the model is right.** Over the settled
25.7 h hold at 180.56 K the sample's rms is **44.1 mK**; `1.36e-6·T²` predicts
**44.3 mK**. That is the quadratic model confirmed near the top of the working
range, and it settles the parked question in favour of the quadratic fit rather
than the linear one. Still outstanding: the same check at low temperature,
where the 1.8 mK floor is what is being claimed and where the campaign below
will pass through anyway.

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
python -m ltspm3.tools.steptest --from-csv data/ltspm3-heater_2026-09-0*.csv
```

Several files may be given at once and are stitched in time order — necessary,
because `Time` restarts at midnight in each file and the holds worth
identifying routinely run past it. The heater column is auto-detected
(`heater_pct` when a software loop was running, else `ls218.aout1`).

> **This command did not work until 2026-09-03**, and the version printed here
> before was `--from-csv data/ltspm3-heater_2026-08-27.csv`. It defaulted to a
> `heater_pct` column that only exists when a `control:` section is running, so
> against every file this cryostat has ever produced it silently dropped every
> row and then reported the file as too short. Three further defects behind it,
> all of which produced numbers rather than errors: each hold was paired with
> the step that *ended* it rather than the one that caused it (gain wrong by
> the ratio of the two steps, with R² = 0.9999 attached); the final and usually
> best-settled segment was never analysed at all; and the 0.003% `AOUT?`
> flicker noted in stage 2 was read as a heater step, which pushed the start of
> each hold past the whole transient. Pinned now by
> `tests_ltspm3/test_steptest_from_csv.py`.

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

#### How long to hold — and why R² will not tell you

**A short hold does not give you a noisy answer. It gives you a confident wrong
one.** Fitting a single exponential over a window much shorter than τ cannot
separate "slow rise, large amplitude" from "fast rise, small amplitude" — the
early part of both is a straight line — so the fit trades τ against K and lands
somewhere plausible. Simulated against the calibrated two-pole response
(τ_fast = 620 s carrying 90% of the step, τ_slow = 14400 s the rest), stepping
1.0% at 65% output, where the true steady-state K is 13.38 K/%:

| hold | K (K/%) | τ (s) | R² | K vs truth | τ/K |
|---|---|---|---|---|---|
| 5 min | 4.53 | 126 | **0.947** | 34% | 27.7 |
| 10 min | 7.38 | 234 | **0.968** | 55% | 31.7 |
| 15 min | 9.15 | 328 | **0.981** | 68% | 35.9 |
| 20 min | 10.26 | 406 | 0.989 | 77% | 39.6 |
| **30 min** | **11.42** | **520** | 0.997 | **85%** | **45.5** |
| 45 min | 12.03 | 606 | 1.000 | 90% | 50.4 |
| 60 min | 12.23 | 642 | 1.000 | 91% | 52.5 |
| 90 min | 12.39 | 674 | 0.999 | 93% | 54.4 |
| *truth* | *13.38* | *620* | | | *46.3* |

At five minutes the fit reports τ five times too small and K three times too
small, **at R² = 0.95** — which reads as a good fit and is not one. R² measures
how well an exponential describes the window you gave it, and a rising line is
described beautifully by the early part of any exponential you like.

Two things follow, and they pull in opposite directions:

- **Do not fit anything held for under ~20 minutes** at these temperatures, no
  matter what R² says. Below that the numbers are not merely imprecise, they are
  wrong by factors.
- **90 minutes is not necessary either.** IMC tuning uses `Kp = τ/(K·τ_cl)`, and
  the two biases are in the same direction, so they largely cancel in the ratio:
  τ/K passes through its true value at **around 30 minutes**, and drifts *away*
  again by 45–90 min as the slow pole leaks in. A 30-minute hold gives a better
  `Kp` than a 90-minute one, while the individual K and τ it prints are each
  about 15% low.

**So: hold ≈ 3τ, and record the window with every number.** Thirty minutes is
right where τ ≈ 620 s. Where τ is genuinely shorter the window shrinks with it —
which matters below, because the model assumes τ is temperature-independent on
the evidence of a single step at 137 K, and that is exactly the assumption a
campaign spanning 4–200 K is in a position to break. In practice: watch the
viewer, find the time to reach about two thirds of the move, and hold three
times that.

**Any τ in these documents without a stated fit window should be read with
this table in hand**, including the τ = 709 s above.

#### The descending staircase — the campaign to actually run

Every tread **is** a step test, so nothing is spent settling twice. Start at the
top, where the cryostat already is, and walk down; each step is one command,
then hands off for the hold.

| # | output % | expected T | step down | expected ΔT | local K | noise |
|---|---|---|---|---|---|---|
| 1 | **69.0** | 178 K | *(start)* | — | 13.9 K/% | 43 mK |
| 2 | **68.0** | 165 K | −1.0 | −13.4 K | 13.0 | 37 mK |
| 3 | **67.0** | 152 K | −1.0 | −12.9 K | 12.9 | 31 mK |
| 4 | **66.0** | 139 K | −1.0 | −13.1 K | 13.2 | 26 mK |
| 5 | **65.0** | 125 K | −1.0 | −13.4 K | 13.3 | 21 mK |
| 6 | **63.5** | 105 K | −1.5 | −20.2 K | 13.1 | 15 mK |
| 7 | **62.0** | 92 K | −1.5 | −13.3 K | 7.0 | 11 mK |
| 8 | **60.0** | 78 K | −2.0 | −13.2 K | 6.2 | 8 mK |
| 9 | **58.0** | 67 K | −2.0 | −11.6 K | 5.4 | 6 mK |
| 10 | **55.0** | 52 K | −3.0 | −14.6 K | 4.4 | 4 mK |
| 11 | **52.0** | 41 K | −3.0 | −11.8 K | 3.5 | 2 mK |
| 12 | **49.0** | 31 K | −3.0 | −9.4 K | 2.8 | 1.8 mK |
| 13 | **45.0** | 22 K | −4.0 | −9.4 K | 2.0 | 1.8 mK |
| 14 | **41.0** | 15 K | −4.0 | −6.6 K | 1.4 | 1.8 mK |
| 15 | **37.0** | 11 K | −4.0 | −4.5 K | 0.9 | 1.8 mK |
| 16 | **32.0** | 7.3 K | −5.0 | −3.5 K | 0.5 | 1.8 mK |
| 17 | **28.0** | 5.7 K | −4.0 | −1.6 K | 0.3 | 1.8 mK |

Temperatures are `SteadyStateCurve.kelvin_for()`, and **most of this ladder is
predicted rather than measured.** The curve's measured knots are:

```
43.00% -> 18.2 K      65.34% -> 129.7 K
63.08% -> 99.6 K      65.90% -> 137.3 K
64.34% -> 116.5 K     66.48% -> 144.9 K
64.97% -> 124.9 K     66.95% -> 151.1 K
                      67.78% -> 161.8 K
                      68.46% -> 170.7 K
```

Nine of the ten sit between 63% and 68.5%. **Between 43% and 63.08% — that is,
between about 18 K and 100 K — there is not a single measured point**, so the
whole middle of this ladder is a two-point power law being asked to cover 20
percentage points of output and 80 K of temperature. It shows: local gain runs
7.3 K/% at 62.5%, 9.3 K/% at 63.0% and 13.1 K/% at 63.5%, and that jump is the
interpolation changing character at the 63.08% knot, not the cryostat doing
anything.

So read the table as: **trustworthy near the top (±2 K), a guess from rows 7–17**,
and worse than a guess above 68.46%, which is the highest output ever measured —
at 69.027% the curve predicts 178.4 K against 180.6 K observed.

**Filling that hole is the most valuable thing this campaign does.** Rows 7–17
are not confirming a curve, they are measuring one where none exists.

Three things this layout is doing:

- **The step grows as the gain falls.** Local K spans 13.9 K/% at the top and
  0.3 K/% at the bottom, a factor of 46. A constant percentage step would give
  13 K of signal up top and 30 mK at the bottom, and the bottom half of the
  schedule would be unfittable. Sizing each step for ≳1.5 K keeps the
  signal-to-noise above 37:1 everywhere and over 1000:1 below 30 K.
- **Below ~28% there is nothing to measure.** 25% → 4.96 K, 20% → 4.31 K,
  10% → 4.01 K: the heater has no authority against the cooler down there, so K
  and τ are not merely small but undefined. The ladder stops at 28%.
- **200 K is not reachable.** 70% — the ceiling, and `hard_max_pct` — predicts
  192 K, and the curve reads about 2 K low up there, so the real top is ~195 K.
  Going higher means raising `max_output_pct` above the point where anything has
  ever been measured.

**The interesting result is τ(T), and the model has no opinion worth trusting.**
`ResponseParams.tau_fast` is a constant 620 s at every temperature, inferred
from one step at 137 K. Heat capacity falls steeply as the cryostat cools, so τ
almost certainly does too — and if it does, the lower half of this ladder holds
far faster than 30 minutes and the whole campaign shortens. Watch the first
couple of low-temperature treads and set the hold from what they do, not from
this page.

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
