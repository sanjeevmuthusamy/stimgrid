# stimgrid

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23694989.svg)](https://doi.org/10.5281/zenodo.23694989) [![PyPI](https://img.shields.io/pypi/v/stimgrid.svg)](https://pypi.org/project/stimgrid/) [![tests](https://github.com/sanjeevmuthusamy/stimgrid/actions/workflows/tests.yml/badge.svg)](https://github.com/sanjeevmuthusamy/stimgrid/actions/workflows/tests.yml)

Your stimulator cannot produce every pulse you write down. It sets current in fixed steps and timing on
a fixed clock, so every value gets rounded. For an asymmetric pulse (for example a short strong first
phase and a long weak recharge phase) that rounding quietly breaks the charge balance you designed. A
rate of 130 Hz may also become 129.87 Hz.

stimgrid shows what your device will actually deliver, and finds the nearest pulse on the device's grid
whose two phases carry exactly equal charge. Pure Python, no dependencies.

For electrical neurostimulation and microstimulation on multi-electrode arrays (MEAs), microelectrodes
and neural interfaces, with presets for the Intan RHS2116, Multi Channel Systems STG4000 and STG5,
Tucker-Davis Technologies IZ2 and Cortical Labs CL, and support for any other current-controlled
stimulator from its current and timing steps.

**Research use only.** stimgrid works from the resolution you give it (or a preset's published
figures). Check the result on your own hardware and settings.

## Install

Requires Python 3.9 or newer, and nothing else.

```bash
pip install git+https://github.com/sanjeevmuthusamy/stimgrid
```

Or from a downloaded copy, inside the `stimgrid` folder:

```bash
pip install .
```

This gives you the `stimgrid` command. You can also run it without installing, from the folder, as
`PYTHONPATH=src python -m stimgrid ...`.

## Quick start

1. **Find your device.** `stimgrid devices` lists the built-in presets and where their figures come
   from. If yours is not there, give its own steps instead (see [Devices](#devices)).
2. **Check your pulse.** Give the first phase, the second phase and the rate:

   ```bash
   stimgrid quick --device mcs-stg4000 --amplitude-ua 7.3 --width-us 117 \
     --second-amplitude-ua 1.9 --second-width-us 450 --frequency-hz 130
   ```

3. **Read the result** (see [Reading the output](#reading-the-output)). Use the `balanced` pulse, or one
   of the options, in your stimulation software, or [export it](#export-to-your-stimulation-software)
   as a file that Intan RHX or MCS MC_Stimulus can load.
4. **Save it for later or for a lab mate** as a [request file](#request-files), and rerun it with
   `stimgrid fit my_pulse.json`.

Leave out `--second-amplitude-ua` and `--second-width-us` for a symmetric pulse; the second phase then
copies the first.

## Reading the output

For the quick start command above:

```
device: mcs-stg4000 (1.6 mA version): 20 µs time steps, 0.2 µA steps, max 1600 µA
  plan      7.3 µA x 117 µs | gap 0 µs | 1.9 µA x 450 µs
  rounded   7.4 µA x 120 µs | gap 0 µs | 2 µA x 460 µs
            charge 0.888 nC (+3.97% vs plan), net -0.032 nC, imbalance 3.4783%
  balanced  7.2 µA x 120 µs | gap 0 µs | 1.8 µA x 480 µs
            charge 0.864 nC (+1.16% vs plan), net 0 nC, imbalance 0.0000%
  option 1  7 µA x 120 µs | gap 0 µs | 2 µA x 420 µs
            charge 0.84 nC (-1.65% vs plan), net 0 nC, imbalance 0.0000%
  ...
  rate      frequency_hz 130 Hz -> 129.87 Hz (385 steps per period, -0.100%)

WARNING SG201 simple rounding leaves the pulse unbalanced (imbalance 3.4783%, net -0.032 nC per pulse)

best pulse: {"amplitude_ua": 7.2, "width_us": 120, "second_amplitude_ua": 1.8, "second_width_us": 480}
result: OK
```

| Line | Meaning |
|---|---|
| `device` | The grid being used: time step, current step and maximum current |
| `plan` | The pulse you asked for: first phase, gap, second phase |
| `rounded` | What the device delivers if you type your plan in as it is |
| `balanced` | The closest pulse whose two phases carry exactly equal charge. **Use this one** |
| `option 1-3` | Other exactly balanced pulses, next closest first |
| `charge` | Charge in the first phase, and how far it moved from your plan |
| `net` | Charge left on the electrode by every pulse; 0 means balanced |
| `imbalance` | The difference between the two phases' charges, as a share of the larger one |
| `rate` | The rate you asked for, and the rate the device's clock really gives |
| `WARNING` / `ERROR` | Findings, explained in [Findings](#findings) |
| `best pulse` | The pulse to use, in a form you can paste into a file or program |
| `result` | `OK`, or `ERROR` if the pulse breaks a device limit or cannot be balanced |

Here the planned pulse would leave 0.032 nC on the electrode with every pulse; the balanced one leaves
none and keeps charge per phase within 1.2% of the plan.

## Another example: Intan RHS2116

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

### Checked against Intan RHX

The `intan-rhs2116` preset was compared with the source code of Intan RHX 3.5.2, Intan's own
acquisition and stimulation software. RHX uses a time step of 1 / sample rate and a current step equal
to the chosen step size, and it sends each phase as the nearest whole number of samples and current
steps, with halves rounded up. That is the same rule stimgrid uses.

The one difference is in RHX's stimulation dialog, which rounds typed values from a step shortened to
three decimals (33.333 µs at 30,000 samples/s). Across 300,000 test values, 42 landed one sample
apart, all at 30,000 samples/s and all within about 0.01 µs of a half-sample. For example, 383.33 µs
gives 11 samples (366.67 µs) in stimgrid and 12 samples (400 µs) in RHX. At 20,000 and 25,000
samples/s the two agreed on every value. If you type widths into RHX, use the `rounded` or `balanced`
widths stimgrid prints; they are whole samples, so RHX keeps them as they are.

This is a comparison with RHX's source code, not a measurement on hardware.

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

## Command options

`stimgrid quick` takes the pulse as flags. Amplitudes are in µA, times in µs, rates in Hz.

| Flag | Meaning |
|---|---|
| `--device NAME` | A preset from `stimgrid devices` |
| `--step-ua`, `--sample-rate-hz`, `--range-ua` | Preset settings (see [Devices](#devices)) |
| `--time-step-us`, `--amplitude-step-ua` | Your own device's steps, without a preset (or to override one) |
| `--max-amplitude-ua`, `--max-charge-per-phase-nc` | Your own device's limits |
| `--amplitude-ua`, `--width-us` | First phase (required) |
| `--second-amplitude-ua`, `--second-width-us` | Second phase (defaults to the same as the first) |
| `--gap-us` | Gap between the phases |
| `--monophasic` | One phase only |
| `--frequency-hz`, `--intra-burst-hz`, `--burst-rate-hz` | Rates to check against the time grid |
| `--keep-first` | Keep the rounded first phase as it is; balance with the second phase only |
| `--max-imbalance 0.001` | Accept up to 0.1% imbalance as balanced (the default is exact) |

Both `quick` and `fit` also take:

| Flag | Meaning |
|---|---|
| `--format text` | Readable report (the default) |
| `--format json` | The full report as JSON, for scripts |
| `--format pulse` | Only the best pulse as JSON |
| `--strict` | Treat warnings as failures (exit code 1), for automated checks |

`stimgrid --help` and `stimgrid quick --help` list them too.

## Export to your stimulation software

Instead of typing the fitted pulse into your stimulation software, `--export` writes it as a file that
software can load. It works with both `quick` and `fit`:

```bash
stimgrid fit examples/intan_asymmetric.json --export intan-rhx --out stim.xml --channels A-000,A-001 --pulses 10
stimgrid fit examples/mcs_stg4000_asymmetric.json --export mcs-ascii --out pulse.txt --pulses 100
```

The report still prints as usual, and the file is written next to it. With `--out -` only the file
contents are printed. Nothing is exported if the fit has errors.

| Flag | Meaning |
|---|---|
| `--export intan-rhx` | Intan RHX stimulation settings (`.xml`), for the RHS Stim/Record Controller |
| `--export mcs-ascii` | MC_Stimulus II (STG4000) or MC_Stimulus III (STG5) ASCII import, current mode |
| `--out FILE` | Where to write it (required; `-` prints it) |
| `--use best` | Which pulse: `best` (the balanced one; the default), `rounded`, `balanced` or `option-1` to `option-3` |
| `--channels` | RHX channel names such as `A-000,B-015` (default `A-000`), or MCS channel numbers such as `1,2` (default `1`) |
| `--pulses N` | Pulses in the train (default 1). Above 1 it needs `--frequency-hz` (or `--intra-burst-hz`) |
| `--first-phase negative` | Sign of the first phase: `negative` (the default) or `positive` |

**Intan RHX.** Load the file with *File > Load Stimulation Settings* (not *Load Settings*, which expects
a full RHX settings file and warns that no signal groups were found). It sets, for each listed channel:
stimulation on, the shape (biphasic, or biphasic with interphase delay when there is a gap), the
polarity, both phase durations and amplitudes, and for a train the number of pulses and the train
period. Trigger, amp settle and charge recovery are left out, so the values already set in RHX stay.
Other channels are not touched.

- The file names the sample rate and step size it was fitted for. RHX ignores every stimulation setting
  in the file, with only a warning, if these differ from the running system, so use the same
  `--sample-rate-hz` (20,000, 25,000 or 30,000) and `--step-ua` as in RHX.
- Durations are written in µs to six decimals. RHX converts them to the nearest whole sample, which is
  the sample count stimgrid fitted.
- stimgrid will not write what RHX would reject: phases or gaps over 5,000 µs, more than 256 pulses, or a
  train period shorter than the pulse.

**MCS MC_Stimulus.** Import the file in MC_Stimulus II or III. Each pulse is written as rows of current
(µA) and duration (µs): the first phase, the gap if there is one, the second phase, then zero current
for the rest of the period when a rate is given. Every pulse in the train is written out, because the
import format has no repeat count.

- Importing overwrites the open worksheet, and channels that are not in the file come out empty. Save
  your current setup first.
- The import takes times in multiples of 20 µs on both the STG4000 and the STG5, although the STG5
  itself has a 5 µs time step. If an STG5 pulse is not on 20 µs steps, stimgrid says so; fit it again
  with `--time-step-us 20`.
- The manuals do not say which sign is cathodic on your electrode. `--first-phase` sets the sign of
  the first phase; check the waveform preview in MC_Stimulus.

Check the loaded values in the vendor software before stimulating. The file formats were written from
the vendors' published descriptions. An `intan-rhx` file for the Intan example (10 pulses at 40 Hz) was
loaded in the RHX 3.5.2 RHS demo at 30,000 samples/s and 0.1 µA steps, and RHX showed the exported
values exactly (7.2 µA × 100 µs, 1.8 µA × 400 µs, cathodic first, 10 pulses, 25 ms period) and
reported the charge as balanced. No `mcs-ascii` file has been loaded in MC_Stimulus yet.

## Devices

| Preset | Time step | Amplitude | Source |
|---|---|---|---|
| `intan-rhs2116` | 1 / sample rate (`--sample-rate-hz`, default 30,000) | `--step-ua` of 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5 or 10 µA, up to 255 steps | Intan RHS2116 datasheet |
| `cortical-labs-cl` | 20 µs | continuous (no step published), up to 3 µA, 3 nC per phase | Cortical Labs CL API docs (StimDesign) |
| `mcs-stg4000` | 20 µs, up to 25 kHz | `--range-ua 1600`: 0.2 µA steps; `--range-ua 16000`: 2 µA steps | MCS STG 4002/4004/4008 datasheets |
| `mcs-stg5` | 5 µs, up to 100 kHz | `--range-ua 160`: 0.1 µA; `1600`: 1 µA; `16000`: 10 µA steps | MCS STG5 brochure |
| `tdt-iz2` | 1 / RZ sample rate (`--sample-rate-hz`, default 24,414.0625) | continuous (no step published); `--range-ua 300` (IZ2, IZ2M) or `3000` (IZ2H, IZ2MH) | TDT IZ2 manual, IZ2M/IZ2MH spec sheet |

All presets are current mode. The MCS range is the model version you bought (STG4000) or the range you
select (STG5). The STG4000 160 µA version is left out because its official datasheet could not be
confirmed.

**Not included yet**, because the vendor does not publish a current step or a timing step:

| Device | Published | Missing |
|---|---|---|
| Axion Maestro Pro / Edge | ±250 µA, ±1.2 V, phase at least 100 µs | current step, time step |
| MaxWell MaxOne / MaxTwo | 32 stimulation units, ±1.0 V | current range and step, time step |
| Blackrock CereStim | phase 44–65,535 µs, 4 Hz–5 kHz, 100 µA steps (macro model) | micro model step, time step |
| A-M Systems 4100 | 100 mA with 1 µA resolution, pulses from 1 µs | time step |

If you have one of these, describe it with your own numbers (below), or send the values from your
manual and they can become a preset.

`stimgrid devices` prints each preset's source. The RHS2116 has no pulse timers; the host sets every
edge, so its timing grid depends on your host software (Intan RHX matches it; see
[Checked against Intan RHX](#checked-against-intan-rhx)). Any other stimulator can be described by its
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

A preset takes its options (`step_ua`, `sample_rate_hz` or `range_ua`, as in the table) and any device
field to override, for example `{"preset": "intan-rhs2116", "step_ua": 0.1, "max_amplitude_ua": 10}` or
`{"preset": "mcs-stg5", "range_ua": 160}`. Invalid input names the exact
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

To export from Python:

```python
from stimgrid import export

device = intan_rhs2116(step_ua=0.1, sample_rate_hz=30000)
report = fit(Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450), device, Rates(frequency_hz=40))
with open("stim.xml", "w") as fh:
    fh.write(export(report, device, "intan-rhx", channels=["A-000"], pulses=10))
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

> Muthusamy S. stimgrid (version 0.3.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.23694989

This DOI always resolves to the latest version; each release also has its own DOI on Zenodo.

## Tests

```bash
python -m unittest discover -s tests
```

## Licence

MIT. See `LICENSE`.
