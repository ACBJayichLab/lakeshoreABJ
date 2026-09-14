"""The judge, on synthetic cryostats and on the real archive.

PID phase 2.  Two halves, and they answer different questions.

The **unit** half drives :class:`Judge` with samples built here, so a rule can
be tested one at a time: the sign of the residual, what the baseline absorbs,
that it freezes at a warning, what has no opinion.  Nothing in it touches a
file.

The **replay** half is plan 2 section 2.3's table, on
``reference/cooldown-10/`` -- versioned, so it runs from a fresh clone, and the
only test in this repository on genuine data that the model was not fitted to
window by window.  It is slow (about half a minute for 1.06 million samples),
so it runs once per session and every assertion reads the same result.
"""

from __future__ import annotations

import datetime as _dt
import math
from pathlib import Path

import pytest

from ltspm3.model import fitted_response as M
from ltspm3.monitor import NO_OPINION, TYPICAL, WARN, Judge, MonitorConfig
from ltspm3.monitor.judge import RollingFit
from ltspm3.monitor.source import Sample, replay

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "reference" / "cooldown-10"


def at(kelvin: float) -> float:
    """The output that holds a temperature, by the table's own reckoning."""
    return M.percent_for_power(M.steady_power_w(kelvin))


def run(judge: Judge, *, kelvin: float, pct: float | None = None,
        seconds: float, t0: float = 0.0, dt: float = 2.0,
        coldplate: float | None = None, segment: int = 0):
    """Hold a temperature at an output and return the last record.

    Absolute times are unix-like and fixed, never ``time.time()``: a test
    anchored to today passes every morning and fails every evening, and this
    repository has been bitten by that once already.
    """
    pct = at(kelvin) if pct is None else pct
    tc = M.coldplate_k(kelvin) if coldplate is None else coldplate
    record = None
    t = t0
    while t < t0 + seconds:
        record = judge.step(Sample(t_s=t, epoch_s=1.788e9 + t, sample_k=kelvin,
                                   coldplate_k=tc, u_pct=pct, segment=segment))
        t += dt
    return record


def glide(judge: Judge, *, frm: float, to: float, pct: float,
          seconds: float, t0: float, dt: float = 2.0):
    """Walk the sample from one temperature to another, linearly.

    A sample cannot teleport, and a test that makes it do so is testing the
    heat-capacity term rather than whatever it meant to.  Stepping 118 K to
    114 K in one sample puts `dT/dt` at 2 K/s, which is 1.7 W of dynamic term
    at 118 K -- two orders of magnitude past any fault -- and the verdict that
    comes back is about the arithmetic.
    """
    record = None
    t = t0
    while t < t0 + seconds:
        frac = (t - t0) / seconds
        record = judge.step(Sample(
            t_s=t, epoch_s=1.788e9 + t, sample_k=frm + (to - frm) * frac,
            coldplate_k=M.coldplate_k(frm), u_pct=pct))
        t += dt
    return record


def state(record, name: str) -> str:
    for v in list(record["verdicts"]) + [record["fault_level"]]:
        if v.name == name:
            return v.state
    raise AssertionError(f"no verdict named {name}")


# -- the rolling regression ------------------------------------------------

def test_the_rolling_fit_measures_a_ramp_and_its_scatter():
    """The arithmetic the whole judge stands on, against a known answer."""
    fit = RollingFit(300.0)
    for i in range(400):
        t = i * 2.0
        fit.add(t, 100.0 + 0.05 * t)
    assert fit.slope == pytest.approx(0.05, rel=1e-9)
    assert fit.rms == pytest.approx(0.0, abs=1e-9)


def test_the_rolling_fit_drops_what_leaves_the_window():
    fit = RollingFit(100.0)
    for i in range(200):
        fit.add(i * 2.0, 50.0)
    assert fit.n <= 51
    assert fit.slope == pytest.approx(0.0, abs=1e-9)


def test_the_scatter_is_about_the_line_and_not_about_the_mean():
    """A window taken during a ramp has the ramp in it, and that is not noise.

    The defect this pins is not hypothetical: measured about the MEAN, a
    5 K/min ramp reads as 1.4 K of thermometer noise and every sweep warns
    about its own sweeping.
    """
    fit = RollingFit(300.0)
    for i in range(200):
        fit.add(i * 2.0, 100.0 + 0.0833 * i * 2.0)
    assert fit.rms == pytest.approx(0.0, abs=1e-9)


