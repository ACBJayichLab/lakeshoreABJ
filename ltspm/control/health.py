"""Deciding whether to believe the control sensor.

The governing requirement is asymmetric: *failing to react* to a real
temperature change costs a little control quality, while *reacting to a bogus
reading* can dump kilowatt-seconds into a cryostat.  So every ambiguous case
resolves to "do not believe it, hold the output".

The escalation is deliberately slow, as requested:

``OK`` -> one bad sample -> ``SUSPECT`` (output frozen immediately, nothing else)
     -> still bad after ``fault_after_s`` -> ``FAULT`` (slow ramp to zero)
     -> ``recover_samples`` consecutive good readings -> ``RECOVERING`` -> ``OK``

Nothing here ever *raises* the heater in response to a fault.
"""

from __future__ import annotations

import enum
import math
from collections import deque
from dataclasses import dataclass

from lschart.model import Reading, Validity


class HealthState(enum.Enum):
    UNKNOWN = "unknown"      # not enough history yet; loop may not close
    OK = "ok"
    SUSPECT = "suspect"      # freeze output, wait it out
    FAULT = "fault"          # sustained failure -> ramp the heater down
    RECOVERING = "recovering"  # good again, but not yet trusted for control

    @property
    def trusted(self) -> bool:
        return self is HealthState.OK


@dataclass
class SensorGuardConfig:
    """Thresholds calibrated against 1,510 h (63 days) of reference logs.

    The earlier defaults came from two files and were wrong in both directions.
    They assumed the failure mode was a drop toward 0 K and that the largest
    legitimate one-sample change was 2.479 K.  Re-scanning all 24 logs shows:

    * the real failure is a *single-channel* burst of scattered values, in both
      directions, between 11 K and 298 K -- it never reads 0 K, so no floor
      catches it (9 events, ~1 per 7 days, always Input 1);
    * genuine cooldown transients reach **1.63 K/s** (-6.5 K in one 4 s sample,
      corroborated by inputs 2 and 3), and ~2.97 K/s just after a heater cut.

    So a single slew number cannot separate them: 1.25 K/s rejects real
    cooldowns, and anything loose enough to pass those also passes much of the
    glitch.  The split is now two-tier, with cross-channel corroboration
    (:mod:`ltspm.control.coherence`) deciding which tier applies.
    """

    valid_min_k: float = 1.0
    valid_max_k: float = 400.0

    #: Hard physical impossibility.  Rejected no matter who agrees.  The
    #: observed glitch runs 7.3 K/s and up; the fastest real move on record is
    #: 2.97 K/s, so this sits between them with margin on both sides.
    max_slew_k_per_s: float = 5.0
    #: Below this, a move needs no corroboration at all -- it is within what
    #: the plant does on its own.  Comfortably above the p99 of 0.26 K/sample.
    corroborate_slew_k_per_s: float = 0.5
    #: The slew test needs a *recent* trusted reference, and after a real
    #: outage the plant may genuinely have moved -- so a stale reference must
    #: not lock out recovery.  But every rejection ages the reference, so at a
    #: 20 s cadence a single rejection used to disable the slew test outright
    #: and let the next glitch sample through.  Hence the dt floor.
    slew_reference_max_age_s: float = 30.0
    slew_reference_min_samples: float = 3.0
    #: Below this the interval is too short to turn a difference into a rate:
    #: dividing a 15 mK sample-to-sample wobble by 20 ms yields 0.77 K/s out of
    #: pure noise.  Samples this close together are compared by magnitude only.
    min_interval_s: float = 0.25

    #: Curvature (reversal) test.  A real thermal signal is a smooth function
    #: of time: its second difference is small even when the first difference
    #: is huge.  The observed glitch reverses direction violently every sample
    #: -- 297 -> 151 -> 292 K -- so the second difference dwarfs the first.
    #:
    #: Measured over the reference logs, ``curvature_ratio`` of 1.5 fires 7
    #: times inside the known glitch, 0 times in a genuine 6.5 K-per-sample
    #: cooldown, and 0 times in a week of quiet holding.  Crucially it needs no
    #: trusted reference at all, so unlike the slew test it keeps working right
    #: through a burst of rejections -- which is exactly when the slew
    #: reference goes stale and stops protecting anything.
    curvature_ratio: float = 1.5
    #: Only applied to moves this large; below it, ordinary noise reverses sign
    #: constantly and means nothing.
    curvature_floor_k: float = 0.5
    curvature_noise_mult: float = 20.0

    #: One bad sample already freezes the output; this only governs the move to
    #: FAULT, which ramps the heater down and (with require_ack_after_fault)
    #: ends the run.  The longest observed self-healing glitch lasted 280 s, so
    #: 60 s would have converted a five-minute sensor burp into a lost cooldown.
    #: Freezing the output for 10 min on a plant with a 360 s pole is harmless.
    fault_after_s: float = 600.0
    recover_samples: int = 5
    #: Leaving FAULT is harder than leaving SUSPECT -- the heater has been moving.
    recover_samples_from_fault: int = 15
    reject_spikes: bool = True


