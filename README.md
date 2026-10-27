# stimgrid

Your stimulator cannot produce every pulse you write down. It sets current in fixed steps and timing on
a fixed clock, so every value gets rounded. For an asymmetric pulse (for example a short strong first
phase and a long weak recharge phase) that rounding quietly breaks the charge balance you designed. A
rate of 130 Hz may also become 129.87 Hz.

stimgrid shows what your device will actually deliver, and finds the nearest pulse on the device's grid
whose two phases carry exactly equal charge. Pure Python, no dependencies.

**Research use only.** stimgrid works from the resolution you give it (or a preset's published
figures). Check the result on your own hardware and settings.

## Install

```bash
pip install .
```

Requires Python 3.9 or newer.

## Example

An asymmetric pulse on an Intan RHS2116 with 0.1 µA steps, driven at 30,000 samples/s:

```bash
stimgrid quick --device intan-rhs2116 --step-ua 0.1 --sample-rate-hz 30000 \
  --amplitude-ua 7.3 --width-us 117 --second-amplitude-ua 1.9 --second-width-us 450 --frequency-hz 40
```

```
device: intan-rhs2116 (step 0.1 µA, 30000 samples/s): 33.3333 µs time steps, 0.1 µA steps, max 25.5 µA
  plan      7.3 µA x 117 µs | gap 0 µs | 1.9 µA x 450 µs
  rounded   7.3 µA x 133.333 µs | gap 0 µs | 1.9 µA x 466.667 µs
            charge 0.97333 nC (+13.96% vs plan), net 0.08667 nC, imbalance 8.9041%
  balanced  7.2 µA x 100 µs | gap 0 µs | 1.8 µA x 400 µs
            charge 0.72 nC (-15.70% vs plan), net 0 nC, imbalance 0.0000%
  option 1  7.2 µA x 133.333 µs | gap 0 µs | 1.8 µA x 533.333 µs
            charge 0.96 nC (+12.40% vs plan), net 0 nC, imbalance 0.0000%
  ...
WARNING SG201 simple rounding leaves the pulse unbalanced (imbalance 8.9041%, net 0.08667 nC per pulse)
WARNING SG203 the balanced pulse changes charge per phase by -15.7%
```

Simple rounding would leave 0.087 nC behind on every pulse. stimgrid lists exactly balanced pulses
instead, closest to your plan first, and warns when the grid forces charge per phase to move.

## How it works

1. **Rounded.** Each amplitude, phase width and gap is rounded (halves up) to the device's steps and
   clamped to its ranges. This is what you get if you type the plan in and let the device round it.
2. **Balanced.** stimgrid searches small changes to both phases, mostly the second phase's width and
   amplitude, the usual balancing knobs. On a gridded device, balance is checked exactly, as whole
   current steps times whole time steps, so "balanced" means equal to the last bit. On a device with
   continuous amplitude it also tries the first-phase amplitude that keeps your planned charge exactly.
3. **Ranking.** Exactly balanced candidates are ranked by closeness to your plan, with first-phase charge
   counted double because it sets the effect. The best and three alternatives are shown.
4. **Rates.** Each rate is rounded to a whole number of time steps per period, and the delivered rate is
   reported.

Options: `--keep-first` balances with the second phase only; `--max-imbalance 0.001` accepts a 0.1%
imbalance as balanced (the default is exact).

## Devices

| Preset | Time step | Amplitude | Source |
|---|---|---|---|
| `intan-rhs2116` | 1 / sample rate (`--sample-rate-hz`, default 30,000) | `--step-ua` of 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5 or 10 µA, up to 255 steps | Intan RHS2116 datasheet |
| `cortical-labs-cl` | 20 µs | continuous (no step published), up to 3 µA, 3 nC per phase | Cortical Labs CL API docs (StimDesign) |

`stimgrid devices` prints each preset's source. The RHS2116 has no pulse timers; the host sets every
edge, so its timing grid depends on your host software. Any other stimulator can be described by its
own numbers:

```bash
stimgrid quick --time-step-us 10 --amplitude-step-ua 0.5 --max-amplitude-ua 100 \
  --amplitude-ua 20 --width-us 105 --second-amplitude-ua 4 --second-width-us 520
```

Fields: `time_step_us` (required), `amplitude_step_ua` (leave out for continuous), `max_amplitude_ua`,
`min_phase_us`, `max_phase_us`, `max_charge_per_phase_nc`, `max_frequency_hz`.

## Request files

```json
{
  "stimgrid": 1,
  "device": {"preset": "intan-rhs2116", "step_ua": 0.1, "sample_rate_hz": 30000},
  "pulse": {"amplitude_ua": 7.3, "width_us": 117, "second_amplitude_ua": 1.9, "second_width_us": 450},
  "rates": {"frequency_hz": 40},
  "max_imbalance": 0,
  "keep_first": false
}
```

```bash
stimgrid fit examples/intan_asymmetric.json
stimgrid fit examples/intan_asymmetric.json --format json
cat request.json | stimgrid fit - --format pulse
```

A preset can also take any device field to override it, for example
`{"preset": "intan-rhs2116", "step_ua": 0.1, "max_amplitude_ua": 10}`. Invalid input names the exact
place, for example `pulse: width_us must be a positive number`.

`--format pulse` prints only the best pulse, in the common shape (`amplitude_ua`, `width_us`,
`second_amplitude_ua`, `second_width_us`, `gap_us`), ready to paste into a protocol file or another tool.

## Findings

| Code | Level | Meaning |
|---|---|---|
| SG101 | error | An amplitude is above the device's maximum |
| SG102 | error | A phase width is outside the device's range |
| SG103 | error | An amplitude is below one current step |
| SG104 | error | Charge per phase is above the device's maximum |
| SG105 | warning | The interphase gap rounds to zero |
| SG106 | warning | Rounding moves a value by more than 10% |
| SG201 | warning | Simple rounding leaves the pulse unbalanced |
| SG202 | error | No balanced pulse was found within the search and the device's limits |
| SG203 | warning | The balanced pulse changes charge per phase by more than 5% |
| SG301 | warning | A rate changes by more than 0.1% on whole periods |
| SG303 | error | The pulse is longer than one period at that rate |
| SG304 | error | A rate is above the device's maximum |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Fitted with no errors (and no warnings, with `--strict`) |
| 1 | Errors (or warnings, with `--strict`) |
| 2 | Bad input or usage |
| 3 | Internal error |

## From Python

```python
from stimgrid import Pulse, Rates, fit, intan_rhs2116

report = fit(Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450),
             intan_rhs2116(step_ua=0.1, sample_rate_hz=30000), Rates(frequency_hz=40))
print(report.balanced.as_pulse(), report.balanced.imbalance)
```

## Limits of this tool

- It models one pulse shape: rectangular phases with an optional gap. It does not model DAC settling,
  electrode impedance, channel-to-channel mismatch or the RHS2116's trim settings.
- Rates are assumed to fall on the same time grid as phase widths.
- Continuous amplitude means the device's real amplitude resolution is unknown; the reported amplitude
  may need rounding again on the hardware.

## Citing

If you use stimgrid in published work, please cite it. GitHub's "Cite this repository" button gives the
reference from `CITATION.cff`:

> Muthusamy S. stimgrid (version 0.1.0) [Computer software]. https://github.com/sanjeevmuthusamy/stimgrid

## Tests

```bash
python -m unittest discover -s tests
```

## Licence

MIT. See `LICENSE`.
