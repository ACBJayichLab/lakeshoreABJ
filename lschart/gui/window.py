"""The strip-chart window.  Qt lives here and nowhere else in the package.

A viewer that can also command, and the distinction matters: it holds no
instrument link and takes no lock, so it can be opened, closed and reopened
while the recorder runs, and two people can watch the same rig at once.  Every
control in it writes exactly the file MATLAB writes and is refused by exactly
the same interlocks -- it has no privileges MATLAB lacks, and what it has
instead is a confirmation dialog that says out loud which buttons apply power.

Which controls exist is decided by what the *recorder* says the selected
instrument has, not by a model-number table kept in here.  A 33x takes a
setpoint and a range and they are genuinely separate acts; a 218 has neither,
just one analog percentage that *is* the power.  Keeping that knowledge in the
recorder means it is not the same table going stale in three clients.

Two plots, not one, and they are stacked and x-linked rather than overlaid.  A
heater percent and a temperature share no axis: 63% and 63 K are different
quantities, and drawing them against one scale invites reading a trend across
the two.  Setpoints go on the kelvin axis, beside the channel they are chasing.

Dragging a rectangle on either panel zooms to exactly that rectangle, because
the question a strip chart gets asked is "what happened between *there* and
*there*", and the preset windows in the combo can only answer it when the
interesting part happens to end now -- and because a 2 mK wobble on a 300 K
axis is invisible until the value axis is cropped to it too.  The X and Y
buttons take an axis out of the drag for the times when only one of them is
the question.  A hand-picked view stops following the recorder -- new samples
land off the right-hand edge, which is what a fixed window means -- so the
state is announced on the button beside the combo and is left by a
double-click, that button, or picking a preset.
"""

from __future__ import annotations

import logging
import os

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from ..instruments.ls33x import HEATER_RANGE_NAMES
from ..ipc.commands import CommandSpool
from .source import CsvTail, StatusSource, capabilities, classify_column

log = logging.getLogger(__name__)

#: Distinguishable at a glance, and distinguishable from each other when
#: printed in greyscale, which is what happens to a plot that gets into a
#: lab notebook.
CURVE_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
    "#8c564b", "#17becf", "#bcbd22", "#e377c2", "#7f7f7f",
]

#: (label, seconds).  ``None`` means everything the log holds.
TIME_WINDOWS = [
    ("10 min", 600.0), ("1 hour", 3600.0), ("6 hours", 21600.0),
    ("24 hours", 86400.0), ("All", None),
]

BANNER_STYLE = {
    "ok":      "background:#e8f5e9; color:#1b5e20; padding:6px; border-radius:4px;",
    "stale":   "background:#fff3e0; color:#e65100; padding:6px; border-radius:4px;",
    "stopped": "background:#eceff1; color:#37474f; padding:6px; border-radius:4px;",
    "absent":  "background:#ffebee; color:#b71c1c; padding:6px; border-radius:4px;",
}


def _duration(seconds: float) -> str:
    """A span in whatever unit keeps it to a couple of digits."""
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


class ZoomViewBox(pg.ViewBox):
    """A view box whose left-drag picks a zoom rectangle instead of panning.

    The rectangle is taken literally: drag one out and both axes become
    exactly its edges, the value axis included.  A drag that is flat in one
    direction -- shorter than ``MIN_DRAG_PX`` across, or shorter than that
    tall -- sets only the axis it actually spans, because reaching for a time
    window with a level hand is the common gesture and cropping the
    temperature axis to a hair by accident is the common accident.  A drag
    that is short both ways is a click that wobbled, and does nothing.

    ``zoom_x`` and ``zoom_y`` take an axis out of the gesture altogether; the
    X and Y buttons beside the window combo are what set them, and with one of
    them off the band spans the full width or height to show it.

    Panning is still on the mouse, under ``Shift`` -- not ``Ctrl``, which macOS
    turns into a right-click before Qt ever sees it -- and so is the wheel
    zoom, the middle-drag pan and the right-click menu.  Nothing pyqtgraph
    offered before is taken away; the left drag is the only gesture reassigned.
    """

    #: ``((t0, t1) or None, (y0, y1) or None)`` -- each pair ordered, and
    #: ``None`` for an axis the drag did not span.  Emitted once, on release.
    sigRegionSelected = QtCore.Signal(object, object)
    #: A double-click anywhere in the panel: go back to following the recorder.
    sigViewReset = QtCore.Signal()

    #: A drag shorter than this is a click that wobbled, not a selection.  In
    #: pixels, because that is the unit of the wobble.
    MIN_DRAG_PX = 6.0

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: Which axes the left-drag may set.  Both, until a button says else.
        self.zoom_x = True
        self.zoom_y = True
        self._band = QtWidgets.QGraphicsRectItem()
        # Width 0 keeps the pen cosmetic: the item lives in data coordinates,
        # where one x unit is a second and a scaled pen would be a smear.
        self._band.setPen(pg.mkPen("#1f77b4", width=0))
        self._band.setBrush(pg.mkBrush(31, 119, 180, 45))
        self._band.setZValue(1e9)
        self._band.hide()
        self.addItem(self._band, ignoreBounds=True)

    def mouseDragEvent(self, ev, axis=None) -> None:  # noqa: N802 - Qt/pyqtgraph name
        pan = ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
        if axis is not None or ev.button() != QtCore.Qt.MouseButton.LeftButton or pan:
            # Dragging an axis, or with Shift held, keeps its usual meaning.
            super().mouseDragEvent(ev, axis=axis)
            return
        ev.accept()
        down, here = ev.buttonDownPos(), ev.pos()
        # The thresholds are judged in pixels, on the way in; what comes back
        # out is in data coordinates, where one x unit is a second.
        wide = self.zoom_x and abs(here.x() - down.x()) >= self.MIN_DRAG_PX
        tall = self.zoom_y and abs(here.y() - down.y()) >= self.MIN_DRAG_PX
        p0, p1 = self.mapToView(down), self.mapToView(here)
        x = (min(p0.x(), p1.x()), max(p0.x(), p1.x())) if wide else None
        y = (min(p0.y(), p1.y()), max(p0.y(), p1.y())) if tall else None
        if not ev.isFinish():
            self._show_band(x, y)
            return
        self._band.hide()
        if x is None and y is None:
            return
        self.sigRegionSelected.emit(x, y)

    def mouseClickEvent(self, ev) -> None:  # noqa: N802 - Qt/pyqtgraph name
        if ev.double():
            ev.accept()
            self.sigViewReset.emit()
            return
        super().mouseClickEvent(ev)   # the right-click menu still belongs here

    def _show_band(self, x, y) -> None:
        """Preview the drag.  An axis it does not span is drawn edge to edge."""
        if x is None and y is None:
            # Nothing has crossed the threshold yet, and shading the whole
            # panel for a click-and-hold would promise a zoom that is not
            # coming.
            self._band.hide()
            return
        view_x, view_y = self.viewRange()
        x0, x1 = x if x is not None else view_x
        y0, y1 = y if y is not None else view_y
        self._band.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self._band.show()


