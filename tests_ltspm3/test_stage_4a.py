"""**The configuration the cryostat is armed at**, graded on the bench.

Everything else in this directory grades the design ENVELOPE -- authority 1.0,
feedforward on, tuning on -- which is where phase 3 proved the loop and where
4c ends.  This module grades the commissioning switches instead: `stage="file"`
takes `authority_pct`, `operating_point_pct`, `feedforward.enabled` and
`tuning.enabled` from `config-ltspm3-armed.yaml` verbatim, so a change to that
file lands here as a failing assertion rather than as a surprise on the
cryostat (AUDIT-2026-09-16 finding 1).

Two kinds of test live here:

* those that grade **whatever the file says**, through `armed()`.  They have to
  keep being true of the running cryostat;
* those that reproduce **stage 4a** -- the 2026-09-16 walk-down, the band that
  does not widen, the 3 K move that is not delivered -- through `at_4a()`,
  which forces the tuner off rather than following the file.  Each documents a
  real event or a real limitation, and following the file would re-grade them
  against a different stage.

**The plant is SETTLED on a weak heater**; see `FittedHarness.equilibrate`.
"""

from __future__ import annotations

import dataclasses

import pytest
from bench_plant import DELIVERED_FRAC, STAGE_FILE, FittedHarness, bench_control_config

from ltspm3.control import SupervisorState
from ltspm3.model import fitted_response as _M

#: The temperature the cryostat has spent its life at, and what
#: `operating_point_pct` in the file is the model's answer for.
BENCH_K = 118.3


def armed(**kw):
    """A settled cryostat, at WHATEVER STAGE THE FILE SAYS, loop closed."""
    kw.setdefault("delivered_frac", DELIVERED_FRAC)
    h = FittedHarness(kelvin=BENCH_K, stage=STAGE_FILE, settled=True, **kw)
    h.sup.arm(h.sup.status.filtered_k)
    return h


def at_4a(**kw):
    """The same, pinned to **stage 4a**: the tuner off, whatever the file says.

    For the scenarios that document how the cryostat behaved at 4a rather than
    how it behaves now.  `hold_speed` is pinned with it, at the code default
    the stage ran, because the armed file's hold has been retuned since.
    """
    cfg = bench_control_config()
    kw.setdefault("tuning_cfg", dataclasses.replace(
        cfg.tuning, enabled=False, hold_speed=3.0))
    return armed(**kw)


# -- the stage itself ------------------------------------------------------

def test_stage_file_really_is_the_file():
    """If this drifts, everything using `armed()` is grading something else."""
    cfg = bench_control_config()
    h = FittedHarness(kelvin=BENCH_K, stage=STAGE_FILE)
    assert h.sup.cfg.authority_pct == cfg.supervisor.authority_pct
    assert h.sup.cfg.operating_point_pct == cfg.supervisor.operating_point_pct
    assert h.sup.feedforward.enabled is cfg.feedforward.enabled
    assert h.sup.tuner.enabled is cfg.tuning.enabled
    # And the STAGE is what those say it is, so a change to the file lands here
    # as a failing assertion rather than as a surprise on the cryostat.  The
    # tuner is on -- it reads the model's SHAPE and needs no gauge -- and the
    # feedforward, which commands its LEVEL, waits for one.
    assert h.sup.feedforward.enabled is False
    assert h.sup.tuner.enabled is True


def test_stage_4a_is_still_reachable_for_the_tests_that_reproduce_it():
    """`at_4a()` must not follow the file, or the three below stop meaning it."""
    h = at_4a()
    assert h.sup.tuner.enabled is False
    assert h.sup.feedforward.enabled is False


def test_a_settled_plant_is_below_the_model_s_answer_for_its_own_output():
    """The state the cryostat was in on 2026-09-16 and the harness could not make.

    The sign is what matters: the model's answer for the output the heater is
    already at is WARMER than the sample, so a loop that drives to the model's
    answer drives DOWN.
    """
    h = FittedHarness(kelvin=BENCH_K, stage=STAGE_FILE, settled=True,
                      delivered_frac=DELIVERED_FRAC)
    settled = h.equilibrium_k
    # Where the plant must come to rest, derived rather than quoted: the
    # temperature whose steady power equals the power this heater actually
    # delivers at the output the nominal curve chose.  50 mK for the
    # integration's own convergence.
    want = _M.steady_temperature_k(DELIVERED_FRAC * _M.power_w(h.bench_pct))
    assert settled == pytest.approx(want, abs=0.05)
    # And that is over a kelvin of model error -- enough to matter at
    # `warn_error_k` 1.0, which is why this state is the one to arm on.
    assert h.sup.feedforward.kelvin_for(h.sup.output_pct) - settled > 1.0


# -- finding 1: the 13:35 walk-down, reproduced ----------------------------

