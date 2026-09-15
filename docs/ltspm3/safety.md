# Safety: the design rules, and the failure they were written for

**Availability of the cryostat outranks control quality.** Jeff's stated
priority is "I don't want to add in big risk of massive failure". Every
ambiguous case resolves to *hold the output and raise an alarm*, never to
correct aggressively.

## The eight rules

1. **Nothing raises the heater in response to a fault. Ever.** The only fault
   responses are freeze and slow ramp-down.
2. **The PID proposes; the supervisor disposes.** `HeaterSupervisor` owns the
   output. Nothing else may write to the analog output.
3. **A single doubtful reading freezes the output.** Escalation to a ramp-down
   takes 60 s of sustained failure by default.
4. **Premise checks: the watts add up.** `δQ = C(T)·dT/dt + [Λ(T) − Λ(T_c)] −
   P(u)` — the same residual the monitor judges by, from the same two model
   functions, so the loop and the judge cannot disagree about what typical
   means, only about what to do. It is valid at a hold **and during a sweep**,
   which a kelvin check is not.

   - beyond `warn_sigma` × `σ_Q`: **alarm and keep tracking**. An alarm is not
     a freeze. The old check froze on any anomaly and escalated on a timer,
     which on a cryostat whose legitimate sweep lag is 43 K made freezing the
     normal outcome of doing what it was told;
   - a **step** of `fault_mw` inside `fault_window_s`: fault. Inherited from
     the monitor, not re-decided — a change in delivered power lands in the
     residual immediately, and anything that takes hours did not step;
   - **authority exhausted** — error past `fault_error_k` with the demand *and
     the output* railed at the **ceiling**, **while the setpoint is not
     moving**. Railed with a large error is the normal state of a loop
     following a ramp, and a loop still travelling up to its window at the rate
     limit has authority it has not applied yet; what makes it a fault is that
     the loop is giving everything it is allowed to give, the sample still will
     not come up, and the setpoint has stopped moving;
   - **railed at the FLOOR with the same error is a WARNING, however far it
     goes** (Jeff, 2026-09-15). Less heat than the model expects for this
     setpoint means the bath has changed or the model is wrong high, and the
     worst case is a sample colder than intended — the safe direction. A
     ramp-down would not improve it and a lockout would stop the loop resuming
     when the bath recovers. A rising coldplate is exactly this;
   - the tracking error in kelvin warns **while the setpoint is not moving**,
     and under about 40 K — below `min_output_pct`, or where the plant is
     faster than the slope is measured — it is the only check there is;
   - a PID demand that jumps by `anomaly_demand_pct` in one cycle still freezes
     the loop. That one is about the *reading*, not about the cryostat.

   **No opinion is not typical.** Outside the table, below `min_output_pct`, or
   where the plant is faster than the slope window *while moving*, the residual
   is silent — and silent must never read as green.

   **The gate on the two kelvin rows is the TRAJECTORY, not the tuner's
   phase.** "In `hold` only" meant "while the setpoint is not moving", and the
   tuner's phase was a fair proxy until the error itself started driving it:
   `update_phase` enters `move` on any error over `move_error_k` = 0.25 K, so
   an error of 1 K — let alone 5 — was by construction in the phase that
   switched both rows off. Measured 2026-09-15: a heater delivering half its
   power at 30 K left the sample 14.3 K low, railed at the ceiling, for an
   hour, in `tracking`, with no alarm of any kind.

   `max_error_k`, `anomaly_hold_s`, `max_ramp_error_k`, `response_lag_s` and
   `model_trust_k` are gone. One kelvin threshold cannot serve a cryostat whose
   gain spans forty-fold, and the ramp allowance propping it up was excusing
   the loop's own 60 s filter as much as anything about the cryostat.
