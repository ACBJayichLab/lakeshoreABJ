"""Open-loop estimate of the heater setting a temperature needs.

**Why this still matters with scheduled gains.**  The gains are no longer a
fixed pair (:mod:`ltspm3.control.tuning`), so the loop is as fast as the plant
allows -- but feedback still has to BUILD the output a new setpoint needs, an
integral time at a time, and the integral time is the plant's own tau.  A
model that already knows what output holds a temperature supplies that level
outright, so the loop only has to correct it.  What feedforward buys is the
*shape*: the output moves when the setpoint moves instead of lagging it by a
thermal time constant.

The model, in two stages
------------------------

The 218's analog output is a **voltage**, driving a stable 75.5 ohm heater, so::

    P = V**2 / R        i.e.   P is proportional to pct**2

That half is exact, temperature-independent, and not up for negotiation.  All
the awkward nonlinearity lives in the *thermal* relation between power and
temperature, where the heat capacity and the conductance to the cold stage
both change with T::

    T - T_bath = A * P**m

Keeping these separate matters.  Lumping them into a single ``T ~ pct**n`` fit
hides the fact that only one of the two factors is uncertain, and it invites
re-fitting the exponent to absorb errors that actually belong to the fixed
quadratic.  The exponents, the range they were fitted over and their
provenance are in :mod:`ltspm3.model.thermal_response`, which is their home.

**No single exponent covers the whole range**, which is exactly what changing
conductances imply.  So a calibration table is used where real points exist and
the power law only extrapolates beyond them.  Accuracy is not critical either
way -- any residual is absorbed by the integral.

Two curves
----------

``source: cd10`` is the legacy steady-state curve, kept for reading the
pre-refit record.  **The default is ``source: fitted``** --
:mod:`ltspm3.model.fitted_response`, the ODE fitted to the 43 h sweep.

They do not agree, and below the narrow band the legacy points actually span
they do not nearly agree -- by tens of kelvin, and by as much again in the
local gain, which is the number the loop tunes itself with.  The fit is not an
improvement on the old curve down there; it is the only measurement there has
ever been.  REFIT_PLAN.md 7.3 has the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import fitted_response as _M
from ..model.thermal_response import (
    MEASURED_CURVE,
    REF_PCT,
    REF_RISE_K,
    T_BATH_K,
    THERMAL_EXPONENT,
    SteadyStateCurve,
    fit_thermal_exponent,
)

__all__ = ["Feedforward", "FeedforwardConfig", "FittedCurve", "MEASURED_CURVE",
           "fit_thermal_exponent"]


@dataclass
class FeedforwardConfig:
    """Which steady-state curve, and the CD10 one's parameters."""

    enabled: bool = True

    #: ``fitted`` (the shipped ODE fit, :mod:`ltspm3.model.fitted_response`) or
    #: ``cd10`` (the 2026 steady-state curve this module was built on).  The
    #: fields below configure ``cd10`` only; ``fitted`` has no free parameters
    #: at all, which is the point of it -- it is a table with a cache key, and
    #: a loop tuned against a curve nobody can trace is how the 4-5 K got in.
    source: str = "fitted"

    t_bath_k: float = T_BATH_K

    #: Anchor for the pure power-law form, used outside the calibration table.
    ref_pct: float = REF_PCT
    ref_rise_k: float = REF_RISE_K

    #: dT is proportional to P**thermal_exponent, and P to pct**2, so the
    #: lumped exponent seen in a T-vs-percent plot is twice this.
    thermal_exponent: float = THERMAL_EXPONENT

    #: (percent, kelvin) steady-state points.  Empty falls back to the pure
    #: power law -- which is also how model mismatch is injected in tests.
    calibration: tuple[tuple[float, float], ...] = MEASURED_CURVE

    #: Never propose anything outside this, whatever the maths says.  The
    #: supervisor's authority band clamps again on top of this.
    min_pct: float = 0.0
    max_pct: float = 70.0

    @property
    def lumped_exponent(self) -> float:
        return 2.0 * self.thermal_exponent


