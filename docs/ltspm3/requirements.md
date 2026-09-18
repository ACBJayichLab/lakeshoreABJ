# Requirements — Jeff, 2026-09-17

**This is the document the loop is graded against.** It replaces the
requirements table in `PID_PLAN.md` §1, which had been rewritten several times
by people other than Jeff as measurements came in, until the express goal of the
project — move a setpoint faster than by hand, and hold it better — had been
reinterpreted as "a rate is a ceiling, not a promise" and "for a static hold the
software PID has nothing to offer". Neither of those was Jeff's.

The first section is his answers, verbatim, to eight questions asked once the
drift was noticed. The second is what was taken from them. The third is what the
bench measured against them the same day. Change the first section only by
asking him.

## 1. The answers

Asked, in this order, on 2026-09-17:

1. **What does "faster than before" mean in minutes?** (before: a hand step
   and a 26 min wait at 118 K; recommended 5 min for a 2 K move)
   > I agree 5 min for 2K at 118K

   **Superseded the same evening — see [§1b](#1b-the-follow-up-once-five-minutes-had-been-measured).**
   Five minutes was agreed before anyone had watched it happen. It is kept here
   because what was asked and what was answered is the point of this section.

2. **What does "better stability at the actual setpoint" mean?**
   > I care about slow wander primarily. Equal or better 0.25, 1, and 5 minute
   > noise seem like good benchmarks. i.e. not degrading noise at a minimum and
   > vastly improving from the open loop long term stability

3. **Weak hold or strong hold?** (the armed config held twelve times slower
   than the plant; recommended the strong direction)
   > Strong direction sounds good, note the open loop temperature is fairly
   > stable as is

4. **Is 5 K/min a real requirement, and for what?**
   > 5K/min was supposed to be a safe limit, it might be overly restrictive

5. **The authority band** (held at ±0.25 % pending a power gauge; recommended
   widening by decree)
   > Shouldn't this move with the setpoint? I view this as a threshold for when
   > delivered vs actual power is wrong. Changing the setpoint should not matter
   > for that question (i.e. a different setpoint trivially requires a different
   > power)

6. **What do you want the loop for, day to day?**
   > Sometimes just manually plugging in temperatures and jumping around,
   > otherwise programmatic sweeps where I am going to measure at a series of
   > temperatures (with holds during measurement)

7. **Documentation** (recommended archiving the dated files and rewriting the
   requirements in his words)
   > After coming to conclusions from talking with me, I would suggest writing
   > down my answers and doing a documentation and code sweep

8. **The filter** (no low-pass, median of three, from 2026-09-11)
   > I believe the current filter is correct

## 1b. The follow-up, once five minutes had been measured

Asked on 2026-09-17, after the retune was armed and the first moves watched.
**These supersede answer 1.** He opened unprompted:

> I am still noticing that the loop response feels very slow. The approach is
> soft with a long tail. 2 K in 5 minutes was excessively generous. The loop
> should be much faster. I think delta 2 K at 120 K in ~30 seconds with a
> settling time of 2-5 minutes would be more appropriate. ie fast setpoint
> approach and then maybe slight adjustment to settle down

9. **How should "~30 seconds" be graded?** (a 2 s cadence and a median of three
   give the loop a 3.0 s dead time; `delay_floor: 4` then puts a floor near
   60 s under a 95 % arrival, and 30 s would need a 1 s cadence)
   > 2s cadence should be maintained

   and, on whether to buy the last 15-20 s by halving the stability margin:
   > Keep 4, accept ~60 s on a 2 K move

10. **What overshoot is acceptable on a 2 K move at 120 K?**
    > Up to ~250 mK

11. **What has to be true for the move to count as settled?**
    > Within 50 mK and staying

12. **`max_rate_k_per_min` is doing two jobs — the trajectory's rate, and,
    divided by the gain, the heater's own slew limit. How should it be split?**
    > Keep 5 K/min, add a separate heater slew limit

Two questions he asked back, answered here because the next person will ask
them too:

* **Is the derived dead time verified?** Yes, twice.
  `tests_ltspm3/test_filter_chain.py` pins the derived 3.0 s and then measures
  an actual step through the filter chain and asserts the derivation equals it.
  The median's lag is observed; the half-cycle zero-order-hold term is sampling
  rather than filtering and is the one term taken on argument.
* **Does the ~60 s floor get worse for bigger moves?** No — it is fixed
  overhead. Arrival is roughly `span / rate + corner + 2 * tau_cl`, and only
  the first term scales with the move. At 5 K/min that is about 12 s per
  kelvin on top of a constant ~40 s.

## 2. What was taken from them

| | requirement | how it is graded |
|---|---|---|
| **move** | a 2 K move at 120 K is **95 % of the way there in 60 s**, overshooting by no more than **250 mK**, no rail, no fault. A fast approach, not a gentle one | `tests_ltspm3/test_stage_4e_fast_move.py` on the bench; on the cryostat, the recorder's CSV after a `send setpoint --software` |
| **settle** | and is then **within 50 mK and staying, inside 2-5 minutes** — the slight adjustment after the approach | the same test; `tuning.hold_error_k` is the same 50 mK, so the gate and the loop's own hysteresis cannot disagree |
| **hold, noise** | Allan deviation at **15 s, 1 min and 5 min no worse than open loop** | the same test, armed against open loop on the same plant |
| **hold, wander** | **much better than open loop** at long averaging times | `analysis/hold_quality.py` against the 2026-09-15 open-loop night, on the cryostat. The bench cannot grade this: its plant has no slow disturbance |
| **hold, direction** | the loop has **real authority** at a hold — faster than the plant, not slower | `hold_speed` below 1 |
| **rate** | 5 K/min is a **safety ceiling on the trajectory**, not a target, and it stays | `ramp.max_rate_k_per_min`. Measured 2026-09-17: raising it made moves worse, because it was also setting the next row |
| **slew** | how fast the **heater output itself** may move is a **separate number**. It was derived from the rate ceiling through the gain, and that coupling is what made a move soft with a long tail | `supervisor.max_output_rate_pct_per_min`. The two are different quantities and are configured apart |
| **band** | the authority band **follows the setpoint**, always. Its width is control room, sized to the model's level error plus the overdrive a fast move needs. It is **not** the "is the delivered power wrong" check — that is the watt residual's job | `HeaterSupervisor.target_band_centre_pct` asks `has_curve`, not `feedforward.enabled`; `authority_pct` 1.0 |
| **use** | single setpoints typed by hand, and programmed ladders with a measurement at each rung. Both are the same operation: a move, then a hold | `send setpoint --software`; a ladder tool is the natural next thing |
| **filter** | unchanged: no low-pass, median of three | `filter.tau: 0`, `median_window: 3` |

Two of the old requirements are **retired** by these. "A rate is a ceiling, not
a promise" was true of the loop as shipped and is not a requirement; the loop
is now shaped so that small moves are governed by the closed-loop speed, and
the rate ceiling only bounds large ones. "For a static hold the software PID has
nothing to offer" was a conclusion from one night on a deliberately weak hold
and is contradicted by answer 2.

What is kept from before, because Jeff said it and it still stands: 4 to 300 K
eventually; warn at a kelvin and fault at five (in watts where the residual has
an opinion); graceful failure; report-only monitor; recovery is the operator's
call; the eight safety rules.

## 3. What the bench measured, 2026-09-17

**This section is the single home for these numbers.** Config comments, code
docstrings and tests point here rather than repeating them
([style.md](../style.md)). All runs are on the fitted plant with the heater
delivering 0.336 % less power than the model claims, which is the cryostat as
wired since the 2026-09-10 connector reseat.

**3a is the morning's retune, graded against the superseded five-minute
requirement; 3b is the fast move, graded against §1b's.** 3a is kept because it
is the measured record of what each knob does, and because 3b only makes sense
against it.

### 3a. Against the superseded requirement

**The move: 2 K at 118 K, band 1.0 %, rate ceiling 5 K/min**, against
`move_speed` (the closed-loop time constant as a ratio of the plant's):

| `move_speed` | arrives (95 %) | overshoot | overdrive above final output |
|---|---|---|---|
| 0.50 (was shipped) | over 30 min | — | 0.14 % |
| 0.25 | 17.0 min | −22 mK | 0.36 % |
| 0.20 | 10.2 min | −5 mK | 0.45 % |
| **0.15** | **4.6 min** | **7 mK** | **0.56 %** |
| 0.12 | 3.3 min | 96 mK | 0.64 % |

The cryostat itself, on 0.50 that morning: 86 % of a 2.1 K move in 19 min,
against about 26 min for an open-loop hand step to reach 95 %.

**The same move at 0.15, other temperatures and sizes:**

| case | arrives (95 %) | overshoot | note |
|---|---|---|---|
| 2 K at 30 K | 1.9 min | 3 mK | delay floor binds |
| 2 K at 60 K | 3.5 min | **387 mK** | rate-limiter windup; open item below |
| 2 K at 100 K | 3.8 min | 17 mK | |
| 2 K at 140 K | 5.0 min | 34 mK | **faulted** before the band followed the setpoint |
| 2 K at 180 K | 4.9 min | 19 mK | output went to **zero** before the band followed |
| −2 K at 118 K | 4.9 min | −3 mK | symmetric |
| 10 K at 118 K | 15.9 min | 20 mK | overdrive 0.80 %, never railed |
| 10 K at 140 K | 15.0 min | −26 mK | |

**The band's width**, 2 K at 118 K, `move_speed` 0.15: 0.25 % arrives in
10.8 min (band-limited), 0.50 % and 1.00 % both in 4.6 min. A half-width
below the model's level error cuts the heater at arming, which is pinned by
`test_a_band_narrower_than_the_level_error_cuts_the_heater_at_arming`.

**The rate ceiling is not the lever.** Raising `max_rate_k_per_min` from 5 to
20 tripped the demand-anomaly freeze at 60 K and slowed 118 K to 6.9 min;
10 K/min slowed 118 K to 5.4 min; lowering it to 2 or 1 made 60 K overshoot
30–40 %. It stays at 5.

**The hold: 3 h at 118 K, Allan deviation as a ratio to the same plant open
loop** (the bench plant has white sensor noise and no slow disturbance, so this
grades only the "not degrading" half of answer 2):

| `hold_speed` | 10 s | 15 s | 60 s | 300 s | 900 s |
|---|---|---|---|---|---|
| 12 (was shipped) | 0.99 | 0.98 | 0.97 | 1.10 | 2.15 |
| 3 (code default) | 0.99 | 0.99 | 0.98 | 1.08 | 1.75 |
| 1 | 0.99 | 0.99 | 0.98 | 0.98 | 1.02 |
| 0.5 | 0.99 | 0.99 | 0.97 | 0.91 | 0.86 |
| **0.25** | **1.00** | **1.00** | **0.97** | **0.81** | **0.71** |

The weak loops stir at long averaging times; that is the 2026-09-16 night. A
strong hold does not slow a move: 4.7 min at 118 K with either 0.25 or 12.

**Before and after, in one table:**

| | before (armed 09-17 morning) | after |
|---|---|---|
| 2 K at 118 K, time to 95 % | not within 30 min | 4.6 min |
| same move at 100 / 140 / 180 K | 140 and 180 K faulted | 3.8 / 5.0 / 4.9 min |
| hold, Allan ratio at 15 s / 60 s / 300 s / 900 s | 0.98 / 0.97 / 1.10 / 2.15 | 1.00 / 0.97 / 0.81 / 0.71 |

The three numbers that changed: `authority_pct` 0.25 → 1.0, `move_speed`
0.5 → 0.15, `hold_speed` 12 → 0.25. One code change: the band centre follows
the setpoint whenever the model has a curve, not only when the feedforward term
is on.

**With the positional feedforward term ON** (a future stage, after a power
gauge), a 3 K move at 118 K at `move_speed` 0.15 overshoots 10.9 % against
3.1 % with it off, and 1.7 % at 0.5; at 60 K it is 16–18 % either way. The
term stacks a level step on a loop that already supplies the drive. The bench
envelope therefore keeps 0.5 (`BENCH_MOVE_SPEED`), and that stage must
re-grade `move_speed` when it comes.

### 3b. The fast move, against §1b's requirement

The test that grades the shipped file is
`tests_ltspm3/test_stage_4e_fast_move.py`. Same plant and the same
delivered-power gauge as 3a.

**What was in the way was never the cryostat.** 2 K in 30 s at 120 K needs
0.059 W of excess heat, which is 67.0 % output against a 70 % ceiling. Three
software limits were, and the first is most of it:

| | |
|---|---|
| **the heater's slew WAS the trajectory's rate** | `max_rate_k_per_min / K(T)` = **0.40 %/min** at 120 K, against the **3.5 %** of overdrive a 5 K/min ramp needs — 8.8 minutes of creep under every move. Raising the ceiling could not fix it, because that steepens the trajectory by the same factor it loosens the actuator, which is why 20 K/min measured *worse* in 3a |
| **`max_kp_pct_per_k: 1.0` bound silently** | at `tau_cl` on the dead-time floor the schedule asks 1.29 / 2.37 / 2.79 / 3.37 %/K at 60 / 100 / 120 / 180 K — every one of them clamped |
| **`move_speed: 0.15`** | `tau_cl` = 79 s at 120 K, which is the closed-loop response time and the trajectory corner both |

**The move, on the shipped numbers** (`max_output_rate_pct_per_min: 20`,
`move_speed: 0.03`, everything else as §1b left it):

| case | 95 % | within 50 mK | overshoot | peak output |
|---|---|---|---|---|
| **+2 K at 120 K** | **86 s** | **114 s** | **11 mK** | 66.6 % |
| −2 K at 120 K | 86 s | 126 s | −10 mK | — |
| +2 K at 30 K | 112 s | 130 s | 3 mK | 53.4 % |
| +2 K at 60 K | 106 s | 144 s | 3 mK | 60.4 % |
| +2 K at 100 K | 86 s | 112 s | 9 mK | 64.4 % |
| +2 K at 140 K | 80 s | 104 s | 14 mK | 68.4 % |
| +2 K at 180 K | 226 s | 262 s | 35 mK | **70.0 %, the ceiling** |
| +10 K at 120 K | 156 s | 278 s | 19 mK | 68.2 % |
| +0.5 K at 120 K | — | 54 s | 13 mK | 65.1 % |

Every overshoot is inside Jeff's 250 mK by more than an order of magnitude.

**86 s is a floor, and it is the floor Jeff's own three answers imply.**
Arrival is about `span / rate + corner + 2 tau_cl`, which at 5 K/min,
`delay_floor` 4 and a corner of 8 dead times is 24 + 24 + ~38 s. Only the first
term scales with the move, so a move costs about 40 s of fixed overhead plus
about 12 s per kelvin — it does not get worse for bigger moves.
**Cutting the corner does not help**: `smooth_delays` 6, 4, 2 and 0 all
overshoot 800–970 mK, because the trajectory then has corners sharper than the
loop's own response. It stays at 8.

**180 K is limited by `hard_max_pct` itself**, not by tuning: the steady output
is high enough there that the overdrive a fast move wants runs into the
ceiling. It still arrives, settles and does not fault.

**Three defects found by going fast**, each of them live on the cryostat and
none of them about speed:

- **the spike test could not follow the file's own rate ceiling.** `predict()`
  added back the low pass's lag but not the median's, so the prediction was
  biased by `rate × 2 s`; past about 3 K/min that exceeded the 8-sigma
  threshold, and a rejected sample does not refresh the reference it was
  rejected against, so the rejection ran away. On a noise-free 5 K/min ramp,
  **38 of 40 honest samples were thrown away** and the loop froze until
  `stale_after_s` forced a reseed.
- **one rejection rejected everything after it.** The escape was a 30 s clock;
  it is now also a sample count (`max_consecutive_spikes`), and reaching it
  *reseeds* rather than merely accepting one sample — accepting one is not
  enough, because the sensor guard needs `recover_samples` good ones in a row
  and a filter limping at one sample in three never gives it them. That cost
  24 s of frozen heater and 274 mK at 100 K.
- **the write decision compared against memory, not against the heater.** On
  every cycle where the rate limiter handed back the target unchanged, a loop
  whose output somebody else had moved decided it was already there and wrote
  nothing, indefinitely. The old creeping limiter hid it by never returning the
  target unchanged. It is the exact blindness
  `test_the_next_move_is_computed_from_where_the_heater_is` exists for.

**What the premise check cost, and what it gained.** A fast move swings the
residual 47 mW against a 10 mW fault threshold, and none of it is delivered
power: `dQ` carries `C(T) dT/dt`, `dT/dt` arrives from a regression 15 s late,
and mid-move the estimator read **0.021 K/s against a true 0.055 K/s** — times
0.88 J/K that is 30 mW of the 32 mW excursion. So the band gained a
`slope_lag` term, which is zero at a hold, zero during a constant sweep however
fast, and nonzero only while the rate is *changing*. Two consequences worth
knowing:

- 3 sigma at a settled 118 K hold went **1.44 → 1.52 mW**. That is
  rectification bias on an acceleration estimate whose true value is zero
  rather than a real widening, and at 1.5 mW against a 10 mW floor it changes
  no verdict.
- the step test now asks for the band **at the two samples that make the
  step**, added in quadrature, instead of the widest band anywhere in the
  window. The old form let one transient raise the threshold for a full half
  hour: with `slope_lag` in the band, a 3 % power loss at 100 K — an
  unmistakable 20 mW fault — went undetected for 1976 s, which is exactly when
  the window rolled past the transient the loss itself had caused. It faults at
  374 s now.

**Open, and written down so it is not lost:**

- **The feedforward term, when it comes on, needs `move_speed` re-graded**
  (3a's table). It has not been graded against 3b's numbers either.
- **10 K takes 16 min for 2 K.** Below `min_output_pct` the loop is in a regime
  nobody has looked at since the retune. Not on Jeff's path today.
- **Nothing here has run on the cryostat yet.** The loop that was armed when
  this was written ran the pre-retune numbers. The first move on the new ones
  is Jeff's to command, with the viewer open.
- **The long-term half of the hold benchmark** waits for a night armed on the
  new numbers, graded by `hold_quality.py` against 2026-09-15.

**Closed by 3b:** 60 K's 387 mK of rate-limiter windup. The cause was the
converted output rate limit rather than the missing anti-windup — giving the
heater its own rate removed it, and 60 K now overshoots 2.8 mK. No
back-calculation against the rate limiter was needed, and none was written.
