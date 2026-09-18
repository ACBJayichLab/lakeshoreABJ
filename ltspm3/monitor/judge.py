"""The judge: is the cryostat behaving typically?  Report only, never commands.

PID phase 2.  This reads the recorder's files and the model's band and says
what it thinks; it holds no port, sends no command, and cannot raise a heater.
Plan 3's supervisor calls **the same two model functions** for the residual and
its band, so the two cannot disagree about what typical means -- only about
what to do about it.

The residual, and why it is judged as a CHANGE
-----------------------------------------------

``missing_power_w`` answers "do the watts add up", in watts, and it is valid
during a sweep as well as at a hold.  But its LEVEL carries two slow things
that have nothing to do with whether the cryostat is well:

* the calibration -- ``bias_q_w``, up to 4.7 mW at 118 K, which is how much of
  a commanded watt the heater circuit actually delivers;
* the campaign drift -- 0.29 mW/day at 118 K, which the shipped fit does not
  model and therefore does not subtract.

**Jeff, 2026-09-14: recalibrate at most once per cooldown, and typical erroring
behaviour is so large as to be unmistakable from these variations.**  Both
halves of that are load-bearing here.  A cooldown runs for months, so a band
that grows with days-since-gauge reaches 52 mW -- 31 K -- by the end of one and
judges nothing.  And it does not need to: the 2026-09-10 fault moved the sample
**3 K**, and nothing here is trying to resolve a milliwatt.

So the judge keeps a slow **baseline** of the residual and alarms on the
departure from it.  The baseline absorbs the calibration and the drift
together, and what is left is judged against :func:`sigma_q_fast_w`, which does
not grow with time at all.  One gauge per cooldown is then enough, which is
what was asked for.

Two rules make that safe rather than merely convenient:

* **the baseline freezes whenever the verdict is not typical**, or it learns
  the fault it is judging;
* **it is slow against a fault and fast against the drift** -- six hours by
  default, against a fault that declares itself in thirty minutes and a drift
  that takes days to matter.

The absolute residual is reported beside it, every cycle, under its own band.
It is how a person asks "where has the level got to since the gauge" -- which
is the question that decides when the next recalibration is due.

No opinion is not typical
-------------------------

A green light outside the table is a lie, so the verdict has a third value and
the conditions for it are listed in :meth:`Judge.opinion`.  The one that is
easy to leave out is the last: **while ``dT_c`` is atypical, ``dQ`` has no
opinion** -- one cause, one alarm.  A rising coldplate makes the sample follow
the physics perfectly while every absolute number moves, and a monitor that
reports it twice teaches its reader to ignore it once.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from ..model import fitted_response as M

#: The three things a residual can be.  Ordered by how much attention they ask
#: for, so ``max`` over a cycle's verdicts is the cycle's verdict.
TYPICAL, NO_OPINION, WARN = "typical", "no opinion", "warn"
_RANK = {TYPICAL: 0, NO_OPINION: 1, WARN: 2}


@dataclass
class MonitorConfig:
    """Everything the judge is allowed to know.  No limit is hardcoded below.

    ``warn_after_s`` came out of the replay -- 600 s is what puts the
    2026-09-10 event at eleven minutes rather than at two.  ``warn_mw`` and
    ``fault_mw`` are Jeff's, 2026-09-14, with the replay's answers in front of
    him: **5 mW and 10 mW**.
    """

    enabled: bool = True

    #: How far out of band before it is worth saying, and for how long.
    warn_sigma: float = 3.0
    warn_after_s: float = 600.0

    #: TWO THRESHOLDS, AND THE ABSOLUTE ONES ARE FLOORS UNDER THE BAND.
    #: Jeff, 2026-09-14: 5 mW to warn and 10 mW to fault.  At 118 K those are
    #: 3.0 K and 6.0 K at the local gain, which is where his original "warn at
    #: a kelvin, fault at five" lands once the band underneath it is measured
    #: rather than guessed.
    #:
    #: They are a floor and not a replacement, because the two constraints say
    #: different things and both have to hold:
    #:
    #:   the BAND says   do not alarm inside the model's own uncertainty.  3
    #:                   sigma is 1.4 mW at a settled 118 K but **8.8 mW at
    #:                   180 K during a 5 K/min sweep**, where the heat
    #:                   capacity term dominates -- a flat 5 mW there would
    #:                   warn about every sweep.
    #:   the FLOOR says  do not alarm about anything smaller than this however
    #:                   confident the model is.  Typical erroring behaviour is
    #:                   unmistakable (Jeff, same day); a 2 mW excursion at a
    #:                   settled hold is not what this exists to catch.
    #:
    #: So the threshold is ``max(warn_sigma * sigma_q_fast, warn_mw)``, and the
    #: fault the same with ``fault_mw``.  Neither can fire inside the band and
    #: neither can fire below Jeff's number.
    warn_mw: float = 5.0
    #: Reported, NEVER acted on -- this process cannot act.  Plan 3's
    #: supervisor is what turns this number into a ramp-down, and REFIT 7.2's
    #: two measured wiring events (-21.9 and -11.6 mW) are both above it: a
    #: connector being reseated will trip this, and that is the intended
    #: behaviour rather than a false alarm.
    fault_mw: float = 10.0
    fault_after_s: float = 180.0
    #: A FAULT IS A STEP INSIDE THIS WINDOW, not a level reached eventually.
    #: Jeff, 2026-09-14: it should trigger fairly quickly or not at all.  The
    #: 2026-09-10 residual reached its full size in seven minutes, which is
    #: what a change in delivered power does; thirty minutes is generous
    #: against that and is the same bound the replay row uses for the warning.
    fault_window_s: float = 1800.0

    #: The trailing baseline.  Slow against a fault, fast against the drift.
    baseline_tau_s: float = 21600.0

    #: Schmitt trigger: once a residual is out of band it has to come back to
    #: this fraction of the threshold before the persistence timer restarts.
    #: A residual sitting exactly on its threshold otherwise toggles, and each
    #: toggle resets the timer -- see `_Persist.leaning`.
    hysteresis_frac: float = 0.8

    #: No opinion within this many plant time constants of a heater move.
    settle_taus: float = 3.0
    #: **What counts as a move, in KELVIN.**  It was `move_pct: 0.005` -- half
    #: a DAC code -- which is right for an archive of typed commands and is
    #: useless the moment a software loop is running: a closed loop with dither
    #: moves the output by a code most cycles, so the gate would be refreshed
    #: every cycle, `in_transient` would never expire, and the judge that
    #: exists to watch the loop would report `no opinion` for the whole of its
    #: life.  Nothing in the archive is closed loop, so the replay cannot see
    #: this.
    #:
    #: In kelvin it means the same thing at both ends and at both cadences: a
    #: move worth a kelvin of sample is 0.076 % of output at 118 K -- seven
    #: codes, far above anything a settled loop commands -- and 2.9 % at 10 K,
    #: where a percent is worth almost nothing.  A ladder rung is worth tens.
    move_k: float = 1.0

    #: The trailing window the pole fit may reach back over, and the one the
    #: rate is regressed on.
    window_s: float = 1800.0
    slope_window_s: float = 300.0

    #: The noise is measured on a SHORTER window than the rate, and that is
    #: the whole of what makes it a noise measurement.  The scatter is taken
    #: about a straight LINE, and a cryostat relaxing through a 300 s window
    #: with a 400 s time constant is a curve -- the curvature lands in the
    #: residual and reads as noise.  It did: on the July cooldown this reported
    #: 685 mK rms against an expected 18 and warned for hours about a
    #: thermometer that was fine.  Over 60 s even a 5 K/min ramp is straight to
    #: well under a millikelvin.
    noise_window_s: float = 60.0

    #: Below this output the heater has no authority and the residual is the
    #: difference of two large numbers.  Also where the campaign drift was
    #: never measured.
    min_output_pct: float = 28.0

    #: The coldplate check: measured rms, and the pole the locus is lagged by.
    #: Both come from the model's own table; these are the multipliers.
    tc_sigma: float = 3.0

    #: THE COLD-HEAD STAGES, watched beside the coldplate, and the 2026-09-09
    #: event is the whole reason they are here.  At a fixed output the cold-head
    #: channels stepped at 18:06:04 and the sample then fell 0.37 K over three
    #: hours -- but the COLDPLATE moved only 7.6 mK, which is a quarter of its
    #: own rms and a tenth of its band.  The step is on the **1st Stage, 80 mK**,
    #: where it is large against that channel's own scatter.  A judge watching
    #: the coldplate alone cannot see the one archive event that is not a heater
    #: event, which is what plan 2 section 2.2 meant by "1st/2nd stage beside
    #: it".
    #:
    #: These have no locus in the model -- nothing fitted them -- so the test is
    #: not against a curve but against the channel's OWN recent behaviour: a
    #: slow baseline, and a departure from it measured in units of the scatter
    #: the same channel has been showing.  That is weaker than the coldplate's
    #: check and it is deliberately only a warning.
    stage_channels: tuple = ("1st Stage", "2nd Stage")

    #: IT IS A STEP DETECTOR, and the first version was not, which is how the
    #: replay caught it.  Tested on magnitude against its own 300 s scatter,
    #: the 1st Stage warned TWENTY TIMES over the archive's last three days:
    #: the channel's short-term scatter is about 4 mK while it wanders 80 to
    #: 160 mK from hour to hour, so any bar tight enough to see the 2026-09-09
    #: step is inside the ordinary wander.  Allan's lesson on a different
    #: channel (`analysis/allan.py`): quiet at ten seconds and wandering at an
    #: hour is the same rms and the opposite problem.
    #:
    #: What separates the event from the wander is not its size, it is its
    #: SPEED.  The manifest says the cold-head channels "stepped down at
    #: 18:06:04"; the wander takes hours.  So the baseline is deliberately
    #: fast -- ten minutes -- and what is measured is the departure from it.
    #: A 105 mK step shows up whole; 100 mK/h of wander shows up as 17 mK.
    stage_baseline_tau_s: float = 600.0
    stage_sigma: float = 3.0
    #: A floor under the scatter, so a channel that is quiet for a minute
    #: cannot make the test arbitrarily sensitive.  **SET WHERE THE ARCHIVE IS
    #: QUIET**, which is what the false-alarm budget means and which is not
    #: where the 2026-09-09 event would be caught -- see plans/pid-2-monitor.md
    #: section 2.4.  At 60 mK the whole 57-day archive produces four warnings,
    #: half of one a week against a budget of one, and all four are real: the
    #: initial cooldown's 57 K step and three movements of 0.3-0.5 K.  At the
    #: 20 mK that catches 09-09 it produces eleven in six days, because a
    #: 70-170 mK step on the 1st Stage happens several times a week and the
    #: 09-09 one is 90 mK.
    #:
    #: The failure this exists for is not subtle: a compressor degrading moves
    #: these channels by KELVINS.
    stage_floor_k: float = 0.060

    #: The noise check: how many times the measured `1.36e-6 T^2` counts as
    #: atypical, and the floor under it.
    noise_ratio: float = 2.0
    noise_floor_k: float = 0.0018
    noise_quadratic: float = 1.36e-6

    #: tau is not measurable below this -- under ten seconds against a 2 s
    #: cadence -- so a ratio there is a number about the cadence.
    tau_min_k: float = 25.0
    tau_ratio_band: tuple = (0.85, 1.10)
    #: How often the pole is actually refitted.  A time constant does not
    #: change between two samples two seconds apart.
    tau_every_s: float = 60.0
    #: An excursion wider than this fraction of the temperature it happens at
    #: changes tau across itself, so there is no single pole in it to measure.
    #: `analysis/steps.MAX_AMPLITUDE_FRAC`, adopted by the 2026-09-13 refit.
    max_amplitude_frac: float = 0.15


@dataclass
class Verdict:
    """One residual's answer, and enough to explain it without re-deriving it."""

    name: str
    state: str = NO_OPINION
    value: float = math.nan
    sigma: float = math.nan
    reason: str = ""
    #: How long it has been out of band.  The verdict does not change until
    #: this passes ``warn_after_s``; a monitor that flickers is one nobody
    #: reads.
    out_of_band_s: float = 0.0


