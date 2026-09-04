"""The 218 driver, and the one write it has.

The 218 is a monitor with an actuator bolted on: eight inputs it can only read,
and an analog output that on the LTSPM3 cryostat is the sample heater.  A 33x can
separate a setpoint from the range that makes it matter, and be gated in two
places accordingly.  Here there is one number and it *is* the power, so the
guards around that single command are the whole of the safety story and are
what these tests are about.
"""

import pytest

from lschart.instruments.base import InstrumentError
from lschart.instruments.ls218 import LS218, AnalogOutputConfig
from lschart.instruments.sim import Sim218, SimulatedCryostat
from lschart.transport import LoopbackTransport, TransportError


def build(**kw):
    cryostat = SimulatedCryostat(None, start_k=96.0)
    sim = Sim218(cryostat)
    kw.setdefault("channels", {1: "Sample", 2: "Cold Head", 3: "Shield"})
    inst = LS218(LoopbackTransport(sim, inter_command_delay=0.0), **kw)
    return inst, sim


def writes(sim) -> list[str]:
    return [c for c in sim.write_log if c.startswith("ANALOG")]


# -- reading ----------------------------------------------------------------

def test_one_query_fetches_every_populated_input():
    inst, _ = build()
    readings, aux = inst.read_frame()
    assert set(readings) == {"Sample", "Cold Head", "Shield"}
    assert "ls218.aout1" in aux


def test_a_channel_map_with_a_gap_reads_the_inputs_it_names():
    """A thermometer is wired to an input, and inputs are not a dense range.

    On 2026-09-04 the LTSPM3 magnet moved from input 3 to input 5, leaving 3
    empty.  The driver already read `sorted(channels)` rather than 1..N, but
    `Sim218` answered `KRDG? 0` from a hardcoded three-element list, so in
    simulation the moved channel came back 0.0000 -- which is precisely what
    the real box reports for an input with nothing plugged into it, and is
    classified as a dead sensor.  A fake that manufactures the one failure
    signature it is supposed to help distinguish is worse than no fake.
    """
    cryostat = SimulatedCryostat(None, start_k=96.0)
    cryostat._aux_base = {"218.2": 8.06, "218.5": 6.72}
    sim = Sim218(cryostat)
    inst = LS218(
        LoopbackTransport(sim, inter_command_delay=0.0),
        channels={1: "Sample", 2: "Coldplate", 5: "Magnet"},
    )
    readings, _ = inst.read_frame()

    assert set(readings) == {"Sample", "Coldplate", "Magnet"}
    # Not merely present: actually carrying the value wired to input 5, and
    # graded on it.  The validity is the assertion that matters -- a 0.0 K here
    # would still have produced a Reading, just one condemned as NO_SENSOR.
    assert readings["Magnet"].kelvin == pytest.approx(6.72, abs=0.5)
    assert readings["Coldplate"].kelvin == pytest.approx(8.06, abs=0.5)
    assert all(r.validity.good for r in readings.values())


def test_an_input_nothing_is_wired_to_reads_zero():
    """The empty half of the same rule.  Input 3 is now bare, and a fake that
    invented a temperature for it would hide exactly the fault the validity
    grading exists to catch."""
    cryostat = SimulatedCryostat(None, start_k=96.0)
    cryostat._aux_base = {"218.2": 8.06, "218.5": 6.72}
    sim = Sim218(cryostat)
    assert float(sim.handle_query("KRDG? 3")) == 0.0
    assert [float(v) for v in sim.handle_query("KRDG? 0").split(",")][2] == 0.0


def test_a_failed_readback_does_not_discard_good_temperatures():
    """AOUT? is a nice-to-have; three working thermometers are not."""
    inst, sim = build()

    def refuse(cmd):
        if cmd.startswith("AOUT?"):
            raise TransportError("no")
        return original(cmd)

    original = sim.handle_query
    sim.handle_query = refuse
    readings, aux = inst.read_frame()
    assert len(readings) == 3
    assert aux == {}


# -- the gate ---------------------------------------------------------------

def test_the_analog_output_is_read_only_by_default():
    """The default must be the safe one: this output is a heater."""
    inst, sim = build()
    assert inst.allow_writes is False
    with pytest.raises(PermissionError):
        inst.set_analog_percent(10.0)
    assert writes(sim) == []


def test_even_zero_is_refused_without_the_gate():
    """A box we may not write to is one we may not write zero to.

    On a shared cryostat that output may be somebody else's, and 0% is as much of a
    change to it as 60% is.
    """
    inst, sim = build()
    with pytest.raises(PermissionError):
        inst.analog_off()
    assert writes(sim) == []


def test_the_transport_interlock_beats_the_gate():
    """`read_only` is a layer below driver policy and outranks it.

    The refusal comes from the transport, not the driver, which is the whole
    point: a bug that flipped `allow_writes` still cannot reach the heater.
    """
    cryostat = SimulatedCryostat(None, start_k=96.0)
    sim = Sim218(cryostat)
    inst = LS218(
        LoopbackTransport(sim, inter_command_delay=0.0, read_only=True),
        allow_writes=True,
    )
    before = sim.analog_pct
    with pytest.raises(PermissionError, match="READ-ONLY"):
        inst.set_analog_percent(10.0)
    assert sim.analog_pct == before
    assert writes(sim) == []


# -- the ceiling ------------------------------------------------------------

def test_a_percentage_above_the_ceiling_is_refused():
    """~10 K/% on this cryostat: the ceiling is the guard, not `0 <= pct <= 100`."""
    inst, sim = build(allow_writes=True, max_output_pct=70.0)
    with pytest.raises(ValueError, match="70"):
        inst.set_analog_percent(85.0)
    assert writes(sim) == []


