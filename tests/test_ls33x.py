"""The 33x driver: what it reads, and what it refuses to write.

These boxes run their own PID loop, so the whole control surface is three
commands -- SETP, RANGE, PID -- and the interesting behaviour is all in the
guards around them.
"""

import pytest

from lschart.instruments.base import InstrumentError
from lschart.instruments.ls33x import CAPS, LS33x, LS335, LS336
from lschart.instruments.sim import Sim33x, SimulatedCryostat
from lschart.transport import LoopbackTransport, TransportError


def build(model="335", **kw):
    cryostat = SimulatedCryostat()
    sim = Sim33x(cryostat, model=model)
    inst = LS33x(LoopbackTransport(sim), model=model, **kw)
    return inst, sim


# -- capabilities -----------------------------------------------------------

def test_a_335_has_two_inputs_and_a_336_has_four():
    assert CAPS["335"].inputs == ("A", "B")
    assert CAPS["336"].inputs == ("A", "B", "C", "D")


def test_only_the_336_has_analog_outputs():
    """Outputs 3 and 4 on a 336 are voltage-only; a 335 has no equivalent."""
    assert CAPS["336"].analog_outputs == (3, 4)
    assert CAPS["335"].analog_outputs == ()


def test_both_models_drive_two_heaters():
    assert CAPS["335"].heater_outputs == CAPS["336"].heater_outputs == (1, 2)


def test_an_unknown_model_is_refused_at_construction():
    cryostat = SimulatedCryostat()
    with pytest.raises(ValueError, match="unsupported model"):
        LS33x(LoopbackTransport(Sim33x(cryostat)), model="999")


# -- reading ----------------------------------------------------------------

def test_a_335_reads_exactly_its_two_inputs():
    inst, _ = build("335")
    readings, _ = inst.read_frame()
    assert len(readings) == 2
    assert set(readings) == {"Sample", "Cold Head"}


def test_channel_labels_come_from_the_instrument_when_unset():
    """A 336 that was never configured here still logs the panel's own names."""
    inst, _ = build("336")
    readings, _ = inst.read_frame()
    assert "THE CHONKE" in readings


def test_explicit_channel_names_win():
    inst, _ = build("335", channels={"A": "Cold Finger"})
    readings, _ = inst.read_frame()
    assert set(readings) == {"Cold Finger"}, "only declared channels are logged"


def test_aux_carries_setpoints_ranges_and_heater_outputs():
    inst, _ = build("335", name="ls335")
    _, aux = inst.read_frame()
    assert "ls335.setpoint1" in aux
    assert "ls335.heater1" in aux
    assert "ls335.range1" in aux


def test_aux_keys_are_known_before_the_first_read():
    """The CSV header is written before any frame arrives."""
    inst, _ = build("335", name="ls335")
    declared = inst.aux_keys()
    _, aux = inst.read_frame()
    assert set(aux) == set(declared)


def test_a_failed_auxiliary_query_does_not_lose_the_frame():
    """One dead query must cost its own column, not every temperature."""
    inst, sim = build("335")
    original = sim.handle_query

    def flaky(cmd):
        if cmd.startswith("HTR?"):
            raise TransportError("simulated")
        return original(cmd)

    sim.handle_query = flaky
    readings, aux = inst.read_frame()
    assert len(readings) == 2, "temperatures survive"
    assert not any(k.endswith("heater1") for k in aux)


def count_queries(inst, sim):
    counted = []
    original = sim.handle_query
    sim.handle_query = lambda cmd: (counted.append(cmd), original(cmd))[1]
    inst.read_frame()
    sim.handle_query = original
    return counted


def test_transaction_budget_matches_the_frame_that_costs_the_most():
    """`check` predicts the bus load without opening anything, so it must agree
    -- with the *worst* frame, which is the one the poll interval has to fit.

    The loop bindings are read on a slow cadence, so most frames are cheaper
    than the budget and the one that refreshes them is exactly it.  A budget
    that averaged the burst away would be a budget the worst cycle overruns.
    """
    inst, sim = build("335", name="ls335")
    inst.read_frame()          # let it discover channel names first

    # The next frame is not a slow tick: cheaper than the budget, never more.
    lean = count_queries(inst, sim)
    assert len(lean) < inst.transactions_per_frame()
    assert not any(c.startswith("OUTMODE?") for c in lean)

    # Wind on to the frame that does refresh them.  That one is the budget.
    while (inst._loop_cycles % inst.loop_every_n_cycles) != 0:
        inst.read_frame()
    fat = count_queries(inst, sim)
    assert len(fat) == inst.transactions_per_frame()
    assert sum(c.startswith("OUTMODE?") for c in fat) == len(inst.caps.loops)


