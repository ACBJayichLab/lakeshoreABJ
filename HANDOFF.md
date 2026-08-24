# Handoff — 2026-08-22 (second pass)

Point-in-time status. Durable context lives in `CLAUDE.md`; this goes stale.

## What changed this session, and why it matters

The reference log set grew from 2 files to **24** (1,510 h / 63 days). That
invalidated several of the previous session's conclusions, and the corrections
drove most of the work below.

### The dropout detector was aimed at the wrong fault

The previous handoff said the logs contained no dropouts and that the detector
"cannot be calibrated from data". Both were artefacts of searching for the
wrong signature — zeros, negatives, sub-1 K readings. The real fault never
reads 0 K. Searching for *single-channel physically-impossible rates* finds
**9 events**, one roughly every 7 days, always on Input 1, scattering between
11 K and 298 K for 2–280 s before healing itself. Full description in
`CLAUDE.md`. The guard is now calibrated against those events rather than from
first principles.

### Three linked recovery defects, all fixed

None were caught by the old suite, because it only tested a 20 s hold where
neither the output nor the plant had moved.

1. **Spike-test deadlock.** The low-pass only advances on accepted samples, so
   during an outage it froze while the plant moved on — and a fault ramp-down
   guarantees the plant moves. On recovery every honest reading sat far from
   the stale prediction, was rejected as an outlier, and so never refreshed it.
   The guard could not leave FAULT no matter how healthy the sensor became.
   Fixed with `MeasurementFilter.is_stale()` / `reseed()`.
2. **PID never re-primed.** `prime()`'s own docstring said it was called on
   recovery; its only caller was `set_mode`. After a ramp-down the first demand
   was a phantom step the size of the whole ramp, which the anomaly check read
   as a broken premise — then ramped down again. A positive-feedback ratchet to
   zero from one transient.
3. **`acknowledge()` could not re-arm.** It left `mode` at PID, so the
   operator's `set_mode(PID)` hit the "already in this mode" short-circuit and
   never re-primed. It now disarms, making re-arming a real transition.

### The stale-slew-reference hole — found by replay, not by the simulator

Every rejection ages the slew reference. At the 20 s cadence of
`cd8_..._sample_cooldown.xls` a *single* rejection pushed it past
`slew_reference_max_age_s`, disabling the slew test outright — so the glitch's
own alternation walked straight through the guard, and garbage values became
the trusted reference:

```
i=1905  150.990  slew_reject   lastgood=296.97
i=1906  291.530  GOOD (!)      lastgood=291.53
i=1907   91.846  slew_reject
i=1908   93.643  GOOD (!)      lastgood=93.643
```

Fixed by a reference-free **reversal test** (`curvature_ratio`): a real thermal
signal is a smooth function of time, so its second difference is small even
when the first difference is huge; the glitch reverses violently every sample.
Measured over the logs it fires 7× inside the known glitch, 0× on a genuine
6.5 K-per-sample cooldown, 0× on a week of quiet holding.

**No simulated fault would have exposed this.** Keep `tools/replay.py` in the
loop for any future guard change.

### Sweeps needed feedforward

The stated requirement is to hold for hours *and* sweep programmatically. A
stepped setpoint trips `max_error_k` by construction, so setpoint moves now
ramp (`control/ramp.py`). That alone was not enough: at `kp=0.02 %/K` against a
7.6 K/% plant the loop gain is ~0.15, and the setpoint reached target while the
plant was still 3 K behind. `control/feedforward.py` inverts the measured
steady-state curve so the output moves *with* the setpoint; the PID trims the
residual. Model error is absorbed by the integral, so the exponent being
uncertain is tolerable.

## Current state

| Area | State |
|---|---|
| `model/transport/instruments` | Complete. `_status_ok` shared; a failed `AOUT?` no longer discards a whole frame; `TransportError` from `RDGST?` now propagates instead of masquerading as 8 sensor faults. |
| `control/` | Complete: filters, guard, coherence, PID, feedforward, ramp, dither, supervisor. |
| `config.py` + `config.yaml` | Complete. Unknown keys are an error. Validates the GPIB transaction budget against the poll interval. |
| `acquisition/` | Complete: poller, recorder (CSV, no row limit, flushed per sample), ring buffer. |
| `tools/` | `import_xls.py`, `replay.py`. |
| `__main__.py` / `app.py` | `run` / `check` / `init`. Going live is two `backend:` lines. |
| Tests | **64 passing**, incl. 6 replaying real logs. |
| GUI | **Not started.** |