class _Persist:
    """A verdict that has to mean it.  Changes only after ``after_s`` out."""

    def __init__(self, after_s: float) -> None:
        self.after_s = float(after_s)
        self.state = NO_OPINION
        self.since: float | None = None
        self.pending: str | None = None

    def update(self, proposed: str, t_s: float) -> str:
        # No opinion is not a claim, so it takes effect at once -- the harm in
        # a monitor is a green light it has not earned, not a silence.
        if proposed == NO_OPINION:
            self.state = NO_OPINION
            self.pending = self.since = None
            return self.state
        if proposed == self.state:
            self.pending = self.since = None
            return self.state
        # Both directions wait.  Coming back to typical is as much of a claim
        # as leaving it, and a verdict that flickers is one nobody reads.
        if proposed != self.pending:
            self.pending, self.since = proposed, t_s
        if t_s - self.since >= self.after_s:
            self.state = proposed
            self.pending = self.since = None
        return self.state

    def held_s(self, t_s: float) -> float:
        return 0.0 if self.since is None else t_s - self.since

    @property
    def leaning(self) -> bool:
        """Already warned, or on the way to warning.

        What a caller needs in order to apply hysteresis, and it is not
        optional.  Without it a residual sitting ON its threshold toggles, and
        every toggle resets the persistence timer: the 2026-09-10 event crosses
        5 mW **four minutes** after it happened and then hovers there, and a
        strict continuous-600-s rule did not declare it until **104 minutes**
        had passed.  The alarm was not slow because the cryostat was subtle, it
        was slow because the arithmetic could not make up its mind.
        """
        return self.state == WARN or self.pending == WARN