def test_a_recorder_that_does_not_want_loop_bindings_does_not_pay_for_them():
    inst, sim = build("335", name="ls335", read_loops=False)
    inst.read_frame()
    assert len(count_queries(inst, sim)) == inst.transactions_per_frame()
    assert inst.loop_bindings == {}


# -- identity ---------------------------------------------------------------

def test_a_model_mismatch_is_caught_at_startup():
    """A 335 config pointed at a 336 would misread every input."""
    cryostat = SimulatedCryostat()
    inst = LS33x(LoopbackTransport(Sim33x(cryostat, model="336")), model="335")
    with pytest.raises(TransportError, match="config says model 335"):
        inst.verify_model()


def test_verify_model_passes_on_the_right_box():
    inst, _ = build("335")
    assert "MODEL335" in inst.verify_model()


# -- write gating -----------------------------------------------------------

def test_every_write_is_refused_while_read_only():
    """The default.  A box being merely watched must not be disturbed."""
    inst, sim = build("335")
    for call in (
        lambda: inst.set_setpoint(1, 100.0),
        lambda: inst.set_heater_range(1, 1),
        lambda: inst.set_pid(1, 50, 20, 0),
        lambda: inst.set_ramp(1, 1.0),
        lambda: inst.all_heaters_off(),
    ):
        with pytest.raises(PermissionError, match="read-only"):
            call()
    assert sim.write_log == [], "nothing reached the instrument"


def test_reads_still_work_while_read_only():
    inst, _ = build("335")
    assert inst.setpoint(1) == 0.0
    assert inst.heater_range(1) == 0


def test_setpoint_is_written_when_allowed():
    inst, sim = build("335", allow_writes=True)
    inst.set_setpoint(1, 77.35)
    assert "SETP 1,77.3500" in sim.write_log
    assert inst.setpoint(1) == pytest.approx(77.35, abs=1e-3)


def test_a_setpoint_above_the_ceiling_is_refused():
    """A typo asking a cryostat for 3000 K stops here, not at the instrument."""
    inst, sim = build("335", allow_writes=True, max_setpoint_k=330.0)
    with pytest.raises(ValueError, match="outside"):
        inst.set_setpoint(1, 3000.0)
    assert sim.write_log == []


def test_a_negative_setpoint_is_refused():
    inst, _ = build("335", allow_writes=True)
    with pytest.raises(ValueError, match="outside"):
        inst.set_setpoint(1, -5.0)


def test_a_loop_the_model_does_not_have_is_refused():
    """A 335 has two loops; a 336 config's loop 4 would silently mean nothing."""
    inst, sim = build("335", allow_writes=True)
    with pytest.raises(ValueError, match="has no loop 4"):
        inst.set_setpoint(4, 100.0)
    assert sim.write_log == []


def test_a_336_does_have_loop_4():
    inst, _ = build("336", allow_writes=True)
    inst.set_setpoint(4, 100.0)
    assert inst.setpoint(4) == pytest.approx(100.0)


# -- the heater range is the thing that applies power -----------------------

def test_setting_a_setpoint_never_turns_a_heater_on():
    """The rule: raising a range is always an explicit act, never a side effect."""
    inst, sim = build("335", allow_writes=True)
    assert inst.heater_range(1) == 0
    inst.set_setpoint(1, 200.0)
    assert inst.heater_range(1) == 0, "setpoint alone must not apply power"
    assert not any(c.startswith("RANGE") for c in sim.write_log)


def test_heater_range_is_written_when_asked_explicitly():
    inst, _ = build("335", allow_writes=True)
    inst.set_heater_range(1, 2)
    assert inst.heater_range(1) == 2


def test_an_out_of_range_heater_setting_is_refused():
    inst, sim = build("335", allow_writes=True)
    with pytest.raises(ValueError, match="0..3"):
        inst.set_heater_range(1, 9)
    assert sim.write_log == []


def test_a_heater_output_the_model_does_not_have_is_refused():
    inst, _ = build("335", allow_writes=True)
    with pytest.raises(ValueError, match="has no heater output 3"):
        inst.set_heater_range(3, 1)


def test_all_heaters_off_zeroes_every_output():
    inst, _ = build("336", allow_writes=True)
    inst.set_heater_range(1, 3)
    inst.set_heater_range(2, 3)
    inst.all_heaters_off()
    assert inst.heater_range(1) == 0
    assert inst.heater_range(2) == 0


# -- the instrument's own ramp ----------------------------------------------

