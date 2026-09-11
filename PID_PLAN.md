# Software PID — plan from here to "well functioning"

**Status: PHASE 0 NOT STARTED.** Rewritten 2026-09-11 after Jeff's answers to
the first draft's questions (§1); first draft at `7684b4f`.
Update this line as phases land, the way `REFIT_PLAN.md` does.

**What this plan is.** The route from the tree as it stands — 915 tests
passing, a loop that has never closed on the cryostat, and a controller whose
numbers the September campaign has since contradicted — to a software PID that
holds and sweeps the LTSPM3 sample over its whole range **and judges, from the
thermal response characterisation, whether the cryostat is behaving typically.**
The model is not only what the controller is tuned from; it is the reference
the cryostat is warned and faulted against.

**What this plan is not.** It does not restate [REFIT_PLAN.md](REFIT_PLAN.md)
(the thermal model) or [docs/ltspm3/commissioning.md](docs/ltspm3/commissioning.md)
(the staged way onto the hardware). It points into both.

**The standing instruction changed.** Until 2026-09-11 `CLAUDE.md` said the
software PID was complete and off limits and the viewer came first. Jeff's
restatement: **the goal is one program that supports both a person watching
their cryostat and a safe software PID.** `ltspm3/control/` is open to change
under the eight rules of [safety.md](docs/ltspm3/safety.md), one rule-scoped
commit at a time, each reviewed against the rule it touches. §2 says where
everything lives.

### Where a new session picks up

| | |
|---|---|
| **now** | Phase 0 (§4) — the fault window, the repair on the record, the `note` command, two stale comments |
| **then** | Phase 1 (§5) — finish the refit, export the typical band beside the table, **and extend the characterisation toward 300 K** |
| **then** | Phase 2 (§6) — `ltspm3/monitor.py`, report-only, graded against the archive's two real events |
| **then** | Phase 3 (§7) — the loop: numbers from the model, one rate limit, warn/fault in watts, filter-aware tuning, crash → disengage |
| **then** | Phase 4 (§8) — commissioning stage 3 close-out, stage 4, stage 5, the up-range ladder as stage 6 |
| **late** | Phase 5 (§9) — warnings and faults in the viewer |

---

## 1. Requirements — Jeff, 2026-09-11

Recorded here because they supersede numbers in `config.yaml`, in
`SupervisorConfig`'s defaults and in three documents. **Where this table and a
document disagree, this table wins until the document is corrected.**

| | requirement | today | consequence |
|---|---|---|---|
| **range** | **4 to 300 K** | measured 4.7–180.6 K; ceiling 70 % ≈ 192 K predicted | the characterisation must be extended upward, and the heater ceiling with it (§5.3). Below ~5 K the heater has no authority: "holding 4 K" is output zero |
| **hold** | slow wander **below the 10 s averaged noise floor** | open loop at 118 K: 26.7 mK rms, Allan 8.3 mK @ 4 s, 7.6 @ 60 s, 4.3 @ 600 s, **6.3 @ 3600 s** (rising again = wander) | the figure of merit is `σ_y(τ) ≤ σ_y(10 s)` for every `τ` from 10 s to the length of the run (§3.5) |
| **sweep** | **5 K/min** is a reasonable limit; 0.5 is excessively conservative | default 0.5, max 5.0 | 5 K/min at 118 K is 0.38 %/min of heater, **above the 0.20 %/min trim limiter**, and lags the setpoint by `r·τ_cl` = 25 K in MOVE. §7.2 resolves both |
| **rate limits** | **simplify — fewer of them** | eight: `max_step_pct`, `max_rate_pct_per_min`, `rate_k_per_min`, `max_rate_k_per_min`, `approach_rate_k_per_min`, two ramp-down rates and a knee | **one** kelvin rate, converted to heater percent through the model's local gain (§7.2) |
| **trim limiter** | 0.20 %/min is fine, "a very generous maximum" | 0.20 | kept — as the **floor** the derived percent limit may not go under when the model has no opinion |
| **ramp-down** | at the same limit, **5 K/min** | 1.0 %/min above 40 %, 2.0 below | expressed in kelvin through the model's inverse curve, open loop, so it works with the sensor lost (§7.2) |
| **premise** | **warn at 1 K, fault at 5 K**; the equivalent for the PID **in watts, informed by the fit** | `max_error_k` 1.0 K holds, then ramps down after 180 s | the 09-10 event (3.6 K, −4.9 mW) becomes a **warning**. Faults are for a lost sensor, a runaway heater or compressor, a strange transient (§3.4) |
| **unattended** | a weekend realistically; **no reason not to run indefinitely** | never run armed | a week is the stage-5 gate, not the design life |
| **failure** | **graceful** — the PID crashing while the cryostat is fine should just disengage | the poller catches a supervisor exception and keeps logging, but **does not disengage** the loop | §7.4 |
| **filter** | measurement filter **20 s**, dead time about one cycle | 60 s, and `pid_tuning.py` assumes 3 s dead time | `filter.tau: 20`; `TAU_FILTER_S = 20`, `DEAD_TIME_S = 2` |
| **monitor** | **report only** by default. Eventually hold or ramp down, **only when armed** | does not exist | the acting version lives in the supervisor, because a ramp-down exists only there (§3.4, §7.3) |
| **viewer** | warnings and faults shown, **late phase** | nothing | Phase 5 |
| **separation** | the PID stays separate from the broader `lschart` | invariant 1 | §2 |

