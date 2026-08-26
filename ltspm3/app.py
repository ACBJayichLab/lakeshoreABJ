"""Build an LTSPM3 application: the generic recorder plus the heater loop.

This is the only module that knows both halves.  :class:`lschart.app.Application`
supplies the transports, instruments, recorder and poller; the two factories
here supply the cryostat -- the calibrated thermal response for the simulator, and the
:class:`~ltspm3.control.supervisor.HeaterSupervisor` that owns the analog output.

Importing this module also registers the ``control:`` config section, so
``ltspm3.config`` never has to be imported by hand.
"""

from __future__ import annotations

import logging

from lschart.app import Application
from lschart.config import AppConfig

from .config import ControlConfig  # noqa: F401  -- registers `control:`
from .control.supervisor import HeaterSupervisor
from .sim_response import LTSPM3_AUX_COUPLING, ResponseParams, ThermalModel

log = logging.getLogger(__name__)


def response_factory(sim_cfg):
    """The calibrated two-pole LTSPM3 thermal response, for a ``sim`` backend."""
    return ThermalModel(ResponseParams(), start_k=sim_cfg.start_k)


def controller_factory(app: Application):
    """The heater loop, or ``None`` for a pure chart recorder."""
    cfg: ControlConfig = app.cfg.section("control")
    if not cfg.enabled:
        return None
    if app.ls218 is None:
        # config validation catches this first; belt and braces, because the
        # alternative is an AttributeError deep inside the poll thread.
        raise ValueError(
            "control.enabled requires ls218.enabled -- the sample heater is "
            "the 218's analog output"
        )
    # The 218's write gate is generic `lschart` policy and knows nothing about
    # a software loop, so a control config that forgets it would build fine and
    # then raise PermissionError on the poll thread at the first output.  Say so
    # here instead, where it is a startup error somebody can act on.
    if not app.ls218.allow_writes:
        raise ValueError(
            f"control.enabled but {app.ls218.name} has allow_writes: false -- "
            "the software loop drives that box's analog output and every write "
            "would be refused. Set allow_writes: true on it, and set "
            "verify_writes: false there too: the supervisor confirms its own "
            "writes, and doing it in both places costs a second transaction "
            "every control cycle"
        )
    return HeaterSupervisor(
        app.ls218,
        channel=app.cfg.control_channel,
        config=cfg.supervisor,
        pid_config=cfg.pid,
        guard_config=cfg.guard,
        coherence_config=cfg.coherence,
        ramp_config=cfg.ramp,
        tuning_config=cfg.tuning,
        feedforward_config=cfg.feedforward,
        filter_kwargs=dict(cfg.filter),
    )


def build(cfg: AppConfig) -> Application:
    """An :class:`Application` wired for the LTSPM3 cryostat."""
    return Application(
        cfg,
        controller_factory=controller_factory,
        response_factory=response_factory,
    )


def simulated_rig(**kw):
    """A :class:`~lschart.instruments.sim.SimulatedCryostat` on the LTSPM3 cryostat.

    Re-exported here so tests and tools have one obvious import for it.
    """
    from .sim_response import ltspm3_cryostat

    return ltspm3_cryostat(**kw)


__all__ = [
    "build",
    "controller_factory",
    "response_factory",
    "simulated_rig",
    "LTSPM3_AUX_COUPLING",
]
