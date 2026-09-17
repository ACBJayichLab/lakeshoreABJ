"""Gain scheduling and IMC tuning: a first-order closed loop with no overshoot.

Why this replaces the steady-state curve as the control model
-------------------------------------------------------------

The sample is a weakly-pinned island: small mass, small heater, one real
thermal link to the coldplate (radiation is mostly from parts that are
themselves sunk to the coldplate).  Everything that matters to a controller is
therefore *local*:

* the **gain** ``K = dT/d(pct)`` in K/%, set by the conductance of that link;
* the **time constant** ``tau = C/G``, set by the island's heat capacity.

Both vary with temperature, and both change again when the coldplate moves --
heating an island off a 300 K coldplate is a different thermal response from heating it
off a 4 K one, because the effective cooling power is completely different.

A global percent-to-temperature curve tries to encode all of that in one
function and cannot: fitting ``dT ~ pct^6.51`` and ``dT ~ (pct-56.9)^0.92`` to
the same 24 settled points gives R^2 of 0.9969 and 0.99998 respectively, and
they disagree by tens of kelvin outside the fitted band.  The logs do not
contain the information needed to choose.  Local gain and time constant, by
contrast, are exactly what a step test measures, need no extrapolation, and are
re-measurable whenever the cryostat changes.

The tuning
----------

Model the thermal response near an operating point as first order::

    G(s) = K / (1 + tau*s)

An IMC / pole-cancellation PI controller then gives a closed loop that is
*first order* -- no overshoot at any gain, which is what "critically damped"
buys on a cryostat where overshoot is wasted hours::

    Ti = tau
    Kp = tau / (K * tau_cl)          =>   closed loop = 1 / (1 + tau_cl*s)

``tau_cl`` is chosen directly: it *is* the closed-loop response time.

A ratio, not a time -- phase 3 §3.2
-----------------------------------

``tau_cl`` used to be configured as two absolute times, 1800 s holding and
300 s moving.  An absolute time is the one thing it must not be here, because
**tau(T) runs from 0.1 s at 10 K to 611 s at 180 K**: 1800 s is three times the
plant at the top of the range and eighteen thousand times it at the bottom, so
the same pair of numbers meant "gentle" up there and "asleep" down here.

So it is a ratio to the plant's own time constant::

    tau_cl = max(speed * tau(T), delay_floor * delay_s)
    Kp     = tau / (K(T) * (tau_cl + theta))     -> 1 / (speed * K(T))
    Ti     = min(tau, 4 * (tau_cl + theta))      SIMC, floored on the delay

and above the floor ``Kp`` is simply ``1 / (speed * K)``, which is the whole
argument for configuring a ratio.

``theta`` is the loop's own dead time, derived from the filter chain and the
measured cadence (``MeasurementFilter.group_delay_s``, about 3 s).  **It is
not decoration.**  Pole cancellation is right only while the plant's pole is
the slowest thing in the loop, and below about 30 K it is not: cancelling a
0.1 s pole against a 3 s delay is a loop integrating a measurement it has not
seen yet.  SIMC's ``min`` and the delay floor are what handle that, and
together they are why the cold end needs no schedule of its own.

Two phases
----------

Holding and moving want opposite things, so they get different ``speed``:

* **HOLD** -- stabilising at temperature for hours.  Disturbances are slow
  (bath drift, radiation) and the measurement floor is a few mK of *correlated*
  noise.  A slow loop rejects that noise; a fast one amplifies it into the
  heater.  The default ``hold_speed: 3`` is three times slower than the
  cryostat; **the armed LTSPM3 file runs 0.25**, four times faster, because
  the slow regime turned out to stir rather than correct
  (docs/ltspm3/requirements.md).
* **MOVE** -- following a commanded sweep, or approaching setpoint after a
  fault.  Here bandwidth is the point, and a few mK of extra noise on the way
  is irrelevant.  ``move_speed`` 0.5 by default, 0.15 in the armed file.

Switching between them is hysteretic, because a loop that oscillates between
tunings is worse than either.

Where the numbers come from
---------------------------

``FittedSchedule`` reads ``K(T)`` and ``tau(T)`` out of
:mod:`ltspm3.model.fitted_response` -- there is no table in this file to keep
in step with the fit, which is PID_PLAN.md's "pastes rot" trap closed by not
having a paste.  It agrees with ``analysis/pid_tuning.py --rows`` to better
than 0.5 % everywhere that prints.
"""

