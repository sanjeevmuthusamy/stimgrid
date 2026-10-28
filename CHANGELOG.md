# Changelog

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
