"""What `lschart` reads off a real supervisor, and what the viewer draws from it.

`lschart` must never import `ltspm3`, so `StatusWriter._control` reads every
field by name off whatever object the poller happens to be holding, defaulting
where it finds nothing.  That is exactly the coupling that breaks silently: a
rename in `ltspm3` leaves a status file that parses, validates and is quietly
full of nulls, and the first symptom is a viewer whose software-loop row has
gone blank on a cryostat nobody is watching closely.

`tests/test_ipc_files.py` pins the projection against a stand-in.  This pins
the *names* against the real thing, which the stand-in by construction cannot.
It lives here and not there because this is the one directory where the two
halves are allowed to meet.
"""

from __future__ import annotations

import json
import time

from lschart.gui.source import control_row, loop_marks
from lschart.ipc.status import StatusWriter, read_status
from lschart.model import Frame
from ltspm3.control import LoopMode, SupervisorConfig


def written(tmp_path, harness, channel="Sample"):
    """One status file, written from a real supervisor's real last answer."""
    writer = StatusWriter(tmp_path / "status.json")
    writer.write(
        Frame(t_wall=time.time(), t_mono=time.monotonic(), readings={}),
        control=harness.sup.status,
        controller=harness.sup,
        control_channel=channel,
    )
    return read_status(tmp_path / "status.json")["control"]


def test_every_field_the_status_file_asks_for_is_one_the_supervisor_has(
        tmp_path, armed):
    """Not one null.  A rename upstream shows up here rather than as a blank
    row on the cryostat."""
    block = written(tmp_path, armed())
    for key in ("state", "mode", "health", "sensor", "setpoint_k",
                "setpoint_target_k", "error_k", "output_pct", "demand_pct",
                "rail_low_pct", "rail_high_pct", "threshold_k"):
        assert block[key] is not None, f"{key} did not survive the projection"


def test_the_band_in_the_file_is_the_band_the_supervisor_enforces(
        tmp_path, armed):
    h = armed()
    low, high = h.sup.band
    block = written(tmp_path, h)
    assert (block["rail_low_pct"], block["rail_high_pct"]) == (low, high)
    # And it is nowhere near the fixed pair a heater output is judged against,
    # which is the whole reason it has to be published.
    assert high - low < 10.0


def test_the_block_is_json_and_carries_no_enum_reprs(tmp_path, armed):
    """`SupervisorState.TRACKING` in the file would be a string no client
    could match on, and MATLAB would not know what to do with it either."""
    block = written(tmp_path, armed())
    json.dumps(block)
    assert block["state"] == "tracking" and block["mode"] == "pid"


def test_a_healthy_armed_loop_draws_a_row_with_neither_mark_lit(
        tmp_path, armed):
    """End to end: supervisor -> status file -> the viewer's projection."""
    h = armed()
    row = control_row(written(tmp_path, h, channel="Sample"))
    assert row["sensor"] == "Sample"
    assert row["range"] is None and row["heater_output"] is None
    marks = loop_marks(row, h.sup.status.filtered_k, rails=row["rails"])
    assert marks == {"trying": True, "saturated": False, "unsettled": False}


def test_a_loop_switched_to_manual_stops_being_marked(tmp_path, armed):
    """Manual is still clamped and rate limited, but it is not chasing a
    setpoint -- so neither warning applies, the same way range 0 suppresses
    them on a heater."""
    h = armed()
    h.sup.set_mode(LoopMode.MANUAL)
    h.step(2)
    row = control_row(written(tmp_path, h))
    assert row["mode_code"] != 1
    assert not loop_marks(row, 400.0, rails=row["rails"])["trying"]


def test_a_panic_hold_is_visible_in_the_row_it_leaves_behind(tmp_path, armed):
    """`panic_hold()` is the one seam `lschart` reaches into `ltspm3` by, and
    the row is where an operator finds out it was taken."""
    h = armed()
    h.sup.panic_hold()
    h.step(2)
    row = control_row(written(tmp_path, h))
    assert row["mode"] in ("idle", "manual")
    assert not loop_marks(row, 400.0, rails=row["rails"])["trying"]


def test_a_saturated_loop_writes_below_its_own_rail(tmp_path, armed):
    """The reason the mark is judged on the demand and not on the output.

    The written value is quantised to a DAC code and the band is re-applied by
    stepping *down* one, so a loop pinned at its clamp writes a number strictly
    below the rail it is sitting on and would never compare equal to it.

    Reaching that state needs a config the shipped one is not: at
    ``authority_pct`` 1.0 and a gain near 7.6 K/%, the band is about +/-7 K of
    authority while ``max_error_k`` is 1.0 K -- so on the real cryostat the
    anomaly hold always fires long before the clamp does, and a *tracking*
    loop cannot saturate.  Widening the premise and narrowing the band is what
    makes the arithmetic reachable; both are config, which is where limits
    belong.
    """
    h = armed(sup_cfg=SupervisorConfig(authority_pct=0.05, max_error_k=20.0,
                                       anomaly_demand_pct=5.0))
    h.sup.set_setpoint(h.equilibrium_k + 5.0, ramp=False)
    h.step(40)
    row = control_row(written(tmp_path, h))
    high = float(row["rails"][1])
    assert float(row["output_pct"]) <= high
    assert float(row["demand_pct"]) > high
    marks = loop_marks(row, h.sup.status.filtered_k, rails=row["rails"])
    assert marks["trying"] and marks["saturated"]
    # And the fixed pair a heater output is judged against says nothing at all
    # about this loop -- 63% is not 99%.
    assert not loop_marks(row, h.sup.status.filtered_k)["saturated"]
