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
from .config import AppConfig, InstrumentConfig
from .instruments.ls218 import AnalogOutputConfig, LS218
from .instruments.ls33x import LS33x
from .ipc.commands import CommandSpool
from .ipc.service import IpcService
from .transport import LoopbackTransport, Transport

log = logging.getLogger(__name__)


def build_transport(cfg: InstrumentConfig, *, device=None) -> Transport:
    """One instrument's link, chosen by ``driver:``.

    Neither ``pyvisa`` nor ``lakeshore`` is imported until the driver that
    needs it is actually selected, so a sim deployment runs on a machine with
    neither installed -- which is every development machine here, and matters
    again for a coworker whose box is on a COM port and who therefore has no
    reason to install a VISA runtime.
    """
    t = cfg.transport
    recovery = dict(
        read_only=t.read_only,
        reconnect=t.reconnect,
        retry_min_s=t.retry_min_s,
        retry_max_s=t.retry_max_s,
        failures_before_reconnect=t.failures_before_reconnect,
    )
    if cfg.driver == "sim":
        if device is None:
            raise ValueError(f"{cfg.resolved_name()}: sim driver needs a simulated device")
        # No pacing in simulation.  inter_command_delay exists to be kind to a
        # GPIB board; applying it to an in-process fake just makes every cycle
        # cost 9 x 50 ms for nothing, and makes the measured cadence disagree
        # with what AppConfig.estimated_cycle_s() predicts for a sim run.
        #
        # `read_only` IS honoured here, and must be: rehearsing a read-only
        # config against the simulator is exactly how someone convinces
        # themselves it is safe before pointing it at a cryostat.  An interlock
        # that silently does nothing in rehearsal is worse than none.
        # Reconnection stays off -- there is no link to lose.
        return LoopbackTransport(
            device, inter_command_delay=0.0, read_only=t.read_only
        )

    if cfg.driver == "visa":
        from .transport import VisaTransport

        return VisaTransport(
            t.resource,
            timeout_ms=t.timeout_ms,
            read_termination=t.read_termination,
            write_termination=t.write_termination,
            inter_command_delay=t.inter_command_delay,
            visa_library=t.visa_library,
            baud_rate=t.baud_rate,
            data_bits=t.data_bits,
            parity=t.parity,
            **recovery,
        )

    if cfg.driver == "lakeshore":
        from .transport import LakeshoreTransport

        return LakeshoreTransport(
            cfg.model,
            com_port=t.com_port or None,
            serial_number=t.serial_number or None,
            ip_address=t.ip_address or None,
            baud_rate=t.baud_rate or 57600,
            timeout_ms=t.timeout_ms,
            inter_command_delay=t.inter_command_delay,
            tcp_port=t.tcp_port,
            **recovery,
        )

    raise ValueError(f"{cfg.resolved_name()}: unknown driver {cfg.driver!r}")


