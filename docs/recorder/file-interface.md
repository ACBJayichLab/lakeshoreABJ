# The file interface

**The recorder owns the port. Everyone else goes through files.**

This is not a preference. A Windows COM port has exactly one holder, so MATLAB
*cannot* open COM10 while the recorder has it, and two processes on one GPIB
board interleave transactions into garbled replies. Talking through files is
the only shape that works at all.

## Why files rather than a socket

A socket puts a connection state machine inside the process that must never
die, and its failure is quiet — a dead server thread keeps recording perfectly
while silently ignoring every setpoint.

The file version has no connection state. There is no session to open, nothing
to close, and nothing to get out of step. **Python never learns that MATLAB
exists**, which is the strongest available form of "do not crash if the client
does".

## The files

Both live in `ipc.directory` (`data/` by default), alongside the CSV, so one
directory is the whole interface.

| | |
|---|---|
| `status.json` | rewritten **in full** every cycle via `os.replace`, so a reader sees one cycle or the other and never a torn mixture |
| `commands/` | the command spool, maildir-style. A client writes `<stem>.json.tmp` and renames it to `<stem>.json`; the recorder globs `*.json`, applies, deletes. No locking, no contention |
| `sources.json` | optional, and the only one an **operator** writes: which clients are switched off right now. Absent by default. See [a sixth gate](#a-sixth-gate-on-a-different-axis) |

### `status.json`

Carries: schema version, pid, host, config path, wall clock and uptime, cycle
count and dropped cycles, every channel (`name`, `kelvin`, `sensor_units`,
`validity`, `usable`, `status`), auxiliary values (setpoints, heater percents),
errors, per-link health (`up`, `consecutive_failures`, `reconnects`,
`last_error`, `writable`) and capability (`loop_numbers`, `heater_outputs`,
`analog_output`, `max_output_pct`), the per-link **loop table** described
below, the recorder's path and row count, control state if there is one, and
command acknowledgements.

#### `links[].loops` — the loop table (schema 2)

One entry per control loop, each carrying every one of these keys, `null`
where the recorder has nothing to say:

| Field | |
|---|---|
| `loop` | the loop number |
| `sensor` | the display name of the input it reads, from `OUTMODE?` |
| `input` | that input's letter |
| `mode`, `mode_code` | `closed loop`, `zone`, `open loop`, `monitor`, `warmup`, `off` — and the number behind it |
| `heater_output` | the heater output it drives, or `null` for an analog-only output (a 336's 3 and 4) |
| `setpoint_k`, `output_pct`, `range` | where it is going, what it is putting out, and how much power it may use. `range` is `null` where there is no range to have |
| `threshold_k` | how far from setpoint still counts as settled, from `loop_thresholds`. `null` when none is configured |
| `ramping` | `RAMPST?` — still traversing to a new setpoint |

**Where these come from.** The bindings are the *instrument's* answer
(`OUTMODE?`), re-read on a slow cadence; the numbers that move come from the
same aux block the CSV carries. They are joined in one place so a client
cannot read the setpoint twice and get two answers.

**Schema 2 moved one key.** `links[].loops` used to be a bare list of loop
numbers; it is now this array of objects, and the plain list lives at
`links[].loop_numbers`. A client written against schema 1 should degrade —
offer the loops it finds under either key, and show no loop table rather than
inventing rows. `capabilities()` and `loop_rows()` in `lschart/gui/source.py`
are the worked example.

**Arrays, not objects.** Channels are `[{"name": ..., "kelvin": ...}, ...]`
rather than `{"Rad Shield": 295.3}`, because MATLAB's `jsondecode` passes
object *keys* through `matlab.lang.makeValidName` and silently mangles them. A
name that lives in a *value* survives verbatim. Every element carries the same
fields, which is what makes `jsondecode` return a struct array rather than a
cell array of dissimilar structs.

**A failed write is not an error.** On Windows, replacing a file another
process has open can fail with a sharing violation. Nothing needs doing: the
next cycle rewrites it a second later. So a failed write is counted and logged
at DEBUG, never raised — an IPC convenience must not be able to stop the
recording it reports on.

### `commands/`

Handled on the acquisition thread, because that thread owns the bus.
`max_commands_per_cycle` (4) bounds how much bus time one cycle spends on them.

| Command | |
|---|---|
| `ping` | proves the whole path, touching no instrument |
| `setpoint` | `loop`, `kelvin` |
| `ramp` | `loop`, `rate_k_per_min` (0 disables) |
| `range` | `output`, `value` 0–3 — **applies power** on a 33x |
| `analog` | `percent` — **applies power** on a 218; there is no inert half to it |
| `heaters_off` | every heater on every writable instrument to zero |

`heaters_off` is the only command that is not aimed at one box. Every other
handler takes an argument that means something on exactly one instrument;
this one takes none and means "stop heating", which on a two-box cryostat had
better include the box carrying the sample heater. A panic button that leaves
one heater running is worse than no panic button, because it will be believed.
Instruments the recorder may not write to are skipped and named in the reply,
rather than failing the whole command — on a shared cryostat a read-only box
is somebody else's, and refusing because of it would leave *our* heaters on.

## Four properties a naive drop-box does not have

All four are load-bearing, and three exist because of a specific way this goes
wrong.

1. **Commands expire** (`ipc.command_ttl_s`, 30 s). Without this a recorder
   that was down for an hour comes back, finds an hour of queued setpoints, and
   walks a live cryostat through every one of them. The last one would even be
   *correct*, which is what makes it dangerous: **the hazard is the traversal,
   not the destination.**
2. **Commands are ordered** — the filename is `<ms>-<seq>-<id>.json`. The
   sequence number is not decoration: Windows resolves `time.time()` to about
   15 ms, so a script queueing a setpoint and a heater range back to back
   stamps both with the same millisecond. Without the tie-break they would be
   applied in whichever order their random ids happened to sort.
3. **Commands are acknowledged** — each carries an `id` that reappears in
   `status.json` with the outcome. Deleting the file cannot be the
   acknowledgement, because the file is deleted whether the command succeeded
   or was refused, so its absence tells a client nothing.
4. **A clock that disagrees is caught.** A command stamped in the future by
   more than the TTL is refused rather than being treated as fresh forever.

## The file door is not a back door

A command arriving by file passes exactly the interlocks a command typed at the
CLI passes, in the same order, with the same message.

```
transport.read_only      refuses at the byte level, below any policy
allow_writes             per-instrument driver policy      (default OFF)
ipc.accept_commands      is this recorder listening at all (default OFF)
ipc.allow_heater_range   may a file raise a 33x heater range   (default OFF)
ipc.allow_analog_output  may a file raise a 218 analog output  (default OFF)
```

The last two are one gate each rather than one gate for both, because they are
different commands on different boxes. On a cryostat where this program drives its
own sample heater but only *watches* a controller holding something else, one
of them wants to be open and the other emphatically does not.

Turning a heater **off** — a range to 0, or an analog output to 0% — never
needs either of the last two: the safe direction is always available.

### A sixth gate, on a different axis

Those five all answer the same question from different heights: *may this
action happen at all*. None of them can say "the operator at this terminal may
drive the cryostat, the analysis script may not", because none of them knows
there is more than one client.

```
ipc.sources              may this CLIENT ask                (default: no policy)
<ipc.directory>/sources.json   the same, narrowed at runtime, never widened
```

Every command carries a `source` label — `matlab`, `lschart-gui`,
`lschart-cli/<pid>` — and the policy is keyed on the part before the first `/`,
because no fixed key in a config file could ever match a pid.

```yaml
ipc:
  sources:
    default: false          # anything not named below
    matlab: true
    lschart-cli: true
```

Leave `sources:` out entirely and there is no policy: every source may ask, and
the five gates above are the whole of it. Write it and `default:` is **false**
unless you say otherwise — naming your clients is how you say you have thought
about the list, and a typo in one of those names has to fail closed.

**This is an interlock against habit and mistake, not against malice.** `source`
is self-declared in the command file; anything that can write to the spool can
write any label. That is the accepted trade — the spool is already a directory
on a machine you trust, and keys and signatures would buy nothing the
filesystem's own permissions do not, at the cost of the protocol no longer
being readable by forty lines of MATLAB.

### `sources.json` — switching a client off without stopping the recorder

A small file beside `status.json`, re-read every cycle:

```json
{"lschart-gui": false}
```

It can only ever **narrow** what `ipc.sources` permits. Granting something the
config refuses means editing the config and restarting, which is the point: a
restart always returns to the audited ceiling.

It is a file and not a command kind, deliberately. A *command* that disabled the
viewer would leave the viewer with no way to re-enable itself — the one client
that needs to undo it is the one it just silenced. A file can be edited by hand,
by anything, and never requires stopping the recorder and making it drop the
port.

Delete the file (or the entry) to clear a lockout. A file caught mid-edit is a
torn read: the recorder keeps the last overlay it managed to parse rather than
widening the policy, because half a file is not permission.

**The panic commands are exempt**, and are the only things that are —
`heaters_off` bypasses the source policy *and* the two power gates above. It
does not bypass `ipc.accept_commands`, `allow_writes` or `transport.read_only`:
a box configured read-only stays read-only and is named in the reply. The
exemption belongs to the command **kind**, not to the viewer — the recorder
cannot tell a menu press from a script, so MATLAB's `heatersOff()` gets it too.
That is deliberate: an automated abort is a large part of why a panic command
exists.

`lschart -c CONFIG status` prints the effective policy, and `check` prints the
configured ceiling before anything is running.

A refusal is not a crash. A driver limit saying no (`max_setpoint_k`,
`max_output_pct`, a loop the box does not have) comes back as
`refused: <reason>` and is logged at WARNING, not as an ERROR with a traceback
— on a live cryostat, an operator's typo must not look like a fault in the log.

## Switching it on

```yaml
ipc:
  enabled: true               # writes status.json -- needed to READ
  accept_commands: true       # reads commands/    -- needed to WRITE
  allow_heater_range: false   # may a file turn a 33x heater ON
  allow_analog_output: false  # may a file drive a 218 output above 0
  sources: {}                 # WHO may ask; empty means everyone
```

Reading and commanding are separate permissions, and commanding *also* needs
`allow_writes: true` on the instrument itself.

## One cycle of lag on the readback

Order within a cycle is read → record → apply commands → write status. So the
`aux` block in the status file written immediately after a command still
carries the value read *before* it was applied; the next cycle catches up. The
acknowledgement itself does not lag — it reports the value the driver read back
from the instrument to confirm the write.

## Clients

| | |
|---|---|
| **MATLAB** | [`matlab/README.md`](../../matlab/README.md) — `LakeShore.m`, plus `selftest.m` |
| **The GUI** | [gui.md](gui.md) — just another client, with no privileges MATLAB lacks |
| **The CLI** | `lschart status` and `lschart send` speak the same protocol |
| **Anything else** | it is JSON in a directory; there is no library to link against |

Prove the path before involving a client at all:

```bash
python -m lschart -c CONFIG status          # read status.json
python -m lschart -c CONFIG send ping       # round-trip a command
```

## One measured bite: MATLAB reseeds its RNG every session

`randi` gives the *same* sequence at every MATLAB startup, so ids built from it
repeat across sessions, and `await()` then matches an acknowledgement left in
the recorder's ring by the **previous** session and reports its outcome as this
command's. Observed, not theorised: a `setSetpoint` reported `pong`.

So `LakeShore.m` derives ids from `tempname` (documented unique, and it does not
disturb the user's RNG state the way `rng('shuffle')` would), *and* `await`
ignores any acknowledgement stamped before the command was issued. Belt and
braces, because the failure it prevents is a confident confirmation of
something that never happened.

**If you write your own client, do the same thing.**
