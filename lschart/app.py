"""Build a running system from an :class:`~lschart.config.AppConfig`.

This is the only place that knows how the pieces fit together, and the only
place that decides whether a transport is real hardware or the simulator.
Everything downstream is handed objects and never asks where they came from --
which is what makes "going live is a config edit" true rather than aspirational.
"""

from __future__ import annotations

import logging

from .acquisition.poller import Poller
from .acquisition.recorder import Recorder
from .acquisition.ringbuffer import RingBuffer
from .config import AppConfig, LS218Config, LS336Config, TransportConfig
from .control.supervisor import HeaterSupervisor, LoopMode
from .instruments.ls218 import AnalogOutputConfig, LS218
from .instruments.ls336 import LS336
from .transport import LoopbackTransport, Transport

log = logging.getLogger(__name__)


def build_transport(cfg: TransportConfig, *, device=None) -> Transport:
    """``sim`` needs a loopback onto a fake device; ``visa`` needs a resource.

    ``pyvisa`` is imported inside :class:`~lschart.transport.VisaTransport`, so
    a sim deployment runs on a machine with no VISA runtime at all -- which is
    every development machine here.
    """
    if cfg.backend == "sim":
        if device is None:
            raise ValueError("sim backend requires a simulated device")
        # No pacing in simulation.  inter_command_delay exists to be kind to a
        # GPIB board; applying it to an in-process fake just makes every cycle
        # cost 9 x 50 ms for nothing, and makes the measured cadence disagree
        # with what AppConfig.estimated_cycle_s() predicts for a sim run.
        return LoopbackTransport(device, inter_command_delay=0.0)
    if cfg.backend == "visa":
        from .transport import VisaTransport

        return VisaTransport(
            cfg.resource,
            timeout_ms=cfg.timeout_ms,
            read_termination=cfg.read_termination,
            write_termination=cfg.write_termination,
            inter_command_delay=cfg.inter_command_delay,
            visa_library=cfg.visa_library,
            baud_rate=cfg.baud_rate,
            data_bits=cfg.data_bits,
            parity=cfg.parity,
        )
    raise ValueError(f"unknown transport backend {cfg.backend!r}")


