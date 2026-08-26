"""Recorder, ring buffer and poller."""

import csv
import time

import pytest

from lschart.acquisition.poller import Poller
from lschart.acquisition.recorder import Recorder
from lschart.acquisition.ringbuffer import RingBuffer
from lschart.model import Frame, Reading, Validity
from lschart.transport import TransportError


def frame(t, **kelvin):
    return Frame(
        t_wall=1_800_000_000.0 + t,
        t_mono=t,
        readings={k: Reading(channel=k, kelvin=v) for k, v in kelvin.items()},
    )


# -- recorder ---------------------------------------------------------------

def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.reader(fh))


def test_every_row_matches_the_header(tmp_path):
    """The 336's channel names only arrive on the first read, so the header
    cannot be written until a frame is in hand."""
    rec = Recorder(str(tmp_path), channels=["Sample"], aux_keys=["heater_pct"])
    rec.write(frame(0.0, Sample=96.0))
    rec.write(frame(1.0, Sample=96.1, THE_CHONKE=290.6))
    rec.close()

    for path in tmp_path.glob("*.csv"):
        rows = read_csv(path)
        assert len(rows) >= 2
        width = len(rows[0])
        for i, row in enumerate(rows[1:], start=2):
            assert len(row) == width, f"{path.name} row {i}: {len(row)} vs {width}"


def test_a_late_channel_rolls_to_a_new_file_rather_than_corrupting(tmp_path):
    rec = Recorder(str(tmp_path), channels=["Sample"])
    rec.write(frame(0.0, Sample=96.0))
    first = rec.path
    rec.write(frame(1.0, Sample=96.1, Surprise=4.2))
    assert rec.path != first, "appended a wider row to an existing header"
    rec.close()
    assert len(list(tmp_path.glob("*.csv"))) == 2


def test_rejected_readings_are_recorded_with_their_reason(tmp_path):
    rec = Recorder(str(tmp_path), channels=["Sample"])
    f = Frame(t_wall=1_800_000_000.0, t_mono=0.0,
              readings={"Sample": Reading("Sample", 151.0, validity=Validity.INCOHERENT)})
    rec.write(f)
    rec.close()
    rows = read_csv(rec.path)
    assert "incoherent" in rows[1][rows[0].index("Validity")]
    # The value is still written: the log is the record of what the instrument
    # said, not of what the control loop chose to believe.
    assert "151" in rows[1][rows[0].index("Sample")]


def test_data_survives_without_a_clean_close(tmp_path):
    """Flush every sample: a power cut costs one sample, not an hour."""
    rec = Recorder(str(tmp_path), channels=["Sample"], flush_every_sample=True)
    for i in range(5):
        rec.write(frame(float(i), Sample=96.0 + i))
    assert len(read_csv(rec.path)) == 6      # header + 5, without closing


def test_no_row_limit(tmp_path):
    """The 65,536-row cap is what forced the cadence changes in the old logs."""
    rec = Recorder(str(tmp_path), channels=["Sample"], flush_every_sample=False)
    for i in range(2000):
        rec.write(frame(float(i), Sample=96.0))
    rec.close()
    assert rec.rows_written == 2000
    assert len(list(tmp_path.glob("*.csv"))) == 1


# -- ring buffer ------------------------------------------------------------

def test_ringbuffer_is_bounded():
    rb = RingBuffer(10)
    for i in range(50):
        rb.append(frame(float(i), Sample=96.0))
    assert len(rb) == 10 and rb.full


def test_ringbuffer_series_drops_unusable_samples():
    rb = RingBuffer(10)
    rb.append(frame(0.0, Sample=96.0))
    rb.append(Frame(t_wall=1.0, t_mono=1.0,
                    readings={"Sample": Reading("Sample", 151.0,
                                                validity=Validity.INCOHERENT)}))
    rb.append(frame(2.0, Sample=96.1))
    ts, ks = rb.series("Sample")
    assert ks == [96.0, 96.1], "plotted a rejected sample as if it were a measurement"
    assert rb.series("Sample", usable_only=False)[1] == [96.0, 151.0, 96.1]


