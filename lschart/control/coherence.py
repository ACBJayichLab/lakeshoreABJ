"""Cross-channel corroboration: is this move thermal, or is it one sick sensor?

Nine glitch events were extracted from ``reference/logs`` (1,510 h, 63 days).
Every one of them had the same shape:

* it hit **Input 1 only** -- never input 2 or 3, never any 336 channel;
* the reported value jumped in *both* directions, scattering between 11 K and
  298 K while the true temperature was smooth;
* it lasted between 2 s and 280 s and then resumed exactly on the pre-glitch
  trend, as if nothing had happened;
* it never once read 0 K, so no zero-detector and no ``valid_min_k`` floor
  would have caught it.

The one thing that separates those events from a genuinely fast thermal
transient is corroboration.  In ``cd8_..._sample_monitor7.xls`` the sample fell
6.5 K in a single 4 s sample -- a rate of 1.63 K/s, well past any plausible
fixed slew limit -- and inputs 2 and 3 fell *with it*, by ~100x their own noise.
In the glitch, inputs 2 and 3 carried on down their existing trend without so
much as a wobble.

So magnitude alone cannot classify these; a threshold loose enough to pass the
real transient passes half the glitch, and one tight enough to catch the glitch
rejects real cooldowns.  What classifies them is whether *anything else on the
cryostat noticed*.

This module answers exactly that question and nothing else.  It reports how
far each channel has departed from its own short-term prediction, in units of
that channel's own robust noise; :class:`~lschart.control.health.SensorGuard`
decides what to do about it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..model import Reading
from .filters import MeasurementFilter


@dataclass
class CoherenceConfig:
    """Thresholds for "did another channel see it too?".

    ``sigma`` is deliberately far looser than the control channel's own spike
    test (8 sigma).  We are not trying to decide whether the corroborating
    channel is healthy -- only whether it moved at all.  A real thermal event
    shows up on the ancillary channels at tens to hundreds of sigma, so 4.0
    has a large margin while still ignoring ordinary noise.
    """

    sigma: float = 4.0
    #: Absolute floor on a channel's noise estimate, so a very quiet sensor
    #: cannot corroborate on a few tenths of a millikelvin of nothing.
    floor_k: float = 0.005
    #: Ancillary channels lag the sample -- they are further from the heater.
    #: A departure this recent still counts as simultaneous.
    window_s: float = 30.0
    #: Per-channel filter time constant.  Kept short deliberately: this is a
    #: movement detector, not a measurement.  Slowing it down so that residuals
    #: persist through a long transient turns ordinary cryostat drift into a
    #: departure and floods the guard with false rejections (measured: 300+/day
    #: against 13/day here).
    tau_s: float = 30.0
    slope_window: int = 9
    #: A sample this far off its own channel's trend is not folded into that
    #: channel's filter.  Without this the reference is poisoned by the very
    #: glitch it is supposed to measure: the observed events alternate wild and
    #: plausible values, and each folded-in wild value drags the trend far
    #: enough that the next wild value looks reasonable.
    hold_off_sigma: float = 20.0


@dataclass
class ChannelDeparture:
    channel: str
    z: float          # |residual| in units of that channel's own robust noise
    residual_k: float
    noise_k: float


class CoherenceMonitor:
    """Tracks every channel's departure from its own trend.

    Cheap: one small filter per channel, updated once per frame.  It is fed
    *every* channel including the control one; callers exclude the channel they
    are asking about.
    """

    def __init__(self, config: CoherenceConfig | None = None) -> None:
        self.cfg = config or CoherenceConfig()
        self._filters: dict[str, MeasurementFilter] = {}
        self._recent: dict[str, deque[tuple[float, float]]] = {}
        self._last_t: dict[str, float] = {}
        self.latest: dict[str, ChannelDeparture] = {}

    def reset(self) -> None:
        self._filters.clear()
        self._recent.clear()
        self._last_t.clear()
        self.latest.clear()

    def _filter_for(self, channel: str) -> MeasurementFilter:
        f = self._filters.get(channel)
        if f is None:
            f = MeasurementFilter(
                tau=self.cfg.tau_s,
                median_window=3,
                slope_window=self.cfg.slope_window,
                stale_after_s=self.cfg.window_s * 2,
            )
            self._filters[channel] = f
            self._recent[channel] = deque()
        return f

    def update(self, t: float, readings: dict[str, Reading]) -> None:
        """Fold one frame in.  Unusable readings are skipped, not zeroed."""
        for name, reading in readings.items():
            if reading is None or not reading.usable:
                continue
            f = self._filter_for(name)
            last = self._last_t.get(name)
            dt = 0.0 if last is None else max(0.0, t - last)
            self._last_t[name] = t

            predicted = f.predict(dt)
            if predicted is None or f.is_stale(t):
                # No usable trend for this channel: report *no* departure rather
                # than leaving the last one standing, which would otherwise be
                # read as a fresh event for as long as the channel stayed dark.
                self.latest.pop(name, None)
                self._recent[name].clear()
                f.update(t, reading.kelvin, dt)
                continue

            residual = reading.kelvin - predicted
            noise = max(f.noise_estimate(), self.cfg.floor_k)
            z = abs(residual) / noise
            self.latest[name] = ChannelDeparture(name, z, residual, noise)

            recent = self._recent[name]
            recent.append((t, z))
            while recent and t - recent[0][0] > self.cfg.window_s:
                recent.popleft()

            # Keep the reference clean: fold in only what is plausibly this
            # channel's own signal.  The filter still ages out via is_stale(),
            # so a genuine step change is picked up by the reseed path rather
            # than by corrupting the trend one wild sample at a time.
            if z < self.cfg.hold_off_sigma:
                f.update(t, reading.kelvin, dt)

    def departure(self, channel: str) -> float | None:
        """How far ``channel`` last sat from its own trend, in its own sigma.

        Unlike a slew test this needs no recent *trusted* reference, so it keeps
        working through a burst of rejections -- which is exactly when the slew
        reference goes stale and stops protecting anything.
        """
        dep = self.latest.get(channel)
        return None if dep is None else dep.z

    def corroboration(self, exclude: str, t: float) -> tuple[bool | None, str]:
        """Did any channel other than ``exclude`` also depart from its trend?

        Returns ``(verdict, why)`` where verdict is:

        * ``True``  -- at least one other channel moved; the event looks thermal
        * ``False`` -- other channels are being tracked and all stayed on trend
        * ``None``  -- nothing else is being tracked, so there is no evidence
          either way.  The guard degrades to its absolute slew limit rather
          than making a single-sensor rig uncontrollable.
        """
        others = [c for c in self._recent if c != exclude]
        if not others:
            return None, "no other channel tracked"

        best: ChannelDeparture | None = None
        for c in others:
            for ts, z in self._recent[c]:
                if t - ts > self.cfg.window_s:
                    continue
                dep = self.latest.get(c)
                if z >= self.cfg.sigma and (best is None or z > best.z):
                    best = ChannelDeparture(c, z, dep.residual_k if dep else 0.0,
                                            dep.noise_k if dep else 0.0)
        if best is not None:
            return True, f"{best.channel} moved {best.z:.0f} sigma at the same time"

        quiet = ", ".join(sorted(others))
        return False, f"no other channel moved ({quiet} all on trend)"
