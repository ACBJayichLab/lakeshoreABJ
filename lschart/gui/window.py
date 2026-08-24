"""The strip-chart window.  Qt lives here and nowhere else in the package.

A viewer, not a controller.  It holds no instrument link, takes no lock, and
knows nothing the file interface does not tell it -- so it can be opened,
closed and reopened while the recorder runs, and two people can watch the same
rig at once.  Sending a setpoint from here writes exactly the file MATLAB
writes, and is refused by exactly the same interlocks.

Two plots, not one, and they are stacked and x-linked rather than overlaid.  A
heater percent and a temperature share no axis: 63% and 63 K are different
quantities, and drawing them against one scale invites reading a trend across
the two.  Setpoints go on the kelvin axis, beside the channel they are chasing.

Dragging across either panel picks the time window, because the question a
strip chart gets asked is "what happened between *there* and *there*", and the
preset windows in the combo can only answer it when the interesting part
happens to end now.  A hand-picked window stops following the recorder -- new
samples land off the right-hand edge, which is what a fixed window means -- so
the state is announced on the button beside the combo and is left by a
double-click, that button, or picking a preset.
"""

from __future__ import annotations

import logging
import os

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from ..ipc.commands import CommandSpool
from .source import CsvTail, StatusSource, classify_column

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


