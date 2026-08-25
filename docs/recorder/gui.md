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
| `--read-only` | open with no command spool at all, so the whole control panel is dead |
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
the control panel below — all of which write into the same spool MATLAB uses,
behind a confirmation dialog, with no privileges MATLAB lacks.

## The control panel

One instrument selector, then whatever the selected box can actually be asked
to do. Which controls appear is decided by what the **recorder** says the
instrument has (`links[].loops`, `heater_outputs`, `analog_output` in
`status.json`), not by a model-number table kept in the viewer — the same table
in three places is the same table going stale in three places.

| Control | Appears for | |
|---|---|---|
| **Setpoint** | a box with loops | loop + kelvin. Inert on its own: a setpoint does nothing while the range is 0 |
| **Heater range** | a box with heater outputs | output + 0/1/2/3. **Above 0 this applies power** |
| **Analog output** | a box with a settable analog output (a 218) | one percentage. **Above 0 this applies power** — there is no inert half |
| **All heaters OFF** | always | every writable instrument to zero, 33x ranges and 218 outputs alike |

Four things this panel does on purpose:

**The analog spin box is capped at the recorder's `max_output_pct`**, not at
100, so the widget cannot express a value that is going to be refused — and the
ceiling is visible in the group title without reading the config file.

**A shut gate is announced, not enforced by greying out.** If the recorder has
`allow_heater_range: false` or `allow_analog_output: false` the control says so
and stays live, because 0 is always permitted. Disabling it would take the
button away at exactly the moment somebody wants to make the rig safe.

**The range dialog quotes the setpoint the loop is about to chase, with its
age.** "Range 3" means nothing on its own. The age is not decoration: the
recorder's cycle is read → apply commands → write status, so a setpoint you set
seconds ago may not be in the status file yet, and the dialog says so rather
than showing a stale number as current.

**One unacknowledged command locks every button.** Otherwise a range can be
queued against a setpoint that turned out to be refused.

The panic button is deliberately *not* aimed at the selected instrument. Every
other control needs an argument that means something on one box; this one means
"stop heating", which on a two-box rig had better include the box carrying the
sample heater.

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

- **no ramp control** — same file protocol, just no widget yet;
- **no ramping of the analog output.** Setting a percentage is one step. Rate
  limiting is control policy and belongs to the supervisor; a second set of
  limits in the viewer is a second set of limits that can disagree;
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
