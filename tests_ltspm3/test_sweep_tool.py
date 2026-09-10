"""The characterisation sweep: the ladder, the stop rule, and the abort paths.

Everything here runs against a plant on a virtual clock, so the suite pays
microseconds for dwells that on the cryostat are half an hour.  The one thing
that cannot be checked here is the timing -- see ``SimLink``'s docstring -- so
what these pin is the *procedure*: that a settled rung is recognised, that an
unsettled one is recorded as unsettled rather than quietly kept, and that every
fault leaves the heater exactly where it was.
"""

import csv
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ltspm3.tools import sweep as S


# -- a plant, and the ways it can misbehave --------------------------------

class FakePlant:
    """First order and exactly integrable, so the fit has a right answer.

    ``steady(u) = t_bath + gain * u`` and ``T`` relaxes toward it with one time
    constant.  Linear on purpose: this is testing the tool, not the cryostat,
    and a plant whose analytic answer is known is what lets a test assert on
    tau rather than on "something plausible".
    """

    def __init__(self, *, gain=2.0, tau=100.0, dt=10.0, t_bath=4.0,
                 start_pct=6.0, unusable_after=None, stuck_output=False,
                 runaway=False):
        self.gain = gain
        self.tau = tau
        self.dt = dt
        self.t_bath = t_bath
        self.u = start_pct
        self.t = 0.0
        self.kelvin = self.steady(start_pct)
        self.unusable_after = unusable_after
        self.stuck_output = stuck_output
        self.runaway = runaway
        self.writes = []
        self.panics = 0
        self.n = 0

    def steady(self, u):
        return self.t_bath + self.gain * u

    def describe(self):
        return "a fake plant"

    def preflight(self, treads):
        return {}, self.u

    def sample(self, timeout_s=60.0):
        self.t += self.dt
        self.n += 1
        target = self.steady(self.u)
        self.kelvin += (target - self.kelvin) * (1.0 - math.exp(-self.dt / self.tau))
        if self.runaway:
            self.kelvin += 40.0
        usable = not (self.unusable_after is not None
                      and self.n > self.unusable_after)
        return S.Sample(t_s=self.t, kelvin=self.kelvin if usable else None,
                        usable=usable, u_pct=self.u, coldplate_k=4.5,
                        iso=f"T+{self.t:.0f}s")

    def set_output(self, percent):
        self.writes.append((self.t, percent))
        if not self.stuck_output:
            self.u = percent
        return f"-> {percent:.3f}%"

    def set_output_panic(self):
        self.panics += 1
        self.u = 0.0
        return "off"


def opts(**kw):
    base = dict(min_dwell_s=30.0, max_dwell_s=3000.0, fit_every=1)
    base.update(kw)
    return S.Options(**base)


# -- the fit ---------------------------------------------------------------

def test_the_pole_fit_recovers_a_known_relaxation():
    tau, t_inf, amp = 240.0, 80.0, -12.0
    samples = [(t, t_inf + amp * math.exp(-t / tau)) for t in range(0, 1800, 2)]
    f = S.fit_pole(samples)
    assert f.tau_s == pytest.approx(tau, rel=0.02)
    assert f.t_inf == pytest.approx(t_inf, rel=1e-3)
    assert f.reach == pytest.approx(1798 / tau, rel=0.03)


def test_a_dwell_cut_off_mid_relaxation_does_not_grade():
    """Half a time constant in, the fit is right and the point is still no good."""
    tau, t_inf, amp = 600.0, 110.0, -11.0
    samples = [(t, t_inf + amp * math.exp(-t / tau)) for t in range(0, 300, 2)]
    f = S.fit_pole(samples)
    assert f.settle_k > S.MAX_SETTLE_K
    assert f.grade() == ""


