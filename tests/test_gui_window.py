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

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from lschart.gui import theme  # noqa: E402
from lschart.gui.window import (  # noqa: E402
    COL_SATURATED, COL_SENSOR, DEFAULT_VIEW_WINDOW_S, ViewerWindow, warn_colour,
)

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


def drag(viewbox, from_frac: float, to_frac: float,
         y_from: float = 0.25, y_to: float = 0.75):
    """Drag between two points given as fractions of the panel.

    A rectangle by default, because that is the only gesture there is; pass
    ``y_from == y_to`` for the flat drag that must be refused.  Returns the
    ((x0, x1), (y0, y1)) the drag covers, in data coordinates, ordered.
    """
    rect = viewbox.boundingRect()
    p0 = QtCore.QPointF(rect.width() * from_frac, rect.height() * y_from)
    p1 = QtCore.QPointF(rect.width() * to_frac, rect.height() * y_to)
    # Mapped before the drag, not after: the values the gesture means are the
    # ones under the mouse when it was pressed, and by the time it is released
    # the view has moved to them.
    v0, v1 = viewbox.mapToView(p0), viewbox.mapToView(p1)
    viewbox.mouseDragEvent(FakeDrag(p0, p1, False))
    viewbox.mouseDragEvent(FakeDrag(p0, p1, True))
    return ((min(v0.x(), v1.x()), max(v0.x(), v1.x())),
            (min(v0.y(), v1.y()), max(v0.y(), v1.y())))


def test_a_drag_makes_that_span_the_window(viewer):
    box = viewer.k_plot.getViewBox()
    (x0, x1), _ = drag(box, 0.4, 0.6)
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
    (x0, x1), _ = drag(box, 0.4, 0.6)
    viewer.refresh()
    assert viewer._span == pytest.approx((x0, x1))
    assert box.viewRange()[0] == pytest.approx([x0, x1])


def test_a_click_that_wobbled_is_not_a_window(viewer):
    """Otherwise a stray click on the chart zooms to a millisecond."""
    box = viewer.k_plot.getViewBox()
    rect = box.boundingRect()
    p0 = QtCore.QPointF(rect.width() * 0.4, rect.height() * 0.5)
    p1 = QtCore.QPointF(rect.width() * 0.4 + 2, rect.height() * 0.5 + 2)
    box.mouseDragEvent(FakeDrag(p0, p1, True))
    assert viewer._span is None
    assert viewer._ylim["K"] is None


def test_a_zoom_by_any_other_route_also_moves_the_window(viewer):
    """The wheel and a Shift-drag land here as a range change, and must refeed."""
    box = viewer.k_plot.getViewBox()
    (x0, x1), _ = drag(box, 0.4, 0.6)
    box.setXRange(x0 - 600, x1 + 600, padding=0)
    assert viewer._span == pytest.approx((x0 - 600, x1 + 600))
    assert len(viewer.curves["Sample"].getData()[0]) > 1200


@pytest.mark.parametrize("leave", ["double-click", "button"])
def test_there_is_a_way_back_to_following_the_recorder(viewer, leave):
    box = viewer.k_plot.getViewBox()
    drag(box, 0.4, 0.6, 0.2, 0.8)
    assert not any(b.isChecked() for b in viewer.span_buttons.values())

    if leave == "double-click":
        # Back to the window that was showing before the drag, not to some
        # canonical one: abandoning a span should not also rescale time.
        box.mouseClickEvent(FakeDoubleClick())
    else:
        viewer.span_buttons[DEFAULT_VIEW_WINDOW_S].click()

    assert viewer._span is None
    assert viewer._ylim == {"K": None, "%": None}
    assert box.autoRangeEnabled() == [True, True]
    assert viewer.span_buttons[DEFAULT_VIEW_WINDOW_S].isChecked()


# -- the rectangle, and the axes it is allowed to set ------------------------
#
# The value axis is the half that is new: a 2 mK wobble on an axis autoscaled
# to a 300 K cooldown is a flat line, and no time window fixes that.


def test_a_rectangle_sets_both_axes_to_exactly_itself(viewer):
    box = viewer.k_plot.getViewBox()
    (x0, x1), (y0, y1) = drag(box, 0.4, 0.6, 0.25, 0.75)
    assert viewer._span == pytest.approx((x0, x1))
    assert viewer._ylim["K"] == pytest.approx((y0, y1))
    view_x, view_y = box.viewRange()
    assert view_x == pytest.approx([x0, x1])
    # Precisely those values: no padding, and no autoscale afterwards putting
    # the axis back where the data wants it.
    assert view_y == pytest.approx([y0, y1])
    assert not box.autoRangeEnabled()[1]


def test_the_value_axis_stays_put_across_a_refresh(viewer):
    box = viewer.k_plot.getViewBox()
    _, (y0, y1) = drag(box, 0.4, 0.6, 0.25, 0.75)
    viewer.refresh()
    assert box.viewRange()[1] == pytest.approx([y0, y1])


def test_a_rectangle_leaves_the_other_panel_autoscaling(viewer):
    """Kelvin and percent share no axis, so a kelvin drag cannot crop percent."""
    drag(viewer.k_plot.getViewBox(), 0.4, 0.6, 0.25, 0.75)
    assert viewer._ylim["%"] is None
    assert viewer.pct_plot.getViewBox().autoRangeEnabled()[1]


def test_a_flat_drag_is_not_a_rectangle_and_does_nothing(viewer):
    """There is no one-axis form of the drag: that is what the buttons are for.

    A stripe with no height would otherwise select a degenerate value axis.
    """
    box = viewer.k_plot.getViewBox()
    drag(box, 0.3, 0.7, 0.5, 0.5)           # level, so no height at all
    assert viewer._span is None
    assert viewer._ylim["K"] is None
    assert box.autoRangeEnabled() == [True, True]


# -- the zoom buttons --------------------------------------------------------


def test_the_x_buttons_zoom_the_time_axis_about_its_middle(viewer):
    before = viewer.k_plot.getViewBox().viewRange()[0]
    middle = (before[0] + before[1]) / 2

    viewer.zoom_buttons["X+"].click()
    t0, t1 = viewer._span
    assert (t0 + t1) / 2 == pytest.approx(middle)
    assert (t1 - t0) == pytest.approx((before[1] - before[0]) / 1.5)

    viewer.zoom_buttons["X−"].click()
    assert viewer._span == pytest.approx(before)


def test_the_y_buttons_zoom_the_value_axis_about_its_middle(viewer):
    before = viewer.k_plot.getViewBox().viewRange()[1]
    middle = (before[0] + before[1]) / 2

    viewer.zoom_buttons["Y+"].click()
    y0, y1 = viewer._ylim["K"]
    assert (y0 + y1) / 2 == pytest.approx(middle)
    assert (y1 - y0) == pytest.approx((before[1] - before[0]) / 1.5)
    assert viewer.k_plot.getViewBox().viewRange()[1] == pytest.approx([y0, y1])

    viewer.zoom_buttons["Y−"].click()
    assert viewer._ylim["K"] == pytest.approx(before)


def test_the_y_buttons_move_both_panels(viewer):
    """They name an axis, not a panel, so both of them had better move."""
    before = {u: p.getViewBox().viewRange()[1] for u, p in viewer._panels.items()}
    viewer.zoom_buttons["Y+"].click()
    for unit, was in before.items():
        now = viewer._ylim[unit]
        assert now is not None
        assert (now[1] - now[0]) == pytest.approx((was[1] - was[0]) / 1.5)


def _grow_the_log(viewer, extra_s: float = 60.0) -> None:
    """Append samples past the right-hand edge of the view, and poll them.

    This is the state a running recorder keeps the viewer in: the redraw
    queues an autoscale that has not been enacted yet, because nothing has
    repainted since.
    """
    path = viewer.tail.path
    end = viewer.curves["Sample"].getData()[0][-1]
    with open(path, "a") as fh:
        for i in range(1, int(extra_s) + 1):
            stamp = _dt.datetime.fromtimestamp(
                end + i).isoformat(timespec="milliseconds")
            fh.write(f"{stamp},0.0,{300.0:.4f},77.0,12.5,,,\n")
    viewer.refresh()


@pytest.mark.parametrize("axis", ["x", "y"])
def test_a_button_press_is_not_swallowed_by_the_queued_autorange(
        viewer, axis):
    """The first press must count while the view is still following the data.

    Disabling an axis enacts one last autoscale on the way out
    (ViewBox.enableAutoRange), and that autoscale's range-changed signal
    arrives *after* the press has computed its new range -- so computing
    before disabling let the signal overwrite the press with the very view
    it was leaving.  New samples keep an autoscale perpetually queued while
    the recorder runs, which is why this bit on the cryostat and not in the
    first single-press test.
    """
    _grow_the_log(viewer)
    before_x = viewer.k_plot.getViewBox().viewRange()[0]
    if axis == "x":
        viewer.zoom_buttons["X+"].click()
        t0, t1 = viewer._span
        assert (t1 - t0) == pytest.approx((before_x[1] - before_x[0]) / 1.5)
        assert viewer.k_plot.getViewBox().viewRange()[0] \
            == pytest.approx([t0, t1])
    else:
        before_y = viewer.k_plot.getViewBox().viewRange()[1]
        viewer.zoom_buttons["Y+"].click()
        y0, y1 = viewer._ylim["K"]
        assert (y1 - y0) == pytest.approx((before_y[1] - before_y[0]) / 1.5)
        assert viewer.k_plot.getViewBox().viewRange()[1] \
            == pytest.approx([y0, y1])


