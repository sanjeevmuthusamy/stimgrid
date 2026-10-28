"""Write a fitted pulse in a file the stimulator's own software can load.

Two formats:

- intan-rhx: a stimulation-settings XML for Intan RHX (File > Load Stimulation Settings),
  for the RHS Stim/Record Controller. Only the pulse-shape attributes are written, so
  trigger, settle and charge-recovery settings already set in RHX stay as they are.
- mcs-ascii: the MC_Stimulus II / III ASCII import (format 4: one "value time" row per
  segment) for Multi Channel Systems STG4000 and STG5 stimulators, current mode.

Both formats were written from the vendors' published file descriptions. Check the loaded
values in the vendor software before stimulating.
"""

from __future__ import annotations

import math
import re
from typing import List, Optional, Sequence
from xml.sax.saxutils import quoteattr

from .devices import INTAN_RHS2116_STEPS_UA, Device
from .fit import Delivered, FitReport

FORMATS = ("intan-rhx", "mcs-ascii")
INTAN_RHX_VERSION = "3.5.2"
INTAN_RHX_SAMPLE_RATES = (20000, 25000, 30000)  # rates the RHS Stim/Record Controller offers
INTAN_RHX_MAX_PHASE_US = 5000.0
INTAN_RHX_MAX_PULSES = 256
MCS_TIME_GRID_US = 20.0  # the ASCII import takes times in multiples of 20 µs (MC_Stimulus II and III)
MCS_MAX_PULSES = 10000
_CHANNEL = re.compile(r"^[A-H]-\d{3}$")


class ExportError(ValueError):
    """The pulse cannot be written in the chosen format."""


def choose(report: FitReport, use: str = "best") -> Delivered:
    """Pick which pulse to export: best, rounded, balanced, or option-N."""
    if use == "best":
        return report.best
    if use == "rounded":
        return report.rounded
    if use == "balanced":
        if report.balanced is None:
            raise ExportError("there is no balanced pulse (the pulse is monophasic)")
        return report.balanced
    m = re.fullmatch(r"option-(\d+)", use)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= len(report.alternatives):
            raise ExportError(f"{use}: the report has {len(report.alternatives)} option(s)")
        return report.alternatives[n - 1]
    raise ExportError(f"use must be best, rounded, balanced or option-N, got {use!r}")


def train_period_ticks(report: FitReport) -> Optional[int]:
    """The pulse period from the report's frequency_hz (or intra_burst_hz) rate, in time steps."""
    for name in ("frequency_hz", "intra_burst_hz"):
        for r in report.rates:
            if r.name == name:
                return r.period_ticks
    return None


def export(report: FitReport, device: Device, fmt: str, *, use: str = "best", pulses: int = 1,
           channels: Optional[Sequence] = None, negative_first: bool = True) -> str:
    """The file contents for one fitted report. Raises ExportError for a report with errors.

    channels: RHX names such as A-000 for intan-rhx (default A-000), numbers for mcs-ascii (default 1).
    """
    if not report.ok:
        raise ExportError("the fit has errors; fix them before exporting")
    pulse = choose(report, use)
    period = train_period_ticks(report)
    if fmt == "intan-rhx":
        return to_intan_rhx(pulse, device, channels=channels or ("A-000",), pulses=pulses, period_ticks=period,
                            negative_first=negative_first)
    if fmt == "mcs-ascii":
        return to_mcs_ascii(pulse, device, channels=channels or (1,), pulses=pulses, period_ticks=period,
                            negative_first=negative_first)
    raise ExportError(f"unknown export format {fmt!r}; known: {', '.join(FORMATS)}")


# --- Intan RHX -----------------------------------------------------------------------------------------------------

def to_intan_rhx(pulse: Delivered, device: Device, *, channels: Sequence[str] = ("A-000",), pulses: int = 1,
                 period_ticks: Optional[int] = None, negative_first: bool = True) -> str:
    """Stimulation-settings XML for Intan RHX. Channels are RHX names such as A-000."""
    rate, step = _intan_grid(device)
    if not pulse.biphasic:
        raise ExportError("Intan RHX amplifier channels have no monophasic shape")
    names = _intan_channels(channels)
    _count(pulses, INTAN_RHX_MAX_PULSES)
    for label, us in (("first phase", pulse.width_us), ("second phase", pulse.second_width_us),
                      ("interphase gap", pulse.gap_us)):
        if us > INTAN_RHX_MAX_PHASE_US + 1e-9:
            raise ExportError(f"the {label} ({us:.6g} µs) is longer than RHX's {INTAN_RHX_MAX_PHASE_US:g} µs limit")
    attrs = [
        ("StimEnabled", "True"),
        ("Shape", "BiphasicWithInterphaseDelay" if pulse.gap_ticks else "Biphasic"),
        ("Polarity", "NegativeFirst" if negative_first else "PositiveFirst"),
        ("FirstPhaseDurationMicroseconds", _num(pulse.width_us)),
        ("SecondPhaseDurationMicroseconds", _num(pulse.second_width_us)),
    ]
    if pulse.gap_ticks:
        attrs.append(("InterphaseDelayMicroseconds", _num(pulse.gap_us)))
    attrs += [
        ("FirstPhaseAmplitudeMicroAmps", _num(pulse.amplitude_ua)),
        ("SecondPhaseAmplitudeMicroAmps", _num(pulse.second_amplitude_ua)),
    ]
    if pulses == 1:
        attrs.append(("PulseOrTrain", "SinglePulse"))
    else:
        period = _period(pulse, device, pulses, period_ticks)
        attrs += [("PulseOrTrain", "PulseTrain"), ("NumberOfStimPulses", str(pulses)),
                  ("PulseTrainPeriodMicroseconds", _num(device.us(period)))]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<IntanRHX Version="{INTAN_RHX_VERSION}" Type="ControllerStimRecord" SampleRateHertz="{rate}" '
             f'StimStepSizeMicroAmps="{step:g}">',
             "    <StimParameters>"]
    for name in names:
        body = " ".join(f"{k}={quoteattr(v)}" for k, v in attrs)
        lines.append(f'        <StimChannel NativeChannelName="{name}" {body}/>')
    lines += ["    </StimParameters>", "</IntanRHX>", ""]
    return "\n".join(lines)