def test_the_firmware_ramp_round_trips():
    inst, _ = build("335", allow_writes=True)
    inst.set_ramp(1, 2.5)
    assert inst.ramp(1) == (True, pytest.approx(2.5))


def test_a_zero_rate_ramp_is_refused():
    """0 K/min means 'infinitely fast' to the instrument -- almost never meant."""
    inst, sim = build("335", allow_writes=True)
    with pytest.raises(ValueError, match="infinitely fast"):
        inst.set_ramp(1, 0.0)
    assert sim.write_log == []


def test_ramping_can_be_turned_off_explicitly():
    inst, _ = build("335", allow_writes=True)
    inst.set_ramp(1, 2.5)
    inst.set_ramp(1, 0.0, enable=False)
    assert inst.ramp(1)[0] is False


# -- the model-named subclasses ---------------------------------------------

def test_the_named_subclasses_carry_their_model_and_default_name():
    cryostat = SimulatedCryostat()
    assert LS335(LoopbackTransport(Sim33x(cryostat, model="335"))).name == "ls335"
    assert LS336(LoopbackTransport(Sim33x(cryostat, model="336"))).name == "ls336"
    assert LS336(LoopbackTransport(Sim33x(cryostat, model="336"))).model == "336"


def test_the_336_defaults_to_read_only():
    """Loop 2 holds THE CHONKE on the LTSPM3 cryostat; disturbing it is a hazard."""
    cryostat = SimulatedCryostat()
    assert LS336(LoopbackTransport(Sim33x(cryostat, model="336"))).allow_writes is False


# -- writes are confirmed, not assumed --------------------------------------

class DeafSim(Sim33x):
    """Accepts writes and ignores them -- what a too-fast readback looks like.

    Measured on a real 336 over USB: query immediately after a write and the
    instrument answers with the PREVIOUS value.  From the driver's side that is
    indistinguishable from a box that simply did not apply the command, and
    both must be caught.
    """

    def handle_write(self, cmd):
        self.write_log.append(cmd)      # received, and deliberately not applied


def test_a_write_that_does_not_take_is_an_error_not_a_success():
    cryostat = SimulatedCryostat()
    sim = DeafSim(cryostat, model="335")
    inst = LS33x(LoopbackTransport(sim), model="335", allow_writes=True)
    with pytest.raises(InstrumentError, match="did not take"):
        inst.set_setpoint(1, 77.0)
    assert sim.write_log, "the command was sent"


def test_the_failure_says_not_to_trust_the_instrument_state():
    """The dangerous outcome is believing a setpoint took when it did not."""
    cryostat = SimulatedCryostat()
    inst = LS33x(LoopbackTransport(DeafSim(cryostat, model="335")),
                 model="335", allow_writes=True)
    with pytest.raises(InstrumentError, match="do not assume"):
        inst.set_setpoint(1, 77.0)


def test_verification_covers_every_write_path():
    cryostat = SimulatedCryostat()
    inst = LS33x(LoopbackTransport(DeafSim(cryostat, model="335")),
                 model="335", allow_writes=True)
    for call in (
        lambda: inst.set_setpoint(1, 77.0),
        lambda: inst.set_heater_range(1, 2),
        lambda: inst.set_pid(1, 10.0, 20.0, 0.0),
        lambda: inst.set_ramp(1, 1.5),
    ):
        with pytest.raises(InstrumentError, match="did not take"):
            call()


def test_a_heater_that_refuses_to_switch_off_is_reported():
    """The most important verification of the lot.

    `all_heaters_off` on already-off heaters legitimately passes -- the state
    asked for is the state held.  What must never pass silently is a heater
    that stays ON after being told to stop.
    """
    cryostat = SimulatedCryostat()
    sim = DeafSim(cryostat, model="335")
    sim.ranges = {1: 3, 2: 3}           # both heaters at full range
    inst = LS33x(LoopbackTransport(sim), model="335", allow_writes=True)
    with pytest.raises(InstrumentError, match="did not take"):
        inst.all_heaters_off()


def test_all_heaters_off_is_satisfied_when_they_are_already_off():
    inst, _ = build("336", allow_writes=True)
    inst.all_heaters_off()              # no exception: nothing to change


def test_verification_can_be_turned_off():
    """For a box whose readback is unavailable -- explicitly, never by default."""
    cryostat = SimulatedCryostat()
    inst = LS33x(LoopbackTransport(DeafSim(cryostat, model="335")),
                 model="335", allow_writes=True, verify_writes=False)
    inst.set_setpoint(1, 77.0)          # no exception


