# Software PID — the plan

**Status: PHASE 0 DONE. PHASE 1 DONE 2026-09-14 — the model is finished and it
carries its own error band.** REFIT_PLAN.md Phase B closed on 09-13 (§1's three
rows green, the table 4-5 K warmer at a given output than every number written
before it, the level on a dated delivered-power gauge). §1.2 closed on 09-14:
the band constants are in `_fitted_table.py`, `missing_power_w` and `sigma_q_w`
are in `fitted_response.py`, and the two of them are what §3 below is now
written against.

**PHASE 2 IS BUILT, 2026-09-14.** `ltspm3/monitor/` — report only, no port, no
commands. The 2026-09-10 fault warns eleven minutes after it happened at
−5.01 mW. **What is left of phase 2 is the 72 h live soak**, which needs the
recorder restarted with the monitor beside it and is Jeff's to schedule.
**PHASE 3 — the loop — is the next code.** Written
2026-09-11 from Jeff's requirements (§1); revised the same day through four
rounds of questions. Update this line as phases land.

**Goal.** A software PID that holds and sweeps the LTSPM3 sample from 4 to
300 K, fails gracefully, and judges from the thermal characterisation whether
the cryostat is behaving typically — warning on the atypical, faulting only on
the dangerous. One program, two halves: `lschart` for the person watching,
`ltspm3` for the loop. `ltspm3/control/` is open to change under the eight
rules of [safety.md](docs/ltspm3/safety.md), one rule-scoped commit at a time.

| phase | document | one-line goal | exit gate |
|---|---|---|---|
| **0** | §5 here | the record is straight | **DONE 2026-09-12** — `curate --propose` clean, `send note` proved on the live recorder, four documents corrected |
| **1** | [plans/pid-1-model.md](plans/pid-1-model.md) | a model that is right from 4 to 300 K, with its error band exported | **DONE 2026-09-14** — REFIT §1 green; in-epoch prediction 0.135 K rms; `missing_power_w` 0.58 mW worst in-epoch over 40 K; `sigma_q_w` exported. The 300 K half is a PIPELINE, run as a dry run; the ladder itself is stage 6 |
| **2** | [plans/pid-2-monitor.md](plans/pid-2-monitor.md) | a judge outside the loop that catches both archive events and nothing else | **BUILT 2026-09-14** — 09-10 warns in 11 min at −5.01 mW; < 1 warning/week met; three rows argued in §2.4 rather than met. **72 h live outstanding** |
| **3** | [plans/pid-3-loop.md](plans/pid-3-loop.md) | the loop rebuilt on the model: one rate, two ratios, watts | bench green at 6 temperatures; 8 rate fields → 2; hold jitter ≤ 0.02 %/min |
| **4** | [plans/pid-4-commissioning.md](plans/pid-4-commissioning.md) | armed on the cryostat, then unattended, then to 300 K | 7 days unattended, hold criterion met, every warning explained; ladder graded to 300 K |
| **5** | §6 here | warnings and faults in the viewer | verdict row visible, contrast-tested |

---

## 1. Requirements — Jeff, 2026-09-11

These supersede `config.yaml`, `SupervisorConfig` defaults and three documents
until those are corrected.

