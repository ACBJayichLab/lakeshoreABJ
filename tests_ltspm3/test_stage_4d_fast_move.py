"""**Jeff's benchmarks, 2026-09-17**, graded on the bench under the shipped file.

docs/ltspm3/requirements.md is the source.  Two of them the bench can grade:

* **a 2 K move at 118 K arrives in about five minutes** -- 95 % of the way and
  staying there, no overshoot worth the name, no rail, no fault;
* **a hold does not degrade the noise** at 15 s, 1 min and 5 min against the
  same plant open loop, and is better at longer averaging.

The third -- "vastly better long-term stability than open loop" -- the bench
cannot grade, because its plant has white sensor noise and no slow disturbance
(HANDOFF 2026-09-17 item D).  That one is `analysis/hold_quality.py` against
the 2026-09-15 open-loop night, on the cryostat.

Everything here runs `stage="file"`: the numbers graded are the numbers the
cryostat is handed.  The thresholds are Jeff's where he gave one and the bench
measurement with a margin where he did not; both are written down beside the
assertion so a future retune knows which kind it is moving.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from bench_plant import STAGE_FILE, FittedHarness

from ltspm3.control import SupervisorState

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import allan  # noqa: E402

#: Same cryostat as `test_stage_4a.py` and `test_stage_4c_tuning.py`: the
#: heater delivering 0.336 % less than `P(u)` claims, settled on it.
DELIVERED_FRAC = 1.0 - 0.00336
BENCH_K = 118.3

#: JEFF'S NUMBER: five minutes for two kelvin at 118 K.
ARRIVE_MIN = 5.0
#: 5 % of the move, which is the usual reading of "arrived"; 100 mK on 2 K.
ARRIVE_FRAC = 0.05


def settled_loop(kelvin=BENCH_K, **kw):
    h = FittedHarness(kelvin=kelvin, stage=STAGE_FILE, settled=True,
                      delivered_frac=DELIVERED_FRAC, **kw)
    h.sup.arm(h.sup.status.filtered_k)
    h.minutes(5)
    return h


def move(h, delta_k, minutes):
    """Command a move and report how it went, in the operator's terms."""
    start = h.sup.status.filtered_k
    target = start + delta_k
    t0 = h.clock.t
    out0 = h.sup.output_pct
    h.sup.set_setpoint(target)
    ts, temps, outs, states = [], [], [], set()
    railed = 0
    for _ in range(int(minutes * 60 / h.DT)):
        st = h.step(1)
        ts.append(h.clock.t - t0)
        temps.append(h.plant.temperature)
        outs.append(st.output_pct)
        states.add(st.state)
        lo, hi = h.sup.band
        if st.output_pct is not None and st.output_pct >= hi - 1e-9:
            railed += 1
    tol = ARRIVE_FRAC * abs(delta_k)
    arrived_s = None
    for i, t in enumerate(ts):
        if all(abs(x - target) <= tol for x in temps[i:]):
            arrived_s = t
            break
    sign = 1.0 if delta_k > 0 else -1.0
    overshoot = sign * (max(sign * x for x in temps) - sign * target)
    return dict(arrived_min=None if arrived_s is None else arrived_s / 60.0,
                overshoot_k=overshoot, railed=railed, states=states,
                final_error_k=temps[-1] - target,
                overdrive_pct=sign * (max(sign * o for o in outs) - outs[-1]),
                step_pct=outs[-1] - out0)


# -- the move ----------------------------------------------------------------

def test_two_kelvin_at_118_arrives_in_five_minutes():
    """THE benchmark.  Measured 4.6-4.7 min with the shipped numbers."""
    r = move(settled_loop(), 2.0, minutes=30)
    assert r["arrived_min"] is not None and r["arrived_min"] <= ARRIVE_MIN
    assert r["overshoot_k"] < ARRIVE_FRAC * 2.0          # under 100 mK; measured 7
    assert r["railed"] == 0
    assert r["states"] == {SupervisorState.TRACKING}
    assert abs(r["final_error_k"]) < 0.05


def test_the_move_back_is_as_quick():
    """Down as well as up: the smoother and the gains are symmetric, and the
    plant's local gain barely changes over 2 K.  Measured 4.9 min."""
    r = move(settled_loop(), -2.0, minutes=30)
    assert r["arrived_min"] is not None and r["arrived_min"] <= ARRIVE_MIN + 0.5
    assert r["overshoot_k"] < ARRIVE_FRAC * 2.0
    assert r["railed"] == 0
    assert r["states"] == {SupervisorState.TRACKING}