---

## 2. Where it lives — the software model

Three packages, one direction of dependency, unchanged. What is new is naming
the **model** as a thing with an address, because three phases of this plan
read it and one writes it.

```
lschart/                 GENERIC.  Recorder, viewer, file interface.  Knows no
                         cryostat, no model, no PID.  Never imports ltspm3.
                         The viewer shows warnings and faults (Phase 5) by
                         reading plant.json -- a file, not an import.

ltspm3/                  THE LTSPM3 CRYOSTAT.  Imports lschart, never the reverse.
  model/                 THE CHARACTERISATION.  One place for every number that
                         describes the cryostat: thermal_response.py (the CD10
                         curve), fitted_response.py + _fitted_table.py (the ODE,
                         GENERATED by analysis/), sim_response.py (the two-pole
                         fake), and -- new -- the typical band (§3.2) and the
                         two residual functions (§3.1).  Stdlib only.
  control/               THE SOFTWARE PID.  supervisor, pid, tuning, guard,
                         coherence, feedforward, ramp, filters, dither.  Reads
                         model/ for feedforward, the schedule, the rate
                         conversion and (Phase 3) the watt residual.  Bound by
                         the eight rules; changed one rule-scoped commit at a time.
  monitor.py             THE JUDGE, outside the loop.  A separate process like
                         the viewer: no port, reads the recorder's files,
                         applies model/'s band, writes plant.json.  Report only.
                         Runs whether or not the loop is armed.
  tools/                 replay, steptest, sweep.  Procedures, not services.

analysis/                PRODUCES model/_fitted_table.py.  Imports neither
                         package; reads the archive and nothing else.
```

**Moving `thermal_response.py`, `fitted_response.py`, `_fitted_table.py` and
`sim_response.py` into `ltspm3/model/`** is a package move with import
updates — cheap, and it makes "the model" a directory rather than four files a
reader has to know about. Decision 3 in §11; the plan assumes yes.

**The one rule of the layout:** `control/` and `monitor.py` both read
`model/` and neither reads the other. The supervisor's in-loop check (§7.3)
and the monitor's out-of-loop check call the **same two functions** in
`model/`, so they cannot disagree about what typical means — only about what
to do, which is the point of having both.

---

## 3. What "typical" means

### 3.1 Watts, not kelvin

Everything the characterisation measured about *disturbances* came out as a
**power at the sample node**:

| what | size | source |
|---|---|---|
| campaign drift | **+0.281 mW/day**, three bands agreeing to ±25 % where K/day spans 5.6× | `analysis/drift.py` |
| the 09-10 heater-circuit fault | **−4.9 mW** step at 0.67 W (0.7 %) | HANDOFF 2026-09-10 |
| per-era offset July → September | 4.6–5.6 mW | REFIT_PLAN §2.3 |

