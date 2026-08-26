# Vocabulary

One concept, one word. These are the terms this repository uses, in docs,
code identifiers, config comments and commit messages alike. When you write,
pick from this table rather than reaching for a synonym.

| Term | Means |
|---|---|
| **cryostat** | the physical setup — the thermometers, the heater, the plumbing. There is exactly one this software is calibrated against: the LTSPM3. |
| **recorder** | the process (`lschart run`) that owns the port, polls the instruments every cycle, and writes the CSV. |
| **viewer** | the strip-chart GUI process (`lschart-view`). A separate process that reads files; it never touches the port. |
| **cycle** | one acquisition pass: read everything → apply any commands → write `status.json`. Not a "poll" or a "sample". |
| **command spool** | the directory clients drop command files into and the recorder consumes. It is maildir-*style*; say "spool", not "maildir". |
| **instrument** (or **box**) | one physical Lake Shore device. The code behind one is its **driver**; how it is reached is its **transport**. |
| **thermal response** | the measured behaviour of heater power → temperature: the steady-state curve and the time constants. The simulator and the feedforward share one copy of it. |
| LTSPM vs **LTSPM3** | LTSPM is the team. LTSPM3 is the cryostat. Anything about hardware, wiring or measured numbers is LTSPM3. |

## Why these words

- **cryostat**, not anything else: it is the physical thing, and both programs
  exist to serve it. Generic statements ("any cryostat") stay true for a
  coworker's setup; LTSPM3-specific statements are marked as such.
- **recorder / viewer**: two processes with different jobs and different
  lifetimes. "GUI" alone does not say which one you mean.
- **cycle**: the recorder's heartbeat has read *and* write *and* status phases;
  "poll" describes only the first of the three.
- **command spool**: names the mechanism clients use. "Maildir" is an
  implementation detail borrowed from email; useful once as a description,
  confusing as a name.
- **thermal response**: says what is actually modelled — how temperature
  responds to heater power — without importing control-engineering jargon.

## Naming conventions this supports

- Units travel inside names: `_k` kelvin, `_pct` output percent, `_s` seconds.
- Classes describe what a thing *is* (`SimulatedCryostat`,
  `FirstOrderResponse`, `CommandSpool`); if a name needs "manager" or "helper"
  to make sense, the abstraction is wrong.
