"""**Jeff's benchmarks**, graded on the bench under the shipped file.

docs/ltspm3/requirements.md is the source, and section 1b is the one these
grade -- the follow-up, once five minutes for 2 K had been armed and watched.
Two of them the bench can grade:

* **a 2 K move at 120 K is 95 % of the way there fast**, overshooting by no
  more than Jeff's 250 mK, and is then **within 50 mK and staying** -- the fast
  approach and the slight adjustment, which are one requirement in two halves;
* **a hold does not degrade the noise** at 15 s, 1 min and 5 min against the
  same plant open loop, and is better at longer averaging.

The third -- "vastly better long-term stability than open loop" -- the bench
cannot grade, because its plant has white sensor noise and no slow disturbance
(HANDOFF 2026-09-17 item D).  That one is `analysis/hold_quality.py` against
the 2026-09-15 open-loop night, on the cryostat.

**What sets the arrival time is not in this file and not in `tuning:`.**  With
`move_speed` parked on the dead-time floor, a move costs
`span / rate + corner + about 2 tau_cl`, and all three of those are Jeff's
choices from section 1b: `max_rate_k_per_min` 5, `delay_floor` 4 (so `tau_cl`
is 12 s at the 2 s cadence and median of three), and a corner of 8 dead times.
86 s at 120 K is the floor those three imply, and it is why ARRIVE_S is 100 and
not the 30 the conversation opened with.

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

#: Where Jeff stated the move requirement, and where this cryostat is run.
BENCH_K = 120.0

#: 5 % of the move, which is the usual reading of "arrived"; 100 mK on 2 K.
ARRIVE_FRAC = 0.05
#: The fast approach.  Measured 86 s at 120 K; graded with room for the
#: temperatures where the plant is slower, and a long way inside the 4.6 min
#: the superseded requirement allowed.
ARRIVE_S = 100.0
#: **JEFF'S NUMBER**: 250 mK on a 2 K move.  Measured 11 mK.
OVERSHOOT_K = 0.25
#: **JEFF'S NUMBER**: within 50 mK and staying, inside 2-5 minutes.  It is the
#: same 50 mK as `tuning.hold_error_k`, so the gate and the loop's own
#: hysteresis cannot come to disagree.
SETTLE_K = 0.05
SETTLE_S = 300.0


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
    held, longest_hold = 0, 0
    for _ in range(int(minutes * 60 / h.DT)):
        st = h.step(1)
        ts.append(h.clock.t - t0)
        temps.append(h.plant.temperature)
        outs.append(st.output_pct)
        states.add(st.state)
        if st.state is SupervisorState.TRACKING:
            held = 0
        else:
            held += 1
            longest_hold = max(longest_hold, held)
        lo, hi = h.sup.band
        if st.output_pct is not None and st.output_pct >= hi - 1e-9:
            railed += 1
    def first_and_staying(tol):
        """When it got this close and never left again.  `None` if it never
        did -- "and staying" is the whole point, so a brush past does not
        count."""
        for i, t in enumerate(ts):
            if all(abs(x - target) <= tol for x in temps[i:]):
                return t
        return None

    sign = 1.0 if delta_k > 0 else -1.0
    overshoot = sign * (max(sign * x for x in temps) - sign * target)
    return dict(arrived_s=first_and_staying(ARRIVE_FRAC * abs(delta_k)),
                settled_s=first_and_staying(SETTLE_K),
                overshoot_k=overshoot, railed=railed, states=states,
                final_error_k=temps[-1] - target,
                overdrive_pct=sign * (max(sign * o for o in outs) - outs[-1]),
                peak_pct=max(o for o in outs if o is not None),
                longest_hold=longest_hold,
                step_pct=outs[-1] - out0)


#: The most cycles a move may spend holding.  The sensor guard's own recovery
#: count, not a number chosen here: a move that accelerates hard can be
#: momentarily ambiguous and rule 6 says an ambiguous case holds, but it must
#: come straight back.  Measured: nothing holds at all except 100 K, which
#: holds once for 14 s and still arrives at 86 s.
HOLD_CYCLES = 15


def assert_arrived(r, *, arrive_s=ARRIVE_S, settle_s=SETTLE_S,
                   overshoot_k=OVERSHOOT_K):
    """Jeff's move requirement, in one place: fast there, then settled."""
    assert r["arrived_s"] is not None and r["arrived_s"] <= arrive_s, r
    assert abs(r["overshoot_k"]) <= overshoot_k, r
    assert r["settled_s"] is not None and r["settled_s"] <= settle_s, r
    assert r["railed"] == 0, r
    assert_never_stopped(r)


def assert_never_stopped(r):
    """No fault, no lockout, and any hold was a moment rather than a stop."""
    assert not ({SupervisorState.RAMPING_DOWN, SupervisorState.LOCKED_OUT,
                 SupervisorState.CRASHED} & r["states"]), r
    assert r["longest_hold"] <= HOLD_CYCLES, r


# -- the move ----------------------------------------------------------------