The same disturbances in kelvin are 40× different across the band, because
the local gain runs 0.35 K/% at 10 K to 13.3 K/% at 118 K (fitted table,
evaluated today). A band in kelvin is wrong at one end or the other; a band in
**milliwatts** is one number for the whole range. So the primary residual is

```
δQ(t) = C(T_s)·dT_s/dt + [Λ(T_s) − Λ(T_c)] − P(u) − D(t)      [W]
```

the power the model cannot account for, `D(t)` the fitted campaign drift run
to today's date. At a settled hold the first term vanishes and this is the
steady-state check. **During a sweep it does not vanish, and the residual is
still valid** — which a check in kelvin cannot be, since a 5 K/min ramp lags
its setpoint by tens of kelvin by design (§7.2). This is the decisive reason
the premise checks move to watts.

`model/` gains two functions, stdlib only:
`missing_power_w(T_s, dT_dt, T_c, u, t)` and `sigma_q_w(T_s, u, t)`.

### 3.2 The band

`σ_Q` is built from what the campaign measured and **exported beside the
table** by `analysis/export_response.py`:

| term | value today | why it is in the band |
|---|---|---|
| `δP` | **0.7 % of P(u)** — the one measured size of the circuit's margin | REFIT_PLAN T10. Power-side, scales with P. Revisited at the repair boundary (§4 item 2) |
| drift uncertainty | ±25 % of 0.281 mW/day × days since `DRIFT_T0` | the spread across bands |
| `sigma_T_inf` → watts | median 14.2 mK × Λ′(T) | Phase A's per-anchor bar at the local slope |
| diurnal bound | 16 mK median, 69 mK max × Λ′(T) | measured amplitude, unknown phase — a bound, not a cycle |
| `T_c` locus | 27.6 mK rms × Λ′(T_c) | `analysis/bath.py` |

### 3.3 Four residuals, not one

`δQ` alone cannot separate the 09-10 event from the 09-09 one — both moved
the sample at a fixed readback. The archive says what distinguishes them:

| residual | typical | what it catches |
|---|---|---|
| **`δQ`** missing power at the sample | `n·σ_Q` | 09-10: −4.9 mW step, cold head unmoved. A **runaway heater** is this residual, large and positive |
| **`δT_c`** coldplate against its locus `T_c_inf(Q)` with a 175 s pole; the 1st and 2nd stage beside it | 28 mK rms | 09-09: cold-head channels **stepped**, sample followed. A **compressor failure** is this residual — the coldplate leaves its locus and keeps going — and **not** `δQ`, which stays small because the sample is doing exactly what the physics says at the new `T_c` |
| **`τ` ratio** observed/`tau_s(T)` on any step the recorder sees | 0.88–1.03 measured 50–114 K | dynamics that are not this cryostat's: cooler state, vacuum, a wire |
| **noise** rms over a settled window against `1.36e-6·T²`, floor 1.8 mK | within 2× | a sensor or a bus, not the cryostat |

### 3.4 Warn and fault — two levels, and what each is for

Jeff's rule: **faults are for a lost sensor, a runaway heater or compressor,
and strange transients. Typical slow cryostat changes never fault, and the
09-10 event should have warned.**

| level | what happens | `δQ` | `δT_c` | guard | τ / noise |
|---|---|---|---|---|---|
| **typical** | nothing | inside `n_warn·σ_Q` | inside band | OK | inside |
| **warn** | alarm in `plant.json` / `status.json`; loop keeps tracking | beyond `n_warn·σ_Q` for `warn_after_s` | beyond band | SUSPECT | outside |
| **fault** | armed: hold, then ramp down at the kelvin rate (§7.2), then lock out. Unarmed: alarm only | beyond `fault_mw` for `fault_after_s` | rising past `tc_fault_k` or at a rate no locus explains | FAULT | never — these inform, they do not fault |

**Kelvin equivalents, so the thresholds mean the same thing to a person.**
Jeff's 1 K / 5 K at the local slope Λ′(T):

| T | Λ′ | 1 K ≡ | 5 K ≡ | note |
|---|---|---|---|---|
| 20 K | 19.2 mW/K | 19 mW | 96 mW | |
| 40 K | 4.8 | 4.8 | 24 | |
| 60 K | 2.3 | 2.3 | 11 | |
| 118 K | 1.65 | **1.7** | **8.3** | the 09-10 event, −4.9 mW, sits between: a **warning**, as required |
| 180 K | 1.9 | 1.9 | 9.4 | |

