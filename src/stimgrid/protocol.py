"""Fit a whole protocol: a list of steps (single pulses, trains, bursts and rests) on one device.

Each step's pulse is fitted as usual. The protocol report adds what only shows up across steps:
how long each step really takes once its rates are rounded to whole time steps, how many pulses
each channel gets, the total charge per phase and the net charge left over the whole protocol.

{
  "stimgrid": 1,
  "device": {"preset": "mcs-stg4000"},
  "protocol": [
    {"name": "baseline", "pulse": {"amplitude_ua": 20, "width_us": 100}, "pulses": 60, "frequency_hz": 1},
    {"name": "pause", "rest_s": 30},
    {"name": "theta", "pulse": {...}, "bursts": 10, "pulses": 4, "intra_burst_hz": 100, "burst_rate_hz": 5,
     "channels": ["1", "2"]}
  ],
  "repeat": 1,
  "use": "best"
}

A train step's time is pulses x period (the last period included); a burst step's is bursts x burst
period. Rests are taken as given.

A train step can ramp its amplitude: "ramp": {"up_pulses": 10, "down_pulses": 10, "start_fraction": 0.1}
scales both phases' amplitudes from 10% up to the full pulse over the first 10 pulses, and back down over
the last 10. Each ramp level is fitted to the device on its own, since rounding a smaller amplitude can
unbalance a pulse that is balanced at full amplitude.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from .devices import Device
from .electrode import Electrode
from .fit import Delivered, FitReport, Issue, Pulse, Rates, assess_density, fit
from .loader import (FORMAT_VERSION, InputError, _build, _known, _number, _object, _wrap, device_from_dict,
                     fit_options, read_json)

USES = ("best", "rounded")
MAX_COUNT = 10_000_000
MAX_RAMP_PULSES = 1000
STEP_FIELDS = ("name", "pulse", "pulses", "frequency_hz", "bursts", "intra_burst_hz", "burst_rate_hz", "rest_s",
               "channels", "ramp")
RAMP_FIELDS = ("up_pulses", "down_pulses", "start_fraction")
PROTOCOL_FIELDS = ("stimgrid", "device", "protocol", "repeat", "use", "max_imbalance", "keep_first",
                   "search_steps", "electrode")
_RATES = ("frequency_hz", "intra_burst_hz", "burst_rate_hz")


@dataclass(frozen=True)
class Ramp:
    """Scale both phases' amplitudes from start_fraction up to the full pulse over the first up_pulses
    pulses of a train, and back down to start_fraction over the last down_pulses."""

    up_pulses: int = 0
    down_pulses: int = 0
    start_fraction: float = 0.1

    def __post_init__(self) -> None:
        for name in ("up_pulses", "down_pulses"):
            v = getattr(self, name)
            if not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_RAMP_PULSES):
                raise ValueError(f"{name} must be a whole number from 0 to {MAX_RAMP_PULSES:,}")
        if self.up_pulses + self.down_pulses == 0:
            raise ValueError("a ramp needs up_pulses or down_pulses")
        if not (_number(self.start_fraction) and 0 < self.start_fraction < 1):
            raise ValueError("start_fraction must be above 0 and below 1")

    @property
    def pulses(self) -> int:
        return self.up_pulses + self.down_pulses

    def fractions(self) -> List[float]:
        """The amplitude scale of each ramp pulse, up then down; the rest of the train runs at 1."""
        s = self.start_fraction
        up = [s + (1 - s) * i / self.up_pulses for i in range(self.up_pulses)]
        down = [s + (1 - s) * (self.down_pulses - 1 - k) / self.down_pulses for k in range(self.down_pulses)]
        return up + down


@dataclass(frozen=True)
class Step:
    """One step: a pulse given pulses times (a train), in bursts, or a rest of rest_s seconds."""

    pulse: Optional[Pulse] = None
    pulses: int = 1  # pulses in the train, or per burst
    frequency_hz: Optional[float] = None
    bursts: Optional[int] = None
    intra_burst_hz: Optional[float] = None
    burst_rate_hz: Optional[float] = None
    rest_s: Optional[float] = None
    channels: Tuple[str, ...] = ()
    name: str = ""
    ramp: Optional[Ramp] = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise ValueError("name must be text")
        given = [n for n in _RATES + ("bursts",) if getattr(self, n) is not None]
        if self.pulse is None:
            if self.rest_s is None:
                raise ValueError("give a pulse, or rest_s for a rest")
            _positive("rest_s", self.rest_s)
            extra = given + (["pulses"] if self.pulses != 1 else []) + (["channels"] if self.channels else [])
            extra += ["ramp"] if self.ramp is not None else []
            if extra:
                raise ValueError(f"a rest takes only rest_s and name, not {', '.join(extra)}")
            return
        if not isinstance(self.pulse, Pulse):
            raise ValueError("pulse must be a Pulse")
        if self.rest_s is not None:
            raise ValueError("rest_s is for a step without a pulse; put the rest in a step of its own")
        _count("pulses", self.pulses)
        for n in _RATES:
            if getattr(self, n) is not None:
                _positive(n, getattr(self, n))
        if self.bursts is None:
            if self.intra_burst_hz is not None or self.burst_rate_hz is not None:
                raise ValueError("intra_burst_hz and burst_rate_hz need bursts")
            if self.pulses > 1 and self.frequency_hz is None:
                raise ValueError("more than one pulse needs frequency_hz")
        else:
            _count("bursts", self.bursts)
            if self.frequency_hz is not None:
                raise ValueError("a burst step uses intra_burst_hz and burst_rate_hz, not frequency_hz")
            if self.pulses > 1 and self.intra_burst_hz is None:
                raise ValueError("more than one pulse per burst needs intra_burst_hz")
            if self.bursts > 1 and self.burst_rate_hz is None:
                raise ValueError("more than one burst needs burst_rate_hz")
        if not isinstance(self.channels, tuple) or not all(isinstance(c, str) and c.strip() for c in self.channels):
            raise ValueError("channels must be a list of channel names")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("a channel is listed twice")
        if self.ramp is not None:
            if not isinstance(self.ramp, Ramp):
                raise ValueError("ramp must be a Ramp")
            if self.bursts is not None:
                raise ValueError("a ramp is for a train step, not bursts")
            if self.ramp.pulses > self.pulses:
                raise ValueError(f"the ramp's {self.ramp.pulses:,} pulses are more than the train's "
                                 f"{self.pulses:,}")

    @property
    def kind(self) -> str:
        return "rest" if self.pulse is None else ("burst" if self.bursts is not None else "train")

    @property
    def count(self) -> int:
        """Pulses in one run of the step."""
        return 0 if self.pulse is None else self.pulses * (self.bursts or 1)


@dataclass(frozen=True)
class Protocol:
    steps: Tuple[Step, ...]
    repeat: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.steps, tuple) or not self.steps or not all(isinstance(s, Step) for s in self.steps):
            raise ValueError("steps must be a non-empty list of steps")
        if all(s.pulse is None for s in self.steps):
            raise ValueError("at least one step needs a pulse")
        _count("repeat", self.repeat)


@dataclass
class RampLevel:
    fraction: float  # of the full pulse's amplitudes
    pulses: int  # ramp pulses at this level in one run of the step
    report: FitReport
    used: Delivered

    def balanced(self, max_imbalance: float) -> bool:
        return self.used.imbalance <= max_imbalance + 1e-9

    def to_dict(self, max_imbalance: float) -> dict:
        return {"fraction": self.fraction, "pulses": self.pulses, "used_pulse": self.used.as_pulse(),
                "balanced": self.balanced(max_imbalance), "net_charge_nc": self.used.net_charge_nc,
                "imbalance": self.used.imbalance, "issues": [vars(i) for i in self.report.issues]}


@dataclass
class StepReport:
    step: Step
    report: Optional[FitReport]  # None for a rest
    used: Optional[Delivered]  # the pulse the protocol uses: best or rounded
    planned_us: float
    delivered_us: float
    issues: List[Issue] = field(default_factory=list)
    ramp: List[RampLevel] = field(default_factory=list)  # lowest level first

    @property
    def count(self) -> int:
        return self.step.count

    @property
    def full_count(self) -> int:
        """Pulses at the full amplitude: all of them, less any ramp."""
        return self.count - sum(r.pulses for r in self.ramp)

    @property
    def net_charge_nc(self) -> float:
        if self.used is None:
            return 0.0
        return self.used.net_charge_nc * self.full_count + sum(r.used.net_charge_nc * r.pulses for r in self.ramp)

    @property
    def phase_charge_nc(self) -> float:
        """First-phase charge summed over the step's pulses."""
        if self.used is None:
            return 0.0
        return self.used.first_charge_nc * self.full_count + sum(r.used.first_charge_nc * r.pulses
                                                                  for r in self.ramp)

    @property
    def balanced(self) -> bool:
        if self.used is None:
            return True
        limit = self.report.max_imbalance
        return self.used.imbalance <= limit + 1e-9 and all(r.balanced(limit) for r in self.ramp)

    @property
    def all_issues(self) -> List[Issue]:
        return self.issues + (self.report.issues if self.report else [])

    def to_dict(self) -> dict:
        s = self.step
        ramp = None
        if s.ramp is not None:
            ramp = {**asdict(s.ramp), "levels": [r.to_dict(self.report.max_imbalance) for r in self.ramp]}
        return {
            "name": s.name, "kind": s.kind, "channels": list(s.channels), "count": self.count,
            "pulses": s.pulses if s.pulse else None, "bursts": s.bursts, "rest_s": s.rest_s,
            "planned_s": self.planned_us / 1e6, "delivered_s": self.delivered_us / 1e6,
            "balanced": self.balanced, "net_charge_nc": self.net_charge_nc, "phase_charge_nc": self.phase_charge_nc,
            "used_pulse": self.used.as_pulse() if self.used else None,
            "ramp": ramp,
            "fit": self.report.to_dict() if self.report else None,
            "issues": [vars(i) for i in self.issues],
        }


