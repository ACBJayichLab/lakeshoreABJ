"""Walk the heater up a ladder of outputs and sit at each one until it settles.

What this is for
----------------

The steady-state curve is measured where somebody happened to dwell, and the
2026-09 sweep left a hole: between about 38 K and 100 K -- roughly 56.5% to 63%
output -- there is almost nothing, because a manual walk spends its patience
where the operator was watching.  ``analysis/plot_gain.py`` draws that hole.
Filling it is what this tool does: a planned ladder, a dwell at every tread,
and each dwell held until it would *grade* rather than until a timer expires.

It is a **client**, not a recorder.  The recorder owns the port; this talks to
it through the file interface exactly as MATLAB does, and so passes exactly the
same interlocks.  Nothing here can move a heater that
``ipc.allow_analog_output`` has not already opened.

Why the dwell is adaptive
-------------------------

Fixed dwells are wrong at both ends, and the fitted model says by how much::

     T      u%     dT/du     tau
     10 K   24.6   0.3 K/%    <1 s
     40 K   56.4   3.9 K/%     36 s
     70 K   60.6  11.6 K/%    246 s
    110 K   63.7  13.2 K/%    489 s

Two minutes at 10 K is already hundreds of time constants; two minutes at 110 K
is a quarter of one, and the point it yields is a fifth of the way to where it
was going.  So the stop rule is the one ``analysis/steps.py`` applies afterwards
when it decides whether a dwell is worth keeping -- fit the dwell as a single
pole ``T(t) = T_inf + A exp(-t/tau)``, and move on when

* ``reach = span/tau`` is at least ``--min-reach``, so tau may be believed;
* ``settle_K = T_inf - T_end``, how far it still had to go, is inside
  ``--max-settle-k``;
* the fitted rate at the last sample is under ``--max-end-rate``.

A dwell that meets those is a point the fitter will keep.  A dwell that does
not meet them by ``--max-dwell`` is recorded anyway, with its grade blank, so
the journal says which treads are worth re-running rather than silently
carrying a point the pipeline will drop.

Safety
------

The tool refuses to start against a recorder whose software loop is driving,
because two things commanding one analog output is a race with a heater on the
end of it.  While it runs, every sample is checked: an over-temperature, an
unusable sample channel, a stale status file, or an output readback that
disagrees with what was commanded all stop the sweep.

**Stopping means holding.**  On any abort the output is left exactly where it
is, which is invariant 6 -- availability outranks the dataset, and cutting this
heater is a change of state rather than a retreat to safety.  ``--on-abort off``
sends the ``heaters_off`` panic instead, and is for the case where the operator
has decided the stage is better cold than warm.

``--on-abort off`` is not a bigger version of the same thing, and on LTSPM3 it
is the wrong choice.  ``heaters_off`` means *every writable heater on this
recorder*, deliberately -- a panic button that leaves one heater running is
worse than none.  ``config-ltspm3-heater.yaml`` also opens the 336, whose
heater 2 is railed at 100% holding THE CHONKE and is somebody else's, so that
flag would cut theirs as well as ours.  Leave it on ``hold``.

Usage
-----

Plan it first -- this touches nothing and prints the ladder and the ETA::

    python -m ltspm3.tools.sweep --plan sweep_plan.csv --plan-only

Rehearse the whole thing on a virtual clock, no recorder and no hardware::

    python -m ltspm3.tools.sweep --plan sweep_plan.csv --simulate

Then, against a running recorder, which asks before the first write::

    python -m ltspm3.tools.sweep -c config.yaml --plan sweep_plan.csv

The ladder comes from ``analysis/plan_sweep.py``, which sizes it and the dwells
from the fitted model.  ``--percents`` takes one on the command line instead,
and is what to use when there is no plan file on the cryostat's machine.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import math
import os
import sys
import time
from dataclasses import dataclass, replace

# -- the grader, mirrored --------------------------------------------------
#
# These are ``analysis/steps.py``'s constants and they are duplicated on
# purpose: ``analysis/`` is exploratory, imports neither package and is
# imported by neither, so the alternative to copying four numbers is a
# dependency that invariant 1 exists to prevent.  They are a stop rule here and
# a keep/drop decision there, and if they ever diverge the symptom is a sweep
# whose points the fitter throws away -- which the journal's ``grade`` column
# reports on the spot rather than a week later.

#: Sensor noise, from docs/ltspm3/thermal-response.md: quadratic in T, floored
#: near 1.8 mK.
NOISE_FLOOR_K = 0.0018
NOISE_QUADRATIC = 1.36e-6

#: A dwell must run this many time constants before its tau is believed.  A fit
#: over less than about three returns tau far too small at an R^2 that still
#: reads healthy.
MIN_REACH = 3.0
#: ...and its transient must be this many times the sensor noise, or there is
#: nothing to fit a time constant to and only T_inf survives.
MIN_AMPLITUDE_SIGMA = 20.0
#: Past this much extrapolation from the last sample to T_inf, the "steady"
#: value is a prediction of the model being fitted rather than a measurement.
MAX_SETTLE_K = 2.0
#: How fast the sample may still be moving when the dwell ends, in K/h, read
#: off the fitted pole at the last sample.  This is the test that separates a
#: settled hold from one cut off mid-relaxation; total amplitude gets that
#: backwards for the long holds.
MAX_END_RATE_K_PER_H = 0.5

#: How far the reported heater may wander from what was commanded before this
#: concludes somebody else is driving.  NOT the DAC resolution: the 218's
#: ``AOUT?`` readback flickers between adjacent codes at some values, which is
#: 0.003% and well inside this.  The smallest deliberate step in this campaign
#: is 0.1%, so there is a factor of five in hand either way.
U_TOL_PCT = 0.02


def noise_k(kelvin: float) -> float:
    return max(NOISE_FLOOR_K, NOISE_QUADRATIC * kelvin * kelvin)


class SweepAbort(RuntimeError):
    """Something the sweep will not drive through.  The output stays put."""


# -- the plan --------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Tread:
    """One rung: where to put the output, and how long to be willing to wait."""

    u_pct: float
    #: What the model expects, for the operator's benefit and for the ETA.
    #: ``None`` when the ladder came from ``--percents``, which carries no model.
    t_pred_k: float | None = None
    tau_pred_s: float | None = None
    dwell_pred_s: float | None = None


def read_plan(path: str) -> list[Tread]:
    """A ladder as written by ``analysis/plan_sweep.py``.

    Only ``u_pct`` is required.  A file with nothing but that column is a
    perfectly good plan; the rest is what makes the ETA and the per-tread dwell
    cap better than one global number.
    """
    def num(row, key):
        try:
            return float(row[key])
        except (KeyError, TypeError, ValueError):
            return None

    out: list[Tread] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            u = num(row, "u_pct")
            if u is None:
                continue
            out.append(Tread(u, num(row, "T_pred_k"), num(row, "tau_pred_s"),
                             num(row, "dwell_pred_s")))
    if not out:
        raise SweepAbort(f"{path}: no usable `u_pct` rows")
    return out


def plan_from_percents(text: str) -> list[Tread]:
    return [Tread(float(p)) for p in text.replace(",", " ").split()]


def without_model_dwells(treads: list[Tread]) -> list[Tread]:
    """The ladder with the model's TIMING stripped and its temperatures kept.

    For the rehearsal, and it is not a detail.  The plan's per-rung caps come
    from ``tau(T)`` off the fitted model -- under a second below 30 K -- and the
    simulator does not implement that model: ``ResponseParams`` carries ONE time
    constant, about 620 s, inferred from a single step at 137 K.  Hand the
    simulator a 120 s cap for every cold rung and it cuts all of them one fifth
    of the way through a relaxation, and the run comes back a page of dropped
    rungs that says nothing whatever about the cryostat.
    """
    return [replace(t, tau_pred_s=None, dwell_pred_s=None) for t in treads]


def order_plan(treads: list[Tread], order: str,
               current_pct: float | None) -> list[Tread]:
    """Up, down, or from whichever end the cryostat is already nearest.

    Direction is not a nicety.  Starting at the far end means one traverse of
    the whole range before the first point is taken, and on this cryostat that
    traverse is hours -- so ``nearest`` is the one to use when a run is being
    restarted after an abort.
    """
    up = sorted(treads, key=lambda t: t.u_pct)
    if order == "up":
        return up
    if order == "down":
        return list(reversed(up))
    if current_pct is None:
        return up
    return (up if abs(current_pct - up[0].u_pct) <= abs(current_pct - up[-1].u_pct)
            else list(reversed(up)))


# -- fitting one dwell, in pure Python -------------------------------------

@dataclass(frozen=True, slots=True)
class PoleFit:
    """``T(t) = T_inf + A exp(-t/tau)`` over one dwell, and what it grades."""

    t_inf: float
    amp: float
    tau_s: float
    rms_k: float
    span_s: float
    n: int
    t_end: float
    mean_k: float

    @property
    def reach(self) -> float:
        return self.span_s / self.tau_s if self.tau_s > 0 else 0.0

    @property
    def settle_k(self) -> float:
        """How far it still had to go, signed."""
        return self.t_inf - self.t_end

    @property
    def end_rate_k_per_h(self) -> float:
        if self.tau_s <= 0:
            return 0.0
        return 3600.0 * abs(self.amp) / self.tau_s * math.exp(-self.span_s / self.tau_s)

    @property
    def amp_sigma(self) -> float:
        return abs(self.amp) / noise_k(self.mean_k)

    @property
    def rms_sigma(self) -> float:
        return self.rms_k / noise_k(self.mean_k)

    def grade(self, *, min_reach: float = MIN_REACH,
              max_settle_k: float = MAX_SETTLE_K,
              max_end_rate: float = MAX_END_RATE_K_PER_H) -> str:
        """``'tau'`` if the time constant may be believed, ``'steady'`` if only
        ``T_inf`` may be, ``''`` if the dwell ended too early to be either."""
        if abs(self.settle_k) > max_settle_k:
            return ""
        if self.end_rate_k_per_h > max_end_rate:
            return ""
        if (self.reach >= min_reach and self.amp_sigma >= MIN_AMPLITUDE_SIGMA
                and self.rms_sigma < 8.0):
            return "tau"
        return "steady"

    def shortfall(self, *, min_reach: float = MIN_REACH,
                  max_settle_k: float = MAX_SETTLE_K,
                  max_end_rate: float = MAX_END_RATE_K_PER_H) -> str:
        """Which test this dwell failed, in words, or why its tau is weak.

        "Cut at the cap" says a dwell ran out of time and not what it ran out
        of, and those want opposite responses: still a long way to go means the
        step was too big for the time allowed, while still moving slowly at the
        end means only that the last stretch of the exponential is expensive
        and the cap wanted another time constant.
        """
        why = []
        if abs(self.settle_k) > max_settle_k:
            why.append(f"{abs(self.settle_k):.2f} K still to go")
        if self.end_rate_k_per_h > max_end_rate:
            why.append(f"still moving {self.end_rate_k_per_h:.2f} K/h")
        if why:
            return ", ".join(why)
        if self.amp_sigma < MIN_AMPLITUDE_SIGMA:
            return "nothing moved, so T_inf only"
        if self.reach < min_reach:
            return f"only {self.reach:.1f} time constants, so T_inf only"
        return ""


def _solve(ts, ys, tau: float):
    """``(T_inf, A, rms)`` for a fixed tau.  Linear in both, so it is exact."""
    n = len(ts)
    se = sy = see = sey = 0.0
    for t, y in zip(ts, ys):
        e = math.exp(-t / tau)
        se += e
        sy += y
        see += e * e
        sey += e * y
    den = n * see - se * se
    if abs(den) < 1e-12:
        a, b = sy / n, 0.0
    else:
        b = (n * sey - se * sy) / den
        a = (sy - b * se) / n
    res = 0.0
    for t, y in zip(ts, ys):
        d = a + b * math.exp(-t / tau) - y
        res += d * d
    return a, b, math.sqrt(res / n)


def fit_pole(samples, *, max_points: int = 800) -> PoleFit:
    """Fit one dwell.  ``samples`` is ``(t_s, kelvin)`` from the step onward.

    Nonlinear in tau alone -- ``(T_inf, A)`` solve exactly at each trial -- so a
    1-D search over log tau has no starting-guess failure mode.  The upper bound
    is deliberately far past the dwell's own length: a dwell that has NOT
    settled has to be able to say so by returning a tau longer than itself,
    instead of being clipped into looking finished.

    Long dwells are strided down to ``max_points`` first.  At a 2 s cadence a
    40-minute tread is 1,200 samples and this re-runs every few seconds; the
    curve has two parameters and gains nothing from the rest.
    """
    pts = [(t, v) for t, v in samples if v is not None]
    if len(pts) < 8:
        raise ValueError("not enough samples to fit a dwell")
    if len(pts) > max_points:
        stride = len(pts) // max_points + 1
        # Keep the last sample whatever the stride does: settle_K and the end
        # rate are both read off the END of the dwell.
        pts = pts[::stride] + [pts[-1]]
    t0 = pts[0][0]
    ts = [t - t0 for t, _ in pts]
    ys = [v for _, v in pts]
    span = ts[-1]
    if span <= 0:
        raise ValueError("dwell has no duration")

    gaps = sorted(b - a for a, b in zip(ts, ts[1:]))
    dt = gaps[len(gaps) // 2] if gaps else 1.0
    lo = max(2.0 * dt, 1.0)
    hi = 20.0 * span
    if hi <= lo:
        hi = lo * 10.0

    # Golden section on ln(tau).  Fifty iterations shrink the bracket by ten
    # orders of magnitude, which is far finer than tau is knowable to.
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = math.log(lo), math.log(hi)
    c, d = b - phi * (b - a), a + phi * (b - a)
    fc = _solve(ts, ys, math.exp(c))[2]
    fd = _solve(ts, ys, math.exp(d))[2]
    for _ in range(50):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = _solve(ts, ys, math.exp(c))[2]
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = _solve(ts, ys, math.exp(d))[2]
    tau = math.exp((a + b) / 2.0)
    t_inf, amp, rms = _solve(ts, ys, tau)
    return PoleFit(t_inf=t_inf, amp=amp, tau_s=tau, rms_k=rms, span_s=span,
                   n=len(pts), t_end=ys[-1], mean_k=sum(ys) / len(ys))


# -- what a link has to provide --------------------------------------------

@dataclass(frozen=True, slots=True)
class Sample:
    """One acquisition cycle, as this tool needs it."""

    t_s: float
    kelvin: float | None
    usable: bool
    u_pct: float | None
    coldplate_k: float | None = None
    iso: str = ""


class RecorderLink:
    """A recorder that is already running, reached through its file interface.

    Every write goes out as an ``analog`` command on the spool and is waited on
    until the recorder acknowledges it, because the alternative -- assuming a
    queued command was applied -- is the failure mode that makes a heater tool
    dangerous.  Every read comes from ``status.json``, deduplicated on the
    recorder's own cycle counter so a fast poll cannot enter one acquisition
    twice and halve the apparent time constant.
    """

    def __init__(self, cfg, *, channel: str, coldplate: str | None,
                 heater_aux: str | None, source: str,
                 ack_timeout_s: float = 30.0,
                 stale_grace_s: float = 120.0) -> None:
        from lschart.ipc.commands import CommandSpool

        self.cfg = cfg
        self.channel = channel
        self.coldplate = coldplate
        self.heater_aux = heater_aux
        self.source = source
        self.ack_timeout_s = ack_timeout_s
        self.stale_grace_s = stale_grace_s
        self.status_path = cfg.ipc.status_path()
        self.spool = CommandSpool(cfg.ipc.command_path(), ttl_s=cfg.ipc.command_ttl_s)
        self.interval_s = float(cfg.acquisition.interval_s)
        self._last_cycle: object = object()
        self._last_age_s = 0.0

    def describe(self) -> str:
        return f"the recorder at {self.status_path}"

    # -- reading -----------------------------------------------------------

    def _status(self, *, strict: bool = False) -> dict:
        """The status file, or ``{}`` when there is nothing current to read.

        ``strict`` is for the preflight, where a stale file means "do not
        start".  Mid-run it is off, and that is deliberate: a 2 s cadence on a
        GPIB board with a 3 s timeout and a 1-30 s reconnect backoff will
        produce cycles longer than the three-interval staleness bar without
        anything being wrong, and a four-hour run that ends on one of those has
        thrown away the afternoon over a hiccup the recorder recovered from by
        itself.  What makes waiting safe is that the heater is not moving while
        we wait -- the sweep only commands between dwells.  ``stale_grace_s``
        is where patience runs out and the run stops with the output held.

        A recorder that says it has *stopped* is not a hiccup and ends the run
        whichever mode this is in.
        """
        from lschart.ipc.status import read_status, status_age_s

        status = read_status(self.status_path)
        if status is None:
            # One unreadable poll is normal on Windows while the file is being
            # replaced.  It is a fault only if it persists, and the caller's
            # own timeout is what decides that.
            return {}
        if not status.get("running", True):
            raise SweepAbort("the recorder says it has stopped")
        age = status_age_s(status) or 0.0
        self._last_age_s = age
        if age > max(3 * self.interval_s, 5.0):
            if strict:
                raise SweepAbort(f"the recorder's status file is {age:.0f} s old "
                                 "-- it is up but not cycling")
            return {}
        return status

    @staticmethod
    def _aux(status: dict, name: str):
        for entry in status.get("aux", []):
            if entry.get("name") == name:
                return entry.get("value")
        return None

    def _heater_name(self, status: dict) -> str:
        if self.heater_aux:
            return self.heater_aux
        names = [str(e.get("name", "")) for e in status.get("aux", [])]
        for name in names:
            head, _, tail = name.rpartition(".")
            if head and tail.startswith("aout"):
                self.heater_aux = name
                return name
        raise SweepAbort(
            "no analog-output readback in the status file (looked for an "
            f"`*.aoutN` entry; saw {names}) -- pass --heater-aux")

    def sample(self, timeout_s: float | None = None) -> Sample:
        """Block until the recorder publishes a cycle we have not seen."""
        timeout_s = self.stale_grace_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + timeout_s
        while True:
            status = self._status()
            if status and status.get("cycle") != self._last_cycle:
                self._last_cycle = status.get("cycle")
                kelvin = None
                usable = None
                cold = None
                for ch in status.get("channels", []):
                    if ch.get("name") == self.channel:
                        kelvin, usable = ch.get("kelvin"), bool(ch.get("usable"))
                    elif self.coldplate and ch.get("name") == self.coldplate:
                        cold = ch.get("kelvin")
                if usable is None:
                    raise SweepAbort(
                        f"no channel named {self.channel!r} in the status file "
                        f"({[c.get('name') for c in status.get('channels', [])]})")
                return Sample(
                    t_s=float(status.get("t_wall") or time.time()),
                    kelvin=kelvin, usable=bool(usable),
                    u_pct=self._aux(status, self._heater_name(status)),
                    coldplate_k=cold, iso=str(status.get("iso") or ""),
                )
            if time.monotonic() > deadline:
                raise SweepAbort(
                    f"no new acquisition cycle in {timeout_s:g} s -- the recorder "
                    "is up but not sampling (its status file was last written "
                    f"{self._last_age_s:.0f} s ago)")
            time.sleep(min(0.5, self.interval_s / 2.0))

    # -- writing -----------------------------------------------------------

    def _await_ack(self, cid: str, what: str) -> str:
        from lschart.ipc.status import read_status

        deadline = time.monotonic() + self.ack_timeout_s
        while time.monotonic() < deadline:
            status = read_status(self.status_path) or {}
            for ack in (status.get("commands") or {}).get("recent", []):
                if ack.get("id") == cid:
                    if ack.get("ok"):
                        return str(ack.get("message"))
                    raise SweepAbort(f"the recorder refused {what}: "
                                     f"{ack.get('message')}")
            time.sleep(0.2)
        raise SweepAbort(
            f"no acknowledgement of {what} within {self.ack_timeout_s:g} s. It "
            "may still have been applied -- check `lschart status` before doing "
            "anything else")

    def set_output(self, percent: float) -> str:
        cid = self.spool.submit("analog", source=self.source, percent=float(percent))
        return self._await_ack(cid, f"the {percent:.3f}% command")

    def set_output_panic(self) -> str:
        """The one write exempt from the power gates.  Deliberate only."""
        cid = self.spool.submit("heaters_off", source=self.source)
        try:
            return self._await_ack(cid, "heaters_off")
        except SweepAbort as exc:
            return f"{exc} -- CHECK THE CRYOSTAT"

    def preflight(self, treads: list[Tread]) -> tuple[dict, float | None]:
        """Everything that can be found out before the first write.

        Refusing here is most of this function's value.  A sweep started
        against an armed software loop wastes cryostat time in a way that is
        also a race for the heater; one started against a recorder that will
        refuse every command wastes only the operator's afternoon.
        """
        from lschart.ipc.status import read_status

        if read_status(self.status_path) is None:
            raise SweepAbort(
                f"no recorder is running here: {self.status_path} is absent or "
                "unreadable. This tool drives a recorder; it does not open the "
                "port itself")
        status = self._status(strict=True)   # staleness and running, by name

        if not (status.get("commands") or {}).get("accepted"):
            raise SweepAbort("this recorder does not accept commands at all "
                             "(ipc.accept_commands is false)")

        control = status.get("control")
        if control and control.get("output_pct") is not None:
            raise SweepAbort(
                f"the software loop is driving the heater ({control.get('state')}, "
                f"output {control.get('output_pct')}%). Two things commanding one "
                "analog output is a race. Send `hold` and let it let go first")

        top = max(t.u_pct for t in treads)
        for inst in getattr(self.cfg, "instruments", None) or ():
            limit = getattr(inst, "max_output_pct", None)
            if limit is not None and top > float(limit):
                raise SweepAbort(
                    f"the plan reaches {top:.2f}% and {getattr(inst, 'name', '?')} "
                    f"has max_output_pct {float(limit):g}%. Raise the ceiling in "
                    "the config deliberately, or shorten the plan")

        return status, self._aux(status, self._heater_name(status))


class SimLink:
    """The same interface against the calibrated simulator, on a virtual clock.

    A rehearsal, and the only way the stepping logic is testable at all.  What
    it proves is the *procedure* -- the ordering, the stop rule, the journal,
    the abort paths.  What it does not prove is the timing: the simulator's
    ``ResponseParams`` carries one time constant inferred from a single step at
    137 K, where the fitted model has tau running from under a second at 10 K to
    about 500 s at 110 K.  Read a rehearsal's dwell lengths as fiction.
    """

    def __init__(self, *, dt: float = 2.0, start_pct: float = 6.0,
                 channel: str = "Sample", coldplate: str | None = "Coldplate",
                 max_output_pct: float = 70.0) -> None:
        from lschart.instruments import LS218
        from lschart.instruments.sim import Sim218, SimulatedCryostat
        from lschart.transport import LoopbackTransport

        from ..sim_response import LTSPM3_AUX_COUPLING, ResponseParams, ThermalModel

        class _Clock:
            t = 0.0

            def __call__(self):
                return self.t

        self.clock = _Clock()
        self.dt = dt
        self.channel = channel
        self.coldplate = coldplate
        params = ResponseParams()
        start_k = params.steady_state(start_pct)
        self.cryostat = SimulatedCryostat(
            ThermalModel(params, start_k=start_k), start_k=start_k,
            time_source=self.clock, seed=7, aux_coupling=LTSPM3_AUX_COUPLING)
        self.sim = Sim218(self.cryostat)
        self.sim.analog_pct = start_pct
        self.cryostat.response.pct = start_pct
        self.inst = LS218(LoopbackTransport(self.sim),
                          channels={1: "Sample", 2: "Coldplate", 5: "Magnet"},
                          allow_writes=True, verify_writes=False,
                          max_output_pct=max_output_pct)
        #: The simulator's own time constant, published so a rehearsal can size
        #: its dwell cap from the plant it is actually driving rather than from
        #: the fitted model, which this is not.
        self.tau_fast_s = float(params.tau_fast)
        self.writes: list[tuple[float, float]] = []

    def describe(self) -> str:
        return "the calibrated simulator, on a virtual clock"

    def sample(self, timeout_s: float = 60.0) -> Sample:
        self.clock.t += self.dt
        readings, aux = self.inst.read_frame()
        r = readings.get(self.channel)
        cold = readings.get(self.coldplate) if self.coldplate else None
        return Sample(
            t_s=self.clock.t,
            kelvin=None if r is None else r.kelvin,
            usable=bool(r is not None and r.usable),
            u_pct=aux.get(f"{self.inst.name}.aout1"),
            coldplate_k=None if cold is None else cold.kelvin,
            iso=f"T+{self.clock.t:.0f}s",
        )

    def set_output(self, percent: float) -> str:
        self.inst.set_analog_percent(percent)
        self.writes.append((self.clock.t, percent))
        return f"simulated analog output -> {percent:.3f}%"

    def set_output_panic(self) -> str:
        self.inst.set_analog_percent(0.0)
        return "simulated heaters off"

    def preflight(self, treads: list[Tread]) -> tuple[dict, float | None]:
        top = max(t.u_pct for t in treads)
        if top > self.inst.max_output_pct:
            raise SweepAbort(
                f"the plan reaches {top:.2f}% and the simulated ceiling is "
                f"{self.inst.max_output_pct:g}%")
        return {}, self.sim.analog_pct


# -- the run itself --------------------------------------------------------

@dataclass
class Options:
    min_dwell_s: float = 120.0
    max_dwell_s: float = 2400.0
    min_reach: float = MIN_REACH
    max_settle_k: float = MAX_SETTLE_K
    max_end_rate: float = MAX_END_RATE_K_PER_H
    max_k: float = 120.0
    max_coldplate_k: float | None = None
    #: Consecutive unusable samples before the sweep gives up.  One is a
    #: glitch, and `control/`'s guard exists precisely because one is common.
    max_bad_samples: int = 5
    #: Samples to let pass after a step before the output readback is expected
    #: to agree.  Writes are applied asynchronously; a query issued too soon
    #: answers with the previous value.
    settle_samples: int = 3
    #: Refit every N samples once the minimum dwell is past.  Every sample
    #: would be honest and pointless -- the answer cannot move in 2 s.
    fit_every: int = 5


@dataclass
class DwellResult:
    tread: Tread
    u_readback_pct: float | None
    started_iso: str
    ended_iso: str
    span_s: float
    n: int
    t_start_k: float
    t_end_k: float
    coldplate_k: float | None
    fit: PoleFit | None
    grade: str
    note: str = ""


JOURNAL_COLUMNS = (
    "u_pct", "u_readback_pct", "t_start", "t_end", "span_s", "n",
    "T_start", "T_end", "T_inf", "settle_K", "tau_s", "reach",
    "amp_K", "amp_sigma", "rms_K", "rms_sigma", "end_rate_k_per_h",
    "Coldplate", "T_pred_k", "tau_pred_s", "grade", "note",
)


def journal_row(d: DwellResult) -> dict:
    """One dwell, in the columns ``analysis/steps.py`` already speaks."""
    f = d.fit

    def q(value, spec):
        return "" if value is None else format(value, spec)

    return {
        "u_pct": f"{d.tread.u_pct:.4f}",
        "u_readback_pct": q(d.u_readback_pct, ".4f"),
        "t_start": d.started_iso,
        "t_end": d.ended_iso,
        "span_s": f"{d.span_s:.1f}",
        "n": d.n,
        "T_start": f"{d.t_start_k:.4f}",
        "T_end": f"{d.t_end_k:.4f}",
        "T_inf": q(None if f is None else f.t_inf, ".4f"),
        "settle_K": q(None if f is None else f.settle_k, ".4f"),
        "tau_s": q(None if f is None else f.tau_s, ".1f"),
        "reach": q(None if f is None else f.reach, ".2f"),
        "amp_K": q(None if f is None else abs(f.amp), ".4f"),
        "amp_sigma": q(None if f is None else f.amp_sigma, ".1f"),
        "rms_K": q(None if f is None else f.rms_k, ".5f"),
        "rms_sigma": q(None if f is None else f.rms_sigma, ".2f"),
        "end_rate_k_per_h": q(None if f is None else f.end_rate_k_per_h, ".4f"),
        "Coldplate": q(d.coldplate_k, ".4f"),
        "T_pred_k": q(d.tread.t_pred_k, ".2f"),
        "tau_pred_s": q(d.tread.tau_pred_s, ".0f"),
        "grade": d.grade,
        "note": d.note,
    }


class Journal:
    """One row per tread, flushed as it is written.

    Flushed because the interesting failure is the one that ends the run: a
    journal that only exists at the end is a journal that does not exist on the
    day it matters.
    """

    def __init__(self, path: str | None) -> None:
        self.path = path
        self.rows: list[dict] = []
        self._fh = None
        self._writer = None

    def __enter__(self):
        if self.path:
            self._fh = open(self.path, "w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._fh, fieldnames=list(JOURNAL_COLUMNS))
            self._writer.writeheader()
            self._fh.flush()
        return self

    def write(self, d: DwellResult) -> None:
        row = journal_row(d)
        self.rows.append(row)
        if self._writer is not None:
            self._writer.writerow(row)
            self._fh.flush()

    def __exit__(self, *exc):
        if self._fh is not None:
            self._fh.close()
        return False


def dwell_cap(tread: Tread, opts: Options) -> float:
    """How long this rung may take.

    A per-tread ceiling beats one global number by the ratio of the time
    constants, and on this cryostat that ratio is three orders of magnitude.
    The plan's own prediction is used where it has one, clamped into
    ``[min, max]`` so a wrong model can make no tread either instantaneous or
    endless.
    """
    if tread.dwell_pred_s:
        return min(opts.max_dwell_s, max(opts.min_dwell_s, tread.dwell_pred_s))
    return opts.max_dwell_s


def dwell(link, tread: Tread, opts: Options, *, on_sample=None) -> DwellResult:
    """Command one rung and hold it until it grades, or until the cap says stop."""
    link.set_output(tread.u_pct)
    cap = dwell_cap(tread, opts)

    samples: list[tuple[float, float]] = []
    t0 = None
    bad = 0
    fit = None
    note = ""
    started_iso = ended_iso = ""
    coldplate: float | None = None
    readback: float | None = None

    while True:
        s = link.sample()
        if not s.usable or s.kelvin is None:
            bad += 1
            if bad > opts.max_bad_samples:
                raise SweepAbort(
                    f"{opts.max_bad_samples} consecutive unusable readings on the "
                    "sample channel -- that is a sensor or a link, not a dwell")
            continue
        bad = 0

        if s.kelvin > opts.max_k:
            raise SweepAbort(f"the sample is at {s.kelvin:.2f} K, over the "
                             f"{opts.max_k:g} K ceiling set for this run")
        if (opts.max_coldplate_k is not None and s.coldplate_k is not None
                and s.coldplate_k > opts.max_coldplate_k):
            raise SweepAbort(f"the coldplate is at {s.coldplate_k:.2f} K, over the "
                             f"{opts.max_coldplate_k:g} K ceiling")

        if t0 is None:
            t0, started_iso = s.t_s, s.iso
        samples.append((s.t_s, s.kelvin))
        if s.coldplate_k is not None:
            coldplate = s.coldplate_k
        if s.u_pct is not None:
            readback = s.u_pct
        ended_iso = s.iso
        elapsed = s.t_s - t0

        # The readback is only checked once the write has had time to land.
        if (len(samples) > opts.settle_samples and s.u_pct is not None
                and abs(s.u_pct - tread.u_pct) > U_TOL_PCT):
            raise SweepAbort(
                f"the heater reads {s.u_pct:.3f}% where {tread.u_pct:.3f}% was "
                "commanded -- something else is driving this output")

        if on_sample is not None:
            on_sample(s, elapsed, fit)

        if elapsed >= opts.min_dwell_s and len(samples) >= 8:
            if fit is None or len(samples) % opts.fit_every == 0:
                try:
                    fit = fit_pole(samples)
                except ValueError:
                    fit = None
            if fit is not None and fit.grade(
                    min_reach=opts.min_reach, max_settle_k=opts.max_settle_k,
                    max_end_rate=opts.max_end_rate):
                break
        if elapsed >= cap:
            note = f"cut at the {cap:.0f} s cap"
            try:
                fit = fit_pole(samples)
            except ValueError:
                fit = None
            break

    grade = "" if fit is None else fit.grade(
        min_reach=opts.min_reach, max_settle_k=opts.max_settle_k,
        max_end_rate=opts.max_end_rate)
    if fit is not None and grade != "tau":
        why = fit.shortfall(min_reach=opts.min_reach,
                            max_settle_k=opts.max_settle_k,
                            max_end_rate=opts.max_end_rate)
        note = f"{note}; {why}" if note and why else (why or note)
    return DwellResult(
        tread=tread, u_readback_pct=readback,
        started_iso=started_iso, ended_iso=ended_iso,
        span_s=samples[-1][0] - samples[0][0], n=len(samples),
        t_start_k=samples[0][1], t_end_k=samples[-1][1],
        coldplate_k=coldplate, fit=fit, grade=grade, note=note)


def run(link, treads: list[Tread], opts: Options, journal: Journal, *,
        echo=print) -> list[DwellResult]:
    """The whole ladder.  Returns the dwells that completed, aborted or not."""
    done: list[DwellResult] = []
    echo(f"{'#':>3} {'u %':>8} {'T pred':>7} {'T_inf':>9} {'settle':>7} "
         f"{'tau s':>7} {'reach':>6} {'span s':>7}  grade")
    for n, tread in enumerate(treads, 1):
        d = dwell(link, tread, opts)
        done.append(d)
        journal.write(d)
        f = d.fit

        def q(value, spec):
            return "" if value is None else format(value, spec)

        echo(f"{n:>3} {tread.u_pct:>8.3f} {q(tread.t_pred_k, '7.1f'):>7} "
             f"{q(None if f is None else f.t_inf, '9.3f'):>9} "
             f"{q(None if f is None else f.settle_k, '7.3f'):>7} "
             f"{q(None if f is None else f.tau_s, '7.0f'):>7} "
             f"{q(None if f is None else f.reach, '6.1f'):>6} "
             f"{d.span_s:>7.0f}  {d.grade or 'DROPPED'}"
             + (f"  ({d.note})" if d.note else ""))
    return done


# -- command line ----------------------------------------------------------

def print_plan(treads: list[Tread], opts: Options, echo=print) -> float:
    """The ladder and what it will cost.  Returns the worst-case seconds."""
    echo(f"{'#':>3} {'u %':>8} {'T pred':>8} {'dT':>7} {'tau s':>8} "
         f"{'dwell s':>8} {'cum':>8}")
    cum = 0.0
    prev = None
    for n, t in enumerate(treads, 1):
        cap = dwell_cap(t, opts)
        cum += cap
        d_t = ("" if (t.t_pred_k is None or prev is None)
               else format(t.t_pred_k - prev, ".2f"))
        echo(f"{n:>3} {t.u_pct:>8.3f} "
             f"{'' if t.t_pred_k is None else format(t.t_pred_k, '8.2f'):>8} "
             f"{d_t:>7} "
             f"{'' if t.tau_pred_s is None else format(t.tau_pred_s, '8.0f'):>8} "
             f"{cap:>8.0f} {cum / 3600.0:>7.2f}h")
        prev = t.t_pred_k
    echo(f"\n{len(treads)} rungs, {cum / 3600.0:.2f} h worst case. A dwell ends "
         "as soon as it grades, so the run is usually shorter than this.")
    return cum


def default_journal_path(status: dict) -> str:
    """Beside the recorder's own log, which is where the full-rate data is."""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = ((status.get("recorder") or {}).get("path") or "")
    directory = os.path.dirname(path) if path else "."
    return os.path.join(directory or ".", f"sweep-{stamp}.csv")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="step the heater up a ladder of outputs and dwell at each rung",
        epilog="The recorder's own CSV is the dataset. This writes the journal: "
               "which rung was held when, and whether it settled.")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--plan", help="ladder CSV, from analysis/plan_sweep.py")
    ap.add_argument("--percents", help="ladder as bare percents, comma separated")
    ap.add_argument("--order", choices=("up", "down", "nearest"), default="up",
                    help="which end to start from (default: up, from the coldest)")
    ap.add_argument("--plan-only", action="store_true",
                    help="print the ladder and the ETA, and touch nothing")
    ap.add_argument("--simulate", action="store_true",
                    help="rehearse against the simulator on a virtual clock")
    ap.add_argument("--journal", help="where to write the dwell journal")
    ap.add_argument("--channel", default="Sample")
    ap.add_argument("--coldplate", default="Coldplate")
    ap.add_argument("--heater-aux", default=None,
                    help="status aux entry carrying the output readback "
                         "(default: the first `*.aoutN` there is)")
    ap.add_argument("--min-dwell", type=float, default=120.0,
                    help="seconds before a rung may be called settled")
    ap.add_argument("--max-dwell", type=float, default=None,
                    help="seconds after which a rung is abandoned and recorded "
                         "anyway (default 2400; a rehearsal sizes it from the "
                         "simulator's own tau instead)")
    ap.add_argument("--min-reach", type=float, default=MIN_REACH,
                    help="time constants a dwell must run before tau is believed")
    ap.add_argument("--max-settle-k", type=float, default=MAX_SETTLE_K)
    ap.add_argument("--max-end-rate", type=float, default=MAX_END_RATE_K_PER_H,
                    help="K/h the sample may still be moving at the end")
    ap.add_argument("--max-k", type=float, default=120.0,
                    help="stop the sweep if the sample goes above this")
    ap.add_argument("--max-coldplate-k", type=float, default=None)
    ap.add_argument("--stale-grace", type=float, default=120.0,
                    help="seconds the recorder may go quiet mid-dwell before "
                         "the run stops. The heater is not moving while we "
                         "wait, and a GPIB retry outlasts the staleness bar")
    ap.add_argument("--on-abort", choices=("hold", "off"), default="hold",
                    help="hold leaves the output where it is (the default, and "
                         "invariant 6); off sends the heaters_off panic, which "
                         "cuts EVERY writable heater on the recorder -- on "
                         "LTSPM3 that includes the 336's, holding THE CHONKE")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation before the first write")
    args = ap.parse_args(argv)

    if bool(args.plan) == bool(args.percents):
        print("give exactly one of --plan or --percents", file=sys.stderr)
        return 2

    opts = Options(
        min_dwell_s=args.min_dwell, max_dwell_s=args.max_dwell or 2400.0,
        min_reach=args.min_reach, max_settle_k=args.max_settle_k,
        max_end_rate=args.max_end_rate, max_k=args.max_k,
        max_coldplate_k=args.max_coldplate_k,
    )
    try:
        treads = (read_plan(args.plan) if args.plan
                  else plan_from_percents(args.percents))
    except (OSError, ValueError, SweepAbort) as exc:
        print(f"cannot read the plan: {exc}", file=sys.stderr)
        return 1

    if args.plan_only:
        print_plan(order_plan(treads, args.order, None), opts)
        return 0

    if args.simulate:
        link = SimLink(dt=2.0, start_pct=min(t.u_pct for t in treads),
                       channel=args.channel, coldplate=args.coldplate or None)
        journal_path = args.journal
        treads = without_model_dwells(treads)
        if args.max_dwell is None:
            # Six time constants.  The end-rate bar is the strictest of the
            # three and needs about four and a half of them; sizing the cap
            # from the fitted model instead would cut every cold rung one fifth
            # of the way through a relaxation the simulator does not share.
            opts.max_dwell_s = 6.0 * link.tau_fast_s
        print("rehearsal: the plan's per-rung caps are dropped. They come from "
              "tau(T) off\nthe fitted model, and the simulator has one flat tau "
              f"of {link.tau_fast_s:.0f} s at every\ntemperature -- so a cap of "
              f"{opts.max_dwell_s:.0f} s applies throughout instead. This checks "
              "the\nprocedure; the timing is fiction.\n")
    else:
        from lschart import config as config_mod

        import ltspm3.config  # noqa: F401 -- registers the `control:` section

        try:
            cfg = config_mod.load(args.config)
        except Exception as exc:                     # noqa: BLE001 -- reported
            print(f"cannot load {args.config}: {exc}", file=sys.stderr)
            return 1
        link = RecorderLink(cfg, channel=args.channel,
                            coldplate=args.coldplate or None,
                            heater_aux=args.heater_aux,
                            stale_grace_s=args.stale_grace,
                            source=f"ltspm3-sweep/{os.getpid()}")
        journal_path = args.journal

    try:
        status, current = link.preflight(treads)
    except SweepAbort as exc:
        print(f"will not start: {exc}", file=sys.stderr)
        return 1
    if journal_path is None:
        journal_path = default_journal_path(status)

    treads = order_plan(treads, args.order, current)
    print_plan(treads, opts)
    print(f"\ndriving {link.describe()}")
    print(f"the heater is at "
          f"{'unknown' if current is None else format(current, '.3f') + '%'}"
          f", the first rung is {treads[0].u_pct:.3f}%")
    print(f"journal: {journal_path}")

    if not args.simulate and not args.yes:
        print("\nThis moves the heater on the cryostat. Type yes to start: ", end="")
        try:
            if input().strip().lower() not in ("yes", "y"):
                print("nothing sent.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\nnothing sent.")
            return 1

    started = time.monotonic()
    code = 0
    with Journal(journal_path) as journal:
        try:
            run(link, treads, opts, journal)
        except SweepAbort as exc:
            print(f"\nSWEEP STOPPED: {exc}", file=sys.stderr)
            code = 1
        except KeyboardInterrupt:
            print("\nSWEEP STOPPED: interrupted", file=sys.stderr)
            code = 1
        if code:
            if args.on_abort == "off":
                print("sending heaters_off, as --on-abort asked", file=sys.stderr)
                print(link.set_output_panic(), file=sys.stderr)
            else:
                print("the output is left where it is -- nothing was cut. "
                      "`lschart status` will say where that is.", file=sys.stderr)
        graded = sum(1 for r in journal.rows if r["grade"])
        print(f"\n{graded}/{len(journal.rows)} dwells graded, "
              f"{(time.monotonic() - started) / 3600.0:.2f} h elapsed; "
              f"journal in {journal.path}")
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