So the thresholds are **stated in watts** — `warn` at `n_warn·σ_Q`, `fault` at
`fault_mw` — and the table above is how the plan checks they are "roughly
equivalent" to 1 K and 5 K. `fault_mw` starts at 8 mW. **Both are then set by
the replay of §6.3**: nothing in the archive may fault, both events must
warn. `n_warn` starts at 3.

Everything in this table is config in `SupervisorConfig` and the monitor's
own section — invariant 7 — and none of it is a constant in code.

### 3.5 The hold figure of merit

Jeff's threshold: slow wander below the 10 s averaged noise floor. As a
measurement: over any settled closed-loop run of length `L`,

```
σ_y(τ) ≤ σ_y(10 s)     for every τ in [10 s, L/4]
```

with `σ_y` the Allan deviation of the sample channel. Open loop today at 118 K
this fails at 3600 s (6.3 mK against about 8 mK at 10 s is a pass; the 2.6 mK/h
linear drift over a day is not). At 290 K the noise floor is 109 mK rms, so the
bar is far looser up there — that is the measurement, not the loop.
Commissioning C6 measures it; `analysis/` gets a small `allan.py` so the same
code grades the open-loop archive and the closed-loop runs.

### 3.6 Where the model has no opinion

The model has data over 4.7–180.6 K, from a cryostat with the cooler running
and shields cold. Outside that it must say so, not cry anomaly:

- **T outside the table** — clamps by design. Today 4.7–195 K; §5.3 extends it.
- **Output below ~28 %** — the heater has no authority against the cooler,
  so K and τ are undefined, not small.
- **Below ~25 K, `τ` ratio** — τ is seconds against a 2 s cadence; the steady
  state is real and the dynamics unmeasurable. `δQ` still applies.
- **Cooler off, or a cooldown in progress** — `δT_c` says so first; while it is
  atypical, `δQ` downgrades to no-opinion rather than a second alarm for one
  cause.
- **Inside a mask window** or within `3·τ(T)` of a heater move.

---

## 4. Phase 0 — bookkeeping, this week

No code in `control/`. Each is small and each is a trap left open.

1. **Mask the 09-10 fault window.** The sample has been flat at 118.33 K on
   64.010 % since 16:00 on 09-10. Archive the stretch and give 11:33 → the
   repair one `mask` row with a paragraph, as `mask-20260909-180604` has.
   REFIT_PLAN §0.4: do not archive a half-event.
2. **Put the repair on the record.** Nothing in the log says what changed at
   ~15:00 on 09-10 except the readback moving 64.016 → 64.010 and the sample
   recovering 3 K in an hour. Add a manifest row the way `era` records the
   recalibration — REFIT_PLAN T10 asks for it — and it becomes the boundary
   at which `δP` may be revisited.
3. **A `note` command kind** through the spool, into the CSV's empty `Notes`
   column. Generic `lschart`, passes `accept_commands` and the source policy,
   moves nothing. Both September events are unattributable for want of it.
   MATLAB gets `note()`.
4. **Two stale comments.** `config.yaml` names `lschart.tools.steptest`; the
   tool is `ltspm3.tools.steptest`. `config-ltspm3-heater.yaml`'s dated
   cryostat block still reads 2026-08-31.
5. **Correct the documents §1 supersedes**: commissioning's decision on
   `MAX_END_RATE_K_PER_H` (moot — the manifest already grades all four rungs
   `tau`), the 96 K Allan figures quoted as the hold target, and the
   "leave `control/` alone" sentences in `running.md` and `REFIT_PLAN.md` §9.

**Exit gate:** `curate.py --propose` clean with the new rows; `send note "..."`
lands in the CSV; the documents corrected.

---

## 5. Phase 1 — the model: finish the refit, export the band, extend the range

### 5.1 The refit

