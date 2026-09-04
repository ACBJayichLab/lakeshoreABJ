# The 10–15 mK on the sample channel, and why a filter does not fix it

The sample thermometer jitters by 10–15 mK near 100 K. The obvious fix is an RC
low pass on the sensor leads at the 218's input, with a time constant of about
100 ms to suit ~1 Hz sampling.

**That specific filter would do nothing**, because 100 ms is in the wrong decade
for the sampling chain it is meant to protect. Whether *some* analogue filter
would help is a narrower and still-open question, and this document is careful
to keep the two apart: the logs settle the first and cannot settle the second.

Read the split as: below ~0.05 Hz a filter is ruled out by measurement; above it
a filter is unproven in both directions, and the way to settle it is an
instrument across the leads, not more arithmetic on these logs.

Everything below comes from `reference/logs/CD10`, two independent settled
holds at constant heater, and is reproduced by:

```bash
python -m lschart.tools.noisespec "reference/logs/CD10/*sample_monitor1.xls" -c "Input 1"
python -m lschart.tools.noisespec "reference/logs/CD10/*sample_monitor3.xls" -c "Input 1"
```

| | `sample_monitor1` | `sample_monitor3` |
|---|---|---|
| span | 72 h, 07-17 → 07-20 | 144 h, 07-23 → 07-31 |
| cadence | 4.0 s | 8.0 s |
| sample temperature | 96.13 K | 99.42 K |
| heater | constant 63.07% | constant 63.072% |
| **rms, quietest 6 h, detrended** | **19.6 mK** | **14.1 mK** |
| display quantum | 1.0 mK | 1.5 mK |

## How the 218 reads a thermometer

