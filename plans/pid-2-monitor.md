# PID Phase 2 — the monitor

Part of [PID_PLAN.md](../PID_PLAN.md). **Goal:** a judge outside the loop
that reads the recorder's files, applies the model's band, catches both
archive events as warnings, and warns about nothing else.

`ltspm3/monitor.py`. A separate process like the viewer: no port, no
commands, ever. Runs whether or not the loop is armed — which is most of the
cryostat's life so far — and is the reference implementation the supervisor's
in-loop check (plan 3 §3.4) is tested against.

## 2.1 Inputs and outputs

| | |
|---|---|
| reads | the recorder's CSV tail (`t` from the monotonic `Time` column, never the timestamp — AUDIT-2026-09-10 finding 4); `status.json` for `control` |
| writes | `plant.json` beside `status.json`: arrays not objects, `SCHEMA_VERSION`, `os.replace`; `plant_YYYY-MM-DD.csv`, one row per cycle |
| config | its own `monitor:` section: `warn_sigma: 3`, `warn_after_s`, `settle_taus: 3`, `window_s` |

## 2.2 Per cycle

| residual | computed | band | verdict |
|---|---|---|---|
| `δQ` | `model.missing_power_w` with `dT/dt` from a regressed slope | `model.sigma_q_w` | typical / warn / no opinion |
| `δT_c` | coldplate − locus, 175 s pole; 1st/2nd stage beside it | 27.6 mK rms | typical / warn |
| τ ratio | pole fit after a heater move holds, against `tau_s(T)` | 0.85–1.10 | typical / warn / no opinion below 25 K |
| noise | trailing rms vs `1.36e-6·T²`, floor 1.8 mK | < 2× | typical / warn |
| fault-level | `δQ` beyond `fault_mw` — **reported**, never acted on | seed 8 mW | flag only |

Persistence: a verdict changes only after `warn_after_s` out of band. No
opinion within `settle_taus × τ(T)` of a heater move, inside a mask, outside
the table, below 28 % output, or while `δT_c` is atypical (one cause, one
alarm).

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

## Exit gate

- The replay table green in `pytest`; `fault_mw` and `warn_after_s` written
  into config from what the replay required.
- 72 h beside the live recorder, `plant.json` read by `lschart status` and
  MATLAB `plant()`, the post-repair residual inside the band throughout.
