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

* the scheduled `(kp, ti)` -- `move_speed` is a faster closed loop than
  `hold_speed`, so a move gets the larger `kp`;
* the **velocity feedforward** (`supervisor.py`, `_step`), which is the drive
  `rate*tau/K` a ramp needs and which no amount of integral action supplies in
  time;
* the **band widening** (`ramp_lead_pct`), so the authority to use that drive
  exists.

With `tuning.enabled: false` all three are zero, and the loop has to drag the
sample with P+I inside a band that never widens.  It does not rail and it does
not fault -- it simply arrives late by about a kelvin whatever rate it is
given, because the rate is not what is binding.  That is what these tests pin.

**`tuning` and `feedforward` are not the same dependency** -- see
`test_the_feedforward_stays_off_when_the_tuner_comes_on` below, and
`config-ltspm3-armed.yaml`'s `feedforward:` block for why the level waits on a
gauge while the shape does not.
"""

from __future__ import annotations

import dataclasses

import pytest
from bench_plant import DELIVERED_FRAC, STAGE_FILE, FittedHarness, bench_control_config

from ltspm3.control.tuning import ControlPhase, simc_pi

#: Same cryostat as the other stage tests -- `DELIVERED_FRAC`, settled on it.
#: Sharing it matters: a ramp test on a healthier cryostat than the hold tests
#: run on would be grading two things at once.
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
    """**Rule 5 is not suspended by the feedforward switch.**

    The centre used to ask `feedforward.enabled`, and with it off -- the armed
    configuration -- the band sat pinned at `operating_point_pct`, so a
    setpoint a few kelvin from where the loop was armed railed against a window
    that never moved.  It asks `has_curve` now, the same seam the ramp-down and
    the output rate limiter use.

    Asserted where the two answers DIFFER.  At the operating point the model's
    answer and `operating_point_pct` are only hundredths of a percent apart
    (the first assertion below pins that they are not the same number at all,
    which is what makes the comparison meaningful), so a test there alone could
    pass either way; the move to 140 K is where a pinned band and a following
    band come apart by percents.
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

#: A 2 K move, and "arrived" is 5 % of it -- the same criterion as
#: `test_stage_4d_fast_move.py`'s.
MOVE_K = 2.0
ARRIVE_K = 0.05 * MOVE_K


@pytest.mark.parametrize("rate", [0.2, 1.0, 5.0])
def test_4a_arrives_about_a_kelvin_late_whatever_rate_it_is_given(rate):
    """The rate is not what is binding -- the drive is.

    A +2 K move at three rates spanning a factor of 25 all land in the same
    place after forty minutes, most of a kelvin short.  A test that only ran
    5 K/min would read as "the ramp is too fast", which is the wrong diagnosis.
    """
    cfg = bench_control_config()
    r = ramp(harness(tuning=False), rate_k_per_min=rate, delta_k=MOVE_K,
             minutes=40)
    # The defect, stated against the thing it would set off: the standing error
    # is most of `warn_error_k`, so this stage sits just under an alarm forever
    # rather than arriving.
    assert r["short_by_k"] > 0.8 * cfg.supervisor.warn_error_k
    # Late, but never dangerous: it does not rail and it does not fault.
    assert r["railed_cycles"] == 0


@pytest.mark.parametrize("rate", [0.2, 1.0, 5.0])
def test_the_tuner_makes_the_setpoint_arrive(rate):
    """Arrived, by the 5 % criterion, where 4a stood most of a kelvin short."""
    cfg = bench_control_config()
    r = ramp(harness(tuning=True), rate_k_per_min=rate, delta_k=MOVE_K,
             minutes=40)
    assert abs(r["short_by_k"]) < ARRIVE_K
    # And nothing warned on the way: `warn_error_k` is the alarm this move has
    # to stay under at every cycle, not merely at the end.
    assert r["worst_error_k"] < cfg.supervisor.warn_error_k
    assert r["railed_cycles"] == 0
    assert r["peak_vff_pct"] > 0.0


# -- hold_speed is the hold's knob, and only the hold's --------------------

#: Two hold speeds, both explicit, neither read from the file -- the file's
#: number moves with commissioning, and comparing it against itself would pass
#: on nothing.  `SLOW_HOLD` is weaker than the plant and `QUICK_HOLD` is the
#: code default; the assertions below hold for any ordered pair.
SLOW_HOLD, QUICK_HOLD = 12.0, 3.0


def test_hold_speed_does_not_touch_the_move_gains():
    """Which is what makes it free to retune.

    `hold_speed` is the knob for the hold -- the loop's authority at long
    averaging times scales as `1/hold_speed` (docs/ltspm3/requirements.md §3)
    -- and this pins that changing it costs a ramp nothing, because a ramp runs
    on `move_speed`.
    """
    slow = harness(tuning=True, hold_speed=SLOW_HOLD).sup.tuner
    quick = harness(tuning=True, hold_speed=QUICK_HOLD).sup.tuner
    slow_kp, _ = slow.gains_for(BENCH_K, ControlPhase.HOLD)
    quick_kp, _ = quick.gains_for(BENCH_K, ControlPhase.HOLD)

    # The bound is `simc_pi`'s own arithmetic rather than a factor somebody
    # chose: kp = tau / (K (speed*tau + delay)), so the two hold gains stand in
    # the ratio of their closed-loop time constants and nothing else.  Asked of
    # the rule directly, so a change to the tuning law fails here rather than
    # being absorbed by a loose inequality.
    gain = quick.schedule.gain_at(BENCH_K)
    tau = quick.schedule.tau_at(BENCH_K)
    for tuner, speed, kp in ((slow, SLOW_HOLD, slow_kp),
                             (quick, QUICK_HOLD, quick_kp)):
        want, _ = simc_pi(gain, tau, tuner.tau_cl_for(ControlPhase.HOLD, BENCH_K),
                          tuner.delay_s,
                          min_ti_s=tuner.cfg.min_ti_delays * tuner.delay_s)
        assert kp == pytest.approx(want, rel=1e-9), speed
    assert slow_kp < quick_kp, "a slower hold must not be the stronger loop"

    # And the move gains are untouched, which is the point of the test.
    assert (slow.gains_for(BENCH_K, ControlPhase.MOVE)
            == quick.gains_for(BENCH_K, ControlPhase.MOVE))


def test_a_slower_hold_still_ramps():
    """Retuning `hold_speed` must not break the thing the tuner was turned on for.

    Twice the arrival window, not the window itself: once the trajectory is
    over the phase falls back to HOLD, so the last of the approach is made on
    the weak hold gains.  What is graded here is that the ramp still delivers
    the move, not that a deliberately weak hold closes it as tightly.
    """
    r = ramp(harness(tuning=True, hold_speed=SLOW_HOLD), rate_k_per_min=1.0,
             delta_k=MOVE_K, minutes=40)
    assert abs(r["short_by_k"]) < 2 * ARRIVE_K
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
