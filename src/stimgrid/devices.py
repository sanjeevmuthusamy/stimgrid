"""Stimulator resolution: the amplitudes and timings a device can actually produce.

A device has a timing grid (every phase width, gap and period is a whole number
of time steps) and an amplitude grid (every current is a whole number of
amplitude steps), plus optional ranges. Amplitude can also be continuous when a
device does not publish its step.

Built-in presets use only figures from the vendor's own documentation, cited in
`source`. Check them against your device and its settings before relying on them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional

INTAN_RHS2116_STEPS_UA = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
INTAN_SOURCE = ("Intan Technologies RHS2116 datasheet: global current step sizes 10 nA to 10 µA, "
                "8-bit magnitude; pulse timing set by the host's command timing")
CL_SOURCE = ("Cortical Labs CL API docs (docs.corticallabs.com/cl.html), StimDesign: phase width evenly "
             "divisible by 20 µs, |current| at most 3.0 µA, charge at most 3.0 nC")


@dataclass(frozen=True)
class Device:
    name: str
    time_step_us: float
    amplitude_step_ua: Optional[float] = None  # None: amplitude is continuous
    max_amplitude_ua: Optional[float] = None
    min_phase_us: Optional[float] = None  # defaults to one time step
    max_phase_us: Optional[float] = None
    max_charge_per_phase_nc: Optional[float] = None
    max_frequency_hz: Optional[float] = None
    source: str = "user-supplied"
    note: str = ""

    def __post_init__(self) -> None:
        if not (isinstance(self.name, str) and self.name):
            raise ValueError("name must be a non-empty string")
        _positive("time_step_us", self.time_step_us)
        for field_name in ("amplitude_step_ua", "max_amplitude_ua", "min_phase_us", "max_phase_us",
                           "max_charge_per_phase_nc", "max_frequency_hz"):
            value = getattr(self, field_name)
            if value is not None:
                _positive(field_name, value)
        if (self.amplitude_step_ua is not None and self.max_amplitude_ua is not None
                and self.max_amplitude_ua < self.amplitude_step_ua):
            raise ValueError("max_amplitude_ua is smaller than one amplitude step")
        if self.min_phase_us is not None and self.max_phase_us is not None and self.max_phase_us < self.min_phase_us:
            raise ValueError("max_phase_us is smaller than min_phase_us")

    # --- grid helpers ---------------------------------------------------------------------------------------

    @property
    def continuous_amplitude(self) -> bool:
        return self.amplitude_step_ua is None

    @property
    def min_ticks(self) -> int:
        if self.min_phase_us is None:
            return 1
        return max(1, math.ceil(self.min_phase_us / self.time_step_us - 1e-9))

    @property
    def max_ticks(self) -> Optional[int]:
        if self.max_phase_us is None:
            return None
        return math.floor(self.max_phase_us / self.time_step_us + 1e-9)

    @property
    def max_amplitude_steps(self) -> Optional[int]:
        if self.continuous_amplitude or self.max_amplitude_ua is None:
            return None
        return math.floor(self.max_amplitude_ua / self.amplitude_step_ua + 1e-9)

    def ticks(self, duration_us: float) -> int:
        return _round_half_up(duration_us / self.time_step_us)

    def us(self, ticks: int) -> float:
        return ticks * self.time_step_us

    def amp_steps(self, amplitude_ua: float) -> int:
        return _round_half_up(amplitude_ua / self.amplitude_step_ua)

    def ua(self, steps: int) -> float:
        return steps * self.amplitude_step_ua


# --- presets ----------------------------------------------------------------------------------------------------

def intan_rhs2116(step_ua: float = 1.0, sample_rate_hz: float = 30000.0) -> Device:
    """Intan RHS2116 with one global current step and a host timing grid of one sample.

    The chip has no pulse timers: the host sets every edge, normally once per sample,
    so the timing grid is 1 / sample rate. Full scale is 255 steps.
    """
    if not any(math.isclose(step_ua, s, rel_tol=1e-9) for s in INTAN_RHS2116_STEPS_UA):
        allowed = ", ".join(f"{s:g}" for s in INTAN_RHS2116_STEPS_UA)
        raise ValueError(f"the RHS2116 step size must be one of {allowed} µA")
    _positive("sample_rate_hz", sample_rate_hz)
    return Device(
        name=f"intan-rhs2116 (step {step_ua:g} µA, {sample_rate_hz:g} samples/s)",
        time_step_us=1e6 / sample_rate_hz,
        amplitude_step_ua=step_ua,
        max_amplitude_ua=255 * step_ua,
        source=INTAN_SOURCE,
        note="Timing grid assumes edges on sample boundaries; check your host software.",
    )


def cortical_labs_cl() -> Device:
    return Device(
        name="cortical-labs-cl",
        time_step_us=20.0,
        amplitude_step_ua=None,
        max_amplitude_ua=3.0,
        max_charge_per_phase_nc=3.0,
        source=CL_SOURCE,
        note="The docs give no amplitude step, so amplitude is treated as continuous. They also limit "
             "burst_hz to 200 Hz for bursts.",
    )


PRESETS: Dict[str, Callable[..., Device]] = {
    "intan-rhs2116": intan_rhs2116,
    "cortical-labs-cl": cortical_labs_cl,
}


def preset(name: str, **options: float) -> Device:
    try:
        make = PRESETS[name]
    except KeyError:
        raise ValueError(f"unknown device preset {name!r}; known: {', '.join(sorted(PRESETS))}") from None
    return make(**options)


def override(device: Device, **fields) -> Device:
    """A copy of a device with some fields changed (None values are ignored)."""
    changes = {k: v for k, v in fields.items() if v is not None}
    return replace(device, **changes) if changes else device


def _round_half_up(x: float) -> int:
    """Round to the nearest whole number, halves up (Python's round() sends 4.5 to 4)."""
    return int(math.floor(x + 0.5 + 1e-9))


def _positive(name: str, value: object) -> None:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and value > 0):
        raise ValueError(f"{name} must be a positive number")
