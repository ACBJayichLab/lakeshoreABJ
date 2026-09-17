"""**Can this loop move a setpoint?**  At 4a it cannot, and the reason is one switch.

`test_stage_4a.py` grades the stage the cryostat is armed at.  This grades the
one question 4a leaves unanswered and which nothing else here asks: a hold is
not the job, and *ramping to a setpoint had never been exercised on the bench
or the cryostat* (2026-09-17).

**Three things are gated on `tuner.enabled`, and only one of them is a gain:**

* the scheduled `(kp, ti)` -- `move_speed: 0.5` is an 8x kp against `hold_speed: 3`;
* the **velocity feedforward** (`supervisor.py`, `_step`), which is the drive
  `rate*tau/K` a ramp needs and which no amount of integral action supplies in
  time;
* the **band widening** (`ramp_lead_pct`), so the authority to use that drive
  exists.

With `tuning.enabled: false` all three are zero, and the loop has to drag the
sample with P+I inside a fixed +/-0.25 % window.  It does not rail and it does
not fault -- it simply arrives late by about a kelvin whatever rate it is
given, because the rate is not what is binding.  That is what these tests pin.

**`tuning` and `feedforward` are not the same dependency**, which is why the
tuner can come on while the feedforward stays off.  The feedforward commands
the model's LEVEL, and the level is stale (HANDOFF item A).  The tuner reads
only `K(T)` and `tau(T)` -- the SHAPE -- which is what `has_curve` already
distinguishes for the ramp-down and the output rate limiter
(AUDIT-2026-09-16 findings 2 and 3).
"""

from __future__ import annotations

import dataclasses

import pytest
from bench_plant import STAGE_FILE, FittedHarness, bench_control_config

from ltspm3.control.tuning import ControlPhase

#: Same cryostat as `test_stage_4a.py`: the heater delivering 0.336 % less
#: power than `P(u)` claims, settled on it.  Sharing the number matters -- a
#: ramp test on a healthier cryostat than the hold tests run on would be
#: grading two things at once.
DELIVERED_FRAC = 1.0 - 0.00336
BENCH_K = 118.3


def harness(*, tuning: bool, hold_speed: float | None = None):
    cfg = bench_control_config()
    tun = dataclasses.replace(cfg.tuning, enabled=tuning)
    if hold_speed is not None:
        tun = dataclasses.replace(tun, hold_speed=hold_speed)
    return FittedHarness(kelvin=BENCH_K, stage=STAGE_FILE, settled=True,
                         delivered_frac=DELIVERED_FRAC, tuning_cfg=tun)


def ramp(h, *, rate_k_per_min, delta_k, minutes):
    """Arm, command a move, and report where it got to.

    Five minutes of closed loop before the move so the loop is tracking rather
    than still priming -- arming and commanding in the same cycle grades the
    handover, which is `test_stage_4a.py`'s subject, not this one.
    """
    start = h.sup.status.filtered_k
    h.sup.arm(start)
    h.minutes(5)
    target = start + delta_k
    h.sup.set_setpoint(target, rate_k_per_min=rate_k_per_min)

    worst_error_k = 0.0
    railed = 0
    widest_band_pct = 0.0
    peak_vff_pct = 0.0
    for _ in range(int(minutes * 60 / h.DT)):
        st = h.step(1)
        if st.error_k is not None:
            worst_error_k = max(worst_error_k, abs(st.error_k))
        lo, hi = h.sup.band
        widest_band_pct = max(widest_band_pct, hi - lo)
        peak_vff_pct = max(peak_vff_pct, abs(st.velocity_ff_pct or 0.0))
        if st.output_pct is not None and st.output_pct >= hi - 1e-9:
            railed += 1
    return {
        "target_k": target,
        "reached_k": h.plant.temperature,
        "short_by_k": target - h.plant.temperature,
        "worst_error_k": worst_error_k,
        "railed_cycles": railed,
        "widest_band_pct": widest_band_pct,
        "peak_vff_pct": peak_vff_pct,
    }


# -- what the switch controls ---------------------------------------------

def test_4a_grants_a_ramp_no_drive_and_no_authority():
    """All three ramp mechanisms are off together, and that is deliberate."""
    h = harness(tuning=False)
    h.sup.arm(h.sup.status.filtered_k)
    h.minutes(5)
    quiet_lo, quiet_hi = h.sup.band
    h.sup.set_setpoint(h.sup.status.filtered_k + 2.0, rate_k_per_min=5.0)
    h.minutes(5)

    assert h.sup.tuner.enabled is False
    assert h.sup.ramp_lead_pct() == 0.0
    assert h.sup.status.velocity_ff_pct == 0.0
    # The band is the same width mid-ramp as it was at the hold.
    lo, hi = h.sup.band
    assert (hi - lo) == pytest.approx(quiet_hi - quiet_lo, abs=1e-9)


