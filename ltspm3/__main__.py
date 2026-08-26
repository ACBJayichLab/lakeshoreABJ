"""``ltspm3`` entry point: the chart recorder plus the LTSPM3 heater loop.

Deliberately a thin shim over :mod:`lschart.__main__`.  The two commands differ
only in what builds the application, so everything else -- signal handling,
the arming interlock, the transaction budget, ``check`` and ``init`` -- is
shared and cannot drift between them.

    python -m ltspm3 -c config.yaml check
    python -m ltspm3 -c config.yaml run
    python -m ltspm3 -c config.yaml run --arm --setpoint 96.0

``python -m lschart`` still works on the same config and simply records: it has
no controller, so ``--arm`` is refused rather than ignored.
"""

from __future__ import annotations

import sys

from lschart import __main__ as cli

from .app import build

# Swapping the builder is the whole of the difference.
cli.BUILDER = build


def main(argv: list[str] | None = None) -> int:
    return cli.main(argv, prog="ltspm3")


if __name__ == "__main__":
    sys.exit(main())