[REFIT_PLAN.md](REFIT_PLAN.md) §7 steps 7–10, unchanged, gate unchanged:
**leave-one-epoch-out predicts the three post-recal holds inside 0.5 K having
never seen them, or nothing proceeds.** The two open questions in `HANDOFF.md`
are settled *before* regenerating — the below-10 K basin and `δP` as a
power-side bar.

### 5.2 What this plan adds to step 10

- `export_response.py` writes the constants of §3.2 beside `TABLE`, plus
  `DRIFT_T0` and the `T_c` locus with `TAU_BATH_S`. Generated, in the cache key.
- `model/` gains `missing_power_w` and `sigma_q_w`.
- `analysis/pid_tuning.py` onto `production_inputs()`, `TAU_FILTER_S = 20`,
  `DEAD_TIME_S = 2`, printing rows in the form §7.1 needs.
- `analysis/allan.py` (§3.5).

**Exit gate:** REFIT_PLAN §1's three rows green; `SUPERSEDED_NOTE` cleared;
`test_fitted_response.py`'s eight pins **regenerated, not loosened**;
`missing_power_w` under 1 mW at every settled anchor the fit was given.

### 5.3 Extending to 300 K — a hardware decision first

Nothing above 180.6 K has ever been measured. The fitted curve predicts about
192 K at the 70 % ceiling (0.80 W) and clamps at 195 K. Extrapolating Λ′ and
adding radiation, **300 K plausibly needs 1.0–1.3 W, which is 80–90 % of the
218's output** into the 75.5 Ω heater (1.63 W at 100 %). Three things must be
true before a ladder goes up there, and only the third is software:

1. **The heater and its wiring are rated for it.** The 09-10 fault was a
   wiring fault at 0.67 W. This is Jeff's call (§11 decision 1).
2. **The rest of the cryostat tolerates it.** THE CHONKE's loop is railed at
   100 % holding 290 K today; a 300 K sample radiating onto a 40 K shield
   loads the cooler. The 1st and 2nd stage channels are the evidence, and
   `δT_c` is the monitor's way of watching them.
3. **`max_output_pct` is raised in steps, never past the highest rung
   measured settled**, exactly as the ceiling has been kept below 70 % so far.

Then the ladder itself is commissioning stage 6 (§8.3): `plan_sweep.py`
upward from 64 %, each rung a `jump` window, `curate.py --propose` the diff,
refit, re-export. The plan's own predicted τ up there is 600 s and falling
slowly; expect an hour a rung.

**The 4 K end costs nothing.** The heater has no authority below ~28 %, so
holding at base is output zero and the loop's job is to *stay* at zero — the
band's floor already allows it and the fitted table already covers 4.7 K up.

---

## 6. Phase 2 — the monitor

`ltspm3/monitor.py`. A **separate process**, like the viewer: no port, reads
the recorder's CSV tail and `status.json`, writes `plant.json` beside
`status.json` — arrays not objects, `SCHEMA_VERSION`, `os.replace` — plus a
`plant_YYYY-MM-DD.csv` of every residual per cycle so a verdict can be
audited a month later.

### 6.1 What it computes

`δQ`, `δT_c`, the noise rms over a trailing settled window, and — when `u`
changes and then holds — a pole fit against `tau_s(T)`. Each with its band,
each with a verdict (typical / warn / fault-level / no opinion) and the reason
in words. Persistence before reporting: `warn_after_s`, as `fault_after_s`
works in the guard. It reads `control` from `status.json`: when the loop is
armed the monitor is the second opinion, and where it disagrees with the
supervisor that is a finding.

### 6.2 What it may do

**Report. Only report.** Jeff, decision 1: the acting version belongs in the
supervisor because a ramp-down exists only there and "only when armed" is
exactly what the supervisor knows. The monitor never sends a command. Its
value is that it runs *now*, unarmed, over a cryostat the loop has never
touched — and that it is the reference implementation the supervisor's
in-loop check (§7.3) is tested against.

### 6.3 The test on genuine data

`python -m ltspm3.monitor --replay reference/cooldown-10/` runs the archive
and prints every verdict change with its time. Pinned in `tests_ltspm3/`:

