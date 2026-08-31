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
| `--max-kelvin N` | where the temperature panel stops panning and zooming outward, default 350 |
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
  logs on disk — whether or not that day was ever backfilled, and at full
  resolution unless the span is too wide to read whole
  ([below](#a-zoom-costs-the-span-not-the-archive)). The backfill bounds what
  is held in *memory*; it never bounds what can be drawn.

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

## The reading table

**One table, not two.** Every thermometer the recorder reads is a row, and the
control loop bound to that thermometer fills the rest of the row:
`Channel · K · Loop · SP · Out · Rng · State · Rail · Off SP`.

There used to be a per-channel readouts table with a loop table beneath it,
which on a 33x-only cryostat is the same four lines twice — every channel is
some loop's sensor. The reason for two is still real, though, and the merged
table has to respect it: a loop-centric table that *replaced* the channel list
would turn an eight-input 218 into however many loops it has, and recording
every thermometer continuously is the recorder's whole job. Making the
**channel** the row and the loop a set of **columns** is what gets one table
without paying that price:

- every channel gets a row, bound to a loop or not;
- a loop fills the loop columns of the row whose sensor it reads;
- a loop whose sensor is not among the channels — an unresolved binding, or a
  second loop on a channel that already has one — gets a row of its own rather
  than being dropped or overwriting the first;
- the software loop is just another loop, and lands on the row for the channel
  it controls.

So a 218 with eight inputs and no loops draws eight rows with the loop columns
empty, which is exactly the table it had before.

The table runs three points above the panel font: live values are what
somebody walks over to read from across the room, and the left panel is sized
so the channel names fit beside it rather than eliding — two thermometers
showing as `Stag…` is worse than useless.

**Clicking a row with an instrument loop selects it** and every control in the
command panel follows. There is no loop spin box and no output combo; two ways
to choose a loop is two things that can disagree about where a setpoint is
going. Rows with no loop, and the software loop's row, are not selectable —
the panel has nothing to point at them with.

Which sensor a loop reads comes from the **instrument** (`OUTMODE?`), not from
a config key. On this family the loop number *is* the output number by
protocol, so the heater binding is derived too. A loop whose output is
analog-only — a 336's 3 and 4 — shows `n/a` in the range column, because it
does not have a range that happens to be unknown; it has none.

`State` is what `OUTMODE?` says the loop is doing — `closed`, `open`, `zone`,
`monitor`, `off`. It decides whether either warning mark applies at all, so a
loop that has quietly stopped trying is worth seeing without a hover.

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

### The software loop's row

On a cryostat running `ltspm3`'s software PID, the **last row** of the table is
that loop, marked `sw` in the `#` column. Before this the viewer drew the
heater percent as a trace and said nothing whatever about the loop driving it —
not its setpoint, not its health, and not that it had locked itself out after a
fault. The loop that most needed watching was the one loop with no row.

Three columns need an answer an instrument loop gets for free:

| | |
|---|---|
| `#` | `sw`. It has no loop number — there is no `SETP 5` to send — and a digit would put it in the same namespace as loops the command panel can address |
| `Sensor` | it *does* have one: the recorder's control channel, published by the same name the trace and the readout carry, so the `K` column fills itself by the same lookup every other row uses |
| `Rng` | `n/a`. It genuinely has none: the 218 has no inert half — no loop, no range, one `ANALOG` command whose percentage *is* the power |

`State` carries the **supervisor's** state rather than `OUTMODE?`'s mode —
`tracking`, `idle`, `holding`, `ramping down`, `locked out`. `ramping down` is
never shortened to `ramping`: it is a fault backing the heater off, not a
setpoint traversal. The loop mode (`off` / `manual` / `pid`) is in the hover,
because `idle` alone cannot tell a loop that was never armed from one that was
armed and then held.

The two marks work the same way with two differences, both because the loop is
not an instrument:

- **`Rail` is judged against the supervisor's own authority band**, not against
  99 %. That band is about a percent wide on this cryostat, so the fixed rails
  a heater output uses could never light the mark at all. It is not a per-loop
  knob let in by the back door — no instrument row has one — it is the clamp
  the supervisor is actually enforcing.
- **It is judged on what the loop asked for, not on what it wrote.** The
  written value is quantised to a DAC code and the band re-applied by stepping
  *down* a code, so a saturated loop writes a number strictly below its own
  rail and would never compare equal to it.

`Off SP` uses the loop's own `max_error_k` — "this should only ever be a small
correction" — which is a real threshold in kelvin and exactly what the column
asks for. On the shipped numbers the premise check fires long before the clamp
does, so a *tracking* loop railing is not something to expect: what you will
see instead is the anomaly hold, as `holding` in the State column.

**The row is read, not clicked.** It is the one row the command panel cannot
follow: the software loop takes no setpoint, range or PID command — it takes
`arm` and the panic `hold`, which are buttons of their own. Clicking it leaves
the selection where it was, rather than pointing the panel at a loop it cannot
honour. When health is anything but `ok` the row is coloured like a lit mark,
because both marks go quiet exactly when the supervisor stops trusting its own
measurement — which is the moment the row most needs to catch an eye.

## Themes

The viewer takes its colours from the **Qt palette**, not from a table of
constants, so it follows whatever the desktop is doing — including a switch
made while it is already open.

This was a bug first. The viewer was written on a light desktop and hardcoded
its foregrounds; opened in macOS dark mode it forced `#000000` onto a `#171717`
table base, a contrast ratio of **1.17:1** — not hard to read, invisible. The
same code would have done the same under a dark Windows or KDE theme.

Two rules came out of it, and they are worth keeping when you add a widget:

- **Never paint the normal case.** Ordinary text has no colour of its own; it
  is whatever the palette says. A hardcoded "black" is a bug on a dark theme
  and a hardcoded "white" is the same bug on a light one — the fix is not a
  better constant, it is no constant. `theme.clear_foreground()` is how a table
  item gets back to that.
- **Paint the exceptional case from a pair.** Warnings do need a colour, so
  every semantic name in `lschart/gui/theme.py` has a light value and a dark
  one, both measured. `tests/test_gui_theme.py` computes the contrast ratios
  rather than trusting anybody's eye, against the grounds Qt actually reports,
  with a 4.5:1 floor.

**The chart itself stays light on both themes.** `pg.setConfigOptions(
background="w")` is deliberate: the trace colours are chosen to separate on
white, the cursor readouts and the stat panel are drawn to sit on it, and it is
what the chart looks like if it is printed. A white plot in a dark window is a
deliberate choice, not an oversight — say so before changing it, because the
ten curve colours would all need re-picking.

Related: the **trace toggles** carry their curve's colour as a stripe rather
than as the text of the label. The colour has to match a line drawn on the
white plot, so it cannot be re-themed for a dark panel — and as text several
of them failed on *both* grounds (cyan reaches only 2.26:1 on white).

## The control panel

The **instrument selector** shares a line with the first group's title —
"Setpoint" on the left, `Instrument [box]` on the right — with that group's
border directly beneath and nothing between them. It is one combo box, and the
panel has no rows to spare.

It works by taking the title *off* the first visible group and drawing it in
the selector's row instead. A titled `QGroupBox` draws its title **above** its
frame, so anything placed against the group's widget rectangle leaves the whole
title band visibly empty — that band is what read as a gap. A group with no
title has no band, so its frame starts at its widget top and the row above sits
on the border.

Which group is first depends on the box: a 218 has no loops, so Setpoint is
hidden and the analog group is what shows. The titles are therefore stored
rather than written straight onto the widgets, and two of them change at
runtime (`Heater range (output 2)`, `Analog output 1 (max 70%)`).


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
| **Clear lockout** | always | clear a software loop's fault lockout. Does **not** resume the loop — it stays disarmed until armed. Beside Arm and not in the Panic menu, because it is the first step back toward power and is gated the same way |
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

### Notes under a dead control

A control the recorder will refuse is disabled, and the line under it names the
**config key** and nothing else — `ipc.allow_pid: false`. The reasoning, and
the way that still works, are in the hover. Three lines of prose explaining a
disabled control are read once and then occupy the panel forever; the key is
what somebody acts on.

A note with nothing to say is hidden rather than left blank, because an empty
word-wrapped label still claims a line.

## The Panic menu

**Red, and its own dialog.** The button is the control somebody reaches for
while something is going wrong on a cryostat, so it is findable without being
read, and it is the same red on a light desktop as on a dark one — "the button
that stops it" should not change colour with the theme.

Clicking it opens a **modal**, not a dropdown. A popup is a small target beside
the pointer, and the two things in it are "stop heating this cryostat" and
"freeze it where it is": the wrong one must not sit a few pixels from the right
one. Both are large, separated, and captioned with what they actually do.

**Still three interactions** — open, choose, confirm — which was the point of
the old menu and stays the point here. These are needed almost never and must
not be reachable by accident; the middle one is what a mis-aimed click lands
on, and cancelling costs nothing.

| | |
|---|---|
| **All heaters OFF** | 33x ranges to 0 and 218 analog outputs to 0%, on every writable box, and a software loop **disarmed** first so the zero sticks. Setpoints are not changed |
| **All temperatures HOLD** | every closed loop's ramping switched off (the rate is kept) and its setpoint moved to its own sensor's present temperature; a software loop DISENGAGED, its heater left where it is |

Neither is aimed at the selected instrument. Every other control needs an
argument that means something on one box; these mean "stop", which on a two-box
cryostat had better include the box carrying the sample heater.

**The button sits outside the command group, and that is structural.** Both kinds
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

## The status strip

Along the bottom of the window, spanning it: the **Panic** menu, **Listen to**,
and the link health line. All three are short and wide by nature and were
previously a vertical stack at the bottom of the left panel — the one column
that has no height to spare. Moving them costs the chart a couple of dozen
pixels and gives the panel three rows back.

### Listen to: which clients the recorder obeys

Three tickboxes, and they are the runtime half of `ipc.sources`:

| | |
|---|---|
| **MATLAB** | the `matlab` source label |
| **This viewer** | `lschart-gui` |
| **Other clients** | the overlay's own `default` — everything the policy does not name: the CLI, a second viewer, a script somebody wrote this morning |

"Other clients" is not a client. It is the only way to shut out a label you do
not know in advance, and like every overlay entry it may only **narrow** what
`ipc.sources` already allows — a tickbox the config refuses outright is
disabled and says so, because enabling it needs a config edit and a restart.

**Muted is about listening, never about reading.** `status.json` is a file
anyone may open, so a muted client keeps every "getting" operation it had:
temperatures, the reading table, the marks, the chart. Only commands stop.

Un-ticking this viewer is not a one-way door. The `source` command is exempt
from the policy it edits, so the tickbox that mutes this viewer is the tickbox
that un-mutes it, and the Panic menu keeps working throughout. That is also why
the strip lives **outside** the command group: a Qt child of a disabled parent
is disabled however firmly you enable it.

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

Zoom and pan on a value axis stop at **0–350 K** and **0–100 %** — *unless the
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
not answered from what survived: a quarter second after the span settles, the
viewer re-reads that span from the logs on disk and swaps it in. Zooming out
and back in shows real samples again, at whatever cadence the recorder wrote.
The disk read costs nothing during a gesture, because it waits for the span
to stop moving.

**The status bar says which of three things you are looking at:**

| It says | You are seeing |
|---|---|
| `reading the log…` | the gesture has landed and the disk read is in flight |
| `full resolution` | every sample the logs hold for this window |
| `too wide to read whole · 1 pt / N s` | the window read at a stride, at the spacing shown |

They look identical on screen, and which one is up depends on the width of
the span and on how long this viewer has been running, so it is stated rather
than left to be worked out.

Only the *drawing* is ever coarsened. Cursor statistics and the region export
re-read the log at full resolution whichever the status bar reports, so a
coarse chart is never a coarse measurement.

### A zoom costs the span, not the archive

The re-read skips any log whose own first and last rows fall outside the
span — read from the rows, not from the filename, which is the same evidence
a full parse would have produced and two lines of it instead of a day's.
Without that skip the cost was the whole archive: on the LTSPM3 machine a
one-hour zoom took 0.9 s against a week of logs and 10.2 s against three
months, for the same 1950 rows recovered. It is now flat at about 135 ms.

A span so wide that re-reading it whole would exceed
`CsvTail.SPAN_READ_BUDGET_BYTES` (32 MiB, a bit over three days of this
recorder's logging) is read at a **stride** instead — one row in n, chosen so
the parse stays bounded and so no column comes back with more than
`SPAN_POINT_BUDGET` points, which is about a dozen per pixel on a wide
window. The whole span is covered; the resolution is what gives.

**It is a stride and not a refusal, and that distinction is the bug this
replaced.** The budget originally declined such a span outright, on the
understanding that the overview would be drawn instead. The overview does not
hold the archive — it holds the last `BACKFILL_COVERAGE_S` of it, 49 hours by
default (see [History across midnight](#history-across-midnight)). A window
reaching further back than that fell between the two bounds and was drawn as
**nothing at all**, with the status bar reporting an overview that was not on
screen: on a five-day window over a real archive, three of the five days were
blank. Losing resolution is visible and is now said out loud. Losing the day
was not.

## Measuring a region: the cursors

`Cursors` puts two vertical lines on both panels, at the thirds of the window.
Left-click or drag on either panel moves **whichever is nearer** to the
pointer — nearest rather than alternating, because alternating means
remembering which one moved last and getting the wrong edge half the time.

Between them, an in-plot panel reports **per trace: mean, standard deviation
and Δvalue**, and **once for the region, Δtime**.

It is laid out as a table -- headed once, one row per trace, and the numbers
right-aligned so the decimal points stack. Columns of padded spaces would not
line up: the panel is drawn in the UI's proportional font, where two spaces
are not a column.

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
