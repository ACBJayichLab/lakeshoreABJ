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

## 2. What was taken from them

| | requirement | how it is graded |
|---|---|---|
| **move** | a 2 K move at 118 K arrives — 95 % of the way and staying — in **5 minutes**. No overshoot worth the name, no rail, no fault | `tests_ltspm3/test_stage_4d_fast_move.py` on the bench; on the cryostat, the recorder's CSV after a `send setpoint --software` |
| **hold, noise** | Allan deviation at **15 s, 1 min and 5 min no worse than open loop** | the same test, armed against open loop on the same plant |
| **hold, wander** | **much better than open loop** at long averaging times | `analysis/hold_quality.py` against the 2026-09-15 open-loop night, on the cryostat. The bench cannot grade this: its plant has no slow disturbance |
| **hold, direction** | the loop has **real authority** at a hold — faster than the plant, not slower | `hold_speed` below 1 |
| **rate** | 5 K/min is a **safety ceiling**, not a target. Raise it only if it is what limits a move | `ramp.max_rate_k_per_min`. Measured 2026-09-17: raising it made moves worse, so it stays |
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

All on the fitted plant with the heater delivering 0.336 % less than the model
claims, which is the cryostat as wired. Full tables are in the comments of
`config-ltspm3-armed.yaml`; the shape of the result is:

| | before (armed 09-17 morning) | after |
|---|---|---|
| 2 K at 118 K, time to 95 % | not within 30 min (cryostat: 86 % at 19 min) | **4.6 min** |
| overdrive above the final output | 0.14 % | 0.56 % |
| same move at 100 / 140 / 180 K | 140 and 180 K **faulted** — band pinned | 3.8 / 5.0 / 4.9 min |
| 10 K at 118 K | — | 16 min, never railed |
| hold, Allan ratio to open loop at 15 s / 60 s / 300 s / 900 s | 0.98 / 0.97 / 1.10 / **2.15** | 1.00 / 0.97 / 0.81 / **0.71** |

The three numbers that changed: `authority_pct` 0.25 → 1.0, `move_speed`
0.5 → 0.15, `hold_speed` 12 → 0.25. One code change: the band centre follows
the setpoint whenever the model has a curve, not only when the feedforward term
is on.

**Open, and written down so it is not lost:**

- **60 K overshoots 0.39 K on a 2 K move.** The output rate limit is slow there
  relative to the loop and the integral winds up behind it. Anti-windup against
  the rate limiter is the fix. Pinned by a test so it is not forgotten.
- **The feedforward term, when it comes on, needs `move_speed` re-graded.**
  With the term on (a future stage, after a power gauge) a 3 K move at 118 K
  overshoots 10.9 % at 0.15, against 3.1 % with it off, because the level step
  stacks on a loop that already supplies the drive. The bench envelope keeps
  0.5 for that reason; the file ships 0.15 with the term off.
- **10 K takes 16 min for 2 K.** Below `min_output_pct` the loop is in a regime
  nobody has looked at since the retune. Not on Jeff's path today.
- **Nothing here has run on the cryostat yet.** The loop that was armed when
  this was written ran the previous numbers. The first move on the new ones is
  Jeff's to command, with the viewer open.
- **The long-term half of the hold benchmark** waits for a night armed on the
  new numbers, graded by `hold_quality.py` against 2026-09-15.
