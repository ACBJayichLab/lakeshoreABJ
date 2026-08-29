"""What `lschart`'s panic commands actually do to a real software loop.

`tests/test_ipc_service.py` pins the command side against a stand-in, and a
stand-in by construction agrees with whatever the service asks of it.  These
tests put the real supervisor on the other end, because the defect they exist
to prevent lived exactly in the gap between the two: the service believed it
had turned a heater off, the stand-in agreed, and the supervisor -- which was
never told -- put the heat back on four minutes later.

The shape of that bug is worth keeping in mind whenever this seam grows:

    `heaters_off` wrote `ANALOG 0` straight to the 218, around the supervisor.
    The supervisor stayed in PID mode with `output_pct` still remembering
    63.08%.  The sample fell, the guard tripped, the loop held for
    `anomaly_hold_s`, and then began its fault ramp-down *from the remembered
    value* -- commanding 63.05% onto a heater an operator had just cut, then
    walking it down over two hours.  Every layer behaved exactly as designed.
    The only thing wrong was that one of them was reasoning from memory about a
    world somebody else had changed.

So: nothing here asserts on an internal.  Each test asks the one question an
operator would ask, which is what the heater is doing some time later.

This file lives in `tests_ltspm3/` and not in `tests/` because it is the one
directory where the two halves are allowed to meet -- the same reason
`test_status_projection.py` is here.
"""

from __future__ import annotations

import pytest

from lschart.ipc.commands import CommandSpool
from lschart.ipc.service import IpcService
from lschart.ipc.status import read_status
from lschart.model import Frame
from ltspm3.control import LoopMode, SupervisorState


def wire(tmp_path, harness, **kw):
    """An `IpcService` holding the real 218 and the real supervisor.

    `software_loop` is the duck-typed object `lschart.app.Application` normally
    supplies.  Building the whole Application here would drag in a poller and a
    thread; what the service actually reaches for is five names, and the
    supervisor is what stands behind all five.
    """
    kw.setdefault("accept_commands", True)
    svc = IpcService(
        status_path=tmp_path / "status.json",
        spool=CommandSpool(tmp_path / "commands"),
        instruments=[harness.inst],
        **kw,
    )
    svc.software_loop = SoftwareLoop(harness.sup)
    svc.poller = FakePoller(harness)
    svc.start()
    return svc


class FakePoller:
    """What the status writer reads off the poller, duck-typed.

    Only four names, all read with `getattr(..., None)`.  `last_control_status`
    is the live `SupervisorStatus` -- not a copy -- so a status file written
    after `harness.step()` reflects the cycle that just ran.
    """

    def __init__(self, harness):
        self.h = harness
        self.control_channel = "Sample"
        self.supervisor = harness.sup

    @property
    def last_control_status(self):
        return self.h.sup.status

    @property
    def last_frame(self):
        return Frame(t_wall=0.0, t_mono=self.h.clock.t, readings=self.h.read())


class SoftwareLoop:
    """`Application`'s half of the seam, with the supervisor behind it.

    Deliberately a copy of what `lschart.app.Application` does rather than an
    import of it: what is under test is that the *names* line up, and a helper
    that imported the real wrapper would pass on a supervisor that had renamed
    every method underneath it.
    """

    def __init__(self, sup):
        self.sup = sup
        self.has_loop = True

    def hold(self):
        return f"software loop OPEN, heater frozen at {self.sup.panic_hold():.3f}%"

    def disarm(self):
        return f"software loop DISARMED, releasing {self.sup.panic_off():.3f}%"

    def acknowledge(self):
        self.sup.acknowledge()
        return "lockout cleared; the loop is disarmed -- `arm` to close it again"

    def arm(self, setpoint_k=None):
        self.sup.arm(setpoint_k if setpoint_k is not None else self.sup.filter.value)


def apply(svc, harness, kind, **kw):
    """Queue one command and let the cycle it lands on run, as the poller would."""
    cid = svc.spool.submit(kind, **kw)
    readings = harness.read()
    svc.on_frame(Frame(t_wall=0.0, t_mono=harness.clock.t, readings=readings))
    for entry in read_status(svc.writer.path)["commands"]["recent"]:
        if entry["id"] == cid:
            return entry
    raise AssertionError(f"no acknowledgement for {cid}")


def snapshot(svc, harness) -> dict:
    """Write and read one status file, for the cycle that has just run.

    Separate from :func:`apply` because the file is written *during* a cycle:
    reading it after stepping the loop further would hand back the previous
    cycle's answers.
    """
    svc.on_frame(Frame(t_wall=0.0, t_mono=harness.clock.t, readings=harness.read()))
    return read_status(svc.writer.path)


@pytest.fixture
def armed_service(tmp_path, armed):
    h = armed()
    return wire(tmp_path, h, allow_analog_output=True), h


# -- heaters_off -------------------------------------------------------------


def test_heaters_off_leaves_the_heater_off(armed_service):
    """The whole point, stated the way an operator would state it.

    Half an hour of cycles after the button, with the sample falling the whole
    time and the loop watching it fall.  Nothing may put the heat back.
    """
    svc, h = armed_service
    assert h.inst.get_analog_percent() > 60.0, "not actually heating to begin with"

    assert apply(svc, h, "heaters_off")["ok"]
    assert h.inst.get_analog_percent() == 0.0

    peak = 0.0
    for _ in range(450):                      # 30 minutes at the 4 s cadence
        h.step(1)
        peak = max(peak, h.inst.get_analog_percent())
    assert peak == 0.0, f"the heater came back to {peak:.3f}% after heaters_off"