5. **The authority band caps heat unconditionally, and it follows the
   setpoint.** The window is `authority_pct` either side of *the output the
   model says holds the setpoint the loop is chasing*, widened while a ramp is
   running by exactly the lead that ramp needs (`rate·τ/K`), and intersected
   with `hard_min_pct` / `hard_max_pct`. **`hard_max_pct` is the part nothing
   moves**: whatever the model, the setpoint or the arithmetic says, the output
   cannot go above it.

   It was two config constants until 2026-09-14, and that could not span 4 to
   300 K — 10 K is 24 % of output and 180 K is 69 %, against a window one point
   wide. No sweep below 60 K was arithmetically possible, and a completed sweep
   above 100 K faulted *afterwards* because the window was still centred where
   it started.

   Two things the rewrite had to learn, both measured rather than reasoned:

   - **The floor may never be above where the heater already is.** It bounds
     what the PID may *ask* for; above the present output it compels heat
     instead, which is invariant 4 broken by the safety layer itself. In a
     regime the model does not describe, a loop holding steadily at 63.09 %
     was walked to 64.68 % by its own envelope.
   - **Nor may it be pinned *to* the output**, which is a ratchet: `out_min`
     equal to the present output means the PID can never ask for less than it
     is already producing, and every upward wiggle is locked in. When the
     window is somewhere the loop is not, the floor is simply `hard_min_pct`.

   The band opening is not itself heat. What governs how fast the heater
   travels into a newly opened window is the output rate limiter, and what
   refuses an absurd setpoint step is rule 8.

   It may go *below* the band, but only as a fault ramp-down — the one
   direction where leaving the band is the safe one.
6. **On exit, hold.** Zeroing a sample heater on a live cryostat is its own
   hazard. `on_exit: hold` is the default; `zero` is opt-in.
7. **Recovery is always the operator's call.** A completed fault ramp-down locks
   out; `acknowledge()` disarms the loop, and re-arming is a deliberate act that
   re-primes the PID and the filter from what the cryostat is doing *now*.
8. **Move the setpoint by ramping it, never by stepping it.** Sweeps, the
   post-fault approach and the fault ramp-down all go through
   `control/ramp.py` at the one rate.

   This used to be enforced by rule 4: a step past `max_error_k` produced an
   error the premise check read as a broken premise, so the loop froze and
   eventually ramped down. **Rule 8 was protecting the cryostat by breaking the
   loop**, and it only worked because the check could not tell a commanded move
   from a fault. Rule 4 can now, so the refusal moved to the *request*:
   `set_setpoint(x, ramp=False)` with a step larger than `warn_error_k` becomes
   a ramp at the one rate, and says so in the log. `ramp=False` still means a
   step for a trim smaller than that.

   The premise check no longer needs widening during a ramp at all, because the
   watt residual carries `C·dT/dt` and a commanded move is not an excursion.

**Never hardcode a limit in `control/`.** It belongs in `SupervisorConfig`,
`SensorGuardConfig` or `PIDConfig`, so every limit is visible and auditable in
one place.

## The sensor glitch — the real failure mode

**It is not a dropout to 0 K.** Searching for zeros finds nothing across all 24
logs, because the fault has a completely different shape.

Searching for *single-channel physically-impossible rates* finds **9 events in
1,510 h**, about one per 7 days:

| Property | Value |
|---|---|
| Channel | **Input 1 only** — never input 2/3, never any 336 channel |
| Shape | scatters in *both* directions, e.g. 297 → 151 → 292 → 92 → 175 K |
| Range | 11 K to 298 K observed. **Never 0 K**, never below 11 K |
| Duration | 2 s to 280 s, then resumes exactly on the pre-glitch trend |
| When | mostly during cooling/warmup; one during a steady hold at 18.5 K |

### What that forces

1. **`valid_min_k` and any zero-check are useless against this.** The glitch
   never produces an obviously invalid number.
2. **A single slew threshold cannot work.** Loose enough to pass the real
   1.63 K/s cooldown also passes half the glitch; tight enough to catch the
   glitch rejects genuine cooldowns. Hence the **two-tier limit plus
   corroboration**.
3. **The discriminator is smoothness and corroboration, not magnitude.** A real
   thermal signal is a smooth function of time and moves every channel; the
   glitch reverses direction each sample, on one channel alone. See
   `control/coherence.py` and `SensorGuardConfig.curvature_ratio`.
4. **`fault_after_s` is 600 s, not 60 s.** The longest observed event healed
   itself in 280 s; escalating at 60 s converts a five-minute sensor burp into a
   ramp-down and a lost cooldown.

### It is measured against the real logs

`ltspm3/tools/replay.py` runs the real pipeline over the historical logs —
currently **12.8 rejections/day and 0 samples ever reaching FAULT** across 63
days. It is the only test on genuine data, and it found the stale-slew-reference
bug that no simulated fault would have.

```bash
python -m ltspm3.tools.replay "reference/logs/CD*/*.xls"
```