@pytest.mark.parametrize("authority", [0.1, 0.25])
def test_feedforward_on_walks_the_heater_down_at_either_band_width(authority):
    """**The 2026-09-16 walk-down**, on the virtual clock.

    Armed where the cryostat was, with the positional feedforward on: the term
    is referenced at arming against a model whose LEVEL is stale, so the loop
    commands the model's answer for the output rather than the one the sample
    actually needs, and walks the sample down with it.  The band's width does
    not save it at either 0.1 or 0.25, because the walk is towards the band's
    own centre -- the model's answer -- and well inside both.

    Nothing faults and nothing rails: this is a loop doing exactly what it was
    configured to do, which is why it took a trace and not an alarm to find.
    """
    cfg = bench_control_config()
    h = at_4a(sup_cfg=dataclasses.replace(cfg.supervisor,
                                          authority_pct=authority),
              ff_cfg=dataclasses.replace(cfg.feedforward, enabled=True))
    at_arm = h.sup.status.filtered_k
    five = h.minutes(5)
    forty = h.minutes(35)
    assert five.filtered_k - at_arm < -0.35
    assert forty.filtered_k - at_arm < -0.5
    # **Down, and towards the model's stale answer** -- which identifies the
    # mechanism rather than merely the symptom.  It is still on its way there
    # at forty minutes, because unwinding an integral at `ti = 900 s` is the
    # slow half of what happened on the day.
    stale = h.sup.feedforward.percent_for(at_arm)
    held = h.sup.cfg.operating_point_pct
    assert stale < forty.output_pct < held
    assert forty.state is SupervisorState.TRACKING


@pytest.mark.parametrize("authority", [0.25, 1.0])
def test_the_armed_stage_holds_where_it_was_armed(authority):
    """Feedforward OFF -- the file as committed -- and the loop simply holds:
    nothing references a stale level at arming.

    This is 4a's gate in miniature: an hour inside `warn_error_k`, `tracking`
    throughout.

    **The band is centred on the MODEL's answer for the setpoint** (rule 5,
    whether or not the feedforward term is on), and the model's answer is not
    where this cryostat actually holds -- it carries the level error the model
    is allowed to carry (see `_fitted_table`'s header).  So the half-width has
    to cover that error or the ceiling cuts the heater at arming, which is the
    test below and why 0.1 is not in the list here.
    """
    cfg = bench_control_config()
    h = armed(sup_cfg=dataclasses.replace(cfg.supervisor,
                                          authority_pct=authority))
    at_arm = h.sup.status.filtered_k
    worst = 0.0
    for _ in range(60):
        st = h.minutes(1)
        assert st.state is SupervisorState.TRACKING
        worst = max(worst, abs(st.filtered_k - at_arm))
    assert worst < cfg.supervisor.warn_error_k
    # And not merely inside the warning: a hold that only just cleared
    # `warn_error_k` would pass the line above while being a tenth of a kelvin
    # from alarming every hour.  A tenth of the warning is the margin this
    # stage was accepted on.
    assert worst < 0.1 * cfg.supervisor.warn_error_k


def test_a_band_narrower_than_the_level_error_cuts_the_heater_at_arming():
    """Why `authority_pct` is sized to the model's calibration envelope.

    With the centre on the model's answer, a half-width of 0.1 % puts the
    ceiling BELOW the output this settled cryostat is already at, because the
    level error the model is allowed to carry (see `_fitted_table`'s header) is
    larger than that.  The first cycle then cuts the heater to the ceiling and
    the sample falls -- less heat is the safe direction, so nothing faults --
    but the loop has been made to leave a hold that was fine.  Rule 5's ceiling
    is doing what it says; the number under it was wrong.

    The shipped file's half-width covers that level error with room for the
    overdrive a fast move needs on top (docs/ltspm3/requirements.md §3).
    """
    cfg = bench_control_config()
    h = armed(sup_cfg=dataclasses.replace(cfg.supervisor, authority_pct=0.1))
    at_arm = h.sup.status.filtered_k
    at_arm_pct = h.sup.output_pct
    st = h.minutes(30)
    lo, hi = h.sup.band
    assert hi < at_arm_pct
    assert st.output_pct <= hi + 1e-9
    assert st.filtered_k - at_arm < -0.1
    assert st.state is SupervisorState.TRACKING


# -- finding 2: what the fault ramp-down costs at this stage ---------------

#: 118 K to base at `ramp.max_rate_k_per_min`, which is what
#: `docs/ltspm3/control.md` promises for a fault descent at this stage; the
#: descent took the no-curve branch and its 0.20 %/min floor instead until the
#: `has_curve` seam landed (AUDIT-2026-09-16 finding 2).
#:
#: The tolerance is two minutes because the rule is written in kelvin and
#: applied in percent through a curve: the sample falls at the one rate only to
#: the accuracy of the local gain the descent converts with, and the assertion
#: on the rate itself below is the statement that matters.
RAMPDOWN_MINUTES = 24.0
RAMPDOWN_TOL_MIN = 2.0


