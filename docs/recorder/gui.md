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
| `--gap-factor N` | how many sample intervals a hole must exceed to be drawn as a gap, default 4 |
| `--max-kelvin N` | where the temperature panel stops panning and zooming outward, default 450 |
| `--max-percent N` | the same stop for the output panel, default 100 |
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

The `View` row holds live-referenced windows and nothing else — **6 h, 12 h,
24 h, 48 h**, opening on 24 h — whose right edge is always the newest sample,
riding forward with the recorder. A drag supersedes any of them; clicking one
again is the way back, and a double-click returns to whichever was showing.

There is deliberately no "everything" button. A window meaning "whatever this
viewer happens to hold" is a different span on a viewer opened an hour ago and
one left up since Tuesday, and it grows silently under whoever is reading it.
Scrolling back to find an older run is acceptable; a view whose extent is an
accident of process uptime is not.

Memory stays bounded by `--max-points`: past the cap a trace is decimated
(every other sample dropped) rather than truncated, so old days lose
resolution in the overview but never disappear — and a hand-picked span is
re-read from disk at full resolution (above).

## A hole in the log is drawn as a hole

Where consecutive samples are more than **four sample intervals** apart the
trace breaks instead of being joined by a straight line. A recorder that was
off for an hour did not spend that hour sliding evenly from one temperature to
the other, and the interpolating line asserts exactly that — at exactly the
place on the chart where nobody has any data to contradict it.

The threshold is a multiple of the *series' own median interval*, not a number
of seconds, because the same trace is drawn at full resolution when a span is
picked and decimated by 2, 4, 16 when it is not. A fixed number of seconds
would shatter a zoomed-out overview into confetti or miss every gap in a fresh
one; a multiple survives both, and survives a log written at a different
interval from the one being written now.

Four, and not two, because a recorder that missed a cycle or two is still
recording: a retry on a jittering bus costs a cycle, and joining across that is
the honest drawing. Three consecutive cycles gone is not jitter. `--gap-factor`
moves the line if a particular cryostat wants it moved.

## What it shows

Two x-linked panels: **kelvin above, output percent below**. They are separate
because 63% and 63 K are different quantities and one axis invites reading a
trend across them.

Plus live readouts, link health, per-trace toggles, and the control panel
below — all of which write into the same spool MATLAB uses, behind a
confirmation dialog, with no privileges MATLAB lacks.

## The loop table

Beneath the per-channel readouts, and **not instead of them**: recording every
thermometer continuously is the recorder's job, and a loop-centric view that
replaced the channel list would turn an eight-input monitor into however many
loops it has.

One row per loop, across every instrument: `# · Sensor · K · SP · Out · Rng ·
Rail · Off SP`. **Clicking a row selects that loop** — instrument and loop
together — and every control in the command panel follows it. There is no loop
spin box and no output combo; two ways to choose a loop is two things that can
disagree about where a setpoint is going.

Which sensor a loop reads comes from the **instrument** (`OUTMODE?`), not from
a config key. On this family the loop number *is* the output number by
protocol, so the heater binding is derived too. A loop whose output is
analog-only — a 336's 3 and 4 — shows `n/a` in the range column, because it
does not have a range that happens to be unknown; it has none.

### The two warning marks

`Rail` and `Off SP` are **two marks and never one**. OR-ing them into a single
warning gives an icon that is lit through every cooldown, and an icon that is
always lit is an icon nobody reads. They also mean different things:

| | |
|---|---|
| **Rail** | the output is at or beyond 99 % or 1 %. Fixed, not per loop and not configurable: "this loop has no authority left" is the same fact on every heater |
| **Off SP** | the sensor is further from the setpoint than that loop's `loop_thresholds` entry |

Both are **suppressed while the loop is not trying** — range 0, a mode other
than closed loop, or a ramp still traversing. A loop that was never going to
the setpoint is not failing to reach it, and a ramp that has not arrived is
doing exactly what it was asked to.

A loop with **no threshold configured gets no `Off SP` mark at all.** 0.5 K is
tight at 4 K and loose at 300 K; with nothing configured the honest answer is
silence rather than a number this software picked.

Worth expecting: a closed-loop output with **no range to switch it off** — a
336's loops 3 and 4 — sitting below ambient will light both marks and keep them
lit. That is literally true (it is asking for a temperature it cannot reach,
with the output pinned at zero) and it is what the suppression rules above
allow, because such a loop has no inert half to be switched off by.

