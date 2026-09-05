# Sensor calibration curves

The `.340` files Lake Shore ships with each thermometer: the breakpoint table
the box interpolates to turn a resistance into a kelvin. They are here because
**a log is meaningless without knowing which of these was loaded when it was
written** — the instrument records kelvin and keeps no note of the curve it
used, so the curve is the only thing that makes an old reading recoverable.

Copied from `Desktop/heater calibration curve/` on the cryostat machine, byte
for byte. Vendor files; nothing here regenerates and nothing edits them. Only
the `.340` is kept — the `.pdf`, `.tbl`, `.cof` and the rest are the same
calibration in formats no code here reads, and the PDFs alone are 5 MB.

| File | Serial | Model | Breakpoints | Range |
|---|---|---|---|---|
| `X186276.340` | X186276 | CX-1050-**CU**-HT-1.4L | 148 | 1.196 – 330.324 K |
| `X186279.340` | X186279 | CX-1050-SD-HT-1.4L | 149 | 1.201 – 330.010 K |
| `X186275.340` | X186275 | CX-1050-SD-HT-1.4L | 147 | 1.048 – 330.039 K |
| `X189259.340` | X189259 | CX-1050-SD-HT-1.4L | 148 | 1.197 – 330.072 K |
| `X207058.340` | X207058 | CX-1030-CU-HT-1.4L | 145 | 1.200 – 330.071 K |

All five are format 4, log ohms against kelvin, negative temperature
coefficient.

## The first two are the Coldplate, and only one of them is its own

**X186279 is the LTSPM3 Coldplate** (218 input 2). **X186276 is a different
thermometer**, and its curve was the one loaded in the 218 until 2026-09-04
12:07:16. The serials differ in one digit and the sensors do not: X186276 is a
CU-package unit that is about 12% higher in resistance at every temperature, so
for the same resistance the box named a temperature that was too warm — the
whole cold end of this cryostat read high for as long as that curve was loaded.

That is the correction, and it is not a constant offset:

| logged | true | | logged | true |
|---|---|---|---|---|
| 6 K | 4.93 K (−1.07) | | 77 K | 65.33 K (−11.67) |
| 10 K | 8.11 K (−1.89) | | 150 K | 130.42 K (−19.58) |
| 20 K | 16.22 K (−3.78) | | 300 K | 265.10 K (−34.90) |

Print the whole table with

```bash
python -m lschart.tools.recalibrate --report --from reference/sensor-curves/X186276.340 --to reference/sensor-curves/X186279.340
```

and see [`docs/ltspm3/cryostat.md`](../../docs/ltspm3/cryostat.md) for what was
done about it. Note the ends: above 330.324 K and below 1.196 K the box was
clamping to the end of the loaded table, and a clamped reading carries no
resistance to convert. Those rows are unrecoverable and the tool blanks them
rather than inventing a number.

## The other three are not established here

X186275, X189259 and X207058 are the remaining thermometers from the same
folder, kept so a future question about one of them does not start with a
search of somebody's Desktop. **Which input each is wired to is not confirmed
by anything in this repository** — the folder names on the cryostat machine
say "2nd stage", "Caleb Heater" and "MAG" respectively, and that is a label a
person typed, not a measurement. The Coldplate pair is the only assignment this
repository has actually established, and it was established by the data: see
`tests/test_recalibrate.py` and the row count in
[`analysis/README.md`](../../analysis/README.md).