@dataclass
class ChannelTotal:
    channel: str  # "" for steps that name no channel
    pulses: int
    phase_charge_nc: float
    net_charge_nc: float
    balanced: bool


@dataclass
class ProtocolReport:
    device: dict
    steps: List[StepReport]
    repeat: int
    use: str
    max_imbalance: float

    @property
    def pulses(self) -> int:
        return sum(s.count for s in self.steps) * self.repeat

    @property
    def planned_us(self) -> float:
        return sum(s.planned_us for s in self.steps) * self.repeat

    @property
    def delivered_us(self) -> float:
        return sum(s.delivered_us for s in self.steps) * self.repeat

    @property
    def net_charge_nc(self) -> float:
        return sum(s.net_charge_nc for s in self.steps) * self.repeat

    @property
    def phase_charge_nc(self) -> float:
        return sum(s.phase_charge_nc for s in self.steps) * self.repeat

    @property
    def balanced(self) -> bool:
        return all(s.balanced for s in self.steps)

    @property
    def ok(self) -> bool:
        """No errors in any step, and every pulse used is balanced."""
        return self.balanced and not any(i.severity == "error" for s in self.steps for i in s.all_issues)

    def channels(self) -> List[ChannelTotal]:
        """Totals per channel named in the steps; steps that name none are grouped under ''."""
        if not any(s.step.channels for s in self.steps):
            return []
        totals: Dict[str, ChannelTotal] = {}
        for s in self.steps:
            if s.step.pulse is None:
                continue
            for c in s.step.channels or ("",):
                t = totals.setdefault(c, ChannelTotal(c, 0, 0.0, 0.0, True))
                t.pulses += s.count * self.repeat
                t.phase_charge_nc += s.phase_charge_nc * self.repeat
                t.net_charge_nc += s.net_charge_nc * self.repeat
                t.balanced = t.balanced and s.balanced
        return list(totals.values())

    def to_dict(self) -> dict:
        return {
            "device": self.device, "use": self.use, "repeat": self.repeat, "max_imbalance": self.max_imbalance,
            "ok": self.ok, "balanced": self.balanced, "pulses": self.pulses,
            "planned_s": self.planned_us / 1e6, "delivered_s": self.delivered_us / 1e6,
            "net_charge_nc": self.net_charge_nc, "phase_charge_nc": self.phase_charge_nc,
            "steps": [s.to_dict() for s in self.steps],
            "channels": [vars(c) for c in self.channels()],
        }


