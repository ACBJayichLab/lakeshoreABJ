# Handoff — 2026-09-14 (PID phase 1 is DONE; the monitor is next)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
route to a working loop is [PID_PLAN.md](PID_PLAN.md) and the model it stands
on is [REFIT_PLAN.md](REFIT_PLAN.md). This goes stale.

Previous: [HANDOFF-2026-09-13.md](HANDOFF-2026-09-13.md), which closed the
thermal refit. This file is what was built on top of it.

> ## THE HEATER IS STILL ON AND NOTHING WAS COMMANDED
>
> The recorder is running `config-ltspm3-heater.yaml`. The 218's analog output
> has been at **64.0100 %** since 2026-09-10 14:49 and the sample flat at
> **118.3 K** since 09-11 00:00. Nothing in this session touched the cryostat —
> every number below comes from the archive.
>
> **It is still a reseat, not a repair.** Jeff ruled on 2026-09-14 that a
> four-probe measurement of the heater circuit is **out of scope**, and that the
> margins should be sized so that it is a non-issue. That ruling is now
> load-bearing in the design and §3 below is what it turned into.

## What this session did

**PID phase 1 is complete: the model carries its own error band, and the two
functions the monitor and the supervisor judge by exist.** 967 passing, `ruff`
clean, the table byte-identical — the curves did not move, only what travels
beside them.

| | |
|---|---|
| `analysis/band.py` | NEW. Measures the six terms of `σ_Q` from the archive the curves were fitted to, and owns phase 1's gate |
| `ltspm3/model/_fitted_table.py` | carries the band, `FIT_KEY` and the gauge as machine-readable constants, each under the comment that says what it is |
| `ltspm3/model/fitted_response.py` | `missing_power_w`, `sigma_q_w`, `sigma_q_terms`, `bias_q_w`, `conductance_w` |
| `analysis/pid_tuning.py --rows` | phase 3 §3.2's schedule, from the production fit, cache key on every row |
| `analysis/plan_sweep.py` | rungs above 192.6 K now say `PREDICTED ONLY` |
| `tests_ltspm3/test_residual.py` | NEW, 29 tests. Plus 13 in `test_fitted_table.py` |

## The four things worth a reviewer's attention

**1. The model's error is flat in KELVIN, and it took measuring it the other way
round to find that out.** The band's model term was written as a fraction of the
delivered power — the shape the campaign drift has, and the shape a heater-circuit
error has. Measured, the same in-epoch residual is **0.07 % at 118 K and 17 % at
5.4 K**, where 8 mW of heater holds the sample: a band built that way is blind at
the cold end and hysterical at the warm one. Flat in kelvin is what it actually
is — 0.107 K rms below 25 K, 0.183 K above 40 K — so it enters as
`SIGMA_MODEL_K × Λ′`, the same shape as the thermometry terms beside it.

The number is **0.135 K**, which is REFIT §1's own row 2 recomputed through the
shipped grid rather than through the fit. That is deliberate: the band's model
term and the gate the refit had to pass are now the same measurement, so the
band cannot quietly claim the model is better than the scoreboard says.

**2. `DELTA_P_FRAC` came OUT of the band, and that is what Jeff's ruling means
in practice.** The 0.7 % the heater circuit may not deliver is a **constant**.
It changes when somebody handles the wiring — three such events are measured,
all a few tenths of an ohm in series with a 75.5 Ω heater — and between them it
does not change at all.

| | 3σ at 118 K | |
|---|---|---|
| band with `δP` in it | **14.0 mW** | the 09-10 fault hides inside it |
| band as shipped | **1.4 mW** | the fault is 3.5× out of band |
| `bias_q_w`, on its own | 4.7 mW = 2.8 K | reported, not alarmed on |

A band carrying a systematic that cannot move inside thirty minutes cannot see a
fault that happens inside thirty minutes. So the bias is exported under its own
name, for the three consumers that feel it: the monitor's **absolute** residual
(whose answer is a trailing baseline, not a wider alarm), the velocity
feedforward, and the open-loop ramp-down — 0.7 % of full power is a few kelvin
of terminal error on an emergency descent to base, and an emergency descent to
base does not care. **The closed loop never sees it**, because integral action
absorbs a constant power offset exactly. That is the answer to "size the margins
so it is a non-issue": α is a non-issue for the loop by construction, and making
the alarm band swallow it would have been the one way to make it a real one.

**3. The band is about a kelvin everywhere, and nothing chose that.** Six
measured terms — the coldplate's own residual through `Λ′(T_c)`, the median
anchor bar, the diurnal amplitude, the in-epoch model error, the campaign drift,
`σ_C·|dT/dt|` — and what they add up to at the local gain is:

| T | 3σ day 0 | day 10 | at 5 K/min | in K, day 0 |
|---|---|---|---|---|
| 10 K | 9.66 mW | 9.74 | 9.66 | **0.41 K** |
| 30 K | 3.99 | 7.05 | 4.03 | 0.44 |
| 60 K | 1.54 | 7.56 | 3.23 | 0.68 |
| 118 K | 1.44 | 8.78 | 6.70 | **0.87** |
| 180 K | 1.61 | 10.13 | 8.80 | 0.86 |

