# The 10–15 mK on the sample channel, and why a filter does not fix it

The sample thermometer jitters by 10–15 mK near 100 K. The obvious fix is an RC
low pass on the sensor leads at the 218's input, with a time constant of about
100 ms to suit ~1 Hz sampling.

**That filter would do nothing.** Not because 100 ms is slightly wrong, but
because it is in the wrong decade for both the instrument and the noise. This
document is the measurement behind that, so nobody has to take it on trust —
and so that if the cryostat changes, the same measurement can be repeated
rather than the conclusion inherited.

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
3. Diode inputs on the 0–2.5 V range resolve **20 µV**.

Fact 1 is why the proposal is not dangerous. Fact 2 is why it is useless.

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

## And the noise is not up there anyway

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

The amplitudes are informative. In the fastest band the shared part is ~7.0 mK
on Input 1 and ~0.38 mK on Input 3, a ratio of about **18:1**, and both of the
obvious explanations land near it:

- a fixed disturbance **in volts** predicts the ratio of the two sensors'
  sensitivities — about 17:1 for a silicon diode between 6.7 K and 96 K;
- a fixed **fractional** temperature disturbance predicts 96/6.7 = 14:1.

The data does not separate those two, and the sensor type is not recorded
anywhere in this repository, so the 17:1 figure carries an assumption that
should be checked before it is leaned on. Either way the component is
instrument- or wiring-level, which is a thing you fix with a ground and a
shield, not with a capacitor.

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
| growth with temperature | fast-band rms rises ~T^1.7 across 118→171 K (16.4 → 30.0 mK, 229 settled hours in `monitor4`/`5`) | thermal, not electrical. A fixed instrument floor in volts would be *flat* here, since a diode's sensitivity barely moves over that span. This is the same effect the `1.36e-6·T²` model in [thermal-response.md](thermal-response.md) describes |

That last row is worth dwelling on, because it disposes of the third hypothesis
in the original question. If the 10–15 mK were the 218's readout floor, it would
be roughly constant in millikelvin across 118–171 K. It nearly doubles. The
readout is not what limits this measurement — which agrees with the Allan
deviation already recorded in
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
