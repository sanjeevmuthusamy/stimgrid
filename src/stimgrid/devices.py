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
MCS_STG4000_RANGES = {1600.0: 0.2, 16000.0: 2.0}  # max µA per channel: current step µA
MCS_STG4000_SOURCE = ("Multi Channel Systems STG 4002/4004/4008 datasheets (1.6 mA and 16 mA versions, 2021): "
                      "output current resolution 200 nA (1.6 mA) or 2000 nA (16 mA), time resolution 20 µs, "
                      "maximum frequency 25 kHz")
MCS_STG5_RANGES = {160.0: 0.1, 1600.0: 1.0, 16000.0: 10.0}
MCS_STG5_SOURCE = ("Multi Channel Systems STG5 brochure (2024): up to 160 µA (resolution 100 nA), 1.6 mA (1 µA) "
                   "or 16 mA (10 µA); temporal pulse resolution 5 µs, up to 100 kHz")
TDT_IZ2_RANGES = {300.0: "IZ2 / IZ2M", 3000.0: "IZ2H / IZ2MH"}
TDT_IZ2_MAX_RATE_HZ = 195312.5
TDT_IZ2_SOURCE = ("Tucker-Davis Technologies IZ2 manual and IZ2M/IZ2MH spec sheet: output ±300 µA (IZ2, IZ2M) "
                  "or ±3 mA (IZ2H, IZ2MH); the stimulator runs at the sampling rate of its RZ processor, "
                  "pulses as short as one sample")


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
        note="Timing grid assumes edges on sample boundaries. This matches Intan RHX 3.5.2, which sends each "
             "phase as the nearest whole number of samples and current steps (halves up). Its dialog rounds "
             "from a truncated step (33.333 µs at 30 kS/s), so values within about 0.01 µs of a half-sample "
             "can land one sample higher there. Check other host software.",
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


def mcs_stg4000(range_ua: float = 1600.0) -> Device:
    """MCS STG 4002/4004/4008 in current mode. The range is fixed by the model version you bought."""
    step = _pick_range("the STG4000 version", range_ua, MCS_STG4000_RANGES)
    return Device(
        name=f"mcs-stg4000 ({range_ua / 1000:g} mA version)",
        time_step_us=20.0,
        amplitude_step_ua=step,
        max_amplitude_ua=range_ua,
        max_frequency_hz=25000.0,
        source=MCS_STG4000_SOURCE,
        note="Current mode. The 160 µA version is not included: its official datasheet could not be confirmed.",
    )


def mcs_stg5(range_ua: float = 1600.0) -> Device:
    """MCS STG5 in current mode, in one of its three ranges."""
    step = _pick_range("the STG5 range", range_ua, MCS_STG5_RANGES)
    return Device(
        name=f"mcs-stg5 (up to {range_ua:g} µA)",
        time_step_us=5.0,
        amplitude_step_ua=step,
        max_amplitude_ua=range_ua,
        max_frequency_hz=100000.0,
        source=MCS_STG5_SOURCE,
        note="Current mode. Figures are from the product brochure.",
    )


def tdt_iz2(range_ua: float = 300.0, sample_rate_hz: float = 24414.0625) -> Device:
    """TDT IZ2-family stimulator; the timing grid is one sample of the RZ processor driving it."""
    model = TDT_IZ2_RANGES.get(float(range_ua)) if _is_number(range_ua) else None
    if model is None:
        raise ValueError("range_ua must be 300 (IZ2, IZ2M) or 3000 (IZ2H, IZ2MH)")
    _positive("sample_rate_hz", sample_rate_hz)
    if sample_rate_hz > TDT_IZ2_MAX_RATE_HZ:
        raise ValueError(f"sample_rate_hz is above the IZ2 family's maximum of {TDT_IZ2_MAX_RATE_HZ:g}")
    return Device(
        name=f"tdt-iz2 ({model}, {sample_rate_hz:g} samples/s)",
        time_step_us=1e6 / sample_rate_hz,
        amplitude_step_ua=None,
        max_amplitude_ua=float(range_ua),
        source=TDT_IZ2_SOURCE,
        note="TDT publishes no current step, so amplitude is treated as continuous. Use your RZ's real "
             "sampling rate (for example 24414.0625, 48828.125, 97656.25 or 195312.5); the larger IZ2 "
             "models support only the lower rates.",
    )


PRESETS: Dict[str, Callable[..., Device]] = {
    "intan-rhs2116": intan_rhs2116,
    "cortical-labs-cl": cortical_labs_cl,
    "mcs-stg4000": mcs_stg4000,
    "mcs-stg5": mcs_stg5,
    "tdt-iz2": tdt_iz2,
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


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _pick_range(what: str, range_ua: object, ranges: Dict[float, float]) -> float:
    for top, step in ranges.items():
        if _is_number(range_ua) and math.isclose(range_ua, top, rel_tol=1e-9):
            return step
    raise ValueError(f"range_ua for {what} must be one of {', '.join(f'{r:g}' for r in ranges)} µA")


def _positive(name: str, value: object) -> None:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and value > 0):
        raise ValueError(f"{name} must be a positive number")