def test_three_presses_are_three_steps(viewer):
    """Each press reads the range the last one left, compounding to 1.5³."""
    _grow_the_log(viewer)
    before = viewer.k_plot.getViewBox().viewRange()[0]
    for _ in range(3):
        viewer.zoom_buttons["X+"].click()
    t0, t1 = viewer._span
    assert (t1 - t0) == pytest.approx((before[1] - before[0]) / 1.5 ** 3)


def test_an_x_zoom_stops_the_chart_following_the_recorder(viewer):
    """Pressing it is a decision, and a decision has to be escapable."""
    assert viewer.span_buttons[DEFAULT_VIEW_WINDOW_S].isChecked()
    viewer.zoom_buttons["X+"].click()
    assert not any(b.isChecked() for b in viewer.span_buttons.values())
    assert not viewer.k_plot.getViewBox().autoRangeEnabled()[0]
    assert "not following" in viewer.statusBar().currentMessage()


def test_a_y_zoom_stops_the_axis_autoscaling(viewer):
    viewer.zoom_buttons["Y+"].click()
    # The view row describes the *time* window, and time still follows: the
    # button stays checked.  The fixed kelvin axis is named in the status bar.
    assert viewer.span_buttons[DEFAULT_VIEW_WINDOW_S].isChecked()
    assert not viewer.k_plot.getViewBox().autoRangeEnabled()[1]
    # The time axis is untouched: the chart still follows the recorder in x.
    assert viewer._span is None
    assert viewer.k_plot.getViewBox().autoRangeEnabled()[0]


def test_a_y_zoom_survives_the_next_poll_of_the_files(viewer):
    viewer.zoom_buttons["Y+"].click()
    fixed = viewer._ylim["K"]
    viewer.refresh()
    assert viewer.k_plot.getViewBox().viewRange()[1] == pytest.approx(list(fixed))


def test_a_view_button_undoes_the_zoom_buttons_too(viewer):
    viewer.zoom_buttons["X+"].click()
    viewer.zoom_buttons["Y+"].click()
    viewer.span_buttons[DEFAULT_VIEW_WINDOW_S].click()
    assert viewer._span is None
    assert viewer._ylim == {"K": None, "%": None}
    assert viewer.k_plot.getViewBox().autoRangeEnabled() == [True, True]


# -- the live-referenced view buttons ----------------------------------------
#
# The last N hours, ending at the newest sample and riding forward with the
# recorder.  Worth testing: that a button cuts the data to its window, that
# the buttons keep their state honest across a drag, and that new samples
# extend the window rather than being cut off by it.


def test_a_view_button_cuts_the_data_to_its_window(tmp_path, qt_app):
    """Ten hours of samples, a six-hour button: four hours leave the screen."""
    t0 = time.time() - 10 * 3600
    csv = tmp_path / "log.csv"
    with csv.open("w") as fh:
        fh.write(HEADER)
        for i in range(600):                      # one sample per minute
            stamp = _dt.datetime.fromtimestamp(t0 + i * 60).isoformat(
                timespec="milliseconds")
            fh.write(f"{stamp},{i}.0,{96.0:.4f},77.0,12.5,,,\n")
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "t_wall": time.time(), "cycle": 3, "running": True, "interval_s": 60.0,
        "channels": [{"name": "Sample", "kelvin": 96.0, "usable": True}],
        "links": [{"name": "ls336", "up": True, "writable": True}],
        "recorder": {"path": str(csv), "rows": 600},
        "commands": {"accepted": True, "recent": []},
    }))
    w = ViewerWindow(str(status), refresh_ms=10_000_000)
    qt_app.processEvents()

    # Ten hours of samples inside the 24 h the viewer opens on: all of them.
    assert len(w.curves["Sample"].getData()[0]) == 600
    assert w.span_buttons[DEFAULT_VIEW_WINDOW_S].isChecked()
    w.span_buttons[6 * 3600.0].click()
    shown = len(w.curves["Sample"].getData()[0])
    assert 360 <= shown <= 362          # 6 h at one minute, plus the bracket
    assert "last 6.0 h" in w.statusBar().currentMessage()
    assert w.span_buttons[6 * 3600.0].isChecked()
    assert not w.span_buttons[DEFAULT_VIEW_WINDOW_S].isChecked()

    # New samples extend into the window's frame: still live-referenced.  The
    # far edge slides forward with the newest sample, dropping about as many
    # old samples as the new one advanced the clock.
    with csv.open("a") as fh:
        stamp = _dt.datetime.fromtimestamp(time.time() + 120).isoformat(
            timespec="milliseconds")
        fh.write(f"{stamp},999.0,{96.5:.4f},77.0,12.5,,,\n")
    w.refresh()
    w.refresh()                          # second tick settles the debounce
    data = w.curves["Sample"].getData()
    assert data[1][-1] == pytest.approx(96.5)
    assert abs(len(data[0]) - shown) <= 4
    w.close()


def test_a_drag_leaves_no_view_button_checked(viewer):
    viewer.span_buttons[24 * 3600.0].click()
    assert viewer.span_buttons[24 * 3600.0].isChecked()
    drag(viewer.k_plot.getViewBox(), 0.4, 0.6)
    assert not any(b.isChecked() for b in viewer.span_buttons.values())
    # And a view button is a way back out of the hand-picked span.
    viewer.span_buttons[12 * 3600.0].click()
    assert viewer._span is None
    assert viewer._follow_span_s == 12 * 3600.0
    assert viewer.span_buttons[12 * 3600.0].isChecked()


# -- the control panel -------------------------------------------------------
#
# What is worth testing here is not that Qt draws a spin box.  It is that the
# panel offers the controls the selected box actually has, that the one number
# a 218 accepts cannot be typed past the recorder's ceiling, and that the panic
# button is not aimed at whichever instrument happens to be selected.


#: The two power gates open.  Most of these tests are about what the panel does
#: with a control, not about whether it is offered -- and since A3 a shut gate
#: disables its control outright, so a fixture with them shut would be testing
#: the gate over and over instead.
OPEN = {"accepted": True, "recent": [],
        "allow_heater_range": True, "allow_analog_output": True}


def cryostat(tmp_path, qt_app, links, commands=None, csv_name="log.csv",
             control=None):
    """A viewer watching a recorder with the given instruments.

    ``control`` is the software loop's block, or None for the plain recorder
    that most installs are -- which is what the status file itself writes.
    """
    from lschart.ipc.commands import CommandSpool

    csv = tmp_path / csv_name
    stamp = _dt.datetime.fromtimestamp(time.time()).isoformat(timespec="milliseconds")
    csv.write_text(HEADER + f"{stamp},0.0,96.0,77.0,12.5,,,\n")
    status = tmp_path / f"status-{csv_name}.json"
    status.write_text(json.dumps({
        "t_wall": time.time(), "cycle": 3, "running": True, "interval_s": 1.0,
        "channels": [{"name": "Sample", "kelvin": 96.0, "usable": True}],
        "links": links,
        "aux": [{"name": "ls336.setpoint1", "value": 77.0}],
        "recorder": {"path": str(csv), "rows": 60},
        "control": control,
        "commands": commands or dict(OPEN),
    }))
    window = ViewerWindow(str(status), refresh_ms=10_000_000,
                          spool=CommandSpool(tmp_path / f"cmd-{csv_name}"))
    # No processEvents() and no show().  These tests ask what the panel decided,
    # not what Qt painted, and a layout pass here makes pyqtgraph's legend emit
    # a wall of sizeHint tracebacks under the offscreen platform -- noise that
    # would bury a real failure.
    return window


def showing(widget) -> bool:
    """Did the panel choose to show this control?

    `isHidden` rather than `isVisible`, deliberately: `isVisible` is False for
    every child of a window that has not been shown, so it would answer a
    question about the test harness rather than about the panel.  These
    viewers are never shown -- showing one under the offscreen platform makes
    pyqtgraph's legend emit a wall of sizeHint tracebacks that would bury a
    real failure.
    """
    return not widget.isHidden()


def loop_entry(n, sensor="Sample", **kw):
    """One entry of a schema-2 ``links[].loops`` array.

    Every key present on every entry, which is the promise the status file
    makes -- MATLAB's jsondecode returns a struct array only when they agree.
    """
    entry = {
        "loop": n, "sensor": sensor, "input": "ABCD"[n - 1],
        "mode": "closed loop", "mode_code": 1,
        "heater_output": n if n in (1, 2) else None,
        "setpoint_k": 77.0, "output_pct": 0.0,
        "range": 0 if n in (1, 2) else None,
        "threshold_k": None, "ramping": False,
        "p": None, "i": None, "d": None,
    }
    entry.update(kw)
    return entry


CTRL = {"name": "ls336", "model": "336", "up": True, "writable": True,
        "loop_numbers": [1, 2, 3, 4], "heater_outputs": [1, 2],
        "analog_output": None, "max_output_pct": 100.0,
        "loops": [loop_entry(n) for n in (1, 2, 3, 4)]}