# -- the residual ----------------------------------------------------------

def test_a_settled_cryostat_on_the_model_is_typical():
    j = Judge()
    r = run(j, kelvin=118.0, seconds=4000.0)
    assert state(r, "missing_power") == TYPICAL
    assert state(r, "fault_level") == TYPICAL


def test_power_going_missing_warns_and_the_baseline_does_not_eat_it():
    """The 2026-09-10 shape: a step down in delivered power, held.

    The baseline freezes at the warning, so the verdict stays -- which is the
    one way this design could fail silently, and the reason the freeze is a
    rule rather than a nicety.
    """
    commanded, settled = settled_with_loss(118.0, 0.0075)
    j = Judge()
    run(j, kelvin=118.0, seconds=40000.0)
    r = run(j, kelvin=settled, pct=commanded, seconds=8000.0, t0=40000.0)
    assert state(r, "missing_power") == WARN
    # Hold it for another twelve hours -- two whole baseline time constants --
    # and it must still be a warning.
    r = run(j, kelvin=settled, pct=commanded, seconds=86400.0, t0=48000.0)
    assert state(r, "missing_power") == WARN


def test_the_baseline_absorbs_a_calibration_offset():
    """What the level is allowed to do, and it is 0.7 % of delivered power.

    A heater that delivers a constant 0.5 % less than it says is not a fault,
    it is a calibration -- and the whole reason the judge alarms on a CHANGE is
    so that one gauge can last a cooldown (Jeff, 2026-09-14).
    """
    j = Judge()
    off = M.percent_for_power(M.power_w(at(118.0)) * 0.995)
    run(j, kelvin=118.0, pct=off, seconds=40000.0)
    r = run(j, kelvin=118.0, pct=off, seconds=4000.0, t0=40000.0)
    assert state(r, "missing_power") == TYPICAL


def test_the_baseline_is_a_fraction_of_power_so_it_survives_a_sweep():
    """Settle cold, sweep warm, and the same calibration must still be absorbed.

    This is the one that chose the variable.  Baselined in watts or in kelvin
    the same 1 % offset is a different number at 40 K and at 140 K -- by a
    factor of twenty-six in kelvin -- so a cooldown that crosses the range
    drags the residual out of band with nothing wrong.  As a fraction of the
    delivered power it is the same number everywhere, which is what a series
    resistance in a voltage-driven heater actually is.
    """
    j = Judge()
    for kelvin in (40.0, 60.0, 90.0, 120.0, 140.0):
        off = M.percent_for_power(M.power_w(at(kelvin)) * 0.99)
        r = run(j, kelvin=kelvin, pct=off, seconds=30000.0,
                t0=30000.0 * (kelvin / 20.0))
        assert state(r, "missing_power") == TYPICAL, f"{kelvin} K"


def settled_with_loss(kelvin: float, fraction: float):
    """``(commanded_pct, settled_k)`` when the circuit delivers ``1 - f`` of it.

    The 2026-09-10 shape, and the distinction it turns on is worth stating
    because the first version of this test got it backwards.  A few tenths of
    an ohm appear in series with the heater.  The **commanded** output does not
    move -- nothing told the recorder anything happened -- but less heat
    arrives, so the sample falls until conduction matches the heat that is
    actually being delivered.

    At the INSTANT of the change the sample has not moved yet, conduction still
    exceeds the delivered power, and the residual is POSITIVE.  Once it settles
    at its new lower temperature, conduction is smaller while ``P(u)`` is
    unchanged, and the residual is NEGATIVE.  The monitor sees the settled
    state -- a 3 K fall at 118 K takes most of an hour -- so negative is what
    "power is missing" looks like to it, and the archive agrees.
    """
    commanded = at(kelvin)
    delivered = M.power_w(commanded) * (1.0 - fraction)
    return commanded, M.steady_temperature_k(delivered)


def test_the_sign_is_negative_once_the_missing_power_has_settled():
    commanded, settled = settled_with_loss(118.0, 0.0075)
    assert settled < 118.0 - 2.0, "0.75 % should be worth about 3 K at 118 K"
    j = Judge()
    run(j, kelvin=118.0, seconds=40000.0)
    r = run(j, kelvin=settled, pct=commanded, seconds=8000.0, t0=40000.0)
    power = next(v for v in r["verdicts"] if v.name == "missing_power")
    assert power.value < 0
    assert power.value == pytest.approx(-5e-3, abs=2e-3), "-4.9 +- 2 mW"