# -- poller -----------------------------------------------------------------

class FakeInstrument:
    name = "fake"

    def __init__(self, fail=False):
        self.fail = fail
        self.reads = 0
        self.read_status = False

    def read_frame(self):
        self.reads += 1
        if self.fail:
            raise TransportError("link down")
        return {"Sample": Reading("Sample", 96.0)}, {"aout": 63.0}


def test_a_failed_instrument_does_not_stop_the_cycle():
    good, bad = FakeInstrument(), FakeInstrument(fail=True)
    p = Poller([bad, good], clock=lambda: 0.0)
    f = p.step()
    assert "Sample" in f.readings, "one dead instrument lost the whole frame"
    assert "fake" in f.errors


def test_a_lost_cycle_still_reaches_the_supervisor():
    """A gap is information.  Swallowing it lets a dead link look like a steady
    temperature, which is the one thing the guard must never be fooled by."""
    seen = []

    class Sup:
        def step(self, t, reading, readings=None):
            seen.append(reading)
            class S:
                state = type("X", (), {"value": "idle"})()
                wrote = False
                output_pct = None
                alarms = []
            return S()

    p = Poller([FakeInstrument(fail=True)], supervisor=Sup(),
               control_channel="Sample", clock=lambda: 0.0)
    p.step()
    assert seen == [None]


def test_a_supervisor_exception_does_not_stop_logging(tmp_path):
    class Exploding:
        def step(self, *a, **k):
            raise RuntimeError("boom")

    rec = Recorder(str(tmp_path), channels=["Sample"])
    p = Poller([FakeInstrument()], supervisor=Exploding(), control_channel="Sample",
               recorder=rec, clock=lambda: 0.0)
    p.step()
    rec.close()
    assert len(read_csv(rec.path)) == 2, "a control bug stopped the chart recorder"


def test_log_every_n_decimates_the_log_but_not_the_control(tmp_path):
    rec = Recorder(str(tmp_path), channels=["Sample"])
    p = Poller([FakeInstrument()], recorder=rec, log_every_n=5, clock=lambda: 0.0)
    for _ in range(10):
        p.step()
    rec.close()
    assert p.cycles == 10
    assert rec.rows_written == 2


def test_status_polling_is_throttled():
    """RDGST? per channel every cycle is what makes 1 Hz impossible."""
    inst = FakeInstrument()
    seen = []
    real = inst.read_frame

    def spy():
        seen.append(inst.read_status)
        return real()

    inst.read_frame = spy
    p = Poller([inst], status_every_n_cycles=4, clock=lambda: 0.0)
    for _ in range(8):
        p.step()
    assert seen.count(True) == 2, seen


# -- the acquisition thread --------------------------------------------------
#
# Everything above drives `step()` by hand.  What follows is the loop around
# it: the cadence, the thread, and the shutdown.  This is the part that has to
# survive months unattended, and it was the least exercised part of the module.


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class RecordingStop:
    """Stands in for the poller's stop Event, so the schedule is observable.

    `run` blocks on `_stop.wait(delay)`, so substituting this makes every
    sleep the loop asks for visible, lets it "pass" without any real time, and
    ends the loop after a fixed number of cycles.  No threads, no sleeping, and
    the cadence arithmetic is checked directly rather than inferred from
    wall-clock timings that would make the test flaky on a busy machine.
    """

    def __init__(self, clock, stop_after: int) -> None:
        self.clock = clock
        self.stop_after = stop_after
        self.waits: list[float] = []
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def clear(self) -> None:
        self._set = False

    def wait(self, delay: float) -> bool:
        self.waits.append(delay)
        self.clock.t += delay
        if len(self.waits) >= self.stop_after:
            self._set = True
        return self._set