def _intan_grid(device: Device):
    if device.continuous_amplitude or not any(math.isclose(device.amplitude_step_ua, s, rel_tol=1e-9)
                                              for s in INTAN_RHS2116_STEPS_UA):
        raise ExportError("intan-rhx needs an Intan device: use the intan-rhs2116 preset")
    rate = 1e6 / device.time_step_us
    for r in INTAN_RHX_SAMPLE_RATES:
        if math.isclose(rate, r, rel_tol=1e-9):
            step = min(INTAN_RHS2116_STEPS_UA, key=lambda s: abs(s - device.amplitude_step_ua))
            return r, step
    raise ExportError(f"the RHS Stim/Record Controller runs at {', '.join(map(str, INTAN_RHX_SAMPLE_RATES))} "
                      f"samples/s; this device runs at {rate:.6g}")


def _intan_channels(channels: Sequence[str]) -> List[str]:
    names = [str(c).strip().upper() for c in channels]
    if not names:
        raise ExportError("give at least one channel, such as A-000")
    for n in names:
        if not _CHANNEL.match(n):
            raise ExportError(f"channel {n!r} is not an RHX channel name such as A-000")
    if len(set(names)) != len(names):
        raise ExportError("a channel is listed twice")
    return names


# --- MCS MC_Stimulus ASCII -----------------------------------------------------------------------------------------

def to_mcs_ascii(pulse: Delivered, device: Device, *, channels: Sequence = (1,), pulses: int = 1,
                 period_ticks: Optional[int] = None, negative_first: bool = True,
                 program: Optional[str] = None) -> str:
    """MC_Stimulus ASCII import (format 4, current mode). Channels are numbers from 1.

    program is "II" (STG4000) or "III" (STG5); by default it follows the device preset.
    Every pulse is written out, because format 4 has no repeat count.
    """
    if device.continuous_amplitude:
        raise ExportError("mcs-ascii needs a device with an amplitude step: use an mcs-stg4000 or mcs-stg5 preset")
    program = program or ("III" if device.name.startswith("mcs-stg5") else "II")
    if program not in ("II", "III"):
        raise ExportError("program must be II (MC_Stimulus II) or III (MC_Stimulus III)")
    numbers = _mcs_channels(channels)
    _count(pulses, MCS_MAX_PULSES)
    sign = -1 if negative_first else 1
    segments = [(sign * pulse.amplitude_ua, pulse.width_us)]
    if pulse.biphasic:
        if pulse.gap_ticks:
            segments.append((0.0, pulse.gap_us))
        segments.append((-sign * pulse.second_amplitude_ua, pulse.second_width_us))
    if pulses > 1 or period_ticks is not None:
        period = _period(pulse, device, pulses, period_ticks)
        rest = period - pulse.duration_ticks
        if rest:
            segments.append((0.0, device.us(rest)))
    for _, us in segments:
        if not _on_grid(us, MCS_TIME_GRID_US):
            raise ExportError(f"MC_Stimulus takes times in multiples of {MCS_TIME_GRID_US:g} µs, but this pulse has "
                              f"{us:.6g} µs; fit it with a {MCS_TIME_GRID_US:g} µs time step (for example "
                              f"--time-step-us {MCS_TIME_GRID_US:g})")
    rows = [f"{_num(a)}\t{int(round(us))}" for a, us in segments] * pulses
    lines = [f"Multi Channel Systems MC_Stimulus {program}", "ASCII import Version 1.10",
             f"channels: {max(numbers)}", "output mode: current", "format: 4"]
    for n in numbers:
        lines += [f"channel: {n}", "value\ttime"] + rows
    return "\n".join(lines) + "\n"


def _mcs_channels(channels: Sequence) -> List[int]:
    out = []
    for c in channels:
        try:
            n = int(str(c).strip())
        except ValueError:
            raise ExportError(f"MCS channel {c!r} must be a number from 1") from None
        if n < 1:
            raise ExportError(f"MCS channel {n} must be a number from 1")
        out.append(n)
    if not out:
        raise ExportError("give at least one channel, such as 1")
    if len(set(out)) != len(out):
        raise ExportError("a channel is listed twice")
    return sorted(out)


# --- shared ------------------------------------------------------------------------------------------------------

def _count(pulses: int, top: int) -> None:
    if not (isinstance(pulses, int) and not isinstance(pulses, bool) and 1 <= pulses <= top):
        raise ExportError(f"pulses must be a whole number from 1 to {top}")


def _period(pulse: Delivered, device: Device, pulses: int, period_ticks: Optional[int]) -> int:
    if period_ticks is None:
        raise ExportError(f"{pulses} pulses need a rate: give frequency_hz")
    if period_ticks < pulse.duration_ticks:
        raise ExportError(f"the pulse ({pulse.duration_us:.6g} µs) is longer than one period "
                          f"({device.us(period_ticks):.6g} µs)")
    return period_ticks


def _on_grid(us: float, grid: float) -> bool:
    n = us / grid
    return abs(n - round(n)) < 1e-6


def _num(x: float) -> str:
    """A decimal with up to 6 places and no float noise: 133.333333, 7.3, 450."""
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s