| | requirement | today | resolution |
|---|---|---|---|
| range | **4–300 K** | measured 4.7–180.6 K; ceiling 70 % ≈ 192 K | ladder upward to 300 K, ceiling raised one measured rung at a time to 100 % (1.63 W; heater rated 1.68 W, wiring fine) |
| hold | slow wander **below the 10 s noise floor** | open loop at 118 K: Allan **8.7 mK @ 10 s, floor 7.4 mK @ 130 s, 12.7 mK @ 1 h, 24.5 mK @ 6.6 h** — measured, `analysis/allan.py`, and **2.9× outside the criterion** | `σ_y(τ) ≤ σ_y(10 s)` for all `τ` in [10 s, run/4] |
| sweep | **5 K/min** | default 0.5 | one rate, converted to heater %/min through the model's gain; ramp lag closed by velocity feedforward |
| rate limits | **fewer** | eight | **two**: `max_rate_k_per_min: 5`, `min_rate_pct_per_min: 0.20` (floor) |
| ramp-down | at the same **5 K/min** | 1 / 2 %/min with a knee | open loop through the model's inverse curve, so it needs no sensor |
| premise | **warn at 1 K, fault at 5 K**; for the PID **in watts, from the fit** | hold at 1 K, ramp down after 180 s | **settled 2026-09-14: `warn_mw: 5`, `fault_mw: 10`** — 3.0 K and 6.0 K at 118 K, as floors under the 3σ band. "The 09-10 event is a warning" is now in tension with 10 mW: it peaks at 14.11 mW. §4 |
| faults | a lost sensor, a runaway heater, a strange transient. **Not** a rising coldplate | — | `δT_c` warns only; **authority exhausted** (railed + error > 5 K) is the fault a compressor failure eventually causes |
| unattended | a weekend; indefinitely in principle | never armed | 7 days is the gate, not the design life |
| failure | **graceful** — a crashed PID disengages | poller keeps logging, loop does not disengage | `panic_hold()` on exception, state `crashed`, `ack` + `arm` to resume |
| filter | **no low-pass**; **median-3**; ~one cycle of dead time | median-5 → 60 s exponential | `tau: 0` = pass-through (class kept), median-3; delay derived from the filter config, ≈ 3 s |
| loop speed | takes input from **the system's τ** | fixed 1800 s / 300 s | `hold_speed: 3`, `move_speed: 0.5`, ratios to τ(T), floored at 4 × delay |
| monitor | **report only**; acting version **only when armed** | none | the monitor never commands; the supervisor acts, on the same two functions |
| viewer | warnings and faults, **late** | — | Phase 5 |
| naming | "frozen" for output-frozen-pending-clarity | `HOLDING`, colliding with the `hold` phase | `SupervisorState.FROZEN`, schema bump |

---

## 2. The software model

```
lschart/          GENERIC.  Recorder, viewer, file interface.  Never imports ltspm3.
ltspm3/model/     THE CHARACTERISATION.  Every number describing the cryostat, the
                  typical band, the two residual functions.  Written by analysis/.
ltspm3/control/   THE SOFTWARE PID.  Reads model/.  Eight rules.
ltspm3/monitor/   THE JUDGE.  Separate process, no port, reads model/ and the
                  recorder's files, writes plant.json.  Report only.  Alarms on
                  the CHANGE in the residual against a slow baseline kept as a
                  FRACTION OF DELIVERED POWER, not on its level -- so one
                  calibration lasts a cooldown.  BUILT 2026-09-14.
analysis/         GENERATES model/_fitted_table.py.  Imports neither package.
```

`control/` and `monitor/` both read `model/`; neither reads the other. They
call the **same two functions** for the residual and its band, so they cannot
disagree about what typical means — only about what to do.

### 2.1 States, in the operator's words

| operator's word | `mode` | `state` | `phase` | ramp |
|---|---|---|---|---|
| holding steady | `pid` | `tracking` | `hold` | none |
| moving to a setpoint | `pid` | `tracking` | `move` | running at the one rate |
| steady ramp | `pid` | `tracking` | `move` | running at a commanded rate |
| — | `pid` | **`frozen`** | any | output frozen pending clarity (suspect reading, warning) |
| — | `pid` | `ramping_down` / `locked_out` | — | fault response / needs `ack` |
| — | `pid` | **`crashed`** | — | exception in the loop; output held; `ack` + `arm` |
| disengaged | `off` / `manual` | `idle` | — | hold, panic, or never armed |

The guard keeps its own sensor state: `unknown / ok / suspect / fault /
recovering`. **The phase decides the gains and which premise check applies:**
kelvin thresholds in `hold`; in `move` the error is the ramp's lag by design
and only the watt residual judges.

---

## 3. What "typical" means

