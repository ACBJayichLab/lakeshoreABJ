"""In-process fakes for the Lake Shore boxes, plus a plain default response.

There is no hardware on the bench, so this is the primary development target.
The fakes answer the subset of each command set the real drivers use, and they
are deliberately *cryostat-agnostic*: what makes a simulated cryostat behave like
one particular cryostat is the response object handed to :class:`SimulatedCryostat`.

A response is anything with this shape::

    response.pct          float, written by the fake when the heater is commanded
    response.temperature  float, the true temperature right now
    response.advance(dt)  integrate dt seconds forward
    response.observe(rng) the temperature as the *sensor* would report it

:class:`FirstOrderResponse` is the default and is intentionally boring -- one pole
onto a linear steady state.  The calibrated LTSPM3 model lives in
:mod:`ltspm3.sim_response`, which builds a cryostat around this same class.

Faults are injected explicitly -- see :meth:`SimulatedCryostat.inject`.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from ..transport import TransportError


@dataclass
class SimpleResponseParams:
    """A single-pole thermal response with a linear steady state.

    Enough to exercise the recorder, the fakes and the IPC layer on any cryostat.
    It is not calibrated to anything, and it is not meant to be: a controller
    tuned against this has learned nothing about a real cryostat.
    """

    t_bath: float = 4.0
    gain_k_per_pct: float = 1.5     # steady-state K per output percent
    tau_s: float = 300.0
    noise_k: float = 0.002
    quantum_k: float = 0.001        # reported resolution


class FirstOrderResponse:
    """``T -> t_bath + gain*pct`` through one pole, integrated exactly.

    Exact exponential updates rather than Euler, so the step size never affects
    stability -- the tests drive this with a virtual clock at wildly varying dt.
    """

    def __init__(self, params: SimpleResponseParams | None = None, *,
                 start_k: float | None = None) -> None:
        self.p = params or SimpleResponseParams()
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
    """Knobs the tests and the viewer's 'fault drill' use to abuse the pipeline.

    ``glitch_channels`` is the one that matters.  It reproduces the failure
    actually present in the reference logs -- 9 events in 1,510 h, always on
    the sample input -- rather than the tidy drop-to-zero this simulator used
    to model, which has never once occurred on this cryostat:

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


class SimulatedCryostat:
    """Holds the shared clock, the response and the fault state for both fakes."""

    #: Resting values for the ancillary channels, so a chart has something to
    #: show.  Plausible rather than calibrated -- they are scenery.
    DEFAULT_AUX_BASE = {
        "218.2": 8.06, "218.3": 6.72,
        "336.A": 38.2, "336.B": 290.6, "336.C": 28.56, "336.D": 3.95,
        "335.B": 77.4,
    }

    #: Channels whose value *is* the cryostat rather than scenery around it.  On a
    #: 335 the sample sits on input A, so that is the one the heater moves.
    CONTROL_KEYS = ("218.1", "335.A")

    def __init__(
        self,
        response=None,
        *,
        seed: int = 0xC01D,
        start_k: float = 96.0,
        time_source=time.monotonic,
        speedup: float = 1.0,
        aux_base: dict[str, float] | None = None,
        aux_coupling: dict[str, float] | None = None,
    ) -> None:
        # A cryostat with no response gets the boring one.  Anything cryostat-specific --
        # the LTSPM3 two-pole model, say -- is injected, so this module never
        # has to know which cryostat it is pretending to be.
        self.response = response if response is not None else FirstOrderResponse(start_k=start_k)
        self.rng = random.Random(seed)
        self.faults = FaultInjection()
        self.speedup = speedup
        self._time = time_source
        self._t0 = self._time()
        self._last = self._t0
        self._aux_base = dict(aux_base) if aux_base else dict(self.DEFAULT_AUX_BASE)
        #: How strongly each ancillary channel follows the control channel, in
        #: K per K.  Empty by default: on an unknown cryostat we have no business
        #: inventing a correlation, and cross-channel corroboration should see
        #: nothing rather than something fictitious.  The LTSPM3 numbers,
        #: measured from the reference logs, are in :mod:`ltspm3.sim_response`.
        self._aux_coupling = dict(aux_coupling) if aux_coupling else {}
        self._response_ref_k = self.response.temperature
        self._stuck_values: dict[str, float] = {}

    # -- clock -------------------------------------------------------------

    def tick(self) -> None:
        now = self._time()
        dt = (now - self._last) * self.speedup
        self._last = now
        self.response.advance(dt)

    def inject(self, **kw) -> None:
        """``cryostat.inject(dropout_channels={'Sample'}, comms_fail=True)``"""
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

    def aux_base(self, key: str) -> float | None:
        """Resting value for an ancillary channel; ``None`` means "this is the
        control channel", which is how :meth:`value` decides whether to read it."""
        if key in self.CONTROL_KEYS:
            return None
        return self._aux_base.get(key, 0.0)

    def value(self, key: str, base: float | None = None) -> float:
        if key in self.faults.dropout_channels:
            return self.faults.dropout_value
        if key in self.faults.glitch_channels:
            true_k = self.response.temperature if base is None else base
            hi = self.faults.glitch_high_k
            hi = true_k if hi is None else hi
            lo = min(self.faults.glitch_low_k, hi)
            return round(self.rng.uniform(lo, hi), 3)
        if key in self.faults.stuck_channels:
            return self._stuck_values.setdefault(key, base if base is not None else 0.0)
        if base is None:
            v = self.response.observe(self.rng)
        else:
            coupled = base + self._aux_coupling.get(key, 0.0) * (
                self.response.temperature - self._response_ref_k
            )
            v = coupled + self.rng.gauss(0, 0.002)
        if self.faults.extra_noise_k:
            v += self.rng.gauss(0.0, self.faults.extra_noise_k)
        return round(v, 4)

    def rdgst(self, key: str) -> int:
        return self.faults.rdgst_channels.get(key, 0)


