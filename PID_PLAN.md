# Software PID — the plan

**Phase status is the table below; each row carries its exit gate.** What is
armed right now, what ran today and what is next live in
[HANDOFF.md](HANDOFF.md) and nowhere else. The requirements this plan is
graded against, and the bench results that graded them, are
[docs/ltspm3/requirements.md](docs/ltspm3/requirements.md) — in Jeff's words,
and only he changes them.

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
| **2** | [plans/pid-2-monitor.md](plans/pid-2-monitor.md) | a judge outside the loop that catches both archive events and nothing else | **BUILT 2026-09-14** — 09-10 warns in 14 min at −5.08 mW and never faults; < 1 warning/week met; two rows argued in §2.4 rather than met. **Live soak run 2026-09-15; the gate is one diurnal cycle, not 72 h** |
| **3** | [plans/pid-3-loop.md](plans/pid-3-loop.md) | the loop rebuilt on the model: one rate, two ratios, watts, **a band that follows the setpoint** | **BUILT 2026-09-14** — 8 scenarios × 6 temperatures green, 8 rate fields → 2, hold at 2 DAC codes/min. **Review fixes all ten landed 2026-09-15** ([plans/pid-3-review.md](plans/pid-3-review.md)): the bench no longer depends on the date, `CRASHED` latches, the descent is bounded and cannot be finished by a failed read, and **both kelvin rows are reachable** — a heater delivering half its power at 30 K now faults instead of holding 14 K low in silence. §3.6's soak outstanding |
| **4** | [plans/pid-4-commissioning.md](plans/pid-4-commissioning.md) | armed on the cryostat, then unattended, then to 300 K | **4a met 2026-09-16; 4e's bench gate met 2026-09-17** (`test_stage_4e_fast_move.py`), cryostat pending; then 7 days unattended, hold graded against the 09-15 open-loop night; ladder graded to 300 K |
| **5** | §6 here | warnings and faults in the viewer | **BUILT** — verdict rows, the loop's detail panel and a software setpoint control; contrast test green. Live check and MATLAB `plant()` outstanding |

---

## 1. Requirements

**[docs/ltspm3/requirements.md](docs/ltspm3/requirements.md) is the
requirements document**, in Jeff's words, dated 2026-09-17. It supersedes the
table that stood here, which had been rewritten by measurement until it said
the opposite of the goal: a 2 K move at 118 K arrives in **five minutes**; a
hold's noise is **no worse than open loop at 15 s to 5 min and much better at
long averaging**; the band **follows the setpoint**; 5 K/min is a **safety
ceiling**, not a target. The lines below are what carries over from
2026-09-11 unchanged.