def test_a_settled_dwell_grades_tau():
    tau, t_inf, amp = 100.0, 50.0, -8.0
    samples = [(t, t_inf + amp * math.exp(-t / tau)) for t in range(0, 900, 2)]
    f = S.fit_pole(samples)
    assert f.grade() == "tau"
    assert f.end_rate_k_per_h < S.MAX_END_RATE_K_PER_H


def test_a_flat_dwell_is_steady_but_carries_no_believable_tau():
    """Nothing moved, so T_inf is a measurement and tau is not."""
    samples = [(t, 42.0 + (1 if t % 4 else -1) * 0.0005) for t in range(0, 600, 2)]
    f = S.fit_pole(samples)
    assert f.amp_sigma < S.MIN_AMPLITUDE_SIGMA
    assert f.grade() == "steady"


def test_the_fit_strides_long_dwells_and_keeps_the_last_sample():
    samples = [(float(t), 100.0 - 5.0 * math.exp(-t / 300.0))
               for t in range(0, 4000, 2)]
    f = S.fit_pole(samples, max_points=200)
    assert f.n <= 202
    assert f.t_end == pytest.approx(samples[-1][1])


# -- the plan --------------------------------------------------------------

def test_a_plan_needs_only_a_u_column(tmp_path):
    path = tmp_path / "plan.csv"
    path.write_text("u_pct\n10\n20\n30\n", encoding="utf-8")
    treads = S.read_plan(str(path))
    assert [t.u_pct for t in treads] == [10, 20, 30]
    assert treads[0].t_pred_k is None


def test_a_plan_carries_the_models_predictions_when_it_has_them(tmp_path):
    path = tmp_path / "plan.csv"
    path.write_text("u_pct,T_pred_k,tau_pred_s,dwell_pred_s\n"
                    "56.064,38.66,31,210\n", encoding="utf-8")
    t = S.read_plan(str(path))[0]
    assert (t.t_pred_k, t.tau_pred_s, t.dwell_pred_s) == (38.66, 31.0, 210.0)


def test_the_dwell_cap_comes_from_the_plan_and_is_clamped():
    o = opts(min_dwell_s=120.0, max_dwell_s=2400.0)
    assert S.dwell_cap(S.Tread(60.0, dwell_pred_s=900.0), o) == 900.0
    assert S.dwell_cap(S.Tread(60.0, dwell_pred_s=99999.0), o) == 2400.0
    assert S.dwell_cap(S.Tread(60.0, dwell_pred_s=1.0), o) == 120.0
    assert S.dwell_cap(S.Tread(60.0), o) == 2400.0


def test_a_dropped_rung_says_which_test_it_failed():
    """"Cut at the cap" does not say what it ran out of, and the two answers
    want opposite responses -- a smaller step, or one more time constant."""
    tau, t_inf, amp = 600.0, 110.0, -30.0
    early = [(t, t_inf + amp * math.exp(-t / tau)) for t in range(0, 300, 2)]
    assert "K still to go" in S.fit_pole(early).shortfall()

    # Settled to well inside the 2 K bar, and still failing on the end rate --
    # which is the expensive one: it needs about four and a half time
    # constants where the settle test needs two and a half.
    late = [(t, t_inf + amp * math.exp(-t / tau)) for t in range(0, 2400, 2)]
    f = S.fit_pole(late)
    assert abs(f.settle_k) < S.MAX_SETTLE_K
    assert "still moving" in f.shortfall()
    assert S.fit_pole([(t, t_inf + amp * math.exp(-t / tau))
                       for t in range(0, 4000, 2)]).shortfall() == ""


def test_the_rehearsal_drops_the_models_timing_and_keeps_its_temperatures():
    """The simulator is a different plant, so the plan's caps are not its caps.

    Its tau is flat at 620 s; the fit's is under a second below 30 K. Handing
    the simulator a 120 s cap for every cold rung cuts all of them one fifth of
    the way through a relaxation, and a page of dropped rungs then says nothing
    about the cryostat at all.
    """
    treads = [S.Tread(56.064, t_pred_k=38.66, tau_pred_s=31.0, dwell_pred_s=210.0)]
    out = S.without_model_dwells(treads)
    assert out[0].t_pred_k == 38.66
    assert out[0].tau_pred_s is None and out[0].dwell_pred_s is None
    assert S.dwell_cap(out[0], opts(max_dwell_s=3720.0)) == 3720.0


