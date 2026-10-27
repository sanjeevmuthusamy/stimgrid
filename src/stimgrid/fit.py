"""Fit a planned pulse to a device's grid.

Two answers come back:

- rounded: what the device delivers if each value is simply rounded to its grid.
  An asymmetric pulse that was charge-balanced on paper often is not after this.
- balanced: the nearest pulse on the grid whose two phases carry equal charge,
  found by searching small changes to both phases (mostly the second phase's
  width and amplitude, the usual balancing knobs).

On a gridded device charge balance is checked exactly, in whole amplitude steps
times whole time steps, so "balanced" means equal to the last bit, not "close".
Pulse rates are also rounded to whole periods and the delivered rate reported.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Tuple

from .devices import Device, _positive, _round_half_up

MAX_SECOND_WIDTH_CANDIDATES = 4001
_EPS = 1e-9


@dataclass(frozen=True)
class Pulse:
    """The pulse you planned. Units: µA and µs."""

    amplitude_ua: float
    width_us: float
    biphasic: bool = True
    second_amplitude_ua: Optional[float] = None  # defaults to amplitude_ua
    second_width_us: Optional[float] = None  # defaults to width_us
    gap_us: float = 0.0

    def __post_init__(self) -> None:
        _positive("amplitude_ua", self.amplitude_ua)
        _positive("width_us", self.width_us)
        if not (isinstance(self.gap_us, (int, float)) and not isinstance(self.gap_us, bool)
                and math.isfinite(self.gap_us) and self.gap_us >= 0):
            raise ValueError("gap_us must be zero or a positive number")
        if self.second_amplitude_ua is not None:
            _positive("second_amplitude_ua", self.second_amplitude_ua)
        if self.second_width_us is not None:
            _positive("second_width_us", self.second_width_us)
        if not isinstance(self.biphasic, bool):
            raise ValueError("biphasic must be true or false")

    @property
    def a2(self) -> float:
        return self.amplitude_ua if self.second_amplitude_ua is None else self.second_amplitude_ua

    @property
    def w2(self) -> float:
        return self.width_us if self.second_width_us is None else self.second_width_us


@dataclass(frozen=True)
class Rates:
    """Pulse rates to round to whole periods. All optional."""

    frequency_hz: Optional[float] = None  # uniform train
    intra_burst_hz: Optional[float] = None  # pulses inside a burst
    burst_rate_hz: Optional[float] = None  # bursts per second

    def __post_init__(self) -> None:
        for name in ("frequency_hz", "intra_burst_hz", "burst_rate_hz"):
            if getattr(self, name) is not None:
                _positive(name, getattr(self, name))

    def items(self) -> Iterable[Tuple[str, float]]:
        for name in ("frequency_hz", "intra_burst_hz", "burst_rate_hz"):
            value = getattr(self, name)
            if value is not None:
                yield name, value


@dataclass
class Delivered:
    """A pulse exactly as the device can produce it."""

    amplitude_ua: float
    width_us: float
    biphasic: bool
    second_amplitude_ua: Optional[float]
    second_width_us: Optional[float]
    gap_us: float
    width_ticks: int
    second_width_ticks: Optional[int]
    gap_ticks: int
    amplitude_steps: Optional[int]  # None when amplitude is continuous
    second_amplitude_steps: Optional[int]
    first_charge_nc: float
    second_charge_nc: float
    net_charge_nc: float
    imbalance: float  # |Q1 - Q2| / max(Q1, Q2); 0 is exactly balanced
    duration_us: float
    duration_ticks: int
    charge_change: float  # first-phase charge versus the planned pulse, as a fraction

    def as_pulse(self) -> dict:
        """The pulse in the common JSON shape (amplitude_ua, width_us, ...)."""
        out = {"amplitude_ua": _clean(self.amplitude_ua), "width_us": _clean(self.width_us)}
        if not self.biphasic:
            out["biphasic"] = False
        else:
            out["second_amplitude_ua"] = _clean(self.second_amplitude_ua)
            out["second_width_us"] = _clean(self.second_width_us)
        if self.gap_ticks:
            out["gap_us"] = _clean(self.gap_us)
        return out


@dataclass
class RateFit:
    name: str
    target_hz: float
    period_ticks: int
    delivered_hz: float
    change: float  # delivered versus target, as a fraction


@dataclass
class Issue:
    code: str
    severity: str  # "error" or "warning"
    message: str


@dataclass
class FitReport:
    device: dict
    target: dict
    rounded: Delivered
    balanced: Optional[Delivered]  # None for monophasic pulses
    alternatives: List[Delivered]
    rates: List[RateFit]
    issues: List[Issue]
    max_imbalance: float

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def best(self) -> Delivered:
        return self.balanced or self.rounded

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        data["best_pulse"] = self.best.as_pulse()
        return data


def fit(pulse: Pulse, device: Device, rates: Optional[Rates] = None, max_imbalance: float = 0.0,
        keep_first: bool = False, search_steps: int = 2) -> FitReport:
    """Round a pulse to a device's grid, and find the nearest exactly balanced version.

    max_imbalance: the imbalance accepted as balanced (0 means exact).
    keep_first: keep the first phase as rounded and balance with the second phase only.
    search_steps: how many grid steps the first phase may move either way while searching.
    """
    if not (isinstance(max_imbalance, (int, float)) and 0 <= max_imbalance < 1):
        raise ValueError("max_imbalance must be at least 0 and below 1")
    if not (isinstance(search_steps, int) and 0 <= search_steps <= 20):
        raise ValueError("search_steps must be a whole number from 0 to 20")
    rates = rates or Rates()
    issues: List[Issue] = []

    a1, a1_issue = _grid_amplitude(pulse.amplitude_ua, device, "amplitude_ua")
    w1, w1_issue = _grid_width(pulse.width_us, device, "width_us")
    gap = device.ticks(pulse.gap_us)
    issues += [i for i in (a1_issue, w1_issue) if i]
    if pulse.biphasic:
        a2, a2_issue = _grid_amplitude(pulse.a2, device, "second_amplitude_ua")
        w2, w2_issue = _grid_width(pulse.w2, device, "second_width_us")
        issues += [i for i in (a2_issue, w2_issue) if i]
    else:
        a2 = w2 = None
    rounded = _deliver(pulse, device, a1, w1, a2, w2, gap)
    if pulse.gap_us and gap == 0:
        issues.append(Issue("SG105", "warning", f"gap_us {pulse.gap_us:g} rounds to 0 on a "
                                                f"{device.time_step_us:.6g} µs grid"))
    for name, want, got in _moved(pulse, rounded):
        issues.append(Issue("SG106", "warning", f"{name} {want:g} becomes {got:.6g} after rounding "
                                                f"({(got - want) / want:+.1%})"))

    balanced, alternatives = None, []
    if pulse.biphasic:
        if rounded.imbalance > max_imbalance + _EPS:
            issues.append(Issue("SG201", "warning",
                                f"simple rounding leaves the pulse unbalanced (imbalance {rounded.imbalance:.4%}, "
                                f"net {rounded.net_charge_nc:.4g} nC per pulse)"))
        ranked = _search(pulse, device, a1, w1, w2, gap, max_imbalance, keep_first, search_steps)
        if ranked:
            balanced, alternatives = ranked[0], ranked[1:4]
            if balanced.imbalance > max_imbalance + _EPS:
                issues.append(Issue("SG202", "error",
                                    f"no pulse within the search is balanced to {max_imbalance:.4%}; the best "
                                    f"found has imbalance {balanced.imbalance:.4%}"))
            if abs(balanced.charge_change) > 0.05:
                issues.append(Issue("SG203", "warning",
                                    f"the balanced pulse changes charge per phase by {balanced.charge_change:+.1%}"))
        else:
            issues.append(Issue("SG202", "error", "no pulse on the device grid fits the search range and limits"))

    best = balanced or rounded
    if device.max_charge_per_phase_nc is not None:
        q = max(best.first_charge_nc, best.second_charge_nc)
        if q > device.max_charge_per_phase_nc + _EPS:
            issues.append(Issue("SG104", "error", f"charge per phase {q:.4g} nC is above the device's "
                                                  f"{device.max_charge_per_phase_nc:g} nC"))
    rate_fits = _rates(rates, device, best, issues)
    issues.sort(key=lambda i: (i.severity != "error", i.code))
    return FitReport(asdict(device), asdict(pulse), rounded, balanced, alternatives, rate_fits, issues,
                     max_imbalance)


# --- grid rounding -----------------------------------------------------------------------------------------------

def _grid_amplitude(value: float, device: Device, name: str) -> Tuple[float, Optional[Issue]]:
    """Amplitude as steps (int) on a gridded device, or µA (float) on a continuous one."""
    if device.continuous_amplitude:
        if device.max_amplitude_ua is not None and value > device.max_amplitude_ua + _EPS:
            return device.max_amplitude_ua, Issue("SG101", "error", f"{name} {value:g} µA is above the device's "
                                                                    f"{device.max_amplitude_ua:g} µA")
        return value, None
    steps = device.amp_steps(value)
    top = device.max_amplitude_steps
    if steps < 1:
        return 1, Issue("SG103", "error", f"{name} {value:g} µA is below one amplitude step "
                                          f"({device.amplitude_step_ua:g} µA)")
    if top is not None and steps > top:
        return top, Issue("SG101", "error", f"{name} {value:g} µA is above the device's "
                                            f"{device.ua(top):g} µA")
    return steps, None


def _grid_width(value: float, device: Device, name: str) -> Tuple[int, Optional[Issue]]:
    ticks = device.ticks(value)
    if ticks < device.min_ticks:
        return device.min_ticks, Issue("SG102", "error", f"{name} {value:g} µs is shorter than the device's "
                                                         f"{device.us(device.min_ticks):.6g} µs minimum")
    top = device.max_ticks
    if top is not None and ticks > top:
        return top, Issue("SG102", "error", f"{name} {value:g} µs is longer than the device's "
                                            f"{device.us(top):.6g} µs maximum")
    return ticks, None


def _deliver(pulse: Pulse, device: Device, a1, w1: int, a2, w2: Optional[int], gap: int) -> Delivered:
    gridded = not device.continuous_amplitude
    a1_ua = device.ua(a1) if gridded else a1
    q1 = a1_ua * device.us(w1) * 1e-3
    target_q1 = pulse.amplitude_ua * pulse.width_us * 1e-3
    if pulse.biphasic:
        a2_ua = device.ua(a2) if gridded else a2
        q2 = a2_ua * device.us(w2) * 1e-3
        if gridded:  # exact: compare whole steps x whole ticks
            p1, p2 = a1 * w1, a2 * w2
            imbalance = abs(p1 - p2) / max(p1, p2)
            if p1 == p2:
                q2 = q1  # equal to the last bit, so no float noise in the net charge
        else:
            imbalance = abs(q1 - q2) / max(q1, q2)
            if imbalance < 1e-12:
                imbalance, q2 = 0.0, q1  # balanced within float precision: report a clean zero net charge
        ticks = w1 + gap + w2
    else:
        a2_ua, q2, imbalance, ticks = None, 0.0, 1.0, w1
    return Delivered(
        amplitude_ua=a1_ua,
        width_us=device.us(w1),
        biphasic=pulse.biphasic,
        second_amplitude_ua=a2_ua,
        second_width_us=device.us(w2) if pulse.biphasic else None,
        gap_us=device.us(gap),
        width_ticks=w1,
        second_width_ticks=w2 if pulse.biphasic else None,
        gap_ticks=gap,
        amplitude_steps=a1 if gridded else None,
        second_amplitude_steps=a2 if (gridded and pulse.biphasic) else None,
        first_charge_nc=q1,
        second_charge_nc=q2,
        net_charge_nc=q1 - q2,
        imbalance=imbalance,
        duration_us=device.us(ticks),
        duration_ticks=ticks,
        charge_change=(q1 - target_q1) / target_q1,
    )


# --- balanced search ---------------------------------------------------------------------------------------------

def _search(pulse: Pulse, device: Device, a1, w1: int, w2: int, gap: int, max_imbalance: float,
            keep_first: bool, search_steps: int) -> List[Delivered]:
    gridded = not device.continuous_amplitude
    r = 0 if keep_first else search_steps
    top_ticks = device.max_ticks
    top_steps = device.max_amplitude_steps

    if gridded:
        first_amps = [a for a in range(a1 - r, a1 + r + 1) if a >= 1 and (top_steps is None or a <= top_steps)]
    else:
        first_amps = [a1]  # continuous: the first phase is kept; the second absorbs the balance
    first_widths = [w for w in range(w1 - r, w1 + r + 1)
                    if w >= device.min_ticks and (top_ticks is None or w <= top_ticks)]

    half = max(20, w2 // 2)
    lo = max(device.min_ticks, w2 - half)
    hi = w2 + half if top_ticks is None else min(top_ticks, w2 + half)
    if hi - lo + 1 > MAX_SECOND_WIDTH_CANDIDATES:
        lo, hi = max(lo, w2 - MAX_SECOND_WIDTH_CANDIDATES // 2), min(hi, w2 + MAX_SECOND_WIDTH_CANDIDATES // 2)
    second_widths = range(lo, hi + 1)

    step = device.amplitude_step_ua if gridded else 1.0
    tick = device.time_step_us
    qmax = device.max_charge_per_phase_nc
    t_a1, t_w1, t_a2, t_w2 = pulse.amplitude_ua, pulse.width_us, pulse.a2, pulse.w2
    t_q1 = t_a1 * t_w1 * 1e-3

    pairs = []
    for fw in first_widths:
        amps = list(first_amps)
        if not gridded:  # also try the amplitude that keeps the planned charge on this width
            keep = t_q1 * 1e3 / (fw * tick)
            if device.max_amplitude_ua is None or keep <= device.max_amplitude_ua + _EPS:
                amps.append(keep)
        pairs += [(fa, fw) for fa in amps]

    scored = []  # (over tolerance, cost, imbalance, candidate)
    for fa, fw in pairs:
        a1u = fa * step
        w1u = fw * tick
        q1 = a1u * w1u * 1e-3
        if qmax is not None and q1 > qmax + _EPS:
            continue
        dq = abs(q1 - t_q1) / t_q1
        base = 2 * dq + abs(a1u - t_a1) / t_a1 + abs(w1u - t_w1) / t_w1
        for sw in second_widths:
            w2u = sw * tick
            for sa in _second_amplitudes(fa, fw, sw, device):
                a2u = sa * step
                if gridded:
                    p1, p2 = fa * fw, sa * sw
                    imb = abs(p1 - p2) / max(p1, p2)
                else:
                    q2 = a2u * w2u * 1e-3
                    imb = abs(q1 - q2) / max(q1, q2)
                    imb = 0.0 if imb < 1e-12 else imb
                if qmax is not None and a2u * w2u * 1e-3 > qmax + _EPS:
                    continue
                cost = base + abs(a2u - t_a2) / t_a2 + abs(w2u - t_w2) / t_w2
                scored.append((imb > max_imbalance + _EPS, cost, imb, (fa, fw, sa, sw)))
    feasible = [s for s in scored if not s[0]]
    if feasible:
        feasible.sort(key=lambda s: (s[1], s[2], s[3]))
        chosen = feasible
    else:
        scored.sort(key=lambda s: (s[2], s[1], s[3]))
        chosen = scored
    return [_deliver(pulse, device, fa, fw, sa, sw, gap) for _, _, _, (fa, fw, sa, sw) in chosen[:4]]


def _second_amplitudes(fa, fw: int, sw: int, device: Device) -> List:
    if device.continuous_amplitude:
        a = fa * fw / sw  # exact balance
        if device.max_amplitude_ua is not None and a > device.max_amplitude_ua + _EPS:
            return []
        return [a]
    x = fa * fw / sw
    top = device.max_amplitude_steps
    return [a for a in {math.floor(x), math.ceil(x)} if a >= 1 and (top is None or a <= top)]



# --- rates -------------------------------------------------------------------------------------------------------

def _rates(rates: Rates, device: Device, best: Delivered, issues: List[Issue]) -> List[RateFit]:
    out = []
    for name, hz in rates.items():
        period = _round_half_up(1e6 / (hz * device.time_step_us))
        if period < 1:
            issues.append(Issue("SG302", "error", f"{name} {hz:g} Hz is faster than one time step"))
            continue
        delivered = 1e6 / (period * device.time_step_us)
        change = (delivered - hz) / hz
        out.append(RateFit(name, hz, period, delivered, change))
        if abs(change) > 0.001:
            issues.append(Issue("SG301", "warning", f"{name} {hz:g} Hz is delivered as {delivered:.6g} Hz "
                                                    f"({change:+.3%}) on a whole number of time steps"))
        if name != "burst_rate_hz" and best.duration_ticks > period:
            issues.append(Issue("SG303", "error", f"the pulse ({best.duration_us:.6g} µs) is longer than one "
                                                  f"period at {name} ({device.us(period):.6g} µs)"))
        if name != "burst_rate_hz" and device.max_frequency_hz is not None and \
                delivered > device.max_frequency_hz + _EPS:
            issues.append(Issue("SG304", "error", f"{name} {delivered:.6g} Hz is above the device's "
                                                  f"{device.max_frequency_hz:g} Hz"))
    return out


def _moved(pulse: Pulse, d: Delivered, limit: float = 0.10) -> List[Tuple[str, float, float]]:
    """Planned values that rounding moves by more than limit (a fraction)."""
    pairs = [("amplitude_ua", pulse.amplitude_ua, d.amplitude_ua), ("width_us", pulse.width_us, d.width_us),
             ("gap_us", pulse.gap_us, d.gap_us)]
    if pulse.biphasic:
        pairs += [("second_amplitude_ua", pulse.a2, d.second_amplitude_ua),
                  ("second_width_us", pulse.w2, d.second_width_us)]
    return [(n, w, g) for n, w, g in pairs if w > 0 and abs(g - w) / w > limit]


def _clean(x: Optional[float]) -> Optional[float]:
    """Drop float noise such as 100.00000000000001 in exported values."""
    if x is None:
        return None
    r = round(x, 9)
    return int(r) if r == int(r) else r
