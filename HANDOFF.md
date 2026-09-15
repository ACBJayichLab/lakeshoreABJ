# Handoff — 2026-09-15 (the phase 3 review fixes; nothing has been armed)

Point-in-time status. Durable context lives in `CLAUDE.md` and `docs/`; the
route to a working loop is [PID_PLAN.md](PID_PLAN.md), the step-by-step is
[plans/pid-3-loop.md](plans/pid-3-loop.md), and what this session did is
[plans/pid-3-review.md](plans/pid-3-review.md). This goes stale.

Previous: [HANDOFF-2026-09-14d.md](HANDOFF-2026-09-14d.md) (phase 3 built).

> ## NOTHING WAS COMMANDED, AND THE HEATER IS WHERE IT WAS
>
> The 218's analog output has been at **64.0100 %** since 2026-09-10 14:49 and
> the sample flat at **118.3 K**. **No `control:` section has ever been armed on
> this cryostat.** Everything below happened on the virtual-clock bench.
> **1211 tests passing, `ruff` clean.**

## All ten review fixes landed

A code review of phase 3's steps 5 to 8 found five defects on the bench and six
more from reading, none of them covered by a test — the suite was green
throughout, which was the point. `plans/pid-3-review.md` is the plan; ten
commits, one per step, each naming the safety rule it touched and each with a
bench row that failed before it.

| step | what | rule |
|---|---|---|
| 1 | the bench stops depending on the date; the step test takes the **fast** band | 4 |
| 2 | `CRASHED` survives an idle cycle | 7 |
| 3 | a failed read does not finish a ramp-down | 1, 3 |
| 4 | the descent is bounded per cycle | 1 |
| 5 | the descent never lacks a start | 1 |
| 6 | **the kelvin rows are reachable, and they answer the two scenarios** | **4** |
| 7 | a stale sink is no opinion | 4 |
| 8 | `model_trusted` is `None` by default; the prime fallback is a percent | 4, 2 |
| 9 | the supervisor's thresholds are validated | — |
| 10 | the documentation sweep | — |

### The three that changed what the loop does to a real sample

**Both kelvin rows were unreachable.** They were gated on the tuner's `hold`
phase, and `update_phase` enters `move` on any error over `move_error_k` =
0.25 K — so an error of 1 K, let alone 5, was by construction in the phase that
switched both rows off. Measured: a heater delivering half its power at 30 K
left the sample **14.3 K low, railed at the ceiling, for an hour, in
`tracking`, silent**. That is the one band where the watt residual cannot
speak — above `min_output_pct` and below the 40 K where the plant outruns the
slope window — so there was no check of any kind there.

**A transient crash's latch lasted one cycle.** The OFF-mode early return
excepted `LOCKED_OUT` and said nothing about `CRASHED`, so `arm` was accepted
two seconds after a crash with no `ack`. The old bench row could not see it:
its sabotage re-crashes every cycle, so the latch was re-set as fast as it was
cleared.

**The loop's premise band grew with the calendar.** 3 σ at 118 K is 1.44 mW on
the day the level was gauged, 8.7 mW ten days later and 52 mW at +60 d, and the
step test scaled its fault threshold by it. A month after a gauge the loop
would not have faulted on a step five times the 2026-09-10 event; the monitor,
which judges its own step against a band with no date in it, still would.

### Jeff's principle, 2026-09-15, and what it changed

Two things go wrong and they need different answers:

1. **A steady change the model explains** — the bath moves, the loop needs less
   heat, and the worst case is a sample colder than intended. **A warning,
   however far it goes**, including all the way to the heater at its floor. A
   ramp-down does not improve it and a lockout would stop the loop resuming
   when the bath recovers.
2. **A sudden, aphysical change** — the watts stop adding up, or the loop rails
   at its *ceiling* and the sample still will not come up. **A fault and a
   ramp-down.**

So the two edges of the band part company at `fault_error_k`: the ceiling
faults, the floor warns. `safety.md` rule 4 is reworded.

### What the bench says now

