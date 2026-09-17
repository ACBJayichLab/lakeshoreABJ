# The LTSPM3 cryostat

## Instruments

| Item | Detail |
|---|---|
| Lake Shore 336 | `GPIB0::12::INSTR` — 4 inputs: RAD SHIELD, THE CHONKE, 1st Stage, 2nd Stage |
| Lake Shore 218 | `GPIB0::15::INSTR` — 8 inputs, 3 populated: **1 Sample**, 2 Coldplate, 5 Magnet. Inputs 3, 4 and 6–8 are empty |
| Sample heater | 218 **analog output 1** → op-amp → heater. The 218 has no heater loop; this software *is* the loop |
| Poll cadence | **2.0 s** by config. `check` budgets 26 transactions at ~1.30 s per cycle, so 1 Hz does not fit without trimming that |

Addresses come from the legacy MATLAB in `reference/` (`DAQManager.m`), which is
kept for reference only and is not part of the build.

On cadence: the legacy logs vary 2–20 s, but that was the 65,536-row Excel
limit forcing slower polling on long runs, not a cryostat constraint.

The loop's status — what is armed, what has run, what is next — is in
[`HANDOFF.md`](../../HANDOFF.md). Numbers here that predate the recorder's own
logs come from the reference logs; see [thermal-response](thermal-response.md)
for which ones have since been measured.

## Changelog — what changed at the box

This is the one list. A config comment may point here; it may not restate a
line of it.

| date | change |
|---|---|
| 2026-08-24 | recorder in service against both boxes over GPIB; the 218's `read_termination` measured as a bare LF (the 336 is CRLF) |
| 2026-09-04 12:07 | recorder stopped; **Coldplate (218 input 2) recalibrated** onto X186279's curve, and **Magnet moved from input 3 to input 5**. Both by hand. See the seam section below |
| 2026-09-10 14:39–14:41 | a connector on the sample heater **reseated** after that morning's fault. **Reseated is not repaired**, and it moved the delivered power — the heater circuit's level is a calibration with a shelf life ([thermal-response](thermal-response.md), [`REFIT_PLAN.md`](../../REFIT_PLAN.md) §7.3) |
| 2026-09-16 | heater wiring handled again; the model's level has been stale since ([requirements.md](requirements.md) §3) |

## The 218's thermometers, and the seam at 2026-09-04 12:07

| Input | Name | Notes |
|---|---|---|
| 1 | Sample | what the software PID controls (`control_input: 1`) |
| 2 | Coldplate | **recalibrated 2026-09-04**; see below |
| 5 | Magnet | **was input 3** until 2026-09-04 |
| 3, 4, 6–8 | — | empty. Not listed in config, so no column and no query |

The map in the configs is deliberately **non-contiguous**, and nothing in the
code minds: the driver reads `sorted(channels)` rather than 1..N, and the CSV
column is the *name*, never the input number. That is why `Magnet` is one
continuous column across the move — same thermometer, same column, a different
socket. Moving a thermometer costs one line of config.

> The simulator did mind, once. `Sim218` answered `KRDG? 0` from a hardcoded
> three-element list, so a channel on input 5 came back `0.0000` — which is
> exactly what the real box reports for an empty input, and is graded
> `NO_SENSOR`. A fake that manufactures the one failure signature it exists to
> help you distinguish is worse than no fake. It now derives its populated
> inputs from the cryostat's own channel map.

### The Coldplate was reading high, because it had another sensor's curve

The 218 was carrying **X186276's** calibration curve on input 2. The Coldplate
is **X186279**. The serials differ in one digit; the sensors do not — X186276
is a CX-1050-**CU** unit about 12% higher in resistance at every temperature,
and the Coldplate is a CX-1050-**SD**. So for the resistance the Coldplate
actually presented, the box named a temperature that was too warm, and the cold
end of this cryostat read high for as long as that curve was loaded. Jeff
loaded the right file at the box on **2026-09-04, at the 12:07:16 cutover**.