def test_a_negative_percentage_is_refused():
    inst, sim = build(allow_writes=True, max_output_pct=70.0)
    with pytest.raises(ValueError):
        inst.set_analog_percent(-1.0)
    assert writes(sim) == []


def test_the_ceiling_itself_is_allowed():
    inst, _ = build(allow_writes=True, max_output_pct=70.0)
    inst.set_analog_percent(70.0)
    assert inst.get_analog_percent() == pytest.approx(70.0, abs=0.02)


def test_the_default_ceiling_does_not_restrict():
    """Generic code must not invent a cryostat's limit; the config supplies it."""
    inst, _ = build(allow_writes=True)
    assert inst.max_output_pct == 100.0


# -- what actually goes on the wire -----------------------------------------

def test_only_the_trailing_value_changes():
    """A recomputed field that differed would change the output's MODE."""
    inst, sim = build(allow_writes=True)
    inst.set_analog_percent(12.5)
    assert writes(sim) == ["ANALOG 1, 0, 2, 1, 1,1,1,12.500"]


def test_the_configured_output_number_is_honoured():
    """Both directions.  A write to 2 confirmed by a readback of 1 is fiction."""
    inst, sim = build(allow_writes=True,
                      analog=AnalogOutputConfig(output=2, decimals=3))
    asked: list[str] = []
    original = sim.handle_query
    sim.handle_query = lambda cmd: (asked.append(cmd), original(cmd))[1]

    assert inst.analog.command(5.0).startswith("ANALOG 2,")
    inst.get_analog_percent()
    assert asked == ["AOUT? 2"]


# -- verification -----------------------------------------------------------

def test_a_write_is_confirmed_by_reading_it_back():
    inst, sim = build(allow_writes=True)
    inst.set_analog_percent(43.0)
    assert sim.analog_pct == pytest.approx(43.0, abs=0.02)


def test_dac_quantisation_is_not_mistaken_for_a_failed_write():
    """The DAC steps 0.01% and AOUT? answers to two decimals.

    An exact comparison would fail on the very value this cryostat actually uses.
    """
    inst, sim = build(allow_writes=True)
    inst.set_analog_percent(63.076)          # the cryostat's own operating point
    assert sim.analog_pct == pytest.approx(63.08, abs=1e-9)


def test_a_write_the_instrument_ignores_raises_rather_than_reporting_success():
    """The failure mode this exists for: a command that looks like it worked."""
    inst, sim = build(allow_writes=True)
    sim.handle_write = lambda cmd: sim.write_log.append(cmd)   # swallow it
    with pytest.raises(InstrumentError, match="NOT applied"):
        inst.set_analog_percent(43.0)


def test_verification_can_be_turned_off_for_a_loop_that_does_its_own():
    """A supervisor writing every cycle must not pay for a second query."""
    inst, sim = build(allow_writes=True, verify_writes=False)
    sim.handle_write = lambda cmd: sim.write_log.append(cmd)   # swallow it
    inst.set_analog_percent(43.0)            # no exception: nothing checked
    assert writes(sim) == ["ANALOG 1, 0, 2, 1, 1,1,1,43.000"]


def test_a_tolerance_too_tight_for_the_dac_reports_a_good_write_as_a_failure():
    """Documenting the trap, so nobody 'tightens' this and blames the box."""
    inst, _ = build(allow_writes=True, readback_tol_pct=1e-9)
    with pytest.raises(InstrumentError):
        inst.set_analog_percent(63.076)


# -- off --------------------------------------------------------------------

def test_analog_off_commands_zero():
    inst, sim = build(allow_writes=True)
    inst.set_analog_percent(20.0)
    inst.analog_off()
    assert sim.analog_pct == 0.0


# -- sensor units -----------------------------------------------------------

def test_sensor_units_are_off_by_default():
    """One transaction is one transaction.  The stock config's 1 Hz headroom is
    not something a diagnostic gets to spend without being asked."""
    inst, _ = build()
    _readings, aux = inst.read_frame()
    assert not [k for k in aux if "sensor" in k]
    assert inst.aux_keys() == ["ls218.aout1"]


def test_sensor_units_are_logged_when_asked_for():
    inst, _ = build(read_sensor_units=True)
    _readings, aux = inst.read_frame()
    assert [k for k in aux if ".sensor" in k] == [
        "ls218.sensor1", "ls218.sensor2", "ls218.sensor3"
    ]


def test_aux_keys_match_the_keys_read_frame_actually_produces():
    """The CSV header is written before the first frame, from aux_keys() alone.
    If the two ever disagree the recorder writes a column it never fills, or
    fills one the header never named."""
    for kw in ({}, {"read_sensor_units": True}):
        inst, _ = build(**kw)
        _readings, aux = inst.read_frame()
        assert inst.aux_keys() == list(aux)


def test_a_failed_srdg_does_not_discard_good_temperatures():
    """Same rule as AOUT?: a diagnostic column is never worth a frame."""
    inst, sim = build(read_sensor_units=True)

    def refuse(cmd):
        if cmd.startswith("SRDG?"):
            raise TransportError("no")
        return original(cmd)

    original = sim.handle_query
    sim.handle_query = refuse
    readings, aux = inst.read_frame()
    assert set(readings) == {"Sample", "Cold Head", "Shield"}
    assert not [k for k in aux if ".sensor" in k]
    assert "ls218.aout1" in aux
