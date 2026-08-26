"""Noise reduction for a 4 s-cadence, ~10 mK-rms measurement.

Sampling is nominally 4.000 s but the real logs jitter between 3.92 s and 4.07 s,
so every filter here takes the actual ``dt`` and uses the exact exponential
coefficient rather than a fixed alpha.  That keeps the effective time constant
honest when the bus is slow or a retry costs a cycle.
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

    At tau=60 s and dt=4 s the output noise is 0.18x the input, i.e. the cryostat's
    ~10 mK sample noise becomes ~1.8 mK -- enough for millikelvin work while
    staying 6x faster than the thermal response's ~360 s fast pole.
    """

    def __init__(self, tau: float) -> None:
        if tau <= 0:
            raise ValueError("tau must be positive")
        self.tau = tau
        self.value: float | None = None

    def reset(self, value: float | None = None) -> None:
        self.value = value

    def update(self, value: float, dt: float) -> float:
        if self.value is None or dt <= 0:
            self.value = value
            return value
        alpha = 1.0 - math.exp(-dt / self.tau)
        self.value += alpha * (value - self.value)
        return self.value

    def noise_gain(self, dt: float) -> float:
        """Ratio of output rms to white-noise input rms, for reporting."""
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
        tau: float = 60.0,
        median_window: int = 5,
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
        temperature -- on this cryostat it is ~1 mK at 18 K and ~10 mK at 96 K.
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