@pytest.mark.parametrize("order,current,first", [
    ("up", None, 10.0),
    ("down", None, 60.0),
    ("nearest", 58.0, 60.0),
    ("nearest", 12.0, 10.0),
    ("nearest", None, 10.0),
])
def test_the_ladder_starts_at_the_end_it_was_asked_to(order, current, first):
    treads = [S.Tread(x) for x in (30.0, 10.0, 60.0)]
    assert S.order_plan(treads, order, current)[0].u_pct == first


# -- one dwell -------------------------------------------------------------

def test_a_dwell_commands_the_rung_and_holds_it_until_it_grades():
    plant = FakePlant(tau=100.0, dt=10.0)
    d = S.dwell(plant, S.Tread(20.0), opts())
    assert plant.writes == [(0.0, 20.0)]
    assert d.grade
    assert d.fit.t_inf == pytest.approx(plant.steady(20.0), abs=0.05)
    # It stopped when it settled, not at the cap.
    assert d.span_s < 3000.0
    assert d.note == ""


def test_a_dwell_that_will_not_settle_is_cut_and_says_so():
    """The cap is reached, the point is still recorded, and its grade is empty.

    Recording it is the whole point: a rung that could not settle in the time
    allowed is exactly the rung somebody needs to go back and re-run, and a
    tool that silently dropped it would hide that.
    """
    plant = FakePlant(tau=4000.0, dt=10.0)
    d = S.dwell(plant, S.Tread(60.0), opts(max_dwell_s=600.0))
    assert "cut at" in d.note
    assert d.grade == ""
    assert d.fit is not None and d.fit.settle_k > S.MAX_SETTLE_K


def test_an_over_temperature_stops_the_dwell_and_writes_nothing_further():
    plant = FakePlant(runaway=True, dt=10.0)
    with pytest.raises(S.SweepAbort, match="over the"):
        S.dwell(plant, S.Tread(20.0), opts(max_k=100.0))
    assert plant.writes == [(0.0, 20.0)]   # the rung, and nothing after it


def test_a_heater_that_does_not_follow_the_command_stops_the_sweep():
    """Somebody else is driving.  There is no safe way to continue."""
    plant = FakePlant(stuck_output=True, dt=10.0)
    with pytest.raises(S.SweepAbort, match="something else is driving"):
        S.dwell(plant, S.Tread(20.0), opts())


def test_one_bad_reading_is_a_glitch_and_six_are_a_fault():
    plant = FakePlant(unusable_after=3, dt=10.0)
    with pytest.raises(S.SweepAbort, match="unusable readings"):
        S.dwell(plant, S.Tread(20.0), opts(max_bad_samples=5))


def test_the_coldplate_has_its_own_ceiling():
    plant = FakePlant(dt=10.0)
    with pytest.raises(S.SweepAbort, match="coldplate"):
        S.dwell(plant, S.Tread(20.0), opts(max_coldplate_k=4.0))


# -- the whole ladder ------------------------------------------------------

def test_the_ladder_runs_in_order_and_journals_every_rung(tmp_path):
    plant = FakePlant(tau=60.0, dt=10.0)
    path = tmp_path / "journal.csv"
    treads = [S.Tread(u) for u in (10.0, 20.0, 30.0)]
    with S.Journal(str(path)) as journal:
        done = S.run(plant, treads, opts(), journal, echo=lambda *_: None)
    assert [u for _, u in plant.writes] == [10.0, 20.0, 30.0]
    assert [d.tread.u_pct for d in done] == [10.0, 20.0, 30.0]

    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    assert len(rows) == 3
    assert list(rows[0]) == list(S.JOURNAL_COLUMNS)
    assert all(r["grade"] for r in rows)
    assert float(rows[-1]["T_inf"]) == pytest.approx(plant.steady(30.0), abs=0.05)