class RollingFit:
    """``dT/dt`` and the scatter about it, over a sliding window, in O(1).

    Both numbers every cycle, from six running sums, because the obvious
    version is not affordable here.  A 300 s window at a 2 s cadence is 150
    samples and the archive replay is a million of them; re-summing the window
    each cycle is 150 million operations per pass and the replay took minutes
    instead of seconds.  The live monitor would not have noticed -- one cycle a
    second, forever -- which is exactly why it was worth fixing: **the replay
    is the only place the cost of a per-cycle scan is visible at all**, and
    plan 2 wants the replay run often enough that somebody actually runs it.

    A **regression** and not a difference of the two ends: at 118 K the
    thermometer carries about 19 mK of noise and a 5 K/min ramp moves
    0.083 K/s, so an endpoint difference over 300 s is 0.5 % noise and over
    10 s is 15 %.  The band's dynamic term is only meaningful with the first.

    The scatter is about the **line**, not about the mean.  A window taken
    during a ramp has the ramp in it, and calling that noise would have every
    sweep warn about its own sweeping.

    The sums are kept relative to the first sample still in the window, and the
    window is re-summed from scratch whenever it empties -- floating-point
    cancellation over a month of seconds is a real effect and a rolling sum
    that is never rebuilt is how it arrives.
    """

    def __init__(self, span_s: float) -> None:
        self.span_s = float(span_s)
        self._pts: deque = deque()
        self._reset()

    def _reset(self) -> None:
        self.n = 0
        self.sx = self.sy = self.sxx = self.sxy = self.syy = 0.0
        self._t0 = 0.0

    def clear(self) -> None:
        self._pts.clear()
        self._reset()

    def add(self, t_s: float, value: float | None) -> None:
        if value is None:
            return
        if not self._pts:
            self._t0 = t_s
        self._pts.append((t_s, value))
        self._accumulate(t_s, value, +1)
        floor = t_s - self.span_s
        while self._pts and self._pts[0][0] < floor:
            old_t, old_v = self._pts.popleft()
            self._accumulate(old_t, old_v, -1)
        if not self._pts:
            self._reset()

    def _accumulate(self, t_s: float, value: float, sign: int) -> None:
        x = t_s - self._t0
        self.n += sign
        self.sx += sign * x
        self.sy += sign * value
        self.sxx += sign * x * x
        self.sxy += sign * x * value
        self.syy += sign * value * value

    @property
    def slope(self) -> float:
        """K/s.  0.0 rather than NaN: an unknown rate is best treated as none."""
        if self.n < 3:
            return 0.0
        den = self.n * self.sxx - self.sx * self.sx
        return 0.0 if den <= 0 else (self.n * self.sxy - self.sx * self.sy) / den

    @property
    def rms(self) -> float:
        """Rms about the fitted line.  NaN when there is nothing to measure."""
        if self.n < 8:
            return math.nan
        b = self.slope
        # sum (y - ybar - b(x - xbar))^2, expanded so the window is not walked.
        ss_y = self.syy - self.sy * self.sy / self.n
        ss_x = self.sxx - self.sx * self.sx / self.n
        ss_xy = self.sxy - self.sx * self.sy / self.n
        resid = ss_y - 2.0 * b * ss_xy + b * b * ss_x
        return math.sqrt(max(resid, 0.0) / self.n)


