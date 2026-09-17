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
from bench_plant import DELIVERED_FRAC, STAGE_FILE, FittedHarness

from ltspm3.control import SupervisorState

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import allan  # noqa: E402

#: Same cryostat as the other stage tests -- `DELIVERED_FRAC`, settled on it.
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
    """THE benchmark (docs/ltspm3/requirements.md §2, §3 for the measurement)."""
    r = move(settled_loop(), 2.0, minutes=30)
    assert r["arrived_min"] is not None and r["arrived_min"] <= ARRIVE_MIN
    # "No overshoot worth the name": worth the name is the arrival window
    # itself, so an overshoot inside it never delayed arrival.
    assert r["overshoot_k"] < ARRIVE_FRAC * 2.0
    assert r["railed"] == 0
    assert r["states"] == {SupervisorState.TRACKING}
    assert abs(r["final_error_k"]) < 0.05


def test_the_move_back_is_as_quick():
    """Down as well as up: the smoother and the gains are symmetric, and the
    plant's local gain barely changes over 2 K.  Half a minute of slack on
    Jeff's five, because the descent is the plant relaxing rather than the
    heater driving and the two are not quite the same time constant."""
    r = move(settled_loop(), -2.0, minutes=30)
    assert r["arrived_min"] is not None and r["arrived_min"] <= ARRIVE_MIN + 0.5
    assert r["overshoot_k"] < ARRIVE_FRAC * 2.0
    assert r["railed"] == 0
    assert r["states"] == {SupervisorState.TRACKING}


#: Jeff's five minutes is stated at 118 K; the warm end is slower because the
#: plant's own tau is longer there, so it is graded with a minute of slack
#: rather than being let off.  (docs/ltspm3/requirements.md §3 for the times.)
@pytest.mark.parametrize("kelvin,limit_min", [
    (100.0, 5.0),
    (140.0, 6.0),
    (180.0, 6.0),
])
def test_the_same_move_arrives_where_experiments_run(kelvin, limit_min):
    """**Where the cryostat has actually been run.**  With the band pinned at
    `operating_point_pct` the 140 K case faulted `authority exhausted` and
    ramped the heater down, and the 180 K case took the output to zero.  Both
    arrive now that the band follows the setpoint."""
    r = move(settled_loop(kelvin=kelvin), 2.0, minutes=30)
    assert r["arrived_min"] is not None and r["arrived_min"] <= limit_min
    assert r["overshoot_k"] < ARRIVE_FRAC * 2.0
    assert r["railed"] == 0
    assert r["states"] == {SupervisorState.TRACKING}


def test_sixty_kelvin_overshoots_and_that_is_the_open_item():
    """**A limitation pinned so it cannot be forgotten**, not a pass.

    At 60 K the output rate limit is slow against the loop's own corner, so the
    overdrive the move needs cannot build in time and the integral winds up
    behind the limiter.  Anti-windup against the rate limiter is the fix, a
    `control/` change under rule 8; the open item and the measurements are in
    docs/ltspm3/requirements.md §3.

    If this starts failing on the LOW side the item is closed: delete this
    test and move 60 K into the parametrised one above.
    """
    r = move(settled_loop(kelvin=60.0), 2.0, minutes=30)
    # Both bounds say something.  The LOWER one is the open item: an overshoot
    # this far outside the arrival window is a defect, and if it stops
    # happening somebody has fixed it and this test has to be retired rather
    # than left passing by accident.  The UPPER one is the guard: whatever else
    # is retuned, 60 K must not get worse than it is today.
    assert 0.2 < r["overshoot_k"] < 0.6
    assert r["arrived_min"] is not None and r["arrived_min"] <= 6.0
    assert r["railed"] == 0
    assert r["states"] == {SupervisorState.TRACKING}


def test_a_ten_kelvin_move_stays_inside_the_band():
    """A big move needs most of the band in overdrive, and gets it without
    touching the ceiling -- the band widening by the ramp's lead doing its job.
    Twenty minutes is the bound because a 10 K move at 5 K/min is a trajectory
    several times longer than a 2 K one, so Jeff's five minutes does not
    apply; the measured time is in docs/ltspm3/requirements.md §3."""
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
    """Jeff's hold benchmark: "equal or better 0.25, 1 and 5 minute noise".

    Allan deviation of the SAME plant, same seed, armed against open loop, over
    `HOLD_HOURS`.  The ratios the shipped hold achieves are in
    docs/ltspm3/requirements.md §3; a weak hold stirs at the long end and that
    is what the last assertion catches.
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
    # And the loop is earning its place where averaging is long.  Jeff asked
    # for "vastly improving" there and gave no number, so the bound is the
    # least that can be told apart from not helping: 10 % better, twice the
    # run-to-run scatter allowed above.
    assert ratio[900.0] < 0.9, ratio
