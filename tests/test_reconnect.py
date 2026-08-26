"""Losing a link must not be terminal.

A recorder is expected to run for months unattended, so every case here is
about the difference between "recorded a gap and carried on" and "stopped
recording until a human noticed".
"""

import pytest

from lschart.acquisition.poller import Poller
from lschart.transport import Transport, TransportError


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


class FlakyTransport(Transport):
    """A link that can be broken and mended from the test."""

    def __init__(self, clock, **kw):
        kw.setdefault("clock", clock)
        super().__init__(**kw)
        self.inter_command_delay = 0.0
        self.up = True                 # can the underlying link be opened?
        self.answering = True          # does it reply once open?
        self.connects = 0
        self.disconnects = 0

    def __str__(self):
        return "flaky"

    def _connect(self):
        self.connects += 1
        if not self.up:
            raise OSError("device not present")

    def _disconnect(self):
        self.disconnects += 1

    def _write(self, cmd):
        if not self.answering:
            raise OSError("write timeout")

    def _query(self, cmd):
        if not self.answering:
            raise OSError("read timeout")
        return "+096.0000"


# -- lazy opening -----------------------------------------------------------

def test_constructing_a_transport_touches_no_hardware():
    """A recorder must start on a cryostat that is only half powered on."""
    t = FlakyTransport(Clock())
    assert t.connects == 0
    assert not t.is_up


def test_a_missing_instrument_does_not_prevent_construction():
    t = FlakyTransport(Clock())
    t.up = False
    with pytest.raises(TransportError, match="could not open"):
        t.query("KRDG? 0")
    assert not t.is_up


def test_the_link_opens_on_first_use():
    t = FlakyTransport(Clock())
    assert t.query("KRDG? 0") == "+096.0000"
    assert t.is_up and t.connects == 1


def test_a_second_transaction_does_not_reopen():
    t = FlakyTransport(Clock())
    t.query("KRDG? 0")
    t.query("KRDG? 0")
    assert t.connects == 1


# -- backoff ----------------------------------------------------------------

def test_retries_are_not_attempted_faster_than_the_backoff():
    """A box that is off for a weekend must not be hammered every poll."""
    clock = Clock()
    t = FlakyTransport(clock, retry_min_s=1.0)
    t.up = False
    with pytest.raises(TransportError):
        t.query("x")
    assert t.connects == 1
    # Immediately again: refused without touching the hardware.
    with pytest.raises(TransportError, match="next reconnect attempt"):
        t.query("x")
    assert t.connects == 1, "no second attempt before the backoff expires"


def test_the_backoff_widens_and_is_capped():
    clock = Clock()
    t = FlakyTransport(clock, retry_min_s=1.0, retry_max_s=4.0)
    t.up = False
    delays = []
    for _ in range(6):
        with pytest.raises(TransportError):
            t.query("x")
        delays.append(t._next_retry_at - clock.t)
        clock.advance(delays[-1])
    assert delays[0] == 1.0
    assert delays[1] == 2.0
    assert delays[2] == 4.0
    assert max(delays) == 4.0, "capped, so a long outage is retried steadily"


def test_a_successful_reconnect_resets_the_backoff():
    """Otherwise one bad weekend leaves every later blip retried at 30 s."""
    clock = Clock()
    t = FlakyTransport(clock, retry_min_s=1.0, retry_max_s=8.0)
    t.up = False
    for _ in range(3):
        with pytest.raises(TransportError):
            t.query("x")
        clock.advance(10.0)
    t.up = True
    t.query("x")
    assert t._backoff == 1.0


# -- one timeout is not a dead bus ------------------------------------------

def test_a_single_failure_does_not_drop_the_link():
    """One GPIB timeout is usually a slow instrument, not a dead board."""
    t = FlakyTransport(Clock(), failures_before_reconnect=3)
    t.query("x")
    t.answering = False
    with pytest.raises(TransportError):
        t.query("x")
    assert t.is_up, "still connected after one failure"
    assert t.disconnects == 0


def test_repeated_failures_drop_and_reopen_the_link():
    clock = Clock()
    t = FlakyTransport(clock, failures_before_reconnect=3, retry_min_s=1.0)
    t.query("x")
    t.answering = False
    for _ in range(3):
        with pytest.raises(TransportError):
            t.query("x")
    assert not t.is_up
    assert t.disconnects == 1

    t.answering = True
    clock.advance(2.0)
    assert t.query("x") == "+096.0000"
    assert t.is_up and t.connects == 2


def test_a_success_clears_the_failure_count():
    """Failures have to be *consecutive*, or an occasional blip eventually
    trips the threshold for no good reason."""
    t = FlakyTransport(Clock(), failures_before_reconnect=3)
    t.query("x")
    for _ in range(2):
        t.answering = False
        with pytest.raises(TransportError):
            t.query("x")
        t.answering = True
        t.query("x")
    assert t.consecutive_failures == 0
    assert t.disconnects == 0


def test_reconnection_can_be_turned_off():
    t = FlakyTransport(Clock(), reconnect=False)
    t.up = False
    with pytest.raises(TransportError, match="could not open"):
        t.query("x")
    with pytest.raises(TransportError, match="reconnect is disabled"):
        t.query("x")


# -- what the rest of the program sees --------------------------------------

def test_the_link_state_is_observable():
    """A link that is down should be visible, not merely absent from the log."""
    clock = Clock()
    t = FlakyTransport(clock, failures_before_reconnect=1)
    t.query("x")
    assert t.is_up and t.last_error is None

    t.answering = False
    with pytest.raises(TransportError):
        t.query("x")
    assert not t.is_up
    assert "read timeout" in t.last_error


def test_polling_continues_across_an_outage_and_records_the_gap():
    """The whole point: a dead link costs cycles, not the run."""
    clock = Clock()
    t = FlakyTransport(clock, failures_before_reconnect=1, retry_min_s=0.0)

    class Inst:
        name = "flaky"

        def read_frame(self):
            return {"Sample": t.query("KRDG? 0")}, {}

    poller = Poller([Inst()], interval_s=1.0, clock=clock)
    assert poller.step().readings, "healthy"

    t.answering = False
    frame = poller.step()
    assert frame.errors and not frame.readings, "the gap is recorded, not hidden"

    t.answering = True
    clock.advance(1.0)
    assert poller.step().readings, "recovered without a restart"
    assert poller.cycles == 3 and poller.dropped_cycles == 1
