# Noise on the sample channel

The sample thermometer jittered by 10–15 mK near 100 K, and this page was
originally a long argument about whether an RC filter on the sensor leads would
fix it. **Most of that argument is gone**, because between 2026-09-04 and 09-05
the cryostat changed underneath it: the wiring was improved, the Magnet moved
from input 3 to input 5, the 218's own filter was turned on, and the per-input
reading rate went from 2 Hz to 4 Hz. Conclusions drawn from the old
configuration do not transfer, and several of them were wrong anyway.

What is kept here is what survives: the archive measurements, which describe
fixed logs and cannot go stale, and the method, which is reusable. What was cut
is a chain of reasoning that had to be walked back three times.

## What the archive measures

Two settled constant-heater holds in `reference/logs/CD10`, reproduced by:

```bash
python -m lschart.tools.noisespec "reference/logs/CD10/*sample_monitor1.xls" -c "Input 1"
python -m lschart.tools.noisespec "reference/logs/CD10/*sample_monitor3.xls" -c "Input 1"
```

| | `sample_monitor1` | `sample_monitor3` |
|---|---|---|
| span, cadence | 72 h, 4.0 s | 144 h, 8.0 s |
| sample temperature | 96.13 K | 99.42 K |
| rms, quietest 6 h, detrended | 19.6 mK | 14.1 mK |
| **sample-to-sample jitter** | **10.4 mK** | **11.8 mK** |

Jitter is `std(diff)/sqrt(2)`. It is the right summary on this cryostat because
the slow content is *real thermal response* — signal, not noise — and a
band-integrated rms buries the thing an operator actually points at.

Where the noise sits, by band:

| band (period) | `monitor1` | `monitor3` |
|---|---|---|
| < 20 s | 9.5 mK | 9.4 mK |
| 20–120 s | 7.3 mK | 7.9 mK |
| 120–600 s | 5.4 mK | 4.2 mK |
| 600–3600 s | 7.8 mK | 2.9 mK |

**28.5% of `monitor1`'s variance sits at periods of 20–28 h** — the room, on a
day/night cycle.

Jitter versus temperature, from 229 settled hours in the `monitor4`/`5` ladder:
**21.0 mK at 118–146 K, 31.5 mK at 166 K**, against 10.4 mK at 96 K. Roughly
`T^1.9`. At the top of the ladder the jitter is 72–88% of the whole hourly rms:
**the jitter is the noise, not a component of it.**

## Averaging works, and by how much

Measured by running the filter over the record rather than assuming — a
*running* mean, matching what an instrument-side filter does:

| window | `monitor1` rms | `monitor3` rms |
|---|---|---|
| none | 19.6 mK | 14.1 mK |
| 32 s | 16.2 mK | 9.0 mK |
| 64 s | 15.4 mK | 7.2 mK |
| 512 s | 14.1 mK | 4.0 mK |

Two things to take from this. Averaging genuinely helps, so a filter that
appears to do nothing is a filter to re-check rather than a result. And it
helps *unevenly*: `monitor3` is mostly jitter and responds well, `monitor1`
carries far more slow content and barely moves. **Only the white part averages
down; the slow part sets a floor.**

## One finding that outlived the rewrite

High-passed at 120 s — fast enough that no thermal path between two stages
could carry it — the sample channel correlated with the 6.7 K stage at
**r = 0.55 in both records**, eight days apart at different cadences, against
0.05 for the third input. Two thermometers ninety kelvin apart cannot share
*sensor* noise, so whatever they shared was electrical: harness, ground, or the
group's A/D.

**That measurement is about a wiring configuration that no longer exists** —
the wiring was reworked and the Magnet moved to input 5, which is in the other
group with its own A/D. Recorded because it is the kind of thing worth
re-measuring after a change, not because it still describes the cryostat.

## What the readout is and is not

**Not quantization-limited.** The reported values sit on a 1 mK lattice
(Rayleigh r = 1.000) which is only the display's third decimal, and a settled
stretch spans 69 distinct codes at 15 mK rms with no coarser step underneath.
Whatever the 218 contributes is analogue front-end noise, not the LSB.

**The temperature scaling proves nothing on its own.** Jitter growing as
`T^1.9` was once offered here as evidence that the noise was thermal rather
than instrumental, on the grounds that a fixed floor would be flat in
millikelvin. That reasoning assumed a silicon diode. **The sample is a Cernox
CX-1050-SD-HT-1.4L**, an NTC whose `|dR/dT|` *falls* as it warms, so a floor
constant in ohms produces kelvin noise that grows with temperature — about the
observed factor. The scaling cannot separate the two hypotheses.

**Which is why kelvin is the wrong domain for this question.** Set
`read_sensor_units: true` and the recorder logs each input in ohms beside the
kelvin columns; a floor constant in ohms is then visible by inspection rather
than by datasheet archaeology. That measurement has not been taken yet and is
the obvious next step.

Two Cernox-specific suspects that no filter reaches, neither yet tested:

- **Excitation self-heating.** The 218 drives a single fixed 1 mA across its
  whole NTC range — `I²R` in the sensor chip, tens of µW at 100 K and more as
  R climbs at low temperature. Fluctuations in the mount's thermal resistance
  are *real sensor temperature*.
- **No current reversal.** The 218 is a monitor, not a bridge, so thermal EMFs
  in the adapter chain and front-end 1/f go uncancelled where a reversing
  bridge would subtract them out.

## The filter, as configured

The 218's filter **is** exponential smoothing applied to the reading, and it
does reach `KRDG?`/`SRDG?` — confirmed at the box, 2026-09-05, after a first
attempt that read as "no effect". If that happens, suspect the setup: the
`window` parameter restarts the filter on any reading outside it, so a tight
window gives a filter that continually resets.

Current setting: **4 points at 4 Hz** — τ ≈ 1 s, noise gain ≈ `1/sqrt(7)` ≈
**2.6×**. Against the response's ~620 s pole, 1 s of lag is nothing. The
mechanics, the sizing rule and the rate table are in
[instruments.md](../recorder/instruments.md#how-a-218-actually-reads-a-thermometer).

**The CSV does not record the filter setting**, and a filtered record and an
unfiltered one look identical apart from being quieter. Write it down when it
changes.

## What was cut, and why

For anyone who reads the history: this page previously argued that the noise
was mostly slow and unreachable by any filter, then that flat jitter versus
sampling rate was an aliasing signature, then that 1/f in the front end
explained a filter that did nothing. The first was answering with band rms when
the question was about jitter. The second rested on a 4 s versus 8 s comparison
across different days — too short a lever. The third was built on one
qualitative observation that the tables above contradict.

The durable lesson is the one in `noisespec`'s own docstring: an rms figure
cannot tell you whether filtering will help, and neither can a record that
cannot see past its own Nyquist. Where those run out, the answer comes from an
instrument across the leads or from a controlled change at the box — not from
more arithmetic on the same log.
