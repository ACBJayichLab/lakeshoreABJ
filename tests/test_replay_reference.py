"""The guard, measured against real cryostat data rather than a simulation.

The simulator can only produce faults someone thought to model.  These tests
run the production pipeline over actual reference logs, which is the only way
to hold the thresholds accountable to reality -- and it is how the stale-slew-
reference hole was found, a bug no simulated fault would have exposed.

Skipped automatically when the logs are not present (they are ~110 MB) or when
xlrd is not installed.
"""

import glob
import os

import pytest

from lschart.control.health import HealthState
from lschart.tools import replay as replay_mod

pytest.importorskip("xlrd")

LOGS = "reference/logs"
GLITCH_LOG = f"{LOGS}/CD8/cd8_2_24_2026_sample_cooldown.xls"
CLEAN_LOG = f"{LOGS}/CD8/cd8_2_24_2026_sample_monitor7.xls"

pytestmark = pytest.mark.skipif(
    not os.path.isdir(LOGS) or not glob.glob(f"{LOGS}/CD*/*.xls"),
    reason="reference logs not present",
)


@pytest.fixture(scope="module")
def glitch_result():
    from lschart.tools.import_xls import load
    return replay_mod.replay(load(GLITCH_LOG), channel="Input 1")


@pytest.fixture(scope="module")
def clean_result():
    from lschart.tools.import_xls import load
    return replay_mod.replay(load(CLEAN_LOG), channel="Input 1")


def test_the_known_glitch_is_detected(glitch_result):
    """cd8_..._sample_cooldown carries the longest observed event: ~280 s of
    scatter between 91 K and 298 K while the true temperature was ~297 K."""
    events = [e for e in glitch_result.events if 38000 <= e.start_t <= 38500]
    assert events, "the documented glitch at t~38100 s was not flagged"
    assert any(e.k_min < 200.0 for e in events), "flagged, but not the wild samples"


def test_glitch_detection_survives_a_stale_slew_reference(glitch_result):
    """Every rejection ages the slew reference.  At this log's 20 s cadence a
    single rejection used to push it past slew_reference_max_age_s, disabling
    the slew test entirely so the next glitch sample sailed through -- the
    events alternated rejected/accepted all the way down.  The reversal test
    needs no reference, so runs should now be contiguous."""
    events = [e for e in glitch_result.events if 38000 <= e.start_t <= 38500]
    assert max(e.n for e in events) >= 3, "detections are still single-sample"


def test_a_fast_but_genuine_cooldown_is_not_torn_apart(clean_result):
    """monitor7 falls 6.5 K in one 4 s sample -- 1.63 K/s, corroborated on all
    three inputs.  It is real, and must not be shredded by the guard."""
    assert clean_result.rejects_per_day < 25.0, (
        f"{clean_result.rejects_per_day:.1f} rejections/day on a legitimate cooldown"
    )


def test_no_reference_log_ever_reaches_fault(glitch_result, clean_result):
    """FAULT ramps the heater down and, with require_ack_after_fault, ends the
    run.  Nothing in the historical record should have triggered that."""
    for r in (glitch_result, clean_result):
        assert r.reached_fault == 0, f"{r.log} would have ramped the heater down"


def test_quiet_holding_produces_no_rejections():
    """A week of steady holding must be completely silent, or the loop spends
    its life frozen."""
    from lschart.tools.import_xls import load
    path = f"{LOGS}/CD8/cd8_2_24_2026_sample_monitor3.xls"
    if not os.path.exists(path):
        pytest.skip("log not present")
    r = replay_mod.replay(load(path), channel="Input 1")
    assert r.n_rejected == 0, f"{r.n_rejected} rejections in {r.hours:.0f} h of quiet"


def test_guard_recovers_to_ok_after_the_glitch_passes(glitch_result):
    """Self-healing events must leave the guard healthy, not latched."""
    late = [e for e in glitch_result.runs if e.start_t > 45000]
    assert not late or all(e.reached != HealthState.FAULT.value for e in late)
