"""What the bench takes from the armed config, and what it pins itself.

Two kinds of number live in `config-ltspm3-armed.yaml`, and only one kind
belongs to the harness.

`hard_max_pct`, the rates and the guard's thresholds are properties of the
**cryostat**, and reading them from the file is what makes the bench grade the
numbers the cryostat actually runs.  `authority_pct`, `feedforward.enabled` and
`tuning.enabled` are where **commissioning** has got to: 4a arms at 0.1 with
both off, 4c ends at 1.0 with both on, and it is the same cryostat every time.

Coupling the second kind to the file made the file mean two things at once.
Narrowing it to 4a's numbers failed 26 bench scenarios -- the 10-180 K sweeps
rail against +/-0.1 % by construction -- so the operational values could not be
committed, and the working configuration sat uncommitted in a tree, one
`git checkout` away from restoring the feedforward that cost 350 mK on
2026-09-16.  These pin the split so it cannot quietly close again.

**Both halves are asked by MOVING the file's answer** rather than by reading it
twice.  Two of these compared the harness against the same file the harness had
just read, which pins "not overridden" and passes equally if the harness ignores
the file and the file happens to hold the same numbers -- and the fourth ended
in an assertion that could not fail at all (AUDIT-2026-09-16 finding 8).

`tests_ltspm3/test_stage_4a.py` is the other side of this split: `stage="file"`
grades the switches the cryostat is actually armed at, which nothing did.
"""

import dataclasses

import bench_plant
from bench_plant import (BENCH_AUTHORITY_PCT, BENCH_FEEDFORWARD, BENCH_TUNING,
                         FittedHarness, bench_control_config)


def test_the_stage_switches_are_pinned_whatever_the_file_is_armed_at():
    h = FittedHarness(kelvin=118.0)
    assert h.sup.cfg.authority_pct == BENCH_AUTHORITY_PCT
    assert h.sup.feedforward.enabled is BENCH_FEEDFORWARD
    # The third one, which was invisible until 2026-09-16: `Harness` passed no
    # `tuning_config` at all, so this was pinned by accident rather than on
    # purpose -- `TuningConfig()` defaults to enabled.
    assert h.sup.tuner.enabled is BENCH_TUNING


def test_the_cryostat_s_own_limits_still_come_from_the_file(monkeypatch):
    """The half that must NOT be pinned, or the bench stops grading this
    cryostat and starts grading a literal in a test.

    **Asked by moving the file's answer**, for the same reason as below:
    comparing two reads of the same file pins "not overridden", and would pass
    equally if the harness ignored the file and the file happened to hold the
    defaults.
    """
    cfg = bench_control_config()
    moved = dataclasses.replace(cfg, supervisor=dataclasses.replace(
        cfg.supervisor, hard_max_pct=55.0, min_rate_pct_per_min=0.33,
        warn_error_k=2.5, fault_error_k=7.5))
    app = bench_plant.bench_app_config()
    app.extensions["control"] = moved
    monkeypatch.setattr(bench_plant, "bench_app_config", lambda: app)

    h = FittedHarness(kelvin=118.0)
    assert h.sup.cfg.hard_max_pct == 55.0
    assert h.sup.cfg.min_rate_pct_per_min == 0.33
    assert h.sup.cfg.warn_error_k == 2.5
    assert h.sup.cfg.fault_error_k == 7.5
    assert h.sup.cfg.hard_min_pct == cfg.supervisor.hard_min_pct


def test_the_pinning_is_a_default_and_a_test_can_still_choose():
    """`sup_cfg` wins, exactly as before -- this is a default, not a clamp."""
    cfg = bench_control_config()
    sup = dataclasses.replace(cfg.supervisor, operating_point_pct=63.96,
                              authority_pct=0.25)
    h = FittedHarness(kelvin=118.0, sup_cfg=sup)
    assert h.sup.cfg.authority_pct == 0.25


def test_the_armed_config_is_free_to_ship_its_commissioning_stage(monkeypatch):
    """The point of the whole exercise: whatever stage the file is armed at,
    the bench still grades the envelope, so the file can be committed.

    **Asked by MOVING the file's answer**, not by reading it twice.  This used
    to assert the same two lines as the first test and then
    `cfg.supervisor.authority_pct is not None`, which cannot fail
    (AUDIT-2026-09-16 finding 8): it would have passed just as well if the
    harness read the file and the file happened to hold the envelope's own
    numbers.  Here the file says 4a and the harness still says envelope.
    """
    cfg = bench_control_config()
    stage_4a = dataclasses.replace(
        cfg,
        supervisor=dataclasses.replace(cfg.supervisor, authority_pct=0.1),
        feedforward=dataclasses.replace(cfg.feedforward, enabled=False),
        tuning=dataclasses.replace(cfg.tuning, enabled=False),
    )
    # `bench_app_config`, because that is what `FittedHarness` calls -- patching
    # `bench_control_config` here would have proved nothing at all, which is the
    # same shape of mistake as the assertion this replaced.
    app = bench_plant.bench_app_config()
    app.extensions["control"] = stage_4a
    monkeypatch.setattr(bench_plant, "bench_app_config", lambda: app)
    assert bench_plant.bench_app_config().section("control") is stage_4a

    h = FittedHarness(kelvin=118.0)
    assert h.sup.cfg.authority_pct == BENCH_AUTHORITY_PCT
    assert h.sup.feedforward.enabled is BENCH_FEEDFORWARD
    assert h.sup.tuner.enabled is BENCH_TUNING
    # And the cryostat's own limits still came from that same file, so the
    # split is what is pinned rather than "ignores the file".
    assert h.sup.cfg.hard_max_pct == stage_4a.supervisor.hard_max_pct
