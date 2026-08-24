"""Open-loop estimate of the heater setting a temperature needs.

The PID here is deliberately gentle -- ``kp`` of 0.02 %/K against a ~7.6 K/%
plant is a loop gain near 0.15, chosen so that a bad reading cannot produce a
violent correction.  That is the right trade for holding a temperature, but it
means feedback alone takes an integral time (900 s) to build the output change
a new setpoint needs.  A sweep asked the loop to follow, it fell 3 K behind, and
by the time the ramp finished the loop was still crawling.

Feedforward fixes that without touching the gains.  The steady-state curve is
known from the logs::

    T = T_bath + A * pct**n           43% -> 18.2 K,  63.076% -> ~100 K

so the output a setpoint needs can simply be computed, and the PID is left to
trim the residual -- which is what a gentle loop is good at.

The model does not need to be accurate.  Any error in ``exponent`` shows up as
a constant offset that the integral absorbs; what feedforward buys is the
*shape*, so the output moves at the same time as the setpoint instead of
lagging it by a plant time constant.  The exponent is fitted between two
points and HANDOFF flags it as unconfirmed (5.0 here, possibly ~5.6); that
uncertainty is exactly what the trim term is for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class FeedforwardConfig:
    """The measured steady-state curve.  See ``reference/logs``."""

    enabled: bool = True
    t_bath_k: float = 4.0
    #: The operating point the rig actually sat at, and its rise above bath.
    ref_pct: float = 63.076
    ref_rise_k: float = 96.0
    #: T - T_bath goes as pct**exponent.  Fitted 43% -> 18.2 K against
    #: 63.076% -> ~100 K.  Unconfirmed; see HANDOFF.
    exponent: float = 5.0
    #: Never propose anything outside this, whatever the maths says.  The
    #: supervisor's authority band clamps again on top of this.
    min_pct: float = 0.0
    max_pct: float = 70.0


class Feedforward:
    """Steady-state output for a temperature, and the local gain there."""

    def __init__(self, config: FeedforwardConfig | None = None) -> None:
        self.cfg = config or FeedforwardConfig()

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def percent_for(self, kelvin: float) -> float:
        """Heater percent whose steady state is ``kelvin``."""
        c = self.cfg
        rise = kelvin - c.t_bath_k
        if rise <= 0:
            return c.min_pct
        pct = c.ref_pct * (rise / c.ref_rise_k) ** (1.0 / c.exponent)
        return max(c.min_pct, min(c.max_pct, pct))

    def kelvin_for(self, pct: float) -> float:
        """Inverse -- the steady state a percent settles at."""
        c = self.cfg
        if pct <= 0:
            return c.t_bath_k
        return c.t_bath_k + c.ref_rise_k * (pct / c.ref_pct) ** c.exponent

    def gain_at(self, pct: float) -> float:
        """dT/d(pct) in K/% -- the number that makes this rig hard to control."""
        c = self.cfg
        if pct <= 0:
            return 0.0
        return c.exponent * c.ref_rise_k * pct ** (c.exponent - 1) / (c.ref_pct**c.exponent)

    def gain_at_kelvin(self, kelvin: float) -> float:
        return self.gain_at(self.percent_for(kelvin))

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        c = self.cfg
        return (
            f"Feedforward(T={c.t_bath_k}+{c.ref_rise_k}*(pct/{c.ref_pct})^{c.exponent}, "
            f"gain@{c.ref_pct}%={self.gain_at(c.ref_pct):.2f} K/%)"
        )


def fit_exponent(pct_a: float, k_a: float, pct_b: float, k_b: float,
                 t_bath_k: float = 4.0) -> float:
    """Fit ``n`` from two measured steady states.

    Provided so the exponent can be re-derived the moment a heated
    steady-state dataset exists, rather than staying a literal in a config file.
    """
    ra, rb = k_a - t_bath_k, k_b - t_bath_k
    if ra <= 0 or rb <= 0 or pct_a <= 0 or pct_b <= 0 or pct_a == pct_b:
        raise ValueError("need two distinct positive operating points above bath")
    return math.log(rb / ra) / math.log(pct_b / pct_a)
