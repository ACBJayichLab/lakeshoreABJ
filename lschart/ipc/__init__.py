"""Talking to other programs without sharing the instrument.

The recorder owns the instrument link exclusively -- a COM port allows exactly
one holder, and two processes on one GPIB board interleave transactions -- so
anything else that wants the data or wants to command a setpoint has to go
through here rather than through the bus.

`lock.InstanceLock` is what makes "exclusively" true.  The rest is the door it
leaves open instead: `status.py` writes what the rig is doing, `commands.py` is
the drop-box for requests coming the other way, and `service.py` joins the two
onto the acquisition cycle.  MATLAB's half of the same protocol is
`matlab/LakeShore.m`; the GUI is another client of it, with no privileges the
MATLAB one lacks.
"""

from .commands import Command, CommandResult, CommandSpool
from .lock import AlreadyRunning, InstanceLock
from .service import IpcService
from .status import SCHEMA_VERSION, StatusWriter, read_status, status_age_s

__all__ = [
    "InstanceLock",
    "AlreadyRunning",
    "CommandSpool",
    "Command",
    "CommandResult",
    "IpcService",
    "StatusWriter",
    "read_status",
    "status_age_s",
    "SCHEMA_VERSION",
]
