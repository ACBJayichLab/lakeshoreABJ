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
4. **Premise checks.** This loop is specified for mK trim. An error above
   `max_error_k`, or a PID demand that jumps by more than `anomaly_demand_pct`,
   means something is wrong *with the rig*, not with the control — so hold, and
   ramp down only if it persists.
5. **The authority band caps heat unconditionally.** Output can never exceed
   `operating_point + authority_pct`. It may go *below* the band, but only as a
   fault ramp-down — the one direction where leaving the band is the safe one.
6. **On exit, hold.** Zeroing a sample heater on a live cryostat is its own
   hazard. `on_exit: hold` is the default; `zero` is opt-in.
7. **Recovery is always the operator's call.** A completed fault ramp-down locks
   out; `acknowledge()` disarms the loop, and re-arming is a deliberate act that
   re-primes the PID and the filter from what the rig is doing *now*.
8. **Move the setpoint by ramping it, never by stepping it.** A step of more
   than `max_error_k` is indistinguishable from a broken premise, so it stalls
   the loop rather than moving it. Sweeps and post-fault approaches both go
   through `control/ramp.py`; the premise check is widened only by the lag the
   ramp itself commands (`rate × plant_lag_s`), decaying once it stops.

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

`ltspm/tools/replay.py` runs the real pipeline over the historical logs —
currently **12.8 rejections/day and 0 samples ever reaching FAULT** across 63
days. It is the only test on genuine data, and it found the stale-slew-reference
bug that no simulated fault would have.

```bash
python -m ltspm.tools.replay "reference/logs/CD*/*.xls"
```
