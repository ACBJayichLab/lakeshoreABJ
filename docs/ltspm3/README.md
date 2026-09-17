# LTSPM3: the software PID

**Everything in this directory is specific to one cryostat.** Every number is
calibrated to Jeff's LTSPM3 cryostat and does not transfer. If you are running a
Lake Shore box on any other system, you want
[the chart recorder docs](../recorder/) and nothing here.

## What it is, and why it exists at all

The LTSPM3 sample heater hangs off the **218's analog output**, and a 218 has
no heater loop of its own. There is no firmware PID to command. So this
software *is* the loop.

That is the whole reason `ltspm3` exists as a separate package. Every other cryostat
in this project drives the instrument's *own* PID by setpoint, which is a much
smaller and much safer thing to do.

```
lschart   generic recorder     drives the INSTRUMENT'S loop by setpoint
ltspm3     LTSPM3 only          IS the loop, on the 218's analog output
```

`ltspm3` imports `lschart`. Nothing in `lschart` may import `ltspm3`.

## Read in this order

1. **[cryostat.md](cryostat.md)** — the hardware, the addresses, what is wired to what,
   and which box must not be touched.
2. **[safety.md](safety.md)** — the eight design rules and the sensor glitch
   that shaped them. **Read this before running anything armed.**
3. **[thermal-response.md](thermal-response.md)** — what the cryostat actually does, measured over
   1,510 hours of logs.
4. **[control.md](control.md)** — how the loop is built out of those numbers.
5. **[running.md](running.md)** — `check`, `run --arm`, replay, step test.

The staged procedure for getting onto the real cryostat, and the calibration
campaign that follows, are in
[plans/pid-4-commissioning.md](../../plans/pid-4-commissioning.md).

## Status

**The loop has closed on this cryostat** — armed 2026-09-16, held overnight,
and moved its first setpoint on 2026-09-17. Phases 0 to 3 of
[PID_PLAN.md](../../PID_PLAN.md) are done; phase 4, commissioning, is under
way on the cryostat.

**What it is graded against is [requirements.md](requirements.md)** — Jeff's
answers of 2026-09-17, in his words: a 2 K move at 118 K in five minutes, a
hold whose noise is no worse than open loop at 15 s to 5 min and much better
at long averaging, a band that follows the setpoint. The loop was retuned to
those the same day and the retune has been graded on the bench, not yet on the
cryostat. Read that document before touching a number in the config.

`control/` is **open to change** under the eight rules of
[safety.md](safety.md), one rule-scoped commit at a time, each reviewed against
the rule it touches. No "while I am in here" changes, and nothing lands in
`control/` without a test on the virtual-clock harness.

Two things are worth knowing before the next armed run:

- **The bench is not the cryostat.** Its plant has white sensor noise and no
  slow disturbance, so it can grade a move and the short-tau half of a hold,
  and cannot grade the slow wander that is the hold's whole point. That is
  `analysis/hold_quality.py` against the 2026-09-15 open-loop night. See
  [thermal-response.md](thermal-response.md) for what the live data has
  measured, and what still comes from the reference logs.
- **`verify_readback` on the 218 is unverified over GPIB, and it can only ever
  see steps larger than `readback_tol_pct`** — which at a hold, and while
  ramping at 118 K, no write is. See
  [running.md](running.md#before-the-first-armed-run) for which regimes it is
  doing real work in and which it is silent in by arithmetic.