class Sim218:
    """Answers the subset of the 218 command set that :class:`LS218` uses."""

    def __init__(self, cryostat: SimulatedCryostat) -> None:
        self.cryostat = cryostat
        self.analog_pct = 63.076
        self.analog_settings = [1, 0, 2, 1, 1, 1, 1, self.analog_pct]
        self.write_log: list[str] = []
        #: The instrument's own DAC resolution.  0.01% here is ~75 mK on this
        #: response, which is exactly why the supervisor dithers.
        self.dac_step = 0.01

    def handle_write(self, cmd: str) -> None:
        self.cryostat._guard_comms()
        self.write_log.append(cmd)
        head, _, args = cmd.partition(" ")
        if head.upper() == "ANALOG":
            parts = [p.strip() for p in args.split(",")]
            if len(parts) != 8:
                raise TransportError(f"malformed ANALOG command: {cmd!r}")
            requested = float(parts[7])
            quantised = round(requested / self.dac_step) * self.dac_step
            self.analog_pct = min(100.0, max(0.0, quantised))
            self.cryostat.response.pct = self.analog_pct

    def handle_query(self, cmd: str) -> str:
        self.cryostat._guard_comms()
        self.cryostat.tick()
        head, _, arg = cmd.partition(" ")
        head = head.upper()
        arg = arg.strip()
        if head == "*IDN?":
            return "LSCI,MODEL218,SIM0001,1.0"
        if head == "KRDG?":
            if arg == "0":
                vals = [
                    self.cryostat.value("218.1"),
                    self.cryostat.value("218.2", self.cryostat.aux_base("218.2")),
                    self.cryostat.value("218.3", self.cryostat.aux_base("218.3")),
                    0.0, 0.0, 0.0, 0.0, 0.0,
                ]
                return ",".join(f"{v:+09.4f}" for v in vals)
            return f"{self.cryostat.value(f'218.{arg}'):+09.4f}"
        if head == "SRDG?":
            return ",".join("+0.00000" for _ in range(8))
        if head == "RDGST?":
            return str(self.cryostat.rdgst(f"218.{arg}"))
        if head == "AOUT?":
            return f"{self.analog_pct:+07.2f}"
        if head == "ANALOG?":
            return ",".join(str(x) for x in self.analog_settings[:7]) + f",{self.analog_pct:.3f}"
        raise TransportError(f"Sim218 does not implement {cmd!r}")


