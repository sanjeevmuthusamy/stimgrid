# Changelog

## 0.4.0

- New `stimgrid check FILE`: reads an Intan RHX stimulation-settings file or an MC_Stimulus ASCII file
  and reports, per channel, the delivered pulse, its charge balance, the net charge over the whole
  channel and the nearest balanced pulse. It also flags values RHX or MC_Stimulus would not accept.
  Exit code 1 when a channel is not balanced. New findings SG401 to SG404.
- Charge density: with `--electrode-diameter-um` or `--electrode-area-um2` (or `"electrode"` in a
  request file), `fit`, `quick` and `check` report the charge density of the larger phase and its
  Shannon k value, and warn (SG501) above `--k-limit` (default 1.75). From Python: `Electrode` and
  `fit(..., electrode=...)`.
- Whole protocols: a request file with a `protocol` list of steps (single pulses, trains, bursts and
  rests, with optional channels and `repeat`) runs with `stimgrid fit`. It reports each step's
  delivered time and charge, and the totals per channel, using the balanced pulse (`--use best`) or the
  simply rounded one (`--use rounded`). New finding SG601 for a burst longer than its period. From
  Python: `load_protocol`, `fit_protocol`, `Protocol` and `Step`.
- A GitHub issue form for requesting a new device preset, linked from the README.
- README: a trademark and no-affiliation note.

## 0.3.0

- Export: `--export intan-rhx` writes an Intan RHX stimulation-settings file, and `--export mcs-ascii`
  writes an MC_Stimulus II / III ASCII import file for MCS STG4000 and STG5. Options `--out`, `--use`,
  `--channels`, `--pulses` and `--first-phase`; `stimgrid.export()` from Python.
- The Intan preset note and the README describe the comparison with Intan RHX's source code.
- Checked: an exported `intan-rhx` file loads in the Intan RHX 3.5.2 demo with the exact exported values.

## 0.2.0

- New device presets: `mcs-stg4000` (1.6 mA and 16 mA versions), `mcs-stg5` (three ranges) and
  `tdt-iz2` (IZ2, IZ2H, IZ2M, IZ2MH), each citing its published source.
- New preset option `range_ua` (`--range-ua`).
- A setting that does not apply to the chosen device is now a clear error.
- A balanced pulse on a continuous-amplitude device now reports a net charge of exactly 0.
- README: quick start, how to read the output, a table of command options, and the devices that are not
  supported yet with what their vendors leave unpublished.

## 0.1.0

- First version: round a pulse to a device's current and timing steps, find exactly charge-balanced
  pulses, check rates, presets for the Intan RHS2116 and Cortical Labs CL.
