"""In-process fakes for the Lake Shore boxes, plus a plain default plant.

There is no hardware on the bench, so this is the primary development target.
The fakes answer the subset of each command set the real drivers use, and they
are deliberately *rig-agnostic*: what makes a simulated cryostat behave like
one particular cryostat is the plant object handed to :class:`SimulatedRig`.

A plant is anything with this shape::

    plant.pct          float, written by the fake when the heater is commanded
    plant.temperature  float, the true temperature right now
    plant.advance(dt)  integrate dt seconds forward
    plant.observe(rng) the temperature as the *sensor* would report it

:class:`FirstOrderPlant` is the default and is intentionally boring -- one pole
onto a linear steady state.  The calibrated LTSPM3 model lives in
:mod:`ltspm.sim_plant`, which builds a rig around this same class.

Faults are injected explicitly -- see :meth:`SimulatedRig.inject`.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from ..transport import TransportError


@dataclass
class SimplePlantParams:
    """A single-pole plant with a linear steady state.

    Enough to exercise the recorder, the fakes and the IPC layer on any rig.
    It is not calibrated to anything, and it is not meant to be: a controller
    tuned against this has learned nothing about a real cryostat.
    """

    t_bath: float = 4.0
    gain_k_per_pct: float = 1.5     # steady-state K per output percent
    tau_s: float = 300.0
    noise_k: float = 0.002
    quantum_k: float = 0.001        # reported resolution


class FirstOrderPlant:
    """``T -> t_bath + gain*pct`` through one pole, integrated exactly.

    Exact exponential updates rather than Euler, so the step size never affects
    stability -- the tests drive this with a virtual clock at wildly varying dt.
    """

    def __init__(self, params: SimplePlantParams | None = None, *,
                 start_k: float | None = None) -> None:
        self.p = params or SimplePlantParams()
        self._rise = (self.p.t_bath if start_k is None else start_k) - self.p.t_bath
        self.pct = 0.0

    @property
    def temperature(self) -> float:
        return self.p.t_bath + self._rise

    def steady_state(self, pct: float) -> float:
        return self.p.t_bath + self.p.gain_k_per_pct * max(pct, 0.0)

    def advance(self, dt: float) -> None:
        if dt <= 0:
            return
        target = self.steady_state(self.pct) - self.p.t_bath
        self._rise += (1.0 - math.exp(-dt / self.p.tau_s)) * (target - self._rise)

    def observe(self, rng: random.Random) -> float:
        noisy = self.temperature + rng.gauss(0.0, self.p.noise_k)
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

    #: Resting values for the ancillary channels, so a chart has something to
    #: show.  Plausible rather than calibrated -- they are scenery.
    DEFAULT_AUX_BASE = {
        "218.2": 8.06, "218.3": 6.72,
        "336.A": 38.2, "336.B": 290.6, "336.C": 28.56, "336.D": 3.95,
    }

    def __init__(
        self,
        plant=None,
        *,
        seed: int = 0xC01D,
        start_k: float = 96.0,
        time_source=time.monotonic,
        speedup: float = 1.0,
        aux_base: dict[str, float] | None = None,
        aux_coupling: dict[str, float] | None = None,
    ) -> None:
        # A rig with no plant gets the boring one.  Anything rig-specific --
        # the LTSPM3 two-pole model, say -- is injected, so this module never
        # has to know which cryostat it is pretending to be.
        self.plant = plant if plant is not None else FirstOrderPlant(start_k=start_k)
        self.rng = random.Random(seed)
        self.faults = FaultInjection()
        self.speedup = speedup
        self._time = time_source
        self._t0 = self._time()
        self._last = self._t0
        self._aux_base = dict(aux_base) if aux_base else dict(self.DEFAULT_AUX_BASE)
        #: How strongly each ancillary channel follows the control channel, in
        #: K per K.  Empty by default: on an unknown rig we have no business
        #: inventing a correlation, and cross-channel corroboration should see
        #: nothing rather than something fictitious.  The LTSPM3 numbers,
        #: measured from the reference logs, are in :mod:`ltspm.sim_plant`.
        self._aux_coupling = dict(aux_coupling) if aux_coupling else {}
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
