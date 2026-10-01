"""**The watt residual is taken at the instant its slope describes.**

PID_PLAN.md 4a-vii; docs/ltspm3/safety.md rule 4.

``dQ = C dT/dt + [Lambda(T) - Lambda(T_c)] - P(u)`` gets ``dT/dt`` from a
30 s regression, which is the slope at the centre of its window, ~16 s ago.
Until 2026-10-01 the other three terms were read NOW.  At a hold and during a
steady sweep that makes no difference.  While the rate is changing it does:
the onset of a 10 K/min move put +108 mW into `dQ` on the cryostat and
faulted the loop (2026-10-01 10:12, 250 -> 145 K), and a 5 K move faulted the
same way upside down (2026-09-30 13:36, 150 -> 155 K).  The heat capacity
was measured over the same minutes, with the heater frozen, at 0.999 +- 0.018
of the model's.  So the model was right and the residual was comparing two
different instants.

Four things are graded here:

* the filter says which instant its slope is from, and the slope really is
  the derivative there;
* on the fitted bench, fast moves at the warm end take the step test nowhere
  near a fault;
* a heater that genuinely stops delivering in the middle of a move still
  faults -- moving the residual 16 s back must not blind it;
* **the two moves that faulted on the cryostat, replayed through the shipped
  supervisor from what the recorder logged, do not fault**, and the residual
  keeps an opinion through them rather than going quiet.

The last one is the only test here on genuine data.  The fixture is the two
events as the recorder wrote them, five columns, nothing derived.
"""
from __future__ import annotations

import csv
import gzip
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bench_plant import STAGE_FILE, FittedHarness

from lschart.model import Reading
from ltspm3.control import SupervisorState
from ltspm3.control.filters import MeasurementFilter
from ltspm3.model import fitted_response as M

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "onset_events.csv.gz"

#: The recorder's timestamps are the lab's local time, which is PDT on both
#: days the fixture covers.  Pinned rather than read from the machine, so the
#: band's drift term -- dated in unix seconds -- is the same on a CI runner in
#: UTC as it was on the cryostat.
LAB_TZ = timezone(timedelta(hours=-7))


def stepped(st) -> bool:
    return any("residual stepped" in a for a in (st.alarms or []))


# -- the filter knows when its slope is from ---------------------------------


def feed(filt, signal, n, dt=2.0):
    t = 0.0
    for _ in range(n):
        t += dt
        filt.update(t, signal(t), dt)
    return t


def test_the_anchor_is_where_the_slope_is_from():
    """A constant acceleration, evenly sampled: the least-squares slope is the
    derivative at the window's centre exactly, and the median of three puts
    one more sample of lag on it.  The anchor is that instant, and with a
    full window it is `slope_delay_s` ago, to the arithmetic."""
    v, a = -0.05, 0.002
    signal = lambda t: 200.0 + v * t + 0.5 * a * t * t   # noqa: E731
    filt = MeasurementFilter(tau=0.0, median_window=3)
    now = feed(filt, signal, 40)
    t_anchor, kelvin = filt.slope_anchor(2.0)

    assert now - t_anchor == pytest.approx(filt.slope_delay_s(2.0), abs=1e-9)
    assert filt._slope_value == pytest.approx(v + a * t_anchor, rel=1e-9)
    # The window's mean of a parabola sits half its variance times the
    # curvature above the parabola at the centre -- 0.07 K here, and nothing
    # on a straight ramp.
    assert kelvin == pytest.approx(signal(t_anchor), abs=0.1)


def test_on_a_ramp_the_anchor_temperature_is_exact():
    filt = MeasurementFilter(tau=0.0, median_window=3)
    feed(filt, lambda t: 100.0 + 0.1 * t, 40)
    t_anchor, kelvin = filt.slope_anchor(2.0)
    assert kelvin == pytest.approx(100.0 + 0.1 * t_anchor, abs=1e-9)