**In watts at the sample node**, because every measured disturbance is a
power — drift +0.281 mW/day, the 09-10 fault −4.9 mW — while the gain runs
0.35 to 13.3 K/% across the band. The residual

```
δQ = C(T_s)·dT_s/dt + [Λ(T_s) − Λ(T_c)] − P(u)            [W]
```

is valid at a hold **and during a sweep**, which a kelvin check is not.
**Negative means power is missing**, which is the direction of every fault
measured here. `ltspm3/model/fitted_response.missing_power_w`, DONE 2026-09-14.

**There is no `D(t)` term, and its absence is a measurement.** This line
carried a fitted campaign drift until §7.3 of [REFIT_PLAN.md](REFIT_PLAN.md)
took the drift back out of the shipped fit: inside one undisturbed epoch there
is no drift to find, and what reads as a rate across the campaign is a
staircase of somebody handling the heater wiring. Nothing predicts it — so the
band carries **all** of it rather than a quarter of it.

**Band** `σ_Q`, exported with the table and measured by `analysis/band.py`
(`sigma_q_w`, DONE 2026-09-14). Six terms in quadrature, every one measured:

| term | | at 118 K, 1σ |
|---|---|---|
| `TC_RMS_K × Λ′(T_c)` | the coldplate is not a bath — `bath.py`'s residual | **0.42 mW** |
| `SIGMA_MODEL_K × Λ′` | where the curve sits, in-epoch: §1's own row 2 | 0.22 mW |
| `SIGMA_TINF_K × Λ′` · `DIURNAL_K × Λ′` | the median anchor bar, the building's day | 0.03 mW |
| `DRIFT_W_PER_DAY × days × P/DRIFT_REF_W` | the campaign, at its **full** rate | 0.29 mW/day |
| `SIGMA_C_FRAC × C × |dT_s/dt|` | 3.0 %, from fitted τ against measured τ | 0 at a hold, 2.2 mW at 5 K/min |

3σ settled is **1.4 mW at 118 K**, 8.8 mW ten days later, 6.7 mW sweeping at
5 K/min — so the band widens during a sweep, which §3 required, and it grows
with the days, which the "typical drifts" trap required. In kelvin at the local
gain it is **0.4 to 0.9 K across 10–180 K**: the band is about a kelvin
everywhere, which is where Jeff's "warn at a kelvin" lands when it is arrived
at from measured terms rather than chosen.

**The 0.7 % `δP` is NOT in the band. It is `bias_q_w`, on its own.** It is a
constant over hours and days — it changes when somebody handles the heater
wiring — and in the band it would make 3σ at 118 K **14 mW**, three times the
09-10 event phase 2 has to catch. The three consumers that feel it are the
monitor's absolute residual (whose answer is a trailing baseline, not a wider
alarm), the velocity feedforward and the open-loop ramp-down. **The closed loop
never sees it**, because integral action absorbs a constant power offset
exactly — which is why measuring the heater circuit four-wire is not on the
critical path (Jeff, 2026-09-14: out of scope, size the margins instead).

At day ~9 the band overtakes the 8 mW `fault_mw` seed. Phase 2's replay is what
sets that number, and it is the argument for re-gauging on a cadence rather
than for widening anything.

**What the fit must get right, and how right.** Steady-state Λ(T) is what
the residual needs: 0.3 K at 118 K is 0.5 mW, and the pre-refit table's
4.3 K miss is 7 mW — a permanent false residual the size of a fault. C(T)
enters only through the dynamic term: 5 %. The controller itself is
forgiving — K within 20 %, τ within 30 % — and below 30 K τ does not enter
at all because the loop's delay floor binds. The low-temperature
short-timescale misfit therefore matters nowhere: τ is unmeasurable there,
unused there, and C is millijoules per kelvin.

