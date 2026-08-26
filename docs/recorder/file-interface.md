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

## The two files

Both live in `ipc.directory` (`data/` by default), alongside the CSV, so one
directory is the whole interface.

| | |
|---|---|
| `status.json` | rewritten **in full** every cycle via `os.replace`, so a reader sees one cycle or the other and never a torn mixture |
| `commands/` | a maildir. A client writes `<stem>.json.tmp` and renames it to `<stem>.json`; the recorder globs `*.json`, applies, deletes. No locking, no contention |

### `status.json`

Carries: schema version, pid, host, config path, wall clock and uptime, cycle
count and dropped cycles, every channel (`name`, `kelvin`, `sensor_units`,
`validity`, `usable`, `status`), auxiliary values (setpoints, heater percents),
errors, per-link health (`up`, `consecutive_failures`, `reconnects`,
`last_error`, `writable`), the recorder's path and row count, control state if
there is one, and command acknowledgements.

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
