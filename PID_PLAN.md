# Software PID — plan from here to "well functioning"

**Status: PHASE 0 NOT STARTED.** Written 2026-09-11 against `bec8906`.
Update this line as phases land, the way `REFIT_PLAN.md` does.

**What this plan is.** The route from the tree as it stands — 915 tests
passing, a loop that has never closed on the cryostat, and a controller whose
numbers the September campaign has since contradicted — to a software PID that
holds and sweeps the LTSPM3 sample **and can say, from the thermal response
characterisation, whether the cryostat is behaving typically or not.** That
second clause is Jeff's ask of 2026-09-11 and it shapes the whole plan: the
model is not only what the controller is tuned from, it is the reference the
cryostat is judged against.

**What this plan is not.** It does not restate [REFIT_PLAN.md](REFIT_PLAN.md)
(the thermal model) or [docs/ltspm3/commissioning.md](docs/ltspm3/commissioning.md)
(the staged way onto the hardware). It points into both and adds what neither
has: the *typical band*, the monitor that applies it, and the order in which
the loop's numbers get replaced.

**Shape:** five phases. Phases 0–4 touch **nothing in `ltspm3/control/`** —
they are bookkeeping, `analysis/`, a new tool, config, tests and procedure at
the cryostat. Phase 5 is the one `control/` change and needs Jeff's explicit go
(§10, decision 2). The standing instruction in `CLAUDE.md` holds throughout.

### Where a new session picks up

| | |
|---|---|
| **now** | Phase 0 (§3) — the fault window, the repair on the record, the `note` command, two stale comments |
| **then** | Phase 1 (§4) — finish the refit (REFIT_PLAN §7 steps 7–10) **and export the typical band with the table** |
| **then** | Phase 2 (§5) — `ltspm3/tools/monitor.py`, graded against the archive's two real events |
| **then** | Phase 3 (§6) — the loop's schedule and feedforward from the model, through config only; bench on `FittedResponse` |
| **then** | Phase 4 (§7) — commissioning stage 3 close-out, stage 4, stage 5, with the monitor as the independent judge |
| **decide** | Phase 5 (§8) — fold the residual into the supervisor, or leave it outside for good |

---

## 1. The goal

Three rows, all green at once, with nothing retuned between them:

| | target | evidence | where it stands 2026-09-11 |
|---|---|---|---|
| **hold** | Allan deviation within 2× of the measurement floor at the operating point (thermal-response.md: 4.1 mK @ 60 s, 2.5 mK @ 600 s near 96 K) over ≥ 6 h | commissioning C6 | never closed |
| **sweep** | 0.5 K/min over ≥ 10 K, tracking error inside `max_error_k` + allowance, no anomaly hold at either end, no overshoot above 5 % of the move | commissioning 4d, C5 | never closed |
| **typical** | the monitor flags **both** archive events (09-09 18:06, 09-10 11:33) and flags **nothing** across the three post-recal holds, the 09-05 ladder, or the 43 h sweep | §5.3 replay | does not exist |

**Definition of done.** All three rows green; `PROVISIONAL_SCHEDULE` no longer
the schedule in force; feedforward on the fitted steady state; the monitor
running beside the recorder and writing `plant.json`; a week at stage 5 with
no unexplained hold; `HANDOFF.md` carrying the commissioning log.

---

## 2. What "typical" means, and in what units

### 2.1 Watts, not kelvin

Everything the characterisation measured about *disturbances* came out as a
**power at the sample node**:

| what | size | source |
|---|---|---|
| campaign drift | **+0.281 mW/day**, three bands agreeing to ±25 % where K/day spans 5.6× | REFIT_PLAN §7 step 5 |
| the 09-10 heater-circuit fault | **−4.9 mW** step at 0.67 W (0.7 %) | HANDOFF 2026-09-10 |
| per-era offset July → September | 4.6–5.6 mW | REFIT_PLAN §2.3 |

