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

## History across midnight

The recorder writes one CSV per day, and the viewer tails whichever one it is
currently writing. Three things make history reachable without weighing the
viewer down:

- **a rollover keeps the history.** When the recorder moves to a new file the
  viewer starts that file from the top but keeps everything it has already
  plotted, so a trace crosses midnight without a gap;
- **a fresh start backfills just over the widest view window** (48 h). A
  viewer opened mid-day still gets yesterday's cooldown; weeks of samples
  nobody asked for are not dragged into memory;
- **older spans are fetched on demand.** Picking a span re-reads it from the
  logs on disk at full resolution — whether or not that day was ever
  backfilled.

The `View` row holds live-referenced windows — **6 h, 12 h, 24 h, 48 h** —
whose right edge is always the newest sample, riding forward with the
recorder, plus **All**, which shows everything this viewer happens to hold.
A drag supersedes any of them; clicking one again is the way back.

Memory stays bounded by `--max-points`: past the cap a trace is decimated
(every other sample dropped) rather than truncated, so old days lose
resolution in the overview but never disappear — and a hand-picked span is
re-read from disk at full resolution (above).

## What it shows

Two x-linked panels: **kelvin above, output percent below**. They are separate
because 63% and 63 K are different quantities and one axis invites reading a
trend across them.

Plus live readouts, link health, per-trace toggles, and the control panel
below — all of which write into the same spool MATLAB uses, behind a
confirmation dialog, with no privileges MATLAB lacks.

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
button away at exactly the moment somebody wants to make the cryostat safe.

**The range dialog quotes the setpoint the loop is about to chase, with its
age.** "Range 3" means nothing on its own. The age is not decoration: the
recorder's cycle is read → apply commands → write status, so a setpoint you set
seconds ago may not be in the status file yet, and the dialog says so rather
than showing a stale number as current.

**The fields fill with what the cryostat is at now.** Each one starts from the
recorder's readback in `status.json` (`aux`): the setpoint field from the
selected loop's setpoint, the analog output from the 218's current percentage,
the range combo from the box's current range. Swapping to a 218 therefore finds
the power it is already driving instead of presenting a misleading 0%, and a
setpoint starts from the value being chased rather than zero — which on these
widgets reads as a plausible number to send. Fields keep tracking the readback
until edited (so a setpoint changed from MATLAB or another viewer shows up
here too); an edit stops the tracking until the selection changes or the
pending command settles.

**A sent value is not snapped back by a stale readback.** Between an
acknowledged command and the next readback, `aux` still holds the *old* value.
Until the readback confirms what was asked for, the field holds at the
commanded number — so asking for 43% never shows 0% again in the seconds while
power is the question.

**One unacknowledged command locks every button.** Otherwise a range can be
queued against a setpoint that turned out to be refused.

The panic button is deliberately *not* aimed at the selected instrument. Every
other control needs an argument that means something on one box; this one means
"stop heating", which on a two-box cryostat had better include the box carrying the
sample heater.

## Zooming with the mouse

**Drag a rectangle on either panel** and the view becomes exactly that
rectangle — both the time axis and the value axis, at precisely the edges
dragged, with no padding and no autoscale afterwards putting them back.

The time axis is shared: both panels move together, because they are x-linked.
The value axis is not. A rectangle dragged on the temperature panel crops the
temperature axis and leaves the percent panel below autoscaling to whatever the
new window holds, because 63 % and 63 K are different quantities and always
were.

A drag has to be a rectangle in **both** directions — under about six pixels
either way and it is treated as a click that wobbled, and does nothing at all.
There is no one-axis form of the gesture: the drag means exactly the box that
was drawn, and the `X±` / `Y±` buttons are how a single axis gets moved in
steps without redrawing a rectangle to do it.

| Gesture | |
|---|---|
| **left-drag** | zoom to exactly that rectangle |
| **X+ X− Y+ Y−** | zoom one axis at a time about its middle |
| **wheel** | zoom about the cursor |
| **shift-drag**, or middle-drag | pan |
| **double-click** | follow the recorder again |
| right-drag, right-click | pyqtgraph's own scaling and menu, untouched |

`Shift` rather than `Ctrl` because macOS turns Ctrl-click into a right-click
before Qt sees it.

### The X and Y buttons

Both zoom one axis at a time, in steps, about the middle of what is shown.

A hand-picked view **stops following the recorder**: new samples land off the
right-hand edge, which is what a fixed window means, and a fixed value axis
will not open up for an excursion that leaves it. While a span is picked the
status bar names it and says `not following`, no view button is checked, and
`All`, a double-click, or any view button returns to a live-referenced view —
all axes at once.

A value axis moved by the **wheel** or a shift-drag counts as hand-picked too;
it is just as fixed as one dragged out, and the `Live` button says so.

The time window is not just a view change: the curves are refed with exactly
the samples in the span (plus one either side, so a trace crossing the edge is
drawn leaving it). That is what lets a panel still autoscaling fit itself to
the span — zoom into a five-minute wobble and the wobble fills the panel
instead of being flattened by a day's excursion. A panel whose value axis was
dragged out keeps the axis it was given; the cut still matters there, for the
other panel and for the number of points Qt is asked to draw.

### Full resolution comes back on zoom-in

The in-memory history is decimated once it outgrows `--max-points` (every
other sample dropped, doubling the span the budget covers). A picked span is
not answered from what survived: one quiet tick after the span settles, the
viewer re-reads that span from the logs on disk at full resolution and swaps
it in. Zooming out and back in shows real samples again, at whatever cadence
the recorder wrote. The overview you see for that first tick is thinned; the
disk read costs nothing during a gesture because it waits for the span to
stop moving.

## What it deliberately does not do

Omissions, not oversights:

- **no ramp control** — same file protocol, just no widget yet;
- **no ramping of the analog output.** Setting a percentage is one step. Rate
  limiting is control policy and belongs to the supervisor; a second set of
  limits in the viewer is a second set of limits that can disagree;
- **no annotation of the log** from the viewer;
- **no cursor readout.** A pyqtgraph one-liner if it turns out to be wanted;
- **no export of the selected span.** Picking a window is a way to look, not a
  way to cut the log; the CSV is the log.

## Running it headless

Verified against a live recorder this way, including the send path and the
acknowledgement round trip:

```bash
QT_QPA_PLATFORM=offscreen python -m lschart.gui -c config.yaml
```
