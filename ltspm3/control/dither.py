"""Sub-code output resolution by first-order sigma-delta modulation.

The 218's analog output resolves 0.01%.  With a local gain near 7.6 K/%
at the 63% operating point, one code is ~76 mK -- roughly eight times the
sensor noise floor and far coarser than the few-mK stabilisation goal.  Plain
rounding would therefore make millikelvin control impossible no matter how good
the PID is.

The fix is to let time carry the extra bits.  The requested value is quantised
to the nearest code and the rounding error is carried forward, so the *sequence*
of codes averages to the request.  The response's ~360 s fast pole low-passes the
dither: at a 4 s update the residual ripple is roughly one code times dt/tau,
i.e. under 1 mK here.
"""

from __future__ import annotations


class SigmaDeltaDither:
    """First-order noise shaping onto a fixed output grid."""

    def __init__(self, step: float = 0.01, *, max_error_steps: float = 4.0) -> None:
        if step <= 0:
            raise ValueError("step must be positive")
        self.step = step
        #: Clamp on the carried error, so a long clamped excursion cannot store
        #: up a surprise correction and dump it the moment limits release.
        self.max_error = max_error_steps * step
        self.error = 0.0

    def reset(self) -> None:
        self.error = 0.0

    def quantise(self, value: float) -> float:
        """Return the code to send for ``value``, updating the carried error."""
        wanted = value + self.error
        code = round(wanted / self.step) * self.step
        self.error = wanted - code
        if self.error > self.max_error:
            self.error = self.max_error
        elif self.error < -self.max_error:
            self.error = -self.max_error
        return round(code, 6)