MON = {"name": "ls218", "model": "218", "up": True, "writable": True,
       "loop_numbers": [], "heater_outputs": [], "analog_output": 1,
       "max_output_pct": 70.0, "loops": []}

#: What a recorder from before schema 2 wrote: a bare list of loop numbers and
#: no loop objects at all.  A viewer must degrade to offering those loops, not
#: to deciding the box has none.
OLD_CTRL = {"name": "ls336", "model": "336", "up": True, "writable": True,
            "loops": [1, 2, 3, 4], "heater_outputs": [1, 2],
            "analog_output": None, "max_output_pct": 100.0}


def queued(window) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(window.spool.pending())]


def test_a_controller_gets_a_setpoint_and_a_range_but_no_analog_control(
        tmp_path, qt_app):
    w = cryostat(tmp_path, qt_app, [CTRL])
    assert showing(w.setpoint_group) and showing(w.range_group)
    assert not showing(w.analog_group)
    # The loop table is the selector; the range follows the loop it selects.
    assert w._loop == 1
    assert w.heater_label.text() == "1"
    assert "output 1" in w.range_group.title()
    w.close()


def test_the_range_control_follows_the_loop_the_table_selected(tmp_path, qt_app):
    """No separate output combo: on this family the loop number *is* the
    output number, and a second control offering to disagree could only put
    power somewhere nobody meant it to go."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    w.loops.selectRow(1)                        # loop 2
    assert w._loop == 2
    assert w.heater_label.text() == "2"
    assert "output 2" in w.range_group.title()
    w.close()


def test_a_loop_with_no_heater_range_is_offered_none(tmp_path, qt_app):
    """A 336's loops 3 and 4 drive an analog output: no range to set, so the
    control that sets one is not shown."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    w.loops.selectRow(2)                        # loop 3
    assert w._loop == 3
    assert showing(w.setpoint_group)
    assert not showing(w.range_group)
    assert not showing(w.analog_group)          # a 336 takes no `analog`
    assert "analog output" in w.loop_note.text()
    w.close()


def test_a_row_names_the_sensor_the_instrument_says_it_reads(tmp_path, qt_app):
    w = cryostat(tmp_path, qt_app, [dict(CTRL, loops=[
        loop_entry(1, "Coldplate"), loop_entry(2, "Stage 2"),
        loop_entry(3, "Rad Shield"), loop_entry(4, "Stage 1")])])
    sensors = [w.loops.item(r, 1).text() for r in range(w.loops.rowCount())]
    assert sensors == ["Coldplate", "Stage 2", "Rad Shield", "Stage 1"]
    ranges = [w.loops.item(r, 5).text() for r in range(w.loops.rowCount())]
    assert ranges == ["0", "0", "n/a", "n/a"]
    w.close()


def test_a_recorder_too_old_to_publish_loops_still_offers_them(tmp_path, qt_app):
    """Schema 1 wrote a bare list of loop numbers.  A viewer pointed at one
    should offer its loops rather than decide the box has none."""
    w = cryostat(tmp_path, qt_app, [OLD_CTRL])
    assert showing(w.setpoint_group) and showing(w.range_group)
    assert w.loops.rowCount() == 0             # no rows to invent
    assert not w.loops.isVisible()
    assert "schema 1" in w.loop_note.text()
    w.close()


def test_a_218_gets_an_analog_control_and_neither_of_the_others(tmp_path, qt_app):
    """It has no loop to aim a setpoint at, and no range to raise."""
    w = cryostat(tmp_path, qt_app, [MON])
    assert showing(w.analog_group)
    assert not showing(w.setpoint_group) and not showing(w.range_group)
    w.close()


def test_the_recorders_ceiling_caps_the_spin_box(tmp_path, qt_app):
    """The widget must not be able to express a value that will be refused."""
    w = cryostat(tmp_path, qt_app, [MON])
    assert w.analog_spin.maximum() == 70.0
    w.analog_spin.setValue(90.0)
    assert w.analog_spin.value() == 70.0
    assert "70" in w.analog_group.title()
    w.close()


def test_switching_instrument_switches_the_controls(tmp_path, qt_app):
    """The LTSPM3 shape, if both boxes were writable: one panel, two shapes."""
    w = cryostat(tmp_path, qt_app, [CTRL, MON])
    w.instrument_combo.setCurrentIndex(0)
    assert showing(w.setpoint_group) and not showing(w.analog_group)
    w.instrument_combo.setCurrentIndex(1)
    assert showing(w.analog_group) and not showing(w.setpoint_group)
    w.close()


def test_a_read_only_box_is_not_offered_as_a_target(tmp_path, qt_app):
    theirs = dict(CTRL, writable=False)
    w = cryostat(tmp_path, qt_app, [theirs, MON])
    assert [w.instrument_combo.itemText(i)
            for i in range(w.instrument_combo.count())] == ["ls218"]
    w.close()


def test_a_shut_gate_disables_its_control_and_says_where_to_go_instead(
        tmp_path, qt_app):
    """The gate now applies to 0 as well, so a live control here could only
    ever produce a refusal -- and what replaces it is the panic menu, which is
    exempt from the gate and is never disabled."""
    w = cryostat(tmp_path, qt_app, [MON],
            commands={"accepted": True, "recent": [],
                      "allow_analog_output": False})
    assert "allow_analog_output" in w.analog_note.text()
    assert "including to 0" in w.analog_note.text()
    assert "Panic" in w.analog_note.text()
    assert not w.analog_button.isEnabled()
    assert not w.analog_spin.isEnabled()
    assert w.panic_button.isEnabled()
    w.close()


def test_a_shut_range_gate_disables_the_range_control_too(tmp_path, qt_app):
    w = cryostat(tmp_path, qt_app, [CTRL],
            commands={"accepted": True, "recent": [],
                      "allow_heater_range": False})
    assert not w.range_button.isEnabled()
    assert not w.range_combo.isEnabled()
    assert "including to 0" in w.range_note.text()
    assert w.panic_button.isEnabled()
    w.close()


def test_an_open_gate_still_warns_that_there_is_no_ramp(tmp_path, qt_app):
    w = cryostat(tmp_path, qt_app, [MON],
            commands={"accepted": True, "recent": [],
                      "allow_analog_output": True})
    assert "No ramp" in w.analog_note.text()
    w.close()


def test_sending_an_analog_percent_queues_the_right_command(
        tmp_path, qt_app, monkeypatch):
    w = cryostat(tmp_path, qt_app, [MON])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.analog_spin.setValue(43.0)
    w.analog_button.click()

    (cmd,) = queued(w)
    assert cmd["kind"] == "analog" and cmd["percent"] == 43.0
    assert cmd["instrument"] == "ls218"
    w.close()


def test_sending_a_heater_range_queues_the_right_command(
        tmp_path, qt_app, monkeypatch):
    w = cryostat(tmp_path, qt_app, [CTRL])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.loops.selectRow(1)                              # loop 2 -> output 2
    w.range_combo.setCurrentIndex(3)                  # range 3, high
    w.range_button.click()

    (cmd,) = queued(w)
    assert cmd["kind"] == "range" and cmd["output"] == 2 and cmd["value"] == 3
    w.close()


def test_cancelling_the_dialog_queues_nothing(tmp_path, qt_app, monkeypatch):
    w = cryostat(tmp_path, qt_app, [MON])
    monkeypatch.setattr(w, "_confirm", lambda *a: False)
    w.analog_spin.setValue(43.0)
    w.analog_button.click()
    assert queued(w) == []
    w.close()


def test_raising_power_is_confirmed_in_blunter_terms_than_lowering_it(
        tmp_path, qt_app, monkeypatch):
    """The dialog is the only thing between a click and heat in a cryostat."""
    seen = []
    w = cryostat(tmp_path, qt_app, [MON])
    monkeypatch.setattr(w, "_confirm", lambda title, text: seen.append(text) or True)

    w.analog_spin.setValue(43.0)
    w.analog_button.click()
    assert "APPLIES POWER" in seen[-1] and "NO RAMP" in seen[-1]

    # Called directly rather than clicked: the first command is still
    # unacknowledged, so every button is locked -- which is itself tested
    # elsewhere and is not what this test is about.
    w.analog_spin.setValue(0.0)
    w._send_analog()
    assert "APPLIES POWER" not in seen[-1]
    w.close()


def test_the_range_dialog_quotes_the_setpoint_it_is_about_to_chase(
        tmp_path, qt_app, monkeypatch):
    """Range 3 means nothing without the number the loop will drive toward."""
    seen = []
    w = cryostat(tmp_path, qt_app, [CTRL])
    monkeypatch.setattr(w, "_confirm", lambda title, text: seen.append(text) or True)
    w.range_combo.setCurrentIndex(2)
    w.range_button.click()
    assert "77.000 K" in seen[-1]
    w.close()


def test_the_panic_button_is_not_aimed_at_the_selected_instrument(
        tmp_path, qt_app, monkeypatch):
    """It means stop heating, which on a two-box cryostat is not one box."""
    w = cryostat(tmp_path, qt_app, [CTRL, MON])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.instrument_combo.setCurrentIndex(0)
    # The action, not the button: the button opens the menu, and clicking it
    # here would block on a modal popup rather than send anything.
    w.off_action.trigger()

    (cmd,) = queued(w)
    assert cmd["kind"] == "heaters_off" and cmd["instrument"] == ""
    w.close()