class CostlyInstrument:
    """An instrument whose reads take a scripted amount of (virtual) time."""

    name = "costly"

    def __init__(self, clock, costs):
        self.clock = clock
        self.costs = list(costs)
        self.starts: list[float] = []

    def read_frame(self):
        self.starts.append(self.clock.t)
        self.clock.t += self.costs.pop(0) if self.costs else 0.0
        return {"Sample": Reading("Sample", 96.0)}, {}


def test_the_cadence_does_not_drift_when_a_cycle_runs_long():
    """The deadline comes from the previous *deadline*, not from the time the
    last cycle happened to finish.

    Otherwise every slow cycle retards the schedule permanently, and a log that
    is meant to be on a 1 s grid quietly becomes 1.4 s after an afternoon of
    retries.
    """
    clock = Clock()
    inst = CostlyInstrument(clock, [0.4, 0.9, 0.4, 0.4])
    p = Poller([inst], interval_s=1.0, clock=clock)
    p._stop = RecordingStop(clock, stop_after=4)
    p.run()

    assert inst.starts == [0.0, 1.0, 2.0, 3.0], "the cycle drifted off its grid"


def test_a_cycle_that_overran_badly_resets_rather_than_bursting():
    """Catching up would mean firing several cycles back to back, which is the
    one thing a paced GPIB bus cannot absorb.  The schedule restarts from now."""
    clock = Clock()
    inst = CostlyInstrument(clock, [3.0, 0.1, 0.1])
    p = Poller([inst], interval_s=1.0, clock=clock)
    p._stop = RecordingStop(clock, stop_after=2)
    p.run()

    # The overrun cycle goes straight round without waiting, and the schedule
    # then restarts from where it actually is.  Without the reset the deadline
    # would still be back at 1.0 s and the next two cycles would fire with no
    # delay at all, working off a backlog into a bus that cannot absorb it --
    # so every recorded wait being a real one is the whole assertion.
    assert inst.starts == [0.0, 3.0, 4.0]
    assert p._stop.waits == [pytest.approx(0.9), pytest.approx(0.9)], "burst-fired"


def test_a_cycle_that_raises_does_not_kill_the_acquisition_thread():
    """A months-long run must not end because one cycle threw.

    `step` already contains per-instrument failures; this is the backstop for
    everything else -- a recorder bug, a supervisor bug, a full disk.
    """
    clock = Clock()
    calls = []

    class Exploding:
        name = "boom"

        def read_frame(self):
            calls.append(clock.t)
            raise RuntimeError("not a TransportError")

    p = Poller([Exploding()], interval_s=1.0, clock=clock)
    p._stop = RecordingStop(clock, stop_after=3)
    p.run()

    assert len(calls) == 3, "the loop stopped at the first exception"


def test_starting_and_stopping_actually_runs_cycles():
    """The real thread, once: everything else here substitutes the event."""
    inst = FakeInstrument()
    p = Poller([inst], interval_s=0.001)
    p.start()
    try:
        deadline = time.monotonic() + 5.0
        while p.cycles < 3 and time.monotonic() < deadline:
            time.sleep(0.001)
    finally:
        p.stop(timeout=5.0)

    assert p.cycles >= 3, "the thread never ran a cycle"
    assert p._thread is None
    assert inst.reads >= 3


def test_starting_a_second_time_is_refused():
    """Two acquisition threads would both drive the bus -- see the lock."""
    p = Poller([FakeInstrument()], interval_s=0.001)
    p.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            p.start()
    finally:
        p.stop(timeout=5.0)


def test_stopping_one_that_never_started_is_harmless():
    """`stop` runs on the shutdown path, which is reached from failures too."""
    Poller([FakeInstrument()]).stop()


def test_it_can_be_used_as_a_context_manager():
    inst = FakeInstrument()
    with Poller([inst], interval_s=0.001) as p:
        deadline = time.monotonic() + 5.0
        while p.cycles < 1 and time.monotonic() < deadline:
            time.sleep(0.001)
    assert p._thread is None, "left the thread running"
    assert p.cycles >= 1
