"""Read a fit request (format version 1). Errors name the exact place, e.g. pulse.width_us.

{
  "stimgrid": 1,
  "device": {"preset": "intan-rhs2116", "step_ua": 0.1, "sample_rate_hz": 30000},
  "pulse": {"amplitude_ua": 7.3, "width_us": 117, "second_amplitude_ua": 1.9, "second_width_us": 450},
  "rates": {"frequency_hz": 40},
  "max_imbalance": 0,
  "keep_first": false
}

A device is a preset (with its options, plus any field to override) or a full description:
{"name": "my stimulator", "time_step_us": 10, "amplitude_step_ua": 0.5, "max_amplitude_ua": 100}
"""

from __future__ import annotations

import inspect
import json
import math
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, Tuple, Union

from .devices import PRESETS, Device, override, preset
from .fit import Pulse, Rates

FORMAT_VERSION = 1
DEVICE_FIELDS = tuple(f.name for f in fields(Device))
TOP_FIELDS = ("stimgrid", "device", "pulse", "rates", "max_imbalance", "keep_first", "search_steps")


class InputError(ValueError):
    """The request is not valid. The message starts with where the problem is."""


def from_dict(data: Any) -> Tuple[Pulse, Device, Rates, Dict[str, Any]]:
    """Returns the pulse, the device, the rates and the fit options."""
    _object(data, "request")
    _known(data, TOP_FIELDS, "request")
    if data.get("stimgrid") != FORMAT_VERSION:
        raise InputError(f"stimgrid: must be {FORMAT_VERSION} (the format version), got {data.get('stimgrid')!r}")
    for key in ("device", "pulse"):
        if key not in data:
            raise InputError(f"request: missing '{key}'")
    device = device_from_dict(data["device"], "device")
    pulse = _build(Pulse, data["pulse"], "pulse")
    rates = _build(Rates, data.get("rates", {}), "rates")
    options = {}
    if "max_imbalance" in data:
        v = data["max_imbalance"]
        if not (_number(v) and 0 <= v < 1):
            raise InputError(f"max_imbalance: must be a number from 0 up to (not including) 1, got {json.dumps(v)}")
        options["max_imbalance"] = float(v)
    if "keep_first" in data:
        if not isinstance(data["keep_first"], bool):
            raise InputError("keep_first: must be true or false")
        options["keep_first"] = data["keep_first"]
    if "search_steps" in data:
        v = data["search_steps"]
        if not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 20):
            raise InputError("search_steps: must be a whole number from 0 to 20")
        options["search_steps"] = v
    return pulse, device, rates, options


def device_from_dict(spec: Any, path: str = "device") -> Device:
    _object(spec, path)
    if "preset" in spec:
        name = spec["preset"]
        if name not in PRESETS:
            raise InputError(f"{path}.preset: unknown preset {name!r}; known: {', '.join(sorted(PRESETS))}")
        option_names = tuple(inspect.signature(PRESETS[name]).parameters)
        _known(spec, ("preset",) + option_names + DEVICE_FIELDS, path)
        options = {k: spec[k] for k in option_names if k in spec}
        changes = {k: spec[k] for k in DEVICE_FIELDS if k in spec}
        for k, v in {**options, **changes}.items():
            if k not in ("name", "source", "note") and not _number(v):
                raise InputError(f"{path}.{k}: must be a number, got {json.dumps(v)}")
        base = _wrap(path, lambda: preset(name, **options))
        return _wrap(path, lambda: override(base, **changes))
    _known(spec, DEVICE_FIELDS, path)
    if "name" not in spec or "time_step_us" not in spec:
        raise InputError(f"{path}: give a 'preset', or at least 'name' and 'time_step_us'")
    return _build(Device, spec, path)


def load(source: Union[str, Path]) -> Tuple[Pulse, Device, Rates, Dict[str, Any]]:
    """Read a request from a file path, or from stdin when source is '-'."""
    try:
        if str(source) == "-":
            data = json.load(sys.stdin)
        else:
            with open(source, encoding="utf-8") as fh:
                data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise InputError(f"input: not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})") from None
    return from_dict(data)


def _build(cls, spec: Any, path: str):
    _object(spec, path)
    _known(spec, tuple(f.name for f in fields(cls)), path)
    return _wrap(path, lambda: cls(**spec))


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _object(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise InputError(f"{path}: must be an object")


def _known(spec: Dict[str, Any], allowed: Tuple[str, ...], path: str) -> None:
    unknown = sorted(set(spec) - set(allowed))
    if unknown:
        raise InputError(f"{path}: unknown field(s) {', '.join(unknown)}")


def _wrap(path: str, make):
    try:
        return make()
    except InputError:
        raise
    except (ValueError, TypeError) as exc:
        raise InputError(f"{path}: {exc}") from None