The same disturbances in kelvin are 40× different across the band, because the
local gain runs 0.3 K/% at 10 K to 13.9 K/% at 180 K. A "typical" band in
kelvin is therefore wrong at one end or the other; a band in **milliwatts** is
one number for the whole range. So the monitor's primary residual is

```
δQ(t) = C(T_s)·dT_s/dt + [Λ(T_s) − Λ(T_c)] − P(u) − D(t)      [W]
```

the power the model cannot account for, with `D(t)` the fitted campaign drift
run to today's date. At a settled hold the first term vanishes and this is the
steady-state check; during a transient it is the dynamic one, with the same
units and the same band. `analysis/fit_ode.py` already computes every term;
`ltspm3/fitted_response.py` carries Λ′, C and `T_c(T_s)` frozen, stdlib only.

### 2.2 The band

"Typical" is `|δQ| < n·σ_Q`, where `σ_Q` is built from what the campaign
measured and **exported beside the table** (§1):

| term | value today | why it is in the band |
|---|---|---|
| `δP` | **0.7 % of P(u)** — the one measured size of the circuit's margin | REFIT_PLAN T10. Power-side, scales with P |
| drift uncertainty | ±25 % of 0.281 mW/day × days since the fit | §7 step 5's spread across bands |
| `sigma_T_inf` → watts | median 14.2 mK × Λ′(T) | Phase A's per-anchor bar, converted at the local slope |
| diurnal bound | 16 mK median, 69 mK max × Λ′(T) | measured amplitude, unknown phase — a bound, not a cycle |
| `T_c` locus | 27.6 mK rms × Λ′(T_c) | `analysis/bath.py` |

`n` is a config number, not a constant in code. Start at 3.

### 2.3 Four residuals, not one

`δQ` alone cannot separate the 09-10 event from the 09-09 one — both moved the
sample at a fixed readback. The archive says what distinguishes them:

| residual | typical | what it caught |
|---|---|---|
| **`δQ`** missing power at the sample | ±n·σ_Q | 09-10: −4.9 mW step, cold head unmoved |
| **`δT_c`** coldplate against its locus `T_c_inf(Q)` with a 175 s pole | 28 mK rms | 09-09: cold-head channels **stepped**, sample followed at τ ≈ 2,200–3,700 s |
| **`τ` ratio** observed/`tau_s(T)` on any step the recorder sees | 0.88–1.03 measured 50–114 K | a τ far off means C or Λ′ is not this cryostat's — cooler state, vacuum, a wire |
| **noise** rms over a settled window against `1.36e-6·T²`, floor 1.8 mK | within 2× | a sensor or a bus, not the cryostat |

Each gets a verdict: **typical / atypical / no opinion**, with the reason in
words. "No opinion" is the important one — see 2.4.

### 2.4 Where the model has no opinion

The model has data over 4.7–195 K, from a cryostat with the cooler running and
shields cold. Outside that it must say so, not cry anomaly:

- **T outside 4.7–195 K** — the table clamps there by design.
- **Output below ~28 %** — the heater has no authority against the cooler, so
  K and τ are undefined, not small.
- **Below ~25 K, `τ` ratio** — τ is seconds against a 2 s cadence; the steady
  state is real and the dynamics unmeasurable. `δQ` still applies.
- **Cooler off, or a cooldown in progress** — the coldplate residual will say
  so first; the monitor should downgrade `δQ` to no-opinion while `δT_c` is
  atypical, rather than report two alarms for one cause.
- **Inside a mask window** or within a settle time of a heater move — the
  first 3τ(T) after any change in `u` is transient by design.

---

## 3. Phase 0 — bookkeeping, this week

No code in `control/`. Each item is small and each is a trap left open.

1. **Mask the 09-10 fault window.** The sample has been flat at 118.34 K on
   64.010 % since 16:00 on 09-10. Archive the stretch and give 11:33 → the
   repair one `mask` row with a paragraph, as `mask-20260909-180604` has.
   REFIT_PLAN §0.4: do not archive a half-event.