class Application:
    """Everything wired together, with a lifecycle."""

    def __init__(self, cfg: AppConfig) -> None:
        cfg.validate()
        self.cfg = cfg
        self.rig = None                 # SimulatedRig, when simulating
        self.instruments: list = []
        self.ls218: LS218 | None = None
        self.ls336: LS336 | None = None
        self.supervisor: HeaterSupervisor | None = None
        self.recorder: Recorder | None = None
        self.ring = RingBuffer(cfg.acquisition.ringbuffer_size)
        self.poller: Poller | None = None
        self._build()

    # -- construction ------------------------------------------------------

    def _simulated_devices(self):
        """One shared plant behind both fakes, so their channels agree."""
        from .instruments.sim import Sim218, Sim336, SimulatedRig

        if self.rig is None:
            self.rig = SimulatedRig(
                start_k=self.cfg.sim.start_k,
                seed=self.cfg.sim.seed,
                speedup=self.cfg.sim.speedup,
            )
            self._sim218 = Sim218(self.rig)
            self._sim336 = Sim336(self.rig)
            self.rig.plant.pct = self._sim218.analog_pct
        return self._sim218, self._sim336

    def _build_218(self, c: LS218Config) -> LS218:
        device = self._simulated_devices()[0] if c.transport.backend == "sim" else None
        return LS218(
            build_transport(c.transport, device=device),
            name=c.name,
            channels=dict(c.channels),
            read_status=c.read_status,
            analog=AnalogOutputConfig(
                output=c.analog_output, decimals=c.analog_decimals
            ),
        )

    def _build_336(self, c: LS336Config) -> LS336:
        device = self._simulated_devices()[1] if c.transport.backend == "sim" else None
        return LS336(
            build_transport(c.transport, device=device),
            name=c.name,
            channels=dict(c.channels) or None,
            read_status=c.read_status,
            read_setpoints=c.read_setpoints,
            read_heaters=c.read_heaters,
            read_analog_outputs=c.read_analog_outputs,
            allow_writes=c.allow_writes,
        )

    def _aux_columns(self) -> list[str]:
        """Auxiliary scalars worth a column, in a stable order.

        The legacy logs carried the 336's setpoints and heaters alongside the
        temperatures and analysis scripts expect them, so keep them.
        """
        cols = ["heater_pct"]
        c = self.cfg
        if c.ls218.enabled:
            cols.append(f"{c.ls218.name}.aout{c.ls218.analog_output}")
        if c.ls336.enabled:
            if c.ls336.read_setpoints:
                cols += [f"{c.ls336.name}.setpoint{i}" for i in (1, 2, 3, 4)]
            if c.ls336.read_heaters:
                cols += [f"{c.ls336.name}.heater{i}" for i in (1, 2)]
            if c.ls336.read_analog_outputs:
                cols += [f"{c.ls336.name}.aout{i}" for i in (3, 4)]
        return cols

    def _build(self) -> None:
        cfg = self.cfg
        if cfg.ls218.enabled:
            self.ls218 = self._build_218(cfg.ls218)
            self.instruments.append(self.ls218)
        if cfg.ls336.enabled:
            self.ls336 = self._build_336(cfg.ls336)
            self.instruments.append(self.ls336)

        if cfg.recorder.enabled:
            self.recorder = Recorder(
                cfg.recorder.directory,
                prefix=cfg.recorder.filename_prefix,
                channels=list(cfg.ls218.channels.values()) if cfg.ls218.enabled else [],
                aux_keys=self._aux_columns(),
                flush_every_sample=cfg.recorder.flush_every_sample,
            )

        if cfg.control.enabled and cfg.sim.speedup != 1.0 and not cfg.uses_hardware:
            log.warning(
                "sim.speedup=%.1f accelerates the plant but NOT the controller, "
                "which still integrates in real time -- closed-loop behaviour in "
                "this run does not represent the rig.  Use the virtual-clock "
                "harness in tests/ for accelerated closed-loop work.",
                cfg.sim.speedup,
            )
        if cfg.control.enabled:
            if self.ls218 is None:
                raise ValueError("control.enabled requires ls218.enabled -- the "
                                 "sample heater is the 218's analog output")
            self.supervisor = HeaterSupervisor(
                self.ls218,
                channel=cfg.control_channel,
                config=cfg.control.supervisor,
                pid_config=cfg.control.pid,
                guard_config=cfg.control.guard,
                coherence_config=cfg.control.coherence,
                ramp_config=cfg.control.ramp,
                tuning_config=cfg.control.tuning,
                feedforward_config=cfg.control.feedforward,
                filter_kwargs=dict(cfg.control.filter),
            )

        self.poller = Poller(
            self.instruments,
            interval_s=cfg.acquisition.interval_s,
            recorder=self.recorder,
            ringbuffer=self.ring,
            supervisor=self.supervisor,
            control_channel=cfg.control_channel if cfg.ls218.enabled else None,
            log_every_n=cfg.acquisition.log_every_n,
            status_every_n_cycles=cfg.ls218.status_every_n_cycles,
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        assert self.poller is not None
        self.poller.start()

    def stop(self) -> None:
        if self.poller is not None:
            self.poller.stop()
        # Order matters: the supervisor decides what the heater is left doing
        # before the log closes, so the decision is recorded.
        if self.supervisor is not None:
            self.supervisor.shutdown()
        if self.recorder is not None:
            self.recorder.close()

    def arm(self, setpoint_k: float | None = None) -> None:
        """Close the loop.  Explicit, never automatic on startup.

        With no setpoint, arm to *hold the temperature the rig is at now*.
        That is what arming means to an operator, and it is the only choice
        that is bumpless: adopting a stale configured setpoint instead asks the
        loop to move somewhere the heater is not currently set for, which the
        premise check correctly reads as a broken premise and refuses.
        Deliberate moves are sweeps -- see HeaterSupervisor.sweep_to.
        """
        if self.supervisor is None:
            raise RuntimeError("control is not enabled in this configuration")
        if setpoint_k is None:
            here = self.current_temperature()
            if here is None:
                raise RuntimeError(
                    "cannot arm: no usable reading yet for "
                    f"{self.cfg.control_channel!r}. Poll first, or pass a setpoint."
                )
            setpoint_k = here
            log.warning("arming to hold the present temperature, %.4f K", setpoint_k)
        self.supervisor.set_setpoint(setpoint_k, ramp=False)
        self.supervisor.set_mode(LoopMode.PID)

    def current_temperature(self) -> float | None:
        """Latest usable reading on the control channel, or None."""
        frame = self.ring.latest()
        if frame is None:
            return None
        return frame.kelvin(self.cfg.control_channel)

    def __enter__(self) -> "Application":
        self.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False
