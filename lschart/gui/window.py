"""The strip-chart window.  Qt lives here and nowhere else in the package.

A viewer that can also command, and the distinction matters: it holds no
instrument link and takes no lock, so it can be opened, closed and reopened
while the recorder runs, and two people can watch the same cryostat at once.  Every
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
*there*" -- and because a 2 mK wobble on a 300 K axis is invisible until the
value axis is cropped to it too.  The drag is always the whole rectangle;
`X+ X- Y+ Y-` are how one axis gets moved on its own, in steps.  A hand-picked
view stops following the recorder -- new samples land off the right-hand edge,
which is what a fixed window means -- so the state is announced in the status
bar (no view button checked) and is left by a double-click or any button in
the `View` row.  That row holds live-referenced windows and nothing else --
the last 6/12/24/48 hours, riding forward with the recorder, opening on 24 h;
a fresh viewer backfills only about the widest of them, and older spans are
re-read from disk when a drag reaches them.  While a span is picked the chart first
draws its thinned overview and then, one quiet tick later, swaps in what
`CsvTail.prepare_span` re-read from the logs at full resolution -- zooming
back into an old day shows real samples again, not whatever survived
decimation.

Where the log has a hole -- the recorder was off, the machine rebooted -- the
trace breaks rather than ruling a straight line across it; `connect_flags` in
`source` decides where, and it is the only thing in the drawing that looks at
the *spacing* of the samples rather than their values.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from typing import NamedTuple

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from ..instruments.ls33x import HEATER_RANGE_NAMES
from ..ipc.commands import CommandSpool
from . import theme
from .source import (
    COMFORT_STOP_K, COMFORT_STOP_PCT, GAP_FACTOR, CsvTail, StatusSource, capabilities,
    SATURATED_HIGH_PCT, SATURATED_LOW_PCT, classify_column, connect_flags, control_row,
    loop_marks, loop_rows, nearest_series, region_stats, write_region_csv,
)

log = logging.getLogger(__name__)

#: Distinguishable at a glance, and distinguishable from each other when
#: printed in greyscale, which is what happens to a plot that gets into a
#: lab notebook.
CURVE_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
    "#8c564b", "#17becf", "#bcbd22", "#e377c2", "#7f7f7f",
]

#: (label, seconds) for the live-referenced view buttons: the last N hours,
#: riding forward with the recorder.  The widest of these is also how much
#: history a fresh viewer backfills from the finished logs.
#:
#: There is no "everything" button.  A window that means "whatever this viewer
#: happens to hold" is not a window: it is a different span on a viewer opened
#: an hour ago and one left up since Tuesday, and it grows silently under
#: whoever is reading it.  Scrolling back to find an older run is acceptable;
#: a view whose extent is an accident of process uptime is not.
VIEW_WINDOWS = [
    ("6 h", 6 * 3600.0), ("12 h", 12 * 3600.0),
    ("24 h", 24 * 3600.0), ("48 h", 48 * 3600.0),
]

#: What a viewer opens on, and what a double-click returns to when no window
#: has been chosen yet.  A day: long enough to hold last night's cooldown,
#: short enough that a two-hour excursion is still a shape rather than a spike.
DEFAULT_VIEW_WINDOW_S = 24 * 3600.0

#: How far back a fresh start reads by default: the widest view button, plus
#: an hour so the edge of a full window has samples to bracket it.
BACKFILL_COVERAGE_S = VIEW_WINDOWS[-1][1] + 3600.0

#: How this viewer labels itself in every command it writes.  A recorder's
#: `ipc.sources` policy is keyed on exactly this string, so it is a constant
#: and not a literal repeated at each call site.
GUI_SOURCE = "lschart-gui"

BANNER_STATES = (
    # Kept only as the list of states.  The colours moved to `gui.theme`,
    # which resolves them per theme at call time -- a module-level string
    # cannot know whether the desktop is dark, and freezes whatever it was at
    # import if it tries.
    "ok", "stale", "stopped", "absent",
)


#: What one press of a zoom button does to the visible range.  A factor and
#: not a number of seconds or kelvin, because the axis it lands on may be a
#: minute wide or a day.  1.5 is a step you can hold down without overshooting
#: and still get somewhere in three presses.
ZOOM_STEP = 1.5


#: The loop table's columns.  ``Rail`` and ``Off SP`` are deliberately two
#: columns and not one: OR-ing them into a single warning gives an icon that is
#: lit through every cooldown, and an icon that is always lit is an icon nobody
#: reads.  They also mean different things -- a loop pinned at its rail has run
#: out of authority, a loop far from its setpoint may simply be on the way.
#: Headings are terse because the panel is narrow and a loop table that
#: scrolls sideways hides the very marks it exists to show.
#:
#: ``State`` carries what the loop is *doing*, which used to be reachable only
#: by hovering.  It decides whether either mark applies at all, so a loop that
#: has quietly stopped trying -- switched to open loop, or a software loop
#: locked out after a fault -- was previously invisible without a mouse.
LOOP_COLUMNS = ["#", "Sensor", "K", "SP", "Out", "Rng", "State", "Rail", "Off SP"]

#: Column indices, by name.  Derived rather than written down: the marks moved
#: one to the right when ``State`` was added, and two hardcoded 6s and 7s are
#: exactly the kind of thing that moves silently.
COL_LOOP, COL_SENSOR, COL_KELVIN = 0, 1, 2
COL_SETPOINT, COL_OUTPUT, COL_RANGE = 3, 4, 5
COL_STATE = LOOP_COLUMNS.index("State")
COL_SATURATED = LOOP_COLUMNS.index("Rail")
COL_UNSETTLED = LOOP_COLUMNS.index("Off SP")

#: What each mark says when it is lit.  Words rather than glyphs: this is read
#: at 2 a.m. by somebody who has not seen the legend.
MARK_SATURATED = "RAIL"
MARK_UNSETTLED = "OFF SP"

#: Red, for a lit mark and for a software loop whose health is not ``ok``.
#: Resolved through `gui.theme` at paint time: the old constant was invisible
#: on a dark desktop, and so was the black it was paired with.
def warn_colour(widget=None) -> str:
    return theme.colour("bad", widget)


def _state_text(mode) -> str:
    """The ``State`` cell: one word where one word will do.

    ``OUTMODE`` says "closed loop" and "open loop", where the second word is
    the same on both and carries nothing: the panel is narrow and a loop table
    that scrolls sideways hides the very marks it exists to show.  Nothing else
    is shortened -- the supervisor's "ramping down" is a fault backing the
    heater off, and clipping it to "ramping" would make it read as an ordinary
    setpoint traversal.  The full string stays in the tooltip either way.
    """
    text = str(mode or "").replace("_", " ").strip()
    if text.endswith(" loop"):
        text = text[:-len(" loop")]
    return text or "—"


#: The two region cursors, and the shading between them.  Deliberately not one
#: of CURVE_COLORS: a cursor that is the same colour as a trace reads as part
#: of the data.
CURSOR_COLOR = "#37474f"

#: How near the pointer has to be to a trace, in pixels, for the hover readout
#: to name it.  In pixels because that is the unit of "the pointer is on it";
#: a tolerance in kelvin would be three screen-inches wide on the percent
#: panel and invisible on a temperature axis cropped to a 2 mK wobble.
HOVER_TOLERANCE_PX = 14.0

#: The floor on how often a cursor region that reaches the live edge is
#: re-measured from *disk*.  Only bites once decimation has started, which is
#: the only case where the statistics cannot be answered from memory --
#: `CsvTail.samples_in` re-reads every log in the directory then, and the live
#: edge grows once a cycle.  A region in the past is measured once and not
#: again: nothing can change what happened between two past instants.
STATS_RELOAD_S = 5.0

#: How long a command's readback guard may hold a field before it gives up.
#: The guard bridges the gap between an acknowledgement and the readback that
#: reflects it, which is a few cycles -- longer when the recorder reads
#: status only every Nth one.  It is a backstop, not a schedule: what it
#: bounds is how long a field may keep showing what was *asked for* when the
#: readback never comes back close enough to agree.  A field that is wrong
#: forever is worse than one that is wrong for half a minute, and on these
#: instruments the number in the box is a heater setting.
READBACK_GRACE_S = 30.0


class _Awaiting(NamedTuple):
    """The one readback a queued command is still owed.

    ``tolerance`` is the precision the widget itself displays.  Comparing any
    tighter than that asks a question the operator cannot see the answer to:
    the drivers confirm their own writes at 1e-3 (33x) and 0.02 % (218), so a
    readback they consider a match can differ in a digit far below what is on
    screen, and an exact comparison would wait for ever on an agreement that
    has already happened.

    ``previous`` is what the readback said *before* the command, and it is
    what the guard is really about.  The hazard is one specific wrong
    picture: the seconds between an acknowledgement and the readback that
    reflects it, where the aux block still holds the old value and a fill
    would snap the field back to it -- showing 0 % just after someone asked
    for 43 %.  So the guard lifts the moment the readback moves off the old
    value, whether or not it landed where it was asked to.  A box that
    rounded, clamped, or did something else entirely should be shown doing
    it, promptly; only agreement with a value nobody can see the difference
    from is worth waiting on.
    """

    aux: str
    expected: float
    previous: float | None
    tolerance: float
    deadline: float


@contextlib.contextmanager
def _quiet(widget):
    """Set a widget's value without its ``valueChanged`` calling back in.

    A fill is not an edit: it must not set the dirty flag that stops the
    field tracking the cryostat.  The ``finally`` matters -- a widget left
    with its signals blocked is a control that has stopped working, and
    nothing about the failure would say so.
    """
    widget.blockSignals(True)
    try:
        yield
    finally:
        widget.blockSignals(False)


def _scaled(rng, factor: float) -> tuple[float, float]:
    """`rng` narrowed by `factor` -- widened, for a factor below one -- about
    its own middle, which is the part the eye is already on."""
    middle = (rng[0] + rng[1]) / 2
    half = (rng[1] - rng[0]) / 2 / factor
    return middle - half, middle + half


def _duration(seconds: float) -> str:
    """A span in whatever unit keeps it to a couple of digits."""
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


class ZoomViewBox(pg.ViewBox):
    """A view box whose left-drag picks a zoom rectangle instead of panning.

    The rectangle is taken literally, always: drag one out and both axes
    become exactly its edges, the value axis included.  There is no
    one-axis form of the gesture -- the drag means the box that was drawn,
    and the zoom buttons beside the window combo are what move one axis on
    its own.

    A drag has to clear ``MIN_DRAG_PX`` in *both* directions to be a
    rectangle at all.  Below that it is a click that wobbled, or a stripe so
    thin the axis it selected would be degenerate, and either way nothing
    happens and no band is drawn to suggest otherwise.

    Panning is still on the mouse, under ``Shift`` -- not ``Ctrl``, which macOS
    turns into a right-click before Qt ever sees it -- and so is the wheel
    zoom, the middle-drag pan and the right-click menu.  Nothing pyqtgraph
    offered before is taken away; the left drag is the only gesture reassigned.
    """

    #: ``((t0, t1), (y0, y1))``, both ordered, in data coordinates.  Emitted
    #: once, on release, and only for a drag that was a rectangle.
    sigRegionSelected = QtCore.Signal(object, object)
    #: A double-click anywhere in the panel: go back to following the recorder.
    sigViewReset = QtCore.Signal()
    #: A time picked with the left button while ``cursor_mode`` is on, in
    #: data coordinates.  Emitted on a click and continuously through a drag,
    #: so a cursor can be dropped or dragged into place with one gesture.
    sigPointPicked = QtCore.Signal(float)

    #: A drag shorter than this, either way, is not a rectangle.  In pixels,
    #: because that is the unit of the wobble it is there to reject.
    MIN_DRAG_PX = 6.0

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: While True the left button places a region cursor instead of
        #: drawing a zoom rectangle.  Two gestures cannot share one button,
        #: and the cursors are the ones that need a *click* -- so the drag is
        #: what gives way, and only while the cursors are on screen.  The
        #: wheel, Shift-drag and the X/Y buttons still zoom throughout, so no
        #: view is unreachable with the cursors up.
        self.cursor_mode = False
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
        if self.cursor_mode:
            # No threshold and no band: a cursor follows the pointer from the
            # first pixel, because dragging one is how it gets put somewhere
            # exact and a dead zone at the start would fight that.
            self.sigPointPicked.emit(float(self.mapToView(ev.pos()).x()))
            return
        down, here = ev.buttonDownPos(), ev.pos()
        # The threshold is judged in pixels, on the way in; what comes back out
        # is in data coordinates, where one x unit is a second.
        if (abs(here.x() - down.x()) < self.MIN_DRAG_PX
                or abs(here.y() - down.y()) < self.MIN_DRAG_PX):
            self._band.hide()
            return
        p0, p1 = self.mapToView(down), self.mapToView(here)
        x = (min(p0.x(), p1.x()), max(p0.x(), p1.x()))
        y = (min(p0.y(), p1.y()), max(p0.y(), p1.y()))
        if not ev.isFinish():
            self._show_band(x, y)
            return
        self._band.hide()
        self.sigRegionSelected.emit(x, y)

    def mouseClickEvent(self, ev) -> None:  # noqa: N802 - Qt/pyqtgraph name
        if ev.double():
            ev.accept()
            self.sigViewReset.emit()
            return
        if (self.cursor_mode
                and ev.button() == QtCore.Qt.MouseButton.LeftButton):
            ev.accept()
            self.sigPointPicked.emit(float(self.mapToView(ev.pos()).x()))
            return
        super().mouseClickEvent(ev)   # the right-click menu still belongs here

    def _show_band(self, x, y) -> None:
        """Preview the rectangle the release would zoom to, exactly."""
        self._band.setRect(QtCore.QRectF(x[0], y[0], x[1] - x[0], y[1] - y[0]))
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
        gap_factor: float = GAP_FACTOR,
        max_kelvin: float = COMFORT_STOP_K[1],
        max_percent: float = COMFORT_STOP_PCT[1],
        config_label: str = "",
    ) -> None:
        super().__init__()
        self.source = StatusSource(status_path)
        self.tail = CsvTail(max_points=max_points,
                            backfill_s=BACKFILL_COVERAGE_S)
        self.spool = spool
        #: How many sample intervals a step has to exceed before the trace is
        #: drawn with a gap there instead of a line.  See `connect_flags`.
        self.gap_factor = gap_factor
        #: Where each panel's value axis stops when the data does not ask for
        #: more, keyed by unit.  Widened to whatever is drawn, never narrowed
        #: to it -- see :meth:`_apply_comfort_stops`.
        self._comfort = {
            "K": (COMFORT_STOP_K[0], float(max_kelvin)),
            "%": (COMFORT_STOP_PCT[0], float(max_percent)),
        }
        self.config_label = config_label

        self.curves: dict[str, pg.PlotDataItem] = {}
        #: Which panel each curve is on, by unit.  Decided once, when the
        #: column is adopted, because `classify_column` is the only thing
        #: entitled to an opinion about it and it should not be asked twice.
        self.curve_units: dict[str, str] = {}
        self.toggles: dict[str, QtWidgets.QCheckBox] = {}
        self._pending: tuple[str, float] | None = None   # (command id, deadline)
        #: ``(aux name, value)`` of the readback that would confirm the last
        #: queued command, while that readback has not caught up yet.  One at
        #: a time, because one unacknowledged command locks every button.
        self._awaiting: _Awaiting | None = None
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
        #: The span whose full-resolution samples have been loaded (and the one
        #: seen on the previous tick, for debouncing).  Until they agree with
        #: ``_span`` the chart draws the thinned overview for that span; once a
        #: span survives one tick unchanged it is worth a disk read to draw
        #: properly.  A wheel gesture crosses dozens of spans a second and
        #: none of them should each cost a file scan.
        self._loaded_span: tuple[float, float] | None = None
        self._armed_span: tuple[float, float] | None = None
        #: The live-referenced window currently shown, in seconds.  Always a
        #: number: every live view is the last N hours of something, and there
        #: is no "everything held" state for it to be absent for.  While the
        #: view is following, each redraw shows the newest sample minus this
        #: many seconds, so the window rides forward with the recorder.  A
        #: drag supersedes it -- ``_span`` then decides -- and it is kept
        #: through that, because it is what a double-click comes back to.
        self._follow_span_s: float = DEFAULT_VIEW_WINDOW_S
        #: The two region cursors, as times in epoch seconds, or None while
        #: no region is picked.  Unordered as stored -- the operator may drag
        #: one past the other -- and sorted wherever a span is wanted.
        self._cursors: tuple[float, float] | None = None
        #: What the last statistics pass was computed from: the region, and
        #: how many rows the tail held when it ran.  Recomputing a region that
        #: cannot have changed would be a scan of every log in the directory
        #: once a second.  See `_update_region_stats`.
        self._stats_key: tuple | None = None
        self._stats_read_at = 0.0
        #: The statistics themselves, by panel unit then column name, so the
        #: export writes exactly the region the panel is describing.
        self._stats: dict[str, dict] = {}
        #: The newest value drawn for each trace, which is what the legend
        #: shows while no region is picked.
        self._live_values: dict[str, float] = {}
        #: The loop every command in the panel is about, chosen by clicking a
        #: row of the loop table.  There is no second selector.
        self._loop: int = 1

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
        self.banner.setStyleSheet(theme.banner_style("absent", self))
        outer.addWidget(self.banner)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(self._left_panel())
        splitter.addWidget(self._plots())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # 500, not 430: the loop table's eight fixed columns want 352 px
        # between them, so at 430 the sensor name was squeezed to 52 px and
        # both "Stage 1" and "Stage 2" elided to the same "Stag…". The extra
        # 70 px comes out of a 900 px chart, which does not miss it.
        splitter.setSizes([500, 900])
        outer.addWidget(splitter, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("waiting for the recorder…")
        # Settle the control panel before the first poll.  Otherwise a viewer
        # opened against a recorder with nothing writable shows every control,
        # greyed out -- which reads as "this cryostat has all of these" rather than
        # "this cryostat has none of them".
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
        # Sized to its rows in _update_readouts, not given a stretch: a cryostat
        # with four channels should not reserve half the panel for the six it
        # does not have, while the trace list underneath goes unscrollable.
        box.addWidget(self.readouts, 0)

        # The loop table, *beneath* the per-channel readouts and not instead of
        # them.  Recording every thermometer continuously is the recorder's
        # job, and a loop-centric view that replaced the channel list would
        # turn an eight-input monitor into however many loops it has.
        self.loops = QtWidgets.QTableWidget(0, len(LOOP_COLUMNS))
        self.loops.setHorizontalHeaderLabels(LOOP_COLUMNS)
        self.loops.verticalHeader().setVisible(False)
        self.loops.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.loops.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.loops.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.loops.horizontalHeader().setStretchLastSection(False)
        header = self.loops.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        # Every column sized to its contents except the sensor name, which
        # takes what is left and elides when there is not enough.
        #
        # Something has to give: nine columns of contents want 427 px in a
        # 404 px panel, and the alternative is the sideways scroll this table
        # must not do -- it would hide the two marks the table exists to show,
        # which is exactly backwards.  The sensor name is the right thing to
        # cut because it is the one column repeated elsewhere: it is in the
        # readouts table directly above and in this row's own tooltip. A
        # truncated "Rad S…" is still identifiable; a mark scrolled off the
        # right-hand edge is not there at all.
        header.setSectionResizeMode(COL_SENSOR, QtWidgets.QHeaderView.Stretch)
        self.loops.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.loops.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.loops.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                 QtWidgets.QSizePolicy.Fixed)
        # Never a vertical scrollbar: the table is sized to its rows below,
        # and one that scrolled would hide a loop behind a scrollbar in a
        # panel that has the room for it.
        self.loops.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.loops.itemSelectionChanged.connect(self._loop_row_selected)
        self.loops.setToolTip(
            "One row per control loop, as the instrument reports it "
            "(OUTMODE?). Click a row to point the command panel at that "
            "loop. A software loop, where there is one, is the last row and "
            "is read rather than clicked — it takes Arm and the panic Hold, "
            "not a setpoint, a range or gains.")
        #: Row index -> (instrument name, loop row), so a click can say which
        #: loop was picked without parsing the cells back out again.
        self._loop_index: list[tuple[str, dict]] = []
        box.addWidget(self.loops, 0)

        view_row = QtWidgets.QHBoxLayout()
        view_row.addWidget(QtWidgets.QLabel("View"))
        # Live-referenced windows: the last N hours, riding forward with the
        # recorder.  Every one of them is a fixed extent ending at the newest
        # sample, which is what makes two viewers side by side comparable.
        self.span_buttons: dict[float, QtWidgets.QPushButton] = {}
        for label, seconds in VIEW_WINDOWS:
            button = QtWidgets.QPushButton(label)
            button.setCheckable(True)
            button.setChecked(seconds == self._follow_span_s)
            button.setToolTip(f"the last {label}, following the recorder")
            button.clicked.connect(
                lambda _checked=False, s=seconds: self._follow_window(s))
            view_row.addWidget(button, 0)
            self.span_buttons[seconds] = button
        view_row.addStretch(1)
        box.addLayout(view_row)

        # One axis at a time, in steps, about the middle of what is shown.
        # The drag is always the whole rectangle; these are how a single axis
        # gets moved without redrawing a box to do it.
        zoom_row = QtWidgets.QHBoxLayout()
        zoom_row.addWidget(QtWidgets.QLabel("Zoom"))
        self.zoom_buttons: dict[str, QtWidgets.QPushButton] = {}
        for label, tip, zoom, factor in (
            ("X+", "zoom in on time", self._zoom_x, ZOOM_STEP),
            ("X−", "zoom out on time", self._zoom_x, 1 / ZOOM_STEP),
            ("Y+", "zoom in on the value axis of both panels",
             self._zoom_y, ZOOM_STEP),
            ("Y−", "zoom out on the value axis of both panels",
             self._zoom_y, 1 / ZOOM_STEP),
        ):
            button = QtWidgets.QPushButton(label)
            button.setToolTip(f"{tip}, about the middle of what is shown")
            button.setMaximumWidth(40)
            button.clicked.connect(
                lambda _checked=False, z=zoom, f=factor: z(f))
            zoom_row.addWidget(button, 0)
            self.zoom_buttons[label] = button
        zoom_row.addStretch(1)
        box.addLayout(zoom_row)

        # Two cursors and what is between them.  A separate row from the view
        # and zoom rows because it answers a different question: those choose
        # what is on screen, this measures a piece of it.
        cursor_row = QtWidgets.QHBoxLayout()
        self.cursor_button = QtWidgets.QPushButton("Cursors")
        self.cursor_button.setCheckable(True)
        self.cursor_button.setToolTip(
            "Two vertical cursors, and the mean, spread and change of every "
            "trace between them.\n"
            "Left-click or drag on a panel moves the nearer one. While they "
            "are up the left button places cursors instead of drawing a zoom "
            "rectangle; the wheel, Shift-drag and the X/Y buttons still zoom.")
        self.cursor_button.clicked.connect(self._toggle_cursors)
        cursor_row.addWidget(self.cursor_button, 0)

        self.export_button = QtWidgets.QPushButton("Export region…")
        self.export_button.setEnabled(False)
        self.export_button.setToolTip(
            "Write the samples between the cursors to a CSV, at full "
            "resolution — not the thinned overview the chart draws.")
        self.export_button.clicked.connect(self._export_region)
        cursor_row.addWidget(self.export_button, 0)
        cursor_row.addStretch(1)
        box.addLayout(cursor_row)

        self.export_note = QtWidgets.QLabel("")
        self.export_note.setWordWrap(True)
        self.export_note.setStyleSheet(theme.note_style("muted", self))
        box.addWidget(self.export_note)

        traces = QtWidgets.QGroupBox("Traces")
        self.traces_layout = QtWidgets.QVBoxLayout(traces)
        self.traces_layout.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(traces)
        scroll.setMinimumHeight(160)
        # The one thing in this panel that should absorb spare height: a cryostat
        # with two instruments has a dozen traces, and hunting for one of them
        # through a three-line window is the difference between a usable
        # viewer and a tolerated one.
        box.addWidget(scroll, 1)

        box.addWidget(self._command_box())
        # OUTSIDE the command group, and that is structural rather than
        # cosmetic. The panic kinds are exempt from the source policy at the
        # recorder, so when that policy switches the panel off these must stay
        # live -- and a Qt child of a disabled parent is disabled however
        # firmly you enable it.
        box.addWidget(self._panic_box())
        box.addWidget(self._source_box())
        self.links_label = QtWidgets.QLabel("")
        self.links_label.setWordWrap(True)
        box.addWidget(self.links_label)
        return panel

    def _command_box(self) -> QtWidgets.QWidget:
        """The control panel: one instrument selector, then whatever it can do.

        Three controls rather than one, because the cryostats this drives are not
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

        # What the selected loop is bound to, in a sentence.  From the
        # recorder's OUTMODE reading, so it is the instrument's answer and not
        # a map kept in here that could go stale.
        self.loop_note = QtWidgets.QLabel("")
        self.loop_note.setWordWrap(True)
        self.loop_note.setStyleSheet(theme.note_style("muted", self))
        box.addWidget(self.loop_note)

        box.addWidget(self._setpoint_group())
        box.addWidget(self._pid_group())
        box.addWidget(self._range_group())
        box.addWidget(self._analog_group())

        # The way back from a hold, and deliberately *outside* the panic menu:
        # arming starts the loop driving the heater again, which is the
        # power-applying direction. Putting it beside the two stopping actions
        # would suggest it shares their exemptions. It shares none of them.
        self.arm_button = QtWidgets.QPushButton("Arm software loop…")
        self.arm_button.setToolTip(
            "Close the software loop at the temperature the cryostat is at "
            "now — the way back from a hold. This APPLIES POWER and is gated "
            "like any other write.")
        self.arm_button.clicked.connect(self._send_arm)
        box.addWidget(self.arm_button)

        self.ack_label = QtWidgets.QLabel("")
        self.ack_label.setWordWrap(True)
        box.addWidget(self.ack_label)
        return self.command_group

    def _panic_box(self) -> QtWidgets.QWidget:
        """The two ways to stop, behind a menu.

        **Three clicks by design**: open the menu, choose the action, confirm
        it. These are needed almost never and must not be reachable by
        accident, and the middle click is what a mis-aimed one lands on.

        Its own widget rather than a button in the command group, because it
        must survive that group being switched off: the panic kinds are exempt
        from the per-client source policy at the recorder, and a Qt child of a
        disabled parent is disabled no matter how firmly it is enabled. A panel
        that greyed out a button the recorder would in fact obey would be
        lying at the moment that matters most.

        The tooltip says what the bypass covers and what it does not, rather
        than "bypasses interlocks" — which would be a promise it does not keep.
        """
        self.panic_button = QtWidgets.QToolButton()
        self.panic_button.setText("Panic ▾")
        self.panic_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.panic_button.setToolTip(
            "Two ways to stop. Both bypass the per-client source policy and "
            "the two power gates. Neither bypasses a read-only instrument, "
            "which is left alone and named in the reply.")
        self.panic_button.setStyleSheet("font-weight:bold; padding:4px;")
        menu = QtWidgets.QMenu(self.panic_button)
        self.off_action = menu.addAction("All heaters OFF…")
        self.off_action.triggered.connect(self._send_heaters_off)
        self.hold_action = menu.addAction("All temperatures HOLD…")
        self.hold_action.triggered.connect(self._send_hold)
        self.panic_button.setMenu(menu)
        return self.panic_button

    def _source_box(self) -> QtWidgets.QWidget:
        """Whether the recorder is listening to *this viewer*, and a way back.

        Outside the command group for the same structural reason the panic menu
        is: this is the control that undoes the thing that disables that group,
        so it cannot live inside it. The `source` command is exempt from the
        policy it edits precisely so this button works when nothing else here
        does.

        Muting stops the recorder listening. It does not stop this viewer
        **reading** -- the chart, the readouts, the loop table and the marks are
        all file reads and carry on exactly as before. That is worth saying on
        the widget, because "disabled" on a panel full of greyed-out controls
        looks a lot like "broken".
        """
        self.source_check = QtWidgets.QCheckBox("Accept commands from this viewer")
        self.source_check.setChecked(True)
        self.source_check.toggled.connect(self._source_toggled)
        return self.source_check

    def _source_toggled(self, checked: bool) -> None:
        """Queue the mute or the un-mute.  Only ever a human's click: the
        periodic fill in :meth:`_sync_source_box` goes through ``_quiet``."""
        if self.spool is None:
            return
        if not checked and not self._confirm(
            "Ignore this viewer",
            "Tell the recorder to ignore commands from this viewer?\n\n"
            "The chart, the readouts and the loop table carry on exactly as "
            "they are — this is only about commands, and reading is not a "
            "command.\n\n"
            "You can undo it from this same box: the command that sets this is "
            "exempt from the policy it sets, so muting is not a one-way door. "
            "The Panic menu also keeps working throughout.",
        ):
            with _quiet(self.source_check):
                self.source_check.setChecked(True)
            return
        self._queue("source", instrument="",
                    name=GUI_SOURCE, allowed=bool(checked))
        self._awaiting = None

    def _sync_source_box(self) -> None:
        """Reflect the recorder's answer, without the reflection sending one."""
        allowed = self.source.source_allowed(GUI_SOURCE)
        permitted = self.source.source_configured(GUI_SOURCE)
        with _quiet(self.source_check):
            self.source_check.setChecked(allowed)
        # A source the *config* refuses cannot be un-muted from here at any
        # price: the overlay may only narrow. Offering the click would be
        # offering a refusal.
        self.source_check.setEnabled(bool(self.spool) and permitted
                                     and self.source.accepts_commands())
        if not permitted:
            self.source_check.setToolTip(
                "This recorder's config (ipc.sources) refuses this viewer "
                "outright. The runtime overlay may only narrow that, so "
                "enabling it needs a config edit and a restart.")
        elif allowed:
            self.source_check.setToolTip(
                "Untick to have the recorder ignore commands from this viewer. "
                "Reading carries on either way, and you can tick it again.")
        else:
            self.source_check.setToolTip(
                "The recorder is ignoring commands from this viewer. Tick to "
                "have it listen again — no restart needed.")

    def _setpoint_group(self) -> QtWidgets.QWidget:
        self.setpoint_group = QtWidgets.QGroupBox("Setpoint")
        form = QtWidgets.QFormLayout(self.setpoint_group)

        # No loop selector here.  The loop table above *is* the selector, and
        # two ways to choose a loop is two things that can disagree about
        # which one a setpoint is going to.
        self.loop_label = QtWidgets.QLabel("—")
        self.loop_label.setStyleSheet("font-weight:600;")
        form.addRow("Loop", self.loop_label)

        self.setpoint_spin = QtWidgets.QDoubleSpinBox()
        self.setpoint_spin.setRange(0.0, 1000.0)
        self.setpoint_spin.setDecimals(3)
        self.setpoint_spin.setSuffix(" K")
        self.setpoint_spin.setValue(0.0)
        # The box tracks the cryostat's own setpoint until the operator touches it;
        # the flag is what stops a fill from fighting a number being typed.
        self._setpoint_dirty = False
        self.setpoint_spin.valueChanged.connect(self._setpoint_edited)
        form.addRow("Target", self.setpoint_spin)

        self.send_button = QtWidgets.QPushButton("Send setpoint…")
        self.send_button.clicked.connect(self._send_setpoint)
        form.addRow(self.send_button)
        return self.setpoint_group

    def _pid_group(self) -> QtWidgets.QWidget:
        """The instrument's own gains for the selected loop.

        Read, not asked for.  This viewer holds no port and cannot query an
        instrument; the numbers arrive in the status file because the recorder
        polls ``PID?`` on a slow cadence, and a recorder configured with
        ``read_pid: false`` leaves them blank and says so.  A "Get PID" button
        would have to be a command that returned data, which is a shape the
        spool, the CLI and MATLAB do not have.

        All three go out together.  ``PID`` is one command on the instrument
        and the driver verifies all three by readback; sending one would mean
        reading the other two back and re-sending them, which is a
        read-modify-write against a box somebody else may be touching.
        """
        self.pid_group = QtWidgets.QGroupBox("PID gains")
        form = QtWidgets.QFormLayout(self.pid_group)

        self.pid_spins = {}
        for key, label, decimals in (
            ("p", "P", 1), ("i", "I", 1), ("d", "D", 1),
        ):
            spin = QtWidgets.QDoubleSpinBox()
            # The instrument's own ranges: 0.1..1000 for P and I, 0..200 for D
            # on this family.  Bounded here so the widget cannot express a
            # value the box will refuse.
            spin.setRange(0.0 if key == "d" else 0.1, 1000.0 if key != "d" else 200.0)
            spin.setDecimals(decimals)
            spin.valueChanged.connect(self._pid_edited)
            self.pid_spins[key] = spin
            form.addRow(label, spin)
        # One flag for the three of them, because they are one command.
        self._pid_dirty = False

        self.pid_button = QtWidgets.QPushButton("Send PID…")
        self.pid_button.clicked.connect(self._send_pid)
        form.addRow(self.pid_button)

        self.pid_note = QtWidgets.QLabel("")
        self.pid_note.setWordWrap(True)
        self.pid_note.setStyleSheet(theme.note_style("warn", self))
        form.addRow(self.pid_note)
        return self.pid_group

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

        # No output selector either.  On this family the loop number *is* the
        # output number by protocol, so the output a range applies to is
        # decided by the row that is selected -- and a second control offering
        # to disagree with that could only ever put power somewhere nobody
        # meant it to go.
        self.heater_label = QtWidgets.QLabel("—")
        self.heater_label.setStyleSheet("font-weight:600;")
        form.addRow("Output", self.heater_label)

        self.range_combo = QtWidgets.QComboBox()
        for value, label in sorted(HEATER_RANGE_NAMES.items()):
            self.range_combo.addItem(f"{value} — {label}", value)
        self._range_dirty = False
        self.range_combo.currentIndexChanged.connect(self._range_edited)
        form.addRow("Range", self.range_combo)

        self.range_button = QtWidgets.QPushButton("Set range…")
        self.range_button.clicked.connect(self._send_range)
        form.addRow(self.range_button)

        self.range_note = QtWidgets.QLabel("")
        self.range_note.setWordWrap(True)
        self.range_note.setStyleSheet(theme.note_style("warn", self))
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
        # Same arrangement as the setpoint: the cryostat's live output until edited.
        self._analog_dirty = False
        self.analog_spin.valueChanged.connect(self._analog_edited)
        form.addRow("Output", self.analog_spin)

        self.analog_button = QtWidgets.QPushButton("Set output…")
        self.analog_button.clicked.connect(self._send_analog)
        form.addRow(self.analog_button)

        self.analog_note = QtWidgets.QLabel("")
        self.analog_note.setWordWrap(True)
        self.analog_note.setStyleSheet(theme.note_style("warn", self))
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
        # A legend on this panel too, because with no cursors set the legend
        # is where the live value is written, and a heater percent is a
        # number people read off the chart as often as a temperature.
        self.pct_plot.addLegend(offset=(-10, 10))
        self.pct_plot.setAxisItems({"bottom": pg.DateAxisItem()})
        # One pan or zoom moves both: comparing a heater step against the
        # temperature it caused is the whole reason there are two panels.
        self.pct_plot.setXLink(self.k_plot)

        #: The panels by the unit of their value axis.  A drag has to say which
        #: one it came from, because the time axis it picked is shared and the
        #: value axis it picked is not.
        self._panels = {"K": self.k_plot, "%": self.pct_plot}

        # The cursors: two per panel, x-linked by being drawn on both, plus
        # the shading between them.  Not `LinearRegionItem`, whose own drag
        # handles would be a third gesture on the left button.
        self._cursor_lines: dict[str, list] = {}
        self._cursor_shades: dict[str, object] = {}
        self._stat_labels: dict[str, pg.TextItem] = {}
        for unit, plot in self._panels.items():
            shade = pg.LinearRegionItem(
                values=(0, 0), movable=False,
                brush=pg.mkBrush(55, 71, 79, 28), pen=pg.mkPen(None))
            shade.setZValue(-1e9)        # behind the traces, not over them
            shade.hide()
            plot.addItem(shade, ignoreBounds=True)
            self._cursor_shades[unit] = shade
            lines = []
            for _ in range(2):
                line = pg.InfiniteLine(
                    angle=90, movable=False,
                    pen=pg.mkPen(CURSOR_COLOR, width=1,
                                 style=QtCore.Qt.PenStyle.DashLine))
                line.setZValue(1e8)
                line.hide()
                plot.addItem(line, ignoreBounds=True)
                lines.append(line)
            self._cursor_lines[unit] = lines

            # The statistics panel.  Parented to the view box rather than
            # placed in data coordinates, so it stays in the corner of the
            # panel through every pan and zoom instead of sliding off it.
            label = pg.TextItem(anchor=(0, 0), color="#263238",
                                fill=pg.mkBrush(255, 255, 255, 225),
                                border=pg.mkPen("#b0bec5"))
            label.setParentItem(plot.getViewBox())
            label.setPos(10, 10)
            label.setZValue(1e9)
            label.hide()
            self._stat_labels[unit] = label

        #: What the pointer is hovering, named where the pointer is.  In data
        #: coordinates on purpose: it belongs to the sample it is identifying,
        #: not to a corner of the panel.
        self._hover_labels: dict[str, pg.TextItem] = {}
        for unit, plot in self._panels.items():
            hover = pg.TextItem(anchor=(0, 1), color="#ffffff",
                                fill=pg.mkBrush(38, 50, 56, 220))
            hover.setZValue(1e9)
            hover.hide()
            plot.addItem(hover, ignoreBounds=True)
            self._hover_labels[unit] = hover

        # Either panel may be dragged; both mean the same time window, and the
        # link carries it to the other one.
        for unit, plot in self._panels.items():
            vb = plot.getViewBox()
            vb.sigRegionSelected.connect(
                lambda x, y, u=unit: self._select_region(u, x, y))
            vb.sigViewReset.connect(self._follow_live)
            vb.sigPointPicked.connect(self._place_cursor)
            # A value axis can also be moved by the wheel or a Shift-drag, and
            # then it is just as fixed as one that was dragged out; noticing
            # here is what keeps the Live button honest about it.
            plot.sigYRangeChanged.connect(
                lambda _vb, rng, u=unit: self._y_range_changed(u, rng))
        # Everything that can move the time axis -- the drag, the wheel, a
        # Shift-drag, the linked panel -- arrives here, so there is one place
        # that decides what the window is and what data belongs in it.
        self.k_plot.sigXRangeChanged.connect(self._x_range_changed)

        # Rate-limited: sigMouseMoved fires per pixel of pointer travel, and
        # every one of them would otherwise cost an interpolation across
        # every trace on the panel.
        self._hover_proxy = pg.SignalProxy(
            layout.scene().sigMouseMoved, rateLimit=30, slot=self._on_hover)

        layout.ci.layout.setRowStretchFactor(0, 3)
        layout.ci.layout.setRowStretchFactor(1, 1)
        # The gesture has to be discoverable by someone who will not read the
        # documentation, which is everyone standing at a cryostat at 2 a.m.
        layout.setToolTip(
            "Drag a rectangle on either panel to zoom to exactly it.\n"
            "The X± and Y± buttons zoom one axis at a time.\n"
            "Shift-drag pans · wheel zooms · double-click follows the "
            "recorder again.")
        return layout

    # -- the tick ----------------------------------------------------------

    def changeEvent(self, event) -> None:
        """Repaint for a desktop that changed theme under a running viewer.

        Qt sends this when the palette changes -- a macOS appearance switch, a
        Windows light/dark toggle, a Qt style swap. Everything colour-bearing
        is resolved through `gui.theme` at call time precisely so that one
        sweep is enough.
        """
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.PaletteChange:
            self._apply_theme()

    def _apply_theme(self) -> None:
        """Re-resolve every colour this window paints itself.

        The tables and the notes are rewritten on the refresh timer anyway, so
        this exists for the gap between a theme switch and the next tick --
        which at a 1 s cadence is short but is exactly when somebody is looking
        at the window they just changed.  Must never raise: a theme change is
        not a reason to lose a viewer.
        """
        try:
            self.export_note.setStyleSheet(theme.note_style("muted", self))
            self.loop_note.setStyleSheet(theme.note_style("muted", self))
            state, _ = self.source.health()
            self.banner.setStyleSheet(theme.banner_style(state, self))
            self._update_readouts()
            self._update_loops()
            self._update_gate_notes()
        except Exception:  # pragma: no cover - cosmetic, never fatal
            log.debug("could not re-apply the theme", exc_info=True)

    def refresh(self) -> None:
        """One poll of both files.  Must never raise: it is on a timer."""
        try:
            self.source.poll()
            self._update_banner()
            self._update_readouts()
            self._update_loops()
            self._update_links()
            self._update_commands()
            self._sync_command_values()
            if self.tail.follow(self.source.log_path()):
                self._first_load_done = False
            if self.tail.poll() or not self._first_load_done:
                self._first_load_done = True
                self._sync_traces()
                self._redraw()
            if self._span is not None and self._span != self._loaded_span:
                if self._span != self._armed_span:
                    # First tick on this span: wait one quiet tick before the
                    # disk work, so a gesture in motion costs nothing.
                    self._armed_span = self._span
                else:
                    # Settled: swap the thinned overview for the real samples.
                    self._armed_span = None
                    self.tail.prepare_span(*self._span)
                    self._loaded_span = self._span
                    self._redraw()
            self._update_region_stats()
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
        self.banner.setStyleSheet(theme.banner_style(state, self))

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
            if usable:
                # No colour of its own: a reading that is fine is ordinary
                # text, and ordinary text is whatever the palette says.
                theme.clear_foreground(item)
            else:
                item.setForeground(QtGui.QBrush(QtGui.QColor(warn_colour(self))))

    def _set_cell(self, row: int, col: int, text: str) -> QtWidgets.QTableWidgetItem:
        item = self.readouts.item(row, col)
        if item is None:
            item = QtWidgets.QTableWidgetItem()
            self.readouts.setItem(row, col, item)
        item.setText(text)
        return item

    def _update_loops(self) -> None:
        """Fill the loop table from what the recorder read off the instruments.

        Every link's loops, in link order, the way the readouts show every
        link's channels -- a loop table that showed only the selected box
        would hide the loop somebody needs to notice.

        The kelvin column is looked up by the loop's *sensor name*, which is
        the same string the trace and the readout carry, because the recorder
        resolved it once from ``OUTMODE?`` and the input labels.  Nothing here
        maps loops to sensors; there is no table in this file to go stale.
        """
        kelvin_by_name = {}
        for channel in self.source.channels():
            kelvin_by_name[str(channel.get("name", ""))] = (
                channel.get("kelvin"), bool(channel.get("usable")))

        entries: list[tuple[str, dict]] = []
        for link in self.source.links():
            name = str(link.get("name", ""))
            for row in loop_rows(link):
                entries.append((name, row))
        # The software loop last, after every loop that lives on a box, because
        # it is the one row that is read rather than clicked -- see
        # `_fill_loop_row`.  `""` for the instrument: it belongs to no link,
        # and the entry is here only to keep this list the same length as the
        # table it indexes.
        software = control_row(self.source.control())
        if software is not None:
            entries.append(("", software))
        self._loop_index = entries

        # Hidden entirely rather than left as an empty header: a recorder with
        # no loops (or one too old to say) should not reserve panel height for
        # a table that will never have a row in it.
        self.loops.setVisible(bool(entries))
        grew = self.loops.rowCount() != len(entries)
        if grew:
            self.loops.setRowCount(len(entries))

        selected = -1
        for index, (instrument, row) in enumerate(entries):
            kelvin, usable = kelvin_by_name.get(str(row.get("sensor") or ""),
                                                (None, False))
            self._fill_loop_row(index, instrument, row,
                                kelvin if usable else None)
            if (instrument and instrument == self.instrument_combo.currentText()
                    and int(row.get("loop") or 0) == self._loop):
                selected = index

        if grew:
            # Sized *after* the cells are filled.  Measuring an empty table
            # measures the row height of a row with nothing in it, which is
            # how the last loop came to sit behind a scrollbar; and a
            # horizontal scrollbar, if the panel is too narrow for the
            # columns, eats a row's worth of height on its own.
            self.loops.resizeRowsToContents()
            height = (self.loops.horizontalHeader().height()
                      + 2 * self.loops.frameWidth())
            for r in range(len(entries)):
                height += self.loops.rowHeight(r)
            # No allowance for a horizontal scrollbar any more: the sensor
            # column stretches and elides so the table always fits its panel,
            # and the bar is switched off outright. Kept as a comment rather
            # than deleted silently, because this used to be a real cause of
            # the last loop sitting behind one.
            self.loops.setFixedHeight(height)

        if selected >= 0 and not self.loops.selectionModel().isRowSelected(
                selected, QtCore.QModelIndex()):
            with _quiet(self.loops):
                self.loops.selectRow(selected)

    def _fill_loop_row(self, index: int, instrument: str, row: dict,
                       kelvin: float | None) -> None:
        """One row of the loop table, instrument loop or software loop alike.

        The two differ in three places and nowhere else, which is why they
        share this: a software loop has no loop number and no range, and it is
        **not selectable** -- the command panel it would point at has a
        setpoint, a range and a set of gains, and the software loop takes none
        of those three commands.  What it takes is `arm` and the panic `hold`,
        which are buttons of their own.  A row that could be clicked into a
        selection the panel cannot honour would be a row that lies.
        """
        software = not instrument
        marks = loop_marks(row, kelvin, rails=row.get("rails"))
        heater = row.get("heater_output")
        cells = [""] * len(LOOP_COLUMNS)
        cells[COL_LOOP] = str(row.get("loop") or "")
        cells[COL_SENSOR] = str(row.get("sensor") or "—")
        cells[COL_KELVIN] = "—" if kelvin is None else f"{float(kelvin):.3f}"
        cells[COL_SETPOINT] = self._maybe(row.get("setpoint_k"), "{:.3f}")
        cells[COL_OUTPUT] = self._maybe(row.get("output_pct"), "{:.1f}")
        # n/a and not "—": a loop whose output is analog-only does not have a
        # range that happens to be unknown, it has none at all.  The software
        # loop is the same case for a stronger reason -- the 218 has no inert
        # half, so there is no range for it to have.
        cells[COL_RANGE] = ("n/a" if heater is None
                            else self._maybe(row.get("range"), "{:.0f}"))
        cells[COL_STATE] = _state_text(row.get("mode"))
        cells[COL_SATURATED] = MARK_SATURATED if marks["saturated"] else ""
        cells[COL_UNSETTLED] = MARK_UNSETTLED if marks["unsettled"] else ""

        # A software loop whose health is anything but ok is coloured like a
        # lit mark, because it *is* the same class of news: the supervisor has
        # stopped trusting its own measurement, and the two marks are silent
        # precisely because it is no longer trying.
        unhealthy = software and row.get("health") not in ("ok", "", None)
        for column, text in enumerate(cells):
            item = self.loops.item(index, column)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.loops.setItem(index, column, item)
            item.setText(text)
            lit = column in (COL_SATURATED, COL_UNSETTLED) and text
            if lit or unhealthy:
                item.setForeground(QtGui.QBrush(QtGui.QColor(warn_colour(self))))
            else:
                theme.clear_foreground(item)
            flags = item.flags()
            item.setFlags(flags & ~QtCore.Qt.ItemIsSelectable if software
                          else flags | QtCore.Qt.ItemIsSelectable)

        self.loops.item(index, COL_SENSOR).setToolTip(
            self._software_tooltip(row) if software else
            f"{instrument} loop {row.get('loop')}: "
            f"{row.get('mode') or 'mode unknown'}"
            + ("" if marks["trying"] else
               " — not trying to reach a setpoint, so neither warning "
               "applies"))
        self.loops.item(index, COL_STATE).setToolTip(
            self._software_tooltip(row) if software else
            f"what OUTMODE? says loop {row.get('loop')} is doing: "
            f"{row.get('mode') or 'unknown'}")
        rails = row.get("rails") if software else None
        low, high = ((rails[0], rails[1]) if rails and rails[0] is not None
                     else (SATURATED_LOW_PCT, SATURATED_HIGH_PCT))
        self.loops.item(index, COL_SATURATED).setToolTip(
            f"the output is at a rail (at or beyond {float(high):g}% or "
            f"{float(low):g}%): this loop has no authority left in the "
            "direction it is asking for"
            if marks["saturated"] else "")
        self.loops.item(index, COL_UNSETTLED).setToolTip(
            f"{row.get('sensor') or 'the sensor'} is further than "
            f"{self._maybe(row.get('threshold_k'), '{:g}')} K from the "
            "setpoint ("
            + ("max_error_k in the controller's config)" if software
               else "loop_thresholds in the recorder's config)")
            if marks["unsettled"] else "")

    @staticmethod
    def _software_tooltip(row: dict) -> str:
        """Everything the supervisor has to say, in one hover.

        The alarms and the reason have nowhere else to go: they are sentences,
        not cells, and a table wide enough for them would be a table that
        scrolls sideways.
        """
        parts = [f"the software loop, reading {row.get('sensor') or '?'}: "
                 f"{row.get('state') or 'state unknown'}"]
        # The state cell shows the supervisor's *state*, which is the half that
        # moves; the mode is what says whether the loop is closed at all, and
        # `idle` alone cannot tell "never armed" from "armed and then held".
        if row.get("mode_name"):
            parts.append(f"mode {row['mode_name']}")
        if row.get("health"):
            parts.append(f"health {row['health']}")
        target = row.get("setpoint_target_k")
        if row.get("ramping") and target is not None:
            parts.append(f"ramping toward {float(target):g} K")
        if row.get("reason"):
            parts.append(str(row["reason"]))
        for alarm in row.get("alarms") or []:
            parts.append(str(alarm))
        parts.append("this loop is watched here, not commanded here: it takes "
                     "no setpoint, range or PID command, only Arm and the "
                     "panic Hold")
        return " — ".join(parts)

    @staticmethod
    def _note(label: QtWidgets.QLabel, text: str, style: str = "") -> None:
        """Set a wrapped note and let it have the height its wrapping needs.

        A word-wrapped ``QLabel`` reports a one-line ``sizeHint``, so a layout
        gives it one line and clips the rest -- which is how the range note
        came to end mid-sentence at "Use Panic \u2192 All heaters OFF, which is
        exempt from thi". These notes are the only explanation of why a
        control is dead, so half of one is worse than none.

        ``setHeightForWidth`` on the size policy is what makes the layout ask
        the label how tall it needs to be at the width it is being given.
        """
        label.setText(text)
        if style:
            label.setStyleSheet(style)
        policy = label.sizePolicy()
        policy.setHeightForWidth(True)
        label.setSizePolicy(policy)
        width = label.width()
        if width > 0:
            label.setMinimumHeight(label.heightForWidth(width))

    @staticmethod
    def _maybe(value, fmt: str) -> str:
        """A number the recorder may not have.  Never a plausible zero."""
        return "—" if value is None else fmt.format(float(value))

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
        allowed = self.source.source_allowed(GUI_SOURCE)
        enabled = bool(self.spool) and accepted and allowed and bool(names)
        self.command_group.setEnabled(enabled)
        # Neither of these is in that group -- see `_panic_box` and
        # `_source_box` -- so they are unaffected here, which is the point.
        self.panic_button.setEnabled(bool(self.spool) and accepted)
        self._sync_source_box()
        if not self.spool:
            why = "this viewer was started without a command spool"
        elif not accepted:
            why = ("the recorder is not accepting commands — set "
                   "ipc.accept_commands: true in its config and restart it")
        elif not allowed:
            why = self.source.source_note(GUI_SOURCE)
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

        if caps["loops"] and self._loop not in caps["loops"]:
            # A different box: the loop number the last one was on may not
            # exist here, and a setpoint sent to a loop that does not exist is
            # a refusal at best.
            self._loop = caps["loops"][0]

        if caps["has_analog"]:
            ceiling = caps["max_output_pct"]
            self.analog_spin.setMaximum(ceiling)
            self.analog_group.setTitle(
                f"Analog output {caps['analog_output']} (max {ceiling:g}%)")
        self._show_loop_controls(caps)
        # A different box, loop or output is a different "now": whatever the
        # operator had half-typed belonged to the previous selection.
        self._setpoint_dirty = False
        self._range_dirty = False
        self._analog_dirty = False
        self._awaiting = None
        self.source.poll()
        self._sync_command_values()
        self._update_gate_notes()

    def _selected_loop_row(self) -> dict:
        """The status entry for the loop the panel is pointed at, or ``{}``.

        ``{}`` for a recorder too old to publish one, which is the same
        degrade `capabilities` makes -- the panel then falls back to what it
        can work out from the capability block alone.
        """
        link = self.source.link_named(self.instrument_combo.currentText())
        for row in loop_rows(link):
            if int(row.get("loop") or 0) == self._loop:
                return row
        return {}

    def _heater_for_selected_loop(self, caps: dict) -> int | None:
        """Which heater output the selected loop drives, or None if it drives
        an analog one.

        From the recorder's `OUTMODE`-derived row where there is one, and from
        the capability table otherwise -- on this family the loop number *is*
        the output number by protocol, so the fallback is not a guess.
        """
        row = self._selected_loop_row()
        if row:
            heater = row.get("heater_output")
            return None if heater is None else int(heater)
        return self._loop if self._loop in caps["heater_outputs"] else None

    def _show_loop_controls(self, caps: dict) -> None:
        """Show the grouping the selected loop can actually be commanded with.

        Only the relevant one is ever on screen.  A loop that drives a heater
        gets the range control; one whose output is analog-only -- a 336's 3
        and 4 -- has no range to set, and offering the control would be
        offering a refusal.  A box with no loops at all (a 218) gets the
        analog control and nothing else, because on that box the percentage
        *is* the power.
        """
        self.setpoint_group.setVisible(caps["has_loops"])
        # Gains belong to a loop, so they appear exactly where a setpoint does
        # -- including on a 336's loops 3 and 4, which have gains and no range.
        self.pid_group.setVisible(caps["has_loops"])
        heater = self._heater_for_selected_loop(caps) if caps["has_loops"] else None

        self.range_group.setVisible(caps["has_heater_range"] and heater is not None)
        self.range_group.setTitle(
            "Heater range" if heater is None else f"Heater range (output {heater})")
        self.heater_label.setText("—" if heater is None else str(heater))

        # The analog grouping belongs to a box that will accept an `analog`
        # command.  A 336 loop 3 has an analog output and no way to command it
        # from here, which is a sentence to say rather than a control to offer.
        self.analog_group.setVisible(
            caps["has_analog"] and (not caps["has_loops"] or heater is None))

        self.loop_label.setText(str(self._loop) if caps["has_loops"] else "—")
        row = self._selected_loop_row()
        if not caps["has_loops"]:
            note = ""
        elif not row:
            note = (f"loop {self._loop} — this recorder does not publish loop "
                    "bindings (schema 1); the sensor and mode are unknown")
        else:
            note = (f"loop {self._loop} reads {row.get('sensor') or '?'} "
                    f"({row.get('mode') or 'mode unknown'})")
            if heater is None:
                note += (" and drives an analog output, which this recorder "
                         "has no command for")
        self.loop_note.setText(note)

    # -- filling the command widgets with what the cryostat is at -----------------

    def _aux_value(self, name: str) -> float | None:
        """One scalar from the status file's aux block, by its full name.

        The aux block is where each driver reports what it *read back* --
        ``{inst}.setpoint{loop}``, ``{inst}.range{out}``, ``{inst}.aout{out}`` --
        so it is also the honest answer to "what is this box at now", age and
        all.  ``None`` when the recorder does not carry that name: an older
        recorder or a query that failed this cycle.
        """
        for entry in (self.source.status or {}).get("aux", []):
            if entry.get("name") == name:
                value = entry.get("value")
                return None if value is None else float(value)
        return None

    def _setpoint_edited(self, _value: float) -> None:
        self._setpoint_dirty = True

    def _range_edited(self, *_ignored) -> None:
        self._range_dirty = True

    def _analog_edited(self, _value: float) -> None:
        self._analog_dirty = True

    def _pid_edited(self, _value: float) -> None:
        self._pid_dirty = True

    def _loop_row_selected(self) -> None:
        """A row of the loop table clicked: point the whole panel at that loop.

        Instrument and loop together, because a row names both -- and because
        selecting a loop on one box while the command panel is still addressed
        to another is exactly the mistake having one selector is meant to
        remove.
        """
        rows = self.loops.selectionModel().selectedRows()
        if not rows:
            return
        index = rows[0].row()
        if not 0 <= index < len(self._loop_index):
            return
        instrument, row = self._loop_index[index]
        self._loop = int(row.get("loop") or 1)
        names = [self.instrument_combo.itemText(i)
                 for i in range(self.instrument_combo.count())]
        if instrument in names:
            if self.instrument_combo.currentText() != instrument:
                # _instrument_changed does the rest, including this loop.
                self.instrument_combo.setCurrentIndex(names.index(instrument))
                return
        elif instrument:
            self.loop_note.setText(
                f"{instrument} is read-only on this recorder; its loops can be "
                "watched here but not commanded")
        # A different loop is a different "now" for every field in the panel.
        self._setpoint_dirty = False
        self._range_dirty = False
        self._awaiting = None
        self._instrument_changed()

    def _sync_command_values(self) -> None:
        """Fill each command widget with its control's current value.

        Swapping to a 218 should find the percentage it is already at; asking
        a 33x for a setpoint should start from the one it is chasing -- not
        from zero, which on these widgets reads as a plausible number to send.
        The values come from the recorder's readback (the aux block), so they
        carry the cycle delay; they are what the box says, not a promise.

        Filling repeats every tick while a widget still shows the cryostat's value,
        so a setpoint changed elsewhere (MATLAB, another viewer) arrives here
        too.  Once the operator edits a field it stops tracking -- a fill that
        fought the number being typed would be worse than a stale one -- until
        the selection changes or the pending command is acknowledged, either of
        which makes the field a fresh question again.

        One gap needs its own guard: between an acknowledged command and the
        readback that reflects it, the aux block still holds the *old* value,
        and a fill here would snap the field back to it -- showing 0% in the
        seconds after someone asked for 43%, which is worse than useless while
        power is the question.  So a queued command names the one readback that
        would confirm it, and until that readback agrees the field is left at
        what was asked for.
        """
        instrument = self.instrument_combo.currentText()
        if not instrument:
            return
        awaiting = self._awaiting
        if awaiting is not None:
            actual = self._aux_value(awaiting.aux)
            answered = actual is not None and (
                # Nothing to snap back to, so nothing to guard against.
                awaiting.previous is None
                # It landed where it was asked to.
                or abs(actual - awaiting.expected) <= awaiting.tolerance
                # Or it landed somewhere else -- which is news, not noise.
                or abs(actual - awaiting.previous) > awaiting.tolerance
            )
            expired = QtCore.QDateTime.currentSecsSinceEpoch() > awaiting.deadline
            if answered or expired:
                self._awaiting = None
                awaiting = None

        def held(aux_name: str) -> bool:
            """True while this control's readback is still owed."""
            return awaiting is not None and awaiting.aux == aux_name

        if not self._setpoint_dirty:
            name = f"{instrument}.setpoint{self._loop}"
            value = self._aux_value(name)
            if value is not None and not held(name):
                with _quiet(self.setpoint_spin):
                    self.setpoint_spin.setValue(value)
        if not self._analog_dirty:
            caps = capabilities(self.source.link_named(instrument))
            if caps["has_analog"]:
                name = f"{instrument}.aout{caps['analog_output']}"
                value = self._aux_value(name)
                if value is not None and not held(name):
                    with _quiet(self.analog_spin):
                        self.analog_spin.setValue(value)
        if not self._pid_dirty:
            for key, spin in self.pid_spins.items():
                name = f"{instrument}.{key}{self._loop}"
                value = self._aux_value(name)
                if value is not None and not held(name):
                    with _quiet(spin):
                        spin.setValue(value)
        heater = self._heater_for_selected_loop(
            capabilities(self.source.link_named(instrument)))
        if not self._range_dirty and heater is not None:
            name = f"{instrument}.range{heater}"
            value = self._aux_value(name)
            if value is not None and not held(name):
                index = self.range_combo.findData(int(value))
                if index >= 0:
                    with _quiet(self.range_combo):
                        self.range_combo.setCurrentIndex(index)

    def _update_gate_notes(self) -> None:
        """Say which of the power gates is open, and disable what is shut.

        Then settle any command still waiting, which is the other thing that
        decides what the command box is currently saying.

        These used to stay live when their gate was shut, on the grounds that
        the direction removing heat was always permitted and hiding it would
        take away the one thing that always worked. Both halves of that are
        gone. The gates now apply to 0 as well -- cutting a heater is not
        automatically the safe direction -- so a live control here could only
        ever produce a refusal, which is the shape `_show_loop_controls`
        already refuses to offer. And the button for "make the cryostat safe
        now" is the Panic menu, which is exempt from these gates and is never
        disabled at all. The note points at it, so nothing is taken away
        without being replaced.
        """
        range_ok = self.source.allows_heater_range()
        self.range_combo.setEnabled(range_ok)
        self.range_button.setEnabled(range_ok)
        if range_ok:
            self._note(self.range_note, "")
        else:
            self._note(self.range_note,
                       "This recorder will not change a heater range from a "
                       "file (ipc.allow_heater_range: false) — including to 0. "
                       "Use Panic → All heaters OFF, which is exempt from this.",
                       theme.note_style("warn", self))
        analog_ok = self.source.allows_analog_output()
        self.analog_spin.setEnabled(analog_ok)
        self.analog_button.setEnabled(analog_ok)
        if analog_ok:
            self._note(self.analog_note,
                       "No ramp: this is one step, as fast as the cryostat "
                       "allows.", theme.note_style("muted", self))
        else:
            self._note(self.analog_note,
                       "This recorder will not drive this output from a file "
                       "(ipc.allow_analog_output: false) — including to 0. Use "
                       "Panic → All heaters OFF, which is exempt from this.",
                       theme.note_style("warn", self))

        # The gains are the one control that is worth *reading* where it
        # cannot be written, so the shut gate disables the button and leaves
        # the boxes live. Greying the numbers would take away the thing that
        # still works; leaving the button live would offer a click that can
        # only ever produce a refusal, which is the shape A3 removed from the
        # range control. Neither half is right on its own.
        #
        # Not conditioned on `polled` below: a recorder with `read_pid: false`
        # can still be *sent* gains, and `set_pid()` verifies them by readback.
        # That is a missing capability, not a withheld permission.
        self.pid_button.setEnabled(self.source.allows_pid())

        # Two different silences to tell apart. Blank boxes because nobody is
        # polling the gains is not the same as a recorder that will not accept
        # new ones, and an operator who cannot see the difference will conclude
        # the wrong thing about both.
        polled = any(self._aux_value(f"{self.instrument_combo.currentText()}."
                                     f"{key}{self._loop}") is not None
                     for key in self.pid_spins)
        if not polled:
            self._note(self.pid_note,
                       "This recorder does not read the loop gains, so these "
                       "are not the instrument's (read_pid: false in its "
                       "config).", theme.note_style("warn", self))
        elif not self.source.allows_pid():
            self._note(self.pid_note,
                       "Shown from the instrument, but this recorder will not "
                       "change them from a file (ipc.allow_pid: false).",
                       theme.note_style("warn", self))
        else:
            self._note(self.pid_note,
                       "The instrument's own gains. Changing them does not "
                       "apply power; it changes how the loop gets anywhere at "
                       "all.", theme.note_style("muted", self))

        self._update_pending()

    def _update_pending(self) -> None:
        """Settle the one command that is waiting to be acknowledged.

        Either the recorder has answered it or the spool's TTL has run out;
        both release the buttons, because a command the recorder can no
        longer apply is not one worth going on waiting for.
        """
        if self._pending is None:
            return
        cid, deadline = self._pending
        ack = self.source.ack_for(cid)
        if ack is not None:
            ok = bool(ack.get("ok"))
            self.ack_label.setText(
                ("✓ " if ok else "✗ ") + str(ack.get("message", "")))
            self.ack_label.setStyleSheet(
                theme.note_style("ok" if ok else "bad", self))
            self._pending = None
            # The question the fields were answering has been settled one way
            # or the other; let them track the cryostat's readback again.  A
            # refused command has no readback coming, so its guard goes at
            # once; an accepted one keeps its guard a little longer, until the
            # readback moves off the value it held before the command -- which
            # is what stops a stale aux value snapping the field back to where
            # the cryostat was.  See `_Awaiting`.
            if not ok:
                self._awaiting = None
            self._setpoint_dirty = False
            self._range_dirty = False
            self._analog_dirty = False
        elif QtCore.QDateTime.currentSecsSinceEpoch() > deadline:
            self.ack_label.setText(
                "no acknowledgement — the recorder may not be reading commands")
            self.ack_label.setStyleSheet(theme.note_style("warn", self))
            self._pending = None
            self._awaiting = None
        else:
            return                       # still waiting; leave the buttons locked
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
        else:
            bits.append(f"last {_duration(self._follow_span_s)} · live")
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
            self.curve_units[name] = "K" if kind == "kelvin" else "%"

            check = QtWidgets.QCheckBox(name)
            check.setChecked(True)
            # The curve's colour as a *swatch*, not as the text.  It has to
            # match a line drawn on the white plot, so it cannot be re-themed
            # for a dark panel -- and several of CURVE_COLORS are unreadable
            # as text on one ground or the other (cyan manages 2.26:1 on
            # white, brown 2.17:1 on a dark window). A stripe carries the same
            # identity and leaves the name to the palette, which is legible on
            # both.
            check.setStyleSheet(
                f"QCheckBox {{ border-left: 4px solid {colour};"
                " padding-left: 6px; font-weight:600; }")
            check.stateChanged.connect(self._redraw)
            self.toggles[name] = check
            self.traces_layout.insertWidget(self.traces_layout.count() - 1, check)

    def _redraw(self) -> None:
        #: The extent of what each panel is actually showing, so the comfort
        #: stop can widen to a reading that lies outside it.
        extents: dict[str, tuple[float, float] | None] = {
            unit: None for unit in self._panels
        }
        for name, curve in self.curves.items():
            if not self.toggles[name].isChecked():
                curve.setData([], [])
                continue
            if self._span is None:
                # A live-referenced window: the newest sample is the right
                # edge, and each redraw rides forward with it.
                t, v = self.tail.recent(name, self._follow_span_s)
            else:
                # Exactly the visible span, so a panel still autoscaling fits
                # itself to what is on screen: zoom into a five-minute wobble
                # and the wobble fills the panel instead of a day's excursion.
                # Full resolution once prepare_span has caught up with the
                # span; the thinned overview draws until then.  A panel whose
                # y axis was dragged out keeps the axis it was given; the cut
                # still matters, for the other panel and for the number of
                # points Qt is asked to draw.
                t, v = self.tail.between(name, *self._span)
            # A break where the samples stop for long enough to mean the
            # recorder was not running: the pen lifts rather than ruling a
            # straight line across an outage the cryostat did not spend at
            # some convenient interpolated temperature.
            curve.setData(t, v, connect=connect_flags(t, factor=self.gap_factor))
            if v:
                self._live_values[name] = float(v[-1])
                unit = self.curve_units[name]
                seen = extents[unit]
                lo, hi = min(v), max(v)
                extents[unit] = (lo, hi) if seen is None else (
                    min(seen[0], lo), max(seen[1], hi))
        self._apply_comfort_stops(extents)

    def _apply_comfort_stops(self, extents: dict) -> None:
        """Hold each value axis inside its comfort stop, or inside the data.

        A stop and not a clamp.  Panning a 300 K axis out to 10 000 K is a
        gesture nobody meant to make and a chart nobody can read, so the axis
        resists it -- but a sensor that has come loose and reads 1400 K is
        exactly the measurement somebody has to be able to look at, and an
        axis that refused to go there would be hiding the reading in favour of
        a number this file guessed.  So the stop is the wider of the two: the
        configured window, or everything on the panel.

        Only the value axis.  Time has no comfortable extent -- a log runs for
        as long as it runs -- and a stop on it would fight the drag that is
        the whole point of the chart.
        """
        for unit, plot in self._panels.items():
            floor, ceiling = self._comfort[unit]
            seen = extents.get(unit)
            if seen is not None:
                # A margin so a reading sitting exactly on the stop is not
                # pinned against the edge of the panel by it.
                pad = 0.05 * max(seen[1] - seen[0], 1e-9)
                floor = min(floor, seen[0] - pad)
                ceiling = max(ceiling, seen[1] + pad)
            plot.getViewBox().setLimits(yMin=floor, yMax=ceiling)

    # -- the cursors, what is between them, and what is under the pointer ---
    #
    # Two questions a strip chart gets asked that the trace alone cannot
    # answer: "what did it do between there and there", and "which of these
    # lines is that".  Both are measurements of the log rather than of the
    # picture, which is why the arithmetic is in `source` and not here -- and
    # why the statistics come from `samples_in`, at whatever resolution the
    # files hold, and never from the decimated overview the chart draws.

    def _toggle_cursors(self, checked: bool) -> None:
        """Put the two cursors on screen, or take them off again.

        They arrive at the thirds of the window rather than nowhere: a pair
        of cursors that has to be placed twice before it measures anything is
        a pair of cursors most people put away again.  From there each click
        moves whichever is nearer.
        """
        for plot in self._panels.values():
            plot.getViewBox().cursor_mode = bool(checked)
        if not checked:
            self._cursors = None
        else:
            x0, x1 = self.k_plot.getViewBox().viewRange()[0]
            width = x1 - x0
            self._cursors = (x0 + width / 3.0, x0 + 2.0 * width / 3.0)
        self.export_note.setText("")
        self._sync_cursor_items()
        self._update_region_stats()

    def _place_cursor(self, x: float) -> None:
        """A click or drag on a panel: move the cursor nearer to it.

        Nearer, rather than alternating.  Alternating means the operator has
        to remember which one moved last, and gets the wrong edge half the
        time; nearest is the rule the pointer already implies.
        """
        if self._cursors is None:
            return
        a, b = self._cursors
        self._cursors = (x, b) if abs(x - a) <= abs(x - b) else (a, x)
        self._sync_cursor_items()
        self._update_region_stats()

    def _sync_cursor_items(self) -> None:
        """Put the drawn cursors where ``_cursors`` says, on both panels."""
        for unit in self._panels:
            lines = self._cursor_lines[unit]
            shade = self._cursor_shades[unit]
            if self._cursors is None:
                for line in lines:
                    line.hide()
                shade.hide()
                continue
            for line, x in zip(lines, self._cursors):
                line.setPos(x)
                line.show()
            shade.setRegion(tuple(sorted(self._cursors)))
            shade.show()

    def _update_region_stats(self) -> None:
        """Measure the region between the cursors, and say what it measured.

        Recomputed only when it can have changed.  A region that lies wholly
        in the past cannot: nothing the recorder does now alters what
        happened between two past instants, so it is measured once and left.
        A region whose right-hand cursor sits beyond the newest sample is
        still filling, and is re-measured as rows arrive -- but no more often
        than ``STATS_RELOAD_S`` once decimation has started, because from
        then on the answer costs a scan of every log in the directory rather
        than a slice of memory.
        """
        if self._cursors is None:
            for label in self._stat_labels.values():
                label.hide()
            self._stats = {}
            self._stats_key = None
            self.export_button.setEnabled(False)
            self._update_legend()
            return

        t0, t1 = sorted(self._cursors)
        newest = self.tail.newest()
        growing = newest is None or newest < t1
        key = (t0, t1, self.tail.rows if growing else None)
        if key == self._stats_key:
            return
        now = time.monotonic()
        if (growing and self.tail.thinned and self._stats_key is not None
                and now - self._stats_read_at < STATS_RELOAD_S):
            return

        samples = self.tail.samples_in(t0, t1)
        self._stats_read_at = now
        self._stats_key = key
        stats = region_stats(samples)

        self._stats = {}
        for unit in self._panels:
            rows = []
            for name in self.curves:
                if self.curve_units[name] != unit:
                    continue
                if not self.toggles[name].isChecked():
                    continue
                st = stats.get(name)
                if st is None:
                    continue
                self._stats.setdefault(unit, {})[name] = st
                rows.append(
                    f"{name}   mean {st.mean:.3f}   sd {st.std:.3f}   "
                    f"Δ {st.delta:+.3f}   (n={st.n})"
                )
            label = self._stat_labels[unit]
            if not rows:
                label.hide()
                continue
            # Δt once, in the header, because it is a property of the region
            # and not of any one trace.
            label.setText("\n".join([f"Δt {_duration(t1 - t0)}", *rows]))
            label.show()
        self.export_button.setEnabled(bool(samples))
        self._update_legend()

    def _update_legend(self) -> None:
        """The legend carries the live value -- but only while nothing is picked.

        With cursors up the statistics panel is the answer to "what is this
        trace doing", and a second number a few pixels away, measured over a
        different span, is how two readings of the same trace come to
        disagree on screen.  So the legend goes back to being names.
        """
        picked = self._cursors is not None
        for name, curve in self.curves.items():
            legend = self._panels[self.curve_units[name]].legend
            if legend is None:
                continue
            label = legend.getLabel(curve)
            if label is None:
                continue
            value = self._live_values.get(name)
            text = name if (picked or value is None) else f"{name}   {value:.3f}"
            if getattr(label, "text", None) != text:
                label.setText(text)

    def _on_hover(self, event) -> None:
        """Name the trace under the pointer, and read it there.

        Interpolated at the pointer's time rather than snapped to the nearest
        sample: on a decimated overview the nearest sample can be minutes
        away, and a number that far from where the pointer is pointing is a
        different reading.

        On a signal rather than the timer, so it must not raise: an exception
        out of a Qt slot takes the event loop with it.
        """
        try:
            pos = event[0] if isinstance(event, (tuple, list)) else event
            for unit, plot in self._panels.items():
                vb = plot.getViewBox()
                label = self._hover_labels[unit]
                if not vb.sceneBoundingRect().contains(pos):
                    label.hide()
                    continue
                point = vb.mapSceneToView(pos)
                x, y = float(point.x()), float(point.y())
                traces = {}
                for name, curve in self.curves.items():
                    if self.curve_units[name] != unit:
                        continue
                    if not self.toggles[name].isChecked():
                        continue
                    t, v = curve.getData()
                    if t is None or len(t) == 0:
                        continue
                    traces[name] = (t, v)
                _, y_per_px = vb.viewPixelSize()
                hit = nearest_series(
                    traces, x, y,
                    tolerance=HOVER_TOLERANCE_PX * abs(y_per_px),
                )
                if hit is None:
                    label.hide()
                    continue
                name, value = hit
                label.setText(f"{name}  {value:.3f} {unit}")
                label.setPos(x, value)
                label.show()
        except Exception:  # noqa: BLE001 - a hover must not stop the viewer
            log.debug("hover readout failed", exc_info=True)

    def _export_region(self) -> None:
        """Write the cursor region out as a CSV, at full resolution.

        Every column the log carries, not only the traces that happen to be
        ticked: the region is a piece of the recording, and what somebody
        wants out of it a week later is not necessarily what was on screen
        when they picked it.
        """
        if self._cursors is None:
            return
        t0, t1 = sorted(self._cursors)
        stamp = QtCore.QDateTime.fromSecsSinceEpoch(int(t0)).toString(
            "yyyyMMdd-HHmmss")
        folder = os.path.dirname(self.tail.path or "") or os.getcwd()
        chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export the cursor region",
            os.path.join(folder, f"region_{stamp}.csv"),
            "CSV files (*.csv);;All files (*)",
        )
        if not chosen:
            return
        try:
            samples = self.tail.samples_in(t0, t1)
            rows = write_region_csv(chosen, samples,
                                    columns=self.tail.columns())
        except OSError as exc:
            self.export_note.setText(f"could not write {chosen}: {exc}")
            self.export_note.setStyleSheet(theme.note_style("bad", self))
            return
        self.export_note.setText(
            f"wrote {rows} row(s) over {_duration(t1 - t0)} to "
            f"{os.path.basename(chosen)}")
        self.export_note.setStyleSheet(theme.note_style("ok", self))

    # -- choosing the window with the mouse --------------------------------

    def _select_region(self, unit: str, x, y) -> None:
        """A drag finished on the panel measured in `unit`: take it literally.

        The time axis is shared, so it goes to both panels over the link.  The
        value axis is not, so it goes only to the panel that was dragged --
        the other one keeps autoscaling to whatever the new window holds.
        """
        self._stop_autoscaling(unit)
        self._ylim[unit] = (y[0], y[1])
        self._panels[unit].setYRange(y[0], y[1], padding=0)
        self._span = (x[0], x[1])
        self.k_plot.setXRange(x[0], x[1], padding=0)
        self._span_changed()

    def _stop_autoscaling(self, unit: str | None = None) -> None:
        """Take the time axis, and one panel's value axis, off autoscale.

        Before anything is recorded, and never after.  pyqtgraph enacts one
        last autoscale on the way out of `enableAutoRange(False)`, so that the
        range someone had been looking at is the one they keep; the range
        change it emits arrives here as a wheel zoom would, and would
        overwrite the window that had just been chosen with the one being
        left behind.  So a gesture that fixes an axis reads what is on screen
        into a local *first*, stops the autoscaling second -- eating that
        signal while its own numbers sit somewhere untouchable -- and only
        then writes and applies them.
        """
        for plot in self._panels.values():
            plot.enableAutoRange(x=False)
        if unit is not None:
            self._panels[unit].enableAutoRange(y=False)

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
        self._sync_view_buttons()
        self._update_statusbar()

    def _is_live(self) -> bool:
        """True while every axis is following the data rather than a decision."""
        return self._span is None and not any(self._ylim.values())

    def _follow_live(self, *_ignored) -> None:
        """A double-click: back to following the recorder.

        Back to *the window that was showing*, not to some canonical one.  A
        double-click is how a hand-picked span is abandoned, and abandoning it
        should return the chart to what it was before the drag rather than
        also silently rescaling the time axis to a day.
        """
        self._set_follow(self._follow_span_s)

    def _follow_window(self, seconds: float) -> None:
        """A live-referenced window button: the last ``seconds``, riding."""
        self._set_follow(seconds)

    def _set_follow(self, seconds: float) -> None:
        """Enter a live-referenced view and drop every hand-picked axis."""
        self._span = None
        self._armed_span = None
        # The overlay the tail holds belongs to whatever span was picked last;
        # coming back here must not assume it still matches a future pick.
        self._loaded_span = None
        self._follow_span_s = seconds
        for unit, plot in self._panels.items():
            self._ylim[unit] = None
            plot.enableAutoRange(x=True, y=True)
        self._sync_view_buttons()
        if self._is_live():
            self._redraw()          # a re-click with nothing to leave
            self._update_statusbar()
        else:
            self._span_changed()

    def _sync_view_buttons(self) -> None:
        """Make the view row say which window is showing.

        While a span is hand-picked no live-referenced view is, so nothing is
        checked -- the buttons are a way *back*, not a description of a fixed
        view.
        """
        following = self._span is None
        for seconds, button in self.span_buttons.items():
            button.setChecked(following and seconds == self._follow_span_s)

    def _zoom_x(self, factor: float) -> None:
        """An X button: scale the time axis about the middle of the window.

        Pressing it is a decision, so it fixes the axis the way a drag does --
        the view stops following the recorder, and the `Live` button says so.
        """
        # Read, stop, apply -- in that order.  Disabling an axis enacts one
        # last autoscale on the way out, and while the recorder runs there is
        # always one queued: its range-changed signal arrives here as any
        # other would, so a `_span` assigned before it came back overwritten
        # by the view being left (the first press of three went missing that
        # way), and a base read after it zoomed from data nobody had seen.
        # Reading into a local keeps the press out of the signal's way.
        base = self.k_plot.getViewBox().viewRange()[0]
        self._stop_autoscaling()
        self._span = _scaled(base, factor)
        self.k_plot.setXRange(*self._span, padding=0)
        self._span_changed()

    def _zoom_y(self, factor: float) -> None:
        """A Y button: scale both value axes, each about its own middle.

        Both, because the buttons name an axis and not a panel, and letting
        one press mean the kelvin panel on Tuesdays is worse than moving a
        percent axis nobody was looking at.  Each keeps its own centre: they
        are different quantities and share no scale.
        """
        for unit, plot in self._panels.items():
            # As in _zoom_x: read, stop, apply.
            base = plot.getViewBox().viewRange()[1]
            plot.enableAutoRange(y=False)
            self._ylim[unit] = _scaled(base, factor)
            plot.setYRange(*self._ylim[unit], padding=0)
        # No redraw: the value axis does not decide which samples are drawn.
        self._sync_view_buttons()
        self._update_statusbar()

    def _span_changed(self) -> None:
        self._sync_view_buttons()
        self._redraw()
        self._update_statusbar()

    # -- commanding --------------------------------------------------------

    def _buttons(self) -> list[QtWidgets.QPushButton]:
        """Everything that can queue a command, so one pending command locks all.

        Not just the button that was pressed: commands are applied in order on
        the recorder's next cycle, and letting a second one be queued while the
        first is unacknowledged is how you get a range raised against a
        setpoint that turned out to be refused.

        **The panic menu is deliberately not in this list.** That reasoning
        inverts for the stopping direction: no pending command can make it
        wrong to stop, and an operator reaching for Panic while somebody's
        setpoint is still being acknowledged must not find it greyed out.
        `arm` *is* in the list -- it applies power, so it queues like any
        other write.
        """
        return [self.send_button, self.pid_button, self.range_button,
                self.analog_button, self.arm_button]

    def _confirm(self, title: str, text: str) -> bool:
        return QtWidgets.QMessageBox.question(
            self, title, text,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        ) == QtWidgets.QMessageBox.Yes

    def _await_readback(self, aux: str, expected: float, tolerance: float) -> None:
        """Hold one field at what was asked for until its readback agrees.

        Only after the command was actually queued -- there is nothing to wait
        for otherwise, and a guard set on a command that never left would hold
        the field against the cryostat for no reason.
        """
        if self._pending is None:
            return
        self._awaiting = _Awaiting(
            aux, float(expected), self._aux_value(aux), tolerance,
            QtCore.QDateTime.currentSecsSinceEpoch() + READBACK_GRACE_S,
        )

    @staticmethod
    def _display_tolerance(spin) -> float:
        """Half of the smallest step the spin box can show.

        Anything closer than this rounds to the same thing on screen, so it
        is the finest difference worth calling a disagreement.
        """
        return 0.5 * 10.0 ** -spin.decimals()

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
                kind, instrument=instrument, source=GUI_SOURCE, **args,
            )
        except OSError as exc:
            self.ack_label.setText(f"could not queue the command: {exc}")
            self.ack_label.setStyleSheet(theme.note_style("bad", self))
            return
        self.ack_label.setText(f"queued {kind} {cid}, waiting for the recorder…")
        self.ack_label.setStyleSheet(theme.note_style("muted", self))
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
        loop = self._loop
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
        self._await_readback(f"{instrument}.setpoint{loop}", kelvin,
                             self._display_tolerance(self.setpoint_spin))

    def _send_pid(self) -> None:
        """Queue all three gains for the selected loop."""
        if self.spool is None:
            return
        instrument = self.instrument_combo.currentText()
        loop = self._loop
        gains = {k: spin.value() for k, spin in self.pid_spins.items()}
        if not self._confirm(
            "Send PID gains",
            f"Retune loop {loop} of {instrument} to "
            f"P {gains['p']:.1f}, I {gains['i']:.1f}, D {gains['d']:.1f}?\n\n"
            "This applies no power: a loop with its range at 0 stays inert "
            "however it is tuned. It does change how the loop behaves for the "
            "rest of the run, including while it is already driving.",
        ):
            return
        self._queue("pid", loop=loop, **gains)
        self._await_readback(f"{instrument}.p{loop}", gains["p"],
                             self._display_tolerance(self.pid_spins["p"]))

    def _send_range(self) -> None:
        """Queue a heater range.  Above 0 this is the command that applies power."""
        if self.spool is None:
            return
        instrument = self.instrument_combo.currentText()
        output = self._heater_for_selected_loop(
            capabilities(self.source.link_named(instrument)))
        if output is None:
            self.ack_label.setText(
                f"loop {self._loop} of {instrument} drives no heater range")
            self.ack_label.setStyleSheet(theme.note_style("warn", self))
            return
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
        # A range is one of a handful of named steps, not a measured
        # quantity: it reads back as the integer it was set to or it did not
        # take, so half a step is all the slack it needs.
        self._await_readback(f"{instrument}.range{output}", float(value), 0.5)

    def _send_hold(self) -> None:
        """Stop every loop where it is.  The second panic action."""
        if self.spool is None:
            return
        if not self._confirm(
            "Hold all temperatures",
            "Stop every loop where it is?\n\n"
            "Each closed 33x loop has its ramping switched off (the rate is "
            "kept) and its setpoint moved to its own sensor's present "
            "temperature. A software loop has its output frozen and stops "
            "regulating.\n\n"
            "HOLD IS NOT A SYNONYM FOR LESS POWER. While a ramp is heading "
            "down, its setpoint sits below the temperature the cryostat has "
            "actually reached — so holding, which adopts that reached "
            "temperature, demands more heat than the ramp was demanding. It "
            "never raises a range, so it stays inside the power already "
            "permitted.\n\n"
            "Hold also means two different things on the two boxes: a 33x loop "
            "holds a TEMPERATURE and keeps regulating; a 218 holds a POWER, "
            "and nothing regulates the sample afterwards, so it will drift "
            "with the cryostat.\n\n"
            "Ramping is left off. Turn it back on yourself when you want it.",
        ):
            return
        self._queue("hold", instrument="")
        # Every loop at once; there is no single readback that confirms it.
        self._awaiting = None

    def _send_arm(self) -> None:
        """Close the software loop again.  The power-applying direction."""
        if self.spool is None:
            return
        if not self._confirm(
            "Arm the software loop",
            "Close the software loop at the temperature the cryostat is at "
            "now?\n\n"
            "THIS APPLIES POWER. The loop starts driving the heater again. It "
            "is not a panic action and is exempt from nothing: it needs "
            "ipc.allow_analog_output like any other write.\n\n"
            "If the cryostat drifted while it was held, the error that has "
            "accumulated is real — but the supervisor's clamp and rate limiter "
            "still bound what the output can do about it.\n\n"
            "A recorder with no software loop will say so rather than doing "
            "anything.",
        ):
            return
        self._queue("arm", instrument="")
        self._awaiting = None

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
        loop = self._loop
        value = self._aux_value(f"{instrument}.setpoint{loop}")
        if value is not None:
            return (f"{value:.3f} K on loop {loop}, as the "
                    f"recorder read it {self.source.age_s or 0.0:.0f} s ago")
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
                "cryostat follows as fast as it can.\n\n"
                f"The recorder's ceiling is {caps['max_output_pct']:g}%. Know "
                "the gain of your heater before confirming — on a cryostat "
                "sample heater a single percent can be tens of kelvin."
            )
        if not self._confirm("Set analog output", text):
            return
        self._queue("analog", percent=percent)
        self._await_readback(f"{instrument}.aout{caps['analog_output']}", percent,
                             self._display_tolerance(self.analog_spin))

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
        # Many readbacks at once; none of them is *the* one to wait for.
        self._awaiting = None