class TimeSpanViewBox(pg.ViewBox):
    """A view box whose left-drag picks a time span instead of panning.

    Horizontal by construction: the y extent of the drag is ignored and the
    band spans the full height of the panel.  Dragging out a rectangle would
    let someone crop the temperature axis by accident while reaching for a
    time window, and on a strip chart the window *is* the time axis.

    Panning is still on the mouse, under ``Shift`` -- not ``Ctrl``, which macOS
    turns into a right-click before Qt ever sees it -- and so is the wheel
    zoom, the middle-drag pan and the right-click menu.  Nothing pyqtgraph
    offered before is taken away; the left drag is the only gesture reassigned.
    """

    #: (t0, t1) in epoch seconds, always ordered, emitted once on release.
    sigTimeSpanSelected = QtCore.Signal(float, float)
    #: A double-click anywhere in the panel: go back to following the recorder.
    sigViewReset = QtCore.Signal()

    #: A drag shorter than this is a click that wobbled, not a selection.  In
    #: pixels, because that is the unit of the wobble.
    MIN_DRAG_PX = 6.0

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
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
        x0 = self.mapToView(down).x()
        x1 = self.mapToView(here).x()
        if not ev.isFinish():
            self._show_band(x0, x1)
            return
        self._band.hide()
        if abs(here.x() - down.x()) < self.MIN_DRAG_PX:
            return
        self.sigTimeSpanSelected.emit(min(x0, x1), max(x0, x1))

    def mouseClickEvent(self, ev) -> None:  # noqa: N802 - Qt/pyqtgraph name
        if ev.double():
            ev.accept()
            self.sigViewReset.emit()
            return
        super().mouseClickEvent(ev)   # the right-click menu still belongs here

    def _show_band(self, x0: float, x1: float) -> None:
        y0, y1 = self.viewRange()[1]
        self._band.setRect(QtCore.QRectF(min(x0, x1), y0, abs(x1 - x0), y1 - y0))
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

        self.setCentralWidget(central)
        self.statusBar().showMessage("waiting for the recorder…")

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
        self.command_group = QtWidgets.QGroupBox("Setpoint")
        form = QtWidgets.QFormLayout(self.command_group)

        self.instrument_combo = QtWidgets.QComboBox()
        form.addRow("Instrument", self.instrument_combo)

        self.loop_spin = QtWidgets.QSpinBox()
        self.loop_spin.setRange(1, 4)
        form.addRow("Loop", self.loop_spin)

        self.setpoint_spin = QtWidgets.QDoubleSpinBox()
        self.setpoint_spin.setRange(0.0, 1000.0)
        self.setpoint_spin.setDecimals(3)
        self.setpoint_spin.setSuffix(" K")
        self.setpoint_spin.setValue(0.0)
        form.addRow("Setpoint", self.setpoint_spin)

        self.send_button = QtWidgets.QPushButton("Send…")
        self.send_button.clicked.connect(self._send_setpoint)
        form.addRow(self.send_button)

        self.ack_label = QtWidgets.QLabel("")
        self.ack_label.setWordWrap(True)
        form.addRow(self.ack_label)
        return self.command_group

    def _plots(self) -> QtWidgets.QWidget:
        layout = pg.GraphicsLayoutWidget()

        self.k_plot = layout.addPlot(row=0, col=0, viewBox=TimeSpanViewBox())
        self.k_plot.setLabel("left", "Temperature", units="K")
        self.k_plot.showGrid(x=True, y=True, alpha=0.25)
        self.k_plot.addLegend(offset=(-10, 10))
        self.k_plot.setAxisItems({"bottom": pg.DateAxisItem()})

        self.pct_plot = layout.addPlot(row=1, col=0, viewBox=TimeSpanViewBox())
        self.pct_plot.setLabel("left", "Output", units="%")
        self.pct_plot.showGrid(x=True, y=True, alpha=0.25)
        self.pct_plot.setAxisItems({"bottom": pg.DateAxisItem()})
        # One pan or zoom moves both: comparing a heater step against the
        # temperature it caused is the whole reason there are two panels.
        self.pct_plot.setXLink(self.k_plot)

        # Either panel may be dragged; both mean the same time window, and the
        # link carries it to the other one.
        for plot in (self.k_plot, self.pct_plot):
            plot.getViewBox().sigTimeSpanSelected.connect(self._select_span)
            plot.getViewBox().sigViewReset.connect(self._follow_live)
        # Everything that can move the time axis -- the drag, the wheel, a
        # Shift-drag, the linked panel -- arrives here, so there is one place
        # that decides what the window is and what data belongs in it.
        self.k_plot.sigXRangeChanged.connect(self._x_range_changed)

        layout.ci.layout.setRowStretchFactor(0, 3)
        layout.ci.layout.setRowStretchFactor(1, 1)
        # The gesture has to be discoverable by someone who will not read the
        # documentation, which is everyone standing at a cryostat at 2 a.m.
        layout.setToolTip(
            "Drag across either panel to pick a time window.\n"
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
        names = [str(link.get("name", "")) for link in self.source.links()
                 if link.get("writable")]
        if [self.instrument_combo.itemText(i)
                for i in range(self.instrument_combo.count())] != names:
            self.instrument_combo.clear()
            self.instrument_combo.addItems(names)

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
                self.send_button.setEnabled(True)
            elif QtCore.QDateTime.currentSecsSinceEpoch() > deadline:
                self.ack_label.setText(
                    "no acknowledgement — the recorder may not be reading commands")
                self.ack_label.setStyleSheet("color:#e65100;")
                self._pending = None
                self.send_button.setEnabled(True)

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
                # Exactly the visible span, so the kelvin axis autoscales to
                # what is on screen: zoom into a five-minute wobble and the
                # wobble fills the panel instead of a day's excursion.
                t, v = self.tail.between(name, *self._span)
            curve.setData(t, v)

    # -- choosing the window with the mouse --------------------------------

    def _select_span(self, t0: float, t1: float) -> None:
        """A drag finished: make that span the window."""
        self._span = (t0, t1)
        # Both panels, because the y autoscale of each is its own; the x range
        # travels over the link.
        for plot in (self.k_plot, self.pct_plot):
            plot.enableAutoRange(x=False, y=True)
        self.k_plot.setXRange(t0, t1, padding=0)
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

    def _follow_live(self, *_ignored) -> None:
        """Drop the hand-picked window and follow the recorder again.

        Takes and ignores whatever the sender passes -- a combo index, a
        button's checked flag -- because three different widgets mean it.
        """
        if self._span is None:
            self._redraw()          # a combo change with nothing to leave
            return
        self._span = None
        for plot in (self.k_plot, self.pct_plot):
            plot.enableAutoRange(x=True, y=True)
        self._span_changed()

    def _span_changed(self) -> None:
        self.live_button.setEnabled(self._span is not None)
        self._redraw()
        self._update_statusbar()

    # -- commanding --------------------------------------------------------

    def _send_setpoint(self) -> None:
        """Queue a setpoint, after saying out loud what is about to happen."""
        if self.spool is None:
            return
        instrument = self.instrument_combo.currentText()
        loop = self.loop_spin.value()
        kelvin = self.setpoint_spin.value()
        answer = QtWidgets.QMessageBox.question(
            self, "Send setpoint",
            f"Set loop {loop} of {instrument} to {kelvin:.3f} K?\n\n"
            "This changes where the instrument's own PID loop is going. It "
            "does not turn a heater on: a setpoint does nothing while the "
            "heater range is 0.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            cid = self.spool.submit(
                "setpoint", instrument=instrument, source="lschart-gui",
                loop=loop, kelvin=kelvin,
            )
        except OSError as exc:
            self.ack_label.setText(f"could not queue the command: {exc}")
            self.ack_label.setStyleSheet("color:#b71c1c;")
            return
        self.ack_label.setText(f"queued {cid}, waiting for the recorder…")
        self.ack_label.setStyleSheet("color:#37474f;")
        self.send_button.setEnabled(False)
        # The recorder refuses anything older than its TTL, so waiting longer
        # than that could only ever report a refusal it has already decided.
        self._pending = (
            cid,
            QtCore.QDateTime.currentSecsSinceEpoch() + int(self.spool.ttl_s),
        )