| | requirement | resolution |
|---|---|---|
| range | **4–300 K** | measured 4.7–180.6 K; ladder upward to 300 K, ceiling raised one measured rung at a time |
| premise | **warn at 1 K, fault at 5 K**; for the PID **in watts, from the fit** | `warn_mw: 5`, `fault_mw: 10` as floors under the 3σ band; a fault is a **step within 30 min**. §4 |
| faults | a lost sensor, a runaway heater, a strange transient. **Not** a rising coldplate | `δT_c` warns only; **authority exhausted** faults |
| unattended | a weekend; indefinitely in principle | 7 days is the gate, not the design life |
| failure | **graceful** — a crashed PID disengages | `panic_hold()` on exception, state `crashed`, `ack` + `arm` to resume |
| filter | **no low-pass**; **median-3** (reconfirmed 2026-09-17) | `tau: 0`, median-3; delay derived, ≈ 3 s |
| monitor | **report only** | the monitor never commands |
| viewer | warnings and faults, **late** | Phase 5 |

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
recovering`. **The phase decides the gains and nothing else** (2026-09-15):
which premise check applies is decided by the TRAJECTORY — the kelvin
thresholds apply while the setpoint is not moving, and while it is moving the
error is the ramp's lag by design and only the watt residual judges. Gating
them on the phase made both unreachable, because an error over `move_error_k`
= 0.25 K puts the tuner in `move` by construction. → plans/pid-3-review.md

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

The band grows about 0.29 mW/day at 118 K with nothing subtracting it, so on a
cooldown that runs for months it stops being usable as a LEVEL. That is why the
monitor judges the residual against a slow baseline instead, and why
`sigma_q_fast_w` — the same band with the drift term removed — is what it
compares to. See plans/pid-2-monitor.md's opening.

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
| `δQ` against a slow baseline | inside the bar | beyond `max(3 σ_fast, warn_mw)` for `warn_after_s` | **a STEP of `fault_mw` inside `fault_window_s`** for `fault_after_s` |
| `δT_c` coldplate vs locus, 175 s pole | < 28 mK rms | beyond band | **never** |
| cold head, 1st/2nd stage step | inside its own scatter | beyond it | **never** |
| τ ratio on a step | 0.85–1.10 | outside | never |
| noise rms vs `1.36e-6·T²` | < 2× | outside | never |
| tracking error, `hold` phase | < 1 K | ≥ 1 K | ≥ 5 K **and railed** at the band (authority exhausted) |
| sensor | guard `ok` | `suspect` | guard `fault` |

`warn_mw: 5` and `fault_mw: 10` are Jeff's (2026-09-14) and are **floors under
the 3σ band, not replacements** — at a settled 118 K the floor binds and on a
5 K/min sweep at 180 K the band does. Kelvin equivalents at Λ′: 5 mW ≡ 3.0 K and
10 mW ≡ 6.0 K at 118 K; 0.26 and 0.52 K at 20 K.

**A fault is a step, not a level.** A change in delivered power puts
`−(1 − α)·P(u)` into the residual immediately; anything that takes hours to
reach a level did not step, and the fault a slow degradation eventually causes
is **authority exhausted**, which is the row below and is not gated by any
window.

**No opinion**, not "typical", outside the table, below 28 % output, within
`max(3τ, the judge's own slope window)` of a heater move, or while `δT_c` is
atypical.

**Hold figure of merit**: `σ_y(τ) ≤ σ_y(10 s)` for `τ` ∈ [10 s, L/4] over any
settled closed-loop run of length `L`. `analysis/allan.py` grades archive and
live runs alike.

---

## 4. Decisions

**Settled 2026-09-11.** Report-only monitor. `control/` open. Heater 1.68 W,
wiring fine. Rising coldplate never faults; authority exhausted does. One
rate. `ltspm3/model/` done. `FROZEN`. Median-3, low-pass off (`tau: 0`, class
kept). ~~Quiet hold tested, not limited.~~ ~~Speed ratios 3 / 0.5.~~ Both
superseded 2026-09-17 → [docs/ltspm3/requirements.md](docs/ltspm3/requirements.md).
The end-rate grading question was moot. The 09-10 mask goes in with the next
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

**A fault is a STEP, not a level** (Jeff, 2026-09-14: it should trigger fairly
quickly or not at all). `fault_window_s: 1800` — the residual must move
`fault_mw` *within* half an hour. This is not a tuning choice: a change in
delivered power puts `−(1 − a)·P(u)` into the residual **immediately**, and the
09-10 event reached its full −5 mW in **seven minutes** and then sat flat for
three hours. A residual that takes hours to reach a level did not step, and
faulting on it is a ramp-down, hours late, for something that was never sudden.
**Slow degradation has its own fault — authority exhausted — which no window
gates.** Across 57 days the fault level fires three times, all genuine steps.

**Settled 2026-09-14, on the revised plan 3 (Jeff).** **The authority band
follows the setpoint** — centred on `percent_for(setpoint)`, `authority_pct`
still the half-width, `hard_max_pct` still absolute and still the last word.
Rule 5 is reworded in that commit and nowhere else. It was believed to work
this way already; it never has, and at a fixed ±1 % around 63.076 % no sweep
below 60 K is arithmetically possible. **And the kelvin premise check stays on
in `move` below `min_output_pct`**, where `δQ` has no opinion and the gain is
small enough that kelvin is not the wrong variable — plans/pid-3-loop.md §3.0.F.

**Open.** Nothing that blocks phase 3.

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
   sentence but a missing measurement: the stability figures were a
   1/√N *prediction* with no drift term in it. `analysis/allan.py` measures it
   instead — **averaging stops helping after a couple of minutes**, where the
   prediction had it still improving. The measured ladder is in
   [plans/pid-4-commissioning.md](plans/pid-4-commissioning.md) C6.

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

**BUILT.** Three things, all reading files and importing nothing:

- **the verdict**, one row per residual with the reason in the hover, coloured
  by `theme.py`'s exceptional pairs. Reads `plant.json` — a file, not an
  import. Stale marks the header and not the rows, because the last verdict is
  still evidence and what is news is that it is history.
- **the loop's own account of itself**, which needed the status file to publish
  what the supervisor knows: the filtered reading the error is computed from,
  asked → allowed → written, and the watt residual with its band. The viewer
  shows these and judges none of them — every mark on that panel is a field the
  supervisor publishes.
- **a setpoint control**, because `send setpoint --software` was reachable from
  every client except the one that is open while somebody types temperatures.
  Its gate is the opposite of the manual output's, which is written down in
  [gui](../docs/recorder/gui.md) rather than left to be inferred.

**Still outstanding:** MATLAB `plant()`.

**Exit gate:** visible on a live recorder, contrast test green. The contrast
half is met — `tests/test_gui_theme.py` drives its check off the panels' own
output, so a severity that resolves to no colour fails the build rather than
silently stopping warning. The live half wants a recorder and a judge running.

---

## 7. Traps

- **Kelvin thresholds are wrong at one end.** Gain spans 40×. Watts.
- **A compressor failure is a coldplate event.** `δQ` stays small because the
  sample follows the physics; `δT_c` warns; authority exhausted faults.
- **"Typical" drifts** 0.28 mW/day. Every export carries `DRIFT_T0`.
- **No opinion is not typical.** A green light outside the table is a lie.
- **A percent rate limit is a function of temperature.** 5 K/min is 0.40 %/min
  at 118 K and 14.9 %/min at 10 K on the 2026-09-13 fit. **And the conversion
  asks whether a curve EXISTS, not whether the stage trusts it** — it asked
  `tuner.enabled` / `feedforward.enabled` until 2026-09-16, which made both the
  rate limiter and the fault ramp-down sit on `min_rate_pct_per_min` at 4a.
  **Which is why the TRACKING limiter no longer converts at all** (4e): how
  fast the heater may travel is about the heater, not the sample, and sharing
  one number with the trajectory cost every move nine minutes of creep. Only
  the fault ramp-down still converts, because a descent with no sensor is a
  trajectory. → `supervisor.max_output_rate_pct_per_min`.
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