def test_one_unacknowledged_command_locks_every_button(tmp_path, qt_app, monkeypatch):
    """Otherwise a range can be queued against a setpoint that was refused."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.send_button.click()
    assert not any(b.isEnabled() for b in w._buttons())
    w.close()


def test_an_acknowledgement_releases_every_button(tmp_path, qt_app, monkeypatch):
    w = cryostat(tmp_path, qt_app, [CTRL])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.send_button.click()
    cid = w._pending[0]

    status = json.loads(open(w.source.path).read())
    status["cycle"] = 4
    status["t_wall"] = time.time()
    status["commands"]["recent"] = [{"id": cid, "ok": True, "message": "done"}]
    open(w.source.path, "w").write(json.dumps(status))
    w.refresh()

    assert all(b.isEnabled() for b in w._buttons())
    assert "done" in w.ack_label.text()
    w.close()


# -- the fields fill with what the cryostat is at ---------------------------------
#
# A command box that opens at zero invites sending zero-adjacent numbers at a
# cryostat that is nowhere near them.  What each field should start from is the
# recorder's own readback -- and swapping to a 218 should find the percentage
# it is already at, not present 0% as if that were a neutral choice.


def aux_status(tmp_path, links, aux):
    """A status file carrying an explicit aux readback block."""
    csv = tmp_path / "aux-log.csv"
    if not csv.exists():
        stamp = _dt.datetime.fromtimestamp(time.time()).isoformat(
            timespec="milliseconds")
        csv.write_text(HEADER + f"{stamp},0.0,96.0,77.0,12.5,,,\n")
    path = tmp_path / f"status-{abs(hash(json.dumps(aux)))}.json"
    path.write_text(json.dumps({
        "t_wall": time.time(), "cycle": 3, "running": True, "interval_s": 1.0,
        "channels": [{"name": "Sample", "kelvin": 96.0, "usable": True}],
        "links": links,
        "aux": [{"name": k, "value": v} for k, v in aux.items()],
        "recorder": {"path": str(csv), "rows": 60},
        "commands": dict(OPEN),
    }))
    return path


def test_a_setpoint_field_opens_at_the_current_setpoint(tmp_path, qt_app):
    path = aux_status(tmp_path, [CTRL], {"ls336.setpoint1": 4.2})
    w = ViewerWindow(str(path), refresh_ms=10_000_000)
    assert w.setpoint_spin.value() == pytest.approx(4.2)
    w.close()


def test_changing_loop_refills_the_setpoint_field(tmp_path, qt_app):
    path = aux_status(tmp_path, [CTRL],
                      {"ls336.setpoint1": 4.2, "ls336.setpoint2": 77.35})
    w = ViewerWindow(str(path), refresh_ms=10_000_000)
    assert w.setpoint_spin.value() == pytest.approx(4.2)
    w.loops.selectRow(1)
    assert w.setpoint_spin.value() == pytest.approx(77.35)
    w.close()


def test_swapping_to_a_218_finds_its_current_output(tmp_path, qt_app):
    """The case that asked for this: no inert half, so 0 is never neutral."""
    path = aux_status(tmp_path, [CTRL, MON], {
        "ls218.aout1": 12.5, "ls218.range1": 0,
    })
    w = ViewerWindow(str(path), refresh_ms=10_000_000)
    w.instrument_combo.setCurrentIndex(1)
    assert w.analog_spin.value() == pytest.approx(12.5)
    w.close()


def test_the_range_combo_follows_the_current_range(tmp_path, qt_app):
    path = aux_status(tmp_path, [CTRL],
                      {"ls336.range1": 1, "ls336.range2": 3})
    w = ViewerWindow(str(path), refresh_ms=10_000_000)
    assert w.range_combo.currentData() == 1
    w.loops.selectRow(1)                            # loop 2 -> output 2
    assert w.range_combo.currentData() == 3
    w.close()


def test_an_edited_field_stops_tracking_until_the_selection_changes(
        tmp_path, qt_app):
    """A fill that fought the number being typed would be worse than stale."""
    path = aux_status(tmp_path, [CTRL], {"ls336.setpoint1": 4.2})
    w = ViewerWindow(str(path), refresh_ms=10_000_000)
    w.setpoint_spin.setValue(300.0)                 # the operator's number
    w.refresh()
    assert w.setpoint_spin.value() == pytest.approx(300.0)

    # A different loop is a different question; the field tracks again.
    w.loops.selectRow(1)
    w.loops.selectRow(0)
    w.refresh()
    assert w.setpoint_spin.value() == pytest.approx(4.2)
    w.close()


def test_a_field_tracks_again_once_its_command_is_acknowledged(
        tmp_path, qt_app, monkeypatch):
    from lschart.ipc.commands import CommandSpool

    path = aux_status(tmp_path, [MON], {"ls218.aout1": 0.0})
    w = ViewerWindow(str(path), refresh_ms=10_000_000,
                     spool=CommandSpool(tmp_path / "cmd-aux"))
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.analog_spin.setValue(43.0)
    w.analog_button.click()

    # The readback has not caught up yet: the field keeps the operator's
    # number until the recorder says what it now reads.
    w.refresh()
    assert w.analog_spin.value() == pytest.approx(43.0)

    cid = w._pending[0]
    status = json.loads(open(w.source.path).read())
    status["cycle"] = 4
    status["t_wall"] = time.time()
    status["commands"]["recent"] = [{"id": cid, "ok": True, "message": "set"}]
    open(w.source.path, "w").write(json.dumps(status))
    w.refresh()
    assert all(b.isEnabled() for b in w._buttons())
    assert w.analog_spin.value() == pytest.approx(43.0)   # still the readback

    status["aux"] = [{"name": "ls218.aout1", "value": 43.0}]
    open(w.source.path, "w").write(json.dumps(status))
    w.refresh()
    assert w.analog_spin.value() == pytest.approx(43.0)   # and it agrees
    w.close()


def test_fields_without_a_readback_are_left_alone(tmp_path, qt_app):
    """No aux entry means no honest number to fill with -- keep the widget's."""
    path = aux_status(tmp_path, [CTRL], {})
    w = ViewerWindow(str(path), refresh_ms=10_000_000)
    assert w.setpoint_spin.value() == 0.0
    assert w.range_combo.currentData() == 0         # the combo's own default
    w.close()


def test_a_readback_that_lands_somewhere_else_is_shown_rather_than_waited_on(
        tmp_path, qt_app, monkeypatch):
    """The guard is about the OLD value, not about getting the asked-for one.

    A box that rounds, clamps, or is simply set elsewhere still moves off the
    value it held before the command.  Once it has, there is no snap-back
    left to protect against, and the honest thing on screen is what the
    instrument now reads -- not the number that was typed at it.
    """
    from lschart.ipc.commands import CommandSpool

    path = aux_status(tmp_path, [MON], {"ls218.aout1": 0.0})
    w = ViewerWindow(str(path), refresh_ms=10_000_000,
                     spool=CommandSpool(tmp_path / "cmd-elsewhere"))
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.analog_spin.setValue(43.0)
    w.analog_button.click()

    cid = w._pending[0]
    status = json.loads(open(w.source.path).read())
    status["cycle"] = 4
    status["t_wall"] = time.time()
    status["commands"]["recent"] = [{"id": cid, "ok": True, "message": "set"}]
    # Acknowledged, and the readback has moved -- but not to 43.0.
    status["aux"] = [{"name": "ls218.aout1", "value": 42.5}]
    open(w.source.path, "w").write(json.dumps(status))
    w.refresh()

    assert w.analog_spin.value() == pytest.approx(42.5)
    assert w._awaiting is None
    w.close()


def test_a_readback_that_never_agrees_stops_holding_the_field(
        tmp_path, qt_app, monkeypatch):
    """A field that is wrong for ever is worse than one wrong for half a minute.

    Nothing but agreement used to release the guard on an accepted command,
    and the comparison was exact -- so a readback the driver had already
    confirmed to its own tolerance could hold a heater field at the number
    that was asked for, indefinitely, while the box sat somewhere else.
    """
    from lschart.ipc.commands import CommandSpool

    path = aux_status(tmp_path, [MON], {"ls218.aout1": 5.0})
    w = ViewerWindow(str(path), refresh_ms=10_000_000,
                     spool=CommandSpool(tmp_path / "cmd-stuck"))
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.analog_spin.setValue(43.0)
    w.analog_button.click()

    cid = w._pending[0]
    status = json.loads(open(w.source.path).read())
    status["cycle"] = 4
    status["t_wall"] = time.time()
    status["commands"]["recent"] = [{"id": cid, "ok": True, "message": "set"}]
    open(w.source.path, "w").write(json.dumps(status))
    w.refresh()
    # The readback is still the old value, so the field rightly holds.
    assert w.analog_spin.value() == pytest.approx(43.0)
    assert w._awaiting is not None

    # Let the grace period lapse with the readback never having moved.
    w._awaiting = w._awaiting._replace(deadline=time.time() - 1.0)
    w.refresh()

    assert w.analog_spin.value() == pytest.approx(5.0)   # what the box reads
    assert w._awaiting is None
    w.close()


