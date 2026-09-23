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

import pytest

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
                "rail_low_pct", "rail_high_pct", "threshold_k",
                # What the loop is reading, as against what the chart draws.
                "phase", "raw_k", "filtered_k", "slope_k_per_s", "noise_k",
                "validity",
                # Asked -> allowed -> written, and the band's envelope.
                "target_pct", "hard_min_pct", "hard_max_pct",
                # The numbers that bound it, and the one that explains a blank.
                "fault_error_k", "min_output_pct", "max_rate_k_per_min",
                # The residual's band and its step, which always have a value
                # even where the residual itself has no opinion.
                "sigma_q_w", "dq_step_w", "velocity_ff_pct"):
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

    Reaching that state on THIS harness needs a config the shipped one is not:
    at ``authority_pct`` 1.0 and a gain near 7.6 K/%, the band is about
    +/-7 K of authority, so a small deliberate error never reaches the clamp.
    Widening the premise and narrowing the band is what makes the arithmetic
    reachable; both are config, which is where limits belong.  On the cryostat
    a tracking loop CAN sit on its rail -- a warning does not stop it, and
    authority exhausted is the check that watches for exactly that.
    """
    h = armed(sup_cfg=SupervisorConfig(authority_pct=0.05, warn_error_k=20.0,
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


def test_no_opinion_is_published_as_null_and_never_as_false(tmp_path, harness):
    """The tri-states, where ``null`` is the CORRECT answer.

    ``model_trusted``, ``corroborated`` and ``missing_power_w`` mean *no
    opinion* when they are None, which is neither trust nor distrust.
    ``bool(None)`` is False, so the obvious spelling of the projection would
    publish a claim nothing had established -- and this codebase has already
    made that mistake once, in `model_trusted`'s own default.

    An unarmed loop is the case that proves it: nothing has run the model check
    yet, so there is genuinely nothing to say.
    """
    h = harness()
    h.step(2)
    block = written(tmp_path, h)
    for key in ("model_trusted", "corroborated", "missing_power_w",
                "model_error_k", "readback_pct"):
        assert key in block, f"{key} is not published at all"
    # The one that must be null rather than False on a loop that has not run
    # the check.  0 and False are both wrong answers here and they are
    # different wrong answers.
    assert block["model_trusted"] is None
    assert block["missing_power_w"] is None
    for key in ("model_trusted", "corroborated"):
        assert block[key] in (None, True, False), f"{key} is not tri-state"
        # `in` would accept 0 and 1, which is exactly the confusion at issue.
        assert block[key] is None or isinstance(block[key], bool)


def test_the_rate_ceiling_in_the_file_is_the_one_the_ramp_enforces(
        tmp_path, armed):
    """A client building a setpoint control must not be able to express a rate
    the supervisor will refuse -- the same reason `max_output_pct` is published
    for an analog output.  This is the contract that would rot in silence."""
    h = armed()
    block = written(tmp_path, h)
    assert block["max_rate_k_per_min"] == h.sup.ramp.cfg.max_rate_k_per_min
    over = block["max_rate_k_per_min"] * 2
    with pytest.raises(ValueError):
        h.sup.sweep_to(h.sup.status.setpoint_k + 5.0, over)


def test_the_three_percentages_tell_one_story(tmp_path, armed):
    """Asked -> allowed -> written is one cycle's whole decision, and it is
    only readable if all three are published together."""
    h = armed()
    block = written(tmp_path, h)
    assert block["demand_pct"] >= block["target_pct"]
    assert block["hard_min_pct"] <= block["target_pct"] <= block["hard_max_pct"]
    assert block["hard_min_pct"] <= block["output_pct"] <= block["hard_max_pct"]


def test_a_disengaged_loop_says_it_did_not_write(tmp_path, armed):
    """A loop reading `tracking` that has stopped writing is broken in a way
    nothing else in this block would show, so `wrote` is its own field."""
    h = armed()
    held = h.sup.panic_hold()
    h.step(2)
    block = written(tmp_path, h)
    assert block["wrote"] is False
    assert block["mode"] == "off"
    assert block["output_pct"] == pytest.approx(held)


def test_a_residual_that_declines_to_judge_says_why(tmp_path, armed):
    """A blank premise and a broken one look identical without this.

    On an ARMED loop the check has run and has declined -- below
    `min_output_pct` the watt residual genuinely has no opinion -- which is a
    different thing from the unarmed case above, where nothing has run at all.
    The reason is what lets a reader tell those two apart, and it is why
    `min_output_pct` is published beside it.
    """
    block = written(tmp_path, armed())
    if block["missing_power_w"] is None:
        assert block["residual_reason"], "no opinion, and no reason given"
        assert block["min_output_pct"] is not None


def test_the_published_settle_rule_is_the_tuners_own(tmp_path, armed):
    """THE RULE A WAITING CLIENT WAITS FOR, and that it is not retyped.

    A script that commands a temperature and then waits for the cryostat is
    waiting for the error to be inside `hold_error_k` for `hold_settle_s` --
    Jeff's "within 50 mK and staying", docs/ltspm3/requirements.md 1b.  The
    rule's own behaviour is pinned in `test_tuning.py`; what is pinned here is
    that the two numbers a client reads off the status file are the *same two*
    the tuner applies, so a MATLAB sweep cannot go on holding to a rule this
    cryostat has stopped using.
    """
    h = armed()
    cfg = h.sup.tuner.cfg
    block = written(tmp_path, h)

    assert block["hold_error_k"] == cfg.hold_error_k
    assert block["hold_settle_s"] == cfg.hold_settle_s
    assert block["unsettle_s"] == cfg.unsettle_s


def test_settled_in_the_file_is_the_supervisors_own_verdict(tmp_path, armed):
    """THE FIELD A WAITING CLIENT WAITS ON, and that it is not re-derived.

    `settled` is decided once, in the supervisor (`Tuner.settled`: on the hold
    gains, inside `hold_error_k` for `hold_settle_s`), and it is the same
    clock that switches the gains -- so the file says `hold` and `settled`
    together the first time.  A command that moves the setpoint takes both
    away on the cycle it lands.  `matlab/LSChartRecorder.m`'s
    `waitUntilSteady` reads this and counts nothing of its own.
    """
    h = armed()
    for _ in range(200):
        if h.sup.status.settled:
            break
        h.step(1)
    block = written(tmp_path, h)
    assert block["settled"] is True, "an armed loop at rest never settled"
    assert block["phase"] == "hold"

    h.sup.sweep_to(h.sup.status.setpoint_k + 5.0, None)
    h.step(2)
    block = written(tmp_path, h)
    assert block["ramping"] is True
    assert block["settled"] is False
    assert block["phase"] == "move"


def test_a_frozen_loop_is_not_settled(tmp_path, armed):
    """Nobody certified the hold through a cycle the loop refused to act on."""
    h = armed()
    for _ in range(200):
        if h.sup.status.settled:
            break
        h.step(1)
    assert h.sup.status.settled
    h.sup.panic_hold()
    h.step(2)
    assert written(tmp_path, h)["settled"] is False