Eight scenarios × six temperatures, plus the review's own rows, **green with
the wall clock pinned to the gauge day and to the gauge plus sixty days** —
which is the gate that stops the bench being a different grader every morning.
New, and all measured:

- a heater delivering 3 % less faults at **both** dates, in 210–264 s; at 12 %
  it is 54–93 mW against a 10 mW floor;
- half power at 30 K warns, faults as authority exhausted, descends, locks out
  and needs an `ack`; at 10 K it warns for two hours at 2.03 K and correctly
  never faults;
- a sink rising 2 K/h at 118 K and 20 K/h at 30 K warns — at the error row and
  then at the floor — holds `tracking`, never raises the heater and never
  faults;
- no descent cycle anywhere moves the output further than the one rate allows.
  It used to move 3.62 % in one 2 s cycle at 60 K, against 0.047 % allowed.

## Three findings the plan did not have

- **The simulated sink was scenery.** The rising-coldplate row moved
  `_aux_base` — the thermometer — and left the plant where it was, so the
  disturbance it was grading did not exist, and it hedged with `if faulted`.
  `FittedHarness.sink_offset` moves both.
- **Railed means the OUTPUT is there too**, not just the demand. A loop
  travelling up to its window at the rate limit has authority it has not
  applied yet, and a PI controller with a standing error rails its demand for
  the whole traverse. On the demand alone the armed-at-0 % case ramped down the
  recovery it was in the middle of.
- **A descent's per-cycle bound belongs on the steady-state curve**, not the
  tuner's schedule. They are the same table on this cryostat and are not in the
  legacy harness, where a descent would be throttled by one curve while
  following another.

## Then, in order — unchanged

1. **Phase 2's 72 h soak is still Jeff's to start**, still unblocked, still one
   command, and the recorder does not need restarting:

   ```
   cd /d C:\Coding\Python\lakeshoreABJ && git pull && .venv\Scripts\python.exe -m ltspm3.monitor -c config-ltspm3-heater.yaml
   ```

   Expect the headline `verdict` to read `no opinion` throughout: it is the
   worst of four, and `tau` cannot have an opinion without a heater move. The
   five residuals underneath it read `typical`.
2. **Phase 3's own soak** — §3.6's quiet hold is one *simulated* hour at each
   temperature and the Allan criterion has no closed-loop record to grade. It
   is the first hour of phase 4.
3. **Phase 4 is the first thing that needs the cryostat.** Stage 3's W1/W2
   close-out comes first — the write-settle sweep is what invariant 5 is
   waiting on, and `verify_readback` is still unverified on the 218 over GPIB.
   **The first `arm` is Jeff's to type.**
4. `lschart status` and MATLAB `plant()` still do not read `plant.json`.
5. The 09-10 mask still goes in with the next archive export.

## Traps this session added to the list

- **A gate that is a proxy stops being one when something else starts driving
  it.** "In `hold` only" meant "while the setpoint is not moving", and the
  tuner's phase said that faithfully until the error began deciding the phase.
  Nothing announced the change; both alarms simply stopped being reachable.
- **A defence has to be measured in the state it defends against.** Every
  wrong-on-purpose row perturbs the CONTROLLER's model, and a heater that
  stops delivering is the opposite experiment. The docstring named the case and
  nothing ran it.
- **A test that disables the premise check has to disable all of it.** Two
  safety tests neutralised `warn_error_k` and `anomaly_demand_pct` and left
  `fault_error_k`, which only ever mattered once the kelvin rows could fire.
- **A validator with an untested line passes everything.** Six of the
  premise check's thresholds could be set to zero and the config still loaded.

## Running it

The venv is Windows and lives at the **repository root**, not in a worktree:

```bash
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ruff check .
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m pytest tests_ltspm3/test_bench.py tests_ltspm3/test_bench_review.py -q
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe -m ltspm3.monitor --replay reference/cooldown-10/
C:/Coding/Python/lakeshoreABJ/.venv/Scripts/python.exe analysis/pid_tuning.py --rows
```

`analysis/` still needs `measure.py` run once (11 s) before anything that fits.