from __future__ import annotations

import bisect
import enum
import math
from dataclasses import dataclass


class ControlPhase(enum.Enum):
    HOLD = "hold"    # stabilising: slow, quiet
    MOVE = "move"    # sweeping or approaching: responsive


@dataclass(frozen=True)
class OperatingPoint:
    """One measured step response.

    ``gain_k_per_pct`` and ``tau_s`` are *local* quantities -- what the response
    does for a small change here, not a claim about the whole range.
    """

    kelvin: float
    gain_k_per_pct: float
    tau_s: float
    #: Coldplate temperature when this was measured.  The same island off a
    #: 300 K plate is a different thermal response; recording it makes that visible
    #: instead of silently averaging two regimes together.
    coldplate_k: float | None = None
    note: str = ""


# `PROVISIONAL_SCHEDULE` was four rows, of which ONE was measured -- the
# 65.9% -> 137.3 K step, tau ~= 620 s -- and the other three were the CD10
# steady-state curve's local slope with that tau copied onto them.  Phase 3
# step 3 deleted it.  Nothing replaced it with a better table: the gain and the
# time constant are now read from `ltspm3.model.fitted_response`, which is the
# 43 h sweep fitted over the whole range, and `FittedSchedule` below is the
# whole of what that takes.
#
# For the record, what the provisional table claimed against what the fit
# measures at the same temperatures:
#
#     18.2 K   1.6 K/%,  tau 300 s (guessed)   ->   0.66 K/%,  tau 0.8 s
#     99.6 K  10.0 K/%,  tau 620 s (copied)    ->  12.42 K/%,  tau 440 s
#    137.3 K  13.0 K/%,  tau 620 s (MEASURED)  ->  12.68 K/%,  tau 586 s
#    170.7 K  13.4 K/%,  tau 620 s (copied)    ->  12.25 K/%,  tau 616 s
#
# The one measured row was good to 2.5 %.  The guessed tau at the cold end was
# out by a factor of 375.


@dataclass
class TuningConfig:
    """Closed-loop speed as a RATIO to the plant's own, and the sane bounds.

    Phase 3 §3.2.  ``hold_tau_cl_s: 1800`` and ``move_tau_cl_s: 300`` were
    absolute times, and an absolute time is the one thing a closed-loop target
    must not be on this cryostat: tau(T) runs from **0.1 s at 10 K to 611 s at
    180 K**, so 1800 s is three times the plant at the top and eighteen
    THOUSAND times it at the bottom.  The same two numbers meant "gentle" up
    there and "asleep" down here.

    A ratio means the same thing everywhere::

        tau_cl = max(speed * tau(T), 4 * delay_s)
    """

    enabled: bool = True

    #: Stabilising.  The DEFAULT is 3 -- slower than the plant, Jeff's
    #: 2026-09-11 instinct that a hold should never amplify noise -- and the
    #: bench envelope (`BENCH_HOLD_SPEED`) is calibrated to it.  **The armed
    #: file ships 0.25** (docs/ltspm3/requirements.md, 2026-09-17): the weak
    #: regime stirred at its own natural period on the cryostat, and on the
    #: bench a hold at 0.25 is 1.00x open loop at 10-15 s and 0.71x at 900 s,
    #: where 12 was 2.15x.  The tables are in config-ltspm3-armed.yaml.
    hold_speed: float = 3.0
    #: Sweeping or approaching: faster than the plant, because bandwidth is the
    #: point and a few mK of extra noise on the way is irrelevant.  The armed
    #: file ships 0.15 -- a 2 K move at 118 K in 4.6 min on the bench, against
    #: a hand step's ~26; 0.5 stacked a 260 s corner on a 260 s loop.
    move_speed: float = 0.5

    #: How many dead times the closed loop must be slower than, whatever the
    #: ratios ask for.  Four is the usual engineering floor for a PI loop with
    #: a delay, and below about 30 K it is what binds -- the plant is faster
    #: than the measurement, so nothing else can set the speed.
    delay_floor: float = 4.0

    #: Enter MOVE when a ramp is running or the error exceeds this.
    move_error_k: float = 0.25
    #: Return to HOLD only below this, and only after settling for a while.
    hold_error_k: float = 0.10
    hold_settle_s: float = 120.0

    #: Absolute bounds on the scheduled gains.  A bad schedule entry, or an
    #: operating point far outside the table, must not produce a violent loop.
    #:
    #: **`max_kp` was 0.50 and the fitted schedule wants 0.519 at 40 K in
    #: `move`**, so it clamped -- silently, which is the worst way for a limit
    #: to bind.  1.0 is above every row the model produces (the largest is
    #: 0.52) and still far below anything violent: at the 1.96 K/% gain of
    #: 30 K, kp = 1.0 is a loop gain of 2.
    min_kp_pct_per_k: float = 0.002
    max_kp_pct_per_k: float = 1.0
    #: **`min_ti` was 60 s against a plant tau of 0.1 s at 10 K.**  An integral
    #: time floored at 600x the plant is not a floor, it is a different
    #: controller.  `Ti` is now floored on the loop's own delay instead --
    #: `min_ti_delays * delay_s` -- which is the only timescale that means
    #: anything down there.  See `Tuner.gains_for`.
    min_ti_delays: float = 1.0
    max_ti_s: float = 7200.0

    #: An explicit table, for a cryostat that has one.  **Empty means read the
    #: shipped fit**, which is what LTSPM3 does: see :class:`FittedSchedule`.
    schedule: tuple[OperatingPoint, ...] = ()


