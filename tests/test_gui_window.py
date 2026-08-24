"""The one gesture in the viewer that changes what is drawn: the drag.

Qt-level, and therefore skipped wherever Qt is not installed -- the recorder
does not depend on it and neither does the rest of the suite.  What is worth
testing here is not that pyqtgraph draws: it is that a drag becomes a time
window, that the window survives the next refresh instead of being snapped
back by the autoscale, and that there is a way out of it again.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from lschart.gui.window import ViewerWindow  # noqa: E402

HEADER = "Timestamp,Time,Sample,ls336.setpoint1,ls336.heater1,Validity,State,Notes\n"


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def viewer(tmp_path, qt_app):
    """A viewer showing an hour of one-second samples from a fake recorder."""
    t0 = time.time() - 3600
    csv = tmp_path / "log.csv"
    with csv.open("w") as fh:
        fh.write(HEADER)
        for i in range(3600):
            stamp = _dt.datetime.fromtimestamp(t0 + i).isoformat(timespec="milliseconds")
            fh.write(f"{stamp},{i}.0,{96.0 + (i % 60) * 0.1:.4f},77.0,12.5,,,\n")
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "t_wall": time.time(), "cycle": 3, "running": True, "interval_s": 1.0,
        "channels": [{"name": "Sample", "kelvin": 96.0, "usable": True}],
        "links": [{"name": "ls336", "up": True, "writable": True}],
        "recorder": {"path": str(csv), "rows": 3600},
        "commands": {"accepted": True, "recent": []},
    }))
    # A refresh interval long enough that the timer never fires during a test:
    # every poll in here is made by hand.
    window = ViewerWindow(str(status), refresh_ms=10_000_000)
    window.resize(1280, 800)
    window.show()
    qt_app.processEvents()
    yield window
    window.close()


class FakeDrag:
    """The three fields pyqtgraph's drag handler asks our override for."""

    def __init__(self, down, pos, finish):
        self._down, self._pos, self._finish = down, pos, finish

    def button(self):
        return QtCore.Qt.MouseButton.LeftButton

    def modifiers(self):
        return QtCore.Qt.KeyboardModifier.NoModifier

    def buttonDownPos(self, *_a):
        return self._down

    def pos(self):
        return self._pos

    def isFinish(self):
        return self._finish

    def accept(self):
        pass


class FakeDoubleClick:
    def double(self):
        return True

    def accept(self):
        pass


def drag(viewbox, from_frac: float, to_frac: float) -> tuple[float, float]:
    """Drag horizontally between two fractions of the panel's width."""
    rect = viewbox.boundingRect()
    y = rect.height() * 0.5
    p0 = QtCore.QPointF(rect.width() * from_frac, y)
    p1 = QtCore.QPointF(rect.width() * to_frac, y)
    viewbox.mouseDragEvent(FakeDrag(p0, p1, False))
    viewbox.mouseDragEvent(FakeDrag(p0, p1, True))
    return viewbox.mapToView(p0).x(), viewbox.mapToView(p1).x()


def test_a_drag_makes_that_span_the_window(viewer):
    box = viewer.k_plot.getViewBox()
    x0, x1 = drag(box, 0.4, 0.6)
    assert viewer._span == pytest.approx((x0, x1))
    assert box.viewRange()[0] == pytest.approx([x0, x1])
    # x-linked: the heater panel below shows the same slice of time, which is
    # the whole reason for two panels rather than one.
    assert viewer.pct_plot.getViewBox().viewRange()[0] == pytest.approx([x0, x1])


def test_a_drag_narrows_the_data_and_not_just_the_view(viewer):
    """The y axis has to autoscale to the span, so the data must be cut to it."""
    box = viewer.k_plot.getViewBox()
    before = len(viewer.curves["Sample"].getData()[0])
    drag(box, 0.4, 0.6)
    after = len(viewer.curves["Sample"].getData()[0])
    assert 0 < after < before / 2


def test_the_window_survives_the_next_poll_of_the_files(viewer):
    """The refresh must not snap the view back to following the recorder."""
    box = viewer.k_plot.getViewBox()
    x0, x1 = drag(box, 0.4, 0.6)
    viewer.refresh()
    assert viewer._span == pytest.approx((x0, x1))
    assert box.viewRange()[0] == pytest.approx([x0, x1])


def test_a_click_that_wobbled_is_not_a_window(viewer):
    """Otherwise a stray click on the chart zooms to a millisecond."""
    box = viewer.k_plot.getViewBox()
    rect = box.boundingRect()
    p0 = QtCore.QPointF(rect.width() * 0.4, rect.height() * 0.5)
    p1 = QtCore.QPointF(rect.width() * 0.4 + 2, rect.height() * 0.5)
    box.mouseDragEvent(FakeDrag(p0, p1, True))
    assert viewer._span is None


def test_a_zoom_by_any_other_route_also_moves_the_window(viewer):
    """The wheel and a Shift-drag land here as a range change, and must refeed."""
    box = viewer.k_plot.getViewBox()
    x0, x1 = drag(box, 0.4, 0.6)
    box.setXRange(x0 - 600, x1 + 600, padding=0)
    assert viewer._span == pytest.approx((x0 - 600, x1 + 600))
    assert len(viewer.curves["Sample"].getData()[0]) > 1200


@pytest.mark.parametrize("leave", ["double-click", "button", "preset"])
def test_there_is_a_way_back_to_following_the_recorder(viewer, leave):
    box = viewer.k_plot.getViewBox()
    drag(box, 0.4, 0.6)
    assert viewer.live_button.isEnabled()

    if leave == "double-click":
        box.mouseClickEvent(FakeDoubleClick())
    elif leave == "button":
        viewer.live_button.click()
    else:
        viewer.window_combo.setCurrentIndex(0)

    assert viewer._span is None
    assert box.autoRangeEnabled()[0]
    assert not viewer.live_button.isEnabled()