Jeff asked for a warning at a kelvin; the measured band arrives at one from the
other end. It also widens during a sweep, which §3 required, and grows with the
days, which the "typical drifts" trap required.

**At the warm end the largest settled term is the SINK, not the sample** — 0.42
mW against 0.027 mW of thermometry, because `Λ′` at 6.6 K is nine times `Λ′` at
118 K. A reader who assumes the sample dominates will size the wrong term, so
there is a test that says so.

**4. Phase 1's gate needed its one sentence read twice, and both readings are
printed.** "`missing_power_w` < 1 mW at every settled anchor the fit was given."

- *"Every anchor the fit was given"* is 136 anchors over 57 days and the level
  is gauged to **one** of them. In-epoch the residual is 0.135 K; against the
  whole campaign it is **4.7 K rms over 40 K**. The second number is the history
  of the cryostat, not the model's error, and no fit that ships one level can
  make it smaller. `band.py` prints both, in that order.
- *"< 1 mW"* is a kelvin gate divided by `Λ′`, and `Λ′` runs 24 mW/K at 10 K
  against 1.7 at 118 K. In-epoch the gate is **met above 40 K (0.583 mW)** and
  missed below 25 K (3.4 mW) — while in kelvin the cold end is the **better**
  half. A gate in watts is fourteen times stricter exactly where the model is
  best.

The one figure the apparatus does not give is §1.2's "3σ at 118 K, day 0,
settled, between 4 and 8 mW". It is **1.4 mW** measured. That figure was written
before the terms were, and 4–8 mW is where the band lands about ten days after a
gauge. Nothing was tuned to reach it.

## Then, in order

1. **PID_PLAN phase 2 — `ltspm3/monitor.py` — is the next work**, and
   [plans/pid-2-monitor.md](plans/pid-2-monitor.md) is unchanged by any of the
   above except that its two functions now exist. It is mostly plumbing plus the
   replay table of §2.3, and the replay is what **sets** `fault_mw` and
   `warn_after_s` rather than choosing them. It also earns its keep immediately:
   it runs whether or not the loop is armed, which is most of this cryostat's
   life so far.
   - **The one thing the monitor has to solve that the model handed it**: the
     absolute residual carries `bias_q_w`, up to 4.7 mW at 118 K, which is three
     times the day-0 band. The monitor's answer is a **trailing baseline** —
     judge the change, not the level — and that belongs in §2.2 beside the
     persistence rule.
2. **At about day 9 the band overtakes the 8 mW `fault_mw` seed.** That is not a
   band problem, it is the drift with nothing subtracting it, and it is an
   argument for **re-gauging on a cadence** — one programmed ladder, under five
   hours, `export_response.py` — rather than for widening anything. Phase 2's
   replay is where the number gets settled.
3. **Phase 1.3's pipeline is proved and its ladder is not run.** `--hi 300`
   builds the rung list and marks the four rungs above 192.6 K as predicted-only;
   the top one asks 77.9 % = **991 mW** against a heater rated 1.68 W. The ladder
   itself is commissioning stage 6 and needs Jeff at the cryostat.
4. The 09-10 mask still goes in with the next archive export, not before.

## Traps this session added to the list

- **A band term must be told what it is proportional to** — the sibling of the
  campaign-drift trap already on this list. The residual's own error looked like
  it should scale with the delivered power, because the mechanism that moves the
  level does. It does not: it scales with `Λ′`. Measuring both and comparing them
  took ten minutes and would have been invisible forever otherwise, because at
  118 K the two forms agree.
- **A systematic and a fluctuation must not be added in quadrature**, however
  natural it looks in an error budget. What decides it is the timescale of the
  question: an alarm that fires in thirty minutes may only carry terms that can
  move in thirty minutes.
- **A gate in watts on a cryostat whose `Λ′` spans 14× is a gate in kelvin in
  disguise**, and it is strictest where the model is best. Quote both.
- **`bath.fit()` returns `(Bath, diagnostics)`, not a dict** — and the diagnostic
  key is `rms_k`, not `rms_K`. Both cost a run.
- **The heredoc trap bit again and it is worth restating**: `\n` inside
  `python - <<'PY'` lands a REAL newline in the file, quoted delimiter or not, so
  a patch script written that way produces an unterminated f-string thirty lines
  from where the mistake is. Write the script to a file. It also bit in a second
  way — replacing a docstring's opening lines without its closing `"""` silently
  swallows the next four functions.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/measure.py
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/band.py
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/holdout.py --in-epoch
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/pid_tuning.py --rows
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/export_response.py --dry-run
```

`band.py` is the one to run first now: it prints the band, what it is worth at
five temperatures, phase 1's gate both ways, and the matched-output pair either
side of the 09-10 fault. It needs `measure.py` to have been run once (11 s) and
then costs about a minute for the production fit plus a few seconds for the
coldplate pole.

`analysis/measured.csv` and `analysis/.fit_cache/` do not exist on a fresh clone
or in a new worktree.
