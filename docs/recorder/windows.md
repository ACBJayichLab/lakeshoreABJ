# Windows deployment

**This is the real target.** Development is macOS; everything below that is
not marked as verified is an expectation, not a measurement.

First deployed to the LTSPM3 rig on **2026-08-24**: Windows 10 Pro 19045, an
NI PXI-GPIB board, a 218 at `GPIB0::15` and a 336 at `GPIB0::12`, recording a
cold cryostat at a 2 s cadence. What that run settled is marked *verified*
below. It also found one real bug — see **The single-instance lock** — which
had made `runtime.single_instance` ineffective on Windows.

## Python version

`pyproject.toml` asks for **>= 3.11**. The LTSPM3 machine had only **3.10.0**,
and installing a second Python onto a machine running a live experiment is not
a free action. The full suite (257 tests) passes on 3.10.0 — nothing in the
codebase uses a 3.11-only feature — so it was installed there with:

```
python -m pip install -e ".[dev,gui]" --ignore-requires-python
```

That is a **deployment** decision, not a support claim: the metadata still says
3.11 because that is what is actually tested on. If you take the same escape
hatch, run `pytest` afterwards on that interpreter, which is what makes it
safe rather than hopeful.

## Install

Same as [install.md](install.md), plus:

- **A 335/336 on USB** needs the vendor USB driver so the box appears as a COM
  port. Lake Shore ships VID `0x1FB9` through a Silicon Labs CP210x bridge.
- **GPIB** needs the **NI-VISA runtime** before `pyvisa` can see `GPIB0::` at
  all, plus the driver for the interface card.

Prefer `driver: lakeshore` for anything on a COM port: it needs no VISA runtime,
which removes the whole NI install from the deployment.

Match a USB instrument on `serial_number` rather than `com_port`. A device that
re-enumerates comes back on a **different COM number and the same serial**.

## Running it unattended

Not yet decided. The two candidates are a **Task Scheduler** entry ("run
whether user is logged on or not", restart on failure) or a service wrapper
such as NSSM. Whichever is chosen, the recorder must:

- start after the USB/GPIB stack is up;
- be the only instance — `runtime.single_instance` handles the race, but a
  supervisor that restarts it must not race *itself*;
- log somewhere durable;
- **wait a moment before restarting a killed recorder.**

That last one is measured, not theoretical. The kernel releases the lock when a
holder dies, but it does so during process *teardown*, which lags the exit code
by a few milliseconds — long enough that an immediate retry is refused roughly
one attempt in three. A supervisor configured to restart instantly on failure
can therefore be turned away by the lock of the process it just watched die,
and `run` exits 2 without retrying.

So give a restart policy a **delay of a second or two**, and prefer a
supervisor that keeps retrying over one that gives up after a single failed
start. `tests/test_lock.py::test_a_killed_process_releases_the_lock` pins the
underlying behaviour.

## The single-instance lock

**VERIFIED, after a fix.** `msvcrt.locking` locks a byte range starting at the
*current file position*, and the lock file is opened `"a+"`, which leaves that
position at end-of-file. Every holder therefore locked a **different** byte,
and no second instance ever collided with the first — on Windows,
`runtime.single_instance` was not doing its job at all.

What made it look like it worked was an accident downstream: the second process
went on to truncate the file and write its own record, and *that* write failed
with a `PermissionError`, because the first holder's lock covered byte 0. So a
second recorder was refused — by a confusing error, after erasing the running
recorder's own diagnostics.

The lock is now taken on a fixed byte far past the record (`_LOCK_OFFSET`),
which fixes both halves: holders contend for the same byte, and because a
Windows lock is *mandatory* rather than advisory, keeping it clear of the
record is what lets a refused starter still read who holds it.

Confirmed on the rig: a second `run` against a live recorder exits 2 with
`another lschart instance already holds data/ltspm3.lock (pid ..., since ...)`,
and does so **before** any transport opens, so it never touches the bus. A
killed holder's lock is released by the kernel; `tests/test_lock.py` now
exercises that on Windows rather than skipping it.

## Three Windows-specific things to verify

1. **`os.replace` over an open `status.json`.** On Windows, replacing a file
   another process has open can fail with a sharing violation. **Not
   reproduced** in the first run — the viewer polled `status.json` while the
   recorder rewrote it every 2 s, and no cycles were dropped — but that is a
   short observation, not a clean bill of health.

   Note the handling is weaker than this page used to claim. `_write_status`
   **discards** the writer's return value, and the failure is logged at
   `DEBUG`. It is counted nowhere, and at the default `INFO` it is silent. If
   you are ever chasing a gap in a status feed, raise `log_level` to `DEBUG`
   before concluding it did not happen.
2. **The ~15 ms clock resolution** behind the command sequence number. The
   sequence tie-break exists precisely because `time.time()` is coarse there;
   confirm that two commands queued back to back really do share a millisecond
   and really are applied in order. **Still unverified** — the first
   deployment records only, with `accept_commands: false`.
3. **`movefile` from MATLAB is a rename**, not a copy-then-delete. The command
   spool depends on the rename being what makes a file visible. **Still
   unverified**, for the same reason.

## Paths

Use forward slashes or double backslashes in YAML. Keep `recorder.directory`,
`ipc.directory` and `runtime.lock_path` on a **local** disk — the status file is
rewritten every second and a network share turns that into a latency problem
and a sharing-violation problem at once.

If MATLAB and the recorder are on different machines, share the data directory
rather than the port, and expect the recorder's own writes to stay local.
