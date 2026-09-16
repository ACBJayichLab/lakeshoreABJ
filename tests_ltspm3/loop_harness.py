"""The closed-loop test harness: simulator -> LS218 -> supervisor -> simulator.

**Not `conftest.py`, and the name matters.**  `tests/` and `tests_ltspm3/` are
both directories without `__init__.py`, so pytest puts each on `sys.path` and
the module name `conftest` means whichever of the two was imported first.  A
test module doing `from conftest import ...` therefore passes under
`pytest -q` and fails under `pytest tests_ltspm3 tests` -- the same
depends-on-how-you-ran-it fragility CLAUDE.md's "a test must not depend on the
working directory" rule exists for, and it bit during phase 3 step 2.  A
uniquely named module has no such ambiguity.

`conftest.py` holds the fixtures and imports these classes from here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ltspm3.control import (
    HeaterSupervisor, PIDConfig, SensorGuardConfig, SupervisorConfig,
)
from ltspm3.control.feedforward import FeedforwardConfig
from lschart.instruments import LS218
from lschart.instruments.sim import Sim218, SimulatedCryostat
from ltspm3.model.fitted_response import DRIFT_T0_UNIX
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

    #: **The bench's wall clock, pinned.**  The supervisor needs unix seconds
    #: for one thing only -- the age of the band's drift term -- and a bench
    #: that reads the real calendar grades differently every day it runs: 3
    #: sigma at 118 K is 1.44 mW on the gauge day, 8.7 ten days later and 52 at
    #: +60 d.  So the default is the day the level was gauged, and a test that
    #: wants to prove a fault still faults two months on passes
    #: `wall_t0=DRIFT_T0_UNIX + 60 * 86400` rather than waiting for November.
    WALL_T0 = DRIFT_T0_UNIX

    def __init__(self, *, start_k=None, sup_cfg=None, pid_cfg=None, guard_cfg=None,
                 filter_kwargs=None, response=None, model=None, cadence_s=None,
                 ff_cfg=None, tuning_cfg=None, aux_base=None, aux_coupling=None,
                 wall_t0=None):
        self.clock = VirtualClock()
        self.wall_t0 = self.WALL_T0 if wall_t0 is None else float(wall_t0)
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
            aux_base=aux_base,
            aux_coupling=aux_coupling or LTSPM3_AUX_COUPLING,
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
            # **THE WATT PREMISE IS SWITCHED OFF IN THIS HARNESS**, and the
            # reason is the same one that pairs it with the CD10 curve above:
            # its plant is `sim_response`'s two-pole model, which is not the
            # cryostat `model/fitted_response` describes.  `dQ` compares a
            # measured sample against THAT fit, so here it correctly reports a
            # mismatch of tens of milliwatts -- about the simulator, not about
            # anything these tests are asking.  `min_output_pct: 100` is "never
            # an opinion", which is the honest way to say it.
            #
            # The phase 3 bench runs the fitted plant and leaves it on; that is
            # where the residual is tested.
            config=sup_cfg or SupervisorConfig(min_output_pct=100.0),
            pid_config=pid_cfg or PIDConfig(setpoint=self.equilibrium_k, kp=0.02, ti=900.0),
            guard_config=guard_cfg or SensorGuardConfig(),
            # The SHIPPED chain, not a literal.  It was `{"tau": 60.0}` until
            # phase 3 step 1 switched the low pass off; leaving it pinned here
            # would have left every safety and glitch test in this directory
            # exercising a filter the cryostat no longer runs.
            filter_kwargs=filter_kwargs or {},
            # THE PLANT AND THE FEEDFORWARD COME FROM THE SAME MEASUREMENTS.
            # This harness runs `sim_response`'s two-pole CD10 model, so its
            # controller gets the CD10 curve; the phase 3 bench runs the fitted
            # plant and gets the fitted curve.  Pairing them the other way
            # would put up to 13 K of model error into every test that is
            # about something else -- a mismatch a test should have to opt
            # into, which is what `ff_cfg` is for.
            feedforward_config=ff_cfg or FeedforwardConfig(source="cd10"),
            # **THE TUNER IS A STAGE SWITCH TOO, and this harness could not
            # say so until 2026-09-16**: it passed no `tuning_config` at all,
            # so every test here ran `TuningConfig()` -- enabled -- whatever
            # the cryostat was armed with.  `control.tuning.enabled: false` is
            # 4a, and with it off the output rate limiter sits on its floor
            # (`_rate_pct_per_min`).  A bench that cannot be handed the
            # cryostat's own switch cannot grade that.
            tuning_config=tuning_cfg,
            cadence_s=cadence_s,
            clock=self.clock,
            # Virtual seconds since the gauge, so the band the loop judges by
            # is the same one on any day this suite runs.
            wall_clock=lambda: self.wall_t0 + self.clock.t,
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