def fit_protocol(protocol: Protocol, device: Device, *, use: str = "best", max_imbalance: float = 0.0,
                 keep_first: bool = False, search_steps: int = 2,
                 electrode: Optional[Electrode] = None) -> ProtocolReport:
    """Fit every step of a protocol. use: 'best' (the balanced pulse when there is one) or 'rounded'."""
    if use not in USES:
        raise ValueError(f"use must be {' or '.join(USES)}, got {use!r}")
    steps = []
    for step in protocol.steps:
        if step.pulse is None:
            steps.append(StepReport(step, None, None, step.rest_s * 1e6, step.rest_s * 1e6))
            continue
        rates = (Rates(frequency_hz=step.frequency_hz) if step.kind == "train"
                 else Rates(intra_burst_hz=step.intra_burst_hz, burst_rate_hz=step.burst_rate_hz))
        report = fit(step.pulse, device, rates, max_imbalance=max_imbalance, keep_first=keep_first,
                     search_steps=search_steps)
        used = report.best if use == "best" else report.rounded
        if electrode is not None:
            assess_density(report, used, electrode)
        timed = _timed(step, report, used, device)
        if step.ramp is not None:
            options = dict(max_imbalance=max_imbalance, keep_first=keep_first, search_steps=search_steps)
            timed.ramp, ramp_issues = _ramp(step, report, device, use, options)
            timed.issues += ramp_issues
        steps.append(timed)
    return ProtocolReport(asdict(device), steps, protocol.repeat, use, max_imbalance)


