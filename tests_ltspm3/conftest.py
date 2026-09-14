import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ltspm3.control import (
    HeaterSupervisor, LoopMode, PIDConfig, SensorGuardConfig, SupervisorConfig,
)
from lschart.instruments import LS218
from lschart.instruments.sim import Sim218, SimulatedCryostat
from ltspm3.model.sim_response import LTSPM3_AUX_COUPLING, ResponseParams, ThermalModel
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
                 filter_kwargs=None, response=None, model=None):
        self.clock = VirtualClock()
        params = response or ResponseParams()
        # Start in equilibrium at the operating point: a cryostat still drifting
        # several kelvin is a genuine anomaly and would mask the real tests.
        self.equilibrium_k = params.steady_state(63.076)
        if start_k is None:
            start_k = self.equilibrium_k
        # `model` is a whole response OBJECT rather than the parameters for the
        # two-pole one -- the seam `FittedResponse` arrives through, and the
        # only reason this class knows phase 3 exists.
        plant = model if model is not None else ThermalModel(params, start_k=start_k)
        # SimulatedCryostat takes a response *object* now, not parameters: the generic
        # simulator has no idea which cryostat it is pretending to be, so the
        # calibrated model and the measured cross-channel couplings are both
        # injected from here.
        self.cryostat = SimulatedCryostat(
            plant,
            start_k=start_k,
            time_source=self.clock,
            seed=7,
            aux_coupling=LTSPM3_AUX_COUPLING,
        )
        self.sim = Sim218(self.cryostat)
        self.cryostat.response.pct = self.sim.analog_pct
        # All three populated inputs, as on the real cryostat -- and at the
        # input numbers the real cryostat uses, which since 2026-09-04 skip 3
        # because the magnet moved to input 5.  The gap is the point: it is the
        # harness's only guard that nothing downstream assumes inputs 1..N.
        # The ancillary channels are not decoration either: cross-channel
        # corroboration is what separates a fast cooldown from a sick sensor.
        self.inst = LS218(
            LoopbackTransport(self.sim),
            channels={1: "Sample", 2: "Coldplate", 5: "Magnet"},
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


# -- the phase 3 bench -------------------------------------------------------
#
# A SECOND harness, on the FITTED plant.  `Harness` above runs
# `sim_response`'s two-pole model, whose one tau of 620 s came from a single
# step at 137 K -- right there and wrong everywhere else -- and every control
# test written before phase 1 is calibrated to it.  Those tests stay on it:
# re-pointing them would silently re-grade a hundred assertions against a
# different cryostat.
#
# What phase 3 has to prove is different.  It is that the loop works at 10 K
# and at 180 K, where tau runs from under a second to 611 s and the gain spans
# forty-fold, and only the fitted table knows that.  So: same wiring, same
# virtual clock, a different plant -- and the LIMITS come from
# `config-ltspm3-armed.yaml` rather than from a literal in a test, because the
# numbers the bench proves have to be the numbers the cryostat would run.

BENCH_CONFIG = Path(__file__).resolve().parents[1] / "config-ltspm3-armed.yaml"

#: The six the plan grades at.  They are not evenly spaced in kelvin because
#: nothing about this cryostat is: 10 and 30 K are where the delay floor binds
#: and the watt residual has no opinion, 60 K is where the plant's own tau
#: overtakes the filter, and 100-180 K is the only part anybody has run.
BENCH_TEMPERATURES = (10.0, 30.0, 60.0, 100.0, 140.0, 180.0)


def bench_control_config():
    """The `control:` section of the armed config, loaded once per call.

    Loaded through `lschart.config.load`, not read as YAML, so the bench proves
    the file the cryostat would be given -- unknown keys, validators and all.
    """
    import ltspm3.config  # noqa: F401  -- registers `control:` and `monitor:`
    from lschart.config import load

    return load(str(BENCH_CONFIG)).section("control")


class FittedHarness(Harness):
    """`Harness` on `FittedResponse`, at a chosen temperature.

    Two differences beyond the plant, and both are deliberate:

    * the cadence is the config's **2 s**, not `Harness.DT`'s 4.  The loop's
      dead time is derived from the cadence, so a bench that runs at twice the
      real period is proving a loop with twice the delay floor.
    * the authority band is re-centred on the scenario's own operating point.
      **That is the bench doing what the cryostat cannot**, and it is exactly
      what step 4 removes: `HeaterSupervisor.band` is two config constants and
      `_apply_band_to_pid()` runs once, in `__init__`, so a band centred at
      63.96 % cannot reach 24.22 % where 10 K lives.  Until step 4 lands, every
      scenario here is a loop that was handed the right window in advance.
    """

    DT = 2.0

    def __init__(self, *, kelvin: float, sup_cfg=None, pid_cfg=None,
                 guard_cfg=None, filter_kwargs=None, **kw):
        import dataclasses

        from ltspm3.model.fitted_response import FittedResponse

        cfg = bench_control_config()
        self.bench_k = float(kelvin)
        # Start the plant AT the output that holds this temperature, so the
        # cryostat is in equilibrium on the first cycle.  One several kelvin
        # from it is a genuine anomaly and would mask every test here.
        plant = FittedResponse(start_k=self.bench_k)
        self.bench_pct = plant.percent_for(self.bench_k)
        plant.pct = self.bench_pct

        sup = sup_cfg or dataclasses.replace(
            cfg.supervisor, operating_point_pct=self.bench_pct)
        pid = pid_cfg or dataclasses.replace(cfg.pid, setpoint=self.bench_k)

        super().__init__(sup_cfg=sup, pid_cfg=pid,
                         guard_cfg=guard_cfg or cfg.guard,
                         filter_kwargs=filter_kwargs or dict(cfg.filter),
                         start_k=self.bench_k, model=plant, **kw)

        # `Harness` sets the plant's output from the simulator's DAC, which
        # starts at zero.  Put the heater where this scenario says instead --
        # on both sides, so the loop's belief and the instrument agree before
        # the first cycle rather than after the first write.
        self.sim.analog_pct = self.bench_pct
        plant.pct = self.bench_pct
        self.sup.output_pct = self.bench_pct
        self.sup.manual_pct = self.bench_pct
        self.equilibrium_k = self.bench_k

    @property
    def plant(self):
        return self.cryostat.response

    def minutes(self, n, dt=None):
        """`n` minutes of closed loop, at the bench cadence."""
        dt = dt or self.DT
        return self.step(int(round(n * 60.0 / dt)), dt=dt)

    def outputs(self):
        return [s.output_pct for s in self.history if s.output_pct is not None]


@pytest.fixture
def bench():
    """A fitted-plant harness, filter primed and the loop closed."""
    def build(kelvin, *, prime=60, settle=10, **kw):
        h = FittedHarness(kelvin=kelvin, **kw)
        h.settle_filter(prime)
        h.sup.set_mode(LoopMode.PID)
        h.step(settle)
        return h

    return build


@pytest.fixture
def bench_cold():
    """The same, NOT armed -- for tests about arming itself."""
    def build(kelvin, **kw):
        return FittedHarness(kelvin=kelvin, **kw)

    return build