| window | must |
|---|---|
| 2026-09-09 18:06 | `δT_c` **warn** within 30 min; `δQ` typical or no-opinion |
| 2026-09-10 11:33 | `δQ` **warn** within 30 min, −5 ± 2 mW; `δT_c` typical; **not fault-level** |
| the three post-recal holds | typical throughout, `δQ` within ±1 mW of the drift line |
| `trace-ladder-20260905`, 30 rungs | no warn; 27 of 30 rungs return a τ ratio, all 0.85–1.10 above 40 K |
| `trace-sweep-20260902`, 43 h | no warn outside its masked windows |
| the 09-04 12:07 recalibration boundary | no verdict change |
| **the whole archive** | **zero fault-level verdicts** — this is what sets `fault_mw` and `tc_fault_k` |

**False-alarm budget: under one warning per week on a settled hold.** Every
verdict change in the replay gets read by a person once. That reading is this
phase's pause.

**Exit gate:** the table green in `pytest`; the monitor beside the live
recorder for 72 h; the residual after the 09-10 repair inside the band.

---

## 7. Phase 3 — the loop

Now `control/` changes, **one rule-scoped commit each**, in this order, each
with the eight rules re-read against it. The bench of §7.5 runs after every
one.

### 7.1 Numbers from the model, through config

- `filter.tau: 20`.
- `TuningConfig.schedule` from `pid_tuning.py`, rows every ~10 K over the
  measured range, replacing `PROVISIONAL_SCHEDULE` (four rows, three at
  τ = 620 s; measured τ is 36 s at 40 K, 166 s at 60 K, 440 s at 100 K).
- `FeedforwardConfig.calibration` from the fitted steady state **at today's
  date**, every 0.5 % over the measured range. The CD10 curve it replaces has
  no point between 43 % and 63 % and was 17 K wrong in the middle.
- `tuning.py` learns the filter: SIMC's half rule with `tau_filter` and
  `dead_time` as `TuningConfig` fields, so the schedule carries the plant's
  own τ and the loop's lags are stated once. (The first draft proposed hiding
  the filter in the rows; with `control/` open, the honest form wins.)

### 7.2 One rate limit — and why it cannot be a constant in percent

**The collision.** 5 K/min and 0.20 %/min are both "fine" and are
incompatible above 50 K:

| T | K/% | 5 K/min needs | plant lag `5·τ/60` | closed-loop lag, MOVE `τ_cl` 300 s |
|---|---|---|---|---|
| 10 K | 0.35 | **14.3 %/min** | 0 K | 25 K |
| 40 K | 3.9 | 1.3 %/min | 3 K | 25 K |
| 60 K | 9.3 | 0.54 %/min | 14 K | 25 K |
| 118 K | 13.3 | **0.38 %/min** | 44 K | 25 K |
| 180 K | 11.8 | 0.43 %/min | 50 K | 25 K |

Two things follow, and they are the two changes in this step.

**One kelvin rate, converted through the model.** `max_rate_k_per_min: 5.0`
becomes the only rate in the config. The heater limiter derives its percent
rate as `max_rate_k_per_min / K(T)` from `model/`, floored at
`min_rate_pct_per_min: 0.20` (Jeff's "generous maximum" becomes the floor the
derivation may not go under where the model has no opinion). Setpoint ramps,
the approach after a fault and the fault ramp-down all use the same number.
Retired: `max_step_pct` (it is rate × cycle), `max_rate_pct_per_min`,
`rate_k_per_min`, `approach_rate_k_per_min`, `rampdown_pct_per_min`,
`rampdown_knee_pct`, `rampdown_below_knee_pct_per_min`. Eight numbers → two.

**The ramp-down goes through the model's inverse curve, open loop.** A fault
may mean the sensor is gone (rule 3), so a kelvin-rate descent cannot close on
the sample. Instead the supervisor walks `u(t) = percent_for(T_target(t))`
with `T_target` falling at the kelvin rate from the last trusted temperature,
which needs the model and not the sensor. Where the model has no opinion it
falls back to the percent floor. From 118 K to base that is about 23 minutes
at 5 K/min against 44 today. Rule 1 holds: this only ever lowers the heater.