2. **Put the repair on the record.** Nothing in the log says what changed at
   ~15:00 on 09-10 except the readback moving 64.016 → 64.010 and the sample
   recovering 3 K in an hour. Jeff knows; the manifest does not. Add a row the
   way `era` records the recalibration — REFIT_PLAN T10 asks for exactly this
   — and it becomes the boundary at which `δP` may be revisited.
3. **A `note` command kind** through the spool, writing into the CSV's empty
   `Notes` column. It is `lschart`, generic, passes only `accept_commands` and
   the source policy, moves nothing. Both September events are unattributable
   on the day for want of it. MATLAB gets `note()` in `LakeShore.m`.
4. **Two stale comments.** `config.yaml` names `lschart.tools.steptest`; the
   tool is `ltspm3.tools.steptest`. `config-ltspm3-heater.yaml`'s dated
   cryostat block still reads 2026-08-31 (AUDIT-2026-09-09 finding 7).

**Exit gate:** `curate.py --propose` clean with the new rows; `send note "..."`
lands in the CSV; the two files corrected.

---

## 4. Phase 1 — finish the refit, and export the band with it

This is [REFIT_PLAN.md](REFIT_PLAN.md) §7 steps 7–10, unchanged, and its gate
is unchanged: **leave-one-epoch-out predicts the three post-recal holds inside
0.5 K having never seen them, or nothing proceeds.** The two open questions
named in `HANDOFF.md` are settled *before* regenerating — the below-10 K basin
(`T_lo` sits where there is no data) and `δP` carried as a power-side bar.

What this plan **adds** to step 10:

- `analysis/export_response.py` writes, beside `TABLE`, the constants of §2.2:
  `DELTA_P_FRAC`, `DRIFT_W_PER_DAY` and its spread, `DRIFT_T0` (the date the
  drift is zero at), `SIGMA_TINF_K`, `DIURNAL_K`, `TC_RMS_K`, and the `T_c`
  locus with its `TAU_BATH_S`. Generated, never hand-edited, in the cache key.
- `ltspm3/fitted_response.py` gains `missing_power_w(T_s, dT_dt, T_c, u, t)`
  and `sigma_q_w(T_s, u, t)` — the two functions §2.1 and §2.2 define, stdlib
  only, with the drift evaluated at wall-clock `t`.
- `analysis/pid_tuning.py` migrated onto `production_inputs()` (it fits its
  own unweighted 9/4 model today) and printing rows in the form §5.1 needs.

**Exit gate:** REFIT_PLAN §1's three rows green; `SUPERSEDED_NOTE` cleared;
`test_fitted_response.py`'s eight pins **regenerated, not loosened** (T8);
`missing_power_w` returns under 1 mW at every settled anchor the fit was given.

---

## 5. Phase 2 — the plant monitor

`ltspm3/tools/monitor.py`. A **separate process**, like the viewer: it holds no
port, reads the recorder's CSV tail and `status.json`, and writes
`plant.json` beside `status.json` — arrays not objects, `SCHEMA_VERSION`, the
same `os.replace` discipline — plus a `plant_YYYY-MM-DD.csv` of every residual
per cycle so the verdicts can be audited a month later.

### 5.1 What it computes, per recorder row

`δQ`, `δT_c`, the noise rms over a trailing settled window, and — when `u`
changes and then holds — a `fit_pole` on the response against `tau_s(T)`. Each
with its band from §2.2, each with a verdict from §2.3, each with the reason.
A rolling verdict needs persistence: a single cycle out of band is noise, and
`atypical_after_s` (config) is how long before it is reported, exactly as
`fault_after_s` works in the guard.

It reads `control` from `status.json` too. When the loop is armed the monitor
is the second opinion: the supervisor holds on `max_error_k` in kelvin, the
monitor reports `δQ` in watts, and they should agree about *when* and disagree
about *why* only in the ways §2.3 predicts.

