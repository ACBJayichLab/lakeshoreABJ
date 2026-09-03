"""What the cryostat did, in one regime, as measured.  Shared by the simulator and
the feedforward so neither can drift away from the other by accident.

This module holds empirical knowledge, not policy.  It is deliberately free of
control logic and imports nothing from ``control/`` or ``instruments/``.

READ THIS BEFORE TRUSTING THE CURVE
-----------------------------------

``MEASURED_CURVE`` is **not a property of the system**.  It is a property of
the system *in one operating regime*: cryocooler running, shields cold, vacuum
as it was during CD10.  The percent-to-temperature relation depends on the
cooling power available to fight the heater, and that changes completely when:

* the **cooler is off** -- before a cooldown, after a warmup, or during a
  service window.  With no cooling power the same percent runs far hotter, and
  the local gain is much higher;
* the cryostat is **warming or cooling** rather than settled;
* vacuum, shield temperature, or base load differ between cooldowns.  The 43%
  point below is from a different cooldown and already disagrees with an
  extrapolation of the rest.

**A temperature log cannot distinguish those regimes.**  Nothing in the .xls
files records cooler state or vacuum, so any fit taken from them silently
assumes whatever was true at the time.  That is a limitation of the data, not
something a better fit can repair.

What follows from that, and is relied on elsewhere:

1. Feedforward enters the PID as a **difference** of ``percent_for()`` between
   the present setpoint and the setpoint at priming (``PID._ff``).  So what
   reaches the output is the curve's local **slope over the interval actually
   traversed** -- effectively ``dT / gain`` -- and not where the curve sits
   absolutely.  An error that is roughly flat across those few kelvin largely
   cancels; a wrong *gain* does not.  A cooler-off regime changes the gain, so
   this is a weak dependence, not no dependence, and it is why the cap in (2)
   exists rather than being belt-and-braces.
2. That slope is bounded by ``SupervisorConfig.max_feedforward_pct``, so a
   wrong regime cannot run the output away before the integral corrects it.
3. ``HeaterSupervisor._check_model`` compares the settled measurement against
   ``kelvin_for(output)`` and alarms when they disagree, rather than assuming
   the calibration still applies.
4. The **dynamics generalise better than the steady state**.  Response times
   come from the same logs and depend on heat capacity and conductance rather
   than on the cooling balance, so ``MEASURED_TAU_S`` travels further across
   regimes than ``MEASURED_CURVE`` does -- though it is not regime-free either.

Setting ``calibration=()`` disables the table entirely and falls back to the
pure power law; setting ``FeedforwardConfig.enabled = False`` disables
feedforward altogether and leaves a pure feedback loop, which is the right
choice in any regime this curve was not measured in.

The actuator half is exact
--------------------------

The 218's analog output is a **voltage**, and it drives a stable 75.5 ohm
heater, so::

    P = V**2 / R      ->      P is proportional to pct**2

There is nothing to fit there and nothing that varies with temperature.

The thermal half is not
-----------------------

Everything awkward lives in ``T(P)``, where the heat capacity and the
conductance to the cold stage both change with temperature::

    T - T_bath = A * P**m

with ``m ~= 3.16`` measured.  Crucially **m is not constant over the range**:
the lumped exponent in a T-vs-percent plot runs from about 5 near 43% to about
7.8 near 64%.  Extrapolating the high-temperature fit down to 43% predicts
12.8 K where 18.2 K was measured.  So the measured points are interpolated
where they exist, and the power law only extrapolates beyond them.

Provenance
----------

24 settled ``ANALOG`` commands in ``reference/logs/CD10/*_monitor4,5.xls``
(~200 commands total, of which 24 dwelt long enough to read a steady state)
give, over 64.3-68.5% / 116-171 K::

    lumped   n = 6.32   R^2 = 0.9962
    thermal  m = 3.16

That supersedes an earlier n = 5.0 fitted from two points, which was too
shallow -- it predicted 65% -> 116 K where the logs show 66.95% -> 151.05 K.
"""

from __future__ import annotations

import math

#: Settled ``(percent, kelvin)`` points, **cooler running, CD10**.
#: See the module docstring: this is one regime, not the system.
#:
#: 63.076% -> 99.60 K is the pre-command steady state at the head of monitor4;
#: the rest are settled dwells from the monitor4/5 series, which is one
#: continuous period and therefore internally consistent.
#:
#: The 43% point is from an earlier, colder regime -- base load varies between
#: cooldowns -- so it anchors the low end only loosely.  It is kept because a
#: sweep needs some shape down there, and the local slope it implies (n ~ 5) is
#: physically sensible: the exponent falls with temperature.
MEASURED_CURVE: tuple[tuple[float, float], ...] = (
    (43.000, 18.200),
    (63.076, 99.600),
    (64.337, 116.509),
    (64.975, 124.913),
    (65.340, 129.734),
    (65.900, 137.334),
    (66.480, 144.935),
    (66.950, 151.053),
    (67.780, 161.785),
    (68.460, 170.682),
)

#: Bath temperature the rises are measured against.
T_BATH_K = 4.0

#: ``dT ~ P**THERMAL_EXPONENT``.  Half the lumped 6.32.
THERMAL_EXPONENT = 3.158

#: Reference operating point, and its rise above bath.
REF_PCT = 63.076
REF_RISE_K = 95.6

