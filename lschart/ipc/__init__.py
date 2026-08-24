"""Talking to other programs without sharing the instrument.

The recorder owns the instrument link exclusively -- a COM port allows exactly
one holder, and two processes on one GPIB board interleave transactions -- so
anything else that wants the data or wants to command a setpoint has to go
through here rather than through the bus.

`lock.InstanceLock` is what makes "exclusively" true.
"""

from .lock import AlreadyRunning, InstanceLock

__all__ = ["InstanceLock", "AlreadyRunning"]