def imc_pi(gain_k_per_pct: float, tau_s: float, tau_cl_s: float) -> tuple[float, float]:
    """PI gains for a first-order closed loop of time constant ``tau_cl_s``.

    Returns ``(kp_pct_per_k, ti_s)``.  Pole cancellation: ``Ti = tau`` removes
    the response pole, leaving an integrator, so the loop is first order and
    cannot overshoot however ``tau_cl`` is chosen.
    """
    if gain_k_per_pct <= 0:
        raise ValueError("response gain must be positive")
    if tau_s <= 0 or tau_cl_s <= 0:
        raise ValueError("time constants must be positive")
    return tau_s / (gain_k_per_pct * tau_cl_s), tau_s


def simc_pi(gain_k_per_pct: float, tau_s: float, tau_cl_s: float,
            delay_s: float = 0.0, *, min_ti_s: float = 0.0) -> tuple[float, float]:
    """PI gains for a first-order plant **with a dead time**.

    :func:`imc_pi` above is this with ``delay_s = 0``, and it is what phase 3
    §3.2 was first written as.  It is wrong at the cold end, and not by a
    little::

        Kc = tau_eff / (K (tau_c + theta))
        Ti = min(tau_eff, 4 (tau_c + theta))        Skogestad's SIMC

    Pole cancellation sets ``Ti = tau`` so that the controller's zero removes
    the plant's pole.  That is exactly right while the plant's pole is the
    slowest thing in the loop -- and below about 30 K it is not.  tau(10 K) is
    **0.1 s** against a measurement dead time of 3 s, so cancelling it leaves
    an integral time thirty times shorter than the delay, which is a loop that
    integrates a measurement it has not seen yet.  SIMC's ``min`` is what
    handles that, and the floor below is what keeps ``Ti`` above the delay
    itself.

    ``min_ti_s`` is derived by the caller from the loop's own dead time, never
    a constant: a fixed 60 s floor was 600x the plant at 10 K.
    """
    if gain_k_per_pct <= 0:
        raise ValueError("response gain must be positive")
    if tau_s <= 0 or tau_cl_s <= 0:
        raise ValueError("time constants must be positive")
    if delay_s < 0:
        raise ValueError("delay must not be negative")
    kp = tau_s / (gain_k_per_pct * (tau_cl_s + delay_s))
    ti = min(tau_s, 4.0 * (tau_cl_s + delay_s))
    return kp, max(ti, min_ti_s)


