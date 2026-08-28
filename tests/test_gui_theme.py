"""Every colour the viewer paints must be legible on the theme it lands in.

Reported from macOS dark mode: the loop table forced ``#000000`` onto a
``#171717`` base, which is a contrast ratio of 1.17 -- not "hard to read",
invisible.  The same code would have done the same thing under a dark Windows
or KDE theme; it was never a macOS bug, it was a hardcoded-foreground bug that
a light desktop had been hiding.

So these tests *compute* the ratios rather than eyeballing the hex values, and
they check both themes, because the failure mode is symmetric: a constant
chosen to fix dark mode breaks light mode just as thoroughly.

The threshold is WCAG AA for normal text (4.5:1). That is a deliberate floor
and not an aspiration -- this is an instrument panel read at 2 a.m., and the
one thing worse than an ugly warning is one nobody notices.
"""

from __future__ import annotations

import pytest

from lschart.gui import theme

#: What Qt actually reports on the two desktops, measured rather than assumed:
#: `Window` is the panel behind the notes and `Base` is the table behind the
#: readouts, and a foreground has to clear both.
DARK_GROUNDS = ("#323232", "#171717")
LIGHT_GROUNDS = ("#ffffff", "#f0f0f0")

#: WCAG AA for normal text.
MIN_RATIO = 4.5


def _relative_luminance(hex_colour: str) -> float:
    raw = hex_colour.lstrip("#")
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def test_the_ratio_helper_agrees_with_the_two_ends_it_can_be_checked_against():
    """Black on white is 21:1 and anything on itself is 1:1. If this drifts,
    every other test in the file is measuring the wrong thing."""
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.05)
    assert contrast("#323232", "#323232") == pytest.approx(1.0, abs=0.001)


@pytest.mark.parametrize("name", sorted(theme.DARK))
@pytest.mark.parametrize("ground", DARK_GROUNDS)
def test_every_dark_foreground_is_legible_on_a_dark_ground(name, ground):
    ratio = contrast(theme.DARK[name], ground)
    assert ratio >= MIN_RATIO, (
        f"{name}={theme.DARK[name]} on {ground} is {ratio:.2f}:1")


@pytest.mark.parametrize("name", sorted(theme.LIGHT))
@pytest.mark.parametrize("ground", LIGHT_GROUNDS)
def test_every_light_foreground_is_legible_on_a_light_ground(name, ground):
    ratio = contrast(theme.LIGHT[name], ground)
    assert ratio >= MIN_RATIO, (
        f"{name}={theme.LIGHT[name]} on {ground} is {ratio:.2f}:1")


def test_the_two_palettes_describe_the_same_things():
    """A name that exists on one theme and not the other is a KeyError waiting
    for somebody to switch desktops."""
    assert set(theme.LIGHT) == set(theme.DARK)


@pytest.mark.parametrize("dark", [False, True])
def test_every_banner_state_is_legible_against_its_own_background(dark):
    """The banner is the one element that paints its own ground, so it is the
    one element whose contrast is entirely its own doing."""
    for state, (background, foreground) in theme.BANNER[dark].items():
        ratio = contrast(foreground, background)
        assert ratio >= MIN_RATIO, (
            f"{'dark' if dark else 'light'} banner {state}: "
            f"{foreground} on {background} is {ratio:.2f}:1")


def test_both_themes_cover_every_banner_state():
    assert set(theme.BANNER[True]) == set(theme.BANNER[False])


def test_the_old_hardcoded_black_would_have_failed_this():
    """The regression this file exists for, stated as arithmetic.

    Kept as a test rather than a comment because it is the only thing that
    explains why `clear_foreground` is worth having: the fix is not a better
    constant, it is no constant.
    """
    assert contrast("#000000", "#171717") < 1.2


# -- and the same, through the real Qt palette --------------------------------

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qt_app():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize("window_colour,expected_dark",
                         [("#ffffff", False), ("#f0f0f0", False),
                          ("#323232", True), ("#171717", True)])
def test_darkness_is_read_from_the_palette_not_from_the_operating_system(
        qt_app, window_colour, expected_dark):
    """A user who has forced a Qt style has an opinion the OS does not know
    about, and Qt is what actually paints the widgets."""
    from PySide6 import QtGui, QtWidgets

    widget = QtWidgets.QWidget()
    palette = QtGui.QPalette(widget.palette())
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(window_colour))
    widget.setPalette(palette)
    assert theme.is_dark(widget) is expected_dark
    assert theme.colours(widget) is (theme.DARK if expected_dark else theme.LIGHT)


def test_a_cleared_item_carries_no_colour_of_its_own(qt_app):
    """`setForeground(QBrush())` is not the same thing: an empty brush is
    still a brush, and some styles honour it as "paint nothing"."""
    from PySide6 import QtGui, QtWidgets

    item = QtWidgets.QTableWidgetItem("295.4")
    item.setForeground(QtGui.QBrush(QtGui.QColor("#000000")))
    assert item.data(QtGui.Qt.ItemDataRole.ForegroundRole) is not None
    theme.clear_foreground(item)
    assert item.data(QtGui.Qt.ItemDataRole.ForegroundRole) is None