# -- no opinion is not typical ---------------------------------------------

def test_no_opinion_below_the_authority_floor():
    j = Judge()
    r = run(j, kelvin=10.0, pct=5.0, seconds=4000.0)
    assert state(r, "missing_power") == NO_OPINION


def test_no_opinion_just_after_a_heater_move():
    j = Judge()
    run(j, kelvin=118.0, seconds=40000.0)
    r = run(j, kelvin=118.0, pct=at(118.0) + 1.0, seconds=200.0, t0=40000.0)
    assert state(r, "missing_power") == NO_OPINION
    assert state(r, "noise") == NO_OPINION


def test_no_opinion_while_the_coldplate_is_atypical():
    """One cause, one alarm.

    A rising sink moves every absolute number here while the sample follows
    the physics exactly, and a monitor that reports one event twice teaches
    its reader to ignore it once.
    """
    j = Judge()
    run(j, kelvin=118.0, seconds=40000.0)
    warm = M.coldplate_k(118.0) + 1.0
    r = run(j, kelvin=118.0, seconds=8000.0, t0=40000.0, coldplate=warm)
    assert state(r, "coldplate") == WARN
    assert state(r, "missing_power") == NO_OPINION


def test_a_new_recording_drops_the_baseline_but_not_the_calibration():
    """CD10 has a 65 h and a 187 h hole in it.

    A first-order filter carried across one of those arrives on the other side
    confident and wrong, which is the same reason `fit_table.py` has a
    `segment` column at all.
    """
    j = Judge()
    run(j, kelvin=118.0, seconds=40000.0)
    assert j.baseline is not None
    j.step(Sample(t_s=1e6, epoch_s=1.789e9, sample_k=118.0,
                  coldplate_k=M.coldplate_k(118.0), u_pct=at(118.0),
                  segment=7))
    assert j.baseline_age_s == 0.0