def test_the_journal_is_on_disk_before_the_run_that_dies_halfway_ends(tmp_path):
    """A journal that only exists at the end does not exist on the day it matters."""
    plant = FakePlant(tau=60.0, dt=10.0, runaway=False)
    path = tmp_path / "journal.csv"
    treads = [S.Tread(10.0), S.Tread(20.0), S.Tread(30.0)]
    with S.Journal(str(path)) as journal:
        with pytest.raises(S.SweepAbort):
            for tread in treads:
                journal.write(S.dwell(plant, tread, opts()))
                plant.runaway = True          # the cryostat goes wrong after #1
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    assert len(rows) == 1


def test_a_journal_row_survives_a_dwell_with_no_usable_fit(tmp_path):
    d = S.DwellResult(tread=S.Tread(12.0), u_readback_pct=None, started_iso="a",
                      ended_iso="b", span_s=10.0, n=3, t_start_k=5.0, t_end_k=5.1,
                      coldplate_k=None, fit=None, grade="", note="")
    row = S.journal_row(d)
    assert row["T_inf"] == "" and row["tau_s"] == "" and row["grade"] == ""
    assert row["u_pct"] == "12.0000"


# -- the rehearsal ---------------------------------------------------------

def test_the_simulated_rehearsal_runs_the_whole_procedure(tmp_path):
    """No recorder, no port, no wall clock: the ladder end to end in the suite."""
    link = S.SimLink(dt=20.0, start_pct=63.0)
    treads = [S.Tread(63.5), S.Tread(64.0)]
    path = tmp_path / "rehearsal.csv"
    with S.Journal(str(path)) as journal:
        done = S.run(link, treads, opts(min_dwell_s=200.0, max_dwell_s=4000.0,
                                        fit_every=5, max_k=250.0),
                     journal, echo=lambda *_: None)
    assert [u for _, u in link.writes] == [63.5, 64.0]
    assert all(d.fit is not None for d in done)
    # The simulated cryostat warms when the output goes up, which is the one
    # thing a rehearsal can genuinely confirm about the direction of the run.
    assert done[1].fit.t_inf > done[0].fit.t_inf


def test_the_rehearsal_refuses_a_plan_that_would_break_the_ceiling():
    link = S.SimLink(dt=20.0, start_pct=63.0, max_output_pct=70.0)
    with pytest.raises(S.SweepAbort, match="ceiling"):
        link.preflight([S.Tread(71.0)])


# -- the file interface ----------------------------------------------------
#
# The recorder is a running process on the cryostat, so what is exercised here
# is the half of the protocol this tool owns: what it refuses to start against,
# what it reads out of a status file, and what it puts on the spool.

def _cfg(tmp_path, *, interval_s=2.0, instruments=()):
    ipc = SimpleNamespace(
        status_path=lambda: str(tmp_path / "status.json"),
        command_path=lambda: str(tmp_path / "commands"),
        command_ttl_s=30.0,
    )
    return SimpleNamespace(ipc=ipc, acquisition=SimpleNamespace(interval_s=interval_s),
                           instruments=list(instruments))


