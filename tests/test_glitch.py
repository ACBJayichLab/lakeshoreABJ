"""The failure mode that is actually in the reference logs.

Nine events over 1,510 h, every one on the sample input.  The value scatters in
both directions between ~11 K and the true temperature, never reads 0 K, lasts
between 2 s and 280 s, and then resumes on the pre-glitch trend.  Other channels
carry on undisturbed throughout.

These tests encode that shape, and the fast-cooldown case that must *not* be
confused with it.
"""

import pytest

from lschart.control import HealthState, LoopMode, SupervisorState
from lschart.control.health import SensorGuardConfig


def armed(harness, **kw):
    h = harness(**kw)
    h.settle_filter(40)
    h.sup.set_mode(LoopMode.PID)
    h.step(10)
    return h


def test_the_280_second_glitch_is_ridden_out_without_moving_the_heater(harness):
    """The longest observed event, at the observed cadence.

    The old fault_after_s of 60 s would have escalated this to a ramp-down and,
    with require_ack_after_fault, ended the run -- for a sensor burp that heals
    itself in five minutes.
    """
    h = armed(harness)
    before = h.sup.output_pct

    h.rig.inject(glitch_channels={"218.1"})
    h.step(70)                                    # 280 s at 4 s cadence

    assert h.sup.state is SupervisorState.HOLDING
    assert h.sup.guard.state is not HealthState.FAULT, "escalated on a self-healing glitch"
    assert h.sup.output_pct == before, "the glitch moved the heater"

    h.rig.clear_faults()
    h.step(30)
    assert h.sup.guard.state is HealthState.OK
    assert h.sup.state is SupervisorState.TRACKING
    assert h.sup.output_pct == pytest.approx(before, abs=0.05)


def test_glitch_never_reads_zero_so_range_checks_alone_would_miss_it(harness):
    """Documents why valid_min_k cannot be the detector for this."""
    h = armed(harness)
    h.rig.inject(glitch_channels={"218.1"})
    h.step(40)

    raws = [s.raw_k for s in h.history[-40:] if s.raw_k is not None]
    guard = SensorGuardConfig()
    assert min(raws) > guard.valid_min_k, "test is not exercising the real fault"
    assert any(s.validity.value in ("slew_reject", "incoherent") for s in h.history[-40:])


def test_a_corroborated_fast_cooldown_is_believed(harness):
    """-6.5 K in one 4 s sample is real: monitor7 recorded it on all three inputs.

    The old max_slew_k_per_s of 1.25 K/s rejected exactly this.
    """
    h = armed(harness)
    h.rig.plant.pct = 20.0                        # a genuine, large step down
    h.step(60)

    rejected = [s for s in h.history[-60:]
                if s.validity.value in ("slew_reject", "incoherent")]
    assert not rejected, f"rejected {len(rejected)} samples of a genuine transient"


def test_uncorroborated_move_is_rejected_even_below_the_hard_slew_limit(harness):
    """The coherence tier is what catches the smaller half of the glitch."""
    h = armed(harness)
    h.rig.inject(glitch_channels={"218.1"},
                 glitch_low_k=h.equilibrium_k - 3.0,   # ~3 K excursions: well
                 glitch_high_k=h.equilibrium_k)        # under 5 K/s at 4 s
    h.step(8)

    window = h.history[-8:]
    assert all(s.corroborated is False for s in window)
    # Rejected on the coherence tier at least once; the spike test also fires on
    # the smaller excursions, and either way the output must freeze.
    assert any(s.validity.value == "incoherent" for s in window)
    assert h.sup.state is SupervisorState.HOLDING


def test_coherence_degrades_gracefully_with_a_single_channel(harness):
    """One sensor means no evidence either way -- fall back to the hard limit,
    rather than making a single-sensor rig uncontrollable."""
    h = harness()
    h.inst.channels = {1: "Sample"}                # drop the ancillary inputs
    h.settle_filter(40)
    h.sup.set_mode(LoopMode.PID)
    h.step(10)

    assert h.sup.status.corroborated is None
    h.rig.plant.pct = 20.0
    h.step(40)
    assert h.sup.state in (SupervisorState.TRACKING, SupervisorState.HOLDING)