The mechanics are generic and live in
[`../recorder/instruments.md`](../recorder/instruments.md#how-a-218-actually-reads-a-thermometer).
Three facts from there decide this question:

1. **Four-lead differential**, with eight **dedicated** constant-current
   sources — one per input, never switched. So a capacitor at a 218 input is
   *safe*: the sensor is excited continuously and the multiplexer only moves
   the voltmeter. On a switched-excitation instrument the same capacitor would
   be a bug, because it could not settle inside a channel dwell.
2. **2 readings per second per input.** The 218's output is band-limited to
   1 Hz per input whatever the recorder does.
3. One fixed excitation per input type — **1 mA across the whole 0–7500 Ω NTC
   RTD range**, which is what a Cernox sits on, with no current reversal to
   cancel thermal EMFs or front-end 1/f.

Fact 1 is why the proposal is not dangerous. Fact 2 is why *this* time constant
is useless. Fact 3 is a separate problem that no filter addresses at all.

## 100 ms puts the corner above the instrument's own Nyquist

τ = 100 ms is a corner at *f*<sub>c</sub> = 1/2πτ = **1.59 Hz**.

| stage | samples at | Nyquist |
|---|---|---|
| the 218, per input | 2 Hz | 1 Hz |
| the recorder | 0.5 Hz (2 s cadence) | 0.25 Hz |

A corner at 1.59 Hz is **above both**. It is therefore not an anti-alias filter
for either stage: everything the recorder logs came out of a stream the 218 had
already band-limited to 1 Hz, and a 1.59 Hz corner does not reach into that
band at all. The only thing such a filter can remove is front-end noise above
~1.6 Hz that would otherwise fold in through the 218's own A/D.

If anti-aliasing is the goal, the corner has to be set by the *slowest* sampler
in the chain, not by intuition about "fast":

| to anti-alias | need *f*<sub>c</sub> ≪ | so τ ≳ |
|---|---|---|
| the 218's 2 Hz per-input conversion | 1 Hz | ~1 s |
| the recorder's 0.5 Hz logging | 0.25 Hz | ~3–6 s |

τ = 100 ms is between ten and sixty times too fast to do the job it is being
proposed for.

## The jitter, which is the thing actually being complained about

Band-integrated rms is the wrong summary here, because on this cryostat the slow
part is *real thermal response* — signal, not noise. The number an operator
points at is the **sample-to-sample jitter**: how much the reading moves between
consecutive samples when nothing has any business moving.
`std(diff)/sqrt(2)` isolates it and ignores the slow part entirely.

| record / dwell | sample temperature | cadence | jitter | hour rms | jitter / rms |
|---|---|---|---|---|---|
| `monitor1` | 96.1 K | 4 s | **10.4 mK** | 35.3 mK | 0.29 |
| `monitor3` | 99.4 K | 8 s | **11.8 mK** | 14.6 mK | 0.81 |
| `monitor4/5` | 118.6 K | 10 s | **21.0 mK** | 23.9 mK | 0.88 |
| `monitor4/5` | 134.2 K | 10 s | **21.0 mK** | 24.9 mK | 0.84 |
| `monitor4/5` | 146.4 K | 10 s | **21.0 mK** | 29.3 mK | 0.72 |
| `monitor4/5` | 166.2 K | 10 s | **31.5 mK** | 40.2 mK | 0.78 |

Two things fall straight out.

**The jitter barely moves with polling rate** — 10.4 mK at 4 s, 11.8 mK at 8 s.
Band-limited noise would do the opposite: open the interval up and consecutive
samples decorrelate, so the jitter *rises*. Flat jitter is the signature of
noise whose bandwidth exceeds the Nyquist of both rates, which is to say it is
being aliased. **The original instinct was right about the character of the
noise, and this document's first version undersold it.**

**The jitter grows as roughly T^1.9** across 96→166 K. For a Cernox, whose
`|dR/dT|` falls as something like `T^-1.5` to `T^-1.9` over that span, that is
what a *constant* disturbance in ohms looks like once converted to kelvin. So
the jitter is consistent with a fixed noise floor at the 218's input rather than
with real temperature — a silicon diode, with its nearly flat sensitivity, could
not produce this scaling from a fixed floor at all.

### The cheap fix that comes before any hardware

At an 8 s cadence the 218 makes **16 readings per logged sample** (2 rdg/s per
input) and the recorder keeps one instantaneous value, throwing 15 away.
Averaging them instead divides white jitter by 4. The instrument will do it
without any help:

```
FILTER 1,1,64,10      # 64-point running average = 32 s at 2 rdg/s
FILTER? 1
```

64 points is 8× on white noise — 11.8 mK becomes ~1.5 mK — at a cost of ~32 s of
lag against a 620 s thermal pole, which is 5% and irrelevant to the loop.
**This is strictly better than averaging the recorder's samples over the same
32 s**, which only gets 4 samples and 2×, because the instrument averages
*before* the recorder's decimation throws the information away.

It is also the test worth running first, because it is one command and it
brackets the answer: if the jitter divides by ~8, it is white at the
instrument's reading rate and averaging has solved the problem with no hardware
at all. If it does not, the noise is correlated inside the 218's own stream and
something stranger is happening.

What `FILTER` cannot tell you is *where* the noise entered — averaging removes
aliased and in-band white noise identically. Only an instrument across the leads
separates those. But for fixing the jitter, that distinction may not matter.

## Most of the noise is nowhere a filter can reach

Where the noise actually lives, by band:

| band (period) | `monitor1` | `monitor3` |
|---|---|---|
| < 20 s | 9.5 mK | 9.4 mK |
| 20–120 s | 7.3 mK | 7.9 mK |
| 120–600 s | 5.4 mK | 4.2 mK |
| 600–3600 s | 7.8 mK | 2.9 mK |

Nothing is concentrated at the fast end. The single largest identified
component is far slower still: **28.5% of `monitor1`'s variance sits at periods
of 20–28 h.** That is the room, on a day/night cycle.

The consequence is best seen by asking what a low pass actually achieves.
`noisespec` measures it by running the filter over the record rather than
assuming, and prints the white-noise model beside it:

| τ | `monitor1` measured | `monitor3` measured | if the noise were white |
|---|---|---|---|
| 3 s | 0.93× | — | 0.82× |
| 10 s | 0.84× | 0.72× | 0.63× / 0.45× |
| 60 s | 0.73× | 0.41× | 0.18× / 0.26× |
| 600 s | 0.49× | 0.22× | 0.06× / 0.08× |
| 1200 s | 0.45× | 0.19× | 0.04× / 0.06× |

Read the top row. **A 3 s filter — already thirty times slower than the one
proposed — removes 7% of the noise.** Ten *minutes* of filtering, which is
comparable with the thermal response's own 620 s pole and therefore unusable in
a control loop, still leaves half of it.

The noise does not integrate down because it is not white. That is the whole
finding, and it is why an rms figure alone could never have answered the
question: 14 mK of broadband hash and 14 mK of slow wander are the same number
and opposite engineering problems.

## About a third of it is not the sample at all

`noisespec` correlates the channels against each other after high-passing at
120 s — slow enough that shared *cryostat* drift is removed, so what is left is
too fast for any thermal path between two stages to carry.

|  | Input 1 (96 K, sample) | Input 2 (8.1 K) | Input 3 (6.7 K) |
|---|---|---|---|
| Input 1 | 1.00 | 0.05 | **0.55** |
| Input 2 | 0.05 | 1.00 | 0.05 |
| Input 3 | **0.55** | 0.05 | 1.00 |

**r = 0.55 in both records**, taken eight days apart at different cadences,
against 0.05 for the third input. Two thermometers on different stages ninety
kelvin apart cannot share *sensor* noise, and at these timescales they cannot
share thermal fluctuation either. Whatever they share is electrical — the
harness, a ground return, or the group's A/D. It accounts for r ≈ 0.3 of the
variance, about **7 mK** of the sample channel's fast band.

The amplitudes are informative, and reading them needs the sensor. **The sample
is a Cernox CX-1050-SD-HT-1.4L** (Jeff, 2026-09-04) — an NTC resistor, so
`|dR/dT|` *falls* steeply with temperature, which is the opposite of a silicon
diode and inverts most of the arithmetic below.

In the fastest band the shared part is ~7.0 mK on Input 1 and ~0.38 mK on
Input 3, a ratio of about **18:1**. What that ratio implies depends on what is
on inputs 2 and 3, which is **not recorded anywhere in this repository** — and
the 218 forces every input in a group of four to the same type, so if Input 1 is
on the NTC RTD range then so are they. Assuming that:

- a fixed additive disturbance **in ohms or volts** predicts the ratio of the
  two sensors' sensitivities. For a Cernox between 6.7 K and 96 K that is
  *hundreds* to one, not 18:1. **This does not fit.**
- a fixed **fractional** temperature disturbance predicts 96/6.7 = 14:1, and a
  fixed fractional *resistance* disturbance predicts `(T/S)` ratio ≈ 30:1 on
  plausible Cernox dimensionless sensitivities. Both are the right order.

So the shared component looks **multiplicative** — excitation, reference or gain
— rather than an additive offset picked up on the leads. That is a different fix
from a shield, and a very different fix from a capacitor. Confirm the sensors on
inputs 2 and 3 before leaning on this.

Input 2 is uncorrelated with both. Whatever the shared path is, it does not
include that channel — which is itself a lead, because it means the harness and
the A/D are not equally suspect.

## The part that cannot be settled from the logs

Being explicit about the limit, because the tables above look more complete
than they are: **a 4 s record cannot see above 0.125 Hz.** Noise at 2 Hz aliased
in before the first row was written and no later arithmetic separates it out.
So these logs prove that a large, slow, filter-proof component exists; they
cannot prove that a fast component does *not*.

What bounds it is the instrument rather than the data: the 218 emits 2
readings/s per input, so anything a 100 ms filter would remove has to be noise
that folds through the 218's own A/D from above 1.6 Hz. That is a real path,
and it is not measured here.

Measuring it does not require building the filter first:

0. **Query `INTYPE? 1` and confirm what the archive was taken with.** Everything
   in this document is measured on Input 1 of the CD10 logs; if that channel
   carried a different sensor in July 2026 than it does now, the conversion from
   millikelvin to ohms changes and so does every cross-channel ratio above. The
   input type also fixes the excitation, which is what the self-heating question
   turns on.
1. **Query `FILTER? 1`.** The two archive records behave differently at the fast
   end — `monitor3` averages down considerably better than `monitor1` — and an
   unrecorded change to the 218's internal 2–64 point running average is the
   simplest explanation. A noise figure taken without knowing this setting is
   not comparable with anything.
2. **Put a scope or an FFT analyser across the sense pair at the 218's input.**
   This is the direct measurement, it takes twenty minutes, and it answers the
   actual question: is there anything between 1 Hz and 10 kHz? Mains pickup is
   unmistakable. If there is nothing there, the filter has nothing to remove
   and the matter is closed; if there is, its frequency sets the corner, rather
   than a guess about the sample rate.
3. **Log at the recorder's fastest cadence for an hour** and re-run `noisespec`.
   That pushes the wall from 0.125 Hz out towards 0.5 Hz. It does not reach
   1.6 Hz, but it does say whether the spectrum is still rising at the edge.
4. **Move the sample sensor to an input in the other group** (inputs 5–8 use the
   second A/D). If the correlation with Input 3 survives the move it is the
   harness; if it dies, it is the group's converter.
5. **Hang a spare diode at room temperature off a spare input on the same
   harness.** Its noise in volts is the instrument's floor with no cryostat in
   the loop.

## What to do instead

**Do not build the 100 ms filter.** It sits above the 218's own Nyquist, and
below that corner the measurement shows nothing for it to remove.

If a filter is wanted, it should be **in software, at tens of seconds**, and one
already exists: the supervisor runs a dt-aware single pole,
`MeasurementFilter(tau=60.0)`, set from the config's `control.filter:` section
([control.md](control.md)). Measured on this
cryostat's own data it delivers 0.41–0.73×, not the 0.18× a white-noise model
promises, and that is the honest number to plan with. It is also free,
adjustable without a soldering iron, reversible, and it keeps the raw samples in
the CSV where the next person can re-examine them — none of which is true of an
RC network buried in a cryostat harness.

The three components actually worth chasing, in order of size:

| component | size | where to look |
|---|---|---|
| 20–28 h wander | 28.5% of `monitor1`'s variance | room temperature; it is already known that a room-temperature covariate works on 2026-08/09 recorder data ([thermal-response.md](thermal-response.md)) |
| common-mode with Input 3 | ~7 mK, r = 0.55 | grounding, shielding, the harness, the group A/D — tests 4 and 5 above |
| growth with temperature | fast-band rms rises ~T^1.7 across 118→171 K (16.4 → 30.0 mK, 229 settled hours in `monitor4`/`5`) | **ambiguous, and it used to be read the wrong way round** — see below |

That last row was originally offered here as disposing of the readout
hypothesis: a fixed instrument floor would be flat in millikelvin across
118–171 K, and this nearly doubles, so the noise had to be thermal. **That
argument assumed a silicon diode and is wrong for a Cernox.** An NTC sensor
loses sensitivity as it warms — `|dR/dT|` falls by roughly 1.5–2× over that
span — so a *fixed* floor in ohms produces kelvin noise that grows by about
that factor. Measured growth is 1.83×. The temperature scaling therefore
**fails to discriminate** readout and wiring from thermal, and readout is back
on the table rather than excluded.

One thing about the readout does survive independently of the sensor:
**it is not quantization-limited.** The reported values sit on a 1 mK lattice
(Rayleigh r = 1.000) which is simply the display's third decimal place, and
across a settled stretch they span 69 distinct codes at 15 mK rms with no
coarser step showing through. Whatever the 218 is contributing, it is analogue
noise in the front end, not the least significant bit.

Two consequences of the Cernox worth chasing, neither of which is a filter:

- **The 218 drives a single fixed 1 mA for the whole NTC RTD range.** In a
  Cernox that is `I²R` dissipated in the sensor chip itself — tens of µW at
  100 K, and far more at low temperature where R climbs steeply. Self-heating
  offsets the reading by whatever the mount's thermal resistance says, and
  **fluctuations in that thermal contact are real sensor temperature**, so no
  filter anywhere reaches them.
- **The 218 is a monitor, not a bridge, and does not reverse the excitation.**
  Thermal EMFs at the junctions in the adapter chain and 1/f in the front end
  are therefore uncancelled, where a reversing bridge would subtract them out.
  That is a plausible home for both the slow wander and the diurnal term.

This all remains consistent with the Allan deviation already recorded in
[thermal-response.md](thermal-response.md) (6.1 mK @ 4 s → 2.5 mK @ 600 s,
about 2× worse than 1/√N) and with its conclusion that sampling faster buys
much less than it looks like it should.

## If you build one anyway

The 218 will tolerate it — dedicated always-on current sources, per
[instruments.md](../recorder/instruments.md#how-a-218-actually-reads-a-thermometer)
— but three things bite, and the first two get worse the larger τ is:

- **Series resistance goes in the sense leads only.** In the *current* leads it
  is harmless up to the source's compliance; in the sense leads it turns the
  differential amplifier's input bias current into a DC offset of `I_bias × R`.
  That is a calibration shift rather than added noise, but at 20 µV of
  resolution it does not take much.
- **A long time constant needs a large capacitor, and a large capacitor leaks.**
  At a sane series resistance, τ ≈ 1 s means tens of microfarads. A film cap of
  that size sitting across ~1 V of diode passes a leakage current whose
  `I_leak × R` offset can be comparable with the noise being removed — and it
  drifts with temperature, which adds exactly the slow component this cryostat
  already has too much of.
- **Match the common-mode legs or make it differential-only.** An unmatched
  capacitor to chassis on each sense lead converts common-mode into
  differential and makes the measurement worse. Given that r = 0.55 says a
  common-mode path is already present, this is not a hypothetical.