def _status(tmp_path, **over):
    import time as _t

    payload = {
        "t_wall": _t.time(), "iso": "2026-09-05T12:00:00", "cycle": 7,
        "running": True, "interval_s": 2.0,
        "channels": [
            {"name": "Sample", "kelvin": 42.0, "usable": True},
            {"name": "Coldplate", "kelvin": 4.9, "usable": True},
        ],
        "aux": [{"name": "ls218.aout1", "value": 56.064}],
        "commands": {"accepted": True, "recent": []},
        "recorder": {"path": str(tmp_path / "log.csv")},
    }
    payload.update(over)
    (tmp_path / "status.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _link(tmp_path, **kw):
    return S.RecorderLink(_cfg(tmp_path, **kw), channel="Sample",
                          coldplate="Coldplate", heater_aux=None,
                          source="test/1", ack_timeout_s=0.4)


def test_preflight_refuses_when_no_recorder_is_running(tmp_path):
    with pytest.raises(S.SweepAbort, match="no recorder is running"):
        _link(tmp_path).preflight([S.Tread(20.0)])


def test_preflight_refuses_a_recorder_that_takes_no_commands(tmp_path):
    _status(tmp_path, commands={"accepted": False, "recent": []})
    with pytest.raises(S.SweepAbort, match="accept_commands"):
        _link(tmp_path).preflight([S.Tread(20.0)])


def test_preflight_refuses_to_race_the_software_loop(tmp_path):
    """Two things commanding one analog output is a race with a heater on it."""
    _status(tmp_path, control={"state": "pid", "output_pct": 63.1, "setpoint_k": 99.0})
    with pytest.raises(S.SweepAbort, match="software loop is driving"):
        _link(tmp_path).preflight([S.Tread(20.0)])


def test_a_loop_that_has_let_go_is_not_in_the_way(tmp_path):
    """`output_pct` null is a loop that is not driving -- see status.py."""
    _status(tmp_path, control={"state": "held", "output_pct": None})
    _, current = _link(tmp_path).preflight([S.Tread(20.0)])
    assert current == pytest.approx(56.064)


def test_preflight_refuses_a_plan_that_would_break_the_configured_ceiling(tmp_path):
    _status(tmp_path)
    inst = SimpleNamespace(name="ls218", max_output_pct=65.0)
    with pytest.raises(S.SweepAbort, match="max_output_pct"):
        _link(tmp_path, instruments=[inst]).preflight([S.Tread(69.0)])


def test_preflight_refuses_a_stale_status_file(tmp_path):
    _status(tmp_path, t_wall=1.0)
    with pytest.raises(S.SweepAbort, match="not cycling"):
        _link(tmp_path).preflight([S.Tread(20.0)])


def test_a_quiet_recorder_is_waited_out_rather_than_treated_as_a_fault(tmp_path):
    """A GPIB retry outlasts the staleness bar, and the heater is not moving.

    Three intervals is the right bar for "should I queue a command", which is
    what `lschart send` uses it for.  It is the wrong bar for a four-hour run
    that is only sitting and watching: one 3 s bus timeout on a 2 s cadence
    would end the afternoon over something the recorder recovers from itself.
    """
    _status(tmp_path, t_wall=1.0)                  # ancient
    link = _link(tmp_path)
    link.stale_grace_s = 0.6
    with pytest.raises(S.SweepAbort, match="not sampling"):
        link.sample()
    # ...and the message says how stale it actually was, not just that it was.
    _status(tmp_path)
    assert link.sample().kelvin == 42.0


def test_a_recorder_that_says_it_stopped_ends_the_run_immediately(tmp_path):
    """Not a hiccup: it is a process that has decided to stop cycling."""
    _status(tmp_path, running=False)
    with pytest.raises(S.SweepAbort, match="has stopped"):
        _link(tmp_path).sample()


def test_a_sample_is_read_out_of_the_status_file(tmp_path):
    _status(tmp_path)
    s = _link(tmp_path).sample()
    assert (s.kelvin, s.usable, s.coldplate_k, s.u_pct) == (42.0, True, 4.9, 56.064)


def test_a_missing_sample_channel_is_named_not_guessed(tmp_path):
    _status(tmp_path)
    link = S.RecorderLink(_cfg(tmp_path), channel="Widget", coldplate=None,
                          heater_aux=None, source="test/1")
    with pytest.raises(S.SweepAbort, match="Widget"):
        link.sample()


def test_one_cycle_is_never_read_twice(tmp_path):
    """A fast poll that re-entered one acquisition would halve the apparent tau."""
    _status(tmp_path)
    link = _link(tmp_path)
    link.sample()
    with pytest.raises(S.SweepAbort, match="no new acquisition cycle"):
        link.sample(timeout_s=0.3)
    _status(tmp_path, cycle=8, channels=[{"name": "Sample", "kelvin": 43.0,
                                          "usable": True}])
    assert link.sample().kelvin == 43.0


def test_setting_the_output_puts_an_analog_command_on_the_spool(tmp_path):
    _status(tmp_path)
    link = _link(tmp_path)
    with pytest.raises(S.SweepAbort, match="no acknowledgement"):
        link.set_output(56.064)                 # nothing is there to answer it
    queued = [json.loads(p.read_text(encoding="utf-8"))
              for p in (tmp_path / "commands").glob("*.json")]
    assert [(c["kind"], c["percent"]) for c in queued] == [("analog", 56.064)]
    assert queued[0]["source"] == "test/1"


def test_a_refused_command_stops_the_sweep_with_the_recorders_own_words(tmp_path):
    _status(tmp_path, commands={"accepted": True, "recent": [
        {"id": "abc", "ok": False, "message": "ipc.allow_analog_output is false"}]})
    with pytest.raises(S.SweepAbort, match="allow_analog_output"):
        _link(tmp_path)._await_ack("abc", "the 20% command")


def test_an_accepted_command_returns_the_acknowledgement(tmp_path):
    _status(tmp_path, commands={"accepted": True, "recent": [
        {"id": "abc", "ok": True, "message": "ls218 analog output 1 -> 20.000%"}]})
    assert "20.000%" in _link(tmp_path)._await_ack("abc", "the 20% command")


def test_the_journal_defaults_to_beside_the_recorders_own_log(tmp_path):
    """The full-rate data is the recorder's CSV; the journal belongs next to it."""
    status = _status(tmp_path)
    path = S.default_journal_path(status)
    assert path.startswith(str(tmp_path)) and path.endswith(".csv")
    assert "sweep-" in path


# -- the admission thresholds, and the clock they are measured on -----------
#
# Both of these pin findings 1 and 3 of AUDIT-2026-09-09.md.

def test_the_mirrored_grader_constants_still_match_analysis_steps():
    """The duplication is deliberate; silent divergence is what it costs.

    ``analysis/steps.py`` is read as TEXT rather than imported.  Importing it
    would need scipy, which the recorder does not depend on and CI does not
    install for this suite -- and a skipped test fails the build here -- and it
    would also make ``analysis/`` a module something imports, which is the
    property that keeps it clear of invariant 1.
    """
    src = (Path(__file__).resolve().parents[1] / "analysis" / "steps.py").read_text(
        encoding="utf-8")
    for name in ("NOISE_FLOOR_K", "NOISE_QUADRATIC", "MIN_REACH",
                 "MIN_AMPLITUDE_SIGMA", "MAX_SETTLE_K", "MAX_END_RATE_K_PER_H",
                 "SETTLED_REMAINDER_K", "MIN_SPAN_S", "MIN_N"):
        found = re.search(rf"^{name}\s*=\s*([0-9.e-]+)\s*$", src, re.M)
        assert found, f"analysis/steps.py no longer defines {name} at module level"
        assert float(found.group(1)) == float(getattr(S, name)), (
            f"{name} has diverged: sweep.py says {getattr(S, name)}, "
            f"analysis/steps.py says {found.group(1)}")


def test_a_finished_relaxation_is_graded_however_fast_it_was_still_moving():
    """The 2026-09-05 rejection, as a test, with the measurement that settled it.

    The 114.28 K rung read 0.71 K/h at the last sample -- over the bar -- after
    running 4.7 time constants with 0.101 K left to go.  The cryostat then held
    that same output for 69.9 h and came to rest at 114.396 K, so the
    extrapolation the rate test threw away was right to 0.11 K.

    Built from the numbers rather than from the log: a pole with tau = 512.8 s
    sampled over 2396 s ending 0.101 K short of 114.28 K reproduces that dwell's
    reach, remainder and end rate.
    """
    tau, span, remainder, T_inf = 512.8, 2396.0, 0.101, 114.28
    amp = -remainder / math.exp(-span / tau)
    samples = [(t, T_inf + amp * math.exp(-t / tau))
               for t in range(0, int(span) + 1, 2)]
    fit = S.fit_pole(samples)

    assert fit.reach > S.MIN_REACH
    assert fit.remainder_k < S.SETTLED_REMAINDER_K
    assert fit.end_rate_k_per_h > S.MAX_END_RATE_K_PER_H   # the old bar rejects it
    assert fit.grade() == "tau"
    assert fit.shortfall() == ""


def test_a_relaxation_cut_off_early_is_still_refused():
    """The other half: reach alone must not be enough.

    Same tau, stopped at 1.5 time constants, so it is still 1.2 K from its own
    T_inf.  Nothing about the new remainder test may let that through -- it is
    exactly the "cut off mid-flight" case the rate bar exists for.
    """
    tau, T_inf, amp = 512.8, 114.28, -5.4
    span = 1.5 * tau
    samples = [(t, T_inf + amp * math.exp(-t / tau))
               for t in range(0, int(span) + 1, 2)]
    fit = S.fit_pole(samples)

    assert fit.remainder_k > S.SETTLED_REMAINDER_K
    assert fit.end_rate_k_per_h > S.MAX_END_RATE_K_PER_H
    assert fit.grade() == ""
    assert "still moving" in fit.shortfall()


def test_a_dwell_shorter_than_the_admission_floor_is_refused_not_clamped(capsys):
    """The 2026-09-05 loss, as a test: it ran, it reported success, it kept nothing."""
    rc = S.main(["--percents", "45.31,47.69,49.82", "--min-dwell", "45",
                 "--simulate"])
    out, err = capsys.readouterr()
    assert rc == 2
    assert "60" in err and "--min-dwell" in err
    # The point is that nothing RAN.  A clamp would have rehearsed the ladder
    # and printed the rung table; the refusal is on stderr and stdout is bare.
    assert "dwells graded" not in out


def test_the_floor_itself_is_accepted():
    assert S.main(["--percents", "45.31", "--min-dwell", str(S.MIN_SPAN_S),
                   "--plan-only"]) == 0


def test_a_recorder_link_times_its_dwells_on_its_own_monotonic_clock(tmp_path):
    """An NTP step in the recorder's `t_wall` must not enter a tau fit.

    `t_wall` is `time.time()`.  A correction on a machine that has been up for
    days lands inside a dwell as a jump in the independent variable, and the
    fit it corrupts is the one being used as the stop rule.
    """
    import time as _t

    # Offsets from now, not absolute: `_status()` rejects a status file that
    # looks stale, so a fabricated 1970-ish `t_wall` would be refused before it
    # could reach the fit at all.  The jump is FORWARD, which is the case that
    # is dangerous rather than merely rude -- a backward step makes the file
    # look stale and the tool says so.
    now = _t.time()
    link = _link(tmp_path)
    stamps = []
    for cycle, offset in enumerate([0.0, 2.0, 4.0,
                                    98000.0,       # +27 h, mid-dwell
                                    98002.0]):
        _status(tmp_path, cycle=cycle, t_wall=now + offset)
        stamps.append(link.sample().t_s)

    steps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(s >= 0.0 for s in steps), "a monotonic clock cannot go backwards"
    assert max(steps) < 5.0, (
        f"the 27 h t_wall jump reached the fit as {max(steps):.0f} s")
