"""Noise reduction for a millikelvin-rms measurement on a jittering cadence.

The recorder polls on a nominal cycle and the real interval wanders around it,
so **every filter here is dt-aware**: it takes the actual ``dt`` and uses the
exact exponential coefficient rather than a fixed alpha.  That keeps the
effective time constant honest when the bus is slow or a retry costs a cycle.
"""

from __future__ import annotations

import math
import statistics
from collections import deque


class MedianFilter:
    """Rolling median -- kills isolated spikes without smearing them into the
    average the way a mean would.  Kept short so it adds little lag."""

    def __init__(self, window: int = 5) -> None:
        if window < 1 or window % 2 == 0:
            raise ValueError("median window must be a positive odd integer")
        self.window = window
        self._buf: deque[float] = deque(maxlen=window)

    def reset(self) -> None:
        self._buf.clear()

    @property
    def primed(self) -> bool:
        return len(self._buf) == self.window

    def update(self, value: float) -> float:
        self._buf.append(value)
        return statistics.median(self._buf)


class ExponentialFilter:
    """Single-pole low pass with dt-aware gain.

    **It buys much less than sqrt(dt/2*tau) suggests, and the difference is not
    small.**  That expression is the noise gain for *uncorrelated* samples, and
    this cryostat's noise is not white: most of it sits at periods of tens of
    seconds to tens of hours, where a low pass cannot reach it without also
    removing the measurement.  Measured against promised, per tau, is in
    ``docs/ltspm3/noise.md``; ``python -m lschart.tools.noisespec`` re-derives
    it.  **Plan with the measured numbers, never with the formula.**

    **``tau = 0`` IS PASS-THROUGH, and on this cryostat that is the setting**
    (Jeff: no low pass, a median-3, about one cycle of dead time).  The
    paragraph above is why: what a pole bought here was a fraction of what it
    promised, paid for in lag that at the cold end is slower than the cryostat
    itself.  The class stays rather than being deleted from the chain: a
    cryostat whose noise moves into the band where a pole *would* help is a
    config edit away, and the group delay
    (:meth:`MeasurementFilter.group_delay_s`) already carries ``tau / 2`` so the
    loop retunes itself when that happens.
    """

    def __init__(self, tau: float) -> None:
        if tau < 0:
            raise ValueError("tau must not be negative")
        self.tau = tau
        self.value: float | None = None

    def reset(self, value: float | None = None) -> None:
        self.value = value

    def update(self, value: float, dt: float) -> float:
        if self.value is None or dt <= 0 or self.tau <= 0:
            self.value = value
            return value
        alpha = 1.0 - math.exp(-dt / self.tau)
        self.value += alpha * (value - self.value)
        return self.value

    def noise_gain(self, dt: float) -> float:
        """Ratio of output rms to white-noise input rms, for reporting."""
        if self.tau <= 0:
            return 1.0
        alpha = 1.0 - math.exp(-dt / self.tau)
        return math.sqrt(alpha / (2.0 - alpha))