**MOVE gets a shorter `τ_cl`.** With a 20 s filter, `move_tau_cl_s` of 60 s
is a phase margin the 60 s filter never allowed; the closed-loop lag on a
5 K/min ramp drops from 25 K to 5 K. `max_ramp_error_k` is retired with the
kelvin premise check (§7.3): during a ramp the watt residual is the check.

### 7.3 Premise checks in watts

`max_error_k` / `anomaly_hold_s` are replaced by the two-level scheme of §3.4:

| field | value | means |
|---|---|---|
| `warn_error_k` | 1.0 | tracking error above this → alarm, keep tracking |
| `warn_sigma` | 3 | `δQ` beyond this many `σ_Q` → alarm |
| `fault_mw` | 8 (then set by §6.3) | `δQ` beyond this for `fault_after_s` → hold, ramp down, lock out |
| `tc_fault_k` | from the replay | coldplate beyond its locus by this → the same |
| `fault_after_s` | 180 | the existing anomaly hold, reused |

The supervisor's `_check_model` calls `model.missing_power_w` and
`model.sigma_q_w` — the same two functions the monitor calls — and drops the
CD10-curve comparison and `model_trust_k` (15 K, which is three 09-10 events).
The kelvin `warn_error_k` stays because a person thinks in kelvin; it never
faults. **Rule 4 is rewritten** in `safety.md` in the same commit: the
premise is now "the watts add up", not "the error is under a kelvin".

### 7.4 Crash → disengage

The poller already catches a supervisor exception and keeps logging. It does
not disengage. After the catch: `panic_hold()` — `OFF`, output frozen where
it is, nothing written — and `status.control.state = "crashed"` with the
exception's first line, until `acknowledge()` + `arm()`. Rules 6 and 7. A test
raises inside `step()` on a virtual clock and asserts the recorder's next
frame is written, the output unchanged, the state `crashed`, and `arm` refused
until `ack`.

### 7.5 Bench on the fitted plant, whole range

The harness (`tests_ltspm3/conftest.py`) runs on `sim_response`, one τ. Add a
fixture on `FittedResponse` and run hold / 5 K/min sweep / glitch / fault /
crash at **10, 30, 60, 100, 140 and 180 K**, with §7.1's tables loaded from a
real config file. The point is 10 and 30 K, where the loop is filter-limited
and the old rows were an order of magnitude off, and 180 K, where the band is
lopsided against the ceiling.

**Exit gate, all six temperatures:** a 3 K move with no overshoot above 5 %;
a 5 K/min sweep of ≥ 10 K with no fault and the `δQ` check quiet; a glitch
that freezes and recovers; a fault that ramps down at the kelvin rate through
the inverse curve and latches; a crash that disengages. `check` prints one
rate and a band bracketing the present output. §6.3's replay still green.

---

## 8. Phase 4 — onto the cryostat

[commissioning.md](docs/ltspm3/commissioning.md), not restated. The monitor
runs alongside from stage 3 on and `plant.json` is part of every gate.

### 8.1 Stage 3 close-out

| | | gate |
|---|---|---|
| **W1** | twenty distinct writes, `AOUT?` at 0/25/50/80/150/300 ms, `write_settle_s` set with margin and the evidence in `HANDOFF.md` | the 09-05 sweep verified 30 writes by readback — the retry loop copes — and says nothing about freshness. Invariant 5 |
| **W2** | `send analog 70.5` refused; `analog 0` refused with the gate closed; `heaters_off` reaches the box | zero refusals in the whole command history |
| **circuit** | the repair recorded (§4 item 2); `δQ` inside its band for 72 h | a flat hold is weak evidence (trap P8); it is the evidence available |

### 8.2 Stage 4, attended, and stage 5

4a → 4d as written, with a third config file `config-ltspm3-armed.yaml` so
neither the read-only nor the heater config ever grows a `control:` section
by accident. `operating_point_pct` re-centred on the output that holds the
chosen temperature today (64.010 % holds 118.3 K); `authority_pct` narrowed
as commissioning 0.3 says. Before arming, `plant.json` reads typical on all
four residuals for the preceding hour. **4d becomes a 5 K/min sweep**, the
requirement, not 0.5.

Stage 5: a week unattended is the gate; indefinite is the design. Ends with
the §3.5 figure and every warning explained.

### 8.3 Stage 6 — the up-range ladder