def test_verification_is_on_by_default():
    cryostat = SimulatedCryostat()
    inst = LS33x(LoopbackTransport(Sim33x(cryostat)), model="336")
    assert inst.verify_writes is True


# -- what a loop is bound to -------------------------------------------------
#
# From OUTMODE?, and from nowhere else.  A map of loops to sensors kept in a
# config file could only go stale or lie, and on this family the loop number
# *is* the output number by protocol.


def test_a_loop_reports_the_sensor_the_instrument_says_it_reads():
    inst, sim = build("336", name="ls336")
    sim.outmodes[1] = (1, 3, 0)          # loop 1 reads input C
    inst.read_frame()
    binding = inst.loop_bindings[1]
    assert binding.input_letter == "C"
    assert binding.sensor == sim.names["C"]
    assert binding.closed_loop


def test_a_loop_in_open_loop_is_not_a_loop_chasing_a_setpoint():
    inst, sim = build("336")
    sim.outmodes[2] = (3, 2, 0)
    inst.read_frame()
    assert inst.loop_bindings[2].mode_name == "open loop"
    assert not inst.loop_bindings[2].closed_loop


def test_a_loop_bound_to_no_input_names_no_sensor():
    inst, sim = build("336")
    sim.outmodes[1] = (0, 0, 0)
    inst.read_frame()
    assert inst.loop_bindings[1].input_letter == ""
    assert inst.loop_bindings[1].sensor == ""


def test_the_heater_output_is_derived_not_configured():
    """On a 336 loops 1 and 2 drive heaters and 3 and 4 drive analog outputs.
    That is the protocol, so there is no key for it to disagree with."""
    inst, _ = build("336")
    inst.read_frame()
    assert [inst.loop_bindings[n].heater_output for n in (1, 2, 3, 4)] == [
        1, 2, None, None]


def test_a_ramp_in_progress_is_reported_so_a_client_can_hold_its_warning():
    inst, sim = build("336")
    sim.ramping[1] = 1
    inst.read_frame()
    assert inst.loop_bindings[1].ramping is True


def test_a_binding_that_fails_to_re_read_keeps_the_one_it_had():
    """OUTMODE changes approximately never, so last cadence's answer is very
    nearly certainly still true; dropping the row over one jittery reply would
    take the loop out of the table for a minute."""
    inst, sim = build("336")
    inst.read_frame()
    was = inst.loop_bindings[1]

    original = sim.handle_query

    def flaky(cmd):
        if cmd.startswith("OUTMODE?"):
            raise TransportError("simulated")
        return original(cmd)

    sim.handle_query = flaky
    while (inst._loop_cycles % inst.loop_every_n_cycles) != 0:
        inst.read_frame()
    inst.read_frame()
    assert inst.loop_bindings[1] == was


def test_the_mode_and_the_ramp_flag_reach_the_log():
    """A mode change is worth recording: "when did loop 2 go to open loop" is
    a question asked after the fact, and only the CSV can answer it."""
    inst, sim = build("336", name="ls336")
    sim.outmodes[2] = (3, 2, 0)
    _, aux = inst.read_frame()
    assert aux["ls336.outmode2"] == 3.0
    assert aux["ls336.ramping2"] == 0.0
    assert "ls336.outmode2" in inst.aux_keys()


def test_the_cached_binding_is_emitted_on_every_frame_not_only_the_slow_one():
    """A column that is blank on 29 rows out of 30 is a column nobody can
    read, and the value did not change on those 29 anyway."""
    inst, _ = build("336", name="ls336")
    inst.read_frame()
    _, aux = inst.read_frame()               # not a slow tick
    assert "ls336.outmode1" in aux


def test_a_loop_row_carries_its_configured_settling_threshold():
    inst, _ = build("336", loop_thresholds={1: 0.5, 2: 2.0})
    inst.read_frame()
    rows = {r["loop"]: r for r in inst.loop_rows()}
    assert rows[1]["threshold_k"] == 0.5
    assert rows[2]["threshold_k"] == 2.0
    # A loop left out has no opinion about being settled, and says None.
    assert rows[3]["threshold_k"] is None


def test_loop_rows_resolve_sensor_names_against_the_labels_in_use():
    """The binding is cached on a slow tick, but the labels are discovered on
    the first frame -- so a cached sensor name would be empty for the rest of
    the run."""
    inst, sim = build("336")
    inst.channels = {}                       # nothing discovered yet
    inst._refresh_loops()
    assert inst.loop_bindings[1].sensor == ""
    inst.read_frame()                        # discovery happens here
    assert inst.loop_rows()[0]["sensor"] == sim.names["A"]
