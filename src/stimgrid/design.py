"""Design a pulse on a device's grid from the start.

fit() takes a pulse you have already written and moves it onto the grid. design() starts from what
the pulse should do instead: the charge per phase, and how much longer (and weaker) the second phase
is than the first. It lists pulses that the device can produce exactly and that are exactly
charge-balanced, closest to that aim first.

On a gridded device both phases carry the same whole number of (current step x time step) units:
first-phase steps x first-phase ticks = second-phase steps x second-phase ticks. design() searches
the first-phase widths, the second-phase widths near the ratio, and the shared charge near the target.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from .devices import Device
from .fit import _EPS, Delivered, Issue, Pulse, _deliver, _grid_width, _issue_order

MAX_FIRST_WIDTHS = 5000
MAX_COUNT = 50


@dataclass
class Option:
    """One designed pulse, with the phase ratio it actually has."""
    pulse: Delivered
    ratio: float  # second-phase width / first-phase width (= first amplitude / second amplitude)
    ratio_change: float  # versus the asked ratio, as a fraction


@dataclass
class DesignReport:
    device: dict
    target: dict
    options: List[Option]
    issues: List[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.options) and not any(i.severity == "error" for i in self.issues)

    @property
    def best(self) -> Optional[Option]:
        return self.options[0] if self.options else None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        data["best_pulse"] = self.best.pulse.as_pulse() if self.best else None
        return data


def design(device: Device, charge_nc: float, ratio: float = 1.0, width_us: Optional[float] = None,
           gap_us: float = 0.0, count: int = 5) -> DesignReport:
    """Exactly balanced pulses on the device's grid, closest to the charge and phase ratio asked for.

    charge_nc: charge per phase.
    ratio: second-phase width / first-phase width; the second phase is that many times weaker,
        so 4 means four times longer and four times weaker. 1 is a symmetric pulse.
    width_us: the first-phase width; rounded to the grid and kept. Leave it out to search all widths.
    gap_us: the gap between the phases (raised to the device's minimum if it has one).
    count: how many pulses to list.

    Options are ranked by charge change plus ratio change; among equals the shorter pulse comes first.
    """
    for name, value in (("charge_nc", charge_nc), ("ratio", ratio)):
        if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
            raise ValueError(f"{name} must be a positive number")
    if width_us is not None and not (isinstance(width_us, (int, float)) and math.isfinite(width_us)
                                     and width_us > 0):
        raise ValueError("width_us must be a positive number")
    if not (isinstance(gap_us, (int, float)) and math.isfinite(gap_us) and gap_us >= 0):
        raise ValueError("gap_us must be zero or a positive number")
    if not (isinstance(count, int) and 1 <= count <= MAX_COUNT):
        raise ValueError(f"count must be a whole number from 1 to {MAX_COUNT}")

    issues: List[Issue] = []
    target = {"charge_nc": charge_nc, "ratio": ratio, "width_us": width_us, "gap_us": gap_us}

    gap = device.ticks(gap_us)
    if gap < device.min_gap_ticks:
        if gap_us:
            issues.append(Issue("SG107", "error", f"gap_us {gap_us:g} is shorter than the device's "
                                                  f"{device.us(device.min_gap_ticks):.6g} µs minimum gap between "
                                                  f"phases; stimgrid uses {device.us(device.min_gap_ticks):.6g} µs"))
        gap = device.min_gap_ticks
    elif gap_us and gap == 0:
        issues.append(Issue("SG105", "warning", f"gap_us {gap_us:g} rounds to 0 on a "
                                                f"{device.time_step_us:.6g} µs grid"))

    qmax = device.max_charge_per_phase_nc
    if qmax is not None and charge_nc > qmax + _EPS:
        issues.append(Issue("SG104", "error", f"charge per phase {charge_nc:g} nC is above the device's "
                                              f"{qmax:g} nC"))
        return _report(device, target, [], issues)

    if width_us is not None:
        w1, w_issue = _grid_width(width_us, device, "width_us")
        if w_issue:
            issues.append(w_issue)
        elif abs(device.us(w1) - width_us) / width_us > 0.10:
            issues.append(Issue("SG106", "warning", f"width_us {width_us:g} becomes {device.us(w1):.6g} after "
                                                    f"rounding ({(device.us(w1) - width_us) / width_us:+.1%})"))
        first_widths = range(w1, w1 + 1)
    else:
        first_widths = _first_widths(device, charge_nc, ratio)

    unit = (device.amplitude_step_ua or 1.0) * device.time_step_us * 1e-3  # nC per (step x tick)
    scored = []  # cheap ranking first; full results are built only for the pulses listed
    for n1 in first_widths:
        for n2 in _second_widths(device, n1, ratio):
            for k1, k2 in _amplitudes(device, charge_nc, n1, n2):
                q = k1 * n1 * unit
                if qmax is not None and q > qmax + _EPS:
                    continue
                cost = abs(q - charge_nc) / charge_nc + abs(n2 / n1 / ratio - 1)
                scored.append((cost, n1 + gap + n2, n1, k1, n2, k2))
    scored.sort()
    options = [_option(device, charge_nc, ratio, gap, n1, n2, k1, k2) for _, _, n1, k1, n2, k2 in scored[:count]]
    options = [o for o in options if o is not None]

    if not options:
        message = "no exactly balanced pulse on the device's grid fits this charge, ratio and the device's limits"
        if width_us is not None and device.max_amplitude_ua is not None:
            w = device.us(first_widths[0])
            need = charge_nc * 1e3 / (min(w, w * ratio))
            if need > device.max_amplitude_ua + _EPS:
                message += (f": {charge_nc:g} nC in {min(w, w * ratio):.6g} µs needs {need:.4g} µA, above the "
                            f"device's {device.max_amplitude_ua:g} µA")
        issues.append(Issue("SG701", "error", message))
    elif abs(options[0].pulse.charge_change) > 0.05:
        issues.append(Issue("SG702", "warning", f"the closest pulse changes charge per phase by "
                                                f"{options[0].pulse.charge_change:+.1%}"))
    return _report(device, target, options, issues)


def _report(device: Device, target: dict, options: List[Option], issues: List[Issue]) -> DesignReport:
    issues.sort(key=_issue_order)
    return DesignReport(asdict(device), target, options, issues)


def _first_widths(device: Device, charge_nc: float, ratio: float) -> range:
    """First-phase widths worth trying: from the shortest the limits allow to where the second phase
    would drop below one current step."""
    tick = device.time_step_us
    lo = max(device.min_ticks, math.ceil(device.min_ticks / ratio - 1e-9) - 1, 1)
    if device.max_amplitude_ua is not None:  # the stronger phase must stay within the maximum
        strongest = max(1.0, 1.0 / ratio)
        lo = max(lo, math.floor(charge_nc * 1e3 * strongest / (device.max_amplitude_ua * tick) - 1e-9))
    hi = lo + MAX_FIRST_WIDTHS - 1
    if not device.continuous_amplitude:  # each phase needs at least one current step
        weakest = max(1.0, ratio)
        hi = min(hi, math.ceil(charge_nc * 1e3 / (device.amplitude_step_ua * tick * weakest) * 1.25) + 2)
    top = device.max_ticks
    if top is not None:
        hi = min(hi, top, math.floor(top / ratio) + 1)
    return range(max(lo, 1), hi + 1)


def _second_widths(device: Device, n1: int, ratio: float) -> List[int]:
    x = n1 * ratio
    top = device.max_ticks
    near = {math.floor(x) - 1, math.floor(x), math.ceil(x), math.ceil(x) + 1}
    return sorted(n for n in near if n >= device.min_ticks and (top is None or n <= top))


def _amplitudes(device: Device, charge_nc: float, n1: int, n2: int):
    """(first, second) amplitudes giving exactly equal charge: steps on a gridded device, µA otherwise."""
    tick = device.time_step_us
    if device.continuous_amplitude:
        a1 = charge_nc * 1e3 / (n1 * tick)
        a2 = charge_nc * 1e3 / (n2 * tick)
        top = device.max_amplitude_ua
        if top is None or max(a1, a2) <= top + _EPS:
            yield a1, a2
        return
    units = charge_nc * 1e3 / (device.amplitude_step_ua * tick)  # charge in (step x tick) units
    lcm = n1 * n2 // math.gcd(n1, n2)
    top = device.max_amplitude_steps
    for m in {max(1, math.floor(units / lcm)), max(1, math.ceil(units / lcm))}:
        p = m * lcm
        k1, k2 = p // n1, p // n2
        if top is None or max(k1, k2) <= top:
            yield k1, k2


def _option(device: Device, charge_nc: float, ratio: float, gap: int, n1: int, n2: int, k1, k2) -> Optional[Option]:
    tick = device.time_step_us
    plan = Pulse(charge_nc * 1e3 / (n1 * tick), n1 * tick, second_amplitude_ua=charge_nc * 1e3 / (n2 * tick),
                 second_width_us=n2 * tick, gap_us=device.us(gap))
    d = _deliver(plan, device, k1, n1, k2, n2, gap)
    qmax = device.max_charge_per_phase_nc
    if d.imbalance > _EPS or (qmax is not None and max(d.first_charge_nc, d.second_charge_nc) > qmax + _EPS):
        return None
    actual = n2 / n1
    return Option(d, actual, actual / ratio - 1)
