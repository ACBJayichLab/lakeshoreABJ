"""Shared fixtures for the generic suite.

Currently one job: make Qt widget destruction happen at a time this suite
chooses, rather than whenever CPython's garbage collector gets round to it.
"""

from __future__ import annotations

import gc
import sys

import pytest


@pytest.fixture(autouse=True)
def reap_qt_widgets():
    """Collect the Qt widgets a test left behind, before the next one runs.

    The viewer tests build their windows inline and `close()` them.  Closing
    hides a widget; it does not delete it.  So each window stayed alive until
    Python happened to collect it -- which could be in the middle of a *later* test,
    while Qt was laying out a different window.  pyqtgraph keeps Python-side
    references to items whose C++ half went with the collected window, and Qt
    then calls `sizeHint`/`boundingRect`/`resizeEvent` on the dead half:

        libshiboken: Internal C++ object
        (PySide6.QtWidgets.QGraphicsTextItem) already deleted.

    Usually that is only noise on stderr.  Occasionally the call lands after
    the memory is reused and the process takes SIGSEGV *after* pytest has
    printed a clean summary -- which is why CI saw "395 passed" and a failed
    step in the same breath, and why it only showed up about one Windows job in
    three.  (Under bash the exit code is 139; pwsh reports the same crash as 1,
    which is what made it look like a test failure.)

    Forcing the collection here, and then draining Qt's DeferredDelete queue,
    means the destruction happens at a point where nothing else is mid-layout.

    A no-op unless a test has actually created a QApplication, so the suite
    still runs with no Qt installed at all -- which is the whole reason the
    recorder does not depend on it.
    """
    yield

    widgets = sys.modules.get("PySide6.QtWidgets")
    core = sys.modules.get("PySide6.QtCore")
    if widgets is None or core is None:
        return
    app = widgets.QApplication.instance()
    if app is None:
        return

    # Deliberately NOT deleting the widgets by hand.  Taking ownership away
    # from Qt (close/setParent(None)/deleteLater) segfaults immediately here:
    # the fixtures still hold these windows, and pyqtgraph's layout does not
    # survive having them pulled out from under it.  The race is *when* the
    # collection happens, not who owns the widget -- so force it to happen
    # here, at a point where nothing is mid-layout, and leave ownership alone.
    # Costs the suite about 2.5 s locally and ~10 s on Windows.  Gating it on
    # there being outstanding top-level widgets was tried and saved nothing
    # measurable -- the cost is inside the viewer tests themselves, which is
    # where the collection is actually needed.
    app.processEvents()
    gc.collect()
    app.sendPostedEvents(None, core.QEvent.Type.DeferredDelete)
    app.processEvents()
