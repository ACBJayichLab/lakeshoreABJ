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
import html
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
    SATURATED_HIGH_PCT, SATURATED_LOW_PCT, classify_column, connect_flags,
    loop_marks, loop_rows, nearest_series, reading_rows, region_stats, write_region_csv,
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
#:
#: This is a bound on the *overview* only.  Anything older is still drawn,
#: and drawn from the log rather than from memory, because `prepare_span`
#: goes back to disk for whatever window is picked -- which is why nothing
#: here may be allowed to fall back to "let the overview answer" for a span
#: that reaches further back than this.  It has nothing to answer with.
BACKFILL_COVERAGE_S = VIEW_WINDOWS[-1][1] + 3600.0

#: How long a view gesture must be still before the span behind it is read
#: off disk.  Long enough that a wheel or a drag is one read rather than
#: thirty; short enough that letting go of the mouse and seeing the trace
#: appear feel like one event.
SPAN_SETTLE_MS = 250

#: How long after the first samples reach the chart the coarse whole-archive
#: backdrop is read.  Long enough that opening the window is not held up by
#: it; far shorter than it takes to reach for the mouse, which is the only
#: thing that needs it.
ATLAS_SETTLE_MS = 1200

#: How this viewer labels itself in every command it writes.  A recorder's
#: `ipc.sources` policy is keyed on exactly this string, so it is a constant
#: and not a literal repeated at each call site.
GUI_SOURCE = "lschart-gui"

#: The clients the source strip offers a switch for, and what to call them.
#:
#: ``default`` is not a client: it is the overlay's own catch-all, and unticking
#: it mutes every client the policy does not name -- the CLI, a second viewer,
#: a script somebody wrote this morning. It is the only way to shut out a label
#: you do not know in advance, and like every overlay entry it may only narrow
#: what the config already allows.
SOURCE_CHOICES = (
    ("matlab", "MATLAB"),
    (GUI_SOURCE, "This viewer"),
    ("default", "Other clients"),
)

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


#: The one reading table's columns.  ``Rail`` and ``Off SP`` are deliberately
#: two columns and not one: OR-ing them into a single warning gives an icon
#: that is lit through every cooldown, and an icon that is always lit is an
#: icon nobody reads.  They also mean different things -- a loop pinned at its
#: rail has run out of authority, a loop far from its setpoint may simply be on
#: the way.  Headings are terse because the panel is narrow and a table that
#: scrolls sideways hides the very marks it exists to show.
#:
#: ``State`` carries what the loop is *doing*, which used to be reachable only
#: by hovering.  It decides whether either mark applies at all, so a loop that
#: has quietly stopped trying -- switched to open loop, or a software loop
#: locked out after a fault -- was previously invisible without a mouse.
#:
#: **One table and not two.**  There used to be a per-channel readouts table
#: with a loop table beneath it, which on a 33x-only cryostat is the same four
#: lines twice.  The row is the *channel* and the loop is a set of columns on
#: it, which is what keeps the eight inputs of a 218 from being collapsed into
#: however many loops it has -- see `reading_rows` in `gui.source`.
READING_COLUMNS = ["Channel", "K", "Loop", "SP", "Out", "Rng",
                   "State", "Rail", "Off SP"]

#: Column indices, by name.  Derived rather than written down: the marks moved
#: when ``State`` was added, and hardcoded 6s and 7s move silently.
#: The panel never starts narrower than this, and never takes so much that the
#: chart is left under ``_MIN_CHART_PX``.  Both are floors on a *measured*
#: width rather than the width itself -- see ``_fit_panel_to_table``, and see
#: what happened when the width itself was written down.
_MIN_PANEL_PX = 560
_MIN_CHART_PX = 520

#: How much channel name the panel is sized to hold, in characters.  The sensor
#: is the column that gives -- it is repeated in the trace list and in the row's
#: own tooltip, so a truncated name is still identifiable, while a mark past the
#: edge is not anywhere.  But it may only give so far: at the old width "Stage 1"
#: and "Stage 2" both came out "Stag…", two thermometers reading the same.
#: So the panel is sized for an ordinary name and a pathological one elides.
_CHANNEL_FIT_CHARS = 14

#: Qt's own left/right margin inside a table cell.
_CELL_PADDING_PX = 8

COL_CHANNEL, COL_KELVIN, COL_LOOP = 0, 1, 2
COL_SETPOINT, COL_OUTPUT, COL_RANGE = 3, 4, 5
COL_STATE = READING_COLUMNS.index("State")
COL_SATURATED = READING_COLUMNS.index("Rail")
COL_UNSETTLED = READING_COLUMNS.index("Off SP")

#: What each mark says when it is lit.  Words rather than glyphs: this is read
#: at 2 a.m. by somebody who has not seen the legend.
MARK_SATURATED = "RAIL"
MARK_UNSETTLED = "OFF SP"

#: Red, for a lit mark and for a software loop whose health is not ``ok``.
#: Resolved through `gui.theme` at paint time: the old constant was invisible
#: on a dark desktop, and so was the black it was paired with.
def warn_colour(widget=None) -> str:
    return theme.colour("bad", widget)


def _tighten(layout) -> None:
    """Trim a group's padding.

    Qt's defaults are laid out for a dialog with room to breathe. This panel
    is short of height on every screen it has been opened on, and the padding
    is the cheapest thing in it to give up -- nothing is removed and nothing
    becomes harder to hit.
    """
    layout.setContentsMargins(8, 6, 8, 6)
    layout.setSpacing(4)


def _compact(button: QtWidgets.QAbstractButton, width: int = 0) -> None:
    """Trim a button to its text.

    Qt's default push button reserves a generous minimum width and a tall
    frame, which is right for a dialog and wasteful for a strip of five
    two-character controls in a panel that is short of height either way.
    """
    button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
    hint = button.fontMetrics().horizontalAdvance(button.text()) + 18
    button.setFixedWidth(width or max(hint, 34))
    button.setMinimumHeight(26)


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


#: The region-statistics panel, as a table rather than as padded text.  The
#: label is drawn in the UI's proportional font, where two spaces are not a
#: column, so the columns are a real ``<table>``: every number lands under its
#: heading whatever the trace is called and however many digits it carries.
#: Numbers right-align, so decimal points line up down a column; names do not.
def _stats_html(header: str, rows: list[tuple[str, str, str, str, str]]) -> str:
    """The statistics panel's markup: a header line over one table.

    ``rows`` is ``(name, mean, sd, delta, n)``, already formatted.  Nothing
    here sets a text colour -- the item's own colour is the normal case, and
    painting it here is the one thing that would break on a dark desktop.
    """
    def cell(text: str, align: str, head: bool = False) -> str:
        tag = "th" if head else "td"
        weight = "" if head else " font-weight: normal;"
        return (f'<{tag} align="{align}" style="padding: 0px 7px;{weight}">'
                f"{html.escape(text)}</{tag}>")

    head = (cell("", "left", True) + cell("mean", "right", True)
            + cell("sd", "right", True) + cell("Δ", "right", True)
            + cell("n", "right", True))
    body = "".join(
        "<tr>" + cell(name, "left") + cell(mean, "right") + cell(sd, "right")
        + cell(delta, "right") + cell(n, "right") + "</tr>"
        for name, mean, sd, delta, n in rows
    )
    return (f"<div>{html.escape(header)}</div>"
            f'<table cellspacing="0" cellpadding="0">'
            f"<tr>{head}</tr>{body}</table>")


def _extent(rng) -> float:
    """The width of an ``(lo, hi)`` range."""
    return float(rng[1]) - float(rng[0])


def _at_fraction(rng, frac: float) -> float:
    """The value ``frac`` of the way up an ``(lo, hi)`` range."""
    return float(rng[0]) + frac * _extent(rng)


