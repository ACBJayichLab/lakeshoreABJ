"""In-process fakes for the 218 and 336, plus the thermal model behind them.

There is no hardware on the bench, so this is the primary development target.
The model is *not* an attempt at cryostat physics; it is tuned to reproduce the
four things the control software actually has to cope with, all measured from
``reference/logs/cd10_7_2026_sample_cold.xls``:

1. a steeply nonlinear steady state -- 43% -> 18.2 K, 63.1% -> ~100 K, which is
   ``T = T_bath + A * pct**5`` and gives a local gain near 7 K/% at 63%;
2. two very different time constants -- ~6 min fast, ~4 h slow tail;
3. temperature-dependent sensor noise -- quadratic in T: ~1.8 mK at 18 K,
   ~14 mK at 96 K, ~110 mK at 290 K;
4. 1 mK reporting quantisation.

Faults never seen in the real logs (dropouts to 0 K, RDGST errors, comms
timeouts) can be injected explicitly -- see :meth:`SimulatedRig.inject`.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from ..transport import TransportError


@dataclass
class PlantParams:
    """Calibrated against the 2026-07 cooldown; see module docstring."""

    t_bath: float = 4.0
    exponent: float = 5.0          # T_ss - T_bath  proportional to pct**exponent
    ref_pct: float = 63.076        # the operating point the rig actually sat at
    ref_rise: float = 96.0         # T_ss - T_bath there, i.e. ~100 K absolute
    tau_fast: float = 360.0        # s   (~6 min)
    tau_slow: float = 14400.0      # s   (~4 h)
    fast_fraction: float = 0.90    # of the step lands on the fast pole
    #: Sensor noise, measured by 3-point local detrending over cd9+cd10:
    #:
    #:     18 K   1.8 mK        190 K   45 mK
    #:     96 K  13.6 mK        240 K   73 mK
    #:                          290 K  109 mK
    #:
    #: That is *quadratic* in T, not linear -- the previous linear model was
    #: calibrated at 96 K and underestimated 290 K noise by about 4x.  It
    #: matters for the sweep requirement: at room temperature the measurement
    #: floor is ~110 mK, so millikelvin stability is unreachable up there
    #: however good the control is.
    noise_floor_k: float = 0.0018  # rms at low T
    noise_quadratic: float = 1.36e-6   # rms = this * T**2
    quantum_k: float = 0.001       # reported resolution

    @property
    def coeff(self) -> float:
        return self.ref_rise / (self.ref_pct**self.exponent)

    def steady_state(self, pct: float) -> float:
        return self.t_bath + self.coeff * max(pct, 0.0) ** self.exponent

    def local_gain(self, pct: float) -> float:
        """dT/d(pct) in K/% -- the number that makes this rig hard to control."""
        if pct <= 0:
            return 0.0
        return self.exponent * self.coeff * pct ** (self.exponent - 1)


class ThermalModel:
    """Two-pole lag onto a nonlinear steady state, integrated with exact
    exponential updates so the step size never affects stability."""

    def __init__(self, params: PlantParams | None = None, *, start_k: float | None = None):
        self.p = params or PlantParams()
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


@dataclass
class FaultInjection:
    """Knobs the tests and the GUI's 'fault drill' use to abuse the pipeline.

    ``glitch_channels`` is the one that matters.  It reproduces the failure
    actually present in the reference logs -- 9 events in 1,510 h, always on
    the sample input -- rather than the tidy drop-to-zero this simulator used
    to model, which has never once occurred on this rig:

    * the value scatters in *both* directions, between ``glitch_low_k`` and
      roughly the true temperature;
    * it never reads 0 K, so nothing keyed to zero or to ``valid_min_k``
      notices it;
    * other channels are untouched, which is the only thing that reliably
      distinguishes it from a genuine fast transient;
    * it clears on its own after seconds to minutes, resuming the real trend.

    ``dropout_channels`` is kept for the hard-zero case (a disconnected sensor
    genuinely does read 0.000 K), but it is not the interesting fault.
    """

    dropout_channels: set[str] = field(default_factory=set)  # report 0.0 K
    dropout_value: float = 0.0
    #: Channels emitting scattered garbage -- the real observed failure.
    glitch_channels: set[str] = field(default_factory=set)
    glitch_low_k: float = 11.0
    glitch_high_k: float | None = None   # None -> the channel's true value
    rdgst_channels: dict[str, int] = field(default_factory=dict)  # channel -> bits
    comms_fail: bool = False
    stuck_channels: set[str] = field(default_factory=set)
    extra_noise_k: float = 0.0


class SimulatedRig:
    """Holds the shared clock, the plant and the fault state for both fakes."""

    def __init__(
        self,
        params: PlantParams | None = None,
        *,
        seed: int = 0xC01D,
        start_k: float = 96.0,
        time_source=time.monotonic,
        speedup: float = 1.0,
    ) -> None:
        self.plant = ThermalModel(params, start_k=start_k)
        self.rng = random.Random(seed)
        self.faults = FaultInjection()
        self.speedup = speedup
        self._time = time_source
        self._t0 = self._time()
        self._last = self._t0
        # Ancillary channels drift gently so the chart has something to show.
        self._aux_base = {
            "218.2": 8.06, "218.3": 6.72,
            "336.A": 38.2, "336.B": 290.6, "336.C": 28.56, "336.D": 3.95,
        }
        #: How strongly each ancillary channel follows the sample, in K per K.
        #: Measured from cd8_..._sample_monitor7.xls, where the sample fell
        #: 22.4 K and input 2 fell 0.183 K (0.008) while input 3 fell 0.051 K
        #: (0.002).  Small, but hundreds of times those channels' own noise --
        #: which is exactly what makes a real transient distinguishable from a
        #: one-channel glitch.  Without this coupling the simulator can never
        #: corroborate anything and the coherence logic is untestable.
        self._aux_coupling = {
            "218.2": 0.0082, "218.3": 0.0023,
            "336.A": 0.0040, "336.B": 0.0002, "336.C": 0.0015, "336.D": 0.0008,
        }
        self._plant_ref_k = self.plant.temperature
        self._stuck_values: dict[str, float] = {}

    # -- clock -------------------------------------------------------------

    def tick(self) -> None:
        now = self._time()
        dt = (now - self._last) * self.speedup
        self._last = now
        self.plant.advance(dt)

    def inject(self, **kw) -> None:
        """``rig.inject(dropout_channels={'Sample'}, comms_fail=True)``"""
        for k, v in kw.items():
            if not hasattr(self.faults, k):
                raise AttributeError(f"no such fault knob: {k}")
            setattr(self.faults, k, v)

    def clear_faults(self) -> None:
        self.faults = FaultInjection()

    # -- channel values ----------------------------------------------------

    def _guard_comms(self) -> None:
        if self.faults.comms_fail:
            raise TransportError("simulated comms failure")

    def value(self, key: str, base: float | None = None) -> float:
        if key in self.faults.dropout_channels:
            return self.faults.dropout_value
        if key in self.faults.glitch_channels:
            true_k = self.plant.temperature if base is None else base
            hi = self.faults.glitch_high_k
            hi = true_k if hi is None else hi
            lo = min(self.faults.glitch_low_k, hi)
            return round(self.rng.uniform(lo, hi), 3)
        if key in self.faults.stuck_channels:
            return self._stuck_values.setdefault(key, base if base is not None else 0.0)
        if base is None:
            v = self.plant.observe(self.rng)
        else:
            coupled = base + self._aux_coupling.get(key, 0.0) * (
                self.plant.temperature - self._plant_ref_k
            )
            v = coupled + self.rng.gauss(0, 0.002)
        if self.faults.extra_noise_k:
            v += self.rng.gauss(0.0, self.faults.extra_noise_k)
        return round(v, 4)

    def rdgst(self, key: str) -> int:
        return self.faults.rdgst_channels.get(key, 0)


class Sim218:
    """Answers the subset of the 218 command set that :class:`LS218` uses."""

    def __init__(self, rig: SimulatedRig) -> None:
        self.rig = rig
        self.analog_pct = 63.076
        self.analog_settings = [1, 0, 2, 1, 1, 1, 1, self.analog_pct]
        self.write_log: list[str] = []
        #: The instrument's own DAC resolution.  0.01% here is ~75 mK on this
        #: plant, which is exactly why the supervisor dithers.
        self.dac_step = 0.01

    def handle_write(self, cmd: str) -> None:
        self.rig._guard_comms()
        self.write_log.append(cmd)
        head, _, args = cmd.partition(" ")
        if head.upper() == "ANALOG":
            parts = [p.strip() for p in args.split(",")]
            if len(parts) != 8:
                raise TransportError(f"malformed ANALOG command: {cmd!r}")
            requested = float(parts[7])
            quantised = round(requested / self.dac_step) * self.dac_step
            self.analog_pct = min(100.0, max(0.0, quantised))
            self.rig.plant.pct = self.analog_pct

    def handle_query(self, cmd: str) -> str:
        self.rig._guard_comms()
        self.rig.tick()
        head, _, arg = cmd.partition(" ")
        head = head.upper()
        arg = arg.strip()
        if head == "*IDN?":
            return "LSCI,MODEL218,SIM0001,1.0"
        if head == "KRDG?":
            if arg == "0":
                vals = [
                    self.rig.value("218.1"),
                    self.rig.value("218.2", self.rig._aux_base["218.2"]),
                    self.rig.value("218.3", self.rig._aux_base["218.3"]),
                    0.0, 0.0, 0.0, 0.0, 0.0,
                ]
                return ",".join(f"{v:+09.4f}" for v in vals)
            return f"{self.rig.value(f'218.{arg}'):+09.4f}"
        if head == "SRDG?":
            return ",".join("+0.00000" for _ in range(8))
        if head == "RDGST?":
            return str(self.rig.rdgst(f"218.{arg}"))
        if head == "AOUT?":
            return f"{self.analog_pct:+07.2f}"
        if head == "ANALOG?":
            return ",".join(str(x) for x in self.analog_settings[:7]) + f",{self.analog_pct:.3f}"
        raise TransportError(f"Sim218 does not implement {cmd!r}")


class Sim336:
    """Answers the subset of the 336 command set that :class:`LS336` uses."""

    NAMES = {"A": "RAD SHIELD", "B": "THE CHONKE", "C": "1st Stage", "D": "2nd Stage"}

    def __init__(self, rig: SimulatedRig) -> None:
        self.rig = rig
        self.setpoints = {1: 0.0, 2: 290.6, 3: 0.0, 4: 0.0}
        self.heaters = {1: 0.0, 2: 97.9}
        self.write_log: list[str] = []

    def handle_write(self, cmd: str) -> None:
        self.rig._guard_comms()
        self.write_log.append(cmd)
        head, _, args = cmd.partition(" ")
        if head.upper() == "SETP":
            loop, val = args.split(",")
            self.setpoints[int(loop)] = float(val)

    def handle_query(self, cmd: str) -> str:
        self.rig._guard_comms()
        self.rig.tick()
        head, _, arg = cmd.partition(" ")
        head = head.upper()
        arg = arg.strip()
        if head == "*IDN?":
            return "LSCI,MODEL336,SIM0002,1.0"
        if head == "KRDG?":
            keys = ["336.A", "336.B", "336.C", "336.D"]
            if arg == "0":
                return ",".join(
                    f"{self.rig.value(k, self.rig._aux_base[k]):+09.4f}" for k in keys
                )
            k = f"336.{arg}"
            return f"{self.rig.value(k, self.rig._aux_base[k]):+09.4f}"
        if head == "RDGST?":
            return str(self.rig.rdgst(f"336.{arg}"))
        if head == "INNAME?":
            return self.NAMES.get(arg, f"Input {arg}")
        if head == "SETP?":
            return f"{self.setpoints.get(int(arg), 0.0):+.3f}"
        if head == "HTR?":
            return f"{self.heaters.get(int(arg), 0.0):+.1f}"
        if head == "AOUT?":
            return "+000.0"
        if head == "RANGE?":
            return "3" if int(arg) == 2 else "0"
        if head == "PID?":
            return "+050.0,+020.0,+000.0"
        raise TransportError(f"Sim336 does not implement {cmd!r}")


def build_simulated_rig(**kw) -> tuple[SimulatedRig, Sim218, Sim336]:
    rig = SimulatedRig(**kw)
    return rig, Sim218(rig), Sim336(rig)