(An earlier revision of this document called it "a transposed digit in the
calibration curve — a 6 where a 9 belonged". The digit was in the *serial
number*, and what was loaded was another thermometer's calibration in full,
not a corrupted copy of the Coldplate's own.)

**Every Coldplate figure timestamped before that cutover is wrong**, including
the dated blocks in the two LTSPM3 configs and all of `reference/logs/`. But it is
wrong by a *knowable* amount, because both curve files exist and the
conversion the box did is invertible. Kelvin → resistance → kelvin:

| logged | true | | logged | true |
|---|---|---|---|---|
| 6 K | 4.93 K (−1.07) | | 77 K | 65.33 K (−11.67) |
| 10 K | 8.11 K (−1.89) | | 150 K | 130.42 K (−19.58) |
| 20 K | 16.22 K (−3.78) | | 300 K | 265.10 K (−34.90) |

Not an offset, and not a local warp either: a smooth 12–13% everywhere. Both
curves are in [`reference/sensor-curves/`](../../reference/sensor-curves/) and
the tool that composes them is
[`lschart.tools.recalibrate`](../../lschart/tools/recalibrate.py):

```bash
python -m lschart.tools.recalibrate --report --from reference/sensor-curves/X186276.340 --to reference/sensor-curves/X186279.340
```

**One end is not recoverable.** Above 330.324 K the box was clamping to the top
of the loaded table — 905 rows of CD10 all read exactly 330.3200 — and a
clamped reading carries no resistance to convert. Those rows come back blank
rather than as the 292.6 K that inverting the clamp would produce, which is a
number indistinguishable from a plausible room temperature and therefore the
worst possible thing to write into a log.

The pre-cutover logs have been reprocessed; `data/coldplate-recal/` holds them
and its README says what is where. Until you are looking at those, treat
2026-09-04 12:07 as a hard seam **in Coldplate and in nothing else**: Sample,
the four 336 inputs, the heater output and both 336 loops are unaffected, and
`Magnet` is comparable across it.

### It reached the thermal fits, and it resolved a known anomaly

`analysis/` is the one place the Coldplate is more than a monitored channel:
it is `T_c` in `Λ(T_s) = Q + Λ(T_c)`. Every fit in that directory was computed
from pre-cutover values, and the tables in `reference/cooldown-10/` have
now been remapped in place.

That directory carried a standing caveat that **at zero power the sample
settles 0.79 K *below* the coldplate reading** — which is not physics, since a
sample cannot rest colder than its own heat sink. It was attributed to
"thermometry plus stray magnet-side load". It was the thermometry, and the
correction settles it rather than merely being of the same order:

| | rows where Sample < Coldplate |
|---|---|
| as logged | 13,290 of 811,292 across the three fit tables, worst −30.19 K |
| corrected | **0**, in every table, at every power |

Not one physically impossible row survives the remap. See
[`analysis/README.md`](../../analysis/README.md).

Nothing derived from Coldplate feeds control — the sample loop does not read
it, so the seam has no bearing on control quality or safety. It is a monitored
channel and one term in the simulator's cross-channel coupling,
`LTSPM3_AUX_COUPLING["218.2"] = 0.0082` K/K, measured under the bad curve on
`cd8_..._sample_monitor7.xls`. Remapping that log scales the coldplate's
excursion by **0.84–0.88** depending on the window chosen, so the honest value
is about **0.0070**. It is left at 0.0082 anyway, and now for a measured
reason rather than an absence of one: this is simulator scenery, the coherence
logic only needs the coupling to be *non-zero and of the right order*, and a
15% fidelity gain does not justify perturbing a tested simulator. Re-derive it
properly from post-cutover data when there is some.

### The log was broken at the seam on purpose

Because the column names did not change, a restart would have appended
post-cutover rows to `data/ltspm3-heater_2026-09-04.csv` and the viewer would
have drawn a straight line across a step that never physically happened. The
pre-cutover part of that day is therefore archived out of the recorder's
directory:

```
data/pre-recal-2026-09-04/ltspm3-heater_2026-09-04.csv
```

Open it on its own with `lschart-view --csv <path>`. The viewer will not splice
it into the live history — backfill only walks the directory the followed file
lives in — which is the whole point of the move.

### Where the reprocessed logs are

`data/` is gitignored, so none of this exists on a fresh clone; the commands
that rebuild it are in `data/coldplate-recal/README.txt`.

| | |
|---|---|
| `data/coldplate-recal/cd10/` | the 28 CD10 daily files, Coldplate remapped. **This is what the viewer should open** for anything before the cutover |
| `data/coldplate-recal/recorder/` | the 16 pre-cutover recorder logs, 08-24 → the 09-04 archive. Three of them call input 2 `Cold Head`, which is the same thermometer under its old label |
| `data/coldplate-recal/fit-inputs/` | the three pre-archive `analysis/` tables. **Superseded** -- `analysis/` reads `reference/cooldown-10/`, built from `cd10/` and `recorder/` above, so the corrected `T_c` still arrives with no argument |

The post-cutover `data/ltspm3-heater_2026-09-04.csv` is **not** in there and
must not be: it was recorded on the right curve and remapping it would break
what is already correct.


## The 336 is read-only, and that matters

Loop 2 of the 336 independently holds **"THE CHONKE" near 290 K with heater 2
around 90 %** (the live value is in `status.json`). This software must not
disturb it.

`allow_writes` defaults to `False` and every write raises `PermissionError`
unless it is explicitly enabled. On this cryostat, leave it that way.

## Actuating the heater

The only command that moves the heater, verified against the `Notes` column of
the reference logs:

```
ANALOG 1, 0, 2, 1, 1,1,1,<percent>      # out 1, unipolar, manual mode, kelvin
AOUT? 1                                 # readback, in percent
```

**Only the trailing value ever changes.** `AnalogOutputConfig` keeps the other
seven fields byte-identical to that known-good string rather than recomputing
them — a recomputed field that happens to differ would change the output's
*mode*, not just its level.

### The `AOUT?` flicker, characterised

`AOUT?` reads back 0.003 % away from what was commanded on some samples — but
**only at some commanded values**, which is why the one-line version of this was
true and misleading:

| held at | samples | flicker |
|---|---|---|
| 66.598 % | 117,000 over 65 h | reads **66.595 on 3.2 %** of samples — 3,372 excursions, ~104/h, almost all exactly one sample long |
| 69.027 % | 46,050 over 25.6 h | **none at all.** 46,050 identical readings |

So it is not a general property of the box. At 0.003 % it is smaller than one
DAC code (0.01 %), which puts it in the instrument's own formatting of that code
rather than in the output.

**Nothing is done about it and nothing needs to be**, but know where it lands:

- **Write verification is unaffected.** Two tolerances exist and both are far
  larger than one flickering code: the driver's `readback_tol_pct` (the 218's
  own write check) and the supervisor's `verify_tol_pct` (the armed loop's
  readback check, `verify_readback`). `_confirm` accepts it.
