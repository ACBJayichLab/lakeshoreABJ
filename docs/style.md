# Vocabulary

One concept, one word. These are the terms this repository uses, in docs,
code identifiers, config comments and commit messages alike. When you write,
pick from this table rather than reaching for a synonym.

| Term | Means |
|---|---|
| **cryostat** | the physical setup — the thermometers, the heater, the plumbing. There is exactly one this software is calibrated against: the LTSPM3. |
| **recorder** | the process (`lschart run`) that owns the port, polls the instruments every cycle, and writes the CSV. |
| **viewer** | the strip-chart GUI process (`lschart-view`). A separate process that reads files; it never touches the port. |
| **monitor** | the judge (`python -m ltspm3.monitor`). A separate process that reads the recorder's files and says whether the cryostat is behaving typically. It reports and never commands. Not a "watchdog" -- a watchdog acts. |
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

# Numbers, dates and history in comments

Every measured number has exactly **one home**. Everywhere else it is a
pointer to that home, never a copy. A copy is a second place that goes stale
the day the measurement is repeated, and this repository has been bitten by
that in configs, docstrings and plans alike.

| kind of number | home |
|---|---|
| Jeff's requirements and the bench results that graded them | `docs/ltspm3/requirements.md` |
| thermal measurements: tau, gain, exponents, noise, ramp-down time | `docs/ltspm3/thermal-response.md` (prose); `ltspm3/model/` (code, with `_fitted_table.py`'s generated header for the fit) |
| the sensor glitch statistics | `docs/ltspm3/safety.md`; `ltspm3/control/coherence.py` in code |
| a threshold a test enforces | the test, with one sentence saying why the bound is what it is |
| what a config knob does and the bound that fixes its value | the knob's own comment, in one to three lines, pointing at the home above |
| the cryostat's wiring, addresses and box-level changes | `docs/ltspm3/cryostat.md` |
| point-in-time state: what is armed, what happened today, what is next | `HANDOFF.md` only, archived under its date when superseded |
| incidents and audits | `archive/` and git; a comment may name one in a sentence, not narrate it |

Rules that follow:

- **No dated current state in a durable file.** CLAUDE.md, `docs/`, `plans/`,
  configs and code are read months later; "today", "since 2026-…", "this file
  has been armed" belong in `HANDOFF.md`.
- **A comment says why the code is as it is, not what it used to be.** Deleted
  fields, superseded values and "until the fix of …" get one clause at most.
  The commit message and `archive/` hold the story.
- **Config comments are not documentation.** One to three lines per knob: what
  it is, the bound that fixes its value, where the measurement lives. No tables.
- **Generic docs (`docs/recorder/`) carry no LTSPM3 numbers or names.**
- **A test's threshold is explained, not decorated.** Say which requirement or
  rule the bound comes from; do not restate the measured value the assertion
  already checks.
- **Retired conclusions are struck, not left beside their replacement.** A
  reader should not have to know which of two paragraphs is current.