def test_the_tuner_widens_the_band_and_supplies_the_drive():
    """The same instant, with the one switch flipped."""
    h = harness(tuning=True)
    h.sup.arm(h.sup.status.filtered_k)
    h.minutes(5)
    quiet_lo, quiet_hi = h.sup.band
    h.sup.set_setpoint(h.sup.status.filtered_k + 2.0, rate_k_per_min=5.0)
    h.minutes(5)

    assert h.sup.ramp_lead_pct() > 0.0
    assert h.sup.status.velocity_ff_pct > 0.0
    lo, hi = h.sup.band
    assert (hi - lo) > (quiet_hi - quiet_lo)


def test_the_feedforward_stays_off_when_the_tuner_comes_on():
    """The two switches are independent, and 4b needs exactly one of them.

    If this fails, turning the tuner on has quietly re-enabled the positional
    feedforward against a stale level -- which is the 2026-09-16 walk-down.
    """
    cfg = bench_control_config()
    h = harness(tuning=True)
    assert h.sup.tuner.enabled is True
    assert h.sup.feedforward.enabled is False
    # ...and the band centre is therefore still the fixed operating point.
    assert h.sup.band_centre_pct() == pytest.approx(
        cfg.supervisor.operating_point_pct)

    # **AND THE ASSERTION ABOVE CAN FAIL**, which at this temperature is not
    # obvious: `operating_point_pct` is 63.960 and the file's comment says that
    # is what holds 118.3 K, so a band centre that had started following the
    # setpoint would land in the same place to three figures and the test would
    # pass while the walk-down was back.  The shipped table actually reads
    # 63.9837 there, and this is the control case that says so.
    with_ff = FittedHarness(
        kelvin=BENCH_K, stage=STAGE_FILE, settled=True,
        delivered_frac=DELIVERED_FRAC,
        tuning_cfg=dataclasses.replace(cfg.tuning, enabled=True),
        ff_cfg=dataclasses.replace(cfg.feedforward, enabled=True))
    assert with_ff.sup.band_centre_pct() != pytest.approx(
        cfg.supervisor.operating_point_pct, abs=1e-3)


# -- does the setpoint arrive? --------------------------------------------

@pytest.mark.parametrize("rate", [0.2, 1.0, 5.0])
def test_4a_arrives_about_a_kelvin_late_whatever_rate_it_is_given(rate):
    """The rate is not what is binding -- the drive is.

    A +2 K move at 0.2, 1 and 5 K/min all land within 0.1 K of each other after
    forty minutes, about a kelvin short.  A test that only ran 5 K/min would
    read as "the ramp is too fast", which is the wrong diagnosis.
    """
    r = ramp(harness(tuning=False), rate_k_per_min=rate, delta_k=2.0,
             minutes=40)
    assert r["short_by_k"] > 0.8
    # Late, but never dangerous: it does not rail and it does not fault.
    assert r["railed_cycles"] == 0


@pytest.mark.parametrize("rate", [0.2, 1.0, 5.0])
def test_the_tuner_makes_the_setpoint_arrive(rate):
    """Under 0.1 K short at forty minutes, against about 1.0 K at 4a."""
    r = ramp(harness(tuning=True), rate_k_per_min=rate, delta_k=2.0,
             minutes=40)
    assert abs(r["short_by_k"]) < 0.1
    assert r["worst_error_k"] < 1.0      # below `warn_error_k`, so no alarm
    assert r["railed_cycles"] == 0
    assert r["peak_vff_pct"] > 0.0


# -- hold_speed is the hold's knob, and only the hold's --------------------

def test_hold_speed_does_not_touch_the_move_gains():
    """Which is what makes it free to raise.

    The 2026-09-16 night put 19.7 mK into the 90-240 min band against 2.6 open
    loop, and the loop's authority in that band scales as `1/hold_speed`.  So
    `hold_speed` is the knob for the hold -- and this pins that raising it
    costs a ramp nothing, because a ramp runs on `move_speed`.
    """
    slow = harness(tuning=True, hold_speed=12.0).sup.tuner
    quick = harness(tuning=True).sup.tuner
    slow_hold = slow.gains_for(BENCH_K, ControlPhase.HOLD)
    quick_hold = quick.gains_for(BENCH_K, ControlPhase.HOLD)
    assert slow_hold[0] < quick_hold[0] / 2.0
    assert (slow.gains_for(BENCH_K, ControlPhase.MOVE)
            == quick.gains_for(BENCH_K, ControlPhase.MOVE))


def test_a_slower_hold_still_ramps():
    """Raising `hold_speed` must not break the thing the tuner was turned on for."""
    r = ramp(harness(tuning=True, hold_speed=12.0), rate_k_per_min=1.0,
             delta_k=2.0, minutes=40)
    assert abs(r["short_by_k"]) < 0.2
    assert r["railed_cycles"] == 0
