"""What the bench takes from the armed config, and what it pins itself.

Two kinds of number live in `config-ltspm3-armed.yaml`, and only one kind
belongs to the harness.

`hard_max_pct`, the rates and the guard's thresholds are properties of the
**cryostat**, and reading them from the file is what makes the bench grade the
numbers the cryostat actually runs.  `authority_pct` and `feedforward.enabled`
are where **commissioning** has got to: 4a arms at 0.1 with feedforward off,
4c ends at 1.0 with it on, and it is the same cryostat both times.

Coupling the second kind to the file made the file mean two things at once.
Narrowing it to 4a's numbers failed 26 bench scenarios -- the 10-180 K sweeps
rail against +/-0.1 % by construction -- so the operational values could not be
committed, and the working configuration sat uncommitted in a tree, one
`git checkout` away from restoring the feedforward that cost 350 mK on
2026-09-16.  These pin the split so it cannot quietly close again.
"""

import dataclasses

from bench_plant import (BENCH_AUTHORITY_PCT, BENCH_FEEDFORWARD, FittedHarness,
                         bench_control_config)


def test_the_stage_switches_are_pinned_whatever_the_file_is_armed_at():
    h = FittedHarness(kelvin=118.0)
    assert h.sup.cfg.authority_pct == BENCH_AUTHORITY_PCT
    assert h.sup.feedforward.enabled is BENCH_FEEDFORWARD


def test_the_cryostat_s_own_limits_still_come_from_the_file():
    """The half that must NOT be pinned, or the bench stops grading this
    cryostat and starts grading a literal in a test."""
    h = FittedHarness(kelvin=118.0)
    cfg = bench_control_config()
    assert h.sup.cfg.hard_max_pct == cfg.supervisor.hard_max_pct
    assert h.sup.cfg.hard_min_pct == cfg.supervisor.hard_min_pct
    assert h.sup.cfg.min_rate_pct_per_min == cfg.supervisor.min_rate_pct_per_min
    assert h.sup.cfg.warn_error_k == cfg.supervisor.warn_error_k
    assert h.sup.cfg.fault_error_k == cfg.supervisor.fault_error_k


def test_the_pinning_is_a_default_and_a_test_can_still_choose():
    """`sup_cfg` wins, exactly as before -- this is a default, not a clamp."""
    cfg = bench_control_config()
    sup = dataclasses.replace(cfg.supervisor, operating_point_pct=63.96,
                              authority_pct=0.25)
    h = FittedHarness(kelvin=118.0, sup_cfg=sup)
    assert h.sup.cfg.authority_pct == 0.25


def test_the_armed_config_is_free_to_ship_its_commissioning_stage():
    """The point of the whole exercise: whatever stage the file is armed at,
    the bench still grades the envelope, so the file can be committed."""
    cfg = bench_control_config()
    # No assertion on the file's VALUES -- that would just re-couple them in a
    # test.  What matters is that the harness is unmoved by whatever it says.
    h = FittedHarness(kelvin=118.0)
    assert h.sup.cfg.authority_pct == BENCH_AUTHORITY_PCT
    assert h.sup.feedforward.enabled is BENCH_FEEDFORWARD
    assert cfg.supervisor.authority_pct is not None      # it exists; it is not read