@dataclass
class GuardResult:
    state: HealthState
    validity: Validity
    reason: str
    kelvin: float | None       # the value to use, or None if untrusted
    changed: bool = False


class SensorGuard:
    """Per-channel validity gate and health state machine."""

    def __init__(self, config: SensorGuardConfig | None = None, *, name: str = "sensor") -> None:
        self.cfg = config or SensorGuardConfig()
        self.name = name
        self.state = HealthState.UNKNOWN
        self.last_good_t: float | None = None
        self.last_good_k: float | None = None
        self.first_bad_t: float | None = None
        self.good_streak = 0
        self.bad_streak = 0
        self.last_reason = ""
        self._raw: deque[tuple[float, float]] = deque(maxlen=3)

    def _curvature(self) -> tuple[float, float] | None:
        """``(|d2 - d1|, max(|d1|, |d2|))`` as rates, or None if not enough history.

        Rates rather than raw differences, because the cadence in the reference
        logs ranges from 2 s to 20 s and a fixed per-sample threshold would mean
        something different in each.
        """
        if len(self._raw) < 3:
            return None
        (t0, v0), (t1, v1), (t2, v2) = self._raw
        dt1, dt2 = t1 - t0, t2 - t1
        if dt1 < self.cfg.min_interval_s or dt2 < self.cfg.min_interval_s:
            return None
        r1, r2 = (v1 - v0) / dt1, (v2 - v1) / dt2
        return abs(r2 - r1), max(abs(r1), abs(r2))

    # -- validity ---------------------------------------------------------

    def _check(
        self,
        t: float,
        reading: Reading | None,
        spike: bool,
        corroborated: bool | None,
        corroboration_why: str,
        noise_k: float,
        dt: float,
    ) -> tuple[Validity, str]:
        if reading is None:
            return Validity.COMMS_ERROR, "no reading in frame"
        if not reading.validity.good:
            return reading.validity, f"instrument reported {reading.validity.value}"
        k = reading.kelvin
        if not math.isfinite(k):
            return Validity.INSTRUMENT_FAULT, "non-finite reading"
        if k < self.cfg.valid_min_k:
            return Validity.OUT_OF_RANGE, f"{k:.4f} K below valid_min_k {self.cfg.valid_min_k}"
        if k > self.cfg.valid_max_k:
            return Validity.OUT_OF_RANGE, f"{k:.4f} K above valid_max_k {self.cfg.valid_max_k}"

        # Slew is only meaningful against a *recent* trusted reference.  After a
        # dropout the plant really may have moved, so a stale reference must not
        # lock us out of recovery forever.
        # Reversal test.  Deliberately before the slew test: it needs no
        # trusted reference, so it still works after a burst of rejections has
        # aged the slew reference out.
        curved = self._curvature()
        if curved is not None:
            jerk, biggest = curved
            floor = max(self.cfg.curvature_floor_k,
                        self.cfg.curvature_noise_mult * max(noise_k, 0.0))
            if biggest > floor and jerk > self.cfg.curvature_ratio * biggest:
                return (
                    Validity.INCOHERENT,
                    f"direction reversed by {jerk:.3f} K/s against a "
                    f"{biggest:.3f} K/s move -- not a thermal signal",
                )

        if self.last_good_k is not None and self.last_good_t is not None:
            age = t - self.last_good_t
            max_age = max(
                self.cfg.slew_reference_max_age_s,
                self.cfg.slew_reference_min_samples * dt,
            )
            if self.cfg.min_interval_s <= age <= max_age:
                delta = abs(k - self.last_good_k)
                rate = delta / age

                if rate > self.cfg.max_slew_k_per_s:
                    return (
                        Validity.SLEW_REJECT,
                        f"jumped {delta:.3f} K in {age:.1f} s = {rate:.2f} K/s "
                        f"(hard limit {self.cfg.max_slew_k_per_s} K/s)",
                    )

                # Between the two tiers the magnitude alone is ambiguous, so the
                # question becomes whether anything else on the cryostat saw it.
                # corroborated is None when no other channel is being tracked:
                # no evidence either way, so fall back to the hard limit rather
                # than making a single-sensor rig uncontrollable.
                if rate > self.cfg.corroborate_slew_k_per_s and corroborated is False:
                    return (
                        Validity.INCOHERENT,
                        f"moved {delta:.3f} K in {age:.1f} s = {rate:.2f} K/s but "
                        f"{corroboration_why}",
                    )

        if spike and self.cfg.reject_spikes:
            return Validity.SPIKE_REJECT, "robust outlier test failed"
        return Validity.GOOD, ""

    # -- state machine ----------------------------------------------------

    def update(
        self,
        t: float,
        reading: Reading | None,
        *,
        spike: bool = False,
        corroborated: bool | None = None,
        corroboration_why: str = "",
        noise_k: float = 0.0,
        dt: float = 0.0,
    ) -> GuardResult:
        # Raw history is kept for *every* sample, accepted or not: during a
        # glitch nothing is accepted, and that is precisely when the reversal
        # test has to keep working.
        if reading is not None and math.isfinite(reading.kelvin):
            self._raw.append((t, reading.kelvin))
        else:
            self._raw.clear()

        validity, reason = self._check(
            t, reading, spike, corroborated, corroboration_why, noise_k, dt
        )
        previous = self.state

        if validity.good:
            self.good_streak += 1
            self.bad_streak = 0
            self.first_bad_t = None
            assert reading is not None
            self.last_good_t = t
            self.last_good_k = reading.kelvin

            needed = (
                self.cfg.recover_samples_from_fault
                if previous is HealthState.FAULT
                else self.cfg.recover_samples
            )
            if previous is HealthState.OK:
                self.state = HealthState.OK
            elif previous is HealthState.UNKNOWN:
                self.state = HealthState.OK if self.good_streak >= 1 else HealthState.UNKNOWN
            elif self.good_streak >= needed:
                self.state = HealthState.OK
            else:
                self.state = HealthState.RECOVERING
                reason = f"{self.good_streak}/{needed} good samples before trusting again"
        else:
            self.good_streak = 0
            self.bad_streak += 1
            if self.first_bad_t is None:
                self.first_bad_t = t
            bad_for = t - self.first_bad_t
            if bad_for >= self.cfg.fault_after_s:
                self.state = HealthState.FAULT
                reason = f"{reason}; bad for {bad_for:.0f} s -> FAULT"
            else:
                self.state = HealthState.SUSPECT

        self.last_reason = reason
        return GuardResult(
            state=self.state,
            validity=validity,
            reason=reason,
            kelvin=reading.kelvin if (validity.good and reading is not None) else None,
            changed=self.state is not previous,
        )

    def reset(self) -> None:
        self.state = HealthState.UNKNOWN
        self.last_good_t = None
        self.last_good_k = None
        self.first_bad_t = None
        self.good_streak = 0
        self.bad_streak = 0
        self.last_reason = ""
        self._raw.clear()