def test_a_fill_leaves_the_widget_able_to_signal_again(tmp_path, qt_app):
    """Signals are blocked around a fill; a widget left blocked is a dead control."""
    path = aux_status(tmp_path, [CTRL], {"ls336.setpoint1": 4.2})
    w = ViewerWindow(str(path), refresh_ms=10_000_000)
    w.refresh()
    assert not w.setpoint_spin.signalsBlocked()
    assert not w.range_combo.signalsBlocked()
    w.close()


def test_an_outage_reaches_the_curve_as_a_break_and_not_a_line(tmp_path, qt_app):
    """The recorder stopped for an hour; the pen lifts rather than ruling across.

    What the window owes `connect_flags` is that its answer actually gets to
    pyqtgraph -- the arithmetic itself is tested where it lives, without Qt.
    """
    t0 = time.time() - 7200
    csv = tmp_path / "log.csv"
    with csv.open("w") as fh:
        fh.write(HEADER)
        for i in list(range(600)) + list(range(4200, 4800)):
            stamp = _dt.datetime.fromtimestamp(t0 + i).isoformat(timespec="milliseconds")
            fh.write(f"{stamp},{i}.0,{96.0:.4f},77.0,12.5,,,\n")
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "t_wall": time.time(), "cycle": 3, "running": True, "interval_s": 1.0,
        "channels": [{"name": "Sample", "kelvin": 96.0, "usable": True}],
        "links": [{"name": "ls336", "up": True, "writable": True}],
        "recorder": {"path": str(csv), "rows": 1200},
        "commands": {"accepted": True, "recent": []},
    }))
    w = ViewerWindow(str(status), refresh_ms=10_000_000)
    qt_app.processEvents()

    connect = w.curves["Sample"].curve.opts["connect"]
    assert not isinstance(connect, str), "the whole trace was drawn as one line"
    assert list(connect).count(0) == 1
    # The break is at the last sample before the hour off, not anywhere else.
    t = w.curves["Sample"].getData()[0]
    broken = list(connect).index(0)
    assert t[broken + 1] - t[broken] > 3000
    w.close()


def test_a_recorder_that_never_stopped_is_still_one_unbroken_trace(viewer):
    assert viewer.curves["Sample"].curve.opts["connect"] == "all"


# -- the value axis has a comfort stop, not a clamp ---------------------------
#
# A 300 K axis panned out to 10 000 K is a chart nobody can read.  A sensor
# that has come loose and reads 1400 K is a chart somebody has to be able to
# read.  The stop has to allow the second while resisting the first, which
# means it is the wider of the configured window and the data.


def y_limits(plot):
    return plot.getViewBox().state["limits"]["yLimits"]


def test_the_value_axis_stops_where_the_viewer_says(viewer):
    lo, hi = y_limits(viewer.k_plot)
    assert lo <= 0.0
    assert hi == pytest.approx(450.0)
    lo, hi = y_limits(viewer.pct_plot)
    assert hi == pytest.approx(100.0)


def test_a_reading_outside_the_stop_widens_it_to_the_reading(tmp_path, qt_app):
    """The measured number wins.  An axis that would not go to 1400 K would be
    hiding the one reading that matters."""
    t0 = time.time() - 600
    csv_path = tmp_path / "log.csv"
    with csv_path.open("w") as fh:
        fh.write(HEADER)
        for i in range(120):
            stamp = _dt.datetime.fromtimestamp(t0 + i).isoformat(
                timespec="milliseconds")
            # A miswired sensor, reading well past the comfort stop.
            fh.write(f"{stamp},{i}.0,1400.0,77.0,12.5,,,\n")
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "t_wall": time.time(), "cycle": 1, "running": True, "interval_s": 1.0,
        "channels": [{"name": "Sample", "kelvin": 1400.0, "usable": True}],
        "links": [{"name": "ls336", "up": True, "writable": True}],
        "recorder": {"path": str(csv_path), "rows": 120},
        "commands": {"accepted": True, "recent": []},
    }))
    w = ViewerWindow(str(status), refresh_ms=10_000_000)
    qt_app.processEvents()
    assert y_limits(w.k_plot)[1] >= 1400.0
    w.close()


def test_the_stop_is_configurable_without_touching_a_config_file(tmp_path, qt_app):
    """In the shape of --max-points and --gap-factor: a viewer flag, no key."""
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "t_wall": time.time(), "cycle": 1, "running": True, "interval_s": 1.0,
        "channels": [], "links": [], "recorder": {"path": "", "rows": 0},
        "commands": {"accepted": False, "recent": []},
    }))
    w = ViewerWindow(str(status), refresh_ms=10_000_000, max_kelvin=20.0,
                     max_percent=50.0)
    qt_app.processEvents()
    assert y_limits(w.k_plot)[1] == pytest.approx(20.0)
    assert y_limits(w.pct_plot)[1] == pytest.approx(50.0)
    w.close()


# -- the cursors and what is between them ------------------------------------


def test_the_cursors_arrive_somewhere_useful(viewer):
    """A pair that measures nothing until it has been placed twice is a pair
    most people put away again."""
    assert viewer._cursors is None
    viewer.cursor_button.click()
    assert viewer._cursors is not None
    a, b = viewer._cursors
    x0, x1 = viewer.k_plot.getViewBox().viewRange()[0]
    assert x0 < a < b < x1
    assert viewer.k_plot.getViewBox().cursor_mode is True


def test_a_click_moves_the_nearer_cursor(viewer):
    viewer.cursor_button.click()
    a, b = viewer._cursors
    viewer._place_cursor(a + (b - a) * 0.1)      # nearer the left one
    assert viewer._cursors[1] == b
    assert viewer._cursors[0] != a


def test_the_statistics_come_from_the_full_resolution_samples(viewer):
    viewer.cursor_button.click()
    newest = viewer.tail.newest()
    viewer._cursors = (newest - 600, newest - 60)
    viewer._update_region_stats()

    stats = viewer._stats["K"]["Sample"]
    got = viewer.tail.samples_in(newest - 600, newest - 60)["Sample"]
    assert stats.n == len(got.v)
    assert stats.mean == pytest.approx(sum(got.v) / len(got.v))
    assert "mean" in viewer._stat_labels["K"].toPlainText()
    # Once for the region, not once per trace.
    assert viewer._stat_labels["K"].toPlainText().count("Δt") == 1


def test_putting_the_cursors_away_takes_the_statistics_with_them(viewer):
    viewer.cursor_button.click()
    viewer._update_region_stats()
    assert viewer._stat_labels["K"].isVisible()
    viewer.cursor_button.click()
    assert viewer._cursors is None
    assert not viewer._stat_labels["K"].isVisible()
    assert viewer.k_plot.getViewBox().cursor_mode is False
    assert not viewer.export_button.isEnabled()


def test_the_legend_carries_the_live_value_only_while_nothing_is_picked(viewer):
    """Two readings of one trace, measured over different spans, a few pixels
    apart, is how a chart comes to disagree with itself."""
    legend = viewer.k_plot.legend
    label = legend.getLabel(viewer.curves["Sample"])
    assert label.text.startswith("Sample")
    assert label.text != "Sample"                # a number is in there

    viewer.cursor_button.click()
    viewer._update_region_stats()
    assert legend.getLabel(viewer.curves["Sample"]).text == "Sample"


def test_a_region_in_the_past_is_not_re_measured_every_tick(viewer):
    """Nothing the recorder does now changes what happened between two past
    instants, and re-reading every log in the directory to confirm that once a
    second is not free."""
    viewer.cursor_button.click()
    newest = viewer.tail.newest()
    viewer._cursors = (newest - 600, newest - 60)
    viewer._update_region_stats()
    key = viewer._stats_key

    calls = []
    real = viewer.tail.samples_in
    viewer.tail.samples_in = lambda *a, **k: (calls.append(a), real(*a, **k))[1]
    viewer.refresh()
    viewer.refresh()
    assert calls == []
    assert viewer._stats_key == key


# -- the cursor gesture displaces the drag, and only while it is up -----------


def test_the_left_drag_still_zooms_while_the_cursors_are_away(viewer):
    (x0, x1), _ = drag(viewer.k_plot.getViewBox(), 0.4, 0.6)
    assert viewer._span == pytest.approx((x0, x1))


def test_the_left_button_places_cursors_instead_of_zooming_while_they_are_up(viewer):
    viewer.cursor_button.click()
    before = viewer._span
    box = viewer.k_plot.getViewBox()
    rect = box.boundingRect()
    p0 = QtCore.QPointF(rect.width() * 0.4, rect.height() * 0.4)
    p1 = QtCore.QPointF(rect.width() * 0.6, rect.height() * 0.6)
    box.mouseDragEvent(FakeDrag(p0, p1, True))
    assert viewer._span == before                  # no window was picked
    assert viewer._cursors[0] == pytest.approx(box.mapToView(p1).x()) or \
        viewer._cursors[1] == pytest.approx(box.mapToView(p1).x())


# -- naming the trace under the pointer --------------------------------------