def _ramp(step: Step, report: FitReport, device: Device, use: str,
          options: Dict[str, Any]) -> Tuple[List[RampLevel], List[Issue]]:
    """Fit each ramp level, and gather its findings by code so a long ramp gives one line per kind."""
    counts: Dict[float, int] = {}
    for f in step.ramp.fractions():
        key = round(f, 12)
        counts[key] = counts.get(key, 0) + 1
    period = next((r.period_ticks * device.time_step_us for r in report.rates if r.name == "frequency_hz"), None)
    p = step.pulse
    levels, found = [], {}
    for f in sorted(counts):
        scaled = replace(p, amplitude_ua=p.amplitude_ua * f,
                         second_amplitude_ua=None if p.second_amplitude_ua is None else p.second_amplitude_ua * f)
        level_report = fit(scaled, device, **options)
        used = level_report.best if use == "best" else level_report.rounded
        levels.append(RampLevel(f, counts[f], level_report, used))
        issues = list(level_report.issues)
        if period is not None and used.duration_us > period + 1e-6:
            issues.append(Issue("SG602", "error", f"the pulse ({used.duration_us:.6g} µs) is longer than one "
                                                  f"period ({period:.6g} µs)"))
        for i in issues:
            found.setdefault((i.code, i.severity), []).append((f, i.message))
    gathered = []
    for (code, severity), hits in found.items():
        where = ", ".join(_percent(f) for f, _ in hits[:5]) + (", ..." if len(hits) > 5 else "")
        first = _percent(hits[0][0])
        if len(hits) == 1:
            message = f"ramp level {first}: {hits[0][1]}"
        else:
            message = f"{len(hits)} ramp levels ({where}); at {first}: {hits[0][1]}"
        gathered.append(Issue(code, severity, message))
    gathered.sort(key=lambda i: ({"error": 0, "warning": 1}.get(i.severity, 2), i.code))
    return levels, gathered


def _percent(fraction: float) -> str:
    return f"{fraction * 100:.4g}%"


def _timed(step: Step, report: FitReport, used: Delivered, device: Device) -> StepReport:
    tick = device.time_step_us
    ticks = {r.name: r.period_ticks for r in report.rates}

    def period(name: str, hz: float) -> Tuple[float, float]:  # planned and delivered, µs
        delivered = ticks[name] * tick if name in ticks else 1e6 / hz  # no ticks: SG302 is already reported
        return 1e6 / hz, delivered

    p = step.pulse
    pulse_planned = p.width_us + ((p.gap_us + p.w2) if p.biphasic else 0.0)
    issues: List[Issue] = []
    if step.kind == "train":
        if step.frequency_hz is None:
            planned, delivered = pulse_planned, used.duration_us
        else:
            per_p, per_d = period("frequency_hz", step.frequency_hz)
            planned, delivered = step.pulses * per_p, step.pulses * per_d
    else:
        if step.intra_burst_hz is None:
            inner_p, inner_d, active = pulse_planned, used.duration_us, used.duration_us
        else:
            per_p, per_d = period("intra_burst_hz", step.intra_burst_hz)
            inner_p, inner_d = step.pulses * per_p, step.pulses * per_d
            active = (step.pulses - 1) * per_d + used.duration_us
        if step.burst_rate_hz is None:
            planned, delivered = inner_p, inner_d
        else:
            per_p, per_d = period("burst_rate_hz", step.burst_rate_hz)
            planned, delivered = step.bursts * per_p, step.bursts * per_d
            if active > per_d + 1e-6:
                issues.append(Issue("SG601", "error", f"a burst ({active:.6g} µs from its first pulse to the end "
                                                      f"of its last) is longer than one burst period "
                                                      f"({per_d:.6g} µs)"))
    return StepReport(step, report, used, planned, delivered, issues)