class ViewerWindow(QtWidgets.QMainWindow):
    """Reads the recorder's files on a timer and draws them."""

    def __init__(
        self,
        status_path: str,
        *,
        spool: CommandSpool | None = None,
        refresh_ms: int = 1000,
        max_points: int = 200_000,
        config_label: str = "",
    ) -> None:
        super().__init__()
        self.source = StatusSource(status_path)
        self.tail = CsvTail(max_points=max_points)
        self.spool = spool
        self.config_label = config_label

        self.curves: dict[str, pg.PlotDataItem] = {}
        self.toggles: dict[str, QtWidgets.QCheckBox] = {}
        self._pending: tuple[str, float] | None = None   # (command id, deadline)
        self._first_load_done = False
        #: The hand-picked window, (t0, t1) in epoch seconds, or None while the
        #: view is following the recorder.  When it is set it is the authority
        #: on what gets drawn -- including after a wheel zoom or a Shift-drag,
        #: which arrive here as a range change like any other.
        self._span: tuple[float, float] | None = None
        #: The hand-picked value axis of each panel, keyed by its unit, or None
        #: where that panel is still autoscaling to what it is showing.  One
        #: per panel and not one shared: a kelvin axis and a percent axis have
        #: nothing to say to each other, which is why there are two panels.
        self._ylim: dict[str, tuple[float, float] | None] = {"K": None, "%": None}

        self.setWindowTitle("lschart — strip chart")
        self.resize(1280, 800)
        self._build()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(max(200, refresh_ms))
        self.refresh()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        pg.setConfigOptions(antialias=True, background="w", foreground="k")

        central = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(central)

        self.banner = QtWidgets.QLabel("starting…")
        self.banner.setStyleSheet(BANNER_STYLE["absent"])
        outer.addWidget(self.banner)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(self._left_panel())
        splitter.addWidget(self._plots())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 900])
        outer.addWidget(splitter, 1)

        # Connected here rather than where the buttons are built, because the
        # panels they aim at do not exist until the line above has run.
        for button in (self.zoom_x_button, self.zoom_y_button):
            button.toggled.connect(
                lambda _checked, b=button: self._zoom_axes_changed(b))

        self.setCentralWidget(central)
        self.statusBar().showMessage("waiting for the recorder…")
        # Settle the control panel before the first poll.  Otherwise a viewer
        # opened against a recorder with nothing writable shows every control,
        # greyed out -- which reads as "this rig has all of these" rather than
        # "this rig has none of them".
        self._instrument_changed()

    def _left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        box = QtWidgets.QVBoxLayout(panel)

        self.readouts = QtWidgets.QTableWidget(0, 2)
        self.readouts.setHorizontalHeaderLabels(["Channel", "Kelvin"])
        self.readouts.horizontalHeader().setStretchLastSection(True)
        self.readouts.verticalHeader().setVisible(False)
        self.readouts.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.readouts.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        # Live values are what someone walks over to read from across the room.
        font = self.readouts.font()
        font.setPointSize(font.pointSize() + 3)
        self.readouts.setFont(font)
        self.readouts.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                    QtWidgets.QSizePolicy.Fixed)
        # Sized to its rows in _update_readouts, not given a stretch: a rig
        # with four channels should not reserve half the panel for the six it
        # does not have, while the trace list underneath goes unscrollable.
        box.addWidget(self.readouts, 0)

        window_row = QtWidgets.QHBoxLayout()
        window_row.addWidget(QtWidgets.QLabel("Show"))
        self.window_combo = QtWidgets.QComboBox()
        for label, _ in TIME_WINDOWS:
            self.window_combo.addItem(label)
        self.window_combo.setCurrentIndex(1)          # 1 hour
        # A preset is a decision to follow the recorder again, so picking one
        # leaves a hand-picked window rather than fighting with it.
        self.window_combo.currentIndexChanged.connect(self._follow_live)
        window_row.addWidget(self.window_combo, 1)

        self.live_button = QtWidgets.QPushButton("Live")
        self.live_button.setEnabled(False)
        self.live_button.setToolTip(
            "drag across a plot to pick a time window; this returns to "
            "following the recorder (so does a double-click on the plot)")
        self.live_button.clicked.connect(self._follow_live)
        window_row.addWidget(self.live_button, 0)
        box.addLayout(window_row)

        zoom_row = QtWidgets.QHBoxLayout()
        zoom_row.addWidget(QtWidgets.QLabel("Drag zooms"))
        # Checkable rather than momentary: which axes the mouse is about to
        # take is a mode, and a mode that is not visible is a mode that gets
        # blamed on the plot.  Both on is a rectangle; one on is a band.
        self.zoom_x_button = QtWidgets.QPushButton("X")
        self.zoom_x_button.setToolTip(
            "let a drag set the time axis\n"
            "turn off to zoom the value axis alone")
        self.zoom_y_button = QtWidgets.QPushButton("Y")
        self.zoom_y_button.setToolTip(
            "let a drag set the value axis of the panel dragged\n"
            "turn off to pick a time window without touching it")
        for button in (self.zoom_x_button, self.zoom_y_button):
            button.setCheckable(True)
            button.setChecked(True)     # connected in _build, once plots exist
            button.setMaximumWidth(44)
            zoom_row.addWidget(button, 0)
        zoom_row.addStretch(1)
        box.addLayout(zoom_row)

        traces = QtWidgets.QGroupBox("Traces")
        self.traces_layout = QtWidgets.QVBoxLayout(traces)
        self.traces_layout.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(traces)
        scroll.setMinimumHeight(160)
        # The one thing in this panel that should absorb spare height: a rig
        # with two instruments has a dozen traces, and hunting for one of them
        # through a three-line window is the difference between a usable
        # viewer and a tolerated one.
        box.addWidget(scroll, 1)

        box.addWidget(self._command_box())
        self.links_label = QtWidgets.QLabel("")
        self.links_label.setWordWrap(True)
        box.addWidget(self.links_label)
        return panel

    def _command_box(self) -> QtWidgets.QWidget:
        """The control panel: one instrument selector, then whatever it can do.

        Three controls rather than one, because the rigs this drives are not
        the same shape.  A 33x takes a setpoint and a range and they are
        genuinely separate acts -- the setpoint is inert until the range is
        raised.  A 218 has neither: one analog percentage that *is* the power.
        Which controls appear is decided by what the recorder says the selected
        box actually has, not by a model-number table kept in here.

        Every one of them writes the same file MATLAB writes and is refused by
        the same interlocks.  The viewer has no privileges; what it has is a
        confirmation dialog that says out loud which of these applies power.
        """
        self.command_group = QtWidgets.QGroupBox("Command")
        box = QtWidgets.QVBoxLayout(self.command_group)

        top = QtWidgets.QFormLayout()
        self.instrument_combo = QtWidgets.QComboBox()
        self.instrument_combo.currentIndexChanged.connect(self._instrument_changed)
        top.addRow("Instrument", self.instrument_combo)
        box.addLayout(top)

        box.addWidget(self._setpoint_group())
        box.addWidget(self._range_group())
        box.addWidget(self._analog_group())

        # Never gated on anything.  The safe direction is always available, and
        # a panic button that can be greyed out is not one.
        self.off_button = QtWidgets.QPushButton("All heaters OFF")
        self.off_button.setToolTip(
            "Every heater this recorder may write to, to zero: 33x ranges and "
            "218 analog outputs alike. Boxes it may not write to are left "
            "alone and named in the reply.")
        self.off_button.setStyleSheet("font-weight:bold; padding:4px;")
        self.off_button.clicked.connect(self._send_heaters_off)
        box.addWidget(self.off_button)

        self.ack_label = QtWidgets.QLabel("")
        self.ack_label.setWordWrap(True)
        box.addWidget(self.ack_label)
        return self.command_group

    def _setpoint_group(self) -> QtWidgets.QWidget:
        self.setpoint_group = QtWidgets.QGroupBox("Setpoint")
        form = QtWidgets.QFormLayout(self.setpoint_group)

        self.loop_spin = QtWidgets.QSpinBox()
        self.loop_spin.setRange(1, 4)
        form.addRow("Loop", self.loop_spin)

        self.setpoint_spin = QtWidgets.QDoubleSpinBox()
        self.setpoint_spin.setRange(0.0, 1000.0)
        self.setpoint_spin.setDecimals(3)
        self.setpoint_spin.setSuffix(" K")
        self.setpoint_spin.setValue(0.0)
        form.addRow("Target", self.setpoint_spin)

        self.send_button = QtWidgets.QPushButton("Send setpoint…")
        self.send_button.clicked.connect(self._send_setpoint)
        form.addRow(self.send_button)
        return self.setpoint_group

    def _range_group(self) -> QtWidgets.QWidget:
        """Heater range.  Off / low / medium / high, and it applies power.

        Held back from the first cut of this viewer on the grounds that
        applying power from a chart is a different decision from typing it.
        That is still true, which is why the dialog for a non-zero range is
        blunter than the setpoint one -- but refusing to offer the control at
        all just means the operator walks to another terminal, and does it
        there without the chart in front of them.
        """
        self.range_group = QtWidgets.QGroupBox("Heater range")
        form = QtWidgets.QFormLayout(self.range_group)

        self.heater_combo = QtWidgets.QComboBox()
        form.addRow("Output", self.heater_combo)

        self.range_combo = QtWidgets.QComboBox()
        for value, label in sorted(HEATER_RANGE_NAMES.items()):
            self.range_combo.addItem(f"{value} — {label}", value)
        form.addRow("Range", self.range_combo)

        self.range_button = QtWidgets.QPushButton("Set range…")
        self.range_button.clicked.connect(self._send_range)
        form.addRow(self.range_button)

        self.range_note = QtWidgets.QLabel("")
        self.range_note.setWordWrap(True)
        self.range_note.setStyleSheet("color:#e65100;")
        form.addRow(self.range_note)
        return self.range_group

    def _analog_group(self) -> QtWidgets.QWidget:
        """A 218 analog output, in percent.  The whole heater, in one number.

        The spin box is capped at the recorder's own ``max_output_pct`` rather
        than at 100, so the widget cannot express a value the recorder is going
        to refuse -- and so the ceiling is visible without reading the config.
        """
        self.analog_group = QtWidgets.QGroupBox("Analog output")
        form = QtWidgets.QFormLayout(self.analog_group)

        self.analog_spin = QtWidgets.QDoubleSpinBox()
        self.analog_spin.setRange(0.0, 100.0)
        self.analog_spin.setDecimals(3)
        self.analog_spin.setSuffix(" %")
        self.analog_spin.setValue(0.0)
        form.addRow("Output", self.analog_spin)

        self.analog_button = QtWidgets.QPushButton("Set output…")
        self.analog_button.clicked.connect(self._send_analog)
        form.addRow(self.analog_button)

        self.analog_note = QtWidgets.QLabel("")
        self.analog_note.setWordWrap(True)
        self.analog_note.setStyleSheet("color:#e65100;")
        form.addRow(self.analog_note)
        return self.analog_group

    def _plots(self) -> QtWidgets.QWidget:
        layout = pg.GraphicsLayoutWidget()

        self.k_plot = layout.addPlot(row=0, col=0, viewBox=ZoomViewBox())
        self.k_plot.setLabel("left", "Temperature", units="K")
        self.k_plot.showGrid(x=True, y=True, alpha=0.25)
        self.k_plot.addLegend(offset=(-10, 10))
        self.k_plot.setAxisItems({"bottom": pg.DateAxisItem()})

        self.pct_plot = layout.addPlot(row=1, col=0, viewBox=ZoomViewBox())
        self.pct_plot.setLabel("left", "Output", units="%")
        self.pct_plot.showGrid(x=True, y=True, alpha=0.25)
        self.pct_plot.setAxisItems({"bottom": pg.DateAxisItem()})
        # One pan or zoom moves both: comparing a heater step against the
        # temperature it caused is the whole reason there are two panels.
        self.pct_plot.setXLink(self.k_plot)

        #: The panels by the unit of their value axis.  A drag has to say which
        #: one it came from, because the time axis it picked is shared and the
        #: value axis it picked is not.
        self._panels = {"K": self.k_plot, "%": self.pct_plot}

        # Either panel may be dragged; both mean the same time window, and the
        # link carries it to the other one.
        for unit, plot in self._panels.items():
            vb = plot.getViewBox()
            vb.sigRegionSelected.connect(
                lambda x, y, u=unit: self._select_region(u, x, y))
            vb.sigViewReset.connect(self._follow_live)
            # A value axis can also be moved by the wheel or a Shift-drag, and
            # then it is just as fixed as one that was dragged out; noticing
            # here is what keeps the Live button honest about it.
            plot.sigYRangeChanged.connect(
                lambda _vb, rng, u=unit: self._y_range_changed(u, rng))
        # Everything that can move the time axis -- the drag, the wheel, a
        # Shift-drag, the linked panel -- arrives here, so there is one place
        # that decides what the window is and what data belongs in it.
        self.k_plot.sigXRangeChanged.connect(self._x_range_changed)

        layout.ci.layout.setRowStretchFactor(0, 3)
        layout.ci.layout.setRowStretchFactor(1, 1)
        # The gesture has to be discoverable by someone who will not read the
        # documentation, which is everyone standing at a cryostat at 2 a.m.
        layout.setToolTip(
            "Drag a rectangle on either panel to zoom to exactly it.\n"
            "The X and Y buttons take an axis out of the drag.\n"
            "Shift-drag pans · wheel zooms · double-click follows the "
            "recorder again.")
        return layout

    # -- the tick ----------------------------------------------------------

    def refresh(self) -> None:
        """One poll of both files.  Must never raise: it is on a timer."""
        try:
            self.source.poll()
            self._update_banner()
            self._update_readouts()
            self._update_links()
            self._update_commands()
            if self.tail.follow(self.source.log_path()):
                self._first_load_done = False
            if self.tail.poll() or not self._first_load_done:
                self._first_load_done = True
                self._sync_traces()
                self._redraw()
            self._update_statusbar()
        except Exception:  # noqa: BLE001 - a drawing bug must not stop the viewer
            log.exception("refresh failed; the viewer continues")

    def _update_banner(self) -> None:
        state, message = self.source.health()
        age = self.source.age_s
        prefix = {
            "ok": "RECORDING", "stale": "NOT UPDATING",
            "stopped": "STOPPED", "absent": "NO RECORDER",
        }[state]
        age_text = f" · {age:.1f} s ago" if age is not None else ""
        self.banner.setText(f"{prefix}{age_text} — {message}")
        self.banner.setStyleSheet(BANNER_STYLE[state])

    def _update_readouts(self) -> None:
        channels = self.source.channels()
        if self.readouts.rowCount() != len(channels):
            self.readouts.setRowCount(len(channels))
            self.readouts.resizeRowsToContents()
            height = self.readouts.horizontalHeader().height() + 2 * self.readouts.frameWidth()
            for row in range(len(channels)):
                height += self.readouts.rowHeight(row)
            self.readouts.setFixedHeight(height)
        for row, ch in enumerate(channels):
            name = str(ch.get("name", "?"))
            kelvin = ch.get("kelvin")
            usable = bool(ch.get("usable"))
            text = "—" if kelvin is None else f"{float(kelvin):.4f}"
            if not usable:
                # Never a bare number for a rejected sample: the whole point of
                # the validity flag is that this reading is not a measurement.
                text = f"{text}  ({ch.get('validity', 'rejected')})"
            self._set_cell(row, 0, name)
            item = self._set_cell(row, 1, text)
            item.setForeground(QtGui.QBrush(
                QtGui.QColor("#000000" if usable else "#b71c1c")))

    def _set_cell(self, row: int, col: int, text: str) -> QtWidgets.QTableWidgetItem:
        item = self.readouts.item(row, col)
        if item is None:
            item = QtWidgets.QTableWidgetItem()
            self.readouts.setItem(row, col, item)
        item.setText(text)
        return item

    def _update_links(self) -> None:
        lines = []
        for link in self.source.links():
            state = "up" if link.get("up") else "DOWN"
            extra = ""
            if link.get("reconnects"):
                extra += f", {link['reconnects']} reconnect(s)"
            if link.get("last_error"):
                extra += f" — {link['last_error']}"
            lines.append(f"{link.get('name', '?')} ({link.get('model', '?')}): "
                         f"{state}{extra}")
        self.links_label.setText("\n".join(lines))

    def _update_commands(self) -> None:
        """Keep the command panel honest about what it can actually do."""
        names = [str(link.get("name", ""))
                 for link in self.source.writable_links()]
        if [self.instrument_combo.itemText(i)
                for i in range(self.instrument_combo.count())] != names:
            # Rebuilding drops the selection, so put it back: this runs on a
            # one-second timer, and a combo that reset itself every tick would
            # be unusable.  Only the *list* changing gets here at all.
            chosen = self.instrument_combo.currentText()
            self.instrument_combo.blockSignals(True)
            self.instrument_combo.clear()
            self.instrument_combo.addItems(names)
            if chosen in names:
                self.instrument_combo.setCurrentIndex(names.index(chosen))
            self.instrument_combo.blockSignals(False)
            self._instrument_changed()

        accepted = self.source.accepts_commands()
        enabled = bool(self.spool) and accepted and bool(names)
        self.command_group.setEnabled(enabled)
        if not self.spool:
            why = "this viewer was started without a command spool"
        elif not accepted:
            why = ("the recorder is not accepting commands — set "
                   "ipc.accept_commands: true in its config and restart it")
        elif not names:
            why = ("no instrument on this recorder allows writes — set "
                   "allow_writes: true on the box you mean to drive")
        else:
            why = "writes a command file the recorder picks up on its next cycle"
        self.command_group.setToolTip(why)
        self._update_gate_notes()

    def _instrument_changed(self, *_ignored) -> None:
        """Show the controls the selected box has, and only those.

        Called when the selection changes rather than every tick, because the
        heater-output combo and the analog ceiling are things the operator may
        be part-way through using.
        """
        link = self.source.link_named(self.instrument_combo.currentText())
        caps = capabilities(link)

        self.setpoint_group.setVisible(caps["has_loops"])
        if caps["loops"]:
            self.loop_spin.setRange(min(caps["loops"]), max(caps["loops"]))

        self.range_group.setVisible(caps["has_heater_range"])
        outputs = [str(n) for n in caps["heater_outputs"]]
        if [self.heater_combo.itemText(i)
                for i in range(self.heater_combo.count())] != outputs:
            self.heater_combo.clear()
            self.heater_combo.addItems(outputs)

        self.analog_group.setVisible(caps["has_analog"])
        if caps["has_analog"]:
            ceiling = caps["max_output_pct"]
            self.analog_spin.setMaximum(ceiling)
            self.analog_group.setTitle(
                f"Analog output {caps['analog_output']} (max {ceiling:g}%)")
        self._update_gate_notes()

    def _update_gate_notes(self) -> None:
        """Say which of the two power gates is open, without disabling anything.

        Greying these out would be the wrong shape.  Both commands are always
        allowed in the direction that removes heat -- range 0, output 0% -- so a
        disabled control would take away the one thing that always works, and
        on a rig you have just decided to make safe that is precisely the wrong
        moment to hide the button.
        """
        if self.source.allows_heater_range():
            self.range_note.setText("")
        else:
            self.range_note.setText(
                "This recorder will not raise a range from a file "
                "(ipc.allow_heater_range: false). Setting 0 still works.")
        if self.source.allows_analog_output():
            self.analog_note.setText(
                "No ramp: this is one step, as fast as the plant allows.")
            self.analog_note.setStyleSheet("color:#37474f;")
        else:
            self.analog_note.setText(
                "This recorder will not drive an output above 0 from a file "
                "(ipc.allow_analog_output: false). Setting 0 still works.")
            self.analog_note.setStyleSheet("color:#e65100;")

        if self._pending is not None:
            cid, deadline = self._pending
            ack = self.source.ack_for(cid)
            if ack is not None:
                ok = bool(ack.get("ok"))
                self.ack_label.setText(
                    ("✓ " if ok else "✗ ") + str(ack.get("message", "")))
                self.ack_label.setStyleSheet(
                    "color:#1b5e20;" if ok else "color:#b71c1c;")
                self._pending = None
                for button in self._buttons():
                    button.setEnabled(True)
            elif QtCore.QDateTime.currentSecsSinceEpoch() > deadline:
                self.ack_label.setText(
                    "no acknowledgement — the recorder may not be reading commands")
                self.ack_label.setStyleSheet("color:#e65100;")
                self._pending = None
                for button in self._buttons():
                    button.setEnabled(True)

    def _update_statusbar(self) -> None:
        status = self.source.status or {}
        rec = status.get("recorder") or {}
        bits = [self.config_label] if self.config_label else []
        if rec.get("path"):
            bits.append(f"{os.path.basename(rec['path'])} · {rec.get('rows', 0)} rows")
        bits.append(f"{self.tail.rows} rows plotted")
        if self._span is not None:
            t0, t1 = self._span
            stamp = QtCore.QDateTime.fromSecsSinceEpoch
            bits.append(
                f"window {stamp(int(t0)).toString('HH:mm:ss')}–"
                f"{stamp(int(t1)).toString('HH:mm:ss')} "
                f"({_duration(t1 - t0)}) · not following")
        for unit, fixed in self._ylim.items():
            if fixed is not None:
                bits.append(f"y {fixed[0]:g}–{fixed[1]:g} {unit} fixed")
        self.statusBar().showMessage("   ".join(bits))

    # -- plotting ----------------------------------------------------------

    def _sync_traces(self) -> None:
        """Create a curve and a checkbox for any column the log has grown."""
        channel_names = {str(c.get("name")) for c in self.source.channels()}
        for name in self.tail.columns():
            if name in self.curves:
                continue
            kind = classify_column(name, channel_names)
            if kind == "other":
                # `rangeN` is a 0..3 enumeration, not a measurement; drawing it
                # as a line would imply an intermediate value means something.
                continue
            plot = self.k_plot if kind == "kelvin" else self.pct_plot
            colour = CURVE_COLORS[len(self.curves) % len(CURVE_COLORS)]
            curve = plot.plot([], [], pen=pg.mkPen(colour, width=2), name=name)
            self.curves[name] = curve

            check = QtWidgets.QCheckBox(name)
            check.setChecked(True)
            check.setStyleSheet(f"color:{colour}; font-weight:600;")
            check.stateChanged.connect(self._redraw)
            self.toggles[name] = check
            self.traces_layout.insertWidget(self.traces_layout.count() - 1, check)

    def _redraw(self) -> None:
        seconds = TIME_WINDOWS[self.window_combo.currentIndex()][1]
        for name, curve in self.curves.items():
            if not self.toggles[name].isChecked():
                curve.setData([], [])
                continue
            if self._span is None:
                t, v = self.tail.window(name, seconds)
            else:
                # Exactly the visible span, so a panel still autoscaling fits
                # itself to what is on screen: zoom into a five-minute wobble
                # and the wobble fills the panel instead of a day's excursion.
                # A panel whose y axis was dragged out keeps the axis it was
                # given; the cut still matters, for the other panel and for
                # the number of points Qt is asked to draw.
                t, v = self.tail.between(name, *self._span)
            curve.setData(t, v)

    # -- choosing the window with the mouse --------------------------------

    def _select_region(self, unit: str, x, y) -> None:
        """A drag finished on the panel measured in `unit`: take it literally.

        The time axis is shared, so it goes to both panels over the link.  The
        value axis is not, so it goes only to the panel that was dragged --
        the other one keeps autoscaling to whatever the new window holds.
        """
        if y is not None:
            self._ylim[unit] = (y[0], y[1])
            # Autoscale off first, or the next redraw refits the axis to the
            # data and the rectangle that was just dragged is gone.
            self._panels[unit].enableAutoRange(y=False)
            self._panels[unit].setYRange(y[0], y[1], padding=0)
        if x is not None:
            self._span = (x[0], x[1])
            for plot in self._panels.values():
                plot.enableAutoRange(x=False)
            self.k_plot.setXRange(x[0], x[1], padding=0)
        self._span_changed()

    def _x_range_changed(self, _vb, rng) -> None:
        """The time axis moved by any other route -- wheel, Shift-drag, link.

        Only meaningful once a window has been picked by hand: while the view
        is following the recorder this fires on every autoscale, and the combo
        is what decides the window then.
        """
        if self._span is None:
            return
        span = (float(rng[0]), float(rng[1]))
        if span == self._span:
            return
        self._span = span
        self._span_changed()

    def _y_range_changed(self, unit: str, rng) -> None:
        """The value axis of one panel moved.

        Only a deliberate move counts.  While the panel is autoscaling this
        fires on every redraw, and an axis that is merely following its data
        is not a view someone has to be offered a way out of.
        """
        if self._panels[unit].getViewBox().autoRangeEnabled()[1]:
            return
        fixed = (float(rng[0]), float(rng[1]))
        if self._ylim[unit] == fixed:
            return
        self._ylim[unit] = fixed
        # Deliberately not `_span_changed`: the value axis does not decide
        # which samples are drawn, so there is nothing to redraw for.
        self.live_button.setEnabled(not self._is_live())
        self._update_statusbar()

    def _is_live(self) -> bool:
        """True while every axis is following the data rather than a decision."""
        return self._span is None and not any(self._ylim.values())

    def _follow_live(self, *_ignored) -> None:
        """Drop every hand-picked axis and follow the recorder again.

        Takes and ignores whatever the sender passes -- a combo index, a
        button's checked flag -- because three different widgets mean it.
        """
        if self._is_live():
            self._redraw()          # a combo change with nothing to leave
            return
        self._span = None
        for unit, plot in self._panels.items():
            self._ylim[unit] = None
            plot.enableAutoRange(x=True, y=True)
        self._span_changed()

    def _zoom_axes_changed(self, source: QtWidgets.QPushButton) -> None:
        """The X or Y button moved: hand the new mode to both panels."""
        if not self.zoom_x_button.isChecked() and not self.zoom_y_button.isChecked():
            # A drag that zooms neither axis is a dead gesture.  The one just
            # switched off comes back on rather than leaving the mouse inert,
            # and re-entry here through `toggled` settles the panels.
            source.setChecked(True)
            return
        for plot in self._panels.values():
            vb = plot.getViewBox()
            vb.zoom_x = self.zoom_x_button.isChecked()
            vb.zoom_y = self.zoom_y_button.isChecked()

    def _span_changed(self) -> None:
        self.live_button.setEnabled(not self._is_live())
        self._redraw()
        self._update_statusbar()

    # -- commanding --------------------------------------------------------

    def _buttons(self) -> list[QtWidgets.QPushButton]:
        """Everything that can queue a command, so one pending command locks all.

        Not just the button that was pressed: commands are applied in order on
        the recorder's next cycle, and letting a second one be queued while the
        first is unacknowledged is how you get a range raised against a
        setpoint that turned out to be refused.
        """
        return [self.send_button, self.range_button,
                self.analog_button, self.off_button]

    def _confirm(self, title: str, text: str) -> bool:
        return QtWidgets.QMessageBox.question(
            self, title, text,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        ) == QtWidgets.QMessageBox.Yes

    def _queue(self, kind: str, *, instrument: str | None = None, **args) -> None:
        """Submit one command and start waiting for its acknowledgement.

        ``instrument=""`` addresses the recorder rather than one box, which is
        what ``heaters_off`` wants; the default is whatever is selected.
        """
        if self.spool is None:
            return
        if instrument is None:
            instrument = self.instrument_combo.currentText()
        try:
            cid = self.spool.submit(
                kind, instrument=instrument, source="lschart-gui", **args,
            )
        except OSError as exc:
            self.ack_label.setText(f"could not queue the command: {exc}")
            self.ack_label.setStyleSheet("color:#b71c1c;")
            return
        self.ack_label.setText(f"queued {kind} {cid}, waiting for the recorder…")
        self.ack_label.setStyleSheet("color:#37474f;")
        for button in self._buttons():
            button.setEnabled(False)
        # The recorder refuses anything older than its TTL, so waiting longer
        # than that could only ever report a refusal it has already decided.
        self._pending = (
            cid,
            QtCore.QDateTime.currentSecsSinceEpoch() + int(self.spool.ttl_s),
        )

    def _send_setpoint(self) -> None:
        """Queue a setpoint, after saying out loud what is about to happen."""
        if self.spool is None:
            return
        instrument = self.instrument_combo.currentText()
        loop = self.loop_spin.value()
        kelvin = self.setpoint_spin.value()
        if not self._confirm(
            "Send setpoint",
            f"Set loop {loop} of {instrument} to {kelvin:.3f} K?\n\n"
            "This changes where the instrument's own PID loop is going. It "
            "does not turn a heater on: a setpoint does nothing while the "
            "heater range is 0.",
        ):
            return
        self._queue("setpoint", loop=loop, kelvin=kelvin)

    def _send_range(self) -> None:
        """Queue a heater range.  Above 0 this is the command that applies power."""
        if self.spool is None:
            return
        instrument = self.instrument_combo.currentText()
        output = int(self.heater_combo.currentText() or 1)
        value = int(self.range_combo.currentData())
        name = HEATER_RANGE_NAMES.get(value, value)
        if value == 0:
            text = (f"Turn heater {output} of {instrument} OFF?\n\n"
                    "The setpoint is left where it is; with the range at 0 it "
                    "does nothing.")
        else:
            # Deliberately blunter than the setpoint dialog.  This is the one
            # click in the viewer that puts heat into a cryostat.
            text = (
                f"Set heater {output} of {instrument} to range {value} "
                f"({name})?\n\n"
                "THIS APPLIES POWER. The loop will immediately begin driving "
                f"toward its setpoint, which reads\n\n    "
                f"{self._setpoint_now(instrument)}\n\n"
                "If you have only just changed that setpoint, the recorder may "
                "not have read it back yet. Check it before continuing."
            )
        if not self._confirm("Set heater range", text):
            return
        self._queue("range", output=output, value=value)

    def _setpoint_now(self, instrument: str) -> str:
        """What the box says its setpoint is, for the range dialog.

        Read from the status file rather than asked for, because the viewer
        holds no instrument link — and therefore **carries an age**, which is
        quoted rather than hidden. The recorder's cycle order is read → apply
        commands → write status, so the aux block written alongside a setpoint
        acknowledgement still holds the value from *before* it. Someone who
        sets a setpoint and then reaches for the range would otherwise be shown
        the old number at the exact moment it matters most.

        Polled first so it is as fresh as the file allows, and reported with
        "unknown" rather than a guess when the recorder does not carry it.
        """
        self.source.poll()
        loop = self.loop_spin.value()
        for entry in (self.source.status or {}).get("aux", []):
            if entry.get("name") == f"{instrument}.setpoint{loop}":
                value = entry.get("value")
                if value is not None:
                    return (f"{float(value):.3f} K on loop {loop}, as the "
                            f"recorder read it {self.source.age_s or 0.0:.0f} s "
                            f"ago")
        return f"not reported by the recorder for loop {loop}"

    def _send_analog(self) -> None:
        """Queue an analog output percentage.  Above 0 this IS the heater."""
        if self.spool is None:
            return
        instrument = self.instrument_combo.currentText()
        percent = self.analog_spin.value()
        caps = capabilities(self.source.link_named(instrument))
        if percent == 0:
            text = (f"Set the analog output of {instrument} to 0%?\n\n"
                    "This removes power from the heater on that output.")
        else:
            text = (
                f"Set the analog output of {instrument} to {percent:.3f}%?\n\n"
                "THIS APPLIES POWER. This box has no loop and no range: the "
                "percentage is the power, and there is no setpoint that has "
                "to be reached first.\n\n"
                "There is NO RAMP. The output goes there in one step and the "
                "plant follows as fast as it can.\n\n"
                f"The recorder's ceiling is {caps['max_output_pct']:g}%. Know "
                "the gain of your heater before confirming — on a cryostat "
                "sample heater a single percent can be tens of kelvin."
            )
        if not self._confirm("Set analog output", text):
            return
        self._queue("analog", percent=percent)

    def _send_heaters_off(self) -> None:
        """The panic button.  Every heater the recorder may write to, to zero."""
        if self.spool is None:
            return
        if not self._confirm(
            "All heaters off",
            "Turn OFF every heater this recorder may write to?\n\n"
            "33x heater ranges to 0 and 218 analog outputs to 0%. "
            "Instruments the recorder is configured read-only for are left "
            "alone — on a shared cryostat those are somebody else's.\n\n"
            "Setpoints are not changed.",
        ):
            return
        # Addressed to the recorder, not to a box: the whole point is that it
        # does not stop at whichever instrument happens to be selected.
        self._queue("heaters_off", instrument="")