def test_hovering_a_trace_names_it_and_reads_it_there(viewer):
    box = viewer.k_plot.getViewBox()
    t, v = viewer.curves["Sample"].getData()
    middle = len(t) // 2
    scene_pos = box.mapViewToScene(QtCore.QPointF(float(t[middle]),
                                                  float(v[middle])))
    viewer._on_hover((scene_pos,))
    label = viewer._hover_labels["K"]
    assert label.isVisible()
    text = label.toPlainText()
    assert text.startswith("Sample")
    assert f"{float(v[middle]):.3f}" in text


def test_hovering_nowhere_near_a_trace_names_nothing(viewer):
    box = viewer.k_plot.getViewBox()
    (_, y1) = box.viewRange()[1]
    t, _ = viewer.curves["Sample"].getData()
    scene_pos = box.mapViewToScene(
        QtCore.QPointF(float(t[len(t) // 2]), y1 * 10))
    viewer._on_hover((scene_pos,))
    assert not viewer._hover_labels["K"].isVisible()


# -- exporting the region ----------------------------------------------------


def test_the_export_writes_the_region_at_full_resolution(viewer, tmp_path,
                                                         monkeypatch):
    viewer.cursor_button.click()
    newest = viewer.tail.newest()
    viewer._cursors = (newest - 600, newest - 60)
    viewer._update_region_stats()
    assert viewer.export_button.isEnabled()

    out = tmp_path / "region.csv"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "")))
    viewer.export_button.click()

    lines = out.read_text().splitlines()
    assert lines[0].startswith("Timestamp,Time,Sample")
    assert len(lines) - 1 == viewer._stats["K"]["Sample"].n
    assert "region.csv" in viewer.export_note.text()


def test_a_cancelled_export_writes_nothing(viewer, monkeypatch):
    viewer.cursor_button.click()
    viewer._update_region_stats()
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: ("", "")))
    viewer.export_button.click()
    assert viewer.export_note.text() == ""


# -- the loop's gains --------------------------------------------------------
#
# Read and not asked for: the viewer holds no port.  The numbers are in the
# status file because the recorder polls PID? on a slow cadence, and a recorder
# that does not poll them has to be distinguishable from one that will not
# accept new ones.


def with_gains(tmp_path, qt_app, *, aux, commands=None):
    """A viewer on a 336 whose status file carries the given aux entries."""
    from lschart.ipc.commands import CommandSpool

    csv = tmp_path / "gains.csv"
    stamp = _dt.datetime.fromtimestamp(time.time()).isoformat(timespec="milliseconds")
    csv.write_text(HEADER + f"{stamp},0.0,96.0,77.0,12.5,,,\n")
    status = tmp_path / "status-gains.json"
    status.write_text(json.dumps({
        "t_wall": time.time(), "cycle": 3, "running": True, "interval_s": 1.0,
        "channels": [{"name": "Sample", "kelvin": 96.0, "usable": True}],
        "links": [CTRL],
        "aux": [{"name": k, "value": v} for k, v in aux.items()],
        "recorder": {"path": str(csv), "rows": 60},
        "commands": commands or {"accepted": True, "recent": []},
    }))
    w = ViewerWindow(str(status), refresh_ms=10_000_000,
                     spool=CommandSpool(tmp_path / "cmd-gains"))
    w.refresh()
    return w


def test_the_gain_boxes_fill_from_what_the_recorder_read(tmp_path, qt_app):
    w = with_gains(tmp_path, qt_app,
                   aux={"ls336.p1": 60.0, "ls336.i1": 25.0, "ls336.d1": 3.0})
    assert showing(w.pid_group)
    assert w.pid_spins["p"].value() == pytest.approx(60.0)
    assert w.pid_spins["i"].value() == pytest.approx(25.0)
    assert w.pid_spins["d"].value() == pytest.approx(3.0)


def test_gains_that_are_not_polled_say_so_rather_than_showing_zero(tmp_path, qt_app):
    """Blank because nobody is looking is not the same as refused."""
    w = with_gains(tmp_path, qt_app, aux={})
    assert "read_pid" in w.pid_note.text()


def test_a_recorder_that_will_not_retune_says_that_instead(tmp_path, qt_app):
    w = with_gains(tmp_path, qt_app, aux={"ls336.p1": 60.0},
                   commands={"accepted": True, "recent": [], "allow_pid": False})
    assert "ipc.allow_pid" in w.pid_note.text()


def test_a_shut_pid_gate_keeps_the_numbers_readable_and_the_button_dead(
        tmp_path, qt_app):
    """The gains are the one control worth *reading* where it cannot be
    written, so the two halves are treated differently on purpose.

    Greying the numbers would take away the thing that still works. Leaving
    the button live would offer a click that can only ever produce a refusal,
    which is exactly the shape A3 removed from the range control. Found on the
    bench 336: the button was live behind a shut gate.
    """
    w = with_gains(tmp_path, qt_app, aux={"ls336.p1": 60.0},
                   commands={"accepted": True, "recent": [], "allow_pid": False})
    assert all(spin.isEnabled() for spin in w.pid_spins.values())
    assert not w.pid_button.isEnabled()
    w.close()


def test_an_open_pid_gate_leaves_the_button_live(tmp_path, qt_app):
    w = with_gains(tmp_path, qt_app, aux={"ls336.p1": 60.0},
                   commands={"accepted": True, "recent": [], "allow_pid": True})
    assert w.pid_button.isEnabled()
    w.close()


def test_gains_may_be_sent_to_a_recorder_that_does_not_read_them_back(
        tmp_path, qt_app):
    """A missing capability is not a withheld permission. `read_pid: false`
    means the boxes are not the instrument's -- which the note says -- not
    that the instrument refuses new ones. `set_pid()` verifies by readback."""
    w = with_gains(tmp_path, qt_app, aux={},
                   commands={"accepted": True, "recent": [], "allow_pid": True})
    assert "read_pid" in w.pid_note.text()
    assert w.pid_button.isEnabled()
    w.close()


def test_a_recorder_that_will_retune_says_it_applies_no_power(tmp_path, qt_app):
    w = with_gains(tmp_path, qt_app, aux={"ls336.p1": 60.0},
                   commands={"accepted": True, "recent": [], "allow_pid": True})
    assert "does not apply power" in w.pid_note.text()


def test_all_three_gains_go_out_in_one_command(tmp_path, qt_app, monkeypatch):
    """PID is one command on the instrument; sending one gain would be a
    read-modify-write against a box somebody else may be touching."""
    w = with_gains(tmp_path, qt_app,
                   aux={"ls336.p1": 60.0, "ls336.i1": 25.0, "ls336.d1": 3.0})
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.pid_spins["p"].setValue(75.0)
    w._send_pid()
    sent = queued(w)
    assert len(sent) == 1
    assert sent[0]["kind"] == "pid"
    assert (sent[0]["p"], sent[0]["i"], sent[0]["d"]) == (75.0, 25.0, 3.0)
    assert sent[0]["loop"] == 1


def test_editing_a_gain_stops_the_fill_fighting_the_typing(tmp_path, qt_app):
    w = with_gains(tmp_path, qt_app,
                   aux={"ls336.p1": 60.0, "ls336.i1": 25.0, "ls336.d1": 3.0})
    w.pid_spins["i"].setValue(99.0)
    w.refresh()
    assert w.pid_spins["i"].value() == pytest.approx(99.0)


def test_the_gains_follow_the_selected_loop(tmp_path, qt_app):
    w = with_gains(tmp_path, qt_app,
                   aux={"ls336.p1": 60.0, "ls336.i1": 25.0, "ls336.d1": 3.0,
                        "ls336.p2": 10.0, "ls336.i2": 5.0, "ls336.d2": 0.0})
    w._loop = 2
    w._sync_command_values()
    assert w.pid_spins["p"].value() == pytest.approx(10.0)


def test_a_box_with_no_loops_offers_no_gains(tmp_path, qt_app):
    w = cryostat(tmp_path, qt_app, [MON])
    assert not showing(w.pid_group)


# -- the panic menu ----------------------------------------------------------
#
# Three clicks by design: open the menu, choose the action, confirm it. These
# are needed almost never and must not be reachable by accident.


def test_the_panic_menu_holds_both_ways_of_stopping(tmp_path, qt_app):
    w = cryostat(tmp_path, qt_app, [CTRL])
    labels = [a.text() for a in w.panic_button.menu().actions()]
    assert any("heaters OFF" in t for t in labels)
    assert any("HOLD" in t for t in labels)
    w.close()


def test_hold_is_aimed_at_the_recorder_and_not_at_one_box(tmp_path, qt_app,
                                                          monkeypatch):
    w = cryostat(tmp_path, qt_app, [CTRL, MON])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.hold_action.trigger()
    (cmd,) = queued(w)
    assert cmd["kind"] == "hold" and cmd["instrument"] == ""
    w.close()


def test_cancelling_the_confirmation_sends_nothing(tmp_path, qt_app, monkeypatch):
    w = cryostat(tmp_path, qt_app, [CTRL])
    monkeypatch.setattr(w, "_confirm", lambda *a: False)
    w.hold_action.trigger()
    w.off_action.trigger()
    assert queued(w) == []
    w.close()