| residual | typical | warn | fault |
|---|---|---|---|
| `δQ` missing power | < 3 σ_Q | ≥ 3 σ_Q for `warn_after_s` | ≥ `fault_mw` (seed 8 mW; set by replay) for `fault_after_s` |
| `δT_c` coldplate vs locus, 175 s pole | < 28 mK rms | beyond band | **never** |
| τ ratio on a step | 0.85–1.10 | outside | never |
| noise rms vs `1.36e-6·T²` | < 2× | outside | never |
| tracking error, `hold` phase | < 1 K | ≥ 1 K | ≥ 5 K **and railed** at the band (authority exhausted) |
| sensor | guard `ok` | `suspect` | guard `fault` |

Kelvin equivalents at Λ′: 1 K ≡ 1.7 mW and 5 K ≡ 8.3 mW at 118 K; 19 and 96 mW
at 20 K. **No opinion**, not "typical", outside the table, below 28 % output,
within 3τ of a heater move, or while `δT_c` is atypical.

**Hold figure of merit**: `σ_y(τ) ≤ σ_y(10 s)` for `τ` ∈ [10 s, L/4] over any
settled closed-loop run of length `L`. `analysis/allan.py` grades archive and
live runs alike.

---

## 4. Decisions

**Settled 2026-09-11.** Report-only monitor. `control/` open. Heater 1.68 W,
wiring fine. Rising coldplate never faults; authority exhausted does. One
rate. Quiet hold tested, not limited. `ltspm3/model/` done. `FROZEN`.
Median-3, low-pass off (`tau: 0`, class kept). Speed ratios 3 / 0.5. The
end-rate grading question was moot. The 09-10 mask goes in with the next
archive export, not before.

**Settled 2026-09-14.** **Recalibrate at most once per cooldown**, and typical
erroring behaviour is so large as to be unmistakable from ordinary variation
(Jeff). Measuring the heater circuit four-wire is **out of scope**; the margins
carry it instead. Both rulings are load-bearing: the first is why the monitor
judges a *change* against a baseline rather than a level, and the second is why
`DELTA_P_FRAC` is `bias_q_w` and not a band term.

**Thresholds, 2026-09-14 (Jeff).** **`warn_mw: 5`, `fault_mw: 10`** — 3.0 K and
6.0 K at 118 K, which is where the row above lands once the band under it is
measured. Both are **floors under the 3σ band, not replacements**: at a settled
118 K the floor binds (band 1.4 mW) and on a 5 K/min sweep at 180 K the band
binds (8.8 mW), and a flat 5 mW there would warn about every sweep.
`warn_after_s: 600` is the replay's, and is what puts the 09-10 event at
fourteen minutes rather than at two.

**Open, and it is one decision.** The 09-10 excursion **peaks at 14.11 mW**, so
under `fault_mw: 10` it *is* fault-level at about +190 min — and the row above
says **"the 09-10 event is a warning"**. All three of that sentence, 10 mW and
14.11 mW cannot hold. The monitor only reports, so nothing is broken today; but
**phase 3 §3.4 turns `fault_mw` into a ramp-down**, and under 10 mW the
supervisor would have ramped that event down rather than warned about it. Either
the fault level rises to about 15 mW or the sentence changes.
plans/pid-2-monitor.md §2.5 has the numbers.

---

## 5. Phase 0 — the record

**Done 2026-09-12**, except the one step that needs the recorder restarted.
Two of the four resolved differently from how this section imagined them.

1. ~~**Next archive export** past 09-10.~~ **DONE.** `cd10_20260904_recorder`
   now runs to 2026-09-11 23:59:59, +86,400 rows, and the extension was checked
   **additive** first — re-running the original glob reproduced the committed
   file byte for byte. `mask-20260910-113000` carries the paragraph, and the
   post-reseat hold **`pc-20260910-144849`** is in as an anchor: 33.19 h at
   64.010 % on 118.33 K, graded `tau`. Still 136 usable anchors, and 38
   believable τ where there were 37; the production fit moves 0.4 %. The
   window's `Q` is wrong, not noisy, and it is still the monitor's test case.