@pytest.mark.parametrize("kelvin,limit_min", [
    (100.0, 5.0),    # measured 3.8
    (140.0, 6.0),    # measured 5.0 -- and FAULTED before the band followed
    (180.0, 6.0),    # measured 4.9 -- and went to zero output before
])
def test_the_same_move_arrives_where_experiments_run(kelvin, limit_min):
    """**Where the cryostat has actually been run.**  With the band pinned at
    `operating_point_pct` (the state until 2026-09-17) the 140 K case faulted
    `authority exhausted` and ramped the heater down, and the 180 K case took
    the output to zero.  Both arrive now."""
    r = move(settled_loop(kelvin=kelvin), 2.0, minutes=30)
    assert r["arrived_min"] is not None and r["arrived_min"] <= limit_min
    assert r["overshoot_k"] < ARRIVE_FRAC * 2.0
    assert r["railed"] == 0
    assert r["states"] == {SupervisorState.TRACKING}


def test_sixty_kelvin_overshoots_and_that_is_the_open_item():
    """**A limitation pinned so it cannot be forgotten**, not a pass.

    At 60 K the plant's corner is 22 s and the 5 K/min output rate limit is
    0.8 %/min, so the 0.6 % of overdrive the move needs takes 45 s to build
    and the integral winds up behind the limiter: 0.39 K over on a 2 K move,
    settling afterwards.  `move_speed: 0.25` does not overshoot here but takes
    10 min at 118 K; a rate ceiling of 10 or 20 K/min was measured WORSE
    (tripped `anomaly_demand_pct`, slower at 118 K).  Anti-windup against the
    rate limiter is the fix, and it is a `control/` change under rule 8.

    If this starts failing on the LOW side the item is closed: delete this
    test and move 60 K into the parametrised one above.
    """
    r = move(settled_loop(kelvin=60.0), 2.0, minutes=30)
    assert 0.2 < r["overshoot_k"] < 0.6
    assert r["arrived_min"] is not None and r["arrived_min"] <= 6.0
    assert r["railed"] == 0
    assert r["states"] == {SupervisorState.TRACKING}


def test_a_ten_kelvin_move_stays_inside_the_band():
    """The band is 1.0 % and a 10 K move needs 0.8 % of overdrive on a 0.77 %
    step.  It arrives in about 16 minutes without touching the ceiling, which
    is the band widening by the ramp's lead doing its job."""
    r = move(settled_loop(), 10.0, minutes=60)
    assert r["arrived_min"] is not None and r["arrived_min"] <= 20.0
    assert r["overshoot_k"] < ARRIVE_FRAC * 10.0
    assert r["railed"] == 0
    assert r["states"] == {SupervisorState.TRACKING}


# -- the hold ------------------------------------------------------------------

#: Jeff's three averaging times, plus the 10 s floor the criterion is anchored
#: to and one longer point where a strong hold should be EARNING its place.
HOLD_TAUS = (10.0, 15.0, 60.0, 300.0, 900.0)
HOLD_HOURS = 2.0


def hold_record(armed: bool):
    h = FittedHarness(kelvin=BENCH_K, stage=STAGE_FILE, settled=True,
                      delivered_frac=DELIVERED_FRAC)
    if armed:
        h.sup.arm(h.sup.status.filtered_k)
        h.minutes(30)      # the arrival transient dies before the record starts
    h.history.clear()
    h.minutes(HOLD_HOURS * 60)
    t = np.array([s.t for s in h.history])
    y = np.array([s.raw_k for s in h.history], dtype=float)
    return t, y


def test_a_hold_does_not_degrade_the_noise_and_helps_at_long_tau():
    """Jeff, 2026-09-17: "equal or better 0.25, 1 and 5 minute noise".

    Allan deviation of the SAME plant, same seed, armed against open loop.
    Measured over 3 h at `hold_speed: 0.25`: 1.00 / 1.00 / 0.97 / 0.81 / 0.71
    at 10 / 15 / 60 / 300 / 900 s.  The weak hold this replaced (12) read 1.10
    at 300 s and 2.15 at 900 s -- the stirring the 2026-09-16 night showed.
    """
    t_open, y_open = hold_record(armed=False)
    t_arm, y_arm = hold_record(armed=True)
    _, sig_open, _ = allan.adev(t_open, y_open, list(HOLD_TAUS))
    _, sig_arm, _ = allan.adev(t_arm, y_arm, list(HOLD_TAUS))
    ratio = dict(zip(HOLD_TAUS, np.array(sig_arm) / np.array(sig_open)))
    # Jeff's three, plus the floor: not worse.  5 % is the bench's own
    # run-to-run scatter at edf in the hundreds, not a concession.
    for tau in (10.0, 15.0, 60.0, 300.0):
        assert ratio[tau] <= 1.05, (tau, ratio)
    # And the loop is earning its place where averaging is long.
    assert ratio[900.0] < 0.9, ratio
