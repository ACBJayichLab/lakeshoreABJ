import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ltspm3.control import (
    HeaterSupervisor, LoopMode, PIDConfig, SensorGuardConfig, SupervisorConfig,
)
from lschart.instruments import LS218
from lschart.instruments.sim import Sim218, SimulatedCryostat
from ltspm3.sim_response import LTSPM3_AUX_COUPLING, ResponseParams, ThermalModel
from lschart.transport import LoopbackTransport


class VirtualClock:
    """Drives both the response and the supervisor so tests run in microseconds."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


class Harness:
    """Closed loop: simulator -> LS218 driver -> supervisor -> simulator."""

    DT = 4.0  # the cryostat's real poll cadence

    def __init__(self, *, start_k=None, sup_cfg=None, pid_cfg=None, guard_cfg=None,
                 filter_kwargs=None, response=None):
        self.clock = VirtualClock()
        params = response or ResponseParams()
        # Start in equilibrium at the operating point: a cryostat still drifting
        # several kelvin is a genuine anomaly and would mask the real tests.
        self.equilibrium_k = params.steady_state(63.076)
        if start_k is None:
            start_k = self.equilibrium_k
        # SimulatedCryostat takes a response *object* now, not parameters: the generic
        # simulator has no idea which cryostat it is pretending to be, so the
        # calibrated model and the measured cross-channel couplings are both
        # injected from here.
        self.cryostat = SimulatedCryostat(
            ThermalModel(params, start_k=start_k),
            start_k=start_k,
            time_source=self.clock,
            seed=7,
            aux_coupling=LTSPM3_AUX_COUPLING,
        )
        self.sim = Sim218(self.cryostat)
        self.cryostat.response.pct = self.sim.analog_pct
        # All three populated inputs, as on the real cryostat.  The ancillary
        # channels are not decoration: cross-channel corroboration is what
        # separates a fast cooldown from a sick sensor.
        self.inst = LS218(
            LoopbackTransport(self.sim),
            channels={1: "Sample", 2: "Coldplate", 3: "Magnet"},
            # What an armed LTSPM3 config has to say, and for the same two
            # reasons.  `allow_writes` because the 218's analog output is the
            # sample heater and the driver now gates it like any other heater;
            # `verify_writes` off because the supervisor confirms its own
            # writes (`SupervisorConfig.verify_readback`) and paying twice
            # would put a second transaction in every control cycle.
            allow_writes=True,
            verify_writes=False,
        )
        self.sup = HeaterSupervisor(
            self.inst,
            channel="Sample",
            config=sup_cfg or SupervisorConfig(),
            pid_config=pid_cfg or PIDConfig(setpoint=self.equilibrium_k, kp=0.02, ti=900.0),
            guard_config=guard_cfg or SensorGuardConfig(),
            filter_kwargs=filter_kwargs or {"tau": 60.0},
            clock=self.clock,
        )
        self.history = []

    def read(self):
        readings, _ = self.inst.read_frame()
        return readings

    def step(self, n=1, dt=None):
        dt = dt or self.DT
        last = None
        for _ in range(n):
            self.clock.advance(dt)
            readings = self.read()
            last = self.sup.step(self.clock.t, readings.get("Sample"), readings)
            self.history.append(last)
        return last

    def settle_filter(self, n=40):
        """Prime the filter/guard before the loop is armed."""
        return self.step(n)


@pytest.fixture
def harness():
    return Harness


@pytest.fixture
def armed(harness):
    """A harness with the filter primed and the loop actually closed.

    Almost every control test starts here, so it lives with `Harness` rather
    than being copied into each file -- four byte-identical copies is four
    places to forget when the arming sequence changes.
    """
    def build(**kw):
        h = harness(**kw)
        h.settle_filter(40)
        h.sup.set_mode(LoopMode.PID)
        h.step(10)
        return h

    return build


@pytest.fixture
def clock():
    return VirtualClock()