- **`_where_the_heater_is()` sees it**, and that is the only live-loop exposure.
  It re-reads `AOUT?` after any cycle that wrote nothing — exactly the steady
  holding regime where the flicker occurs — and that value is the base for the
  rate limiter's step, the fault ramp-down's step, and the value a manual hold
  adopts. Against a per-cycle step of 0.013 % at 118 K it is a quarter of one
  cycle's move; it is zero-mean and one sample long, so it does not accumulate,
  and it can only shift the quantised code when the target already sits within
  0.003 % of a code boundary. Bounded at one code, ~0.13 K.
- **Analysis of the CSV must allow for it.** `steptest` has a 0.005 % deadband
  for exactly this; anything else reading `ls218.aout1` and looking for steps
  needs its own. Without one, the 08-24 → 09-03 data shows 14,509 apparent
  output changes instead of 99.

The recorder logs the flicker faithfully, and that is correct — it should record
what the instrument said, not what we think it meant.

## The other two cryostats in this repo, for contrast

Neither uses any of this.

**The bench 336** — a spare from a third system, on USB, cryo off, at
atmosphere. The only instrument actually connected. Inputs A Coldplate,
B Stage 2, C Rad Shield, D Stage 1, all ~295–297 K. All four loops closed-loop,
loops 3/4 with `powerup_enable=1`, all setpoints 275 K — i.e. *below* ambient,
so every loop demands zero heat, and all ranges are 0. **Benign by value, not
by configuration.** `TLIMIT` 330 K on every input.
See [`examples/config-336-usb.yaml`](../../examples/config-336-usb.yaml).

**A coworker's 335** on COM10, heaters on its own outputs, so its firmware runs
the loop. Needs logging plus setpoint, and no software PID at all. Driven by
`driver: lakeshore`, which needs no VISA runtime.
See [`examples/config-335-usb.yaml`](../../examples/config-335-usb.yaml).
