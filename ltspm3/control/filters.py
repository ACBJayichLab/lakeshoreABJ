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
        accel_window: int = 7,
        spike_sigma: float = 8.0,
        spike_floor_k: float = 0.02,
        spike_min_k: float = 0.0,
        residual_window: int = 60,
        stale_after_s: float = 30.0,
        max_consecutive_spikes: int = 3,
    ) -> None:
        self.median = MedianFilter(median_window)
        self.lowpass = ExponentialFilter(tau)
        self.slope = SlopeEstimator(slope_window)
        #: The slope OF THE SLOPE, on a deliberately short window.  Two
        #: consumers, both of which widen something while the cryostat is
        #: accelerating: the residual's `slope_lag` band term and
        #: :meth:`spike_threshold`.  Short because a lagging estimate would
        #: widen them after the acceleration that needed it -- and the noise
        #: that costs is taken back out exactly, in
        #: :meth:`acceleration_excess`.
        self.accel = SlopeEstimator(accel_window)
        self._accel_value = 0.0
        self._accel_sq = 0.0
        self._accel_gain_key: tuple | None = None
        self._accel_gain = 0.0
        self.spike_sigma = spike_sigma
        self.spike_floor_k = spike_floor_k
        #: **The smallest miss the spike test may call a spike**, in kelvin,
        #: whatever the noise says.  NOT a noise floor -- `spike_floor_k` is
        #: that, and it also sets `acceleration_noise`, which the residual's
        #: `slope_lag` band is built on, so raising it to quiet the spike test
        #: would narrow the band during exactly the moves that need it.  This
        #: one touches the spike test and nothing else.  0 is off.
        self.spike_min_k = spike_min_k
        #: Past this long without an accepted sample the stored state describes
        #: a cryostat that has since moved, so :meth:`predict` is no longer a
        #: statement about the present.  See :meth:`is_spike`.
        self.stale_after_s = stale_after_s
        #: **THE OTHER WAY OUT OF THE SAME DEADLOCK, counted in samples rather
        #: than seconds.**  A rejected sample does not update the filter, so
        #: the reference it was rejected against never moves: one wrong
        #: rejection rejects everything after it, for ever.  `stale_after_s`
        #: is the escape, and at 30 s it is thirty seconds of a frozen heater.
        #:
        #: A run this long is not the isolated outlier this test exists for.
        #: Three is the shortest value that keeps what the chain was built to
        #: reject: the median of three absorbs one bad sample, and glitches of
        #: one AND two samples are a graded case
        #: (`test_a_glitch_of_EITHER_length_freezes_the_output_and_moves_nothing`).
        #: The third consecutive rejection is the world having moved.
        self.max_consecutive_spikes = max_consecutive_spikes
        self._residuals: deque[float] = deque(maxlen=residual_window)
        self._slope_value = 0.0
        self._spike_run = 0
        self.last_accept_t: float | None = None

    def reset(self) -> None:
        self.median.reset()
        self.lowpass.reset()
        self.slope.reset()
        self.accel.reset()
        self._residuals.clear()
        self._slope_value = 0.0
        self._accel_value = 0.0
        self._accel_sq = 0.0
        self._spike_run = 0
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

    def slope_delay_s(self, cadence_s: float) -> float:
        """**How old the slope this chain reports is.**  Derived, never typed.

        A least-squares slope over ``N`` samples is the slope at the window's
        centre, ``(N - 1) * cadence / 2`` ago, and it is fed a value that has
        already been through the median and the pole -- so their lag is on it
        too.  15.0 s at the 15-sample window, median of three and 2 s cadence
        this cryostat runs; the formula gives 16.0, and a band is the one place
        where the conservative direction is the wide one.

        Different from :meth:`group_delay_s`, which is the delay on the
        *value*.  The slope is a much older statement than the value is, and
        the residual's `slope_lag` term is what that costs.
        """
        return ((self.slope.window - 1) * cadence_s / 2.0
                + (self.median.window // 2) * cadence_s
                + self.lowpass.tau)

    @property
    def acceleration(self) -> float:
        """``d2T/dt2`` in K/s^2, raw.  Mostly noise at a hold -- see
        :meth:`acceleration_excess`, which is what consumers want."""
        return self._accel_value

    def acceleration_noise(self, cadence_s: float) -> float:
        """One sigma on :attr:`acceleration`, in K/s^2.  **Derived.**

        Two least-squares slopes in series are one linear filter on the raw
        samples, so its noise gain is the L2 norm of the two kernels
        convolved -- exact, and a function of nothing but the two window
        lengths and the cadence.  Propagating the two stages as if they were
        independent understates it by 35 %, because consecutive slopes come
        from overlapping windows and are correlated; this does not have that
        problem because it never treats them as separate measurements.
        """
        gain = self._accel_noise_gain(cadence_s)
        return gain * max(self.noise_estimate(), self.spike_floor_k)

    def _accel_noise_gain(self, cadence_s: float) -> float:
        ns, na = self.slope.window, self.accel.window
        if ns < 3 or na < 3 or cadence_s <= 0:
            return 0.0
        key = (ns, na, cadence_s)
        if self._accel_gain_key != key:
            def kernel(n: int) -> list[float]:
                ts = [i * cadence_s for i in range(n)]
                mt = sum(ts) / n
                sxx = sum((x - mt) ** 2 for x in ts)
                return [(x - mt) / sxx for x in ts]
            a, b = kernel(na), kernel(ns)
            combined = [0.0] * (na + ns - 1)
            for i, u in enumerate(a):
                for j, v in enumerate(b):
                    combined[i + j] += u * v
            self._accel_gain_key = key
            self._accel_gain = math.sqrt(sum(c * c for c in combined))
        return self._accel_gain

    def acceleration_excess(self, cadence_s: float) -> float:
        """``|d2T/dt2|`` with the estimator's own noise taken out, in K/s^2.

        **The consumers want this one and not the raw value.**  A measured
        magnitude is ``sqrt(true^2 + noise^2)`` in expectation, so the noise
        comes out in quadrature and nothing needs a threshold anybody has to
        choose: at a settled hold this is zero because the whole magnitude is
        explained, and during a move the noise is a twelfth of the signal and
        takes essentially nothing off it.

        It matters that this is zero at a hold.  Both consumers -- the
        residual's ``slope_lag`` band and :meth:`spike_threshold` -- would
        otherwise be three times wider at a settled hold, which is exactly
        where the residual earns its place.

        **The magnitude is averaged in power before the subtraction**, over the
        acceleration window's own span, because subtracting the expected noise
        from a single sample leaves the fluctuation behind: one sample sitting
        a plausible 1.5 sigma high still came out as an acceleration, and that
        alone doubled the settled band.  A band is a statement about a stretch
        of time, so it is entitled to look at one.
        """
        sigma = self.acceleration_noise(cadence_s)
        excess = self._accel_sq - sigma ** 2
        return math.sqrt(excess) if excess > 0.0 else 0.0

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
        self.accel.update(t, 0.0)
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

    def spike_threshold(self, dt: float = 0.0) -> float:
        """How far a raw sample may sit from :meth:`predict` and be believed.

        Two terms, and the second one is not noise.

        The first is the robust noise floor times ``spike_sigma`` -- the
        question the test was built to ask, and the only term that matters at a
        hold or during a constant sweep.

        The second is **what the prediction itself is uncertain by**, and it
        exists for the same reason the residual's ``slope_lag`` band term does:
        :meth:`predict` leans on a slope that is :meth:`slope_delay_s` old, so
        while the cryostat is ACCELERATING the prediction is wrong by the slope
        error times the horizon it is projected over -- through no fault of the
        thermometer.  Ignoring it made a fast move reject its own good samples
        and freeze the heater part way up, which cost 518 mK of overshoot at
        100 K on the bench.

        It is zero at a hold and zero at a constant rate however fast, so
        nothing this widens was ever narrow when it was needed.

        **Under both, `spike_min_k`.**  The second term is right in form and
        late in fact at the ONSET of a fast move: the acceleration estimate is
        a slope of a 30 s slope and lags the heater kicking the sample from
        rest.  On the cryostat 2026-09-23 every 2 K move started with the
        prediction ~0.19 K behind a smooth, one-way curve against a 0.16 K
        threshold, and two rejected samples froze the heater for 14 s.  The
        glitch this test exists for is tens of kelvin (docs/ltspm3/safety.md),
        so a minimum well above the onset miss and far below the glitch costs
        nothing it was built to catch.
        """
        noise = max(self.spike_sigma * max(self.noise_estimate(), self.spike_floor_k),
                    self.spike_min_k)
        horizon = (self.median.window // 2) * dt + self.lowpass.tau + dt
        stale_slope = self.acceleration_excess(dt) * self.slope_delay_s(dt)
        return noise + stale_slope * horizon

    def predict(self, dt: float) -> float | None:
        """Where the *next raw sample* should land.

        Comparing a raw sample against the filter output directly would be
        wrong during any sustained ramp: the chain lags a ramp of rate ``r`` by
        exactly ``r`` times its own delay, so every honest sample looks like an
        outlier.  Adding that lag back makes the prediction unbiased for a
        constant ramp, so the spike test only fires on genuine discontinuities.

        **BOTH lags, not just the pole's.**  This used to add back
        ``tau + dt`` and leave the median's ``window // 2`` cadences out, which
        was a bias of exactly ``r * 2 s`` at the median of three this cryostat
        runs -- invisible at a drift and fatal at a sweep.  Past about
        3 K/min it exceeded the 8-sigma threshold, and then it ran away: a
        rejected sample does not update the filter, so the prediction ages, so
        the residual grows, so the next sample is rejected too.  On a
        noise-free 5 K/min ramp -- the rate this file's own
        `ramp.max_rate_k_per_min` names -- 38 of 40 samples were thrown away
        and the loop froze until `stale_after_s` forced a reseed.

        The lag is the same quantity :meth:`group_delay_s` reports, less its
        zero-order-hold term: that one is the sampling and not the filter
        state, and ``dt`` already carries it here.
        """
        if self.lowpass.value is None:
            return None
        lag = (self.median.window // 2) * dt + self.lowpass.tau + dt
        return self.lowpass.value + self._slope_value * lag

    def is_spike(self, value: float, dt: float = 0.0, t: float | None = None) -> bool:
        """Robust outlier test against the predicted next sample.

        Returns False whenever the filter state is stale: a prediction built
        from a reading minutes old says nothing about now, and using it anyway
        rejects the very samples that would refresh it.

        **It counts its own run**, because that is the only place that sees
        every verdict, and a run of them is the one thing that distinguishes a
        cryostat which moved from a sensor which lied
        (`max_consecutive_spikes`).  Call it once per sample; `update()` clears
        the run, and so does accepting anything.
        """
        if t is not None and self.is_stale(t):
            self._spike_run = 0
            return False
        predicted = self.predict(dt)
        if predicted is None or len(self._residuals) < 8:
            self._spike_run = 0
            return False
        if abs(value - predicted) <= self.spike_threshold(dt):
            self._spike_run = 0
            return False
        self._spike_run += 1
        if self._spike_run >= self.max_consecutive_spikes:
            # Believe it, and **START AGAIN FROM IT**.  Whatever this is, it is
            # not one bad reading, and going on rejecting it is how the
            # reference never recovers.
            #
            # Accepting the sample without reseeding is not enough, and the
            # difference is the whole of this branch.  Folding one honest
            # sample into a reference that is now seconds of ramp behind leaves
            # the prediction just as wrong, so the next two are rejected too
            # and the filter limps at one sample in three -- while the SENSOR
            # GUARD needs `recover_samples` GOOD ones in a row before it will
            # trust the channel again, and never gets them.  Measured: a fast
            # 2 K move at 100 K froze the heater for 24 s that way and
            # overshot 274 mK.
            #
            # `reseed` is the remedy already written for this deadlock (see its
            # docstring); it was simply only reachable on the `stale_after_s`
            # clock.  It clears the residuals too, so the next few samples are
            # accepted unconditionally while the slope re-learns -- which is
            # what lets the guard collect a clean run and the loop resume.
            self._spike_run = 0
            if t is not None:
                self.reseed(t, value)
            return False
        return True

    def update(self, t: float, value: float, dt: float) -> tuple[float, float]:
        """Fold an *accepted* sample in.  Returns ``(filtered, slope_K_per_s)``."""
        self._spike_run = 0
        predicted = self.predict(dt)
        self._residuals.append(0.0 if predicted is None else value - predicted)
        med = self.median.update(value)
        filtered = self.lowpass.update(med, dt)
        self._slope_value = self.slope.update(t, filtered)
        self._accel_value = self.accel.update(t, self._slope_value)
        # Mean SQUARE, over the acceleration window's own span -- dt-aware like
        # everything else here.  `acceleration_excess` is what reads it.
        span = max((self.accel.window - 1) * dt, dt)
        alpha = 1.0 - math.exp(-dt / span) if dt > 0 else 1.0
        self._accel_sq += alpha * (self._accel_value ** 2 - self._accel_sq)
        self.last_accept_t = t
        return filtered, self._slope_value
