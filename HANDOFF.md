# Handoff — 2026-09-14 (phase 1 done, phase 2 built; the loop is next)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
route to a working loop is [PID_PLAN.md](PID_PLAN.md). This goes stale.

Previous: [HANDOFF-2026-09-14a.md](HANDOFF-2026-09-14a.md) (phase 1, the band)
and [HANDOFF-2026-09-13.md](HANDOFF-2026-09-13.md) (the thermal refit).

> ## NOTHING WAS COMMANDED, AND THE HEATER IS WHERE IT WAS
>
> The 218's analog output has been at **64.0100 %** since 2026-09-10 14:49 and
> the sample flat at **118.3 K** since 09-11. Every number below comes from the
> archive. **994 tests passing, `ruff` clean.**

## Jeff's two rulings, and what each one turned into

**1. A four-probe measurement of the heater circuit is out of scope; size the
margins instead.** → `DELTA_P_FRAC` left the error band and became `bias_q_w`,
reported on its own. In the band it makes 3σ at 118 K **14 mW**, three times the
09-10 fault the monitor has to catch. Out of it, the band is **1.4 mW** and the
loop never feels the bias anyway, because integral action absorbs a constant
power offset exactly.

**2. Recalibrate at most once per cooldown; typical erroring behaviour is so
large as to be unmistakable from these variations.** → the monitor alarms on the
**change** in the residual against a slow baseline, not on its level. The level
carries the calibration and the unmodelled drift, which over a months-long
cooldown reach **31 K**; the change does not grow with time at all. One gauge
per cooldown is then enough, which is what was asked for.

The second ruling also arrived from the data independently, in §2.4 of plan 2 —
see the 09-09 event below.

## Phase 1 — the model — DONE

`analysis/band.py` measures the band; the constants ship in
`_fitted_table.py`; `missing_power_w` and `sigma_q_w` are in
`fitted_response.py`. 3σ settled is **1.4 mW at 118 K** and **0.4–0.9 K across
10–180 K** at the local gain — the band is about a kelvin everywhere, which is
where Jeff's "warn at a kelvin" lands when it is arrived at from measured terms.
The gate is **0.583 mW worst over 40 K in-epoch**, met.

Two findings worth keeping: the model's error is flat **in kelvin** (0.135 K),
not in watts and not as a fraction of power; and a gate in watts on a cryostat
whose Λ′ spans 14× is a kelvin gate in disguise, strictest exactly where the
model is best.

## Phase 2 — the monitor — BUILT

`ltspm3/monitor/`. Separate process, no port, no commands — structurally, not by
configuration. 27 tests, eleven of them the replay on genuine data.

```
python -m ltspm3.monitor --replay reference/cooldown-10/
python -m ltspm3.monitor -c config-ltspm3-heater.yaml
```

**The row that matters: the 2026-09-10 fault warns at 11:44 — eleven minutes
after the event — at −5.01 mW against a required −4.9 ± 2 mW, and as a warning
rather than a fault.** The recalibration moves no verdict, the three long holds
and the 43 h sweep are quiet, and the false-alarm budget is met.

### The baseline variable, which took two wrong answers

What the baseline absorbs is the heater circuit's delivered fraction — the
calibration, and the campaign drift, which REFIT §7.2 measured to be the *same
quantity* moving slowly. So it is kept as a **fraction of delivered power**.

| kept in | a 1 % calibration error is | |
|---|---|---|
| watts | 12 mW at 18 K, 20 mW at 94 K | the baseline chases the sweep |
| kelvin | 0.1 K at 18 K, 2.6 K at 94 K | a factor of **26** |
| **fraction of P** | the same number everywhere | what a series resistance *is* |

Kelvin is the trap, and it is worth remembering why: the model's **shape** error
is flat in kelvin, which is a real measurement from phase 1. It looks like the
right variable right up until the thing being absorbed is the **level** instead.
Shape error and level error are different quantities with different shapes.

### Four defects the replay found that no unit test would have

- a pole fitted to an hour of settled hold has no relaxation left in it and fits
  the cryostat's **drift** — ratios of 62. The refit's own two grading rules now
  apply here too.
- the noise check measured scatter about a **line** over 300 s; a cryostat
  relaxing through that window is a curve, so it reported 685 mK rms against an
  expected 18 and warned for hours about a thermometer that was fine.
- the noise and coldplate checks had no transient gate: a ladder rung read as
  1626 mK of noise and 192 mK of sink.
- a per-cycle window scan is 150 million operations a pass. `RollingFit` makes
  the slope and the scatter O(1) and the replay 30 s instead of minutes. **The
  live monitor would never have noticed** — one cycle a second, forever.

### Three rows not met, and §2.4 argues each