## The control panel

One instrument selector, then whatever the selected box can actually be asked
to do. Which controls appear is decided by what the **recorder** says the
instrument has (`links[].loops`, `heater_outputs`, `analog_output` in
`status.json`), not by a model-number table kept in the viewer — the same table
in three places is the same table going stale in three places.

| Control | Appears for | |
|---|---|---|
| **Setpoint** | a box with loops | kelvin, aimed at **the loop selected in the loop table**. Inert on its own: a setpoint does nothing while the range is 0 |
| **PID gains** | a box with loops | P, I and D on **the selected loop** — the instrument's own, not any software loop's. All three go out together. Applies no power |
| **Heater range** | a selected loop that drives a heater output | 0/1/2/3, applied to **that loop's** output. **Above 0 this applies power** |
| **Analog output** | a box with a settable analog output (a 218) | one percentage. **Above 0 this applies power** — there is no inert half |
| **Arm software loop** | always | close the software loop at the temperature the cryostat is at now — the way back from a hold. **This applies power** |
| **Panic ▾** | always, and never greyed out | a menu of the two ways to stop: **All heaters OFF** and **All temperatures HOLD** |
| **Accept commands from this viewer** | always | whether the recorder is listening to *this viewer*. Unticking mutes it; ticking undoes that |

**Only the relevant grouping is ever shown.** Selecting a 336 loop 3 or 4
hides the heater-range control, because there is no range to set — and says so
in a sentence rather than offering a control that could only produce a
refusal.

**There is no "get PID" button, because there could not be one.** This viewer
holds no port and cannot ask an instrument anything. The gains are in the panel
because the recorder polls `PID?` on a slow cadence and publishes them; a
recorder configured with `read_pid: false` — the default — leaves the boxes
blank, and the note under them names the key that would fill them. That note is
deliberately different from the one about `ipc.allow_pid`: "nobody is reading
these" and "this recorder will not change these" are different facts, and an
operator who cannot tell them apart will conclude the wrong thing about both.

Five things this panel does on purpose:

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
queued against a setpoint that turned out to be refused. The Panic menu is the
exception, deliberately: that reasoning inverts for the stopping direction — no
pending command can make it wrong to stop, and an operator reaching for Panic
while somebody's setpoint is still being acknowledged must not find it greyed
out.

## The Panic menu

Two ways to stop, behind a menu, and **three clicks by design**: open the menu,
choose the action, confirm it. These are needed almost never and must not be
reachable by accident; the middle click is what a mis-aimed one lands on.

| | |
|---|---|
| **All heaters OFF** | 33x ranges to 0 and 218 analog outputs to 0%, on every writable box. Setpoints are not changed |
| **All temperatures HOLD** | every closed loop's ramping switched off (the rate is kept) and its setpoint moved to its own sensor's present temperature; a software loop's output frozen |

Neither is aimed at the selected instrument. Every other control needs an
argument that means something on one box; these mean "stop", which on a two-box
cryostat had better include the box carrying the sample heater.

**The menu sits outside the command group, and that is structural.** Both kinds
are exempt from the recorder's per-client source policy, so when that policy
switches the rest of the panel off these must stay live — and in Qt a child of a
disabled parent is disabled however firmly it is enabled. A panel that greyed
out a button the recorder would in fact obey would be lying at the moment it
matters most.

**What the bypass covers, and what it does not.** These two bypass the source
policy and the two power gates. They do *not* bypass `ipc.accept_commands`,
`allow_writes` or `transport.read_only` — a box configured read-only stays
read-only and is named in the reply. The menu's tooltip says exactly that,
rather than "bypasses interlocks", which would be a promise it does not keep.

**Hold's dialog says two things that are easy to get wrong.** Hold is not a
synonym for less power: while a ramp is heading down, its setpoint sits below
the temperature the cryostat has reached, so holding demands *more* heat than
the ramp was demanding. And hold means two different things on the two boxes — a
33x loop holds a temperature and keeps regulating; a 218 holds a power, and
nothing regulates the sample afterwards.

## Muting this viewer

The checkbox below the Panic menu is whether the recorder is **listening** to
this viewer. It writes the same `sources.json` a text editor writes, through the
`source` command.

It sits outside the command group for the same structural reason the Panic menu
does — it is the control that undoes the thing which disables that group, so it
cannot live inside it. The `source` command is exempt from the policy it edits
precisely so this works when nothing else in the panel does: **muting is not a
one-way door.**

