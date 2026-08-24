"""The strip-chart viewer.  A separate process, and another file-IPC client.

Importing this package does **not** import Qt.  :mod:`lschart.gui.source` is
plain Python and is what the tests exercise; Qt appears only in
:mod:`lschart.gui.window`, which :mod:`lschart.gui.__main__` imports when a
window is actually being opened.  That keeps ``import lschart`` free of a Qt
dependency on a machine that only ever runs the recorder -- which is the
machine that matters, since it is the one that has to stay up for months.

    python -m lschart.gui -c config.yaml        # or: lschart-view -c config.yaml
"""

from .source import CsvTail, StatusSource, classify_column

__all__ = ["CsvTail", "StatusSource", "classify_column"]