2. ~~**The repair as a manifest row**, as `era` records the recalibration.~~
   **DONE, but not as an `era`.** `segments.ERAS` is keyed by archive *file*,
   one era per table, and a reseat is mid-file; forcing a fourth era would mean
   splitting the table for an event that moves no calibration. It is in the
   mask's note, which is the human's channel. And it was a **reseat, not a
   repair** — Jeff, 2026-09-12: the repair is changing the op-amp driver to a
   robust, correct differential design. So `DELTA_P_FRAC` stays a systematic on
   the whole campaign rather than a bounded episode, and there are now two
   independent signatures of the difference: a **0.30 K steady-state deficit at
   matched output**, and **twice the wander over 300–1200 s** with the coldplate
   unchanged.
3. ~~**`note` command kind.**~~ **DONE** — in `lschart`, not `ltspm3`, because a
   person writing down what they did is not specific to one cryostat. CLI,
   MATLAB `note()`, ten tests. It lands on the **next** row and that is pinned
   by a test; no power gate, but `accept_commands` and the source policy apply,
   and it is deliberately **not** a panic kind.
4. ~~**Correct** four documents.~~ **DONE**, and the fourth was not a stale
   sentence but a missing measurement: commissioning's stability figures were a
   1/√N *prediction* with no drift term in it. `analysis/allan.py` measures it
   instead — **averaging stops helping at about two minutes**, floor 7.38 mK at
   τ = 130 s, rising to 24.5 mK at 6.6 h, and the prediction was optimistic by
   nearly 4× at 600 s.

**Exit gate: MET, 2026-09-12.** `curate.py --propose` clean ✔; the four
documents corrected ✔; `send note "x"` in the CSV ✔ — proved on the live
recorder after Jeff restarted it, row 30210 of
`data/ltspm3-heater_2026-09-12.csv` at 16:47:12, sample 118.33 K, output
64.0100 %:

```
[lschart-cli] connector reseated 2026-09-10 14:39-14:41; reseat not repair,
op-amp driver still to change
```

**The first note in 30,214 rows**, which is the gap this item existed to close.
The row before it is blank, so the documented "lands on the next row" behaviour
is what the cryostat actually did. `lschart/tools/fit_table.py` carries the
column into the archive's `note`, so a note written today is in the dataset the
next time the archive is exported.

One thing this turned up and it is worth keeping: a restart is not enough on its
own. The first restart changed nothing because the **main checkout had never
pulled** — `origin/main` was six commits ahead of the working tree the recorder
runs from, so there was no handler on disk to load. The refusal said so plainly
(`unknown command 'note'`, with the old eleven-command list), which is the
command spool earning its keep.

## 6. Phase 5 — the viewer

A verdict row per residual in the loop table's style, coloured by
`theme.py`'s exceptional pairs, the reason in the hover; MATLAB `plant()`.
Reads `plant.json` — a file, not an import. **Exit gate:** visible on a live
recorder, contrast test green.

---

## 7. Traps

- **Kelvin thresholds are wrong at one end.** Gain spans 40×. Watts.
- **A compressor failure is a coldplate event.** `δQ` stays small because the
  sample follows the physics; `δT_c` warns; authority exhausted faults.
- **"Typical" drifts** 0.28 mW/day. Every export carries `DRIFT_T0`.
- **No opinion is not typical.** A green light outside the table is a lie.
- **A percent rate limit is a function of temperature.** 5 K/min is 0.38 %/min
  at 118 K and 14 %/min at 10 K.
- **The ramp-down must not need the sensor.** Rule 3: the fault may be the
  sensor. Hence the inverse curve, open loop.
- **Nothing above 180.6 K is measured.** The 300 K numbers are extrapolations
  until the stage-6 ladder replaces them.
- **Above 195 K the thermometer is the floor** (109 mK at 290 K). The hold
  criterion is relative for this reason.
- **Pastes rot.** Schedule rows carry the fit's cache key; a test diffs them
  against a fresh export.
- **A flat hold is weak evidence the circuit is fine.** Only `δQ` on the drift
  line across *changing* `u` says the watts arrive.