### 5.2 What it may do

Report, always. **Act, only if configured to**: `monitor.on_atypical: hold`
sends `hold` through the spool, which passes exactly the gates a typed `hold`
passes and reaches `panic_hold()` by the seam that already exists. Default
`report`. A monitor that can freeze the heater on a model's say-so is a client
like any other and gets no exemption — the panic kinds already bypass the
power gates by *kind*, which is what makes this safe to allow.

### 5.3 The test on genuine data

`python -m ltspm3.tools.monitor --replay reference/cooldown-10/` runs the whole
archive and prints every verdict change with its time. The acceptance test,
pinned in `tests_ltspm3/`:

| window | must |
|---|---|
| 2026-09-09 18:06 | `δT_c` atypical within 30 min; `δQ` no-opinion or typical (the cause was upstream) |
| 2026-09-10 11:33 | `δQ` atypical within 30 min, magnitude −5 ± 2 mW; `δT_c` typical |
| the three post-recal holds | typical throughout, `δQ` within ±1 mW of the drift line |
| `trace-ladder-20260905`, 30 rungs | no atypical verdict; 27 of 30 rungs return a τ ratio, all 0.85–1.10 above 40 K |
| `trace-sweep-20260902`, 43 h | no atypical verdict outside its masked windows |
| the 09-04 12:07 recalibration boundary | no verdict change — the remap is exact, and if it is not, this is where it shows |

**False-alarm budget: under one atypical verdict per week on a settled hold.**
Every verdict change in the replay gets read by a person once, the way the
manifest was; that is the review pause of this phase.

**Exit gate:** the table above green in `pytest`; the monitor running beside
the live recorder for 72 h with its `plant.json` open in `status` / MATLAB
(`LakeShore.m` gains `plant()`); the residual after the 09-10 repair sitting
inside the band, which is the first evidence the repair holds.

---

## 6. Phase 3 — the loop's numbers, from the model, through config

Nothing here edits `control/`. Both tables the controller runs on are already
config fields.

### 6.1 The schedule

`TuningConfig.schedule` replaces `PROVISIONAL_SCHEDULE` — four rows, three at
τ = 620 s — with rows from `analysis/pid_tuning.py` every ~10 K over 30–190 K.
Measured τ runs 36 s at 40 K, 246 s at 70 K, 489 s at 110 K; below about
100 K the shipped rows are wrong by up to an order of magnitude in both Ti and
Kp.

`pid_tuning.py` already knows the loop is not the plant: the 60 s measurement
filter and ~3 s of dead time are comparable to τ below 60 K, and it applies
SIMC's half rule. The shipped `tuning.py` is IMC on the plant alone,
`Kp = τ/(K·τ_cl)`, `Ti = τ`. **The two are made identical by what goes in
the rows, with no code change:** put `τ_eff = τ + 30 s` in each row's `tau_s`
and add `θ = 33 s` to both `hold_tau_cl_s` and `move_tau_cl_s`. Then IMC's
`Kp` is SIMC's `Kc` exactly, and IMC's `Ti = τ_eff` equals SIMC's
`min(τ_eff, 4(τ_c+θ))` everywhere in range, because `4(τ_c+θ)` is 1,332 s
even in MOVE and no `τ_eff` here exceeds 650 s. `pid_tuning.py` prints the
rows in that form and says so in the note field. `min_ti_s: 60` will clamp
below ~30 K, where `τ_eff` is ~31 s — that is the filter being the plant, and
it is correct.

### 6.2 Feedforward

`FeedforwardConfig.calibration` takes a `(pct, kelvin)` table. Export one from
the fitted steady state **at today's date** (the drift moves it 0.28 mW/day,
which is ~0.2 K/day at 114 K), at every 0.5 % from 28 % to 70 %. The CD10
ten-knot curve it replaces has no point between 43 % and 63 % and was 17 K
wrong in the middle. Re-export whenever the schedule is; `plan_sweep --as-of`
already has the date plumbing.