class Application:
    """Everything wired together, with a lifecycle."""

    def __init__(self, cfg: AppConfig, *, controller_factory=None,
                 response_factory=None) -> None:
        """``controller_factory`` and ``response_factory`` are the extension seams.

        The recorder knows nothing about software control loops or about any
        particular cryostat's thermal model, so both are injected:

        ``controller_factory(app) -> controller | None``
            Anything with ``step(t, reading, readings) -> status`` and
            ``shutdown()``.  The poller steps it once per frame and already
            isolates it -- an exception there is logged and logging continues.
        ``response_factory(cfg.sim) -> response``
            The simulated thermal response; see :mod:`lschart.instruments.sim`.  Defaults
            to the plain one-pole model.

        :mod:`ltspm3.app` passes both.
        """
        cfg.validate()
        self.cfg = cfg
        self._controller_factory = controller_factory
        self._response_factory = response_factory
        self.cryostat = None                 # SimulatedCryostat, when simulating
        self.instruments: list = []
        #: By config name, so a controller or a tool can find one box among
        #: several without caring what order they were declared in.
        self.by_name: dict = {}
        self.ls218: LS218 | None = None     # the first 218, if any
        self.supervisor = None          # set by controller_factory, if any
        self.recorder: Recorder | None = None
        self.ring = RingBuffer(cfg.acquisition.ringbuffer_size)
        #: status.json + the command spool.  None when `ipc.enabled` is false.
        self.ipc: IpcService | None = None
        self.poller: Poller | None = None
        self._build()

    # -- construction ------------------------------------------------------

    def _sim_device(self, cfg: InstrumentConfig):
        """A fake for one instrument, all sharing one response and one clock.

        The shared cryostat is what makes the fakes' channels agree with each other,
        which is what makes cross-channel corroboration testable at all.
        """
        from .instruments.sim import Sim218, Sim33x, SimulatedCryostat

        if self.cryostat is None:
            response = (
                self._response_factory(self.cfg.sim)
                if self._response_factory is not None else None
            )
            self.cryostat = SimulatedCryostat(
                response,
                start_k=self.cfg.sim.start_k,
                seed=self.cfg.sim.seed,
                speedup=self.cfg.sim.speedup,
            )
        if cfg.model == "218":
            dev = Sim218(self.cryostat)
            # The cryostat starts wherever the fake's analog output already is,
            # so an unarmed run does not begin with a phantom step.
            self.cryostat.response.pct = dev.analog_pct
            return dev
        return Sim33x(self.cryostat, model=cfg.model)

    def _build_instrument(self, c: InstrumentConfig):
        device = self._sim_device(c) if c.driver == "sim" else None
        transport = build_transport(c, device=device)
        if c.model == "218":
            return LS218(
                transport,
                name=c.resolved_name(),
                channels=dict(c.channels),
                read_status=c.read_status,
                analog=AnalogOutputConfig(
                    output=c.analog_output, decimals=c.analog_decimals
                ),
                allow_writes=c.allow_writes,
                max_output_pct=c.max_output_pct,
                verify_writes=c.verify_writes,
                readback_tol_pct=c.readback_tol_pct,
            )
        return LS33x(
            transport,
            model=c.model,
            name=c.resolved_name(),
            channels=dict(c.channels) or None,
            read_status=c.read_status,
            read_setpoints=c.read_setpoints,
            read_heaters=c.read_heaters,
            read_analog_outputs=c.read_analog_outputs,
            allow_writes=c.allow_writes,
            max_setpoint_k=c.max_setpoint_k,
        )

    def _channel_columns(self) -> list[str]:
        """Every logged temperature channel, in declaration order.

        Taken from configuration, not from a frame: the CSV header is written
        before the first read, and a channel that is merely slow to answer must
        not silently lose its column for the rest of the run.
        """
        cols: list[str] = []
        for c in self.cfg.enabled_instruments:
            for label in c.channels.values():
                if label not in cols:
                    cols.append(label)
        return cols

    def _aux_columns(self) -> list[str]:
        """Auxiliary scalars worth a column, in a stable order.

        Asked of each instrument rather than assumed, so adding a second 335
        adds its columns without anything here changing.  `heater_pct` leads
        because the legacy logs put the commanded output first and analysis
        scripts expect it.
        """
        # `heater_pct` is what a *software* loop commanded.  On a cryostat whose
        # box runs its own PID there is no such number, and an always-empty
        # column in a months-long CSV is just a question every reader has to
        # ask once.
        cols = ["heater_pct"] if self.supervisor is not None else []
        for inst, c in zip(self.instruments, self.cfg.enabled_instruments):
            if c.model == "218":
                cols.append(f"{inst.name}.aout{c.analog_output}")
            else:
                cols += inst.aux_keys()
        return cols

    def _build(self) -> None:
        cfg = self.cfg
        for c in cfg.enabled_instruments:
            inst = self._build_instrument(c)
            self.instruments.append(inst)
            self.by_name[inst.name] = inst
            if c.model == "218" and self.ls218 is None:
                self.ls218 = inst

        if self._controller_factory is not None:
            self.supervisor = self._controller_factory(self)
            if self.supervisor is not None and cfg.sim.speedup != 1.0 \
                    and not cfg.uses_hardware:
                log.warning(
                    "sim.speedup=%.1f accelerates the thermal response but NOT the controller, "
                    "which still integrates in real time -- closed-loop behaviour in "
                    "this run does not represent the cryostat.  Use the virtual-clock "
                    "harness in tests/ for accelerated closed-loop work.",
                    cfg.sim.speedup,
                )

        if cfg.recorder.enabled:
            self.recorder = Recorder(
                cfg.recorder.directory,
                prefix=cfg.recorder.filename_prefix,
                channels=self._channel_columns(),
                aux_keys=self._aux_columns(),
                flush_every_sample=cfg.recorder.flush_every_sample,
            )

        # Built before the poller so its `on_frame` hook can be handed over at
        # construction: the service must see the very first cycle, because a
        # status file that appears only on cycle two is a status file a client
        # can catch absent.
        if cfg.ipc.enabled:
            self.ipc = IpcService(
                status_path=cfg.ipc.status_path(),
                spool=CommandSpool(cfg.ipc.command_path(),
                                   ttl_s=cfg.ipc.command_ttl_s),
                instruments=self.instruments,
                recorder=self.recorder,
                accept_commands=cfg.ipc.accept_commands,
                allow_heater_range=cfg.ipc.allow_heater_range,
                allow_analog_output=cfg.ipc.allow_analog_output,
                max_commands_per_cycle=cfg.ipc.max_commands_per_cycle,
                ack_history=cfg.ipc.ack_history,
                config_path=cfg.source_path,
                interval_s=cfg.acquisition.interval_s,
            )

        self.poller = Poller(
            self.instruments,
            interval_s=cfg.acquisition.interval_s,
            recorder=self.recorder,
            ringbuffer=self.ring,
            supervisor=self.supervisor,
            control_channel=(
                cfg.control_channel if cfg.control_instrument is not None else None
            ),
            log_every_n=cfg.acquisition.log_every_n,
            # One cadence for the whole cycle: the poller toggles read_status
            # on every instrument at once, so the strictest declared value wins.
            status_every_n_cycles=min(
                (c.status_every_n_cycles for c in cfg.enabled_instruments), default=0
            ),
            on_frame=self.ipc.on_frame if self.ipc is not None else None,
        )
        if self.ipc is not None:
            self.ipc.poller = self.poller

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        assert self.poller is not None
        if self.ipc is not None:
            self.ipc.start()
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
        # Last, so the final status file reports the closed log and whatever
        # the supervisor decided to leave the heater doing.
        if self.ipc is not None:
            self.ipc.stop()

    def arm(self, setpoint_k: float | None = None) -> None:
        """Close the loop.  Explicit, never automatic on startup.

        With no setpoint, arm to *hold the temperature the cryostat is at now*.
        That is what arming means to an operator, and it is the only choice
        that is bumpless: adopting a stale configured setpoint instead asks the
        loop to move somewhere the heater is not currently set for, which the
        premise check correctly reads as a broken premise and refuses.
        Deliberate moves are sweeps -- see the controller's own ``sweep_to``.
        """
        if self.supervisor is None:
            raise RuntimeError("no controller is configured -- this is a recorder")
        if setpoint_k is None:
            here = self.current_temperature()
            if here is None:
                raise RuntimeError(
                    "cannot arm: no usable reading yet for "
                    f"{self.cfg.control_channel!r}. Poll first, or pass a setpoint."
                )
            setpoint_k = here
            log.warning("arming to hold the present temperature, %.4f K", setpoint_k)
        self.supervisor.arm(setpoint_k)

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
