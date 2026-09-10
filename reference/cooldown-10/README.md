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
| `cd10_20260904_recorder.csv.gz` | 2026-09-04 23:38 -> 09-09 22:08 | 213,225 | 2 |

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
                          seg 1  09-09 20:14 -> 09-09 22:08     1.89 h    3,405
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
  -0.019 K, Coldplate -0.0076 K) and the sample fell 0.306 K over the following
  hours. The segment split at 20:12:52 -> 20:14:38 is a separate 106 s
  recording gap, unrelated.
- The cryostat **warms +0.167 K/day at fixed heater output** across this
  archive. Two anchors taken weeks apart are not two measurements of the same
  cryostat. `REFIT_PLAN.md` §2.3.

## How these were built

```bash
python -m lschart.tools.fit_table "data/coldplate-recal/cd10/cd10_*.csv"          -o reference/cooldown-10/cd10_20260715_prepython.csv
python -m lschart.tools.fit_table "data/coldplate-recal/recorder/ltspm3-heater_*.csv" -o reference/cooldown-10/cd10_20260824_recorder.csv
python -m lschart.tools.fit_table "data/ltspm3-heater_2026-09-0[456789].csv"      -o reference/cooldown-10/cd10_20260904_recorder.csv
gzip -9 -n *.csv
```

Glob `ltspm3-heater_*.csv` and **not** `ltspm3_*.csv` in the middle one: the
latter is a different recorder instance whose logs overlap these in time.

`data/` is gitignored and exists only on the cryostat machine, which is why
these are committed despite being derived -- the same reasoning as
`analysis/README.md`'s, one level up.

## Not here yet

`segments.csv`, the manifest of curated windows -- the point of the
reorganisation, and the next thing to write. Until it exists, `analysis/` is
still reading the old overlapping tables in `../heater-calibration/`. See
`REFIT_PLAN.md` §5.