### 6.3 `response_lag_s`

620 s everywhere over-allows ramp error at low T, which is benign but makes
the anomaly premise check meaningless down there. Set it to `tau_s(T)` at the
intended operating point in each armed config, and say in the comment which
point. A scheduled version is a `control/` change and is **not** done here.

### 6.4 Bench on the fitted plant

The control harness (`tests_ltspm3/conftest.py`) runs on `sim_response` — one
τ, 620 s. Add a second fixture on `FittedResponse` and run the existing hold /
sweep / glitch / fault scenarios at **30, 60, 100, 140 and 180 K** with the §6.1
schedule and §6.2 table loaded from a real config file, not constructed in
the test. The point is not that they pass at 137 K, where the two models
agree; it is 30 K and 60 K, where the loop is filter-limited and the old rows
were an order of magnitude off.

**Exit gate:** at all five temperatures in simulation — no overshoot above
5 % of a 3 K move, a 0.5 K/min sweep inside `max_error_k` + allowance with no
anomaly hold at either end, a glitch that freezes and recovers, a fault that
ramps down and latches. `python -m ltspm3 -c config-ltspm3-armed.yaml check`
prints a band bracketing the present output. The monitor's replay (§5.3) is
still green with the new table.

---

## 7. Phase 4 — onto the cryostat

This is [commissioning.md](docs/ltspm3/commissioning.md), which is not
restated. What changes is that **the monitor runs alongside from stage 3 on**
and its `plant.json` is part of every gate.

### 7.1 Stage 3 close-out

| | | gate |
|---|---|---|
| **W1** | twenty distinct writes, `AOUT?` at 0/25/50/80/150/300 ms, `write_settle_s` set with margin and the evidence in `HANDOFF.md` | the 09-05 sweep verified 30 writes by readback, which says the retry loop copes and says nothing about freshness. Invariant 5 |
| **W2** | `send analog 70.5` refused; `analog 0` refused with `allow_analog_output: false`; `heaters_off` reaches the box | zero refusals in the whole command history |
| **circuit** | the repair recorded (§3 item 2); `δQ` inside its band for 72 h at fixed output | the loop's premise checks would have ramped the heater to zero on the 09-10 event, correctly. It cannot hold unattended until the circuit is trusted |

### 7.2 Stage 4, attended

4a → 4d as written, with two additions. Before arming, `plant.json` reads
typical on all four residuals for the preceding hour — arm into a cryostat the
model recognises. At every gate, the monitor's verdict is recorded beside the
supervisor's state; where they disagree, that is a finding, not noise.

The armed config is a **third file**, `config-ltspm3-armed.yaml`, so that
neither the read-only nor the heater config ever grows a `control:` section by
accident. `operating_point_pct` re-centred on the output that holds the chosen
temperature today (64.010 % holds 118.3 K); `authority_pct` narrowed as
commissioning 0.3 says; the §6 tables loaded.

### 7.3 Stage 5

A week unattended. Ends with the C6 stability figure and a monitor log with
every atypical verdict explained. Any unexplained one drops back a stage.

---

## 8. Phase 5 — fold the residual into the supervisor, or do not

The supervisor's `_check_model` compares the filtered temperature with
`feedforward.kelvin_for(u)` against `model_trust_k` of 15 K. That is the
right idea in the wrong units and on the wrong curve: 15 K is three times the
09-10 event at 114 K and forty times it at 30 K. Replacing it with `δQ`
against `σ_Q`, and taking the expected value from the fitted steady state, is
about twenty lines in `control/supervisor.py` and a `model_trust_mw` field in
`SupervisorConfig`.