class Sim33x:
    """Answers the subset of the 33x command set that :class:`LS33x` uses.

    One fake for both models; what differs is how many inputs, loops and
    heaters it admits to having, which comes from the same capability table the
    real driver uses -- so a test cannot accidentally exercise a loop the
    instrument does not have.
    """

    DEFAULT_NAMES = {
        "336": {"A": "RAD SHIELD", "B": "THE CHONKE", "C": "1st Stage", "D": "2nd Stage"},
        "335": {"A": "Sample", "B": "Cold Head"},
    }

    def __init__(self, cryostat: SimulatedCryostat, *, model: str = "336") -> None:
        from .ls33x import CAPS

        self.cryostat = cryostat
        self.model = str(model)
        self.caps = CAPS[self.model]
        self.names = dict(self.DEFAULT_NAMES.get(self.model, {}))
        self.setpoints = {loop: 0.0 for loop in self.caps.loops}
        self.heaters = {out: 0.0 for out in self.caps.heater_outputs}
        self.ranges = {out: 0 for out in self.caps.heater_outputs}
        self.pids = {loop: (50.0, 20.0, 0.0) for loop in self.caps.loops}
        self.ramps = {loop: (0, 0.0) for loop in self.caps.loops}
        self.write_log: list[str] = []
        if self.model == "336":
            # The LTSPM3 336: loop 2 independently holds THE CHONKE, heater near
            # full range.  Anything that disturbs this in a test is a bug.
            self.setpoints[2] = 290.6
            self.heaters[2] = 97.9
            self.ranges[2] = 3

    def handle_write(self, cmd: str) -> None:
        self.cryostat._guard_comms()
        self.write_log.append(cmd)
        head, _, args = cmd.partition(" ")
        head = head.upper()
        parts = [p.strip() for p in args.split(",")]
        if head == "SETP":
            self.setpoints[int(parts[0])] = float(parts[1])
        elif head == "RANGE":
            self.ranges[int(parts[0])] = int(float(parts[1]))
        elif head == "PID":
            self.pids[int(parts[0])] = tuple(float(x) for x in parts[1:4])
        elif head == "RAMP":
            self.ramps[int(parts[0])] = (int(float(parts[1])), float(parts[2]))

    def handle_query(self, cmd: str) -> str:
        self.cryostat._guard_comms()
        self.cryostat.tick()
        head, _, arg = cmd.partition(" ")
        head = head.upper()
        arg = arg.strip()
        if head == "*IDN?":
            return f"LSCI,MODEL{self.model},SIM0002,1.0"
        if head == "KRDG?":
            keys = [f"{self.model}.{letter}" for letter in self.caps.inputs]
            if arg == "0":
                return ",".join(
                    f"{self.cryostat.value(k, self.cryostat.aux_base(k)):+09.4f}" for k in keys
                )
            k = f"{self.model}.{arg}"
            return f"{self.cryostat.value(k, self.cryostat.aux_base(k)):+09.4f}"
        if head == "RDGST?":
            return str(self.cryostat.rdgst(f"{self.model}.{arg}"))
        if head == "INNAME?":
            return self.names.get(arg, f"Input {arg}")
        if head == "SETP?":
            return f"{self.setpoints.get(int(arg), 0.0):+.3f}"
        if head == "HTR?":
            return f"{self.heaters.get(int(arg), 0.0):+.1f}"
        if head == "RANGE?":
            return str(self.ranges.get(int(arg), 0))
        if head == "AOUT?":
            return "+000.0"
        if head == "PID?":
            p, i, d = self.pids.get(int(arg), (0.0, 0.0, 0.0))
            return f"{p:+07.1f},{i:+07.1f},{d:+07.1f}"
        if head == "RAMP?":
            on, rate = self.ramps.get(int(arg), (0, 0.0))
            return f"{on},{rate:+.3f}"
        if head == "RAMPST?":
            return "0"
        raise TransportError(f"Sim33x({self.model}) does not implement {cmd!r}")


class Sim336(Sim33x):
    """The LTSPM3 336, for callers that want it by name."""

    NAMES = Sim33x.DEFAULT_NAMES["336"]

    def __init__(self, cryostat: SimulatedCryostat) -> None:
        super().__init__(cryostat, model="336")


class Sim335(Sim33x):
    def __init__(self, cryostat: SimulatedCryostat) -> None:
        super().__init__(cryostat, model="335")


def build_simulated_cryostat(**kw) -> tuple[SimulatedCryostat, Sim218, Sim336]:
    cryostat = SimulatedCryostat(**kw)
    return cryostat, Sim218(cryostat), Sim336(cryostat)