Replay against the full reference set: **12.8 rejections/day, 0 samples ever
reaching FAULT** across 63 days.

### Sensor noise is quadratic in T, and that caps the sweep requirement

A full-corpus curvature scan (all 24 files) flagged 21 sub-Kelvin wiggles in
`cd9_..._sample_cool.xls` that the 4-file validation had not shown. Checking
them across channels showed they are **not** glitches -- Input 3 wobbles by a
comparable amount in the same window -- and the shipped guard correctly rejects
**nothing** there, because it uses a local adaptive noise floor rather than the
flat file-wide estimate the prototype scan used. It does still catch the two
real glitches in that file as contiguous 7-sample runs.

The useful by-product was a proper noise-vs-temperature curve (3-point local
detrending over cd9+cd10):

| T | 18 K | 96 K | 190 K | 240 K | 290 K |
|---|---|---|---|---|---|
| rms | 1.8 mK | 13.6 mK | 45 mK | 73 mK | 109 mK |

That is `~1.36e-6 * T**2`, not linear. The simulator's old linear model was
calibrated at 96 K and understated 290 K noise by ~4x; it now matches to within
about 10% across the range.

**Consequence worth telling Jeff:** millikelvin stability is a low-temperature
capability. Near 96 K the measurement floor is ~2.5-4 mK; near room temperature
it is ~100 mK, and no amount of control quality changes that. Sweeps that end
high will not hold to mK.

## Answers from Jeff this session

- **Recovery after a fault: always require operator acknowledgement.** No
  automatic resumption, even if the sensor looks healthy.
- **Usage:** sits at a temperature for a few hours; wants better stability and
  *programmatic sweeps*.
- **The glitch:** "sudden jump to a lower value, sometimes flickers back and
  forth… no way it could cool that quickly and it is very discrete." Matches
  the 9 events exactly.
- **Cadence:** was told the 2–20 s variation was driven by file-size limits.
  Confirmed — it tracks the 65,004-row cap in every long file.

## Not built yet

1. **GUI** — pyqtgraph strip chart + PID panel. PySide6/pyqtgraph are declared
   in `pyproject.toml` but are *not* in `.venv` (they exist in the system
   Python). `uv pip install -e ".[dev]"` before starting.
2. **Sweep scheduler** — `sweep_to()` exists and is tested; a sequence of
   setpoints with dwell times does not.
3. **`lschart` is still not installed into `.venv`.** `tests/conftest.py`
   inserts the repo root on `sys.path`, which papers over it. Only `pyyaml` was
   added this session.

## Still to verify against real hardware

Unchanged from last time except where noted.

- **`RDGST?` bit weights** (1 invalid, 16 under, 32 over, 64 units zero,
  128 units over). Now polled every `status_every_n_cycles` rather than every
  cycle, so a wrong decoding is less costly — but still unconfirmed.
- **The 218's true analog-output resolution.** 0.01% is assumed and the dither
  step depends on it. Send a few values and watch `AOUT?`.
- **The 218's per-input update rate.** 1 Hz is the new default cadence and is
  believed to be comfortably inside what the box produces; confirm against the
  manual before anyone raises it.
- **The plant exponent.** Still fitted at n≈5.0 from two points; Jeff recalls
  "65-ish is around 150 K", which wants n≈5.6. `feedforward.fit_exponent()` is
  provided so this can be re-derived the moment a heated steady-state dataset
  exists. Feedforward tolerates the error; the *shape* is what matters.
- **GPIB timing.** At 1 Hz with `read_status: false` a cycle is ~9
  transactions ≈ 0.45 s at 50 ms pacing. `lschart check` prints the budget and
  `AppConfig.validate()` refuses a cadence the bus cannot sustain — but the
  50 ms figure itself is untested on this bus.

## Things worth knowing

- **`sim.speedup` accelerates the plant but not the controller**, which still
  integrates in real time. Fine for exercising the recorder; meaningless for
  closed-loop behaviour. Use the virtual-clock harness in `tests/conftest.py`.
- **Filenames in `reference/logs` lie.** `cd10_7_2026_st2_monitor3.xls` is a
  218 log. `import_xls` sniffs row 0; never trust the name.
- **`reference/logs` is ~110 MB and is deliberately *not* gitignored** — it is
  the only empirical record of the plant, and every default in `control/` is
  derived from it. Revisit if the repo needs to stay small.
- **The repo still has no commits.** `.gitignore` exists now and 49 files are
  staged; committing was left to Jeff.