def test_two_kelvin_at_120_is_there_fast_and_then_settles():
    """**THE benchmark** (docs/ltspm3/requirements.md §1b).

    Measured: 95 % at 86 s, within 50 mK at 114 s, overshoot 11 mK.  The
    superseded requirement allowed 4.6 min to the same point.
    """
    r = move(settled_loop(), 2.0, minutes=30)
    assert_arrived(r)
    assert abs(r["final_error_k"]) < SETTLE_K


def test_the_move_back_is_as_quick():
    """Down as well as up: the smoother and the gains are symmetric, and the
    plant's local gain barely changes over 2 K.  Measured 86 s and 126 s."""
    r = move(settled_loop(), -2.0, minutes=30)
    assert_arrived(r)


#: Where the cryostat is actually run, and the two ends that bind for different
#: reasons -- see the two rows carrying their own limit below.
@pytest.mark.parametrize("kelvin,arrive_s", [
    (30.0, 130.0),      # the dead-time floor binds: the plant is faster than
                        # the measurement, so `delay_floor` sets the speed
    (60.0, 130.0),
    (100.0, 100.0),
    (140.0, 100.0),
])
def test_the_same_move_arrives_where_experiments_run(kelvin, arrive_s):
    """**Where the cryostat has actually been run.**  Measured 112 / 106 / 88 /
    80 s at 95 %, all overshooting under 15 mK.

    The cold end is graded looser and that is not a concession: below about
    60 K `tau(T)` is shorter than the loop's own dead time, so `delay_floor`
    and not the cryostat decides how fast this may go.
    """
    r = move(settled_loop(kelvin=kelvin), 2.0, minutes=30)
    assert_arrived(r, arrive_s=arrive_s)


def test_at_180_the_ceiling_is_what_limits_the_move():
    """**A limitation pinned so it cannot be mistaken for a tuning problem.**

    At 180 K the steady output is high enough that the overdrive a fast move
    wants runs into `hard_max_pct` itself, so the move takes 226 s rather than
    the 80-90 it takes lower down.  Nothing here is retunable: the cryostat is
    giving what it is allowed to give.  It still arrives, still settles, and
    still does not fault, which is the part that matters.

    If this starts arriving inside `ARRIVE_S`, somebody has raised the ceiling
    or re-gauged the model -- check which, then move 180 K into the
    parametrised row above.
    """
    r = move(settled_loop(kelvin=180.0), 2.0, minutes=30)
    assert r["arrived_s"] is not None and r["arrived_s"] <= 300.0, r
    assert abs(r["overshoot_k"]) <= OVERSHOOT_K, r
    assert r["settled_s"] is not None and r["settled_s"] <= 300.0, r
    assert_never_stopped(r)
    assert r["peak_pct"] == pytest.approx(70.0, abs=0.02), (
        "180 K no longer reaches the ceiling -- see the docstring")


def test_sixty_kelvin_no_longer_overshoots():
    """**A closed open item** (docs/ltspm3/requirements.md §3).

    At 60 K a 2 K move used to overshoot 387 mK: the output rate limit was the
    trajectory's kelvin rate through the gain, it was slow against the loop's
    own corner, and the integral wound up behind it.  Giving the heater its own
    rate removed the cause rather than the symptom -- measured 2.8 mK now.

    Kept as its own test, rather than folded into the row above, because a
    regression here has a known shape and a known cause and this docstring is
    where that is written down.
    """
    r = move(settled_loop(kelvin=60.0), 2.0, minutes=30)
    assert abs(r["overshoot_k"]) < 0.05, r
    assert_arrived(r, arrive_s=130.0)


def test_a_ten_kelvin_move_stays_inside_the_band():
    """A big move needs most of the band in overdrive, and gets it without
    touching the ceiling -- the band widening by the ramp's lead doing its job.

    **Measured 156 s, against 15.9 min before the heater had its own rate.**
    That case is the clearest single statement of what the old conversion cost:
    sustaining 5 K/min needs 3.5 % of overdrive, the converted limit delivered
    0.40 %/min, and the ramp was long over before the drive arrived.
    """
    r = move(settled_loop(), 10.0, minutes=60)
    assert r["arrived_s"] is not None and r["arrived_s"] <= 240.0, r
    assert abs(r["overshoot_k"]) <= OVERSHOOT_K, r
    assert r["railed"] == 0, r
    assert_never_stopped(r)


def test_a_small_move_settles_inside_a_minute():
    """Jeff's day-to-day case: a ladder with a measurement at each rung.

    The fixed overhead is what a small move pays -- `span / rate` is nothing
    on half a kelvin -- so this is the arrival time the loop cannot beat, and
    it is under a minute.  Graded on the 50 mK gate rather than on 95 %,
    because 5 % of 0.5 K is 25 mK and that is asking the hold to be twice as
    good as Jeff asked for, not asking the move to be quick.
    """
    r = move(settled_loop(), 0.5, minutes=20)
    assert r["settled_s"] is not None and r["settled_s"] <= 60.0, r
    assert abs(r["overshoot_k"]) <= OVERSHOOT_K, r
    assert_never_stopped(r)


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