def test_arm_is_outside_the_panic_menu(tmp_path, qt_app, monkeypatch):
    """It applies power. Sitting beside the stopping actions would suggest it
    shares their exemptions, and it shares none of them."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    labels = [a.text() for a in w.panic_button.menu().actions()]
    assert not any("Arm" in t for t in labels)

    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.arm_button.click()
    (cmd,) = queued(w)
    assert cmd["kind"] == "arm" and cmd["instrument"] == ""
    w.close()


def test_the_source_policy_does_not_grey_out_the_panic_menu(tmp_path, qt_app):
    """The recorder would obey it. Disabling it here would be a lie."""
    w = cryostat(tmp_path, qt_app, [CTRL], commands={
        "accepted": True, "recent": [], "source_policy": True,
        "source_default": False,
        "sources": [{"name": "lschart-gui", "allowed": False,
                     "configured": False, "disabled_at_runtime": False}],
    })
    w.refresh()
    assert not w.command_group.isEnabled()
    assert w.panic_button.isEnabled()
    w.close()


def test_a_pending_command_does_not_lock_the_panic_menu(tmp_path, qt_app,
                                                        monkeypatch):
    """No pending command can make it wrong to stop."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.send_button.click()                      # queues a setpoint, unacked
    assert not w.send_button.isEnabled()
    assert w.panic_button.isEnabled()
    w.close()


# -- muting this viewer, from this viewer ------------------------------------
#
# The control that undoes the thing which disables the command group cannot
# live inside the command group. The `source` command is exempt from the policy
# it edits precisely so this works when nothing else in the panel does.


def muted(allowed=False, configured=True):
    return {"accepted": True, "recent": [], "source_policy": True,
            "source_default": True,
            "sources": [{"name": "lschart-gui", "allowed": allowed,
                         "configured": configured,
                         "disabled_at_runtime": not allowed}]}


def test_the_toggle_shows_the_recorder_is_listening(tmp_path, qt_app):
    w = cryostat(tmp_path, qt_app, [CTRL])
    w.refresh()
    assert w.source_check.isChecked()
    w.close()


def test_a_muted_viewer_can_still_untick_its_way_back(tmp_path, qt_app,
                                                      monkeypatch):
    w = cryostat(tmp_path, qt_app, [CTRL], commands=muted())
    w.refresh()
    assert not w.source_check.isChecked()
    # The command group is off, and this is not in it.
    assert not w.command_group.isEnabled()
    assert w.source_check.isEnabled()

    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.source_check.setChecked(True)
    (cmd,) = queued(w)
    assert cmd["kind"] == "source"
    assert cmd["name"] == "lschart-gui" and cmd["allowed"] is True
    w.close()


def test_muting_asks_first_and_says_reading_carries_on(tmp_path, qt_app,
                                                       monkeypatch):
    w = cryostat(tmp_path, qt_app, [CTRL])
    w.refresh()
    seen = {}
    monkeypatch.setattr(w, "_confirm",
                        lambda title, text: (seen.update(text=text), True)[1])
    w.source_check.setChecked(False)
    assert "not a command" in seen["text"]
    assert "one-way door" in seen["text"]
    (cmd,) = queued(w)
    assert cmd["allowed"] is False
    w.close()


def test_cancelling_the_mute_puts_the_tick_back(tmp_path, qt_app, monkeypatch):
    """A checkbox left unticked after a cancelled confirmation would say the
    recorder is ignoring this viewer when it is not."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    w.refresh()
    monkeypatch.setattr(w, "_confirm", lambda *a: False)
    w.source_check.setChecked(False)
    assert queued(w) == []
    assert w.source_check.isChecked()
    w.close()


def test_the_periodic_fill_does_not_send_a_command(tmp_path, qt_app, monkeypatch):
    """A refresh is not a click. This runs on a one-second timer."""
    w = cryostat(tmp_path, qt_app, [CTRL], commands=muted())
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    for _ in range(3):
        w.refresh()
    assert queued(w) == []
    w.close()


def test_a_config_refusal_cannot_be_ticked_away(tmp_path, qt_app):
    """The overlay may only narrow, so offering the click would be offering a
    refusal."""
    w = cryostat(tmp_path, qt_app, [CTRL],
                 commands=muted(allowed=False, configured=False))
    w.refresh()
    assert not w.source_check.isEnabled()
    assert "restart" in w.source_check.toolTip()
    w.close()


def test_a_muted_viewer_still_draws_everything(tmp_path, qt_app):
    """Muted is about listening, not reading. A panel full of greyed-out
    controls must not be mistaken for a broken viewer."""
    w = cryostat(tmp_path, qt_app, [CTRL], commands=muted())
    w.refresh()
    assert w.readouts.rowCount() > 0
    assert w.loops.rowCount() > 0
    assert w.panic_button.isEnabled()
    w.close()


# -- X1: the software loop's own row -----------------------------------------
#
# A viewer pointed at a running `ltspm3` used to show the heater percent as a
# trace and say nothing about the loop driving it -- not its setpoint, not its
# health, and not that it had locked itself out after a fault.  The loop that
# most needed watching was the one loop with no row.

SOFTWARE = {
    "state": "tracking", "mode": "pid", "health": "ok", "sensor": "Sample",
    "setpoint_k": 96.0, "setpoint_target_k": 96.0, "ramping": False,
    "error_k": 0.02, "output_pct": 63.07, "demand_pct": 63.10,
    "rail_low_pct": 62.076, "rail_high_pct": 64.076, "threshold_k": 1.0,
    "alarms": [], "reason": "",
}


def cells(window, row):
    return [window.loops.item(row, c).text() for c in range(window.loops.columnCount())]


def test_a_plain_recorder_grows_no_software_row(tmp_path, qt_app):
    """Most recorders have no controller at all, and `control` is null."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    assert w.loops.rowCount() == 4
    assert [w.loops.item(r, 0).text() for r in range(4)] == ["1", "2", "3", "4"]
    w.close()


def test_the_software_loop_is_the_last_row_and_carries_its_own_sensor(
        tmp_path, qt_app):
    """Last, after every loop that lives on a box; and the kelvin column fills
    itself, because the sensor is named by the same string the readout uses."""
    w = cryostat(tmp_path, qt_app, [CTRL], control=dict(SOFTWARE))
    assert w.loops.rowCount() == 5
    row = cells(w, 4)
    assert row[0] == "sw"                      # not a loop number
    assert row[1] == "Sample"
    assert row[2] == "96.000"                  # from the channel readout
    assert row[3] == "96.000" and row[4] == "63.1"
    assert row[5] == "n/a"                     # no range, not an unknown one
    assert row[6] == "tracking"
    w.close()


def test_a_recorder_that_is_only_a_software_loop_still_gets_a_table(
        tmp_path, qt_app):
    """A 218 has no loops of its own.  Before this the table was hidden
    entirely on exactly the cryostat that has a loop worth watching."""
    w = cryostat(tmp_path, qt_app, [MON], control=dict(SOFTWARE))
    assert w.loops.rowCount() == 1 and showing(w.loops)
    w.close()


def test_the_software_row_cannot_be_selected_into_the_command_panel(
        tmp_path, qt_app):
    """It takes no setpoint, range or PID command -- only Arm and the panic
    Hold.  A row that could be clicked into a selection the panel cannot
    honour would be a row that lies."""
    w = cryostat(tmp_path, qt_app, [CTRL], control=dict(SOFTWARE))
    w.loops.selectRow(1)                       # loop 2, a real one
    assert w._loop == 2
    w.loops.selectRow(4)                       # the software row
    assert w._loop == 2                        # unmoved
    assert not w.loops.item(4, 0).flags() & QtCore.Qt.ItemIsSelectable
    assert w.loops.item(0, 0).flags() & QtCore.Qt.ItemIsSelectable
    w.close()


def test_a_locked_out_software_loop_says_so_in_the_state_column(
        tmp_path, qt_app):
    """A fault ramp-down ending in a lockout is the single most important
    thing this row exists to show, and it must not need a hover."""
    w = cryostat(tmp_path, qt_app, [CTRL],
                 control=dict(SOFTWARE, state="locked_out", mode="pid",
                              health="fault", output_pct=0.0,
                              alarms=["sensor lost for 60 s"]))
    assert cells(w, 4)[6] == "locked out"
    assert "sensor lost for 60 s" in w.loops.item(4, 1).toolTip()
    w.close()


def test_a_held_loop_is_told_apart_from_one_that_was_never_armed(
        tmp_path, qt_app):
    """Both sit at state `idle`.  The mode is what separates them, and it goes
    in the hover because the State column has room for one word."""
    held = cryostat(tmp_path, qt_app, [CTRL],
                    control=dict(SOFTWARE, state="idle", mode="manual"))
    assert "mode manual" in held.loops.item(4, 1).toolTip()
    held.close()
    never = cryostat(tmp_path, qt_app, [CTRL],
                     control=dict(SOFTWARE, state="idle", mode="off"))
    assert "mode off" in never.loops.item(4, 1).toolTip()
    never.close()


def test_a_ramping_down_loop_is_not_abbreviated_into_an_ordinary_ramp(
        tmp_path, qt_app):
    """"ramping" alone would read as a setpoint traversal.  This one is a
    fault backing the heater off."""
    w = cryostat(tmp_path, qt_app, [CTRL],
                 control=dict(SOFTWARE, state="ramping_down", health="fault"))
    assert cells(w, 4)[6] == "ramping down"
    w.close()