**It is the only `control/` change in this plan, and it is not made without
Jeff's go.** The case against making it at all: the monitor already does this
outside the loop, can send `hold` through the spool, and works when the loop
is not armed — which is most of the cryostat's life so far. The case for: the
supervisor's own status would carry the number the viewer and MATLAB already
read, with no second process to keep alive.

---

## 9. Traps

**P1 · A band in kelvin is wrong at one end.** Gain spans 40×; every threshold
the monitor applies is in watts at the sample node, converted to kelvin only
for display.

**P2 · The circuit fault and a missing load path look identical in `δQ`.**
REFIT_PLAN §2.5 and T10. `δT_c` is what separates "something at the sample"
from "something at the cold head"; report both, and let the person decide.

**P3 · "Typical" drifts.** `D(t)` moves 0.281 mW/day, so a band that is right
today is 8 mW off in a month if the date is not in it. Every export carries
`DRIFT_T0`; the monitor evaluates at wall-clock time; a config that pins a
schedule pins the date it was made.

**P4 · No opinion is not typical.** Outside 4.7–195 K, below 28 %, during the
first 3τ after a move, with the cooler off: the monitor says it cannot judge.
A green light there is a lie.

**P5 · `T_c` is an input, not a state.** `fit_ode` reads the coldplate from the
log and that is exact for fitting. For prediction the monitor has the
measured `T_c` too, so `δQ` uses it directly; only `δT_c` needs `bath.py`'s
locus, and only to judge the coldplate itself.

**P6 · The monitor inherits the naive-timestamp fold.** It reads the recorder's
CSV, so on 2026-11-01 02:00 its clock runs backwards for an hour
(AUDIT-2026-09-10 finding 4). Take `t` from the recorder's monotonic `Time`
column, as `fit_table` still needs to; do it in the monitor from day one.

**P7 · The schedule is a paste, and pastes rot.** Rows in `TuningConfig` do not
know which fit made them. `pid_tuning.py` prints the fit's cache key and date
into each row's `note`; a test compares the config's rows to a fresh export
and fails when they diverge by more than the fit's own error.

**P8 · A flat hold is not evidence the circuit is fine.** 64.016 % held flat
for 25 hours before 11:33 on 09-10. Only `δQ` sitting on the drift line for
days at a *changing* `u` says the watts arrive; §7.1's 72 h at fixed output is
the weakest test that is still worth running, not a proof.

**P9 · The filter is the plant below 60 K.** With `filter.tau: 60` the loop's
dominant lag at 30 K is the instrument's. §6.1's rows encode that; do not
"fix" a slow loop down there by shortening `tau_cl` — shorten `filter.tau` if
anything, and only after C1's low-temperature noise check says the floor is
what the model claims.

**P10 · Phases 0–4 do not touch `control/`.** If a step seems to need to, it is
either config (invariant 7 says every limit already lives there), a tool, or
Phase 5 arriving early. Stop and say which.

---

## 10. Decisions for Jeff

1. **May the monitor send `hold`?** §5.2 defaults to report-only. Enabling it
   is one config line; the question is whether a model verdict should ever
   freeze the heater without a person.
2. **Phase 5 at all?** §8. The alternative is to leave `_check_model` as it
   is and let the monitor own the judgement permanently.
3. **SIMC through the schedule rows** (§6.1, no code change) or a `tuning.py`
   that knows about the filter (cleaner, a `control/` change). The plan takes
   the first.
4. **`MAX_END_RATE_K_PER_H`** — commissioning still lists this as open: four
   good warm rungs thrown out at 0.51–0.71 K/h. It changes what the archive
   keeps, so it is a manifest diff to read, not a tidy-up.

---

## 11. Out of scope

The viewer, the MATLAB interface and Windows deployment remain `CLAUDE.md`'s
standing priority and are not displaced by this plan; §3's `note` command
and §5's `plant()` in `LakeShore.m` are the only places it touches them, and
both are additive. Fixing the heater circuit is hardware and Jeff's. Nothing
in phases 0–3 arms a heater or moves an output.
