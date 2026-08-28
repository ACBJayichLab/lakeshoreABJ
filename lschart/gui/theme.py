"""Colours that survive the viewer being opened on a dark desktop.

The viewer was written on a light desktop and hardcoded its foregrounds, which
is fine until somebody opens it in dark mode: a table item forced to ``#000000``
lands on a ``#171717`` base at a contrast ratio of **1.17**, which is not "hard
to read", it is invisible.  Reported from macOS dark mode; the same code would
do the same thing under a dark Windows or KDE theme.

Two rules come out of that, and they are the whole of this module:

**Never paint the normal case.**  Ordinary text has no colour of its own -- it
is whatever the palette says, and the palette is the one thing that already
knows what the desktop is doing.  :func:`clear_foreground` is how a table item
goes back to that, and it is what every non-warning cell gets.  A hardcoded
"black" is a bug on a dark theme and a hardcoded "white" is the same bug on a
light one; the fix is not a better constant, it is no constant.

**Paint the exceptional case from a pair.**  A warning does need a colour, so
each semantic name resolves to one value for light and another for dark, both
measured.  Every pair below clears 4.5:1 against the backgrounds Qt actually
reports on both themes -- see ``tests/test_gui_theme.py``, which computes the
ratios rather than trusting this sentence.

**Resolved at call time, never at import.**  A desktop can switch theme while
the viewer is open, and Qt sends a palette change when it does.  Reading the
palette per call is what lets one ``_apply_theme()`` put the whole window
right; a module-level constant would freeze whatever the theme was at launch.
"""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets

#: Below this lightness, the desktop is dark.  Measured against the *palette*
#: rather than the OS: Qt is what actually paints the widgets, and a user who
#: has forced a Qt style has an opinion the OS does not know about.
_DARK_BELOW = 0.5

#: Semantic foregrounds.  Light values are the ones the viewer already used and
#: are kept so nothing changes on the desktop it was written on -- except
#: ``warn``, whose old ``#e65100`` managed only 3.79:1 even on white.
LIGHT = {
    "muted": "#37474f",
    "ok": "#1b5e20",
    "warn": "#bf360c",
    "bad": "#b71c1c",
}

DARK = {
    "muted": "#b0bec5",
    "ok": "#81c784",
    "warn": "#ffb74d",
    "bad": "#ef9a9a",
}

#: The health banner, which is the one element that paints its own background
#: as well.  A pastel chip is legible but reads as a hole punched in a dark
#: window, so dark mode gets dark grounds with light text rather than the same
#: chip inverted.  ``(background, foreground)``.
BANNER = {
    False: {                       # light desktop
        "ok": ("#e8f5e9", "#1b5e20"),
        "stale": ("#fff3e0", "#bf360c"),
        "stopped": ("#eceff1", "#37474f"),
        "absent": ("#ffebee", "#b71c1c"),
    },
    True: {                        # dark desktop
        "ok": ("#1b3320", "#a5d6a7"),
        "stale": ("#3a2a15", "#ffb74d"),
        "stopped": ("#2f3a40", "#cfd8dc"),
        "absent": ("#3a1f1f", "#ef9a9a"),
    },
}


def is_dark(widget: QtWidgets.QWidget | None = None) -> bool:
    """Is the palette we are painting into a dark one?

    From the widget's own palette where there is one, because a widget can be
    given a palette its application does not have.
    """
    if widget is not None:
        palette = widget.palette()
    else:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return False
        palette = app.palette()
    window = palette.color(QtGui.QPalette.ColorRole.Window)
    return window.lightnessF() < _DARK_BELOW


def colours(widget: QtWidgets.QWidget | None = None) -> dict[str, str]:
    """The semantic foregrounds for the theme in force right now."""
    return DARK if is_dark(widget) else LIGHT


def colour(name: str, widget: QtWidgets.QWidget | None = None) -> str:
    return colours(widget)[name]


def note_style(name: str, widget: QtWidgets.QWidget | None = None) -> str:
    """``color:`` for one of the panel's explanatory notes."""
    return f"color:{colour(name, widget)};"


def banner_style(state: str, widget: QtWidgets.QWidget | None = None) -> str:
    """The health banner's whole stylesheet for one state."""
    background, foreground = BANNER[is_dark(widget)].get(
        state, BANNER[is_dark(widget)]["stopped"])
    return (f"background:{background}; color:{foreground}; "
            "padding:6px; border-radius:4px;")


def clear_foreground(item) -> None:
    """Give a table item back to the palette.

    Not "paint it black", and not "paint it white" either: an item with no
    foreground of its own is drawn in the palette's text colour, which is the
    only value that is right on both themes and stays right when the desktop
    switches underneath a running viewer.

    Clearing the *role* rather than assigning a default-constructed brush,
    because an empty ``QBrush`` is still a brush and some styles honour it as
    "paint nothing".
    """
    item.setData(QtGui.Qt.ItemDataRole.ForegroundRole, None)
