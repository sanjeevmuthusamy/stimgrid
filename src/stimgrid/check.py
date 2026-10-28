"""Check an existing stimulation file: what each channel delivers, and whether it is balanced.

Reads the two formats stimgrid exports:

- Intan RHX stimulation settings (.xml, saved with File > Save Stimulation Settings).
- MCS MC_Stimulus II / III ASCII import, format 4 ("value time" rows), current mode.

Each pulse is rounded to the device grid the way the vendor software does it (nearest step,
halves up), so the result is what the stimulator delivers, not what the file says.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .devices import INTAN_RHS2116_STEPS_UA, Device, intan_rhs2116, mcs_stg4000, mcs_stg5, override
from .fit import FitReport, Issue, Pulse, Rates, fit
from .formats import INTAN_RHX_MAX_PHASE_US, INTAN_RHX_MAX_PULSES, INTAN_RHX_SAMPLE_RATES, MCS_TIME_GRID_US

KINDS = ("intan-rhx", "mcs-ascii")


class CheckError(ValueError):
    """The file cannot be read as a stimulation file."""


@dataclass
class PulseCheck:
    """One pulse shape in a channel, how often it occurs, and its fit."""

    count: int
    report: FitReport
    period_us: Optional[float] = None

    @property
    def net_charge_nc(self) -> float:
        """Net charge per pulse as delivered (the rounded pulse)."""
        return self.report.rounded.net_charge_nc

    @property
    def balanced(self) -> bool:
        return self.report.rounded.imbalance <= self.report.max_imbalance + 1e-9


@dataclass
class ChannelCheck:
    channel: str
    status: str  # "checked", "off" or "skipped"
    note: str = ""
    pulses: List[PulseCheck] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)

    @property
    def net_charge_nc(self) -> float:
        """Net charge over every pulse the channel delivers in one run of the file."""
        return sum(p.net_charge_nc * p.count for p in self.pulses)

    @property
    def balanced(self) -> bool:
        return all(p.balanced for p in self.pulses)

    @property
    def ok(self) -> bool:
        """No error in the file itself or in delivering it (a missing balanced suggestion does not count)."""
        errors = [i for i in self.issues if i.severity == "error"]
        errors += [i for p in self.pulses for i in p.report.issues if i.severity == "error" and i.code != "SG202"]
        return not errors

    def to_dict(self) -> dict:
        return {
            "channel": self.channel, "status": self.status, "note": self.note,
            "balanced": self.balanced, "net_charge_nc": self.net_charge_nc,
            "pulses": [{"count": p.count, "period_us": p.period_us, "balanced": p.balanced,
                        "fit": p.report.to_dict()} for p in self.pulses],
            "issues": [vars(i) for i in self.issues],
        }


@dataclass
class CheckReport:
    kind: str
    device: Device
    description: str
    channels: List[ChannelCheck]

    @property
    def checked(self) -> List[ChannelCheck]:
        return [c for c in self.channels if c.status == "checked"]

    @property
    def unbalanced(self) -> List[ChannelCheck]:
        return [c for c in self.checked if not c.balanced]

    @property
    def ok(self) -> bool:
        """True when every checked channel is balanced and nothing would stop the file working."""
        return bool(self.checked) and not self.unbalanced and all(c.ok for c in self.channels)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "description": self.description, "ok": self.ok,
                "channels": [c.to_dict() for c in self.channels]}


def detect(text: str) -> str:
    head = text.lstrip("﻿ \t\r\n")[:2000]
    if head.startswith("<") and "<intanrhx" in head.lower():
        return "intan-rhx"
    if head.lower().startswith("multi channel systems mc_stimulus"):
        return "mcs-ascii"
    if head.startswith("<"):
        raise CheckError("this XML is not an Intan RHX settings file (no IntanRHX element)")
    raise CheckError("not a known stimulation file: expected an Intan RHX .xml or an MC_Stimulus ASCII file")


def check_text(text: str, kind: Optional[str] = None, *, sample_rate_hz: Optional[float] = None,
               step_ua: Optional[float] = None, device: Optional[str] = None, range_ua: Optional[float] = None,
               max_imbalance: float = 0.0) -> CheckReport:
    kind = kind or detect(text)
    if kind == "intan-rhx":
        if device is not None or range_ua is not None:
            raise CheckError("--device and --range-ua apply to MC_Stimulus files only")
        return _check_rhx(text, sample_rate_hz, step_ua, max_imbalance)
    if kind == "mcs-ascii":
        if sample_rate_hz is not None or step_ua is not None:
            raise CheckError("--sample-rate-hz and --step-ua apply to Intan RHX files only")
        return _check_mcs(text, device, range_ua, max_imbalance)
    raise CheckError(f"unknown file kind {kind!r}; known: {', '.join(KINDS)}")


def check_file(path: str, kind: Optional[str] = None, **options) -> CheckReport:
    with open(path, encoding="utf-8-sig") as fh:
        return check_text(fh.read(), kind, **options)


# --- Intan RHX -----------------------------------------------------------------------------------------------------

def _check_rhx(text: str, sample_rate_hz, step_ua, max_imbalance: float) -> CheckReport:
    try:
        root = ET.fromstring(text.lstrip("﻿"))
    except ET.ParseError as exc:
        raise CheckError(f"not valid XML: {exc}") from None
    if root.tag.lower() != "intanrhx":
        raise CheckError("the root element is not IntanRHX")
    attrs = _lower(root.attrib)
    if attrs.get("type", "ControllerStimRecord").lower() != "controllerstimrecord":
        raise CheckError(f"the file is for a {attrs['type']} system, not the RHS Stim/Record Controller")
    rate = _rhx_rate(attrs.get("sampleratehertz"), sample_rate_hz)
    step = _rhx_step(attrs.get("stimstepsizemicroamps"), step_ua)
    dev = intan_rhs2116(step, rate)
    channels = [_rhx_channel(_lower(el.attrib), dev, max_imbalance)
                for el in root.iter() if el.tag.lower() == "stimchannel"]
    if not channels:
        raise CheckError("no StimChannel entries: save the file with File > Save Stimulation Settings in RHX")
    return CheckReport("intan-rhx", dev, f"Intan RHX, {rate:g} samples/s, {step:g} µA steps", channels)


def _rhx_rate(in_file: Optional[str], given: Optional[float]) -> float:
    rate = None
    if in_file not in (None, ""):
        try:
            rate = float(in_file)
        except ValueError:
            raise CheckError(f"SampleRateHertz {in_file!r} is not a number") from None
        if given is not None and not math.isclose(given, rate):
            raise CheckError(f"the file says {rate:g} samples/s but --sample-rate-hz is {given:g}")
    rate = rate if rate is not None else given
    if rate is None:
        raise CheckError("the file does not give its sample rate; add --sample-rate-hz")
    if not any(math.isclose(rate, r) for r in INTAN_RHX_SAMPLE_RATES):
        raise CheckError(f"{rate:g} samples/s is not a rate the RHS Stim/Record Controller runs at "
                         f"({', '.join(map(str, INTAN_RHX_SAMPLE_RATES))})")
    return rate


def _rhx_step(in_file: Optional[str], given: Optional[float]) -> float:
    step = None
    if in_file not in (None, ""):
        try:
            step = float(in_file)
        except ValueError:
            raise CheckError(f"StimStepSizeMicroAmps is {in_file!r}; give the step with --step-ua") from None
        if given is not None and not math.isclose(given, step):
            raise CheckError(f"the file says {step:g} µA steps but --step-ua is {given:g}")
    step = step if step is not None else given
    if step is None:
        raise CheckError("the file does not give its step size; add --step-ua")
    for s in INTAN_RHS2116_STEPS_UA:
        if math.isclose(step, s, rel_tol=1e-9):
            return s
    raise CheckError(f"{step:g} µA is not an RHS2116 step size")


_RHX_NEEDED = ("shape", "firstphasedurationmicroseconds", "secondphasedurationmicroseconds",
               "firstphaseamplitudemicroamps", "secondphaseamplitudemicroamps")


def _rhx_channel(a: Dict[str, str], dev: Device, max_imbalance: float) -> ChannelCheck:
    name = a.get("nativechannelname") or a.get("customchannelname") or "?"
    a = {k: v for k, v in a.items() if v.strip().upper() != "N/A"}
    if a.get("stimenabled", "").lower() != "true":
        return ChannelCheck(name, "off", "stimulation is off" if "stimenabled" in a
                            else "StimEnabled is not in the file (RHX keeps its current setting)")
    missing = [k for k in _RHX_NEEDED if k not in a]
    if missing:
        return ChannelCheck(name, "skipped", "not in the file, so RHX keeps its current values: "
                            + ", ".join(missing))
    shape = a["shape"].lower()
    if shape not in ("biphasic", "biphasicwithinterphasedelay"):
        return ChannelCheck(name, "skipped", f"shape {a['shape']} is not checked (only Biphasic and "
                                             f"BiphasicWithInterphaseDelay)")
    try:
        w1, w2 = _float(a, "firstphasedurationmicroseconds"), _float(a, "secondphasedurationmicroseconds")
        a1, a2 = _float(a, "firstphaseamplitudemicroamps"), _float(a, "secondphaseamplitudemicroamps")
        gap = _float(a, "interphasedelaymicroseconds", 0.0) if shape == "biphasicwithinterphasedelay" else 0.0
        train = a.get("pulseortrain", "SinglePulse").lower() == "pulsetrain"
        count = int(_float(a, "numberofstimpulses", 1.0)) if train else 1
        period = _float(a, "pulsetrainperiodmicroseconds", 0.0) if train else None
    except ValueError as exc:
        return ChannelCheck(name, "skipped", str(exc))

    issues: List[Issue] = []
    for label, us in (("first phase", w1), ("second phase", w2), ("interphase delay", gap)):
        if us > INTAN_RHX_MAX_PHASE_US:
            issues.append(Issue("SG401", "error", f"the {label} ({us:g} µs) is over RHX's "
                                                  f"{INTAN_RHX_MAX_PHASE_US:g} µs limit, so RHX will not load it"))
    if train and not 1 <= count <= INTAN_RHX_MAX_PULSES:
        issues.append(Issue("SG401", "error", f"NumberOfStimPulses {count} is outside RHX's 1 to "
                                              f"{INTAN_RHX_MAX_PULSES}"))
    if min(w1, w2) <= 0 or min(a1, a2) <= 0:
        issues.append(Issue("SG402", "error", "a phase has zero duration or zero amplitude, so the pulse "
                                              "cannot be balanced"))
        return ChannelCheck(name, "checked", "", [], issues)

    rates = Rates(frequency_hz=1e6 / period) if train and period and period > 0 else Rates()
    report = fit(Pulse(a1, w1, second_amplitude_ua=a2, second_width_us=w2, gap_us=gap), dev, rates,
                 max_imbalance=max_imbalance)
    if train and period is not None:
        report.issues = [i for i in report.issues if i.code != "SG303"]  # SG403 below says more
        if dev.ticks(period) < report.rounded.duration_ticks:
            issues.append(Issue("SG403", "error", f"the train period ({period:g} µs) is shorter than the pulse "
                                                  f"({report.rounded.duration_us:.6g} µs); RHX skips this channel "
                                                  f"when it uploads, with only a warning"))
    return ChannelCheck(name, "checked", "", [PulseCheck(count, report, period)], issues)


# --- MCS MC_Stimulus ASCII -----------------------------------------------------------------------------------------

def _check_mcs(text: str, device: Optional[str], range_ua: Optional[float], max_imbalance: float) -> CheckReport:
    lines = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    program = "III" if lines[0].lower().endswith("iii") else "II"
    header: Dict[str, str] = {}
    blocks: Dict[str, List[Tuple[float, float]]] = {}
    current: Optional[str] = None
    for n, ln in enumerate(lines[1:], 2):
        low = ln.lower()
        if low.startswith("ascii import"):
            continue
        if ":" in ln and not _is_row(ln):
            key, value = (s.strip() for s in ln.split(":", 1))
            if key.lower() == "channel":
                current = value
                if current in blocks:
                    raise CheckError(f"channel {current} appears twice")
                blocks[current] = []
            else:
                header[key.lower()] = value.lower()
            continue
        if low.replace("\t", " ").split() == ["value", "time"]:
            continue
        if current is None:
            raise CheckError(f"line {n}: a data row before any 'channel:' line")
        parts = ln.replace(",", " ").split()
        if len(parts) != 2:
            raise CheckError(f"line {n}: expected 'value time', got {ln!r}")
        try:
            blocks[current].append((float(parts[0]), float(parts[1])))
        except ValueError:
            raise CheckError(f"line {n}: expected two numbers, got {ln!r}") from None
    if header.get("output mode", "current") != "current":
        raise CheckError(f"output mode is {header['output mode']}; only current mode is checked")
    if header.get("format", "4") != "4":
        raise CheckError(f"format {header['format']} is not supported; only format 4 ('value time' rows) is")
    if not blocks:
        raise CheckError("no 'channel:' blocks in the file")

    dev = _mcs_device(device or ("mcs-stg5" if program == "III" else "mcs-stg4000"), range_ua)
    channels = [_mcs_channel(name, rows, dev, max_imbalance) for name, rows in blocks.items()]
    desc = (f"MC_Stimulus {program} ASCII, {dev.name}, {dev.amplitude_step_ua:g} µA steps, "
            f"times in {MCS_TIME_GRID_US:g} µs steps")
    return CheckReport("mcs-ascii", dev, desc, channels)


def _mcs_device(name: str, range_ua: Optional[float]) -> Device:
    makers = {"mcs-stg4000": mcs_stg4000, "mcs-stg5": mcs_stg5}
    if name not in makers:
        raise CheckError(f"--device for an MC_Stimulus file must be {' or '.join(makers)}")
    try:
        dev = makers[name](range_ua) if range_ua is not None else makers[name]()
    except ValueError as exc:
        raise CheckError(str(exc)) from None
    return override(dev, time_step_us=MCS_TIME_GRID_US)  # the import's grid


def _mcs_channel(name: str, rows: List[Tuple[float, float]], dev: Device, max_imbalance: float) -> ChannelCheck:
    issues: List[Issue] = []
    off_grid = sorted({t for _, t in rows if abs(t / MCS_TIME_GRID_US - round(t / MCS_TIME_GRID_US)) > 1e-6})
    if off_grid:
        issues.append(Issue("SG404", "error", f"times must be multiples of {MCS_TIME_GRID_US:g} µs; "
                                              f"found {', '.join(f'{t:g}' for t in off_grid[:5])} µs"))
    if any(t <= 0 for _, t in rows):
        issues.append(Issue("SG404", "error", "a row has a duration of zero or less"))
    if not any(v for v, _ in rows):
        return ChannelCheck(name, "off", "no current in this channel", issues=issues)

    groups: Dict[Tuple, List] = {}
    order: List[Tuple] = []
    for key in _mcs_pulses(rows):
        if key not in groups:
            groups[key] = [0]
            order.append(key)
        groups[key][0] += 1
    pulses = []
    for key in order:
        (v1, t1), gap, second = key
        if second is None:
            pulse = Pulse(abs(v1), t1, biphasic=False)
        else:
            v2, t2 = second
            pulse = Pulse(abs(v1), t1, second_amplitude_ua=abs(v2), second_width_us=t2, gap_us=gap)
        pulses.append(PulseCheck(groups[key][0], fit(pulse, dev, max_imbalance=max_imbalance)))
    return ChannelCheck(name, "checked", "", pulses, issues)


def _mcs_pulses(rows: List[Tuple[float, float]]):
    """Split rows into pulses: a phase, then an opposite-sign phase straight after it or after one
    zero-current row (the gap). Adjacent rows of the same sign are one phase."""
    phases: List[Tuple[float, float, float]] = []  # (value, time, zero time before it)
    zero = 0.0
    for v, t in rows:
        if v == 0:
            zero += t
            continue
        if phases and zero == 0 and phases[-1][0] == v:
            pv, pt, pz = phases[-1]
            phases[-1] = (pv, pt + t, pz)
        else:
            phases.append((v, t, zero))
        zero = 0.0
    i = 0
    while i < len(phases):
        v1, t1, _ = phases[i]
        if i + 1 < len(phases) and (phases[i + 1][0] > 0) != (v1 > 0):
            v2, t2, gap = phases[i + 1]
            yield (v1, t1), gap, (v2, t2)
            i += 2
        else:
            yield (v1, t1), 0.0, None
            i += 1


# --- helpers -------------------------------------------------------------------------------------------------------

def _lower(attrib: Dict[str, str]) -> Dict[str, str]:
    return {k.lower(): v for k, v in attrib.items()}


def _float(a: Dict[str, str], key: str, default: Optional[float] = None) -> float:
    if key not in a:
        if default is None:
            raise ValueError(f"{key} is missing")
        return default
    try:
        value = float(a[key])
    except ValueError:
        raise ValueError(f"{key} is {a[key]!r}, not a number") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{key} is {a[key]!r}, not a number of zero or more")
    return value


def _is_row(line: str) -> bool:
    parts = line.replace(",", " ").split()
    try:
        [float(p) for p in parts]
        return True
    except ValueError:
        return False