class FittedCurve:
    """The shipped fit, behind the four methods :class:`Feedforward` asks for.

    An adapter rather than a rewrite, so the two curves are interchangeable and
    a test can put either one under the same loop -- which is how the "model
    wrong on purpose" row of §3.7 works.

    Nothing here extrapolates.  ``fitted_response``'s table clamps at its ends
    (4.7-195 K, 0.72-69.95 %) because past them the curves are flat and the
    physics is fiction, and a feedforward that invents an output for 300 K is
    exactly the thing PID_PLAN's "nothing above 180.6 K is measured" trap warns
    about.  Clamped, the worst it can do is propose too little heat, which the
    integral then supplies slowly.
    """

    def __init__(self, ref_pct: float = REF_PCT) -> None:
        self.ref_pct = ref_pct

    def relative_power(self, pct: float) -> float:
        """Heater power as a fraction of power at ``ref_pct``.  Exact -- the
        218's output is a voltage into a stable resistance, so P ~ pct**2 and
        this half of the model is not fitted at all."""
        ref = _M.power_w(self.ref_pct)
        return _M.power_w(pct) / ref if ref > 0 else 0.0

    def kelvin_for(self, pct: float) -> float:
        return _M.steady_temperature_k(_M.power_w(pct))

    def percent_for(self, kelvin: float) -> float:
        return _M.percent_for_power(_M.steady_power_w(kelvin))

    def gain_at(self, pct: float) -> float:
        """``dT/d(pct)`` in K/%, central difference on the table."""
        h = max(pct * 1e-3, 1e-3)
        lo = max(pct - h, 0.0)
        return (self.kelvin_for(pct + h) - self.kelvin_for(lo)) / ((pct + h) - lo)

    def local_exponent(self, pct: float) -> float:
        """The lumped ``n`` in ``dT ~ pct**n`` right here.  Not a constant, and
        on this cryostat not even nearly one: it is the slope of log dT against
        log pct, and it runs from about 20 at the cold end to 4 at the top."""
        t_bath = _M.T_MIN_K
        rise = self.kelvin_for(pct) - t_bath
        if rise <= 0 or pct <= 0:
            return 0.0
        return self.gain_at(pct) * pct / rise


class Feedforward:
    """Steady-state output for a temperature, and the local gain there."""

    def __init__(self, config: FeedforwardConfig | None = None) -> None:
        self.cfg = config or FeedforwardConfig()
        if self.cfg.source == "fitted":
            self.curve = FittedCurve(self.cfg.ref_pct)
        elif self.cfg.source == "cd10":
            self.curve = SteadyStateCurve(
                self.cfg.calibration,
                t_bath_k=self.cfg.t_bath_k,
                thermal_exponent=self.cfg.thermal_exponent,
                ref_pct=self.cfg.ref_pct,
                ref_rise_k=self.cfg.ref_rise_k,
            )
        else:
            raise ValueError(
                f"feedforward.source must be 'fitted' or 'cd10', got "
                f"{self.cfg.source!r}"
            )

    @property
    def enabled(self) -> bool:
        """Whether the loop may use the model's LEVEL as a control term.

        A commissioning decision, and it stays off until a power gauge exists:
        the level is a calibration with a shelf life and the loop must not
        drive to one it cannot check.  See :attr:`has_curve` for the question
        this is repeatedly mistaken for.
        """
        return self.cfg.enabled

    @property
    def has_curve(self) -> bool:
        """Whether there is a steady-state curve to ASK, enabled or not.

        **A different question from :attr:`enabled`** (AUDIT-2026-09-16
        findings 2 and 3).  `enabled` answers "should the loop trust the
        model's level"; this answers "is there a curve to convert kelvin into
        percent with".  The fault ramp-down and the output rate limiter need
        the second, and asking the first leaves both crawling at their floor
        rate whenever a stage has the level switched off -- hours instead of
        the descent `HeaterSupervisor._rampdown_target` describes.

        The shape of a curve does not expire when its level does: a descent at
        the one rate walked on a curve whose level is a kelvin or two off is
        still a descent at about the one rate, and it is bounded at every write
        by `_rampdown_step_pct` besides.

        False only for a cryostat with no fitted response at all, which is what
        the fallback branches exist for.
        """
        return self.curve is not None

    def relative_power(self, pct: float) -> float:
        """Heater power as a fraction of power at ``ref_pct``.  Exact."""
        return self.curve.relative_power(pct)

    def kelvin_for(self, pct: float) -> float:
        """The steady state a percent settles at."""
        return self.curve.kelvin_for(pct)

    def percent_for(self, kelvin: float) -> float:
        """Heater percent whose steady state is ``kelvin``."""
        return max(self.cfg.min_pct, min(self.cfg.max_pct, self.curve.percent_for(kelvin)))

    def gain_at(self, pct: float) -> float:
        """dT/d(pct) in K/%."""
        return self.curve.gain_at(pct)

    def gain_at_kelvin(self, kelvin: float) -> float:
        return self.gain_at(self.percent_for(kelvin))

    def local_exponent(self, pct: float) -> float:
        """The lumped ``n`` in ``dT ~ pct**n`` right here -- not a constant."""
        return self.curve.local_exponent(pct)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        c = self.cfg
        return (
            f"Feedforward(P~pct^2, dT~P^{c.thermal_exponent:.2f}, "
            f"{len(c.calibration or ())} calibration points, "
            f"gain@{c.ref_pct}%={self.gain_at(c.ref_pct):.2f} K/%)"
        )


fit_exponent = fit_thermal_exponent   # backwards-compatible alias
