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
    assert not viewer.live_button.isChecked()

    if leave == "double-click":
        box.mouseClickEvent(FakeDoubleClick())
    else:
        viewer.live_button.click()

    assert viewer._span is None
    assert viewer._ylim == {"K": None, "%": None}
    assert box.autoRangeEnabled() == [True, True]
    assert viewer.live_button.isChecked()


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
    assert viewer.live_button.isChecked()
    viewer.zoom_buttons["X+"].click()
    assert not viewer.live_button.isChecked()
    assert not viewer.k_plot.getViewBox().autoRangeEnabled()[0]
    assert "not following" in viewer.statusBar().currentMessage()


def test_a_y_zoom_stops_the_axis_autoscaling(viewer):
    viewer.zoom_buttons["Y+"].click()
    # The view row describes the *time* window, and time still follows: the
    # button stays checked.  The fixed kelvin axis is named in the status bar.
    assert viewer.live_button.isChecked()
    assert not viewer.k_plot.getViewBox().autoRangeEnabled()[1]
    # The time axis is untouched: the chart still follows the recorder in x.
    assert viewer._span is None
    assert viewer.k_plot.getViewBox().autoRangeEnabled()[0]


def test_a_y_zoom_survives_the_next_poll_of_the_files(viewer):
    viewer.zoom_buttons["Y+"].click()
    fixed = viewer._ylim["K"]
    viewer.refresh()
    assert viewer.k_plot.getViewBox().viewRange()[1] == pytest.approx(list(fixed))


def test_live_undoes_the_buttons_too(viewer):
    viewer.zoom_buttons["X+"].click()
    viewer.zoom_buttons["Y+"].click()
    viewer.live_button.click()
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

    assert len(w.curves["Sample"].getData()[0]) == 600   # All shows everything
    w.span_buttons[6 * 3600.0].click()
    shown = len(w.curves["Sample"].getData()[0])
    assert 360 <= shown <= 362          # 6 h at one minute, plus the bracket
    assert "last 6.0 h" in w.statusBar().currentMessage()
    assert w.span_buttons[6 * 3600.0].isChecked()
    assert not w.live_button.isChecked()

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
    assert not viewer.live_button.isChecked()
    # And a view button is a way back out of the hand-picked span.
    viewer.span_buttons[12 * 3600.0].click()
    assert viewer._span is None
    assert viewer._follow_span_s == 12 * 3600.0
    assert viewer.live_button.isChecked() is False


# -- the control panel -------------------------------------------------------
#
# What is worth testing here is not that Qt draws a spin box.  It is that the
# panel offers the controls the selected box actually has, that the one number
# a 218 accepts cannot be typed past the recorder's ceiling, and that the panic
# button is not aimed at whichever instrument happens to be selected.


def cryostat(tmp_path, qt_app, links, commands=None, csv_name="log.csv"):
    """A viewer watching a recorder with the given instruments."""
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
        "commands": commands or {"accepted": True, "recent": []},
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


CTRL = {"name": "ls336", "model": "336", "up": True, "writable": True,
        "loops": [1, 2, 3, 4], "heater_outputs": [1, 2],
        "analog_output": None, "max_output_pct": 100.0}

MON = {"name": "ls218", "model": "218", "up": True, "writable": True,
       "loops": [], "heater_outputs": [], "analog_output": 1,
       "max_output_pct": 70.0}


def queued(window) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(window.spool.pending())]


def test_a_controller_gets_a_setpoint_and_a_range_but_no_analog_control(
        tmp_path, qt_app):
    w = cryostat(tmp_path, qt_app, [CTRL])
    assert showing(w.setpoint_group) and showing(w.range_group)
    assert not showing(w.analog_group)
    assert w.loop_spin.maximum() == 4
    assert [w.heater_combo.itemText(i)
            for i in range(w.heater_combo.count())] == ["1", "2"]
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


def test_a_shut_gate_is_announced_and_does_not_disable_the_control(
        tmp_path, qt_app):
    """Greying it out would remove the one direction that always works.

    Range 0 and 0% are always permitted, so a disabled control would take the
    button away at exactly the moment somebody wants to make the cryostat safe.
    """
    w = cryostat(tmp_path, qt_app, [MON],
            commands={"accepted": True, "recent": [],
                      "allow_analog_output": False})
    assert "allow_analog_output" in w.analog_note.text()
    assert w.analog_button.isEnabled()
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
    w.heater_combo.setCurrentIndex(1)                 # output 2
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
    w.off_button.click()

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
        "commands": {"accepted": True, "recent": []},
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
    w.loop_spin.setValue(2)
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
    w.heater_combo.setCurrentIndex(1)               # output 2
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
    w.loop_spin.setValue(2)
    w.loop_spin.setValue(1)
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
