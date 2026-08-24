# The strip chart

```bash
python -m lschart.gui -c config.yaml
lschart-view -c config.yaml            # same thing, installed script
```

Needs the `gui` extra:

```bash
pip install -e ".[gui]"      # pyqtgraph + PySide6
```

| Flag | |
|---|---|
| `-c CONFIG` | the **recorder's** config — the viewer reads it only to find the data directory |
| `--status PATH` | point at a `status.json` directly instead |
| `--refresh S` | redraw cadence, default 1.0 |
| `--max-points N` | default 200,000 |
| `--read-only` | hide the setpoint control |
| `--log-level` | |

## It is a separate process, not a thread

So a Qt bug, a wedged event loop or a closed laptop lid cannot take logging
with it; so the viewer can be closed and reopened mid-run; and so two people
can watch at once.

It is just another client of [the file interface](file-interface.md), with no
privileges the MATLAB one lacks. Qt is an optional dependency imported by
exactly one module in the repo (`gui/window.py`) — the recorder is what has to
stay up for months, and every dependency it does not have is one that cannot
break it.

`gui/source.py` holds everything that is not Qt (`CsvTail`, `StatusSource`) and
is what the tests cover.

## What it shows

Two x-linked panels: **kelvin above, output percent below**. They are separate
because 63% and 63 K are different quantities and one axis invites reading a
trend across them.

Plus live readouts, link health, a time-window selector, per-trace toggles, and
a setpoint control that writes into the same spool MATLAB uses, behind a
confirmation dialog.

## Picking the time window with the mouse

**Drag across either panel** and that span becomes the window. The drag is
horizontal by construction — the vertical extent is ignored, so reaching for a
time window cannot crop the temperature axis by accident — and both panels move
together, because they are x-linked.

| Gesture | |
|---|---|
| **left-drag** | pick a time window |
| **wheel** | zoom about the cursor |
| **shift-drag**, or middle-drag | pan |
| **double-click** | follow the recorder again |
| right-drag, right-click | pyqtgraph's own scaling and menu, untouched |

`Shift` rather than `Ctrl` because macOS turns Ctrl-click into a right-click
before Qt sees it.

A hand-picked window **stops following the recorder**: new samples land off the
right-hand edge, which is what a fixed window means. While one is in effect the
`Live` button beside the combo lights up and the status bar names the span and
says `not following`. The button, a double-click, or picking any preset from
the combo returns to following.

The window is not just a view change: the curves are refed with exactly the
samples in the span (plus one either side, so a trace crossing the edge is
drawn leaving it). That is what lets the kelvin axis autoscale to the span —
zoom into a five-minute wobble and the wobble fills the panel instead of being
flattened by a day's excursion.

## What it deliberately does not do

Omissions, not oversights:

- **no heater range control.** It applies power; doing that from a chart is a
  different decision from typing it. The spool supports it, gated;
- **no ramp control** — same file protocol, just no widget yet;
- **no annotation of the log** from the viewer;
- **no y-axis autoscale lock or cursor readout.** Both are pyqtgraph one-liners
  if they turn out to be wanted;
- **no export of the selected span.** Picking a window is a way to look, not a
  way to cut the log; the CSV is the log.

## Running it headless

Verified against a live recorder this way, including the send path and the
acknowledgement round trip:

```bash
QT_QPA_PLATFORM=offscreen python -m lschart.gui -c config.yaml
```