def test_no_anchor_until_the_slope_is_primed():
    """The same rule `SlopeEstimator.update` reports a slope by: half a
    window.  Before it, the residual has no opinion rather than one built on
    a slope of zero."""
    filt = MeasurementFilter(tau=0.0, median_window=3)
    feed(filt, lambda t: 100.0, filt.slope.window // 2 - 1)
    assert filt.slope_anchor(2.0) is None
    feed(filt, lambda t: 100.0, 1)
    assert filt.slope_anchor(2.0) is not None


# -- the bench ---------------------------------------------------------------


def moved(start_k, end_k, minutes=15, **kw):
    """Arm, hold five minutes, then move.  `h.hold` is the hold's history and
    `h.history` the move's."""
    h = FittedHarness(kelvin=start_k, stage=STAGE_FILE, settled=True, **kw)
    h.sup.arm(h.sup.status.filtered_k)
    h.minutes(5)
    h.hold = list(h.history)
    h.history.clear()
    h.sup.set_setpoint(end_k)
    h.minutes(minutes)
    return h


#: Where the 10 K/min onset bites hardest: the warm end, where C is largest,
#: and the two moves the cryostat faulted on.  Measured before the change: a
#: step of 55.6 / 54.8 / 34.2 / 20.2 mW, every one past the 10 mW floor, and a
#: worst `dQ` 2.5 to 3.3 times its own 3 sigma.  After: 3.2 to 7.8 mW.
FAST_MOVES = [(240.0, 200.0), (200.0, 240.0), (150.0, 155.0), (120.0, 122.0)]


@pytest.mark.parametrize("start_k,end_k", FAST_MOVES)
def test_a_fast_move_takes_the_step_test_nowhere_near_a_fault(start_k, end_k):
    """A plant the model describes exactly, moved at the file's one rate.

    Graded against `fault_mw` itself -- the FLOOR under the step test's
    threshold -- rather than against the threshold the band raises it to,
    because the band widening during a move is exactly what let a 56 mW step
    pass here unremarked while a 75 mW one faulted the cryostat.
    """
    h = moved(start_k, end_k, delivered_frac=1.0)
    floor_w = h.sup.cfg.fault_mw * 1e-3
    worst = max(s.dq_step_w for s in h.history)
    assert worst < floor_w, f"{1e3 * worst:.1f} mW step on {start_k} -> {end_k} K"
    assert {s.state for s in h.history} == {SupervisorState.TRACKING}
    # **AND IT STILL HAS AN OPINION.**  The cheap way to pass the row above is
    # a residual that goes quiet while the cryostat moves.
    judged = sum(1 for s in h.history if s.missing_power_w is not None)
    assert judged >= 0.95 * len(h.history), f"{judged}/{len(h.history)} judged"


#: How far past the hold's own scatter a moving cycle may sit before it counts
#: as the move's doing.  Four sigma of the same plant held still, so a few
#: hundred cycles of honest noise do not reach it.
HOLD_NOISE_SIGMA = 4.0
#: The most a move's worst cycle may exceed that allowance by.  Measured
#: 0.63 / 1.53 / 1.07 / 0.78 after the change, and 3.57 / 8.13 / 11.25 / 13.42
#: before it.  Not 1.0: what is left is the regression's own reach at a CORNER
#: -- a 30 s window centred 16 s back still sees a little of the next moment --
#: and it costs an occasional warning at the onset or the landing, never a
#: fault (the step test above grades that).
CORNER_EXCESS = 2.0


@pytest.mark.parametrize("start_k,end_k", FAST_MOVES)
def test_the_move_s_residual_stays_near_its_band(start_k, end_k):
    """While the trajectory is moving, measured against whichever is larger:
    the band `dQ` is granted, or the scatter the same plant already had at
    the hold before it.

    **The second half is the bench's thermometer, not this change.**  On the
    gauge day the settled band at 240 K is 1.8 mW 3 sigma, while `C` times the
    slope's own noise is about 2.5 mW rms there (`noise_quadratic * T**2` is
    78 mK), so a still plant crosses its band on a quarter of cycles before
    anything moves.  That belongs to the hold, and it is the same before and
    after the change.
    """
    h = moved(start_k, end_k, delivered_frac=1.0)
    still = [s.missing_power_w for s in h.hold if s.missing_power_w is not None]
    noise = (sum(x * x for x in still) / len(still)) ** 0.5
    moving = [s for s in h.history
              if s.ramping and s.missing_power_w is not None]
    assert moving
    worst = max(abs(s.missing_power_w) / max(
        h.sup.cfg.warn_sigma * s.sigma_q_w, HOLD_NOISE_SIGMA * noise)
        for s in moving)
    assert worst < CORNER_EXCESS, f"{worst:.2f}x its allowance"


#: The 12 % loss the bench faults on (`FittedResponse.delivered_frac`).
LOSS_FRAC = 0.88
#: How soon the step test must call it.  Measured 18-36 s over every case
#: below; before the change the same cases took 46-90 s where they were called
#: at all, and the change as first written -- `slope_lag` still in the step
#: band -- never called six of them.
CALLED_WITHIN_S = 60.0


@pytest.mark.parametrize("start_k,end_k", [
    (200.0, 240.0), (240.0, 200.0), (150.0, 180.0), (180.0, 150.0)])
@pytest.mark.parametrize("into_move_s", [0.0, 30.0, 120.0, 240.0])
def test_a_heater_that_stops_delivering_mid_move_is_called(
        start_k, end_k, into_move_s):
    """**Moving the residual back 16 s must not blind it.**  An eighth of the
    heater's power gone somewhere inside a 10 K/min move, up and down, and
    the step test calls it inside a minute.

    This is the row the old residual failed in one direction and the first
    draft of this change failed in the other.  Before the change, a loss
    during a DESCENT was never called by the step test, because the onset's
    false residual had the opposite sign and raised the band.  With the
    residual aligned but `slope_lag` still in the step band, a loss 30 s or
    more into a CLIMB was never called, because the window's high sample was
    the onset's and carried the onset's band.  Both were eventually caught by
    the loop railing, 280-550 s later.
    """
    h = FittedHarness(kelvin=start_k, stage=STAGE_FILE, settled=True,
                      delivered_frac=1.0)
    h.sup.arm(h.sup.status.filtered_k)
    h.minutes(5)
    h.sup.set_setpoint(end_k)
    if into_move_s:
        h.step(int(round(into_move_s / h.DT)))
    h.history.clear()
    t_loss = h.clock.t
    h.deliver(LOSS_FRAC)
    h.minutes(5)
    called = [s for s in h.history if stepped(s)]
    assert called, "a 12 % loss in delivered power was never called"
    assert called[0].t - t_loss <= CALLED_WITHIN_S, called[0].t - t_loss


# -- the cryostat, replayed --------------------------------------------------


def _load_event(name):
    rows = []
    with gzip.open(FIXTURE, "rt", newline="") as f:
        for r in csv.DictReader(f):
            if r["event"] == name:
                rows.append(r)
    assert rows, f"no event {name!r} in {FIXTURE.name}"
    return rows


def _unix(stamp: str) -> float:
    return datetime.fromisoformat(stamp).replace(tzinfo=LAB_TZ).timestamp()


def replay(name, *, arm_k, move_at, move_to_k):
    """Drive the shipped supervisor from what the recorder logged.

    Readings in, and THE LOGGED HEATER forced in as the output in force each
    cycle -- the one the previous row wrote -- so the residual sees what the
    cryostat was given rather than what this replay's loop would have done.
    What the loop writes goes to the bench's simulator and nowhere.  Armed
    after a minute of priming, and the move commanded at the moment the
    operator commanded it.
    """
    rows = _load_event(name)
    t0 = _unix(rows[0]["Timestamp"])
    h = FittedHarness(kelvin=min(arm_k, M.T_MAX_K - 1.0), stage=STAGE_FILE,
                      wall_t0=0.0)
    t_move = _unix(move_at)
    armed = moved_yet = False
    out, u_prev = [], float(rows[0]["heater_pct"])
    for r in rows:
        t = _unix(r["Timestamp"])
        if not armed and t - t0 >= 60.0:
            h.sup.arm(arm_k)
            armed = True
        if not moved_yet and t >= t_move:
            h.sup.set_setpoint(move_to_k)
            moved_yet = True
        h.clock.t = t
        h.sup.output_pct = u_prev
        readings = {c: Reading(channel=c, kelvin=float(r[c]))
                    for c in ("Sample", "Coldplate", "Magnet")}
        st = h.sup.step(t, readings["Sample"], readings)
        out.append((t, st))
        u_prev = float(r["heater_pct"])
    return [(t - t_move, st) for t, st in out]


#: (fixture event, armed at, the command's own timestamp, where it went to).
#: The commands are the status file's `recent` list for 10-01 and the Notes
#: column's `[matlab]` line for 09-30.
EVENTS = [
    ("2026-10-01_250_to_145", 250.0, "2026-10-01T10:10:08.187", 145.0),
    ("2026-09-30_150_to_155", 150.0, "2026-09-30T13:36:35.000", 155.0),
]


@pytest.mark.parametrize("name,arm_k,move_at,move_to_k", EVENTS)
def test_the_moves_that_faulted_on_the_cryostat_neither_fault_nor_warn(
        name, arm_k, move_at, move_to_k, caplog):
    """**THE TWO REAL FAULTS.**

    Before the change, the 10-01 replay faults 116 s after the command --
    10:12:04, the second the cryostat did -- on a 104 mW step, and the 09-30
    one warns on 9 cycles of the move at up to 90 mW.  The 09-30 fault itself
    was a single cycle 0.5 mW past the 10 mW floor, called against the hours
    of history the cryostat had and not reproduced by a replay that starts six
    minutes before.  After: no fault, no warning, `dQ` at most 15 and 18 mW.
    """
    caplog.set_level(logging.ERROR)
    out = replay(name, arm_k=arm_k, move_at=move_at, move_to_k=move_to_k)
    faulted = [(dt, st) for dt, st in out if stepped(st)]
    assert not faulted, (
        f"step fault {faulted[0][0]:.0f} s after the command: "
        f"{[a for a in faulted[0][1].alarms if 'stepped' in a][0]}")
    assert not ({SupervisorState.FROZEN, SupervisorState.RAMPING_DOWN}
                & {st.state for _, st in out})
    warned = [(dt, a) for dt, st in out if dt >= 0.0
              for a in st.alarms if "missing power" in a]
    assert not warned, warned[:3]

    # **AND THE RESIDUAL WAS WATCHING.**  From one slope-age after the sample
    # is inside the table, every cycle of the move has an opinion.  The 250 K
    # hold is 0.9 K past the top of the table and silent by design, so on
    # 10-01 that is the first ~35 s; on 09-30 it is the whole move.
    inside = next(dt for dt, st in out
                  if dt >= 0.0 and st.filtered_k is not None
                  and st.filtered_k <= M.T_MAX_K)
    move = [st for dt, st in out if inside + 20.0 <= dt <= inside + 200.0]
    silent = [st.residual_reason for st in move if st.missing_power_w is None]
    assert move and not silent, silent[:3]