def test_the_fault_ramp_down_walks_the_curve_down_at_the_one_rate():
    h = armed()
    h.minutes(2)
    h.cryostat.inject(dropout_channels={"218.1"})

    t_start = None
    from_pct = None
    for _ in range(400_000):
        h.step(1)
        if t_start is None and h.sup.state is SupervisorState.RAMPING_DOWN:
            t_start, from_pct = h.clock.t, h.sup.output_pct
        if h.sup.state is SupervisorState.LOCKED_OUT:
            break

    assert h.sup.state is SupervisorState.LOCKED_OUT, "never finished"
    assert from_pct == pytest.approx(63.98, abs=0.05)
    minutes = (h.clock.t - t_start) / 60.0
    assert minutes == pytest.approx(RAMPDOWN_MINUTES, abs=RAMPDOWN_TOL_MIN)
    # And it is the ONE RATE that sets it, stated as the arithmetic rather than
    # as a duration so the reason sits next to the number: the sample was taken
    # from where it was to the bottom of the table at `max_rate_k_per_min`.
    span_k = h.sup.feedforward.kelvin_for(from_pct) - _M.T_MIN_K
    assert span_k / minutes == pytest.approx(
        h.sup.ramp.cfg.max_rate_k_per_min, rel=0.15)
    # Emphatically NOT the floor rate, which is what this took before the
    # `has_curve` seam and is 13x longer.
    assert minutes < from_pct / h.sup.cfg.min_rate_pct_per_min / 10.0


# -- finding 3: the output rate limiter, and what it is told ---------------

@pytest.mark.parametrize("stage", ["4a", "file"])
def test_the_output_rate_limiter_converts_the_one_rate_at_either_stage(stage):
    """The conversion needs the SCHEDULE; `tuning.enabled` says whether to
    reschedule the GAINS.  Two questions, and they were one switch.

    On the floor it was 0.20 %/min -- **2.6 K/min at 118 K**, against the 5 the
    file's `ramp.max_rate_k_per_min` names, and 0.6 K/min at 30 K.
    AUDIT-2026-09-16 finding 3.

    **Asserted at BOTH stages**, which is what the finding actually claims:
    the limiter asks `has_curve` -- is there a curve to convert
    with -- and NOT whether this stage's tuner is switched on.  Running it only
    at whatever the file happens to say would have let the tuner's arrival hide
    a regression back to the floor, and the floor is a five-hour fault
    ramp-down.
    """
    h = at_4a() if stage == "4a" else armed()
    assert h.sup.tuner.enabled is (stage != "4a")
    gain = h.sup.schedule.gain_at(BENCH_K)
    one_rate = h.sup.ramp.cfg.max_rate_k_per_min / gain
    assert one_rate == pytest.approx(0.40, abs=0.02)
    assert h.sup._rate_pct_per_min(BENCH_K) == pytest.approx(one_rate)
    assert h.sup._rate_pct_per_min(BENCH_K) > h.sup.cfg.min_rate_pct_per_min
    # The floor still governs where the model has nothing to say.
    assert h.sup._rate_pct_per_min(None) == pytest.approx(
        h.sup.cfg.min_rate_pct_per_min)


def test_the_band_still_does_not_widen_during_a_ramp_at_this_stage():
    """**The conservative half of the same conflation, left in place.**

    `ramp_lead_pct` widens the AUTHORITY BAND, which is the one direction that
    grants the loop more heater than it had, so it stays gated on
    `tuner.enabled` where the ramp-down and the rate limiter no longer are.
    Both of those were failing slow, and slow is the safe side of rule 1.
    """
    h = at_4a()
    h.minutes(2)
    h.sup.set_setpoint(h.sup.pid.cfg.setpoint + 3.0)
    h.minutes(1)
    assert abs(h.sup.smoother.rate_k_per_s) > 0.0, "not actually ramping"
    assert h.sup.ramp_lead_pct() == 0.0
    lo, hi = h.sup.band
    assert hi - lo == pytest.approx(2 * h.sup.cfg.authority_pct)


def test_a_three_kelvin_setpoint_move_is_not_delivered_at_five_k_per_min():
    """`set_setpoint` ramps in KELVIN at `max_rate_k_per_min` and the output
    limiter cannot follow, so the error stands long after the trajectory is
    over.

    The smoothed setpoint has arrived within two minutes and half an hour
    later the sample has delivered less than two of the three kelvin asked
    for -- more than `warn_error_k` of error, standing.  The limiter is only
    half of that at this stage; the gains are the other half, since
    `tuning.enabled: false` leaves `kp` at the file's starting value rather
    than the scheduled one.  Both halves are the same switch conflation.
    """
    h = at_4a()
    h.minutes(2)
    sp0 = h.sup.pid.cfg.setpoint
    start_k = h.sup.status.filtered_k
    h.sup.set_setpoint(sp0 + 3.0)

    after_2 = h.minutes(2)
    assert h.sup.smoother.value == pytest.approx(sp0 + 3.0, abs=0.1)
    assert after_2.filtered_k - start_k < 0.5

    after_30 = h.minutes(28)
    assert after_30.filtered_k - start_k < 2.0      # 3 K asked for
    assert after_30.state is SupervisorState.TRACKING
