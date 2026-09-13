# Software PID — the plan

**Status: PHASE 0 DONE. PHASE 1 UNDER WAY — §1.1's two "settle first" items
are done, and REFIT_PLAN.md PHASE B IS COMPLETE.** `ltspm3/model/` exists and
**`_fitted_table.py` has been regenerated** (2026-09-13): §1's three rows are
green, `SUPERSEDED_NOTE` is cleared, and the table is 4-5 K warmer at a given
output than the one every earlier number in this plan was computed against.
**Its level now carries a dated delivered-power gauge** — work on the heater
wiring expires it, the shape does not; REFIT_PLAN.md §7.3. Written 2026-09-11 from Jeff's requirements (§1); revised
the same day through four rounds of questions. Update this line as phases land.

**Goal.** A software PID that holds and sweeps the LTSPM3 sample from 4 to
300 K, fails gracefully, and judges from the thermal characterisation whether
the cryostat is behaving typically — warning on the atypical, faulting only on
the dangerous. One program, two halves: `lschart` for the person watching,
`ltspm3` for the loop. `ltspm3/control/` is open to change under the eight
rules of [safety.md](docs/ltspm3/safety.md), one rule-scoped commit at a time.

| phase | document | one-line goal | exit gate |
|---|---|---|---|
| **0** | §5 here | the record is straight | **DONE 2026-09-12** — `curate --propose` clean, `send note` proved on the live recorder, four documents corrected |
| **1** | [plans/pid-1-model.md](plans/pid-1-model.md) | a model that is right from 4 to 300 K, with its error band exported | REFIT §1 green; leave-one-epoch-out < 0.5 K; table to 300 K; `missing_power_w` < 1 mW at every anchor |
| **2** | [plans/pid-2-monitor.md](plans/pid-2-monitor.md) | a judge outside the loop that catches both archive events and nothing else | replay table all green; < 1 warning/week on a settled hold; 72 h live |
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
| premise | **warn at 1 K, fault at 5 K**; for the PID **in watts, from the fit** | hold at 1 K, ramp down after 180 s | kelvin thresholds in `hold` phase only; `δQ` in watts always; the 09-10 event is a warning |
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
ltspm3/monitor.py THE JUDGE.  Separate process, no port, reads model/ and the
                  recorder's files, writes plant.json.  Report only.
analysis/         GENERATES model/_fitted_table.py.  Imports neither package.
```

`control/` and `monitor.py` both read `model/`; neither reads the other. They
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
δQ = C(T_s)·dT_s/dt + [Λ(T_s) − Λ(T_c)] − P(u) − D(t)      [W]
```

is valid at a hold **and during a sweep**, which a kelvin check is not.

**Band** `σ_Q` (exported with the table): `δP` = 0.7 % of P(u) · drift ±25 %
of 0.281 mW/day × days since fit · 14.2 mK × Λ′ · diurnal 16 mK × Λ′ ·
coldplate 27.6 mK × Λ′(T_c) · **`σ_C × |dT_s/dt|`** — at 5 K/min and 118 K
the dynamic term is ~70 mW, so a 5 % error in C is 3.5 mW and the band must
widen during a sweep or every sweep warns.

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

**Open.** None that block Phase 0 or 1. Phase 3's `fault_mw` and Phase 2's
`warn_after_s` are set by the replay, not chosen.

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
