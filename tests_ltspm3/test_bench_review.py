"""The phase 3 REVIEW's bench -- plans/pid-3-review.md, one section per step.

The same fitted plant and the same six temperatures as `test_bench.py`; what is
new is that the plant can be WRONG.  `FittedHarness.deliver` makes the heater
deliver less power than `P(u)` claims and `FittedHarness.sink_offset` puts the
coldplate above its locus, and between them they are the two scenarios Jeff
separated on 2026-09-15:

1. **a steady, typical-looking change the model explains** -- the bath moves,
   the loop needs less heat, and the worst case is a sample colder than
   intended.  A WARNING however far it goes, including all the way to the
   heater at its floor.
2. **a sudden, aphysical change** -- the watts stop adding up, or the loop
   rails at its ceiling and the sample still will not come up.  A FAULT and a
   ramp-down.

Every test here failed before the step named in its docstring.
"""
from __future__ import annotations

import pytest

from bench_plant import BENCH_TEMPERATURES, FittedHarness
from ltspm3.control import LoopMode, SupervisorState
from ltspm3.model.fitted_response import DRIFT_T0_UNIX

#: Where the watt residual can speak at all: above `min_output_pct`, so not
#: 10 K (24.2 % of output), and above about 40 K, so not 30 K -- tau there is
#: 9 s against a 30 s slope window.  Between them is where §3R.6's kelvin rows
#: are the only check there is.
WATT_TEMPERATURES = tuple(k for k in BENCH_TEMPERATURES if k >= 60.0)

#: Two months of drift.  The full band at 118 K goes 1.44 -> 52 mW over it.
TWO_MONTHS = 60 * 86400.0


def armed(kelvin, *, prime=60, settle=10, **kw):
    h = FittedHarness(kelvin=kelvin, **kw)
    h.settle_filter(prime)
    h.sup.set_mode(LoopMode.PID)
    h.step(settle)
    return h


def run_until_fault(h, seconds):
    """Step until the loop starts a ramp-down, or give up after ``seconds``."""
    for _ in range(int(seconds / h.DT) + 1):
        h.step(1)
        if h.sup.state in (SupervisorState.RAMPING_DOWN,
                           SupervisorState.LOCKED_OUT):
            return True
    return False


# -- 3R.1, the bench stops depending on the date ----------------------------


def test_the_band_a_level_is_judged_by_is_pinned_to_the_gauge_day():
    """3R.0.B: `sigma_q_w` carries the drift since the level was gauged, so a
    bench reading the real calendar grades a different loop every morning.

    118 K rather than a bench temperature, because that is where the numbers in
    the review's table were measured and where this cryostat has spent its
    life.  1.44 mW is 3 sigma on the gauge day; ten days later the same hold
    allows 8.7 mW and sixty days later 52.
    """
    h = armed(118.0)
    s = h.history[-1]
    assert s.missing_power_w is not None, s.residual_reason
    assert 3e3 * s.sigma_q_w == pytest.approx(1.44, abs=0.005)

    # And the pin is what holds it there: the same hold, two months on.
    old = armed(118.0, wall_t0=DRIFT_T0_UNIX + TWO_MONTHS)
    assert 3e3 * old.history[-1].sigma_q_w > 40.0


@pytest.mark.parametrize("kelvin", WATT_TEMPERATURES)
@pytest.mark.parametrize("wall_t0", [0.0, TWO_MONTHS], ids=["gauge", "+60d"])
@pytest.mark.parametrize("frac", [0.88, 0.97], ids=["12 % lost", "3 % lost"])
def test_a_heater_that_stops_delivering_faults_at_either_date(kelvin, wall_t0, frac):
    """3R.1's gate, and §3.7's wrong-on-purpose docstring finally tested.

    That docstring names this case -- "a heater delivering 12 % less power is a
    genuine fault, and must" -- and nothing exercised it: every row of that
    test perturbs the CONTROLLER's model, which is the opposite experiment.

    **The 3 % row is the one that grades this step.**  12 % is 54 to 93 mW and
    is gross enough to fault through the old dated band as well; 3 % is 17 to
    23 mW -- about five times the 2026-09-10 event -- against a full band that
    has grown to 44-60 mW at +60 d and a fast band that is still 1.4-1.6 mW.
    Measured before this step: the 3 % rows faulted at 230-264 s on the gauge
    day and never at all two months on.

    Both rows are a genuine fault by scenario 2's definition: the watts have
    stopped adding up, and the loop cannot make the power back because
    restoring it needs `1/sqrt(frac)` of the output against a band one percent
    wide.
    """
    h = armed(kelvin, wall_t0=DRIFT_T0_UNIX + wall_t0)
    h.deliver(frac)
    cfg = h.sup.cfg
    assert run_until_fault(h, cfg.fault_window_s + cfg.fault_after_s), (
        f"a {100 * (1 - frac):.0f} % power loss at {kelvin} K never faulted")
    assert any("stepped" in a or "authority exhausted" in a
               for x in h.history[-40:] for a in x.alarms)


# -- 3R.2, CRASHED survives an idle cycle -----------------------------------


@pytest.mark.parametrize("kelvin", BENCH_TEMPERATURES)
def test_a_one_off_exception_stays_crashed_until_acknowledged(kelvin):
    """3R.0.A: the latch lasted exactly one cycle.

    `_step`'s OFF-mode early return reset the state to IDLE unless it was
    LOCKED_OUT, and CRASHED was not excepted -- so one cycle after a crash the
    loop reported idle and `arm` was accepted with no `ack`, which is the whole
    point of the latch gone.

    The bench's own crash row could not see it: its sabotage (`filter = None`)
    re-crashes on every cycle, so the latch was being re-set as fast as it was
    being cleared.  This one detonates ONCE and puts the loop back exactly as
    it was -- which is the shape of a real transient, and the shape nobody has
    looked at yet either.
    """
    h = armed(kelvin)
    before = h.sup.output_pct
    real = h.sup._check_premise

    def once(*a, **kw):
        h.sup._check_premise = real           # a transient, not a broken loop
        raise RuntimeError("a one-off lurch")

    h.sup._check_premise = once
    s = h.step(1)
    assert s.state is SupervisorState.CRASHED
    assert h.sup._check_premise is real, "the sabotage did not clear itself"

    # Twenty healthy cycles later it is still latched, still says why, and
    # still has not moved the heater.
    for _ in range(20):
        s = h.step(1)
        assert s.state is SupervisorState.CRASHED
    assert "one-off lurch" in h.sup._locked_reason
    assert "ack" in s.reason
    assert h.sup.output_pct == pytest.approx(before, abs=1e-9)

    with pytest.raises(PermissionError, match="crashed"):
        h.sup.arm(kelvin)
    h.sup.acknowledge()
    assert h.sup.state is SupervisorState.IDLE
