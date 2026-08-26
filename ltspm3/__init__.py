"""LTSPM3 software PID: the cryostat-specific control layer over ``lschart``.

Everything in here is calibrated to Jeff's LTSPM3 cryostat -- the measured
steady-state curve, the sensor-glitch thresholds, the gain schedule -- and is
deliberately kept out of ``lschart`` so the chart recorder stays usable on any
Lake Shore cryostat.  The dependency runs one way: ``ltspm3`` imports ``lschart``,
never the reverse.

The join is :class:`ltspm3.control.supervisor.HeaterSupervisor`, which satisfies
the controller protocol that :class:`lschart.acquisition.poller.Poller` steps
once per frame.
"""

__version__ = "0.1.0"
