"""**A setpoint past the top of the model's table**, 2026-09-30.

The table ends at `T_MAX_K` and CLAMPS there: the band's centre for any
setpoint above it is the output that holds the top, so every kelvin above
the table has to come out of the half-width.  At the table's last slope a
kelvin up there costs about 0.08 % of output, so `authority_pct` 1.0 runs
out about 12 K past the top -- and a rail at the ceiling with the error past
`fault_error_k` is a fault ramp-down, for asking for 25 K more.  (When this
was written the top was 195 K; it is wherever the extension in
`analysis/extend_table.py` has carried it, and everything below is relative.)

`SupervisorConfig.authority_beyond_table_pct_per_k` is the fix: extra
half-width per kelvin the setpoint sits past the table's ends, zero by default.
It is a WIDENING, not an extrapolation of the centre -- the model still says
nothing past its edge -- and `hard_max_pct` still caps it.

The plant cannot follow: `FittedResponse` clamps its own state at `T_MAX_K`
for the same reason the centre does.  So these grade the BAND the loop is
granted for such a setpoint, which is the whole of the change, and not a move
that a fictional plant would have to complete.
"""

from __future__ import annotations

import dataclasses

import pytest
from bench_plant import DELIVERED_FRAC, STAGE_FILE, FittedHarness, bench_control_config

from ltspm3.model import fitted_response as _M

#: What the armed file should carry, and what the table's last slope sizes:
#: about twice the ~0.08 %/K a kelvin costs at the top, because the slope is
#: rising and nobody has measured it.  Read by `test_the_recommended_value_
#: covers_a_linear_extrapolation_to_300_k`, which is what makes it a number
#: rather than a guess.
RECOMMENDED_PCT_PER_K = 0.15

#: Just inside the top of the table, where the walk past it starts.
EDGE_K = _M.T_MAX_K - 5.0
#: How far past the top the tests ask for.
FAR_K = _M.T_MAX_K + 25.0
NEAR_K = _M.T_MAX_K + 15.0


def _cfgs(per_k: float):
    cfg = bench_control_config()
    sup = dataclasses.replace(cfg.supervisor,
                              authority_beyond_table_pct_per_k=per_k)
    # The curve's own ceiling must not be the thing that clamps the centre:
    # the class default of 70 sits a quarter-percent UNDER the model's answer
    # at the top of the table.
    ff = dataclasses.replace(cfg.feedforward, max_pct=cfg.supervisor.hard_max_pct)
    return sup, ff


def armed_near_the_top(per_k: float, **kw):
    sup, ff = _cfgs(per_k)
    h = FittedHarness(kelvin=EDGE_K, stage=STAGE_FILE, settled=True,
                      delivered_frac=DELIVERED_FRAC, sup_cfg=sup, ff_cfg=ff, **kw)
    h.sup.arm(h.sup.status.filtered_k)
    return h


def _chase(h, target_k: float, minutes: float = 8.0) -> float:
    """Command a setpoint and run until the smoothed one has arrived past the
    table, whatever the plant does.  Returns the setpoint being chased."""
    h.sup.set_setpoint(target_k)
    h.minutes(minutes)
    chased = h.sup.pid.cfg.setpoint
    assert chased > _M.T_MAX_K, f"setpoint never left the table: {chased:.2f} K"
    return chased


def _centre_at_the_edge(h) -> float:
    return h.sup.feedforward.percent_for(_M.T_MAX_K)


def test_inside_the_table_the_knob_changes_nothing():
    """A setpoint the model was fitted over gets the band it always had."""
    with_it = armed_near_the_top(RECOMMENDED_PCT_PER_K)
    without = armed_near_the_top(0.0)
    for h in (with_it, without):
        h.minutes(3)
    assert with_it.sup.beyond_table_pct() == 0.0
    assert with_it.sup.band == pytest.approx(without.sup.band, abs=1e-9)


