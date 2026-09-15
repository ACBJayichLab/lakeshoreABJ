"""Fixtures only.  The classes live in `loop_harness` and `bench_plant`."""

import pytest

from bench_plant import FittedHarness
from loop_harness import Harness, VirtualClock
from ltspm3.control import LoopMode

__all__ = ["Harness", "VirtualClock", "FittedHarness"]


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
# The harness itself lives in `bench_plant.py`, not here: see that module's
# docstring for why `from conftest import ...` is a trap across two test trees.
# Imported inside the fixtures rather than at module scope because
# `bench_plant` imports `Harness` from this file.


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