def test_heaters_off_disarms_rather_than_freezing(armed_service):
    """`hold` would not have been enough, and this is why.

    A manual output is still clamped to the authority band, so a *held* loop
    cannot sit at zero -- it would be pulled up to the bottom of the band.  Off
    is the only mode that writes nothing at all.
    """
    svc, h = armed_service
    apply(svc, h, "heaters_off")

    assert h.sup.mode is LoopMode.OFF
    band_low, _ = h.sup.band
    assert band_low > 60.0, "the band no longer makes this test meaningful"
    h.step(50)
    assert h.inst.get_analog_percent() == 0.0


def test_a_disarmed_loop_stops_claiming_an_output(armed_service):
    """The lie the first fix left behind, caught on a live recorder.

    `output_pct` is what the loop last *commanded*, and it went on being
    reported after the loop had let go -- so `status.json` said the software
    loop was holding 63.08% while `ls218.aout1` beside it said 0.00, and the
    CSV wrote both numbers into the same row.  A log that disagrees with itself
    about whether the heater is on is worse than one that admits it does not
    know: null is the honest answer, and the instrument's own reading still
    carries the truth.
    """
    svc, h = armed_service
    apply(svc, h, "heaters_off")
    h.step(3)

    control = snapshot(svc, h)["control"]
    assert control["output_pct"] is None
    assert control["mode"] == "off"
    assert h.inst.get_analog_percent() == 0.0


def test_heaters_off_does_not_quietly_clear_a_lockout(tmp_path, armed):
    """Stopping the heater is not the same as having looked at the cryostat.

    Otherwise the panic button doubles as an acknowledge, and the latch that
    exists to make somebody investigate is undone by the reflex that means
    nobody has yet.
    """
    h = armed()
    h.sup.state = SupervisorState.LOCKED_OUT
    h.sup._locked_reason = "sensor fault"
    svc = wire(tmp_path, h, allow_analog_output=True)

    apply(svc, h, "heaters_off")
    assert h.sup.state is SupervisorState.LOCKED_OUT


# -- the loop is not reasoning from memory -----------------------------------


def test_the_next_move_is_computed_from_where_the_heater_is(armed_service):
    """The general form of the bug, without going through a command at all.

    `send analog` writes the same bytes `heaters_off` does, and so does a hand
    at the front panel.  The supervisor cannot prevent any of that.  What it
    must not do is compute its next *relative* move from a value that stopped
    being true -- every limit it enforces is a limit on a step from *here*.

    In MANUAL the operator has asked for a particular output, so restoring it
    is right; doing so one rate-limited step at a time from where the heater
    actually is, rather than not noticing at all, is the behaviour under test.
    A supervisor reasoning from memory sees `code == output_pct`, writes
    nothing, and stays blind to the change indefinitely.
    """
    svc, h = armed_service
    h.sup.set_mode(LoopMode.MANUAL)          # frozen where it is, still clamped
    h.step(2)
    asked_for = h.sup.manual_pct
    assert asked_for > 62.9, "the manual setpoint is not where this test assumes"

    h.inst.set_analog_percent(62.5)          # somebody else moves it, mid-band
    h.step(5)

    moved = h.inst.get_analog_percent()
    assert moved > 62.5, "the loop never noticed the output had moved"
    # And it walked, rather than jumping straight back to the remembered value:
    # five cycles of `max_step_pct` is 0.1%, nowhere near the 0.58% gap.
    assert moved < asked_for - 0.2


def test_a_hold_freezes_where_the_heater_is_not_where_it_was(armed_service):
    """`panic_hold` adopts the present output.  It has to read it to know it.

    And the number in the reply is the number the heater keeps: the message an
    operator is shown used to be one the loop was about to overwrite.
    """
    svc, h = armed_service
    h.inst.set_analog_percent(62.2)          # moved by something else

    message = apply(svc, h, "hold")["message"]
    assert "62.200" in message

    h.step(100)
    assert h.inst.get_analog_percent() == pytest.approx(62.2, abs=1e-9)


# -- ack ---------------------------------------------------------------------


def test_a_locked_out_loop_refuses_to_arm_and_names_the_way_out(armed_service):
    """A refusal that names a Python method is a signpost pointing at a wall."""
    svc, h = armed_service
    h.sup.state = SupervisorState.LOCKED_OUT
    h.sup._locked_reason = "anomaly persisted"

    entry = apply(svc, h, "arm")
    assert entry["ok"] is False
    assert "send ack" in entry["message"]


def test_ack_then_arm_is_the_whole_way_back(armed_service):
    """Two acts, on purpose: clear the latch, look at the cryostat, then close."""
    svc, h = armed_service
    h.sup.state = SupervisorState.LOCKED_OUT
    h.sup._locked_reason = "sensor fault"

    assert apply(svc, h, "ack")["ok"]
    assert h.sup.state is SupervisorState.IDLE
    assert h.sup.mode is LoopMode.OFF, "ack must not resume the loop by itself"

    h.step(20)                                # a usable reading to arm onto
    assert apply(svc, h, "arm")["ok"]
    assert h.sup.mode is LoopMode.PID
