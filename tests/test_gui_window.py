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


# -- the control panel -------------------------------------------------------
#
# What is worth testing here is not that Qt draws a spin box.  It is that the
# panel offers the controls the selected box actually has, that the one number
# a 218 accepts cannot be typed past the recorder's ceiling, and that the panic
# button is not aimed at whichever instrument happens to be selected.


def rig(tmp_path, qt_app, links, commands=None, csv_name="log.csv"):
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
    w = rig(tmp_path, qt_app, [CTRL])
    assert showing(w.setpoint_group) and showing(w.range_group)
    assert not showing(w.analog_group)
    assert w.loop_spin.maximum() == 4
    assert [w.heater_combo.itemText(i)
            for i in range(w.heater_combo.count())] == ["1", "2"]
    w.close()


def test_a_218_gets_an_analog_control_and_neither_of_the_others(tmp_path, qt_app):
    """It has no loop to aim a setpoint at, and no range to raise."""
    w = rig(tmp_path, qt_app, [MON])
    assert showing(w.analog_group)
    assert not showing(w.setpoint_group) and not showing(w.range_group)
    w.close()


def test_the_recorders_ceiling_caps_the_spin_box(tmp_path, qt_app):
    """The widget must not be able to express a value that will be refused."""
    w = rig(tmp_path, qt_app, [MON])
    assert w.analog_spin.maximum() == 70.0
    w.analog_spin.setValue(90.0)
    assert w.analog_spin.value() == 70.0
    assert "70" in w.analog_group.title()
    w.close()


def test_switching_instrument_switches_the_controls(tmp_path, qt_app):
    """The LTSPM shape, if both boxes were writable: one panel, two shapes."""
    w = rig(tmp_path, qt_app, [CTRL, MON])
    w.instrument_combo.setCurrentIndex(0)
    assert showing(w.setpoint_group) and not showing(w.analog_group)
    w.instrument_combo.setCurrentIndex(1)
    assert showing(w.analog_group) and not showing(w.setpoint_group)
    w.close()


def test_a_read_only_box_is_not_offered_as_a_target(tmp_path, qt_app):
    theirs = dict(CTRL, writable=False)
    w = rig(tmp_path, qt_app, [theirs, MON])
    assert [w.instrument_combo.itemText(i)
            for i in range(w.instrument_combo.count())] == ["ls218"]
    w.close()


def test_a_shut_gate_is_announced_and_does_not_disable_the_control(
        tmp_path, qt_app):
    """Greying it out would remove the one direction that always works.

    Range 0 and 0% are always permitted, so a disabled control would take the
    button away at exactly the moment somebody wants to make the rig safe.
    """
    w = rig(tmp_path, qt_app, [MON],
            commands={"accepted": True, "recent": [],
                      "allow_analog_output": False})
    assert "allow_analog_output" in w.analog_note.text()
    assert w.analog_button.isEnabled()
    w.close()


def test_an_open_gate_still_warns_that_there_is_no_ramp(tmp_path, qt_app):
    w = rig(tmp_path, qt_app, [MON],
            commands={"accepted": True, "recent": [],
                      "allow_analog_output": True})
    assert "No ramp" in w.analog_note.text()
    w.close()


def test_sending_an_analog_percent_queues_the_right_command(
        tmp_path, qt_app, monkeypatch):
    w = rig(tmp_path, qt_app, [MON])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.analog_spin.setValue(43.0)
    w.analog_button.click()

    (cmd,) = queued(w)
    assert cmd["kind"] == "analog" and cmd["percent"] == 43.0
    assert cmd["instrument"] == "ls218"
    w.close()


def test_sending_a_heater_range_queues_the_right_command(
        tmp_path, qt_app, monkeypatch):
    w = rig(tmp_path, qt_app, [CTRL])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.heater_combo.setCurrentIndex(1)                 # output 2
    w.range_combo.setCurrentIndex(3)                  # range 3, high
    w.range_button.click()

    (cmd,) = queued(w)
    assert cmd["kind"] == "range" and cmd["output"] == 2 and cmd["value"] == 3
    w.close()


def test_cancelling_the_dialog_queues_nothing(tmp_path, qt_app, monkeypatch):
    w = rig(tmp_path, qt_app, [MON])
    monkeypatch.setattr(w, "_confirm", lambda *a: False)
    w.analog_spin.setValue(43.0)
    w.analog_button.click()
    assert queued(w) == []
    w.close()


def test_raising_power_is_confirmed_in_blunter_terms_than_lowering_it(
        tmp_path, qt_app, monkeypatch):
    """The dialog is the only thing between a click and heat in a cryostat."""
    seen = []
    w = rig(tmp_path, qt_app, [MON])
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
    w = rig(tmp_path, qt_app, [CTRL])
    monkeypatch.setattr(w, "_confirm", lambda title, text: seen.append(text) or True)
    w.range_combo.setCurrentIndex(2)
    w.range_button.click()
    assert "77.000 K" in seen[-1]
    w.close()


def test_the_panic_button_is_not_aimed_at_the_selected_instrument(
        tmp_path, qt_app, monkeypatch):
    """It means stop heating, which on a two-box rig is not one box."""
    w = rig(tmp_path, qt_app, [CTRL, MON])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.instrument_combo.setCurrentIndex(0)
    w.off_button.click()

    (cmd,) = queued(w)
    assert cmd["kind"] == "heaters_off" and cmd["instrument"] == ""
    w.close()


def test_one_unacknowledged_command_locks_every_button(tmp_path, qt_app, monkeypatch):
    """Otherwise a range can be queued against a setpoint that was refused."""
    w = rig(tmp_path, qt_app, [CTRL])
    monkeypatch.setattr(w, "_confirm", lambda *a: True)
    w.send_button.click()
    assert not any(b.isEnabled() for b in w._buttons())
    w.close()


def test_an_acknowledgement_releases_every_button(tmp_path, qt_app, monkeypatch):
    w = rig(tmp_path, qt_app, [CTRL])
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
