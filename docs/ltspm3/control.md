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

That includes the file interface. `lschart`'s `hold` and `arm` commands reach
this loop through `panic_hold()` and `arm()` — **called duck-typed, by name**,
so `lschart` still never imports `ltspm3` (invariant 1). Going through the
supervisor rather than around it is the point: a panic hold is
`abort_ramp()` + `set_mode(MANUAL)`, and the clamp and the rate limiter still
apply to everything that leaves. See
[running](running.md#stopping-the-loop-deliberately-from-a-file).

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
authority band : 58.076% .. 68.076%  (on_exit=hold)
```

Every limit lives in one of those config classes. **Never hardcode one in
`control/`.**