#: The one clean step response in the logs: 65.9% -> 137.3 K settles with
#: tau ~= 620 s.  The previously assumed 360 s was a guess.  Every other
#: command in those logs is a sub-2 K trim, so this is a single measurement at
#: a single temperature -- and heat capacity varies, so tau certainly does too.
#: Treat it as a lower bound on the uncertainty, not as a calibration.
MEASURED_TAU_S = 620.0
MEASURED_TAU_AT_K = 137.3


class SteadyStateCurve:
    """Percent <-> kelvin, by log-log interpolation of measured points.

    Log-log piecewise-linear is a locally-varying power law, which is the right
    shape when the exponent itself drifts with temperature.  Outside the
    measured range it extrapolates on the end segments' slopes.
    """

    def __init__(
        self,
        points=MEASURED_CURVE,
        *,
        t_bath_k: float = T_BATH_K,
        thermal_exponent: float = THERMAL_EXPONENT,
        ref_pct: float = REF_PCT,
        ref_rise_k: float = REF_RISE_K,
    ) -> None:
        self.t_bath_k = t_bath_k
        self.thermal_exponent = thermal_exponent
        self.ref_pct = ref_pct
        self.ref_rise_k = ref_rise_k
        pts = []
        for pct, kelvin in points or ():
            rise = kelvin - t_bath_k
            if pct > 0 and rise > 0:
                pts.append((math.log(pct), math.log(rise)))
        pts.sort()
        self._fwd = pts
        self._inv = sorted((y, x) for x, y in pts)

    @property
    def lumped_exponent(self) -> float:
        return 2.0 * self.thermal_exponent

    @property
    def calibrated(self) -> bool:
        return len(self._fwd) >= 2

    # -- the exactly-known half -------------------------------------------

    def relative_power(self, pct: float) -> float:
        """Power as a fraction of power at ``ref_pct``.  Exact: V**2 / R."""
        return (max(pct, 0.0) / self.ref_pct) ** 2

    def percent_for_relative_power(self, ratio: float) -> float:
        return self.ref_pct * math.sqrt(max(ratio, 0.0))

    # -- interpolation -----------------------------------------------------

    @staticmethod
    def _interp(x: float, pts: list[tuple[float, float]]) -> float:
        if x <= pts[0][0]:
            (x0, y0), (x1, y1) = pts[0], pts[1]
        elif x >= pts[-1][0]:
            (x0, y0), (x1, y1) = pts[-2], pts[-1]
        else:
            i = max(k for k in range(len(pts) - 1) if pts[k][0] <= x)
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        if x1 == x0:
            return y0
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    def kelvin_for(self, pct: float) -> float:
        if pct <= 0:
            return self.t_bath_k
        if self.calibrated:
            return self.t_bath_k + math.exp(self._interp(math.log(pct), self._fwd))
        return self.t_bath_k + self.ref_rise_k * (
            self.relative_power(pct) ** self.thermal_exponent
        )

    def percent_for(self, kelvin: float) -> float:
        rise = kelvin - self.t_bath_k
        if rise <= 0:
            return 0.0
        if self.calibrated:
            return math.exp(self._interp(math.log(rise), self._inv))
        ratio = (rise / self.ref_rise_k) ** (1.0 / self.thermal_exponent)
        return self.percent_for_relative_power(ratio)

    def gain_at(self, pct: float) -> float:
        """dT/d(pct) in K/% -- the number that makes this cryostat hard to control."""
        if pct <= 0:
            return 0.0
        h = max(pct * 1e-4, 1e-6)
        return (self.kelvin_for(pct + h) - self.kelvin_for(pct - h)) / (2 * h)

    def local_exponent(self, pct: float) -> float:
        """The lumped ``n`` in ``dT ~ pct**n`` right here -- not a constant."""
        if pct <= 0:
            return self.lumped_exponent
        h = max(pct * 1e-4, 1e-6)
        ra = self.kelvin_for(pct - h) - self.t_bath_k
        rb = self.kelvin_for(pct + h) - self.t_bath_k
        if ra <= 0 or rb <= 0:
            return self.lumped_exponent
        return (math.log(rb) - math.log(ra)) / (math.log(pct + h) - math.log(pct - h))


def fit_thermal_exponent(points, t_bath_k: float = T_BATH_K) -> tuple[float, float]:
    """Least-squares ``m`` in ``dT ~ P**m`` from settled ``(pct, kelvin)`` points.

    Returns ``(m, r_squared)``.  Provided so the exponent can be re-derived from
    a fresh dataset rather than staying a literal in a config file.
    """
    xs, ys = [], []
    for pct, kelvin in points:
        rise = kelvin - t_bath_k
        if pct > 0 and rise > 0:
            xs.append(math.log(pct))
            ys.append(math.log(rise))
    n = len(xs)
    if n < 2:
        raise ValueError("need at least two settled points above bath temperature")
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((a - mx) ** 2 for a in xs)
    if den <= 0:
        raise ValueError("all points are at the same output percent")
    slope = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / den
    res = sum((b - (my + slope * (a - mx))) ** 2 for a, b in zip(xs, ys))
    tot = sum((b - my) ** 2 for b in ys)
    return slope / 2.0, (1.0 - res / tot if tot > 0 else 1.0)
