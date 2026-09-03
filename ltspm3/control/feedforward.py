"""Open-loop estimate of the heater setting a temperature needs.

The PID here is deliberately gentle -- ``kp`` of 0.02 %/K against a response of
several K/% is a loop gain near 0.2, chosen so that a bad reading cannot
produce a violent correction.  That is the right trade for holding a
temperature, but it means feedback alone takes an integral time (900 s) to
build the output change a new setpoint needs.  A sweep asked the loop to
follow, it fell 3 K behind, and by the time the ramp finished the loop was
still crawling.  Feedforward fixes that without touching the gains.

The model, in two stages
------------------------

The 218's analog output is a **voltage**, driving a stable 75.5 ohm heater, so::

    P = V**2 / R        i.e.   P is proportional to pct**2

That half is exact, temperature-independent, and not up for negotiation.  All
the awkward nonlinearity lives in the *thermal* relation between power and
temperature, where the heat capacity and the conductance to the cold stage
both change with T::

    T - T_bath = A * P**m           with m ~= 3.16 measured

Keeping these separate matters.  Lumping them into a single ``T ~ pct**n`` fit
(the previous model, with n = 5) hides the fact that only one of the two
factors is uncertain, and it invites re-fitting the exponent to absorb errors
that actually belong to the fixed quadratic.

Measured, not assumed
---------------------

``reference/logs/CD10/*_monitor4,5.xls`` contain ~200 ``ANALOG`` commands in
their Notes column.  Twenty-four of those settled long enough to read a steady
state, giving a regression over 64.3-68.5% / 116-171 K::

    lumped   n = 6.32   (dT ~ pct**n)      R^2 = 0.9962
    thermal  m = 3.16   (dT ~ P**m)

That is much steeper than the n = 5.0 previously fitted from two points, and it
is closer to Jeff's recollection that "65-ish is around 150 K" -- the logs put
151.05 K at 66.95%.

**No single exponent covers the whole range**, which is exactly what changing
conductances imply: extrapolating m = 3.16 down to 43% predicts 12.8 K where
18.2 K was measured.  So a calibration table is used where real points exist
and the power law only extrapolates beyond them.

Accuracy is not critical either way -- any residual is absorbed by the
integral.  What feedforward buys is the *shape*, so the output moves when the
setpoint moves instead of lagging it by a thermal time constant.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..thermal_response import (
    MEASURED_CURVE,
    REF_PCT,
    REF_RISE_K,
    T_BATH_K,
    THERMAL_EXPONENT,
    SteadyStateCurve,
    fit_thermal_exponent,
)

__all__ = ["Feedforward", "FeedforwardConfig", "MEASURED_CURVE", "fit_thermal_exponent"]


@dataclass
class FeedforwardConfig:
    """The measured steady-state curve.  See :mod:`ltspm3.thermal_response`."""

    enabled: bool = True
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


class Feedforward:
    """Steady-state output for a temperature, and the local gain there."""

    def __init__(self, config: FeedforwardConfig | None = None) -> None:
        self.cfg = config or FeedforwardConfig()
        self.curve = SteadyStateCurve(
            self.cfg.calibration,
            t_bath_k=self.cfg.t_bath_k,
            thermal_exponent=self.cfg.thermal_exponent,
            ref_pct=self.cfg.ref_pct,
            ref_rise_k=self.cfg.ref_rise_k,
        )

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

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