**The 2026-09-09 event is not visible in the channels the monitor has**, and
this is the most interesting thing the phase found. Its coldplate step is
**7.6 mK** — a quarter of that channel's own rms — and its 1st Stage step is
90 mK. Adding the cold-head stages catches it at 18:17. But the replay then
answered a question the plan had not asked: **a 70–170 mK step on the 1st Stage
happens five times in the four settled days before it.** The 09-09 step is not
exceptional in the only channel that shows it. Tuned to catch it the check costs
nine warnings a week against a budget of one; set where ordinary steps are quiet
it is silent across the archive and still catches what it exists for — a
compressor degrading moves those channels by **kelvins**.

That is Jeff's second ruling arriving from the data rather than from the
instruction. What stays on the record is the manifest's own conclusion, which
this replay independently confirms: *something moves the sample's steady state
at constant power that Λ(T_s) − Λ(T_c) cannot see.* REFIT §2.5's open
diagnostic, and no threshold closes it.

**The τ ratio** is 0.97–1.01 above 60 K and **0.84 at 45–58 K**. Waiting longer
makes it worse, not better — at a reach of 8 the ladder's dwells are too short
and nothing is measured at all. It is the monitor's one-pass pole fit on a short
dwell, not the model (the refit's graded τ agree to 6.7 %). Left as it is rather
than tuned into passing: τ never faults and is not what catches a heater event.

**The archive has two fault-level excursions** — 2026-09-04 and 2026-09-10, both
real wiring events. The row expected none. What it actually says is that **8 mW
is below the size of a reseated connector**, which matters before plan 3 turns
`fault_mw` into a ramp-down.

### And one thing it corroborated in passing

Five cold-head steps in the four settled days before the fault; six in the one
and a half after the reseat. HANDOFF-2026-09-13 measured **twice the wander over
300–1200 s** in the same period by a completely different method. Two
independent signatures: **reseated is not repaired.**

## Then, in order

1. **PID_PLAN phase 3 — the loop — is the next code**, and
   [plans/pid-3-loop.md](plans/pid-3-loop.md) is unchanged. Every step is one
   commit, names the safety rule it touches, and runs the bench before it lands.
   `pid_tuning.py --rows` already prints §3.2's schedule from the production fit
   with the shipped table's `FIT_KEY` on every row.
   - **§3.4 has a decision waiting**, not a calculation: `fault_mw` seeded at
     8 mW is smaller than either measured wiring event, so a ramp-down there
     would fire every time somebody touches the heater. The two events are
     −21.9 mW and −11.6 mW. Jeff's call, with those in front of it.
2. **Phase 2's 72 h live soak is outstanding** and nothing about it is blocked
   on code — it needs the recorder restarted with the monitor beside it. Worth
   doing early: the monitor runs whether or not the loop is armed, which is most
   of this cryostat's life, and the soak is what turns the replay's false-alarm
   budget into a measured one.
3. `lschart status` and MATLAB `plant()` do not read `plant.json` yet. Phase 5's
   work in the viewer and ten lines in `LakeShore.m`; neither blocks the soak,
   which only needs the file to exist and be current.
4. The 09-10 mask still goes in with the next archive export, not before.

## Traps this session added to the list

- **A band term must be told what it is proportional to** — and so must a
  baseline. Three variables, three different answers, and at 118 K all three
  agree, which is why only a record that crosses the range can choose between
  them.
- **A systematic and a fluctuation must not be added in quadrature.** What
  decides it is the timescale of the question: an alarm that fires in thirty
  minutes may only carry terms that can move in thirty minutes.
- **A gate in watts on a cryostat whose Λ′ spans 14× is a gate in kelvin in
  disguise.** Quote both.
- **Short-term scatter is not long-term wander, on every channel and not just
  the sample.** The 1st Stage is quiet to 4 mK over a minute and wanders 100 mK
  over an hour; a bar set from the first warns twenty times on the second.
- **A per-cycle window scan is invisible in the live path and fatal in the
  replay**, which is an argument for having a replay rather than against having
  a scan.
- **`bath.fit()` returns `(Bath, diagnostics)`, not a dict**, and the key is
  `rms_k`.
- **Do not patch Python through a heredoc.** `\n` inside `python - <<'PY'` lands
  a real newline in the file; and replacing a docstring's opening without its
  closing `"""` silently swallows the next four functions. Both cost a revert
  this session. Write the patch script to a file.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ltspm3.monitor --replay reference/cooldown-10/
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/band.py
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/pid_tuning.py --rows
```

The replay needs nothing prepared — `reference/cooldown-10/` is versioned, and
it is the one test here on genuine data. `analysis/` still needs `measure.py`
run once (11 s) before anything that fits.
