"""Getting back to work after a fault.

Every test here failed before the recovery rework.  The three defects were
independent but had one cause: nothing re-seeded the measurement filter or the
PID bias when control resumed, even though the cryostat and the output had both
moved in the meantime.
"""

import pytest

from ltspm3.control import HealthState, LoopMode, SupervisorConfig, SupervisorState


def armed(harness, **kw):
    h = harness(**kw)
    h.settle_filter(40)
    h.sup.set_mode(LoopMode.PID)
    h.step(10)
    return h


def test_guard_escapes_fault_after_the_plant_has_moved(harness):
    """The spike-test deadlock.

    The low-pass only advances on accepted samples, so during an outage it
    freezes while the cryostat keeps moving -- and a fault ramp-down guarantees the
    cryostat moves.  On recovery every honest reading sat far from the stale
    prediction, was rejected as an outlier, and so never refreshed it.  The
    guard could not leave FAULT no matter how healthy the sensor became.
    """
    h = armed(harness)
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(200)                                   # 800 s -> FAULT -> ramping down
    assert h.sup.guard.state is HealthState.FAULT
    assert h.sup.state is SupervisorState.RAMPING_DOWN
    assert h.sup.output_pct < 63.076              # the cryostat is now cooling

    h.cryostat.clear_faults()
    h.step(40)
    assert h.sup.guard.state is HealthState.OK, "guard deadlocked on a stale prediction"


def test_filter_reseeds_rather_than_rejecting_forever(harness):
    h = armed(harness)
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(200)
    h.cryostat.clear_faults()
    h.step(40)

    assert h.sup.filter.value is not None
    assert not h.sup.filter.is_stale(h.clock.t)
    # The reseeded filter must describe the cryostat *now*, not the pre-fault value.
    # It still lags a falling cryostat, which is what a low pass is for -- what
    # matters is that it is nowhere near the stale 100 K it froze at.
    assert h.sup.filter.value == pytest.approx(h.sup.status.raw_k, abs=2.0)
    assert abs(h.sup.filter.value - h.equilibrium_k) > 1.0


def test_acknowledge_then_rearm_actually_resumes_control(harness):
    """acknowledge() used to leave mode at PID, so the operator's set_mode(PID)
    hit the 'already in this mode' short-circuit and never re-primed.  The loop
    held on a phantom demand step and locked out again minutes later."""
    cfg = SupervisorConfig(rampdown_pct_per_min=60.0, safe_output_pct=62.5,
                           authority_pct=2.0)
    h = armed(harness, sup_cfg=cfg)
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(250)
    assert h.sup.state is SupervisorState.LOCKED_OUT

    h.cryostat.clear_faults()
    h.sup.acknowledge()
    assert h.sup.mode is LoopMode.OFF, "acknowledge must disarm so re-arming re-primes"
    assert h.sup.state is SupervisorState.IDLE

    h.sup.set_mode(LoopMode.PID)
    assert h.sup.pid.bias == pytest.approx(62.5, abs=0.02), "primed from a stale output"

    h.step(200)
    assert h.sup.state is SupervisorState.TRACKING, f"stalled in {h.sup.state.value}"


def test_recovery_does_not_ratchet_further_down(harness):
    """A ramp-down cools the cryostat, which grows the error, which used to trigger
    another ramp-down.  That positive feedback ran the heater to zero from a
    single transient."""
    cfg = SupervisorConfig(rampdown_pct_per_min=60.0, safe_output_pct=62.5,
                           authority_pct=2.0)
    h = armed(harness, sup_cfg=cfg)
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(250)
    h.cryostat.clear_faults()
    h.sup.acknowledge()
    h.sup.set_mode(LoopMode.PID)

    h.step(200)
    outs = [s.output_pct for s in h.history[-200:] if s.output_pct is not None]
    assert min(outs) >= 62.4, f"ratcheted down to {min(outs):.3f}%"
    assert h.sup.state is not SupervisorState.LOCKED_OUT


def test_a_brief_hold_does_not_reseed_the_filter(harness):
    """Reseeding throws away noise history, so it must happen only when the
    stored state is genuinely stale -- not after every short SUSPECT."""
    h = armed(harness)
    before = h.sup.filter.value
    h.cryostat.inject(dropout_channels={"218.1"})
    h.step(3)                                     # 12 s: well inside stale_after_s
    h.cryostat.clear_faults()
    h.step(10)
    assert h.sup.filter.value == pytest.approx(before, abs=0.5)