class PlantSchedule:
    """Local gain and time constant as functions of temperature."""

    def __init__(self, points=()) -> None:
        pts = sorted(points, key=lambda p: p.kelvin)
        if not pts:
            raise ValueError("schedule needs at least one operating point")
        self.points = tuple(pts)
        self._k = [p.kelvin for p in self.points]

    @staticmethod
    def _blend(x, x0, x1, y0, y1):
        """Interpolate in log space -- gain and tau are positive and span
        decades, so a linear blend would badly misrepresent the middle."""
        if x1 == x0:
            return y0
        f = (x - x0) / (x1 - x0)
        f = max(0.0, min(1.0, f))          # clamp: never extrapolate a gain
        return math.exp(math.log(y0) + f * (math.log(y1) - math.log(y0)))

    def _bracket(self, kelvin: float) -> tuple[OperatingPoint, OperatingPoint]:
        if len(self.points) == 1:
            return self.points[0], self.points[0]
        i = bisect.bisect_left(self._k, kelvin)
        if i <= 0:
            return self.points[0], self.points[0]      # clamp, do not extrapolate
        if i >= len(self.points):
            return self.points[-1], self.points[-1]
        return self.points[i - 1], self.points[i]

    def gain_at(self, kelvin: float) -> float:
        a, b = self._bracket(kelvin)
        return self._blend(kelvin, a.kelvin, b.kelvin, a.gain_k_per_pct, b.gain_k_per_pct)

    def tau_at(self, kelvin: float) -> float:
        a, b = self._bracket(kelvin)
        return self._blend(kelvin, a.kelvin, b.kelvin, a.tau_s, b.tau_s)

    def extrapolating(self, kelvin: float) -> bool:
        """True outside the measured range, where gains are clamped not fitted."""
        return kelvin < self._k[0] or kelvin > self._k[-1]


class FittedSchedule:
    """The same two questions, answered by the model instead of by a table.

    **There is no paste, so there is nothing to rot.**  PID_PLAN.md's trap says
    a schedule row carries the fit's cache key and a test diffs it against a
    fresh export, which is a good answer to a table that has to be copied by
    hand.  Not copying it is a better one: ``gain_k_per_pct`` and ``tau_s`` are
    functions of the shipped table, so a re-export moves the controller and the
    simulator together, in the same commit, by construction.

    It agrees with ``analysis/pid_tuning.py --rows`` -- the thing that would
    have been pasted -- to better than 0.5 % at every temperature that prints.

    Clamps at the table's ends rather than extrapolating, exactly as
    :class:`PlantSchedule` does, and for the same reason: past 195 K nobody has
    measured this cryostat and a confident gain there is a fiction.
    """

    def __init__(self) -> None:
        from ..model import fitted_response as M

        self._M = M
        self.points = ()

    def _clamp(self, kelvin: float) -> float:
        return min(max(kelvin, self._M.T_MIN_K), self._M.T_MAX_K)

    def gain_at(self, kelvin: float) -> float:
        return self._M.gain_k_per_pct(self._clamp(kelvin))

    def tau_at(self, kelvin: float) -> float:
        return self._M.tau_s(self._clamp(kelvin))

    def extrapolating(self, kelvin: float) -> bool:
        return not (self._M.T_MIN_K <= kelvin <= self._M.T_MAX_K)

    @property
    def key(self) -> str:
        """The shipped table's cache key, so a status line can carry it."""
        return self._M.FIT_KEY


