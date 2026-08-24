"""The 33x driver: what it reads, and what it refuses to write.

These boxes run their own PID loop, so the whole control surface is three
commands -- SETP, RANGE, PID -- and the interesting behaviour is all in the
guards around them.
"""

import pytest

from lschart.instruments.ls33x import CAPS, LS33x, LS335, LS336
from lschart.instruments.sim import Sim33x, SimulatedRig
from lschart.transport import LoopbackTransport, TransportError


def build(model="335", **kw):
    rig = SimulatedRig()
    sim = Sim33x(rig, model=model)
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
    rig = SimulatedRig()
    with pytest.raises(ValueError, match="unsupported model"):
        LS33x(LoopbackTransport(Sim33x(rig)), model="999")


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


def test_transaction_budget_matches_what_a_frame_actually_costs():
    """`check` predicts the bus load without opening anything, so it must agree."""
    inst, sim = build("335", name="ls335")
    inst.read_frame()          # let it discover channel names first
    counted = []
    original = sim.handle_query
    sim.handle_query = lambda cmd: (counted.append(cmd), original(cmd))[1]
    inst.read_frame()
    assert len(counted) == inst.transactions_per_frame()


# -- identity ---------------------------------------------------------------

def test_a_model_mismatch_is_caught_at_startup():
    """A 335 config pointed at a 336 would misread every input."""
    rig = SimulatedRig()
    inst = LS33x(LoopbackTransport(Sim33x(rig, model="336")), model="335")
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
    rig = SimulatedRig()
    assert LS335(LoopbackTransport(Sim33x(rig, model="335"))).name == "ls335"
    assert LS336(LoopbackTransport(Sim33x(rig, model="336"))).name == "ls336"
    assert LS336(LoopbackTransport(Sim33x(rig, model="336"))).model == "336"


def test_the_336_defaults_to_read_only():
    """Loop 2 holds THE CHONKE on the LTSPM rig; disturbing it is a hazard."""
    rig = SimulatedRig()
    assert LS336(LoopbackTransport(Sim33x(rig, model="336"))).allow_writes is False


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
    rig = SimulatedRig()
    sim = DeafSim(rig, model="335")
    inst = LS33x(LoopbackTransport(sim), model="335", allow_writes=True)
    with pytest.raises(Exception, match="did not take"):
        inst.set_setpoint(1, 77.0)
    assert sim.write_log, "the command was sent"


def test_the_failure_says_not_to_trust_the_instrument_state():
    """The dangerous outcome is believing a setpoint took when it did not."""
    rig = SimulatedRig()
    inst = LS33x(LoopbackTransport(DeafSim(rig, model="335")),
                 model="335", allow_writes=True)
    with pytest.raises(Exception, match="do not assume"):
        inst.set_setpoint(1, 77.0)


def test_verification_covers_every_write_path():
    rig = SimulatedRig()
    inst = LS33x(LoopbackTransport(DeafSim(rig, model="335")),
                 model="335", allow_writes=True)
    for call in (
        lambda: inst.set_setpoint(1, 77.0),
        lambda: inst.set_heater_range(1, 2),
        lambda: inst.set_pid(1, 10.0, 20.0, 0.0),
        lambda: inst.set_ramp(1, 1.5),
    ):
        with pytest.raises(Exception, match="did not take"):
            call()


def test_a_heater_that_refuses_to_switch_off_is_reported():
    """The most important verification of the lot.

    `all_heaters_off` on already-off heaters legitimately passes -- the state
    asked for is the state held.  What must never pass silently is a heater
    that stays ON after being told to stop.
    """
    rig = SimulatedRig()
    sim = DeafSim(rig, model="335")
    sim.ranges = {1: 3, 2: 3}           # both heaters at full range
    inst = LS33x(LoopbackTransport(sim), model="335", allow_writes=True)
    with pytest.raises(Exception, match="did not take"):
        inst.all_heaters_off()


def test_all_heaters_off_is_satisfied_when_they_are_already_off():
    inst, _ = build("336", allow_writes=True)
    inst.all_heaters_off()              # no exception: nothing to change


def test_verification_can_be_turned_off():
    """For a box whose readback is unavailable -- explicitly, never by default."""
    rig = SimulatedRig()
    inst = LS33x(LoopbackTransport(DeafSim(rig, model="335")),
                 model="335", allow_writes=True, verify_writes=False)
    inst.set_setpoint(1, 77.0)          # no exception


def test_verification_is_on_by_default():
    rig = SimulatedRig()
    inst = LS33x(LoopbackTransport(Sim33x(rig)), model="336")
    assert inst.verify_writes is True