def test_by_default_a_setpoint_past_the_table_is_chased_from_the_edge():
    """The old behaviour, pinned: centre at the edge, half-width unchanged."""
    h = armed_near_the_top(0.0)
    chased = _chase(h, FAR_K)
    assert h.sup.beyond_table_pct() == 0.0
    lo, hi = h.sup.band
    centre = _centre_at_the_edge(h)
    assert h.sup.band_centre_pct() == pytest.approx(centre, abs=1e-9)
    assert hi == pytest.approx(centre + h.sup.cfg.authority_pct + h.sup.ramp_lead_pct(),
                               abs=1e-9)
    # ...and that ceiling is BELOW what a linear read of the table's last
    # slope says 220 K needs, which is why this used to fault.
    assert hi < _need_pct(FAR_K), (chased, hi, _need_pct(FAR_K))


def test_past_the_table_the_band_widens_per_kelvin_and_the_centre_does_not_move():
    h = armed_near_the_top(RECOMMENDED_PCT_PER_K)
    chased = _chase(h, FAR_K)
    beyond = h.sup.beyond_table_pct()
    assert beyond == pytest.approx(RECOMMENDED_PCT_PER_K * (chased - _M.T_MAX_K))
    lo, hi = h.sup.band
    centre = _centre_at_the_edge(h)
    assert h.sup.band_centre_pct() == pytest.approx(centre, abs=1e-9)
    assert hi == pytest.approx(
        min(h.sup.cfg.hard_max_pct,
            centre + h.sup.cfg.authority_pct + h.sup.ramp_lead_pct() + beyond),
        abs=1e-9)
    assert hi > _need_pct(FAR_K)


def test_the_hard_ceiling_still_caps_it():
    """Rule 5: `hard_max_pct` is the one cap nothing moves, this included."""
    h = armed_near_the_top(5.0)          # absurdly generous on purpose
    _chase(h, _M.T_MAX_K + 45.0)
    lo, hi = h.sup.band
    assert hi == pytest.approx(h.sup.cfg.hard_max_pct)
    assert lo <= hi


def test_the_widening_is_granted_to_the_setpoint_not_to_the_reading():
    """The plant is pinned at the table's edge; the widening follows what the
    loop is CHASING, exactly as the centre does, so the two cannot disagree."""
    h = armed_near_the_top(RECOMMENDED_PCT_PER_K)
    chased = _chase(h, NEAR_K)
    assert h.sup.status.filtered_k <= _M.T_MAX_K + 0.5
    assert h.sup.beyond_table_pct() == pytest.approx(
        RECOMMENDED_PCT_PER_K * (chased - _M.T_MAX_K))


def _need_pct(kelvin: float) -> float:
    """What holding ``kelvin`` costs if the table's last slope simply
    continues -- the ONLY estimate there is past the table, and a low one,
    because Lambda' is still rising at the edge."""
    q = (_M.steady_power_w(_M.T_MAX_K)
         + _M.lambda_slope_w_per_k(_M.T_MAX_K) * (kelvin - _M.T_MAX_K))
    return _M.percent_for_power(q)


@pytest.mark.parametrize("kelvin", [_M.T_MAX_K + 5.0, _M.T_MAX_K + 25.0,
                                    _M.T_MAX_K + 50.0, 300.0])
def test_the_recommended_value_covers_a_linear_extrapolation_to_300_k(kelvin):
    """The number in the armed file, tied to the table rather than decreed.

    At a HOLD (no ramp lead) the band the recommended value grants must
    contain what the table's own last slope says the temperature needs, with
    room to spare for the slope being an underestimate -- and must do so all
    the way to the 300 K the requirements name, or be capped there by
    `hard_max_pct` with the need still inside.
    """
    cfg = bench_control_config()
    sup = cfg.supervisor
    centre = _M.percent_for_power(_M.steady_power_w(_M.T_MAX_K))
    granted = min(sup.hard_max_pct,
                  centre + sup.authority_pct
                  + RECOMMENDED_PCT_PER_K * (kelvin - _M.T_MAX_K))
    need = _need_pct(kelvin)
    assert need < sup.hard_max_pct, "300 K is not reachable under the ceiling"
    assert granted - need >= 0.5, (kelvin, granted, need)