# --- reading ---------------------------------------------------------------------------------------------------

def protocol_from_dict(data: Any) -> Tuple[Protocol, Device, Dict[str, Any]]:
    """Returns the protocol, the device and the options for fit_protocol."""
    _object(data, "request")
    if "pulse" in data:
        raise InputError("request: give 'pulse' or 'protocol', not both (a protocol's pulses go in its steps)")
    _known(data, PROTOCOL_FIELDS, "request")
    if data.get("stimgrid") != FORMAT_VERSION:
        raise InputError(f"stimgrid: must be {FORMAT_VERSION} (the format version), got {data.get('stimgrid')!r}")
    for key in ("device", "protocol"):
        if key not in data:
            raise InputError(f"request: missing '{key}'")
    device = device_from_dict(data["device"], "device")
    if not isinstance(data["protocol"], list) or not data["protocol"]:
        raise InputError("protocol: must be a non-empty list of steps")
    steps = tuple(_step(spec, f"protocol[{n}]") for n, spec in enumerate(data["protocol"]))
    repeat = data.get("repeat", 1)
    if not _whole(repeat):
        raise InputError(f"repeat: must be a whole number from 1 to {MAX_COUNT:,}")
    protocol = _wrap("protocol", lambda: Protocol(steps, repeat))
    options = fit_options(data)
    if "use" in data:
        if data["use"] not in USES:
            raise InputError(f"use: must be {' or '.join(repr(u) for u in USES)}, got {data['use']!r}")
        options["use"] = data["use"]
    return protocol, device, options


def load_protocol(source) -> Tuple[Protocol, Device, Dict[str, Any]]:
    """Read a protocol request from a file path, or from stdin when source is '-'."""
    return protocol_from_dict(read_json(source))


def is_protocol(data: Any) -> bool:
    return isinstance(data, dict) and "protocol" in data


def _step(spec: Any, path: str) -> Step:
    _object(spec, path)
    _known(spec, STEP_FIELDS, path)
    args: Dict[str, Any] = {}
    if "pulse" in spec:
        args["pulse"] = _build(Pulse, spec["pulse"], f"{path}.pulse")
    for k in _RATES + ("rest_s",):
        if k in spec:
            if not _number(spec[k]):
                raise InputError(f"{path}.{k}: must be a number, got {spec[k]!r}")
            args[k] = spec[k]
    for k in ("pulses", "bursts"):
        if k in spec:
            if not _whole(spec[k]):
                raise InputError(f"{path}.{k}: must be a whole number from 1 to {MAX_COUNT:,}")
            args[k] = spec[k]
    if "channels" in spec:
        ch = spec["channels"]
        if not isinstance(ch, list) or not all(isinstance(c, (str, int)) and not isinstance(c, bool) for c in ch):
            raise InputError(f"{path}.channels: must be a list of channel names, such as [\"A-000\"] or [1, 2]")
        args["channels"] = tuple(str(c) for c in ch)
    if "name" in spec:
        args["name"] = spec["name"]
    if "ramp" in spec:
        args["ramp"] = _build(Ramp, spec["ramp"], f"{path}.ramp")
    return _wrap(path, lambda: Step(**args))


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_COUNT


def _count(name: str, v: Any) -> None:
    if not _whole(v):
        raise ValueError(f"{name} must be a whole number from 1 to {MAX_COUNT:,}")


def _positive(name: str, v: Any) -> None:
    if not (_number(v) and v > 0):
        raise ValueError(f"{name} must be a positive number")
