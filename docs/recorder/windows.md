# Windows deployment

**This is the real target, and it is the least-tested part of the system.**
Development is macOS. Everything below that is not marked as verified is an
expectation, not a measurement.

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
- log somewhere durable.

## Three Windows-specific things to verify

These are known, handled, and **unobserved**. Confirming them is the point of
the first deployment.

1. **`os.replace` over an open `status.json`.** On Windows, replacing a file
   another process has open can fail with a sharing violation. This is handled
   — the failed write is counted and logged, and the next cycle rewrites it a
   second later — but it has never been *seen*, and it is worth knowing whether
   it happens once an hour or never.
2. **The ~15 ms clock resolution** behind the command sequence number. The
   sequence tie-break exists precisely because `time.time()` is coarse there;
   confirm that two commands queued back to back really do share a millisecond
   and really are applied in order.
3. **`movefile` from MATLAB is a rename**, not a copy-then-delete. The command
   spool depends on the rename being what makes a file visible.

## Paths

Use forward slashes or double backslashes in YAML. Keep `recorder.directory`,
`ipc.directory` and `runtime.lock_path` on a **local** disk — the status file is
rewritten every second and a network share turns that into a latency problem
and a sharing-violation problem at once.

If MATLAB and the recorder are on different machines, share the data directory
rather than the port, and expect the recorder's own writes to stay local.