After §5.3's hardware decision. Rungs upward from 64 % with the ceiling
raised one measured rung at a time; each rung is a `jump` window and a
refit input; the monitor's `δT_c` and the stage channels are the watch on the
cooler. Stops where Jeff says, or where `δT_c` says the cooler is losing.

---

## 9. Phase 5 — warnings and faults in the viewer

Late, by request. The viewer reads `plant.json` as it reads `status.json`: a
row per residual in the loop table's style, the verdict coloured by
`theme.py`'s exceptional pairs and contrast-checked, the reason in the hover.
No import of `ltspm3` — it is a file. MATLAB gets `plant()`.

---

## 10. Traps

**P1 · A band in kelvin is wrong at one end.** Gain spans 40×; every threshold
is in watts at the sample node, converted to kelvin only for display.

**P2 · The circuit fault and a missing load path look identical in `δQ`.**
`δT_c` separates "at the sample" from "at the cold head"; report both.

**P3 · A compressor failure is a coldplate event, not a `δQ` event.** With
`T_c` measured and the model right, the sample follows the physics and `δQ`
stays small while the cryostat warms. The fault criterion for it is `δT_c`
and the stage channels. A monitor watching only `δQ` would call a compressor
failure typical.

**P4 · "Typical" drifts.** 0.281 mW/day; a band right today is 8 mW off in a
month without the date in it. Every export carries `DRIFT_T0`.

**P5 · No opinion is not typical.** Outside the table, below 28 %, within
`3·τ` of a move, cooler off: say so. A green light there is a lie.

**P6 · The monitor inherits the naive-timestamp fold** (AUDIT-2026-09-10
finding 4). Take `t` from the recorder's monotonic `Time` column from day one.

**P7 · Pastes rot.** Schedule rows carry the fit's cache key and date in
`note`; a test compares the config's rows to a fresh export.

**P8 · A flat hold is not evidence the circuit is fine.** 64.016 % held flat
for 25 hours before 11:33. Only `δQ` on the drift line across *changing* `u`
says the watts arrive.

**P9 · A kelvin rate in percent is a function of temperature.** 5 K/min is
0.38 %/min at 118 K and 14 %/min at 10 K. Any constant percent limit is
either a brake at the cold end or no limit at the warm one.

**P10 · The ramp-down must not need the sensor.** Rule 3 says a fault may be
the sensor. The inverse-curve descent is open loop for that reason; a
kelvin-rate descent closed on the sample would stall on a lost reading.

**P11 · Nothing above 180.6 K is measured.** The table clamps at 195 K. Every
number about 300 K in this plan is an extrapolation and says so; the ladder
of §8.3 is what replaces it.

**P12 · Above 195 K the sensor noise is the loop's floor.** 109 mK rms at
290 K. The hold criterion is relative to the 10 s floor for this reason; an
absolute mK target up there would be a target for the thermometer.

---

## 11. Decisions for Jeff

1. **The heater ceiling for 300 K.** §5.3: plausibly 80–90 % and 1.0–1.3 W
   into a heater whose wiring faulted at 0.67 W. Is the heater and its wiring
   rated for that, and what is the ceiling you are willing to raise
   `max_output_pct` toward?
2. **One rate.** §7.2's proposal: `max_rate_k_per_min: 5.0` the only rate,
   heater percent derived through the model's gain with a 0.20 %/min floor,
   ramp-down through the inverse curve at the same rate. Yes, or keep any of
   the eight?
3. **`ltspm3/model/`.** Move the four model files into a package (§2). Cheap;
   yes unless you object.
4. **`move_tau_cl_s`** from 300 s to 60 s with the 20 s filter (§7.2). It is
   what makes a 5 K/min sweep track inside a few kelvin.

Settled 2026-09-11: report-only monitor, acting version in the supervisor and
only when armed; `control/` open under the eight rules; the end-rate grading
question was already moot.

---

## 12. Out of scope

Fixing or re-rating the heater circuit is hardware and Jeff's. The viewer,
MATLAB and Windows deployment continue as the monitoring half of the same
program; this plan touches them only additively (§4 item 3, §9). Nothing in
phases 0–3 arms a heater or moves an output.