class SlopeEstimator:
    """Least-squares dT/dt over a time window.

    Differencing consecutive samples would hand the derivative term pure noise;
    a regression over ~10 samples gives a usable drift rate, which is what the
    slow thermal tail actually looks like.
    """

    def __init__(self, window: int = 15) -> None:
        self.window = window
        self._t: deque[float] = deque(maxlen=window)
        self._y: deque[float] = deque(maxlen=window)

    def reset(self) -> None:
        self._t.clear()
        self._y.clear()

    @property
    def primed(self) -> bool:
        return len(self._t) >= max(3, self.window // 2)

    def update(self, t: float, y: float) -> float:
        self._t.append(t)
        self._y.append(y)
        if not self.primed:
            return 0.0
        n = len(self._t)
        mt = sum(self._t) / n
        my = sum(self._y) / n
        sxx = sum((t - mt) ** 2 for t in self._t)
        if sxx <= 0:
            return 0.0
        sxy = sum((t - mt) * (y - my) for t, y in zip(self._t, self._y))
        return sxy / sxx


class MeasurementFilter:
    """median -> single-pole low pass, plus a robust spike test and a slope.

    Testing and committing are deliberately separate calls.  The supervisor asks
    :meth:`is_spike` first, decides with the sensor guard whether to believe the
    sample, and only then calls :meth:`update`.  A rejected reading therefore
    never touches the filter state, so one bogus value cannot drag the control
    measurement even slightly.
    """

    def __init__(
        self,
        *,
        tau: float = 0.0,
        median_window: int = 3,
        slope_window: int = 15,
        spike_sigma: float = 8.0,
        spike_floor_k: float = 0.02,
        residual_window: int = 60,
        stale_after_s: float = 30.0,
    ) -> None:
        self.median = MedianFilter(median_window)
        self.lowpass = ExponentialFilter(tau)
        self.slope = SlopeEstimator(slope_window)
        self.spike_sigma = spike_sigma
        self.spike_floor_k = spike_floor_k
        #: Past this long without an accepted sample the stored state describes
        #: a cryostat that has since moved, so :meth:`predict` is no longer a
        #: statement about the present.  See :meth:`is_spike`.
        self.stale_after_s = stale_after_s
        self._residuals: deque[float] = deque(maxlen=residual_window)
        self._slope_value = 0.0
        self.last_accept_t: float | None = None

    def reset(self) -> None:
        self.median.reset()
        self.lowpass.reset()
        self.slope.reset()
        self._residuals.clear()
        self._slope_value = 0.0
        self.last_accept_t = None

    def group_delay_s(self, cadence_s: float) -> float:
        """The chain's pure delay, in seconds.  **Derived, never a constant.**

        Three terms, and each one is a lag something in this chain really has::

            median_window // 2 * cadence     the median's group delay: a
                                             window of 3 reports the middle
                                             sample, which is one cadence old
            cadence / 2                      the zero-order hold -- on average
                                             half a cycle passes between the
                                             cryostat moving and a sample of it
            tau / 2                          what is left of the low pass.
                                             Zero while it is switched off, and
                                             correct again the day it is not

        3.0 s at the 2 s cadence and median-3 this cryostat runs, and this
        method is the one home for that number.  It is the floor under how fast
        the loop may be asked to go -- ``tau_cl = max(speed * tau(T),
        delay_floor * delay_s)`` -- and at the cold end the plant's own tau is
        shorter than this, so it is the delay and not the cryostat that sets
        the closed-loop speed.  Which is why the cold end needs no schedule of
        its own.

        It lives here rather than in :class:`TuningConfig` because it is a
        property OF THIS CHAIN.  A number copied into the tuning section would
        be right until somebody changed the median window, and then it would be
        a tuning that quietly believed in a filter that no longer exists.
        """
        return ((self.median.window // 2) * cadence_s
                + cadence_s / 2.0
                + self.lowpass.tau / 2.0)

    def is_stale(self, t: float) -> bool:
        """True when no sample has been accepted recently enough to trust state."""
        if self.last_accept_t is None:
            return self.lowpass.value is not None
        return (t - self.last_accept_t) > self.stale_after_s

    def reseed(self, t: float, value: float) -> None:
        """Throw away stale state and restart from ``value``.

        Called when control resumes after an outage long enough that the cryostat
        may have moved underneath us.  Without this the frozen low-pass value
        keeps being used as the spike-test reference, every honest sample is
        rejected as an outlier, and nothing can ever update the reference again
        -- a deadlock that no amount of good data recovers from.
        """
        self.reset()
        self.median.update(value)
        self.lowpass.reset(value)
        self.slope.update(t, value)
        self.last_accept_t = t

    @property
    def value(self) -> float | None:
        return self.lowpass.value

    @property
    def primed(self) -> bool:
        return self.lowpass.value is not None and self.slope.primed

    def noise_estimate(self) -> float:
        """Robust (MAD-based) estimate of the current single-sample rms.

        Shown on the chart so the operator can watch the noise floor track
        temperature -- on this cryostat it rises steeply with T, and what it
        rises as is :class:`ltspm3.model.fitted_response.FittedParams`.
        """
        if len(self._residuals) < 8:
            return 0.0
        med = statistics.median(self._residuals)
        mad = statistics.median([abs(r - med) for r in self._residuals])
        return 1.4826 * mad

    def spike_threshold(self) -> float:
        return self.spike_sigma * max(self.noise_estimate(), self.spike_floor_k)

    def predict(self, dt: float) -> float | None:
        """Where the *next raw sample* should land.

        Comparing a raw sample against the low-pass output directly would be
        wrong during any sustained ramp: a single pole lags a ramp of rate ``r``
        by exactly ``r * tau``, so at tau=60 s a 0.011 K/s drift already sits
        0.66 K below the truth and every honest sample looks like an outlier.
        On the real cryostat's commanded ramp (2.5 K per sample) that would drive the
        loop to FAULT within a minute.

        Adding back ``slope * (tau + dt)`` makes the prediction unbiased for a
        constant ramp, so the spike test only fires on genuine discontinuities.
        """
        if self.lowpass.value is None:
            return None
        return self.lowpass.value + self._slope_value * (self.lowpass.tau + dt)

    def is_spike(self, value: float, dt: float = 0.0, t: float | None = None) -> bool:
        """Non-mutating robust outlier test against the predicted next sample.

        Returns False whenever the filter state is stale: a prediction built
        from a reading minutes old says nothing about now, and using it anyway
        rejects the very samples that would refresh it.
        """
        if t is not None and self.is_stale(t):
            return False
        predicted = self.predict(dt)
        if predicted is None or len(self._residuals) < 8:
            return False
        return abs(value - predicted) > self.spike_threshold()

    def update(self, t: float, value: float, dt: float) -> tuple[float, float]:
        """Fold an *accepted* sample in.  Returns ``(filtered, slope_K_per_s)``."""
        predicted = self.predict(dt)
        self._residuals.append(0.0 if predicted is None else value - predicted)
        med = self.median.update(value)
        filtered = self.lowpass.update(med, dt)
        self._slope_value = self.slope.update(t, filtered)
        self.last_accept_t = t
        return filtered, self._slope_value