class Judge:
    """One cryostat's verdicts, advanced one sample at a time.

    Deliberately not a thread and not a loop: :meth:`step` is pure with respect
    to everything except this object, so the replay and the live monitor drive
    exactly the same code and a test can drive it with a list.
    """

    def __init__(self, cfg: MonitorConfig | None = None) -> None:
        self.cfg = cfg or MonitorConfig()
        self.window: deque = deque()
        #: The sliding regression the slope and the noise both read.  One pass
        #: over each sample, never over the window.
        self.fit = RollingFit(self.cfg.slope_window_s)
        self.noise_fit = RollingFit(self.cfg.noise_window_s)
        self.baseline: float | None = None
        #: When the baseline last moved, and when it was first seeded.  Two
        #: different questions: the first sizes the filter step, the second is
        #: how much of a cooldown it has actually seen.
        self.baseline_t: float | None = None
        self.baseline_seeded_t: float | None = None
        self.baseline_age_s = 0.0
        self._states: dict = {}
        self._tc_pred: float | None = None
        #: One slow baseline and one rolling scatter per cold-head stage.
        self._stage_base: dict = {}
        self._stage_fit: dict = {}
        self._stage = _Persist(self.cfg.warn_after_s)
        self._last_u: float | None = None
        self._move_t: float | None = None
        self._segment: int | None = None
        self._last_t: float | None = None
        #: The pole fit is the one expensive check -- up to 800 points of
        #: 1-D search -- and a time constant does not change between cycles.
        self._tau_at: float | None = None
        self._tau_last: Verdict | None = None
        #: The residual's recent history, for the step test, and whether a
        #: step has ever been seen.  A step that happened does not stop having
        #: happened when its window scrolls past.
        self._fault_hist: deque = deque()
        self._fault_seen = False
        self._fault_note = ""
        self._q = _Persist(self.cfg.warn_after_s)
        self._tc = _Persist(self.cfg.warn_after_s)
        self._noise = _Persist(self.cfg.warn_after_s)
        self._tau = _Persist(self.cfg.warn_after_s)
        self._fault = _Persist(self.cfg.fault_after_s)
        self.changes: list[tuple] = []
        self.cycles = 0

    # -- the cycle ---------------------------------------------------------

    def step(self, s) -> dict:
        """One sample in, one plant record out."""
        cfg = self.cfg
        if self._segment is not None and s.segment != self._segment:
            self._reset_history()
        self._segment = s.segment
        dt = 0.0 if self._last_t is None else max(0.0, s.t_s - self._last_t)
        self._last_t = s.t_s
        self.cycles += 1

        self.window.append(s)
        floor = s.t_s - cfg.window_s
        while self.window and self.window[0].t_s < floor:
            self.window.popleft()
        self.fit.add(s.t_s, s.sample_k)
        self.noise_fit.add(s.t_s, s.sample_k)
        self._track_move(s)

        rate = self.fit.slope
        tc = self._coldplate(s, dt)
        noise = self._noise_verdict(s)
        stages = self._stage_verdict(s, dt)
        tau = self._tau_verdict(s)
        q_abs, q, fault = self._power(s, rate, tc)

        record = {
            "t_s": s.t_s, "epoch_s": s.epoch_s, "segment": s.segment,
            "sample_k": s.sample_k, "coldplate_k": s.coldplate_k,
            "u_pct": s.u_pct, "dT_dt_k_per_s": rate,
            "verdicts": [q, tc, stages, tau, noise],
            "fault_level": fault,
            "missing_power_abs_w": q_abs,
            "baseline_w": self.baseline,
            "baseline_age_s": self.baseline_age_s,
            **self._headline(q, tc, stages, tau, noise, fault),
        }
        self._record_changes(record)
        return record

    @staticmethod
    def _headline(*verdicts) -> dict:
        """The one word for the whole cryostat, and who it speaks for.

        **The worst thing the judge actually knows** (Jeff, 2026-09-17), which
        is not the same as the worst of everything it was asked.  This was
        ``max`` by rank over four of the six residuals and it had two faults,
        in opposite directions:

        *It said nothing.*  ``tau`` answers ``no move to measure`` unless there
        is a step to fit, and at a hold there never is -- so a silence that
        carries no information about the cryostat outranked four residuals that
        did.  Measured on 2026-09-17: the headline read ``no opinion`` on
        **every one of 46,236 samples** while ``missing_power`` read
        ``typical`` on 85 % of them.  Not a wrong answer, which is what made it
        easy to miss; a permanently uninformative one.

        *And it hid a warning.*  ``cold_head`` and the latched ``fault_level``
        were in the published rows and not in this line at all, so a cold-head
        warning reached nobody who read the summary -- the unsafe direction,
        and PID_PLAN.md section 7's "a green light outside the table is a lie".

        So: the worst of the residuals that **have an opinion**, and
        ``verdict_for`` names them.  ``no opinion`` still wins when nothing can
        speak, so a silence is never dressed up as green -- the honesty moved
        into saying what the word covers rather than into refusing to say one.

        A latched ``fault_level`` reads ``warn`` here because ``warn`` is the
        most severe word this monitor has; the row itself is what distinguishes
        a step from a level, and giving it a fourth word would change what
        every residual row, the viewer's palette and MATLAB's reader all say.
        """
        speaking = [v for v in verdicts if v.state != NO_OPINION]
        return {
            "verdict": (max((v.state for v in speaking), key=lambda x: _RANK[x])
                        if speaking else NO_OPINION),
            "verdict_for": [v.name for v in speaking],
        }

    def _reset_history(self) -> None:
        """A new recording.  Keep the calibration, drop everything measured.

        The baseline goes because CD10 has a 65 h and a 187 h hole in it and a
        first-order filter carried across one of those arrives at the other
        side confident and wrong.  ``tau_bath`` goes for the same reason.
        """
        self.window.clear()
        self.fit.clear()
        self.noise_fit.clear()
        self._tau_at = self._tau_last = None
        self.baseline = self.baseline_t = None
        self.baseline_age_s = 0.0
        self._tc_pred = None
        self._stage_base.clear()
        for f in self._stage_fit.values():
            f.clear()
        self._last_u = self._move_t = None
        self._last_t = None
        self.baseline_seeded_t = None
        self._fault_hist.clear()
        self._fault_seen = False
        self._fault_note = ""

    def in_transient(self, s) -> bool:
        """Is the cryostat still relaxing from the last heater move?

        Shared by three of the four checks, and leaving it out of two of them
        was a real defect the replay found rather than a hypothetical one.  On
        the 2026-09-05 ladder the noise check reported **1626 mK rms against an
        expected 9** and the coldplate check reported +192 mK from its locus --
        neither about the thermometer or the sink, both about a cryostat that
        was being driven up a ladder at the time.  A relaxation is a curve; the
        noise is the scatter about a LINE; over a step the difference between
        those two is the step.
        """
        if self._move_t is None or s.sample_k is None:
            return False
        tau = M.tau_s(min(max(s.sample_k, M.T_MIN_K), M.T_MAX_K))
        # FLOORED AT THE JUDGE'S OWN WINDOW, and not at the cryostat's tau
        # alone.  Below 30 K the plant settles in under ten seconds while the
        # slope is still being regressed over five minutes, so the gate expired
        # while every number downstream of it was still half made of the
        # previous regime.  On the 2026-09-05 ladder's cold end that read as a
        # 99 mW step and flagged fault-level -- a commanded ladder, working.
        #
        # Two different settling times, and the longer one is what governs: the
        # cryostat's, and the measurement's.
        settle = max(self.cfg.settle_taus * tau, self.cfg.slope_window_s)
        return s.t_s - self._move_t < settle

    def _track_move(self, s) -> None:
        if s.u_pct is None:
            return
        if self._last_u is not None and s.sample_k is not None:
            gain = M.gain_k_per_pct(
                min(max(s.sample_k, M.T_MIN_K), M.T_MAX_K))
            if abs(s.u_pct - self._last_u) * gain > self.cfg.move_k:
                self._move_t = s.t_s
        self._last_u = s.u_pct

    # -- the residuals -----------------------------------------------------

    def _coldplate(self, s, dt: float) -> Verdict:
        """``dT_c``: the coldplate against where the sample says it should be.

        The locus in the table is where the coldplate settles, and during a
        transient the real one lags it by ``TAU_BATH_S`` -- 175 s, measured.
        So the locus is passed through that pole before it is compared, or
        every heater move would warn about the sink for three minutes.

        **This never faults.**  A rising coldplate is a compressor question and
        the sample follows the physics throughout; PID_PLAN.md section 1 is
        explicit that the fault a failing compressor eventually causes is
        authority exhausted, which is the supervisor's to see and not this.
        """
        v = Verdict("coldplate")
        if s.sample_k is None or s.coldplate_k is None:
            v.reason = "no reading"
            return v
        if not M.T_MIN_K <= s.sample_k <= M.T_MAX_K:
            v.reason = "sample outside the table"
            return v
        locus = M.coldplate_k(s.sample_k)
        if self.in_transient(s):
            # Keep predicting -- the pole has to stay warm across the step or
            # it restarts from the wrong place -- but do not judge.  TAU_BATH_S
            # is the SETTLED lag and a ladder rung outruns it.
            if dt > 0 and self._tc_pred is not None:
                self._tc_pred += (1.0 - math.exp(-dt / M.TAU_BATH_S)) * (
                    locus - self._tc_pred)
            elif self._tc_pred is None:
                self._tc_pred = s.coldplate_k
            v.reason = "relaxing from a heater move"
            v.state = self._tc.update(NO_OPINION, s.t_s)
            return v
        if self._tc_pred is None:
            self._tc_pred = s.coldplate_k
        elif dt > 0:
            alpha = 1.0 - math.exp(-dt / M.TAU_BATH_S)
            self._tc_pred += alpha * (locus - self._tc_pred)
        v.value = s.coldplate_k - self._tc_pred
        v.sigma = M.TC_RMS_K
        bar = self.cfg.tc_sigma * v.sigma
        out = abs(v.value) > bar * (self.cfg.hysteresis_frac
                                    if self._tc.leaning else 1.0)
        v.state = self._tc.update(WARN if out else TYPICAL, s.t_s)
        v.out_of_band_s = self._tc.held_s(s.t_s)
        if v.state == WARN:
            v.reason = (f"coldplate {1e3 * v.value:+.0f} mK from its locus, "
                        f"band {1e3 * self.cfg.tc_sigma * v.sigma:.0f} mK")
        return v

    def _stage_verdict(self, s, dt: float) -> Verdict:
        """The cold head, against its own recent behaviour.

        Not against a model: nothing in the fit describes the 1st and 2nd
        stages, and inventing a curve for them would be inventing a cryostat.
        What is available is that these channels are very quiet when the
        cryostat is well -- so a step that is many times their own trailing
        scatter is worth a sentence, and nothing smaller is.

        **Warns, never faults**, for the same reason the coldplate does not: a
        cold head going off is a compressor question, the sample follows the
        physics throughout, and the fault it eventually causes is authority
        exhausted, which is the supervisor's to see and not this.
        """
        v = Verdict("cold_head")
        if self.in_transient(s):
            # The heater drives these channels too -- the 2nd Stage moves half
            # a kelvin up a ladder -- so a step here during a relaxation is the
            # step somebody commanded.
            v.reason = "relaxing from a heater move"
            v.state = self._stage.update(NO_OPINION, s.t_s)
            return v
        worst = None
        for name in self.cfg.stage_channels:
            value = s.aux.get(name)
            if value is None:
                continue
            fit = self._stage_fit.get(name)
            if fit is None:
                fit = self._stage_fit[name] = RollingFit(self.cfg.noise_window_s)
            fit.add(s.t_s, value)
            base = self._stage_base.get(name)
            if base is None:
                self._stage_base[name] = value
                continue
            scatter = fit.rms
            if math.isnan(scatter):
                continue
            band = self.cfg.stage_sigma * max(scatter, self.cfg.stage_floor_k)
            if self._stage.leaning:
                band *= self.cfg.hysteresis_frac
            excess = abs(value - base) / band if band > 0 else 0.0
            if worst is None or excess > worst[0]:
                worst = (excess, name, value - base, band)
            # The baseline freezes on a departure, exactly as the power one
            # does -- otherwise it walks onto the step within the hour and the
            # event disappears from the record that is meant to hold it.
            if excess <= 1.0 and dt > 0:
                alpha = 1.0 - math.exp(-dt / self.cfg.stage_baseline_tau_s)
                self._stage_base[name] = base + alpha * (value - base)
        if worst is None:
            v.reason = "no cold-head reading"
            v.state = self._stage.update(NO_OPINION, s.t_s)
            return v
        excess, name, delta, band = worst
        v.value, v.sigma = delta, band
        v.state = self._stage.update(WARN if excess > 1.0 else TYPICAL, s.t_s)
        v.out_of_band_s = self._stage.held_s(s.t_s)
        if v.state == WARN:
            v.reason = (f"{name} moved {1e3 * delta:+.0f} mK against its own "
                        f"{1e3 * band:.0f} mK band")
        return v

    def _noise_verdict(self, s) -> Verdict:
        """Trailing rms against the thermometer's own measured noise."""
        v = Verdict("noise")
        if s.sample_k is None:
            v.reason = "no reading"
            return v
        if self.in_transient(s):
            v.reason = "relaxing from a heater move"
            v.state = self._noise.update(NO_OPINION, s.t_s)
            return v
        rms = self.noise_fit.rms
        if math.isnan(rms):
            v.reason = "not enough samples"
            return v
        expected = max(self.cfg.noise_floor_k,
                       self.cfg.noise_quadratic * s.sample_k ** 2)
        v.value, v.sigma = rms, expected
        bar = self.cfg.noise_ratio * expected
        out = rms > bar * (self.cfg.hysteresis_frac
                           if self._noise.leaning else 1.0)
        v.state = self._noise.update(WARN if out else TYPICAL, s.t_s)
        v.out_of_band_s = self._noise.held_s(s.t_s)
        if v.state == WARN:
            v.reason = (f"{1e3 * rms:.1f} mK rms against an expected "
                        f"{1e3 * expected:.1f} mK")
        return v

    def _tau_verdict(self, s) -> Verdict:
        """The plant's own time constant, after a heater move has relaxed.

        No opinion below ``tau_min_k``: tau is under ten seconds there against
        a 2 s cadence, so what a pole fit measures is the cadence.

        **Throttled to ``tau_every_s``**, and not as an optimisation to be
        removed later.  A pole fit is a 1-D search over up to 800 points and a
        time constant does not change between two samples two seconds apart, so
        running it every cycle would spend almost all of this module's time
        recomputing a number that cannot have moved.  Between fits the last
        verdict is repeated, which is also what a person reading it means by it.
        """
        if (self._tau_at is not None and self._tau_last is not None
                and s.t_s - self._tau_at < self.cfg.tau_every_s):
            return self._tau_last
        self._tau_at = s.t_s
        v = Verdict("tau")
        self._tau_last = v
        if s.sample_k is None or self._move_t is None:
            v.reason = "no move to measure"
            return v
        if s.sample_k < self.cfg.tau_min_k:
            v.reason = f"below {self.cfg.tau_min_k:.0f} K, tau is unmeasurable"
            return v
        expected = M.tau_s(s.sample_k)
        span = s.t_s - self._move_t
        if span < self.cfg.settle_taus * expected:
            v.reason = "still relaxing"
            return v
        if span > self.cfg.window_s:
            v.reason = "the last move has scrolled out of the window"
            return v
        pts = [(x.t_s, x.sample_k) for x in self.window
               if x.t_s >= self._move_t and x.sample_k is not None]
        try:
            from ..tools.sweep import fit_pole
            fit = fit_pole(pts)
        except (ValueError, ZeroDivisionError):
            v.reason = "no pole to fit"
            return v

        # THE TWO GRADING RULES FROM THE REFIT, and the replay is what made
        # them necessary here too.  Without them this reported ratios of 62 --
        # a pole fitted to an hour of a settled hold, which has no relaxation
        # left in it and fits the cryostat's DRIFT instead.  Both are "there is
        # no single pole here to measure", which is not a cryostat fault and
        # must not read as one.
        #
        # A tau LONGER THAN THE WINDOW IT WAS FITTED IN is unmeasured by
        # definition, and `fit_pole`'s deliberately huge upper bound exists so
        # that it can say so rather than be clipped into looking finished.
        if fit.tau_s > fit.span_s:
            v.reason = (f"tau {fit.tau_s:.0f} s is longer than the {fit.span_s:.0f} s "
                        "it was fitted in -- not settled")
            return v
        # And an excursion wide enough to change tau across itself has no
        # single pole to find: `analysis/steps.MAX_AMPLITUDE_FRAC`, which the
        # 2026-09-13 refit adopted after two of eleven windows failed on it.
        if abs(fit.amp) > self.cfg.max_amplitude_frac * max(s.sample_k, 1.0):
            v.reason = (f"the step is {abs(fit.amp):.1f} K across {s.sample_k:.0f} K -- "
                        "tau changes within it")
            return v
        v.value = fit.tau_s / expected if expected > 0 else math.nan
        v.sigma = expected
        lo, hi = self.cfg.tau_ratio_band
        out = not (lo <= v.value <= hi)
        v.state = self._tau.update(WARN if out else TYPICAL, s.t_s)
        v.out_of_band_s = self._tau.held_s(s.t_s)
        if v.state == WARN:
            v.reason = (f"tau {fit.tau_s:.0f} s against the model's "
                        f"{expected:.0f} s, ratio {v.value:.2f}")
        return v

    def opinion(self, s, tc: Verdict) -> str:
        """Why the power residual has no opinion, or "" when it has one.

        Every one of these is a case where ``dQ`` is computable and meaningless,
        which is a worse failure than not computing it: a green light outside
        the table is a lie.
        """
        if s.sample_k is None or s.coldplate_k is None or s.u_pct is None:
            return "no reading"
        if not M.T_MIN_K <= s.sample_k <= M.T_MAX_K:
            return "sample outside the table"
        if s.u_pct < self.cfg.min_output_pct:
            return f"output below {self.cfg.min_output_pct:.0f} %"
        if self.in_transient(s):
            return f"within {self.cfg.settle_taus:.0f} tau of a heater move"
        if tc.state == WARN:
            # One cause, one alarm.  A rising sink moves every absolute number
            # here while the sample follows the physics exactly.
            return "the coldplate is atypical"
        return ""

    def _power(self, s, rate: float, tc: Verdict):
        """``dQ`` against the baseline, and the absolute residual beside it."""
        v = Verdict("missing_power")
        fault = Verdict("fault_level")
        why = self.opinion(s, tc)
        if why:
            v.reason = why
            v.state = self._q.update(NO_OPINION, s.t_s)
            # THE STEP HISTORY BREAKS HERE, and that is the point of clearing
            # it rather than merely not appending.  Every no-opinion sample is
            # a moment the residual is not comparable with the one before it --
            # a commanded heater move above all -- so a range taken across the
            # gap measures the COMMAND.  On the 2026-09-05 ladder that read as
            # a 21 mW step and flagged fault-level nine times, which is the
            # programmed ladder working exactly as intended.
            self._fault_hist.clear()
            latched = self._fault.update(NO_OPINION, s.t_s)
            fault.state = WARN if self._fault_seen else latched
            fault.reason = self._fault_note or why
            return math.nan, v, fault

        q_abs = M.missing_power_w(s.sample_k, rate, s.coldplate_k, s.u_pct)
        sigma = M.sigma_q_fast_w(s.sample_k, s.u_pct, rate)
        v.sigma = sigma

        # THE BASELINE IS KEPT AS A FRACTION OF THE DELIVERED POWER, and which
        # variable that is decides whether any of this works.  What the
        # baseline exists to absorb is the heater circuit's delivered fraction
        # -- the calibration, and the campaign drift, which REFIT_PLAN.md
        # section 7.2 measured to be the SAME QUANTITY moving slowly.  A
        # fraction of delivered power is what that is, by definition: a series
        # resistance in a voltage-driven heater takes a fixed share of the
        # watts, whatever temperature the sample happens to be at.
        #
        # Both of the obvious alternatives were tried on the replay and both
        # fail on the July cooldown, which sweeps 300 K to 18 K and back to
        # 94 K inside a day:
        #
        #   in WATTS   the same 3 % calibration error is 12 mW at 18 K and
        #              20 mW at 94 K, so the baseline is chasing the sweep;
        #   in KELVIN  it is 0.1 K at 18 K and 2.6 K at 94 K -- a factor of
        #              TWENTY-SIX, because Lambda' falls by ten across that
        #              range while P(u) doubles.  This one is the trap: the
        #              model's SHAPE error is flat in kelvin (band.py measured
        #              0.135 K), so kelvin looks like the right variable right
        #              up until the thing being absorbed is the LEVEL instead.
        #
        # Shape error and level error are different quantities with different
        # shapes, and the baseline is only ever absorbing the second.
        watts = M.power_w(s.u_pct)
        q_frac = q_abs / watts

        if self.baseline is None:
            self.baseline = q_frac
            self.baseline_t = self.baseline_seeded_t = s.t_s
        self.baseline_age_s = s.t_s - self.baseline_seeded_t

        v.value = (q_frac - self.baseline) * watts     # reported in watts
        threshold = max(self.cfg.warn_sigma * sigma, self.cfg.warn_mw * 1e-3)
        release = threshold * (self.cfg.hysteresis_frac if self._q.leaning
                               else 1.0)
        out = abs(v.value) > release
        v.state = self._q.update(WARN if out else TYPICAL, s.t_s)
        v.out_of_band_s = self._q.held_s(s.t_s)
        if v.state == WARN:
            why = ("the 3 sigma band" if threshold > self.cfg.warn_mw * 1e-3
                   else f"the {self.cfg.warn_mw:.0f} mW floor")
            v.reason = (f"{1e3 * v.value:+.2f} mW from the baseline "
                        f"({100 * (q_frac - self.baseline):+.2f} % of "
                        f"delivered), over {why} at "
                        f"{1e3 * threshold:.2f} mW")

        # THE BASELINE FREEZES ON THE BAND, NOT ON THE WARNING, and the two
        # came apart the moment Jeff set a 5 mW floor under the warning.
        #
        # What the floor says is "do not bother me about anything smaller than
        # this".  That is a REPORTING decision.  Freezing the baseline is a
        # LEARNING decision, and the right criterion for it is the model's own
        # uncertainty: anything outside 3 sigma is, by construction, not
        # something a model of ordinary behaviour should be absorbing.
        #
        # Keyed to the warning instead, the 2026-09-10 event disappears
        # entirely.  Its residual is -5.01 mW against a 5 mW floor, so the
        # verdict stayed typical, so the baseline kept learning, so it walked
        # onto the fault inside a few hours -- and the fault-level flag never
        # fired either, because by the time the excursion reached 11.6 mW the
        # baseline had moved most of the way to meet it.  A monitor that is
        # told to ignore small things must not thereby be taught that a large
        # thing is normal.
        learning = abs(v.value) <= self.cfg.warn_sigma * sigma
        dt = s.t_s - (self.baseline_t or s.t_s)
        if learning and dt > 0:
            alpha = 1.0 - math.exp(-dt / self.cfg.baseline_tau_s)
            self.baseline += alpha * (q_frac - self.baseline)
        self.baseline_t = s.t_s

        # THE FAULT IS A STEP, NOT A LEVEL, and the archive is what settled
        # that (Jeff, 2026-09-14: "it should trigger fairly quickly or not at
        # all").
        #
        # A change in delivered power puts a STEP in dQ, immediately and by
        # construction: with `C dT/dt = a P(u) - [Lambda(T_s) - Lambda(T_c)]`,
        # the residual is identically `-(1 - a) P(u)` from the instant `a`
        # changes, whatever the sample then does.  The 2026-09-10 event is that
        # shape exactly -- +0.13 mW to -5.18 mW in SEVEN MINUTES, flat at -5
        # for the three hours after, and the -14 mW at +190 min is a different
        # event, the connector being reseated.
        #
        # So a residual that takes an hour to reach a fault level did not step,
        # and whatever it is, it is not the failure this fault is for.  Faulting
        # on it is the worst available outcome: a ramp-down, hours late, for
        # something that was never sudden.  Slow degradation has its own fault
        # and it is a different one -- authority exhausted, railed at the band
        # with the error past `fault_error_k` (PID_PLAN.md section 1), which no
        # window here gates.
        #
        # Measured as the RANGE of the residual inside `fault_window_s`, which
        # also keeps working when a second event lands on top of a warning
        # already in progress -- a level test anchored to the band crossing
        # would have gone blind for the whole three hours the 09-10 warning was
        # up, which is exactly when the connector was handled.
        self._fault_hist.append((s.t_s, v.value))
        floor_t = s.t_s - self.cfg.fault_window_s
        while len(self._fault_hist) > 1 and self._fault_hist[0][0] < floor_t:
            self._fault_hist.popleft()
        recent = [x[1] for x in self._fault_hist]
        step = max(recent) - min(recent)

        fault_at = max(self.cfg.fault_mw * 1e-3, self.cfg.warn_sigma * sigma)
        beyond = step >= fault_at * (self.cfg.hysteresis_frac
                                     if self._fault.leaning else 1.0)
        fault.value, fault.sigma = step, fault_at
        # Latched: a step that happened does not stop having happened, and the
        # window it was measured in scrolls past in half an hour.  Cleared by a
        # new recording, which is the same thing that clears the baseline.
        declared = self._fault.update(WARN if beyond else TYPICAL, s.t_s)
        if declared == WARN and not self._fault_seen:
            self._fault_seen = True
            self._fault_note = (
                f"the residual stepped {1e3 * step:.2f} mW inside "
                f"{self.cfg.fault_window_s / 60:.0f} min, past a "
                f"{1e3 * fault_at:.1f} mW fault level -- REPORTED, not acted on")
        fault.state = WARN if self._fault_seen else declared
        fault.out_of_band_s = self._fault.held_s(s.t_s)
        if fault.state == WARN:
            fault.reason = self._fault_note
        return q_abs, v, fault

    # -- what a person reads ----------------------------------------------

    def _record_changes(self, record: dict) -> None:
        """Every verdict CHANGE, with its time.  The replay's whole output."""
        for v in list(record["verdicts"]) + [record["fault_level"]]:
            was = self._states.get(v.name)
            if was != v.state:
                self._states[v.name] = v.state
                if was is not None:
                    self.changes.append(
                        (record["epoch_s"], record["t_s"], v.name, was,
                         v.state, v.reason, record["sample_k"]))