def test_a_software_loop_that_is_not_tracking_lights_neither_mark(
        tmp_path, qt_app):
    """Same rule as a heater at range 0: a loop that was never going to the
    setpoint is not failing to reach it."""
    w = cryostat(tmp_path, qt_app, [CTRL],
                 control=dict(SOFTWARE, mode="manual", state="idle",
                              demand_pct=99.0, setpoint_k=40.0))
    row = cells(w, 4)
    assert row[7] == "" and row[8] == ""
    w.close()


def test_the_software_loop_rails_at_its_own_clamp(tmp_path, qt_app):
    """Its band is about a percent wide.  Against the fixed 99% a heater uses,
    this mark could never light."""
    w = cryostat(tmp_path, qt_app, [CTRL],
                 control=dict(SOFTWARE, demand_pct=64.5))
    assert cells(w, 4)[7] == "RAIL"
    assert "64.076%" in w.loops.item(4, 7).toolTip()
    w.close()


def test_an_unhealthy_software_loop_is_coloured_even_with_both_marks_dark(
        tmp_path, qt_app):
    """The marks go quiet exactly when the supervisor stops trusting itself,
    which is the moment the row most needs to catch an eye."""
    w = cryostat(tmp_path, qt_app, [CTRL],
                 control=dict(SOFTWARE, health="fault", state="holding",
                              reason="reading rejected"))
    assert cells(w, 4)[7] == "" and cells(w, 4)[8] == ""
    assert w.loops.item(4, 1).foreground().color().name() == warn_colour(w)
    # And a healthy row has no colour of its own at all -- it is whatever the
    # palette says, which is the only value that is right on both themes.
    assert w.loops.item(0, 1).data(QtCore.Qt.ItemDataRole.ForegroundRole) is None
    w.close()


def test_the_instrument_rows_gained_the_state_column_too(tmp_path, qt_app):
    """It decides whether either mark applies, and it used to be reachable
    only by hovering -- so a loop switched to open loop was invisible."""
    w = cryostat(tmp_path, qt_app, [dict(CTRL, loops=[
        loop_entry(1), loop_entry(2, mode="open loop", mode_code=3),
        loop_entry(3), loop_entry(4, mode="off", mode_code=0)])])
    assert [cells(w, r)[6] for r in range(4)] == ["closed", "open", "closed", "off"]
    w.close()


# -- the viewer on a dark desktop --------------------------------------------
#
# Reported from macOS dark mode: the tables forced #000000 onto a #171717 base.
# `tests/test_gui_theme.py` checks the palettes are legible; these check the
# window actually uses them, and keeps using them when the desktop changes
# under a viewer that is already open.


def repaint(qt_app, window, dark: bool):
    """Hand the window a light or dark palette and tell it, as Qt would."""
    pal = QtGui.QPalette(window.palette())
    ground, base = ("#323232", "#171717") if dark else ("#f0f0f0", "#ffffff")
    pal.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(ground))
    pal.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(base))
    window.setPalette(pal)
    qt_app.sendEvent(window, QtCore.QEvent(QtCore.QEvent.Type.PaletteChange))


def test_an_ordinary_reading_is_never_given_a_colour_of_its_own(tmp_path, qt_app):
    """The bug, at its root. A usable reading is ordinary text, and ordinary
    text is whatever the palette says -- forcing black made it invisible on a
    dark desktop, and forcing white would do the same on a light one."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    role = QtCore.Qt.ItemDataRole.ForegroundRole
    assert w.readouts.item(0, 1).data(role) is None
    assert w.loops.item(0, 0).data(role) is None
    w.close()


def test_a_rejected_reading_still_gets_a_colour(tmp_path, qt_app):
    """Clearing the normal case must not clear the exceptional one."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    w.source.status["channels"] = [
        {"name": "Sample", "kelvin": 96.0, "usable": False, "validity": "rejected"}]
    w._update_readouts()
    assert w.readouts.item(0, 1).foreground().color().name() == warn_colour(w)
    w.close()


def test_the_window_follows_the_desktop_changing_theme_under_it(tmp_path, qt_app):
    """A macOS appearance switch, a Windows toggle, a Qt style swap. Every
    colour is resolved at call time so one sweep puts the whole window right,
    rather than waiting for the next refresh tick."""
    w = cryostat(tmp_path, qt_app, [dict(CTRL, loops=[
        loop_entry(1, mode_code=1, range=3, output_pct=100.0)])])
    repaint(qt_app, w, dark=True)
    assert w.loop_note.styleSheet() == theme.note_style("muted", w)
    assert theme.DARK["muted"] in w.loop_note.styleSheet()
    dark_mark = w.loops.item(0, COL_SATURATED).foreground().color().name()
    assert dark_mark == theme.DARK["bad"]

    repaint(qt_app, w, dark=False)
    assert theme.LIGHT["muted"] in w.loop_note.styleSheet()
    assert w.loops.item(0, COL_SATURATED).foreground().color().name() == \
        theme.LIGHT["bad"]
    w.close()


def test_the_banner_repaints_for_the_new_theme_too(tmp_path, qt_app):
    """It is the one element that paints its own background, so a stale
    stylesheet there is a light chip punched into a dark window."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    repaint(qt_app, w, dark=True)
    assert theme.BANNER[True][w.source.health()[0]][0] in w.banner.styleSheet()
    repaint(qt_app, w, dark=False)
    assert theme.BANNER[False][w.source.health()[0]][0] in w.banner.styleSheet()
    w.close()


def test_a_trace_toggle_does_not_colour_its_own_text(tmp_path, qt_app):
    """The curve colour has to match a line on the white plot, so it cannot be
    re-themed for a dark panel. It becomes a stripe and the name is left to
    the palette -- cyan was 2.26:1 as text on white, brown 2.17:1 on dark."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    w.refresh()
    for check in w.toggles.values():
        assert "border-left" in check.styleSheet()
        assert "color:" not in check.styleSheet()
    w.close()


def test_the_loop_table_never_scrolls_sideways(tmp_path, qt_app):
    """The marks live in the last two columns, so a sideways scroll hides the
    very thing the table exists to show. Adding the State column pushed nine
    columns of contents into a narrower panel and did exactly that -- the
    fourth loop went behind the scrollbar and `Off SP` off the edge."""
    w = cryostat(tmp_path, qt_app, [CTRL])
    w.resize(1500, 900)
    w.show()
    for _ in range(3):
        w.refresh()
        qt_app.processEvents()
    table = w.loops
    assert not table.horizontalScrollBar().isVisible()
    total = sum(table.columnWidth(c) for c in range(table.columnCount()))
    assert total <= table.viewport().width()
    # And every row is reachable, not just the columns.
    assert table.rowCount() == 4
    w.close()


def test_a_long_sensor_name_elides_rather_than_widening_the_table(tmp_path, qt_app):
    """The sensor is the column that gives, because it is the one repeated in
    the readouts above and in the row's own tooltip. A truncated name is still
    identifiable; a mark scrolled off the edge is not there at all."""
    w = cryostat(tmp_path, qt_app, [dict(CTRL, loops=[
        loop_entry(1, "A sensor with an unreasonably long label")])])
    w.resize(1500, 900)
    w.show()
    for _ in range(3):
        w.refresh()
        qt_app.processEvents()
    assert not w.loops.horizontalScrollBar().isVisible()
    assert w.loops.item(0, COL_SATURATED) is not None
    w.close()


def test_the_ordinary_sensor_names_are_not_elided_at_the_default_width(
        tmp_path, qt_app):
    """At the old 430 px both "Stage 1" and "Stage 2" came out as "Stag…",
    which is worse than useless -- two different loops reading the same."""
    w = cryostat(tmp_path, qt_app, [dict(CTRL, loops=[
        loop_entry(1, "Coldplate"), loop_entry(2, "Stage 2"),
        loop_entry(3, "Rad Shield"), loop_entry(4, "Stage 1")])])
    w.resize(1500, 900)
    w.show()
    for _ in range(3):
        w.refresh()
        qt_app.processEvents()
    metrics = w.loops.fontMetrics()
    width = w.loops.columnWidth(COL_SENSOR)
    for name in ("Coldplate", "Stage 2", "Rad Shield", "Stage 1"):
        assert metrics.horizontalAdvance(name) <= width, f"{name} would elide"
    w.close()


def test_a_gate_note_is_given_the_height_its_wrapping_needs(tmp_path, qt_app):
    """A word-wrapped QLabel reports a one-line sizeHint, so the layout gave
    it one line and clipped the rest -- the range note ended mid-sentence at
    "which is exempt from thi". These notes are the only explanation of why a
    control is dead, so half of one is worse than none."""
    w = cryostat(tmp_path, qt_app, [CTRL],
                 commands={"accepted": True, "recent": [],
                           "allow_heater_range": False})
    w.resize(1500, 900)
    w.show()
    for _ in range(3):
        w.refresh()
        qt_app.processEvents()
    note = w.range_note
    assert note.text(), "the note should be saying why the control is dead"
    assert note.height() >= note.heightForWidth(note.width())
    w.close()