def _close(a, b, *, tol: float = 1e-9) -> bool:
    """Are two ranges the same range?  Scaled tolerance, because these are
    kelvin and percent and the numbers are what the axis happens to hold."""
    scale = max(1.0, abs(float(a[0])), abs(float(a[1])))
    return (abs(float(a[0]) - float(b[0])) <= tol * scale
            and abs(float(a[1]) - float(b[1])) <= tol * scale)


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
        #: The value range the wheel has actually been asked for, before the
        #: comfort stop clamps it for display, and the clamped range that came
        #: back.  ``None`` until a wheel gesture starts, and again whenever
        #: anything else moves the axis.  See :meth:`wheelEvent`.
        self._y_virtual: tuple[float, float] | None = None
        self._y_shown: tuple[float, float] | None = None
        self._band = QtWidgets.QGraphicsRectItem()
        # Width 0 keeps the pen cosmetic: the item lives in data coordinates,
        # where one x unit is a second and a scaled pen would be a smear.
        self._band.setPen(pg.mkPen("#1f77b4", width=0))
        self._band.setBrush(pg.mkBrush(31, 119, 180, 45))
        self._band.setZValue(1e9)
        self._band.hide()
        self.addItem(self._band, ignoreBounds=True)

    #: How far past the comfort stop the wheel may go on wanting to zoom out,
    #: as a multiple of the stop's own width.  Without a cap, holding the
    #: wheel out for a few seconds would have to be undone notch for notch
    #: before the value axis moved again, which reads as a dead control.
    #: Sixteen is four notches at the default step: far enough that no
    #: ordinary gesture reaches it, near enough to scroll back out of.
    Y_OVERSCROLL_LIMIT = 16.0

    def _pointer_fraction(self, ev) -> float:
        """How far up the panel the pointer is, 0 at the bottom, 1 at the top.

        A fraction and not a value, because the value depends on which range
        is being asked -- the one on screen or the one the wheel has been
        accumulating -- and the pixel is the thing the two have in common.
        """
        rect = self.boundingRect()
        if rect.height() <= 0:
            return 0.5
        down = (float(ev.pos().y()) - rect.y()) / rect.height()
        return down if self.yInverted() else 1.0 - down

    def wheelEvent(self, ev, axis=None) -> None:  # noqa: N802 - Qt/pyqtgraph name
        """Scroll scales both axes about the pointer, and comes back.

        pyqtgraph's own wheel does the first half.  The second half is what
        the comfort stop takes away: scrolling out, the value axis stops at
        the stop while the time axis keeps going, so the notches the value
        axis could not follow are still undone on the way back in.  Measured
        with the pointer parked in one place, four notches out and four back:
        the value axis left at 186 K to 242 K under a 289 K trace, off the top
        of a panel that had been fitted to it a moment earlier.  An empty
        screen from a gesture that ended where it began.

        So what the wheel scales is the *unclamped* value range, and the stop
        clamps only what is shown.  Scroll out past the stop and the axis sits
        at the stop; scroll back in and it retraces its own steps exactly,
        because the range being scaled never stopped moving.

        The factor is read off the **time** axis rather than recomputed from
        the event, because the time axis has no limits on it and therefore
        got what the gesture actually asked for -- pyqtgraph applies one
        factor to both.  That keeps the arithmetic of a wheel notch in
        pyqtgraph, where it belongs, instead of copied here to drift.

        The virtual range is dropped the moment the value axis is moved by
        anything else -- a drag, a Y button, an autoscale -- because then the
        axis is somewhere this gesture did not put it, and scaling from a
        remembered range would drag it back on the next notch.
        """
        if axis is not None:
            # Over an axis item, pyqtgraph scales that one axis, and there is
            # no second axis for the stop to desynchronise it from.
            self._y_virtual = self._y_shown = None
            super().wheelEvent(ev, axis=axis)
            return
        shown = tuple(self.viewRange()[1])
        if (self._y_virtual is None or self._y_shown is None
                or not _close(shown, self._y_shown)):
            self._y_virtual = shown
        # Where the pointer is *on the virtual range*, which is not where it
        # is on the clamped one -- an axis sitting at the stop shows a
        # different value under the same pixel.  Taking `mapToView` here
        # would zoom about that different value, and the axis would walk up
        # the screen a notch at a time instead of coming back.
        centre = _at_fraction(self._y_virtual, self._pointer_fraction(ev))
        was = _extent(self.viewRange()[0])
        super().wheelEvent(ev, axis=axis)
        factor = (_extent(self.viewRange()[0]) / was) if was else 1.0
        lo, hi = self._y_virtual
        lo, hi = centre + (lo - centre) * factor, centre + (hi - centre) * factor
        room = self.Y_OVERSCROLL_LIMIT * max(_extent(shown), 1e-12)
        if hi - lo > room and hi > lo:
            # Held out past any use: stop banking the excess, so scrolling
            # back in moves the axis on the first notch rather than the tenth.
            middle, half = (lo + hi) / 2.0, room / 2.0
            lo, hi = middle - half, middle + half
        self._y_virtual = (lo, hi)
        self.setYRange(lo, hi, padding=0)
        # What the stop actually allowed, so the next notch can tell this
        # gesture's own doing from somebody else's.
        self._y_shown = tuple(self.viewRange()[1])

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
        csv_path: str | None = None,
    ) -> None:
        super().__init__()
        self.source = StatusSource(status_path)
        #: A log to read instead of whatever ``status.json`` names.  This is
        #: how a finished run is opened -- an archived cooldown, or a legacy
        #: log put through `lschart.tools.xls_to_csv` -- with no recorder
        #: running at all.  The status half still polls and still reports
        #: itself absent, which is the honest banner for a file nobody is
        #: writing; the chart does not depend on it.
        self._csv_path = csv_path
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
        #: The span whose samples have been loaded from disk.  Until it agrees
        #: with ``_span`` the chart draws whatever the overview holds for that
        #: span, which past the backfill cap is nothing at all -- so the gap
        #: between the two is a gap the operator can see, and closing it fast
        #: is the whole job of ``_span_load`` below.  A wheel gesture crosses
        #: dozens of spans a second and none of them should each cost a scan.
        self._loaded_span: tuple[float, float] | None = None
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
        #: What the last redraw actually put on screen, for the status bar.
        self._drawn_points: int = 0
        self._drawn_spacing: float | None = None
        #: The loop every command in the panel is about, chosen by clicking a
        #: row of the loop table.  There is no second selector.
        self._loop: int = 1

        self.setWindowTitle("lschart — strip chart")
        self.resize(1280, 800)
        self._build()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(max(200, refresh_ms))

        #: Collapses a burst of view changes into one redraw.
        #:
        #: A wheel zoom emits a range change per notch and a drag emits one
        #: per mouse move, and redrawing inline made the window pay for every
        #: one of them -- the gesture finished long before the drawing caught
        #: up with it, which is what a zoom that lags behind the pointer
        #: actually is.  A zero-length single shot runs on the next turn of
        #: the event loop, so ten notches arriving together cost one redraw
        #: and the last one wins.
        self._redraw_pending = QtCore.QTimer(self)
        self._redraw_pending.setSingleShot(True)
        self._redraw_pending.setInterval(0)
        self._redraw_pending.timeout.connect(self._redraw)

        #: Waits for a view gesture to settle, then reads the span off disk.
        #:
        #: This used to ride the 1 s refresh timer, arming on one tick and
        #: loading on the next -- which cost up to two seconds before the
        #: read even started, and every one of them was a second spent
        #: looking at a window the overview could not fill.  Its own timer
        #: costs the same nothing between gestures and answers in a quarter
        #: of a second, and it also decouples the two: a long read no longer
        #: has to fit inside a refresh tick to keep the chart current.
        self._span_load = QtCore.QTimer(self)
        self._span_load.setSingleShot(True)
        self._span_load.setInterval(SPAN_SETTLE_MS)
        self._span_load.timeout.connect(self._load_span)

        #: Builds the coarse whole-archive backdrop, once the live view is up.
        #:
        #: Not on the startup path: opening the window needs none of it, and a
        #: second of reading before the first frame is a second of nothing.
        #: A moment afterwards is early enough -- the backdrop exists to
        #: answer a *gesture*, and nobody has made one yet.
        self._atlas_load = QtCore.QTimer(self)
        self._atlas_load.setSingleShot(True)
        self._atlas_load.setInterval(ATLAS_SETTLE_MS)
        self._atlas_load.timeout.connect(self._load_atlas)

        self.refresh()

    def _schedule_redraw(self) -> None:
        """Redraw once the current burst of view changes has finished."""
        self._redraw_pending.start()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        pg.setConfigOptions(antialias=True, background="w", foreground="k")

        central = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(central)

        self.banner = QtWidgets.QLabel("starting…")
        self.banner.setStyleSheet(theme.banner_style("absent", self))
        outer.addWidget(self.banner)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        # The panel scrolls rather than crushes.  Its content genuinely wants
        # more height than a laptop screen has -- the command box alone asks
        # for 700 px of the ~850 a 949 px screen leaves after the banner and
        # the status bar -- and a plain QVBoxLayout answers that by squeezing
        # children below their minimums, which is how the Setpoint, PID and
        # Heater range groups came to be three titles with no controls under
        # them.  A scroll area gives the panel its minimumSizeHint instead, so
        # nothing is ever drawn smaller than it can be read at.
        self._panel_scroll = QtWidgets.QScrollArea()
        self._panel_scroll.setWidgetResizable(True)
        self._panel_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._panel_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._panel_scroll.setWidget(self._left_panel())
        splitter.addWidget(self._panel_scroll)
        splitter.addWidget(self._plots())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # A starting point only. The real width is measured from the table once
        # it has rows in it -- see `_fit_panel_to_table`. This used to be 560
        # with the arithmetic written out beside it ("the eight fixed columns
        # want 426 px and 'Rad Shield' wants 86 more"), and both halves of that
        # sum are properties of a font rather than of the table.
        self._splitter = splitter
        self._panel_fitted = False
        splitter.setSizes([_MIN_PANEL_PX, 900])
        outer.addWidget(splitter, 1)
        # Across the whole window, under the chart. The plot gives up ~26 px
        # for it, which it does not miss, and the left panel gets three rows
        # back -- which it very much does.
        outer.addWidget(self._status_strip(), 0)

        self.setCentralWidget(central)
        self.statusBar().showMessage("waiting for the recorder…")
        # Settle the control panel before the first poll.  Otherwise a viewer
        # opened against a recorder with nothing writable shows every control,
        # greyed out -- which reads as "this cryostat has all of these" rather than
        # "this cryostat has none of them".
        self._instrument_changed()

    #: The command groups' own layout, whose top margin is what drops the
    #: instrument selector onto the first group's title line.  Set in
    #: `_command_box`; None until then, because `_place_instrument_selector`
    #: can be reached from a palette change before the panel is built.
    _group_stack = None
    _group_titles: dict = {}

    def _left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        box = QtWidgets.QVBoxLayout(panel)
        _tighten(box)

        # ONE table, not two.  There used to be a per-channel readouts table
        # with a loop table beneath it; on a 33x-only cryostat that is the same
        # four lines twice, because every channel is some loop's sensor.
        #
        # What the two tables were protecting against is still real: a
        # loop-centric table that *replaced* the channel list would turn an
        # eight-input 218 into however many loops it has, and recording every
        # thermometer continuously is the recorder's job.  So the row is the
        # channel and the loop is a set of columns on it -- see `reading_rows`,
        # which does the join and is where the rules live.
        self.readings = QtWidgets.QTableWidget(0, len(READING_COLUMNS))
        self.readings.setHorizontalHeaderLabels(READING_COLUMNS)
        self.readings.verticalHeader().setVisible(False)
        self.readings.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.readings.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.readings.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        header = self.readings.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        # Every column sized to its contents except the channel name, which
        # takes what is left and elides when there is not enough.  Something
        # has to give and it should be the name: it is the one column repeated
        # in the trace list and in this row's own tooltip, while a mark
        # scrolled off the right-hand edge is not anywhere.
        header.setSectionResizeMode(COL_CHANNEL, QtWidgets.QHeaderView.Stretch)
        self.readings.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.readings.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.readings.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        # Live values are what somebody walks over to read from across the
        # room, which is why the old readouts table ran three points up. The
        # merged table inherits that, and the space the shortened notes and
        # the dropped duplicate sentence gave back is what pays for it.
        font = self.readings.font()
        font.setPointSize(font.pointSize() + 3)
        self.readings.setFont(font)
        self.readings.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                    QtWidgets.QSizePolicy.Fixed)
        self.readings.itemSelectionChanged.connect(self._loop_row_selected)
        self.readings.setToolTip(
            "Every thermometer the recorder reads, with the control loop bound "
            "to it where there is one (from the instrument's own OUTMODE?). "
            "Click a row with a loop to point the command panel at it. A "
            "software loop is read rather than clicked — it takes Arm and the "
            "panic Hold, not a setpoint, a range or gains.")
        #: Row index -> (instrument name, joined row), so a click can say which
        #: loop was picked without parsing the cells back out again.
        self._loop_index: list[tuple[str, dict]] = []
        box.addWidget(self.readings, 0)

        # Three rows of labelled buttons became two dense ones.  These are
        # small, frequently-hit controls and they were spending three rows of a
        # panel that has none to spare; compact buttons and a shared row lose
        # nothing but padding.
        view_row = QtWidgets.QHBoxLayout()
        view_row.setSpacing(3)
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
            _compact(button)
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
        zoom_row.setSpacing(3)
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
            _compact(button, width=34)
            button.clicked.connect(
                lambda _checked=False, z=zoom, f=factor: z(f))
            zoom_row.addWidget(button, 0)
            self.zoom_buttons[label] = button
        # The cursor pair shares the zoom row rather than taking a third: they
        # answer different questions -- zoom chooses what is on screen, cursors
        # measure a piece of it -- but a separator says that as well as a whole
        # row of empty panel did.
        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.VLine)
        separator.setFrameShadow(QtWidgets.QFrame.Sunken)
        zoom_row.addWidget(separator)

        cursor_row = zoom_row
        self.cursor_button = QtWidgets.QPushButton("Cursors")
        self.cursor_button.setCheckable(True)
        self.cursor_button.setToolTip(
            "Two vertical cursors, and the mean, spread and change of every "
            "trace between them.\n"
            "Left-click or drag on a panel moves the nearer one. While they "
            "are up the left button places cursors instead of drawing a zoom "
            "rectangle; the wheel, Shift-drag and the X/Y buttons still zoom.")
        self.cursor_button.clicked.connect(self._toggle_cursors)
        # These two take the horizontal slack rather than leaving it empty at
        # the right-hand end. The zoom steppers keep their fixed width -- they
        # are two-character controls, and stretching them would make four big
        # buttons out of four small ones for no gain at all.
        self.cursor_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                         QtWidgets.QSizePolicy.Preferred)
        self.cursor_button.setMinimumHeight(26)
        cursor_row.addWidget(self.cursor_button, 1)

        self.export_button = QtWidgets.QPushButton("Export region…")
        self.export_button.setEnabled(False)
        self.export_button.setToolTip(
            "Write the samples between the cursors to a CSV, at full "
            "resolution — not the thinned overview the chart draws.")
        self.export_button.clicked.connect(self._export_region)
        self.export_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                         QtWidgets.QSizePolicy.Preferred)
        self.export_button.setMinimumHeight(26)
        cursor_row.addWidget(self.export_button, 2)
        box.addLayout(zoom_row)

        self.export_note = QtWidgets.QLabel("")
        self.export_note.setWordWrap(True)
        self.export_note.setStyleSheet(theme.note_style("muted", self))
        self.export_note.setVisible(False)
        box.addWidget(self.export_note)

        traces = QtWidgets.QGroupBox("Traces")
        self.traces_layout = QtWidgets.QVBoxLayout(traces)
        self.traces_layout.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(traces)
        # Two rows, not ten.  This is both the one thing that absorbs spare
        # height and the first thing to give it back, which is the right way
        # round: a trace list has its own scrollbar and loses nothing by being
        # short, while every other control in this panel either disappears or
        # becomes unreadable when squeezed.  On a tall window it still takes
        # all the slack -- a cryostat with two instruments has a dozen traces
        # and hunting for one through a three-line window is the difference
        # between a usable viewer and a tolerated one.
        scroll.setMinimumHeight(72)
        self.traces_scroll = scroll
        box.addWidget(scroll, 1)

        box.addWidget(self._command_box())
        # OUTSIDE the command group, and that is structural rather than
        # cosmetic. The panic kinds are exempt from the source policy at the
        # recorder, so when that policy switches the panel off these must stay
        # live -- and a Qt child of a disabled parent is disabled however
        # firmly you enable it.
        # The panic menu, the source policy and the link health used to live
        # here, stacked. They are now a horizontal strip spanning the whole
        # window under the chart -- see `_status_strip`. Three short controls
        # in a narrow column is the worst shape for them, and this panel is the
        # thing that is short of height.
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
        _tighten(box)
        # No top margin: the selector row is the first thing in this group and
        # should sit under the "Command" title, not a band below it. What is
        # left above it is the style's own title height, which is not ours to
        # give away.
        margins = box.contentsMargins()
        box.setContentsMargins(margins.left(), 0, margins.right(),
                               margins.bottom())

        # The instrument selector shares a line with the first group's title:
        # the title text on the left, "Instrument [combo]" on the right, and
        # the group's frame directly beneath with nothing between them.
        #
        # It works by taking the title *off* the first visible group and
        # drawing it here instead. A QGroupBox with no title has no title band,
        # so its frame starts at its widget top and the row above it sits flush
        # on the border -- which is the whole point. Every other way of doing
        # this fights the layout: a top margin is compressible (asked for 40 px
        # it returned 25), a spacer gets squeezed the same way, and overlaying
        # the selector on the group draws it straight through the frame.
        #
        # `_place_instrument_selector` is what moves the title, because which
        # group is first depends on the box: a 218 has no loops, so it shows
        # the analog group where a 33x shows Setpoint.
        selector = QtWidgets.QHBoxLayout()
        selector.setContentsMargins(8, 0, 0, 2)
        selector.setSpacing(6)
        self.group_title = QtWidgets.QLabel("")
        selector.addWidget(self.group_title)
        selector.addStretch(1)
        selector.addWidget(QtWidgets.QLabel("Instrument"))
        self.instrument_combo = QtWidgets.QComboBox()
        self.instrument_combo.currentIndexChanged.connect(self._instrument_changed)
        selector.addWidget(self.instrument_combo)

        groups = QtWidgets.QWidget()
        stack = QtWidgets.QVBoxLayout(groups)
        stack.setSpacing(4)
        stack.setContentsMargins(0, 0, 0, 0)
        # Any slack goes to the BOTTOM. Without this it lands above the first
        # visible group instead -- on a 218, where the three groups before the
        # analog one are hidden, that put 15 px of nothing between the
        # selector and the box it is supposed to be sitting on.
        stack.addStretch(0)
        self._group_stack = stack

        # What the selected loop is bound to, in a sentence.  From the
        # recorder's OUTMODE reading, so it is the instrument's answer and not
        # a map kept in here that could go stale.
        # No standing "loop N reads X" line: it said what the Loop row of the
        # Setpoint group says, one group further down the panel. What is left
        # here is the handful of cases that genuinely have something else to
        # say -- a schema-1 recorder, a read-only box -- and it takes no height
        # when it is empty.
        self.loop_note = QtWidgets.QLabel("")
        self.loop_note.setWordWrap(True)
        self.loop_note.setStyleSheet(theme.note_style("muted", self))
        self.loop_note.setVisible(False)
        box.addWidget(self.loop_note)

        stack.addWidget(self._setpoint_group())
        stack.addWidget(self._pid_group())
        stack.addWidget(self._range_group())
        stack.addWidget(self._analog_group())

        head = QtWidgets.QWidget()
        joined = QtWidgets.QVBoxLayout(head)
        joined.setContentsMargins(0, 0, 0, 0)
        joined.setSpacing(0)
        joined.addLayout(selector)
        joined.addWidget(groups)
        box.addWidget(head)
        #: Each group's own title, so the one lent to `group_title` can be
        #: given back when a different group becomes the first visible one.
        #: Each group's *intended* title -- the one it shows when it is not
        #: the first visible group, which is the one lending its title line to
        #: the selector. Two of them change at runtime, so this is the source
        #: of truth and the widget follows it.
        self._group_titles = {g: g.title() for g in (
            self.setpoint_group, self.pid_group,
            self.range_group, self.analog_group)}

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

        # Beside Arm and not in the panic menu, for the same reason Arm is not:
        # this is the first of the two steps back to driving the heater, and it
        # is gated exactly as Arm is. Named for what it does rather than for
        # the command, because "Ack" beside an acknowledgement label would be
        # two unrelated meanings of the word in one panel.
        self.clear_lockout_button = QtWidgets.QPushButton("Clear lockout…")
        self.clear_lockout_button.setToolTip(
            "Clear a software loop's fault lockout after a ramp-down. This "
            "does NOT resume the loop — it stays disarmed until you arm it.")
        self.clear_lockout_button.clicked.connect(self._send_clear_lockout)
        box.addWidget(self.clear_lockout_button)

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
        self.panic_button = QtWidgets.QPushButton("Panic Menu")
        self.panic_button.setToolTip(
            "Two ways to stop. Both bypass the per-client source policy and "
            "the two power gates. Neither bypasses a read-only instrument, "
            "which is left alone and named in the reply.")
        self.panic_button.setStyleSheet(theme.panic_style())
        # Red, and roughly twice the width it had. This is the control somebody
        # reaches for while something is going wrong on a cryostat; it should
        # be findable without reading, and the colour is the same on both
        # themes because "the button that stops it" is not a thing that should
        # look different on a light desktop.
        self.panic_button.setMinimumWidth(150)
        self.panic_button.setMinimumHeight(30)
        self.panic_button.clicked.connect(self._open_panic_menu)

        # The two actions stay QActions even though the menu is gone: they are
        # the single place each behaviour lives, the dialog's buttons trigger
        # them, and anything else that wants to reach one -- a test, a future
        # shortcut -- has a handle that is not a dialog button.
        self.off_action = QtGui.QAction("All heaters OFF…", self)
        self.off_action.triggered.connect(self._send_heaters_off)
        self.hold_action = QtGui.QAction("All temperatures HOLD…", self)
        self.hold_action.triggered.connect(self._send_hold)
        return self.panic_button

    def _open_panic_menu(self) -> None:
        """The panic actions, as a modal rather than a dropdown.

        Still three interactions -- open, choose, confirm -- which was the
        point of the menu and stays the point here: these are needed almost
        never and must not be reachable by accident.

        A dialog rather than a popup because a popup is a small target next to
        the pointer, and the two things in it are "stop heating this cryostat"
        and "freeze it where it is". Large, separated buttons make the choice
        deliberate and make the wrong one hard to hit by a few pixels.
        """
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Panic")
        dialog.setModal(True)
        box = QtWidgets.QVBoxLayout(dialog)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(18)

        blurb = QtWidgets.QLabel(
            "Both of these bypass the per-client source policy and the two "
            "power gates.\n\nNeither bypasses a read-only instrument: those "
            "are left alone and named in the reply.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(theme.note_style("muted", self))
        box.addWidget(blurb)

        chosen: list[QtGui.QAction] = []
        for action, blurb_text in (
            (self.off_action,
             "33x heater ranges to 0 and 218 analog outputs to 0%. "
             "Setpoints are not changed."),
            (self.hold_action,
             "Every loop holds the temperature it is at now. Ramping is "
             "switched off first, and left off."),
        ):
            button = QtWidgets.QPushButton(action.text())
            button.setStyleSheet(theme.panic_style())
            button.setMinimumHeight(52)
            font = button.font()
            font.setPointSize(font.pointSize() + 3)
            button.setFont(font)
            button.clicked.connect(
                lambda _checked=False, a=action, d=dialog: (
                    chosen.append(a), d.accept()))
            box.addWidget(button)
            caption = QtWidgets.QLabel(blurb_text)
            caption.setWordWrap(True)
            caption.setStyleSheet(theme.note_style("muted", self))
            box.addWidget(caption)
            box.addSpacing(6)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        dialog.setMinimumWidth(420)

        #: The live dialog, so a test can drive it without a nested event loop.
        self._panic_dialog = dialog
        dialog.exec()
        self._panic_dialog = None
        # Fired after the dialog closes, so the action's own confirmation is
        # not a second modal stacked on this one.
        for action in chosen:
            action.trigger()

    def _status_strip(self) -> QtWidgets.QWidget:
        """Panic, the source policy and link health, across the window.

        These three were a vertical stack at the bottom of the left panel,
        which is the column that has no height to spare -- and all three are
        short and wide by nature. Spanning them under the chart costs a little
        plot height and gives the panel back three rows.
        """
        strip = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(strip)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(10)
        row.addWidget(self._panic_box())
        row.addWidget(self._source_box())
        row.addStretch(1)
        self.links_label = QtWidgets.QLabel("")
        row.addWidget(self.links_label, 0)
        return strip

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
        box = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(QtWidgets.QLabel("Listen to:"))
        self.source_checks: dict[str, QtWidgets.QCheckBox] = {}
        for name, label in SOURCE_CHOICES:
            check = QtWidgets.QCheckBox(label)
            check.setChecked(True)
            check.toggled.connect(
                lambda checked, n=name: self._source_toggled(n, checked))
            self.source_checks[name] = check
            row.addWidget(check)
        #: The one this viewer is, kept for the places that ask about itself.
        self.source_check = self.source_checks[GUI_SOURCE]
        return box

    def _source_toggled(self, name: str, checked: bool) -> None:
        """Queue the mute or the un-mute.  Only ever a human's click: the
        periodic fill in :meth:`_sync_source_box` goes through ``_quiet``."""
        if self.spool is None:
            return
        label = dict(SOURCE_CHOICES).get(name, name)
        mine = name == GUI_SOURCE
        if not checked and not self._confirm(
            f"Ignore {label}",
            f"Tell the recorder to ignore commands from {label}?\n\n"
            + ("The chart, the readouts and the loop table carry on exactly as "
               "they are — this is only about commands, and reading is not a "
               "command.\n\n" if mine else
               "This only stops the recorder *listening* to that client. "
               "Anything reading status.json — its chart, its readouts — "
               "carries on exactly as before.\n\n")
            + ("You can undo it from this same strip: the command that sets "
               "this is exempt from the policy it sets, so muting is not a "
               "one-way door. The Panic menu also keeps working throughout."),
        ):
            with _quiet(self.source_checks[name]):
                self.source_checks[name].setChecked(True)
            return
        self._queue("source", instrument="", name=name, allowed=bool(checked))
        self._awaiting = None

    def _sync_source_box(self) -> None:
        """Reflect the recorder's answer, without the reflection sending one."""
        live = bool(self.spool) and self.source.accepts_commands()
        for name, label in SOURCE_CHOICES:
            check = self.source_checks[name]
            allowed = self.source.source_allowed(name)
            permitted = self.source.source_configured(name)
            with _quiet(check):
                check.setChecked(allowed)
            # A source the *config* refuses cannot be un-muted from here at any
            # price: the overlay may only narrow. Offering the click would be
            # offering a refusal.
            check.setEnabled(live and permitted)
            if not permitted:
                check.setToolTip(
                    f"This recorder's config (ipc.sources) refuses {label} "
                    "outright. The runtime overlay may only narrow that, so "
                    "enabling it needs a config edit and a restart.")
            elif allowed:
                check.setToolTip(
                    f"Untick to have the recorder ignore commands from {label}. "
                    "Reading carries on either way, and you can tick it again.")
            else:
                check.setToolTip(
                    f"The recorder is ignoring commands from {label}. Tick to "
                    "have it listen again — no restart needed.")

    def _setpoint_group(self) -> QtWidgets.QWidget:
        self.setpoint_group = QtWidgets.QGroupBox("Setpoint")
        form = QtWidgets.QFormLayout(self.setpoint_group)
        _tighten(form)

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
        _tighten(form)

        # P, I and D on ONE row, not three.  They are one command on the
        # instrument and they are sent together whatever happens, so stacking
        # them spent two rows of a panel that has none to spare in order to
        # separate three things that never travel apart.
        self.pid_spins = {}
        gains = QtWidgets.QHBoxLayout()
        gains.setContentsMargins(0, 0, 0, 0)
        gains.setSpacing(4)
        for key, label, decimals in (
            ("p", "P", 1), ("i", "I", 1), ("d", "D", 1),
        ):
            spin = QtWidgets.QDoubleSpinBox()
            # The instrument's own ranges: 0.1..1000 for P and I, 0..200 for D
            # on this family.  Bounded here so the widget cannot express a
            # value the box will refuse.
            spin.setRange(0.0 if key == "d" else 0.1, 1000.0 if key != "d" else 200.0)
            spin.setDecimals(decimals)
            spin.setMinimumWidth(64)
            spin.valueChanged.connect(self._pid_edited)
            self.pid_spins[key] = spin
            gains.addWidget(QtWidgets.QLabel(label))
            gains.addWidget(spin, 1)
        # One flag for the three of them, because they are one command.
        self._pid_dirty = False
        row = QtWidgets.QWidget()
        row.setLayout(gains)
        form.addRow(row)

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
        _tighten(form)

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
        _tighten(form)

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
                                fill=pg.mkBrush(255, 255, 255, 248),
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
            self._update_readings()
            self._update_gate_notes()
            self._place_instrument_selector()
        except Exception:  # pragma: no cover - cosmetic, never fatal
            log.debug("could not re-apply the theme", exc_info=True)

    def refresh(self) -> None:
        """One poll of both files.  Must never raise: it is on a timer."""
        try:
            self.source.poll()
            self._update_banner()
            self._update_readings()
            self._update_links()
            self._update_commands()
            self._sync_command_values()
            if self.tail.follow(self._csv_path or self.source.log_path()):
                self._first_load_done = False
                # A rollover is a day of new archive, and the backdrop's point
                # budget was spread across the archive as it stood.  Rebuild
                # it; a day apart is rare enough to cost nothing.
                self._atlas_load.start()
            # What the newest sample was before this poll, so a picked window
            # can tell whether the rows that just arrived are inside it.
            was_newest = self.tail.newest()
            if self.tail.poll() or not self._first_load_done:
                first = not self._first_load_done
                self._first_load_done = True
                if first and not self._atlas_load.isActive():
                    # There are samples on the chart now, so the window is
                    # worth looking at; the backdrop follows a moment later.
                    self._atlas_load.start()
                if first and self._csv_path is not None:
                    # An archived log has no "live" edge to follow.  The
                    # default window is the last N seconds *of now*, and a run
                    # that finished a fortnight ago falls entirely outside it
                    # -- which draws an empty chart over a file that loaded
                    # perfectly.  Open on the data's own extent instead.
                    self._span_to_all()
                self._sync_traces()
                # A hand-picked window is a question about the past, and rows
                # appended beyond its right edge cannot change the answer.
                # Redrawing anyway meant a hundred thousand points per trace
                # re-sliced, re-scanned for gaps and re-stroked every second
                # to put back exactly the picture already on screen -- which
                # is most of what made a picked window feel heavy to drag.
                if (self._span is None or was_newest is None
                        or self._span[1] >= was_newest):
                    self._redraw()
            if self._span is not None and self._span != self._loaded_span:
                # Normally `_span_changed` has already started this; here for
                # a span that arrived by some other route, and as the retry
                # after a load that could not be completed.
                if not self._span_load.isActive():
                    self._span_load.start()
            self._update_region_stats()
            self._update_statusbar()
        except Exception:  # noqa: BLE001 - a drawing bug must not stop the viewer
            log.exception("refresh failed; the viewer continues")

    def _load_atlas(self) -> None:
        """Read the coarse backdrop -- every log on disk, heavily thinned.

        The floor under the drawing.  A scroll out crosses a window a second
        and none of them is read until the last one settles, so without
        something already loaded and already covering the archive the screen
        is empty for the length of the gesture -- and the faster the gesture,
        the wider the window it ends on and the less of it anything else
        reaches.

        On the GUI thread and about a second on 55 MB, which is why it is on
        its own timer and happens once.  Like every other timer here it must
        never raise: a viewer without a backdrop draws less, and one that
        stopped drawing draws nothing.
        """
        try:
            self.tail.prepare_atlas()
        except Exception:  # noqa: BLE001 - the backdrop is a nicety, not a duty
            log.exception("could not read the backdrop; the viewer continues")
            return
        self._schedule_redraw()

    def _load_span(self) -> None:
        """The picked window has settled: read it off the logs and draw it.

        Runs on the GUI thread, and takes about a second on a week of this
        recorder's logs, which is why nothing gets here until the gesture has
        stopped moving.  It must never raise: it is on a timer, and a viewer
        that stops drawing because one span could not be read is worse than
        one showing a coarse picture of it.

        Often there is nothing to do.  ``prepare_span`` reads a quarter of a
        screen either side of the window it is given, so a pan or a zoom that
        stays inside that has its samples in hand already -- and re-reading
        them would spend a second of this thread producing the picture that
        is on screen.  ``overlay_serves`` is the tail's own answer to whether
        that is the case; it says no while a fresh read would be a better
        one, which is what keeps zooming in on a coarse span honest.
        """
        if self._span is None or self._span == self._loaded_span:
            return
        span = self._span
        if self.tail.overlay_serves(*span):
            # Already drawn from the overlay by the redraw the gesture
            # scheduled.  All that is left is to stop calling it unloaded,
            # so the status bar stops saying the log is being read.
            self._loaded_span = span
            self._update_statusbar()
            return
        try:
            self.tail.prepare_span(*span)
        except Exception:  # noqa: BLE001 - a bad span must not stop the viewer
            log.exception("could not read the span; leaving what is drawn")
            return
        # Against `span` and not `self._span`: a gesture during the read
        # moved the window, and claiming the new one is loaded would leave
        # the old one's samples on screen with nothing to correct them.
        self._loaded_span = span
        self._redraw()
        self._update_statusbar()

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

    def _update_readings(self) -> None:
        """Fill the one table: every thermometer, with its loop where it has one.

        The join is in `reading_rows`, which has no Qt in it and is where the
        rules about not losing a thermometer are written down and tested. This
        method is only the painting.
        """
        entries: list[tuple[str, dict]] = []
        for row in reading_rows(self.source.channels(), self.source.links(),
                               self.source.control()):
            entries.append((str(row.get("instrument") or ""), row))
        self._loop_index = entries

        self.readings.setVisible(bool(entries))
        grew = self.readings.rowCount() != len(entries)
        if grew:
            self.readings.setRowCount(len(entries))

        selected = -1
        for index, (instrument, row) in enumerate(entries):
            self._fill_reading_row(index, row)
            if (row.get("has_loop") and instrument
                    and instrument == self.instrument_combo.currentText()
                    and int(row.get("loop") or 0) == self._loop):
                selected = index

        # Every refresh, not only the ones that add rows: the fit needs a laid
        # out window, and the first refresh can land before the window is shown.
        # It latches itself, so this costs one comparison thereafter.
        fitted = self._fit_panel_to_table()

        if grew or fitted:
            # Sized *after* the cells are filled.  Measuring an empty table
            # measures the row height of a row with nothing in it, which is
            # how the last row came to sit behind a scrollbar.  `fitted` counts
            # because changing the font changes every row height.
            self.readings.resizeRowsToContents()
            height = (self.readings.horizontalHeader().height()
                      + 2 * self.readings.frameWidth())
            for r in range(self.readings.rowCount()):
                height += self.readings.rowHeight(r)
            self.readings.setFixedHeight(height)

        if selected >= 0 and not self.readings.selectionModel().isRowSelected(
                selected, QtCore.QModelIndex()):
            with _quiet(self.readings):
                self.readings.selectRow(selected)

    def _reading_table_wants(self) -> int:
        """Pixels the reading table needs to draw every column in full.

        Per column, the larger of what the *contents* want and what the
        *heading* wants -- which is what ``ResizeToContents`` itself uses, and
        the two differ sharply here: "Off SP" holds a single mark or nothing,
        so its contents want 7 px and its heading wants 100.
        """
        header = self.readings.horizontalHeader()
        want = [
            max(self.readings.sizeHintForColumn(c), header.sectionSizeHint(c))
            for c in range(self.readings.columnCount())
        ]
        # The channel column asks for an ordinary name, never for whatever the
        # longest label happens to be.  A 40-character sensor name must elide
        # rather than push the marks off the edge or summon a scrollbar --
        # sizing the whole panel to it would let one label rearrange the window.
        cap = (self.readings.fontMetrics().averageCharWidth()
               * _CHANNEL_FIT_CHARS) + 2 * _CELL_PADDING_PX
        want[COL_CHANNEL] = min(want[COL_CHANNEL], cap)
        return sum(want) + 2 * self.readings.frameWidth()

    def _fit_panel_to_table(self) -> bool:
        """Give the panel the width the reading table actually needs.

        It used to start at a written-down 560 px, with the arithmetic beside
        it: "the eight fixed columns want 426 px between them and 'Rad Shield'
        wants 86 more".  **That arithmetic is correct** for Segoe UI 9 pt, this
        desktop's font, and on this machine the panel is right and tight: the
        columns come to 542 in a 542 px viewport and nothing is clipped.  This
        method changes nothing here -- it measures 465, below the 560 floor,
        and leaves the panel exactly where it was.

        What it buys is the other desktops.  Both halves of that sum are
        properties of a *font*, and this program ships to machines whose font
        is not this one; the table sets ``ScrollBarAlwaysOff``, so on a desktop
        that needs more the surplus is not scrolled to, it is **not drawn**.
        Rather than a constant that happens to suit one machine, the panel asks
        the table.

        Do not diagnose this from the test suite.  ``QT_QPA_PLATFORM=offscreen``
        resolves no font at all, so every width there is roughly doubled and
        the same columns "want" 928 px -- which is how a healthy viewer came to
        look like a clipping regression.  Re-measure with
        ``QT_QPA_PLATFORM=windows`` before believing any pixel number.

        So measure rather than remember.  Once, on the first refresh that puts
        rows in the table -- before an operator can have dragged the splitter,
        and never taking so much that the chart drops under ``_MIN_CHART_PX``.

        If even that is not enough, on a genuinely small screen, the **font**
        gives way rather than the columns: a smaller number is still a number,
        and a column that is not drawn is not anywhere.  It never shrinks below
        the desktop's own font, which is the size everything else is read at.

        Returns True on the one call that does the fitting, because changing
        the font changes the row heights the caller has just measured.
        """
        if self._panel_fitted:
            return False
        total = self._splitter.width()
        # Wait for a real layout.  Before the window is shown the splitter
        # reports a nominal width, and fitting to *that* squeezed the channel
        # column to 36 px and shrank the font to its floor -- solving a problem
        # the window did not have yet, and keeping the solution afterwards.
        #
        # Visibility is the whole test.  A width threshold here would also skip
        # genuinely small windows, which are exactly the ones that need this.
        if not self.isVisible() or total <= 0:
            return False
        self._panel_fitted = True

        chrome = max(0, self._panel_scroll.width()
                     - self.readings.viewport().width())
        target = max(_MIN_PANEL_PX,
                     min(self._reading_table_wants() + chrome,
                         total - _MIN_CHART_PX))
        self._splitter.setSizes([target, max(_MIN_CHART_PX, total - target)])

        available = target - chrome
        font = self.readings.font()
        floor = QtWidgets.QApplication.font().pointSize()
        while (font.pointSize() > floor
               and self._reading_table_wants() > available):
            font.setPointSize(font.pointSize() - 1)
            self.readings.setFont(font)

        if self._reading_table_wants() > available:
            # Last resort, on a window too small for nine columns at any font
            # this program is willing to read.  Scrolling is a poor answer and
            # the panel is built never to need one -- but it is a far better
            # answer than drawing eight columns and silently dropping the
            # ninth, which is what `ScrollBarAlwaysOff` does once the
            # arithmetic runs out.  Nothing here is allowed to be *quietly*
            # missing.
            #
            # The channel column stops stretching with it: stretched into a
            # space that is already short it collapses to the minimum section
            # size, and "Stage 1" and "Stage 2" both read "S…" -- two different
            # thermometers showing the same name, which is the failure this
            # table's own comments call worse than useless.
            self.readings.horizontalHeader().setSectionResizeMode(
                COL_CHANNEL, QtWidgets.QHeaderView.ResizeToContents)
            self.readings.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarAsNeeded)
        return True

    def _fill_reading_row(self, index: int, row: dict) -> None:
        """One row: a thermometer, and the loop bound to it if there is one.

        Three shapes share this. A channel with no loop fills the first two
        columns and leaves the rest blank -- that is the eight-input 218 case,
        and it is the whole reason this is one table rather than a loop table
        that quietly drops thermometers. A channel with an instrument loop
        fills everything and is **selectable**, which is how the command panel
        is pointed. A software loop fills everything but is **not** selectable:
        it takes no setpoint, range or PID command, only `arm` and the panic
        `hold`, so a row that could be clicked into a selection the panel
        cannot honour would be a row that lies.
        """
        has_loop = bool(row.get("has_loop"))
        instrument = str(row.get("instrument") or "")
        software = has_loop and not instrument
        kelvin = row.get("kelvin") if row.get("usable") else None
        marks = (loop_marks(row, kelvin, rails=row.get("rails")) if has_loop
                 else {"trying": False, "saturated": False, "unsettled": False})
        heater = row.get("heater_output")

        name = str(row.get("channel") or "—")
        if has_loop and row.get("kelvin") is not None and not row.get("usable"):
            # Never a bare number for a rejected sample: the point of the
            # validity flag is that this reading is not a measurement.
            name = name
        cells = [""] * len(READING_COLUMNS)
        cells[COL_CHANNEL] = name
        raw_k = row.get("kelvin")
        if raw_k is None:
            cells[COL_KELVIN] = "—"
        elif row.get("usable"):
            cells[COL_KELVIN] = f"{float(raw_k):.3f}"
        else:
            cells[COL_KELVIN] = (f"{float(raw_k):.3f} "
                                 f"({row.get('validity') or 'rejected'})")
        if has_loop:
            cells[COL_LOOP] = str(row.get("loop") or "")
            cells[COL_SETPOINT] = self._maybe(row.get("setpoint_k"), "{:.3f}")
            cells[COL_OUTPUT] = self._maybe(row.get("output_pct"), "{:.1f}")
            # n/a and not "—": a loop whose output is analog-only does not have
            # a range that happens to be unknown, it has none at all. The
            # software loop is the same case for a stronger reason -- the 218
            # has no inert half, so there is no range for it to have.
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
        bad_reading = raw_k is not None and not row.get("usable")
        for column, text in enumerate(cells):
            item = self.readings.item(index, column)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.readings.setItem(index, column, item)
            item.setText(text)
            lit = column in (COL_SATURATED, COL_UNSETTLED) and text
            reading_cell = column in (COL_CHANNEL, COL_KELVIN)
            if lit or unhealthy or (bad_reading and reading_cell):
                item.setForeground(QtGui.QBrush(QtGui.QColor(warn_colour(self))))
            else:
                theme.clear_foreground(item)
            flags = item.flags()
            selectable = has_loop and not software
            item.setFlags(flags | QtCore.Qt.ItemIsSelectable if selectable
                          else flags & ~QtCore.Qt.ItemIsSelectable)

        if not has_loop:
            self.readings.item(index, COL_CHANNEL).setToolTip(
                "recorded, but no control loop reads it on this recorder")
            return
        self.readings.item(index, COL_CHANNEL).setToolTip(
            self._software_tooltip(row) if software else
            f"{instrument} loop {row.get('loop')}: "
            f"{row.get('mode') or 'mode unknown'}"
            + ("" if marks["trying"] else
               " — not trying to reach a setpoint, so neither warning "
               "applies"))
        self.readings.item(index, COL_STATE).setToolTip(
            self._software_tooltip(row) if software else
            f"what OUTMODE? says loop {row.get('loop')} is doing: "
            f"{row.get('mode') or 'unknown'}")
        rails = row.get("rails") if software else None
        low, high = ((rails[0], rails[1]) if rails and rails[0] is not None
                     else (SATURATED_LOW_PCT, SATURATED_HIGH_PCT))
        self.readings.item(index, COL_SATURATED).setToolTip(
            f"the output is at a rail (at or beyond {float(high):g}% or "
            f"{float(low):g}%): this loop has no authority left in the "
            "direction it is asking for"
            if marks["saturated"] else "")
        self.readings.item(index, COL_UNSETTLED).setToolTip(
            f"{row.get('channel') or 'the sensor'} is further than "
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
        # An empty note takes no height at all. A word-wrapped QLabel still
        # claims a line when it has nothing in it, and a line of nothing above
        # the trace list is exactly the empty vertical space this panel cannot
        # afford.
        label.setVisible(bool(text))
        if style:
            label.setStyleSheet(style)
        policy = label.sizePolicy()
        policy.setHeightForWidth(True)
        label.setSizePolicy(policy)
        width = label.width()
        if not text:
            # A cleared note is hidden, so it claims no height anyway -- but it
            # would keep whatever minimum the last text left on it, and asking
            # an EMPTY label how tall it is at a width answers -1, which is not
            # a size.  Qt refuses it and says so, once per refresh, for as long
            # as the gate it describes stays open.
            label.setMinimumHeight(0)
        elif width > 0:
            height = label.heightForWidth(width)
            # -1 means "no height depends on width here" -- an unwrapped label,
            # say.  It is an answer, not a measurement, and not a minimum.
            if height >= 0:
                label.setMinimumHeight(height)

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
            self._set_group_title(
                self.analog_group,
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

    def _place_instrument_selector(self) -> None:
        """Lend the first visible group's title to the selector's row.

        A QGroupBox draws its title *above* its frame, so a row placed against
        the group's widget top leaves the whole title band -- 18 px on this
        style -- visibly empty between the selector and the box. Taking the
        title off that group removes the band entirely: its frame then starts
        at its widget top, and the row above sits directly on the border.

        Every group keeps its own title while it is not first, which is why
        the originals are held in `_group_titles` rather than recomputed.
        """
        if not self._group_titles:
            return
        first = next((g for g in self._group_titles if not g.isHidden()), None)
        for group, title in self._group_titles.items():
            group.setTitle("" if group is first else title)
        self.group_title.setText(
            self._group_titles.get(first, "") if first is not None else "")
        # Blanking a title changes the group's size hint, and hiding the
        # groups above it leaves the stack holding stale positions -- on a 218
        # the analog group sat 15 px down inside a stack whose own geometry
        # was still 0x0. Nothing schedules that re-layout for us here.
        if self._group_stack is not None:
            self._group_stack.invalidate()
            self._group_stack.activate()

    def _set_group_title(self, group, title: str) -> None:
        """Set the title a group *should* show.

        Two of these are dynamic -- "Heater range (output 2)", "Analog output
        1 (max 70%)" -- and the group that is currently first is showing a
        blank one on the selector's behalf. So the intended title is stored
        here and applied by `_place_instrument_selector`; writing it straight
        onto the widget would either clobber the blank or be clobbered by the
        next re-place, depending on the order the two happened to run in.
        """
        self._group_titles[group] = title
        self._place_instrument_selector()

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
        self._set_group_title(
            self.range_group,
            "Heater range" if heater is None else f"Heater range (output {heater})")
        self.heater_label.setText("—" if heater is None else str(heater))

        # The analog grouping belongs to a box that will accept an `analog`
        # command.  A 336 loop 3 has an analog output and no way to command it
        # from here, which is a sentence to say rather than a control to offer.
        self.analog_group.setVisible(
            caps["has_analog"] and (not caps["has_loops"] or heater is None))

        # The loop AND the sensor it reads, on the row that was already there.
        row = self._selected_loop_row()
        if not caps["has_loops"]:
            self.loop_label.setText("—")
        else:
            sensor = str(row.get("sensor") or "") if row else ""
            self.loop_label.setText(
                f"{self._loop} → {sensor}" if sensor else str(self._loop))
            self.loop_label.setToolTip(str(row.get("mode") or "") if row else "")
        if caps["has_loops"] and not row:
            note = ("this recorder does not publish loop bindings (schema 1); "
                    "the sensor and mode are unknown")
        elif caps["has_loops"] and heater is None:
            note = "drives an analog output, which this recorder cannot command"
        else:
            note = ""
        self._note(self.loop_note, note, theme.note_style("muted", self))
        # Which group is first can have just changed, and the selector rides
        # on it.
        self._place_instrument_selector()

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
        rows = self.readings.selectionModel().selectedRows()
        if not rows:
            return
        index = rows[0].row()
        if not 0 <= index < len(self._loop_index):
            return
        instrument, row = self._loop_index[index]
        if not row.get("has_loop") or not instrument:
            # A bare thermometer, or the software loop: neither points the
            # command panel anywhere. Both are non-selectable at the item
            # level, so this is belt and braces rather than the usual path.
            return
        self._loop = int(row.get("loop") or 1)
        names = [self.instrument_combo.itemText(i)
                 for i in range(self.instrument_combo.count())]
        if instrument in names:
            if self.instrument_combo.currentText() != instrument:
                # _instrument_changed does the rest, including this loop.
                self.instrument_combo.setCurrentIndex(names.index(instrument))
                return
        elif instrument:
            self._note(self.loop_note,
                       f"{instrument} is read-only here: watched, not commanded",
                       theme.note_style("muted", self))
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
            # The key, and nothing else. Three lines explaining a disabled
            # control are read once and then occupy the panel forever; the key
            # is what somebody acts on, and the reasoning is a hover away.
            self._note(self.range_note, "ipc.allow_heater_range: false",
                       theme.note_style("warn", self))
            self.range_note.setToolTip(
                "This recorder will not change a heater range from a file, "
                "including to 0 — cutting a heater is not automatically the "
                "safe direction. Panic → All heaters OFF is exempt from this "
                "gate and always works.")
        analog_ok = self.source.allows_analog_output()
        self.analog_spin.setEnabled(analog_ok)
        self.analog_button.setEnabled(analog_ok)
        if analog_ok:
            self._note(self.analog_note, "one step, no ramp",
                       theme.note_style("muted", self))
            self.analog_note.setToolTip(
                "This is one step, as fast as the cryostat allows — there is "
                "no ramp on this path.")
        else:
            self._note(self.analog_note, "ipc.allow_analog_output: false",
                       theme.note_style("warn", self))
            self.analog_note.setToolTip(
                "This recorder will not drive this output from a file, "
                "including to 0. Panic → All heaters OFF is exempt from this "
                "gate and always works.")

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
            self._note(self.pid_note, "read_pid: false — not the instrument's",
                       theme.note_style("warn", self))
            self.pid_note.setToolTip(
                "This recorder does not poll PID?, so these boxes are not the "
                "instrument's own gains. Set read_pid: true in its config.")
        elif not self.source.allows_pid():
            self._note(self.pid_note, "ipc.allow_pid: false",
                       theme.note_style("warn", self))
            self.pid_note.setToolTip(
                "Shown from the instrument, but this recorder will not change "
                "them from a file. The gains apply no power on their own — a "
                "loop at range 0 stays inert however it is tuned.")
        else:
            self._note(self.pid_note, "the instrument's own gains",
                       theme.note_style("muted", self))
            self.pid_note.setToolTip(
                "Changing these does not apply power; it changes how the loop "
                "gets anywhere at all.")

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
            # The date, once a window is wider than a day.  Without it a five
            # day window reads "03:00:00-03:00:00", which is the same string
            # a zero-width one would produce and says nothing about which
            # days are on screen -- exactly what somebody hunting for last
            # Friday needs to be told.
            fmt = 'HH:mm:ss' if t1 - t0 < 86400.0 else 'MMM d HH:mm'
            bits.append(
                f"window {stamp(int(t0)).toString(fmt)}–"
                f"{stamp(int(t1)).toString(fmt)} "
                f"({_duration(t1 - t0)}) · not following")
            bits.append(self._resolution_note(t0, t1))
        else:
            bits.append(f"last {_duration(self._follow_span_s)} · live")
        for unit, fixed in self._ylim.items():
            if fixed is not None:
                bits.append(f"y {fixed[0]:g}–{fixed[1]:g} {unit} fixed")
        self.statusBar().showMessage("   ".join(bits))

    def _resolution_note(self, t0: float, t1: float) -> str:
        """Whether the picked span is drawn from every sample, or from some.

        The chart has three honest answers for a hand-picked window and two
        of them look identical: every sample the log holds, a coarser reading
        of it, or -- for the quarter second after a gesture -- neither yet.
        Which one is on screen depends on how wide the span is and on how
        long this viewer has been running, neither of which is visible in the
        trace, so it is said here instead of left to be inferred.

        Saying it is not decoration.  The version of this that only knew two
        answers reported "overview" for a span the overview did not reach,
        while the chart underneath it was blank -- the note agreed with a
        picture that was not being drawn, which is worse than no note.  What
        it reports now is measured from the samples that were drawn.

        The distinction is about the *drawing* only.  Cursor statistics and
        the region export re-read the log at full resolution whatever this
        says, which is why a coarse chart is a presentational compromise and
        never a measurement one.
        """
        if self._span_load.isActive() or self._span != self._loaded_span:
            return "reading the log…"
        if self.tail.overlay_is_full_resolution(t0, t1):
            return "full resolution"
        stride = self.tail.overlay_stride()
        if self._drawn_spacing:
            note = f"1 pt / {_duration(self._drawn_spacing)}"
        else:
            note = "coarse"
        return f"too wide to read whole · {note}" if stride > 1 else f"overview · {note}"

    # -- plotting ----------------------------------------------------------

    def _sync_traces(self) -> None:
        """Create a curve and a checkbox for any column the log has grown."""
        channel_names = {str(c.get("name")) for c in self.source.channels()}
        if not channel_names:
            # No status file -- an archived log opened with --csv, or a
            # recorder whose status went unreadable before the first sync.
            # The header still says which columns are thermometers.
            channel_names = set(self.tail.channel_columns())
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
            # Width 1, and not for looks.  Qt's raster engine strokes a
            # 1-pixel pen along a fast path and anything wider through the
            # full path stroker, per segment -- which on a day of 1 Hz
            # samples is the difference between a 127 ms redraw and a
            # 1247 ms one, on a window that redraws every second.  The
            # antialiasing is kept: measured against downsampling it costs
            # 2 ms, and it is what stops a dense trace looking like a comb.
            curve = plot.plot([], [], pen=pg.mkPen(colour, width=1), name=name)
            # Draw what the screen can show, not what the log holds.  A day
            # at 1 Hz is 86 400 samples across roughly 900 pixels, so ninety
            # nine of every hundred points land on a pixel that is already
            # painted.  `peak` keeps each pixel's minimum and maximum rather
            # than sampling one of them, so a spike survives the reduction --
            # which matters, because a spike is exactly what somebody is
            # looking for.  Statistics and exports never come through here;
            # they read the log at full resolution via `samples_in`.
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
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
            check.stateChanged.connect(self._schedule_redraw)
            self.toggles[name] = check
            self.traces_layout.insertWidget(self.traces_layout.count() - 1, check)

    def _redraw(self) -> None:
        #: The extent of what each panel is actually showing, so the comfort
        #: stop can widen to a reading that lies outside it.
        extents: dict[str, tuple[float, float] | None] = {
            unit: None for unit in self._panels
        }
        #: The spacing of the longest trace actually drawn, which is what the
        #: status bar reports.  Measured from the samples rather than assumed
        #: from the span: decimation is what changes it, and the whole point
        #: of saying it out loud is that the operator should not have to
        #: work out which regime the chart is in.
        self._drawn_points = 0
        self._drawn_spacing = None
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
            if len(t) > self._drawn_points:
                self._drawn_points = len(t)
                if len(t) > 1:
                    self._drawn_spacing = (t[-1] - t[0]) / (len(t) - 1)
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
        self._note(self.export_note, "")
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
                rows.append((name, f"{st.mean:.3f}", f"{st.std:.3f}",
                             f"{st.delta:+.3f}", f"{st.n}"))
            label = self._stat_labels[unit]
            if not rows:
                label.hide()
                continue
            # Δt once, in the header, because it is a property of the region
            # and not of any one trace.
            label.setHtml(_stats_html(f"Δt {_duration(t1 - t0)}",
                                      rows))
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
            self._note(self.export_note, f"could not write {chosen}: {exc}",
                       theme.note_style("bad", self))
            return
        self._note(self.export_note,
                   f"wrote {rows} row(s) over {_duration(t1 - t0)} to "
                   f"{os.path.basename(chosen)}",
                   theme.note_style("ok", self))

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

    def _x_range_changed(self, vb, rng) -> None:
        """The time axis moved by any route other than the drag -- wheel,
        Shift-drag, middle-drag, a linked view.

        A deliberate move **is** a picked window, whether or not one had been
        picked before.  This used to give up while ``_span`` was None, on the
        grounds that the signal fires on every autoscale while the view is
        following the recorder.  That much is true, and it also meant the
        *wheel* was never noticed: scrolling out from a live view moved the
        axis to five days and left the viewer drawing the last 48 hours,
        because nothing had told it the window had changed.  No span, no read,
        and four fifths of the screen blank however long you waited -- and
        then dragging a rectangle over the same view filled it in, because a
        drag does set the span.  That is what made it look like data failing
        to load rather than data never asked for.

        What separates the two is autoscaling, not ``_span``: an axis merely
        following its data has not been moved by anybody, and an axis that
        has stopped following it has.  ``_y_range_changed`` has always made
        the distinction that way for the value axes.
        """
        if self._span is None and vb.autoRangeEnabled()[0]:
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

    def _span_to_all(self) -> None:
        """Frame everything the tail holds.  Used when a finished log is
        opened directly, where following the live edge shows nothing."""
        oldest = newest = None
        for series in self.tail.series.values():
            if not series.t:
                continue
            if oldest is None or series.t[0] < oldest:
                oldest = series.t[0]
            if newest is None or series.t[-1] > newest:
                newest = series.t[-1]
        if oldest is None or newest is None or newest <= oldest:
            return
        pad = (newest - oldest) * 0.02
        self._span = (oldest - pad, newest + pad)
        self._span_load.start()

    def _set_follow(self, seconds: float) -> None:
        """Enter a live-referenced view and drop every hand-picked axis."""
        self._span = None
        self._span_load.stop()
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
        self._schedule_redraw()
        # Restarting the timer is what debounces: a gesture still in motion
        # keeps pushing the read out, and it happens once the gesture stops.
        if self._span is not None and self._span != self._loaded_span:
            self._span_load.start()
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
                self.analog_button, self.arm_button, self.clear_lockout_button]

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
            "temperature. A software loop is DISENGAGED — it stops writing "
            "to the heater entirely, and the heater keeps the value it "
            "has.\n\n"
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

    def _send_clear_lockout(self) -> None:
        """Clear a fault lockout.  Half the way back, deliberately."""
        if self.spool is None:
            return
        if not self._confirm(
            "Clear the lockout",
            "Clear the software loop's fault lockout?\n\n"
            "The loop latched itself out after a fault ramp-down. That latch "
            "exists so that somebody looks at the cryostat before it drives "
            "the heater again — clearing it is a claim that you have.\n\n"
            "This does NOT resume the loop. It stays disarmed; use “Arm "
            "software loop” when you are ready to close it.\n\n"
            "It is not a panic action: it is the first step back toward "
            "applying power, so it is gated like arming.",
        ):
            return
        self._queue("ack", instrument="")
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
