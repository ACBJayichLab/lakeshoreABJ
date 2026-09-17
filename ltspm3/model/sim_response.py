"""The LTSPM3 thermal model behind the simulator.

Calibrated to ``reference/logs/``; the generic fakes it plugs into live in
:mod:`lschart.instruments.sim`, which knows nothing about this cryostat.  The model
is *not* an attempt at cryostat physics; it reproduces the four things the
control software actually has to cope with:

1. a steeply nonlinear steady state.  The curve, the exponents and where they
   were measured are in :mod:`ltspm3.model.thermal_response`, which is their
   one home; this module uses them and restates none of them;
2. two very different time constants.  ``tau_fast`` here is ONE number from one
   clean step, right where it was measured and wrong elsewhere;
   :mod:`ltspm3.model.fitted_response` is the one that varies with T;
3. temperature-dependent sensor noise, quadratic in T
   (:class:`ltspm3.model.fitted_response.FittedParams`, shared);
4. reporting quantisation.

Build a cryostat with :func:`ltspm3_cryostat`, which wires this response and the measured
cross-channel couplings into the generic :class:`~lschart.instruments.sim.SimulatedCryostat`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from lschart.instruments.sim import SimulatedCryostat

from .thermal_response import MEASURED_CURVE, SteadyStateCurve

@dataclass
class ResponseParams:
    """Calibrated against the 2026-07 cooldown; see module docstring."""

    t_bath: float = 4.0
    #: The analog output is a VOLTAGE into a fixed heater, so power goes as
    #: pct**2 exactly.  The remaining nonlinearity is thermal -- changing heat
    #: capacity and conductance -- and is carried here:
    #:
    #:     P  ~ pct**2                          (exact, temperature-independent)
    #:     dT ~ P**thermal_exponent
    #:
    #: The fit, its range and its provenance are in
    #: :mod:`ltspm3.model.thermal_response`.
    thermal_exponent: float = 3.158
    ref_pct: float = 63.076        # the operating point the cryostat actually sat at
    ref_rise: float = 95.6         # T_ss - T_bath there: 99.60 K absolute
    #: Measured steady-state points, shared with the feedforward via
    #: ltspm3.model.thermal_response so the two cannot silently disagree.  Set to () to fall
    #: back to the pure power law -- which is how model mismatch between the
    #: response and the controller's idea of it gets injected in tests.
    calibration: tuple = MEASURED_CURVE
    tau_fast: float = 620.0        # s   ONE measured step, right where it was
                                   #     measured and wrong elsewhere; see
                                   #     fitted_response for tau(T)
    tau_slow: float = 14400.0      # s   (~4 h)
    fast_fraction: float = 0.90    # of the step lands on the fast pole
    #: Sensor noise, *quadratic* in T rather than linear.  The same two numbers
    #: as :class:`ltspm3.model.fitted_response.FittedParams`, which is their
    #: home -- the thermometer did not change when the model did.  It matters
    #: for the sweep requirement: at room temperature the measurement floor
    #: alone puts millikelvin stability out of reach however good the control.
    noise_floor_k: float = 0.0018  # rms at low T
    noise_quadratic: float = 1.36e-6   # rms = this * T**2
    quantum_k: float = 0.001       # reported resolution

    @property
    def exponent(self) -> float:
        """Lumped exponent of dT vs percent: twice the thermal exponent."""
        return 2.0 * self.thermal_exponent

    @property
    def coeff(self) -> float:
        return self.ref_rise / (self.ref_pct**self.exponent)

    @property
    def curve(self) -> SteadyStateCurve:
        return SteadyStateCurve(
            self.calibration, t_bath_k=self.t_bath,
            thermal_exponent=self.thermal_exponent,
            ref_pct=self.ref_pct, ref_rise_k=self.ref_rise,
        )

    def relative_power(self, pct: float) -> float:
        """Power as a fraction of power at ref_pct.  Exact: V**2 / R."""
        return (max(pct, 0.0) / self.ref_pct) ** 2

    def steady_state(self, pct: float) -> float:
        return self.curve.kelvin_for(pct)

    def local_gain(self, pct: float) -> float:
        """dT/d(pct) in K/% -- the number that makes this cryostat hard to control."""
        return self.curve.gain_at(pct)


class ThermalModel:
    """Two-pole lag onto a nonlinear steady state, integrated with exact
    exponential updates so the step size never affects stability."""

    def __init__(self, params: ResponseParams | None = None, *, start_k: float | None = None):
        self.p = params or ResponseParams()
        start = self.p.t_bath if start_k is None else start_k
        self._fast = start - self.p.t_bath
        self._slow = start - self.p.t_bath
        self.pct = 0.0

    @property
    def temperature(self) -> float:
        p = self.p
        return p.t_bath + p.fast_fraction * self._fast + (1 - p.fast_fraction) * self._slow

    def advance(self, dt: float) -> None:
        if dt <= 0:
            return
        target = self.p.steady_state(self.pct) - self.p.t_bath
        a_f = 1.0 - math.exp(-dt / self.p.tau_fast)
        a_s = 1.0 - math.exp(-dt / self.p.tau_slow)
        self._fast += a_f * (target - self._fast)
        self._slow += a_s * (target - self._slow)

    def observe(self, rng: random.Random) -> float:
        t = self.temperature
        sigma = max(self.p.noise_floor_k, self.p.noise_quadratic * t * t)
        noisy = t + rng.gauss(0.0, sigma)
        q = self.p.quantum_k
        return round(noisy / q) * q if q > 0 else noisy


#: Measured on ``cd8_..._sample_monitor7.xls``, where the sample fell 22.4 K
#: while input 2 fell 0.183 K and input 3 fell 0.051 K.  Small, but hundreds of
#: times those channels' own noise -- which is exactly what makes a real
#: transient distinguishable from a one-channel glitch.  Without this coupling
#: the simulator can never corroborate anything and the coherence logic is
#: untestable.
#:
#: Keyed by the input each thermometer is WIRED TO, not by what it is called.
#: The magnet's 0.0023 was measured while it sat on input 3; it moved to input 5
#: on 2026-09-04 and the coupling moved with it, because it is a property of the
#: thermometer's place in the cryostat and not of the socket.
LTSPM3_AUX_COUPLING = {
    "218.2": 0.0082, "218.5": 0.0023,
    "336.A": 0.0040, "336.B": 0.0002, "336.C": 0.0015, "336.D": 0.0008,
}


def ltspm3_cryostat(params: ResponseParams | None = None, *, start_k: float = 96.0, **kw):
    """A :class:`SimulatedCryostat` running the calibrated LTSPM3 thermal response."""
    return SimulatedCryostat(
        response=ThermalModel(params, start_k=start_k),
        aux_coupling=LTSPM3_AUX_COUPLING,
        **kw,
    )
