import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lschart.control import HeaterSupervisor, PIDConfig, SensorGuardConfig, SupervisorConfig
from lschart.instruments import LS218
from lschart.instruments.sim import PlantParams, Sim218, SimulatedRig
from lschart.transport import LoopbackTransport


class VirtualClock:
    """Drives both the plant and the supervisor so tests run in microseconds."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


class Harness:
    """Closed loop: simulator -> LS218 driver -> supervisor -> simulator."""

    DT = 4.0  # the rig's real poll cadence

    def __init__(self, *, start_k=None, sup_cfg=None, pid_cfg=None, guard_cfg=None,
                 filter_kwargs=None, plant=None):
        self.clock = VirtualClock()
        plant = plant or PlantParams()
        # Start in equilibrium at the operating point: a rig still drifting
        # several kelvin is a genuine anomaly and would mask the real tests.
        self.equilibrium_k = plant.steady_state(63.076)
        if start_k is None:
            start_k = self.equilibrium_k
        self.rig = SimulatedRig(
            plant, start_k=start_k, time_source=self.clock, seed=7
        )
        self.sim = Sim218(self.rig)
        self.rig.plant.pct = self.sim.analog_pct
        # All three populated inputs, as on the real rig.  The ancillary
        # channels are not decoration: cross-channel corroboration is what
        # separates a fast cooldown from a sick sensor.
        self.inst = LS218(
            LoopbackTransport(self.sim),
            channels={1: "Sample", 2: "Cold Head", 3: "Shield"},
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
def clock():
    return VirtualClock()
