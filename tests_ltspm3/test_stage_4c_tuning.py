"""**Can this loop move a setpoint?**  At 4a it cannot, and the reason is one switch.

**This is the TUNING step of 4c** -- "widen to 1.0 %, then tuning, then
feedforward, one per watched hour" in plans/pid-4-commissioning.md.  4b in that
plan is the provoked fault at low temperature and is a different thing
entirely.

`test_stage_4a.py` already pins that a 3 K move is not delivered at 5 K/min and
names the switch conflation behind it
(`test_a_three_kelvin_setpoint_move_is_not_delivered_at_five_k_per_min`).  What
is new here is the other half: what flipping that switch actually buys, at
three rates, and that the two switches can be flipped independently.

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
    h = harness(tuning=True)
    assert h.sup.tuner.enabled is True
    assert h.sup.feedforward.enabled is False
    # The positional TERM is what the switch governs.  Off, the PID's
    # feedforward contribution is zero however far the setpoint is from the
    # model's answer for the present output.
    assert h.sup.pid.feedforward is None or not h.sup.pid.feedforward.enabled


def test_the_band_follows_the_setpoint_with_the_feedforward_off():
    """**Rule 5 is not suspended by the feedforward switch** (2026-09-17).

    Until 2026-09-17 the centre asked `feedforward.enabled`, and with it off
    -- the armed configuration -- the band sat pinned at
    `operating_point_pct`.  A setpoint more than about 3 K from where the loop
    was armed then railed against a window that never moved: bench, a +2 K
    move at 140 K faulted `authority exhausted`.  The centre now asks
    `has_curve`, the same seam the ramp-down and the rate limiter use.

    Asserted where the two answers DIFFER.  At 118.3 K `operating_point_pct`
    is 63.960 and the shipped table reads 63.9837, so a test at the operating
    point alone could pass either way; the move to 140 K is where a pinned
    band and a following band come apart by two whole percent.
    """
    cfg = bench_control_config()
    h = harness(tuning=True)
    assert h.sup.feedforward.enabled is False
    assert h.sup.band_centre_pct() == pytest.approx(
        h.sup.feedforward.percent_for(h.sup.pid.cfg.setpoint), abs=1e-9)
    assert h.sup.band_centre_pct() != pytest.approx(
        cfg.supervisor.operating_point_pct, abs=1e-3)

    h.sup.arm(h.sup.status.filtered_k)
    h.minutes(5)
    before = h.sup.band_centre_pct()
    h.sup.set_setpoint(140.0)
    h.minutes(30)
    after = h.sup.band_centre_pct()
    assert after - before > 1.5
    assert after == pytest.approx(h.sup.feedforward.percent_for(h.sup.pid.cfg.setpoint),
                                  abs=1e-9)


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
    # Both explicit.  `harness(tuning=True)` reads `hold_speed` from the file,
    # and since 2026-09-17 the file says 12 -- so comparing against it would
    # have been comparing 12 with 12 and passing on neither.
    slow = harness(tuning=True, hold_speed=12.0).sup.tuner
    quick = harness(tuning=True, hold_speed=3.0).sup.tuner
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


# -- and `check` has to SAY which of the two it is -------------------------

def _check_output(capsys, path):
    import lschart.__main__ as cli

    import ltspm3.config  # noqa: F401  -- registers `control:`
    assert cli.main(["-c", str(path), "check"]) == 0
    return capsys.readouterr().out


def test_check_says_the_gains_are_scheduled(capsys, tmp_path):
    """**Three behaviours hang off `tuning.enabled` and `check` printed none.**

    The band line has said which band it is since AUDIT-2026-09-16 finding 7.
    This is the same argument one switch over: somebody deciding whether to arm
    can now read whether a ramp will get its drive, rather than finding out
    forty minutes into one.
    """
    from bench_plant import BENCH_CONFIG

    out = _check_output(capsys, BENCH_CONFIG)
    assert "gain schedule  : ON" in out
    assert "velocity feedforward" in out

    # And the other branch, from the same file with the one key flipped -- so
    # this cannot pass by agreeing with whatever the file happens to say.
    #
    # Scoped to the `tuning:` block deliberately.  A bare replace of the first
    # `enabled: true` rewrites the 218's, and the load then fails with
    # "control.enabled requires ls218.enabled" rather than exercising anything.
    head, sep, tail = BENCH_CONFIG.read_text(encoding="utf-8").partition(
        "\n  tuning:\n")
    assert sep, "the `tuning:` block moved; this test is rewriting the wrong key"
    assert "\n    enabled: true\n" in tail
    off = tmp_path / "tuning-off.yaml"
    off.write_text(head + sep + tail.replace("\n    enabled: true\n",
                                             "\n    enabled: false\n", 1),
                   encoding="utf-8")
    out = _check_output(capsys, off)
    assert "gain schedule  : OFF" in out
    assert "arrives late at any rate" in out