**Muted is about listening, never about reading.** The chart, the readouts, the
loop table and the marks are all reads of `status.json` and carry on exactly as
before. A panel full of greyed-out controls looks a lot like a broken viewer,
which is why the confirmation says this out loud.

The checkbox is disabled — with a tooltip saying why — when the recorder's
*config* (`ipc.sources`) refuses this viewer outright. The overlay may only
narrow, so that one needs a config edit and a restart, and offering the click
would be offering a refusal.

**Arm is outside the menu**, next to it rather than in it. Arming starts the
loop driving the heater again, which is the power-applying direction; sitting it
beside the two stopping actions would suggest it shares their exemptions, and it
shares none of them.

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
| **left-drag** | zoom to exactly that rectangle — *or* place a cursor, while the cursors are up |
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
a double-click or any view button returns to a live-referenced view — all axes
at once.

A value axis moved by the **wheel** or a shift-drag counts as hand-picked too;
it is just as fixed as one dragged out, and the status bar says so.

### The value axis has a comfort stop

Zoom and pan on a value axis stop at **0–450 K** and **0–100 %** — *unless the
data goes outside them*, in which case the stop widens to the data. A 300 K
axis panned out to 10 000 K is a chart nobody can read; a sensor that has come
loose and reads 1400 K is a chart somebody has to be able to read, and an axis
that refused to go there would be hiding the measurement in favour of a number
the viewer guessed. `--max-kelvin` and `--max-percent` move the stop. The time
axis has none: a log runs for as long as it runs.

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

## Measuring a region: the cursors

`Cursors` puts two vertical lines on both panels, at the thirds of the window.
Left-click or drag on either panel moves **whichever is nearer** to the
pointer — nearest rather than alternating, because alternating means
remembering which one moved last and getting the wrong edge half the time.

Between them, an in-plot panel reports **per trace: mean, standard deviation
and Δvalue**, and **once for the region, Δtime**.

Three things worth knowing about those numbers:

- **they come from full-resolution samples, never from what is drawn.** The
  chart decimates; a mean over every other sample is a different number. The
  samples come from memory while nothing has been thinned and from the logs
  once something has, which is `CsvTail.samples_in`;
- **Δvalue is last minus first, not max minus min.** A trace that wandered out
  and came back moved nowhere, and the standard deviation beside it is what
  says it wandered;
- **a region in the past is measured once.** Nothing the recorder does now
  changes what happened between two past instants. A region whose right-hand
  cursor sits past the newest sample is still filling and is re-measured as
  rows arrive.

While the cursors are up **the left button places them instead of drawing a
zoom rectangle** — two gestures cannot share one button. The wheel, shift-drag
and the `X±` / `Y±` buttons still zoom throughout, so no view is unreachable
with the cursors on screen.

With **no** cursors set, the legend on each panel carries the **live value** of
its traces. With cursors up it goes back to being names: a second number a few
pixels from the statistics panel, measured over a different span, is how a
chart comes to disagree with itself.

## Naming the trace under the pointer

Hovering names the nearest trace and gives its value there, on both panels, to
3 dp. Interpolated at the pointer's time rather than snapped to the nearest
sample — on a decimated overview the nearest sample can be minutes away, and a
number that far from where the pointer is pointing is a different reading.
Independent of the cursors; it works whether they are up or not.

## Exporting a region

`Export region…` writes the samples between the cursors to a CSV, at full
resolution, in the recorder's own shape — `Timestamp`, `Time`, then one column
per channel. **Every column the log carries**, not only the traces that happen
to be ticked: what somebody wants out of a region a week later is not
necessarily what was on screen when they picked it. A column with no sample at
some timestamp is left empty, which is what the recorder writes for a channel
that failed a cycle.

## What it deliberately does not do

Omissions, not oversights:

- **no ramp control** — same file protocol, just no widget yet;
- **no ramping of the analog output.** Setting a percentage is one step. Rate
  limiting is control policy and belongs to the supervisor; a second set of
  limits in the viewer is a second set of limits that can disagree;
- **no annotation of the log** from the viewer;
- **no "everything held" view button** — see above.

## Running it headless

Verified against a live recorder this way, including the send path and the
acknowledgement round trip:

```bash
QT_QPA_PLATFORM=offscreen python -m lschart.gui -c config.yaml
```
