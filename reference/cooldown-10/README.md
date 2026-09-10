# Cooldown 10, as one archive

**Cooldown 10 began 2026-07-15 and is still running.** Everything in this
directory is that one cooldown: the pre-Python chart-recorder half, and the
Python recorder's own logs. It is not several cooldowns and must not be
described as such -- see `REFIT_PLAN.md` §2.3.

Three tables, **non-overlapping**, together covering the cooldown end to end.
Bytes live exactly once: any window anybody wants is a slice of one of these,
named in the manifest, never a second copy on disk.

| file | span | rows | segments |
|---|---|---|---|
| `cd10_20260715_prepython.csv.gz` | 2026-07-15 20:56 -> 08-20 14:49 | 298,617 | 5 |
| `cd10_20260824_recorder.csv.gz` | 2026-08-24 17:10 -> 09-04 12:07 | 461,849 | 4 |
| `cd10_20260904_recorder.csv.gz` | 2026-09-04 23:38 -> 09-09 23:59 | 216,582 | 2 |

All three carry the same twelve columns: `Timestamp, t_s, segment, Sample,
Coldplate, Magnet, RAD SHIELD, THE CHONKE, 1st Stage, 2nd Stage, u_pct, note`.

`segment` increments at every recording gap and **is the column that matters**:
fit each segment as its own trajectory. Integrating an ODE across a gap
converges on a number anyway, which is the failure it exists to prevent.

```
cd10_20260715_prepython   seg 0  07-15 20:56 -> 07-20 17:02   116.11 h  104,499
                          seg 1  07-23 10:26 -> 07-23 11:07     0.67 h      303
                          seg 2  07-23 11:25 -> 07-25 20:09    56.74 h   25,533
                          seg 3  07-25 20:16 -> 07-31 20:42   144.44 h   65,000
                          seg 4  08-08 15:56 -> 08-20 14:49   286.89 h  103,282
cd10_20260824_recorder    seg 0  08-24 17:10 -> 08-28 11:11    90.00 h  162,006
                          seg 1  08-28 12:50 -> 08-28 13:07     0.28 h      498
                          seg 2  08-28 13:08 -> 08-28 13:36     0.46 h      822
                          seg 3  08-28 14:16 -> 09-04 12:07   165.85 h  298,523
cd10_20260904_recorder    seg 0  09-04 23:38 -> 09-09 20:12   116.57 h  209,820
                          seg 1  09-09 20:14 -> 09-09 23:59     3.76 h    6,762
```

## The two boundaries, and why they are where they are

**08-20 -> 08-24** is a real four-day recording gap. The heater went to zero
somewhere inside it and the cryostat sat at base: the last pre-Python row is
137.44 K at 65.934 %, the first recorder row is 4.74 K at 0 %. **The cryostat
stayed cold**, which is why this is one cooldown and not two.

**09-04 12:07:16** is the Coldplate recalibration cutover, and it is also a
recording gap (12:07 -> 23:38). Until that moment the 218 carried X186276's
curve on input 2 where the Coldplate is X186279.

- The **first two** tables are built from `data/coldplate-recal/`, so their
  `Coldplate` column is already remapped kelvin -> resistance -> kelvin onto the
  correct curve. See `lschart/tools/recalibrate.py` and
  `docs/ltspm3/cryostat.md`.
- The **third** is post-cutover and is the recorder's own numbers, untouched.

No table spans the cutover, so no table mixes two calibrations.

## Caveats that travel with the data

- **The pre-Python heater column is reconstructed**, not read back: a
  zero-order hold on the `ANALOG` commands in the log's Notes. `Coldplate` is
  blank for the first hour and `u_pct` for the first 14.25 h.
- **The aux thermometers stop on 2026-07-23.** `RAD SHIELD`, `THE CHONKE`,
  `1st Stage` and `2nd Stage` are populated only 07-15 -> 07-23 in the
  pre-Python table -- the 336 stopped logging -- and are complete in both
  recorder tables. Anything regressed on them can cover 28 of the 55 days at
  most. `THE CHONKE` reads 289.999-290.000 throughout: a held setpoint, no
  signal.
- **`cd10_20260904_recorder` contains the 2026-09-09 18:06 power transient**,
  inside segment 0 -- the recording is continuous across it. At a fixed
  64.0154 % the cold-head channels stepped (1st Stage -0.080 K, 2nd Stage
  -0.019 K, Coldplate -0.0076 K) and the sample fell 0.37 K over the following
  three hours, flat again by 21:20. The segment split at 20:12:52 -> 20:14:38 is
  a separate 106 s recording gap, unrelated. `mask-20260909-180604` in the
  manifest is that window; the start is pinned at **18:06:04**, the first 60 s
  Coldplate mean more than 3 sigma below the preceding five hours.