class Tuner:
    """Picks (kp, ti) for the present temperature and phase."""

    def __init__(self, config: TuningConfig | None = None) -> None:
        self.cfg = config or TuningConfig()
        self.schedule = (PlantSchedule(self.cfg.schedule) if self.cfg.schedule
                         else FittedSchedule())
        self.phase = ControlPhase.HOLD
        self._settled_since: float | None = None
        #: The loop's pure delay, in seconds.  **Set by the supervisor from the
        #: filter chain and the measured cadence** -- see
        #: `MeasurementFilter.group_delay_s` -- because it is a property of that
        #: chain and not a number anybody should type into a tuning section.
        #: 0.0 means "nobody has said", which is what a bare `Tuner()` in a test
        #: gets and is why section 3.2's floor is written `max(..., 4 * delay)`
        #: rather than assuming a delay exists.
        self.delay_s = 0.0

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def speed_for(self, phase: ControlPhase) -> float:
        return (self.cfg.move_speed if phase is ControlPhase.MOVE
                else self.cfg.hold_speed)

    def tau_cl_for(self, phase: ControlPhase, kelvin: float | None = None) -> float:
        """The closed-loop time constant here: a RATIO to the plant's own.

        Floored at ``delay_floor * delay_s``, which is what binds below about
        30 K -- there the plant settles faster than the loop can see it, so the
        measurement and not the cryostat decides how fast this may go.
        `analysis/pid_tuning.py --rows` prints where that happens: `move` is
        floored from 10 to 30 K at the 2 s cadence.
        """
        speed = self.speed_for(phase)
        tau = self.schedule.tau_at(kelvin) if kelvin is not None else 0.0
        return max(speed * tau, self.cfg.delay_floor * self.delay_s)

    def update_phase(self, t: float, *, error_k: float, ramping: bool) -> ControlPhase:
        """Hysteretic HOLD/MOVE selection.

        A ramp always means MOVE.  Otherwise MOVE is entered on a large error
        and left only after the error has been small for ``hold_settle_s`` --
        chattering between two tunings is worse than either of them.
        """
        c = self.cfg
        if ramping or abs(error_k) > c.move_error_k:
            self.phase = ControlPhase.MOVE
            self._settled_since = None
        elif self.phase is ControlPhase.MOVE:
            if abs(error_k) <= c.hold_error_k:
                if self._settled_since is None:
                    self._settled_since = t
                elif t - self._settled_since >= c.hold_settle_s:
                    self.phase = ControlPhase.HOLD
                    self._settled_since = None
            else:
                self._settled_since = None
        return self.phase

    def gains_for(self, kelvin: float, phase: ControlPhase | None = None
                  ) -> tuple[float, float]:
        """``(kp, ti)`` for this temperature and phase, bounded.

        Above the delay floor ``kp`` reduces to ``1 / (speed * K(T))`` -- one
        number, and the reason a ratio was the right thing to configure.
        """
        c = self.cfg
        phase = phase or self.phase
        gain = self.schedule.gain_at(kelvin)
        tau = self.schedule.tau_at(kelvin)
        kp, ti = simc_pi(gain, tau, self.tau_cl_for(phase, kelvin), self.delay_s,
                         min_ti_s=c.min_ti_delays * self.delay_s)
        kp = max(c.min_kp_pct_per_k, min(c.max_kp_pct_per_k, kp))
        ti = min(c.max_ti_s, ti)
        return kp, ti


def identify_first_order(samples, *, settle_fraction: float = 0.1
                         ) -> tuple[float, float, float]:
    """Fit ``(T_final, tau, r2)`` to a step response.

    ``samples`` is ``(t_s, kelvin)`` starting at the step.  The fit is on
    ``ln|T_inf - T|``, which is linear in t for a first-order response;
    ``T_inf`` is taken from the tail.  Points within ``settle_fraction`` of
    ``T_inf`` are dropped because their logarithm is dominated by noise.

    This is the whole of what a step test needs to yield -- combined with the
    output step size it gives gain, and it gives tau directly.
    """
    pts = [(t, v) for t, v in samples if v is not None]
    if len(pts) < 20:
        raise ValueError("need at least 20 samples to identify a step response")
    t0, y0 = pts[0]
    tail = pts[-max(5, len(pts) // 20):]
    t_inf = sum(v for _, v in tail) / len(tail)
    span = t_inf - y0
    if abs(span) < 1e-9:
        raise ValueError("no step in this data")

    xs, ys = [], []
    for t, v in pts:
        r = t_inf - v
        if r * span <= 0 or abs(r) < settle_fraction * abs(span):
            continue
        xs.append(t - t0)
        ys.append(math.log(abs(r)))
    if len(xs) < 10:
        raise ValueError("not enough of the transient survives the settle cut")

    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((a - mx) ** 2 for a in xs)
    slope = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / den
    if slope >= 0:
        raise ValueError("response is not decaying toward a final value")
    res = sum((b - (my + slope * (a - mx))) ** 2 for a, b in zip(xs, ys))
    tot = sum((b - my) ** 2 for b in ys)
    return t_inf, -1.0 / slope, (1.0 - res / tot if tot > 0 else 1.0)