def test_the_monitor_holds_no_port_and_writes_no_command():
    """Report only, and it is structural rather than configured.

    PID_PLAN.md section 4, settled 2026-09-11.  The recorder owns the port
    exclusively (invariant 2); this module never builds a transport, an
    instrument or a command, so there is no code path from a verdict to a
    heater for a future change to open by accident.
    """
    import ltspm3.monitor.judge as judge_mod
    import ltspm3.monitor.report as report_mod
    import ltspm3.monitor.source as source_mod
    for mod in (judge_mod, report_mod, source_mod):
        text = Path(mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("set_output", "write_command", "Transport",
                          "send_command", "ANALOG"):
            assert forbidden not in text, f"{mod.__name__} mentions {forbidden}"


# -- the replay ------------------------------------------------------------

@pytest.fixture(scope="module")
def archive_run():
    """The whole archive through the judge, once.

    Not skipped when the archive is missing -- it is versioned, and a skipped
    test fails the build in this repository.  If this cannot find the tables,
    the checkout is wrong and that is worth a failure.
    """
    assert ARCHIVE.is_dir(), f"{ARCHIVE} is versioned and must be present"
    judge = Judge(MonitorConfig())
    n = 0
    for s in replay(str(ARCHIVE)):
        judge.step(s)
        n += 1
    assert n > 1_000_000, f"only {n} samples -- the archive looks truncated"
    return judge


def stamp(text: str) -> float:
    return _dt.datetime.fromisoformat(text).timestamp()


def warns(judge, name: str, lo: str, hi: str):
    return [c for c in judge.changes
            if c[2] == name and c[4] == WARN and stamp(lo) <= c[0] <= stamp(hi)]


def test_the_2026_09_10_fault_warns_within_thirty_minutes(archive_run):
    """Plan 2 section 2.3's row that matters most.

    11:33, the heater circuit developed a few tenths of an ohm in series and
    the sample cooled 3 K.  The monitor has to catch it, and it has to catch it
    as a WARNING and not as a fault -- it is the event that sizes the gap
    between the two.
    """
    hits = warns(archive_run, "missing_power",
                 "2026-09-10T11:33:00", "2026-09-10T12:03:00")
    assert hits, "the 2026-09-10 fault did not warn within thirty minutes"
    minutes = (hits[0][0] - stamp("2026-09-10T11:33:00")) / 60.0
    assert 0 <= minutes <= 30, f"warned after {minutes:.0f} min"
    # -4.9 +- 2 mW, from the plan.  The reason line carries the number.
    assert "-5." in hits[0][5] or "-4." in hits[0][5], hits[0][5]


def test_the_09_10_event_is_a_warning_and_never_a_fault(archive_run):
    """PID_PLAN.md §1: "the 09-10 event is a warning".  It holds.

    It holds because the fault is a STEP and not a level (Jeff, 2026-09-14:
    it should trigger fairly quickly or not at all).  The event's residual
    steps 5.2 mW in seven minutes and then sits flat at -5 mW for three hours,
    so it warns at +14 min and never faults -- which is exactly the shape §1
    describes and, under a level test at 10 mW, was not what happened: the
    excursion reaches 14.11 mW eventually and would have faulted at +190 min.

    The window runs to 14:39, one minute before the connector was reseated.
    What happens after that is a different event.
    """
    hits = warns(archive_run, "fault_level",
                 "2026-09-10T11:33:00", "2026-09-10T14:39:00")
    assert not hits, f"the 09-10 event read as fault-level: {hits}"


def test_the_coldplate_is_typical_across_the_09_10_event(archive_run):
    """The sink did not move; the heater did.  Reporting both would be wrong."""
    hits = warns(archive_run, "coldplate",
                 "2026-09-10T11:00:00", "2026-09-10T13:00:00")
    assert not hits, f"the coldplate warned across a heater event: {hits}"


def test_the_recalibration_changes_no_verdict(archive_run):
    """2026-09-04 12:07, the Coldplate sensor curve was corrected.

    A *reading* changed and the cryostat did not, so nothing here may move.
    """
    window = [c for c in archive_run.changes
              if stamp("2026-09-04T12:00:00") <= c[0] <= stamp("2026-09-04T12:20:00")]
    assert not window, f"the recalibration moved a verdict: {window}"


def test_the_settled_holds_are_quiet(archive_run):
    """The three long post-recalibration holds: 11.5 h, 69.9 h and 24.5 h.

    The false-alarm budget is under one warning a week on a settled hold, and
    these are 106 hours of settled hold between them.
    """
    noisy = warns(archive_run, "missing_power",
                  "2026-09-05T17:00:00", "2026-09-08T15:00:00")
    assert not noisy, f"a settled hold warned: {noisy}"


def test_nothing_warns_across_the_43_hour_sweep(archive_run):
    """`trace-sweep-20260902` is what the model was FITTED to.

    If the judge cannot call the fit's own training data typical, the band is
    wrong rather than the cryostat.
    """
    noisy = warns(archive_run, "missing_power",
                  "2026-09-02T00:00:00", "2026-09-03T19:00:00")
    assert not noisy, f"the sweep warned: {noisy}"


def test_no_verdict_is_ever_a_number_that_cannot_be_written(archive_run):
    """Every reported value is finite or explicitly absent.

    ``plant.json`` has no NaN -- JSON cannot hold one -- and a monitor whose
    output a client cannot parse is a monitor nobody reads.
    """
    from ltspm3.monitor.report import payload
    record = archive_run.step(
        Sample(t_s=9e6, epoch_s=1.789e9, sample_k=118.0,
               coldplate_k=M.coldplate_k(118.0), u_pct=at(118.0), segment=99))
    out = payload(record, cfg=MonitorConfig())
    for entry in out["residuals"]:
        for key in ("value", "sigma", "out_of_band_s"):
            assert entry[key] is None or math.isfinite(entry[key])


# -- the live path ---------------------------------------------------------

RECORDER_HEADER = ("Timestamp,Time,Sample,Coldplate,Magnet,ls218.aout1,"
                   "Validity,State,Notes")


def write_log(path: Path, *, rows: int, kelvin: float, pct: float,
              t0: float = 1.7886e9, dt: float = 2.0, start: float = 0.0) -> None:
    """A recorder-format CSV, in the recorder's own column order."""
    lines = [RECORDER_HEADER]
    for i in range(rows):
        when = _dt.datetime.fromtimestamp(t0 + start + i * dt)
        lines.append(
            f"{when.isoformat(timespec='milliseconds')},{start + i * dt:.3f},"
            f"{kelvin:.4f},{M.coldplate_k(kelvin):.4f},6.80,{pct:.4f},ok,idle,")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_tail_reads_the_recorder_s_own_format(tmp_path):
    from ltspm3.monitor.source import RecorderTail
    log = tmp_path / "ltspm3-heater_2026-09-14.csv"
    write_log(log, rows=50, kelvin=118.0, pct=at(118.0))
    tail = RecorderTail(str(tmp_path))
    assert tail.poll() == 50
    s = tail.samples[-1]
    assert s.sample_k == pytest.approx(118.0)
    assert s.u_pct == pytest.approx(at(118.0), abs=1e-3)
    assert tail.errors == 0
    # A second poll with nothing new must add nothing rather than re-read.
    assert tail.poll() == 0


def test_the_tail_keeps_the_clock_monotonic_across_a_rollover(tmp_path):
    """AUDIT-2026-09-10 finding 4, as an assertion.

    The recorder stamps naive local time and `Time` restarts at every file.  An
    hour that happens twice -- which it does, once a year -- must not run the
    monitor's clock backwards, because a regression slope across a fold is
    wrong in a way nothing notices.
    """
    from ltspm3.monitor.source import RecorderTail
    first = tmp_path / "log_2026-11-01a.csv"
    second = tmp_path / "log_2026-11-01b.csv"
    write_log(first, rows=20, kelvin=118.0, pct=64.0, t0=1.7886e9)
    tail = RecorderTail(str(tmp_path))
    tail.poll()
    # The clocks go back: the next file's stamps are an hour EARLIER.
    write_log(second, rows=20, kelvin=118.0, pct=64.0, t0=1.7886e9 - 3600.0)
    tail.poll()
    times = [s.t_s for s in tail.samples]
    assert times == sorted(times), "the monitor's clock folded"
    assert tail._clock.rewinds == 1
    assert tail.samples[-1].segment == 1


def test_a_partial_row_is_held_back_until_its_newline_arrives(tmp_path):
    """The file is being written by another process while this reads it."""
    from ltspm3.monitor.source import RecorderTail
    log = tmp_path / "log_2026-09-14.csv"
    write_log(log, rows=5, kelvin=118.0, pct=64.0)
    tail = RecorderTail(str(tmp_path))
    assert tail.poll() == 5
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("2026-09-14T00:00:20.000,20.000,118.0,6.6")   # no newline
    assert tail.poll() == 0
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("49,6.80,64.0,ok,idle,\n")
    assert tail.poll() == 1


def test_the_live_path_writes_plant_json_and_a_daily_csv(tmp_path):
    from ltspm3.monitor.report import PlantLog, write_json
    from ltspm3.monitor.source import RecorderTail
    import json

    log = tmp_path / "ltspm3-heater_2026-09-14.csv"
    write_log(log, rows=400, kelvin=118.0, pct=at(118.0))
    tail = RecorderTail(str(tmp_path))
    tail.poll()
    judge, plant = Judge(), PlantLog(str(tmp_path))
    record = None
    for s in tail.samples:
        record = judge.step(s)
        plant.write(record)
    plant.close()
    assert write_json(tmp_path / "plant.json", record, cfg=MonitorConfig())

    payload = json.loads((tmp_path / "plant.json").read_text(encoding="utf-8"))
    assert payload["schema"] >= 1
    assert payload["verdict"] in (TYPICAL, NO_OPINION, WARN)
    # Arrays, not objects: MATLAB's jsondecode mangles object KEYS.
    assert isinstance(payload["residuals"], list)
    assert {e["name"] for e in payload["residuals"]} == {
        "missing_power", "coldplate", "cold_head", "tau", "noise",
        "fault_level"}

    # Named for the day the SAMPLES are from, not for today -- a test anchored
    # to the wall clock passes every morning and fails every evening.
    day = _dt.datetime.fromtimestamp(tail.samples[0].epoch_s).date()
    rows = (tmp_path / f"plant_{day.isoformat()}.csv").read_text(
        encoding="utf-8").splitlines()
    assert rows[0].startswith("Timestamp,t_s,segment,Sample")
    assert len(rows) == 401


def test_the_tail_follows_the_recorder_and_not_the_neighbours(tmp_path):
    """MEASURED ON THE CRYOSTAT, 2026-09-14, and it made the monitor useless.

    A recorder's data directory is not full of only its own logs.  This one had
    28 CSVs in it and the last BY NAME was `sweep-20260905-131753.csv` -- the
    sweep tool's grading table, nine days stale, with no `Sample` column.  The
    live monitor followed it in silence.

    The second file here is the worse one, because this process writes it:
    `plant_` sorts after `ltspm3-heater_`, so the monitor moved onto its own
    daily log the instant it wrote a verdict and never read the cryostat again.
    """
    from ltspm3.monitor.source import RecorderTail
    log = tmp_path / "ltspm3-heater_2026-09-14.csv"
    write_log(log, rows=50, kelvin=118.0, pct=at(118.0))
    # Everything else that really shares that directory, all sorting after it.
    (tmp_path / "sweep-20260905-131753.csv").write_text(
        "u_pct,t_start,T_inf,grade\n64.0,0,118.3,tau\n", encoding="utf-8")
    (tmp_path / "plant_2026-09-14.csv").write_text(
        "Timestamp,t_s,segment,Sample\n2026-09-14T00:00:00,0,0,118.3\n",
        encoding="utf-8")
    (tmp_path / "ltspm3_2026-08-28.csv").write_text(
        "Timestamp,Time,Sample\n2026-08-28T00:00:00,0,4.7\n", encoding="utf-8")

    tail = RecorderTail(str(tmp_path), prefix="ltspm3-heater")
    assert tail.poll() == 50
    assert tail.following == str(log)
    assert tail.samples[-1].sample_k == pytest.approx(118.0)

    # And it stays there when the recorder rolls over -- the prefix selects,
    # the date still orders.
    write_log(tmp_path / "ltspm3-heater_2026-09-15.csv", rows=10,
              kelvin=118.0, pct=at(118.0), t0=1.7886e9 + 86400.0)
    assert tail.poll() == 10
    assert tail.following.endswith("ltspm3-heater_2026-09-15.csv")


def test_the_tail_never_follows_its_own_plant_log(tmp_path):
    """The no-prefix fallback, which is the one a caller can get wrong."""
    from ltspm3.monitor.source import RecorderTail
    log = tmp_path / "ltspm3-heater_2026-09-14.csv"
    write_log(log, rows=20, kelvin=118.0, pct=at(118.0))
    (tmp_path / "plant_2026-09-14.csv").write_text(
        "Timestamp,t_s,segment,Sample\n2026-09-14T00:00:00,0,0,118.3\n",
        encoding="utf-8")
    tail = RecorderTail(str(tmp_path))          # no prefix configured
    assert tail.poll() == 20
    assert tail.following == str(log)


def test_a_tail_with_nothing_to_read_says_so_rather_than_looking_idle(tmp_path):
    from ltspm3.monitor.source import RecorderTail
    tail = RecorderTail(str(tmp_path), prefix="ltspm3-heater")
    assert tail.poll() == 0
    assert tail.following is None


# -- the cold head ---------------------------------------------------------

def stage_run(judge, *, seconds: float, t0: float, first: float,
              second: float = 3.95, dt: float = 2.0, kelvin: float = 118.0):
    """Hold, with the cold-head stages at given temperatures."""
    record = None
    t = t0
    while t < t0 + seconds:
        record = judge.step(Sample(
            t_s=t, epoch_s=1.788e9 + t, sample_k=kelvin,
            coldplate_k=M.coldplate_k(kelvin), u_pct=at(kelvin),
            aux={"Sample": kelvin, "1st Stage": first, "2nd Stage": second}))
        t += dt
    return record


def test_a_compressor_going_off_moves_the_cold_head_by_kelvins_and_warns():
    """The failure this check exists for, and it is not subtle."""
    j = Judge()
    stage_run(j, seconds=8000.0, t0=0.0, first=28.6)
    r = stage_run(j, seconds=4000.0, t0=8000.0, first=30.6)
    assert state(r, "cold_head") == WARN


def test_an_ordinary_cold_head_step_is_not_a_warning():
    """A tenth of a kelvin on the 1st Stage happens several times a week.

    Plan 2 section 2.4: at a bar tight enough to catch the 2026-09-09 event the
    archive produces eleven warnings in six days, because the 09-09 step is
    90 mK and the ordinary ones are 70-170.  Jeff, 2026-09-14: typical erroring
    behaviour is so large as to be unmistakable from these kinds of variation.
    """
    j = Judge()
    stage_run(j, seconds=8000.0, t0=0.0, first=28.6)
    r = stage_run(j, seconds=4000.0, t0=8000.0, first=28.6 - 0.09)
    assert state(r, "cold_head") == TYPICAL


def test_the_cold_head_never_faults():
    """A cold head going off is a compressor question.

    PID_PLAN.md section 1: the sample follows the physics throughout, and the
    fault a failing compressor eventually causes is authority exhausted, which
    is the supervisor's to see and not this process's.
    """
    j = Judge()
    stage_run(j, seconds=8000.0, t0=0.0, first=28.6)
    r = stage_run(j, seconds=8000.0, t0=8000.0, first=40.0)
    assert state(r, "cold_head") == WARN
    assert state(r, "fault_level") != WARN


def test_the_warning_threshold_is_a_floor_under_the_band_not_a_replacement():
    """Jeff's 5 mW and 10 mW, 2026-09-14, and both constraints have to hold.

    The band says do not alarm inside the model's own uncertainty; the floor
    says do not alarm about anything smaller than this however confident the
    model is.  At a settled 118 K the floor binds (band 1.4 mW); at 180 K on a
    5 K/min sweep the BAND binds (8.8 mW), and a flat 5 mW there would warn
    about every sweep.
    """
    cfg = MonitorConfig()
    settled = 3 * M.sigma_q_fast_w(118.0, at(118.0))
    sweeping = 3 * M.sigma_q_fast_w(180.0, at(180.0), 5.0 / 60.0)
    assert settled < cfg.warn_mw * 1e-3, "the floor should bind at a hold"
    assert sweeping > cfg.warn_mw * 1e-3, "the band should bind on a sweep"
    assert max(settled, cfg.warn_mw * 1e-3) == pytest.approx(5e-3)


def test_the_baseline_freezes_on_the_band_and_not_on_the_warning():
    """The two came apart the moment a floor was put under the warning.

    A 3 mW excursion at a settled 118 K is inside Jeff's 5 mW floor, so it is
    deliberately NOT reported -- and it is outside the 1.4 mW band, so the
    baseline must not learn it anyway.  Keyed to the warning instead, the
    2026-09-10 event vanished completely: the baseline walked onto it inside a
    few hours and the fault-level flag never fired either.

    Being told to ignore small things must not teach a monitor that a large
    thing is normal.
    """
    j = Judge()
    run(j, kelvin=118.0, seconds=40000.0)
    frozen_at = j.baseline
    quiet = M.percent_for_power(M.power_w(at(118.0)) - 3e-3)
    r = run(j, kelvin=118.0, pct=quiet, seconds=30000.0, t0=40000.0)
    assert state(r, "missing_power") == TYPICAL, "3 mW is under the 5 mW floor"
    assert j.baseline == pytest.approx(frozen_at, rel=1e-6), \
        "the baseline learned an excursion it was outside the band for"


def test_a_residual_sitting_on_its_threshold_still_declares_itself():
    """Hysteresis, and the 2026-09-10 event is what asked for it.

    Its residual crosses 5 mW four minutes after the event and then hovers
    there.  Without a Schmitt trigger every dip back under reset the 600 s
    persistence timer and the warning did not land for **104 minutes**; with
    one it lands at **14**.  The alarm was not slow because the cryostat was
    subtle.
    """
    j = Judge()
    held = at(118.0)
    run(j, kelvin=118.0, seconds=40000.0)
    # Straddle the floor by moving the SAMPLE, not the heater: touching the
    # output is a commanded move and the judge correctly has no opinion for
    # three time constants afterwards.
    over = M.steady_temperature_k(M.power_w(held) - 5.4e-3)
    under = M.steady_temperature_k(M.power_w(held) - 4.6e-3)
    t = 40000.0
    for _ in range(40):            # 40 * 60 s = 40 min of toggling
        run(j, kelvin=over, pct=held, seconds=30.0, t0=t)
        r = run(j, kelvin=under, pct=held, seconds=30.0, t0=t + 30.0)
        t += 60.0
    assert state(r, "missing_power") == WARN


def test_a_fault_is_a_step_and_a_slow_creep_never_faults():
    """Jeff, 2026-09-14: it should trigger fairly quickly or not at all.

    A change in delivered power puts a step in the residual immediately and by
    construction -- dQ is identically `-(1 - a) P(u)` from the instant the
    delivered fraction `a` moves, whatever the sample then does.  So a residual
    that takes hours to reach a fault level did not step, and whatever it is,
    faulting on it is the worst available outcome: a ramp-down, hours late, for
    something that was never sudden.

    Slow degradation has its own fault and it is a different one -- authority
    exhausted, which is plan 3's and is not gated by any window here.
    """
    j = Judge()
    held = at(118.0)
    run(j, kelvin=118.0, seconds=40000.0)
    # Walk the residual out to 20 mW over eight hours: four times the fault
    # level, and never more than a milliwatt inside any half-hour window.
    t = 40000.0
    for i in range(1, 33):
        kelvin = M.steady_temperature_k(M.power_w(held) - i * 0.6e-3)
        r = run(j, kelvin=kelvin, pct=held, seconds=900.0, t0=t)
        t += 900.0
    power = next(v for v in r["verdicts"] if v.name == "missing_power")
    assert abs(power.value) > 15e-3, "the creep should have reached 15 mW+"
    assert state(r, "missing_power") == WARN, "and it is very much a warning"
    assert state(r, "fault_level") != WARN, "but it never stepped"


def test_a_real_step_faults_even_while_a_warning_is_already_up():
    """The 2026-09-10 reseat landed three hours into an existing warning.

    A fault test anchored to the moment the band was last crossed would have
    been blind for the whole of those three hours -- which is exactly when the
    connector was handled.  Measuring the RANGE inside a trailing window is
    what keeps a second event visible on top of a first.
    """
    j = Judge()
    held = at(118.0)
    run(j, kelvin=118.0, seconds=40000.0)
    warm = M.steady_temperature_k(M.power_w(held) - 6e-3)
    glide(j, frm=118.0, to=warm, pct=held, seconds=1800.0, t0=40000.0)
    r = run(j, kelvin=warm, pct=held, seconds=20000.0, t0=41800.0)
    assert state(r, "missing_power") == WARN
    assert state(r, "fault_level") != WARN, "6 mW over half an hour is not 10"
    # Now a genuine step, on top of the warning, at the rate the 2026-09-10
    # event actually moved: its residual reached full size in seven minutes.
    stepped = M.steady_temperature_k(M.power_w(held) - 20e-3)
    glide(j, frm=warm, to=stepped, pct=held, seconds=420.0, t0=61800.0)
    r = run(j, kelvin=stepped, pct=held, seconds=1200.0, t0=62220.0)
    assert state(r, "fault_level") == WARN


def test_a_fault_stays_reported_after_its_window_scrolls_past():
    """A step that happened does not stop having happened.

    The window is half an hour and a person reads `plant.json` when they get
    in.  Cleared by a new recording, which is the same thing that clears the
    baseline.
    """
    j = Judge()
    held = at(118.0)
    run(j, kelvin=118.0, seconds=40000.0)
    stepped = M.steady_temperature_k(M.power_w(held) - 20e-3)
    glide(j, frm=118.0, to=stepped, pct=held, seconds=420.0, t0=40000.0)
    r = run(j, kelvin=stepped, pct=held, seconds=1200.0, t0=40420.0)
    assert state(r, "fault_level") == WARN
    r = run(j, kelvin=stepped, pct=held, seconds=14400.0, t0=41620.0)
    assert state(r, "fault_level") == WARN, "four hours later, still reported"


def test_a_commanded_heater_move_is_not_a_residual_step():
    """A ladder rung is a command, not a fault.

    The step history breaks at every no-opinion sample rather than merely not
    growing, because a range taken across a commanded move measures the
    command.  On the 2026-09-05 ladder that read as a 21 mW step and flagged
    fault-level nine times.
    """
    j = Judge()
    run(j, kelvin=60.0, seconds=40000.0)
    r = run(j, kelvin=140.0, seconds=40000.0, t0=40000.0)
    assert state(r, "fault_level") != WARN