- The cryostat **warms +0.167 K/day at fixed heater output** across this
  archive. Two anchors taken weeks apart are not two measurements of the same
  cryostat. `REFIT_PLAN.md` §2.3.

## How these were built

```bash
python -m lschart.tools.fit_table "data/coldplate-recal/cd10/cd10_*.csv"              -o reference/cooldown-10/cd10_20260715_prepython.csv
python -m lschart.tools.fit_table "data/coldplate-recal/recorder/ltspm3-heater_*.csv" -o reference/cooldown-10/cd10_20260824_recorder.csv --rename "Cold Head=Coldplate,Shield=Magnet"
python -m lschart.tools.fit_table "data/ltspm3-heater_2026-09-0[456789].csv"          -o reference/cooldown-10/cd10_20260904_recorder.csv
gzip -9 -n *.csv
```

Two things in the middle command are not decoration.

Glob `ltspm3-heater_*.csv` and **not** `ltspm3_*.csv`: the latter is a different
recorder instance whose logs overlap these in time.

And **`--rename` is required**, because the 218's inputs 2 and 3 were relabelled
at the 2026-08-26 part-roll -- `Cold Head` -> `Coldplate` and `Shield` ->
`Magnet`, values continuous across the boundary to 2 mK. Built without it this
table has four half-empty columns instead of two full ones: 83,215 of its
461,849 rows carry the cold end under the old name and `Coldplate` blank, so
every fit reading `Coldplate` silently loses the first two days. **It was built
that way once** and the first thing that read it found the hole; the rename is
about the 08-26 relabelling and is a different event from the 09-04 input move,
which needs none because the name did not change.

`data/` is gitignored and exists only on the cryostat machine, which is why
these are committed despite being derived -- the same reasoning as
`analysis/README.md`'s, one level up.

## `segments.csv` -- the manifest

239 named windows in the three tables: which stretch is a long settled hold,
which is a driven step worth a time constant, which is a trajectory for the ODE,
and which is masked off and why.

| | |
|---|---|
| `kind` | `jump` a driven step · `hold` a dwell over 8 h · `trace` a trajectory for the ODE · `mask` a window nothing may be fitted from |
| `use` | `tau` believe its time constant · `steady` believe only where it was heading · `ode` integrate it · `excluded` the grader said no, and `quality` says which test it failed |
| `quality` | `tau` · `steady` · `unsettled` · `over-extrapolated` · and for authored rows `primary` / `transient` |

**The file is not hand-written and it is not machine-written.** Three things in
it are a human's -- the `mask` rows, the `trace` rows and the `note` column --
and everything else is computed from the archive by `analysis/steps.py`'s dwell
finder and grader. `analysis/curate.py --propose` recomputes the derived half
and prints a diff; on a clean tree it prints none. A change to the finder or to
one of its constants therefore arrives as a diff somebody has to look at,
which is the whole point:

```bash
python analysis/curate.py --propose      # diff against the committed manifest
python analysis/curate.py --propose -v   # ...with every window's grading numbers
python analysis/segments.py              # validate it and print it
python analysis/segments.py --tables     # the archive's segment structure
```

**Masks are an input to the proposal, not an output of it.** A dwell that would
cross a mask is cut at its edge and a dwell inside one is dropped, which is what
lets `--propose` be a regression check and still respect a judgement. There is
one mask so far -- the 2026-09-09 18:06 power transient -- and cutting there is
what turns 26 h of hold into an anchor: read straight through the transient the
same stretch fits a 24-day pole and grades as nothing.

What it comes to, against the five overlapping tables it replaces:

| | |
|---|---|
| dwells found | 234 -> 236 |
| usable anchors | 111 -> **111**, 109 inside 4-200 K, 36 with a believable tau |
| reproduction | 103 of the 105 shared graded anchors agree to **3 nK** |

The differences are all boundary effects, all in the archive's favour, and each
one has a `note` on its row. Two matter. The sweep's **opening** hold: the
region export starts 3.3 h into it, so the pole fit had only drift to work with
and returned tau = 19 days, reach 0.1, `T_inf` 180.07 K -- against 26.10 h,
tau = 1001 s, reach 94 and 180.563 K here. And the **09-09 hold at 64.015 %**,
which nothing versioned before 2026-09-10 saw past 16:16: 24.47 h and 118.535 K
then, 26.30 h and 118.570 K now, cut at the transient.

Read the manifest by `use`. The 126 `excluded` rows are there so the accounting
is complete -- every constant-heater dwell in the cooldown has a row and a
verdict -- and so that a grader change shows up as a verdict flipping rather
than as a line appearing.
