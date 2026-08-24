"""Build an LTSPM3 application: the generic recorder plus the heater loop.

This is the only module that knows both halves.  :class:`lschart.app.Application`
supplies the transports, instruments, recorder and poller; the two factories
here supply the cryostat -- the calibrated plant for the simulator, and the
:class:`~ltspm.control.supervisor.HeaterSupervisor` that owns the analog output.

Importing this module also registers the ``control:`` config section, so
``ltspm.config`` never has to be imported by hand.
"""

from __future__ import annotations

import logging

from lschart.app import Application
from lschart.config import AppConfig

from .config import ControlConfig  # noqa: F401  -- registers `control:`
from .control.supervisor import HeaterSupervisor
from .sim_plant import LTSPM_AUX_COUPLING, PlantParams, ThermalModel

log = logging.getLogger(__name__)


def plant_factory(sim_cfg):
    """The calibrated two-pole LTSPM plant, for a ``sim`` backend."""
    return ThermalModel(PlantParams(), start_k=sim_cfg.start_k)


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
    """An :class:`Application` wired for the LTSPM cryostat."""
    return Application(
        cfg,
        controller_factory=controller_factory,
        plant_factory=plant_factory,
    )


def simulated_rig(**kw):
    """A :class:`~lschart.instruments.sim.SimulatedRig` on the LTSPM plant.

    Re-exported here so tests and tools have one obvious import for it.
    """
    from .sim_plant import ltspm_rig

    return ltspm_rig(**kw)


__all__ = [
    "build",
    "controller_factory",
    "plant_factory",
    "simulated_rig",
    "LTSPM_AUX_COUPLING",
]
