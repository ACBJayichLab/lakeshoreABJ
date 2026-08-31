# The control loop

Read [safety.md](safety.md) first: it is the rules these modules implement, and
[thermal-response.md](thermal-response.md) is where every default number comes from.

## Shape

```
Reading  ->  SensorGuard  ->  filters  ->  PID  ->  HeaterSupervisor  ->  dither  ->  ANALOG
                  ^                         ^              |
              coherence               feedforward      authority band
```

**`HeaterSupervisor` owns the output.** The PID proposes; the supervisor
disposes. Nothing else may write to the analog output.

That includes the file interface. `lschart`'s `hold`, `heaters_off`, `arm` and
`ack` commands reach this loop through `panic_hold()`, `panic_off()`, `arm()`
and `acknowledge()` — **called duck-typed, by name**, so `lschart` still never
imports `ltspm3` (invariant 1). See
[running](running.md#stopping-the-loop-deliberately-from-a-file).

**Both panic actions disengage the loop** — `abort_ramp()` then `set_mode(OFF)`,
which writes nothing at all, ever. A person reaching for either has decided the
loop should stop deciding. `MANUAL` is not good enough and was the bug: it still
clamps to the authority band and still rate limits, so a hold taken while the
heater sat outside the band moved it on the next cycle.

**The band caps heat; it does not compel it.** The ceiling is hard and immediate
— less heat is never the dangerous direction. The floor bounds what the PID may
*ask* for (`_apply_band_to_pid` sets `out_min`), not what the DAC must carry.
Enforcing it on the output too meant `clamp` ran after the rate limiter and
undid it: arming with the heater at 0 % wrote 62.076 % in one step, past
`max_step_pct`, which exists for exactly that. It also meant the loop could not
hold any temperature whose steady-state output lay below the band — at base
temperature it would command operating-point power and then fault.

**"Owns the output" is a claim about this program, not about the world.** A
front panel, another process, or `lschart`'s own `analog` command can all move
that DAC, and the supervisor cannot stop any of them. What it must not do is
compute its next move from a value that stopped being true: every limit it
enforces — the rate limit, the ramp-down step, the output a manual hold adopts
— is a limit on a step from *here*, so it reads where the heater is rather than
remembering where it left it. See `_where_the_heater_is`, and
`tests_ltspm3/test_panic_seam.py` for what it cost when it did not.

## The modules

| Module | |
|---|---|
| `supervisor.py` | **the safety envelope. Read this first.** Owns the output, the authority band, the fault states, the lockout. `panic_hold()` is the one method `lschart` reaches in by, duck-typed |
| `health.py` | `SensorGuard`: the validity gate and the OK / SUSPECT / FAULT / RECOVERING state machine |
| `coherence.py` | cross-channel corroboration. Read together with `health.py` |
| `pid.py` | derivative on a **regressed slope**, integral clamped in **output units**, bumpless `prime()`, feedforward-aware |
| `tuning.py` | IMC gain scheduling from the measured K and τ |
| `feedforward.py` | the steady-state output for a temperature, from the same curve the simulator uses |
| `ramp.py` | `SetpointRamp` + `SetpointSmoother` |
| `filters.py` | median / exponential / slope, staleness-aware |
| `dither.py` | `SigmaDeltaDither`, for sub-code resolution |

## Why each piece is shaped the way it is

**The guard is two-tier, not one threshold.** A single slew limit cannot both
pass a genuine 1.63 K/s cooldown and reject the sensor glitch — see
[safety.md](safety.md#the-sensor-glitch--the-real-failure-mode). The
discriminator is smoothness and cross-channel corroboration.

**The derivative is on a regressed slope**, not a difference of two samples,
because the noise is correlated (lag-1 autocorrelation +0.51) and a two-sample
difference amplifies exactly that.

**The integral is clamped in output units**, not in error-seconds, so the clamp
means something physical: it is a bound on how much heater the integrator can
ask for.

**Feedforward and the simulator import the same curve.** `ltspm3/thermal_response.py` is the
single copy of `P(pct)` and `T(P)`, so the model the controller assumes and the
model the simulator implements cannot drift apart. Testing against a simulator that
silently agreed with a wrong feedforward would prove nothing.

**Filters are dt-aware** (`alpha = 1 - exp(-dt/tau)`), never fixed-alpha — the
bus jitters and a retry can cost a cycle.

**The output is sigma-delta dithered**, because one 0.01% DAC code is ~100 mK at
the operating point. See [thermal-response.md](thermal-response.md#the-consequence-that-shapes-the-whole-design).

**Setpoints ramp, never step** — rule 8. A step larger than `max_error_k` is
indistinguishable from a broken premise and would stall the loop rather than
move it.

## Configuration

`ltspm3/config.py` registers the `control:` section on import, which is why
`lschart` alone rejects it as unknown — a recorder that does not have the
controller must not silently accept a config that asks for one.

```yaml
control:
  enabled: false        # the loop exists but is not built unless this is true
  supervisor: {...}     # SupervisorConfig -- the envelope
  pid:        {...}     # PIDConfig
  guard:      {...}     # SensorGuardConfig
  coherence:  {...}     # CoherenceConfig
  ramp:       {...}     # RampConfig
  tuning:     {...}     # TuningConfig
  feedforward:{...}     # FeedforwardConfig
  filter:     {...}
```

`control.enabled` requires `ls218.enabled`: the sample heater *is* the 218's
analog output, and config validation says so rather than letting an
`AttributeError` surface from inside the poll thread.

`check` prints the resulting authority band and the `on_exit` policy — the two
numbers worth reading before arming anything:

```
authority band : 62.076% .. 64.076%  (on_exit=hold)
```

**Read that off `check`, never off this page.** It said `58.076% .. 68.076%`
until 2026-08-31 — five times too wide, because `authority_pct` is 1.0 and not
5.0. A stale band in a document is not a cosmetic error: the band is what
decides whether the output you are sitting on is one the loop may keep.

Every limit lives in one of those config classes. **Never hardcode one in
`control/`.**
