"""Command line.

  stimgrid fit FILE|-     fit the pulse in a request file (or stdin)
  stimgrid quick ...      fit a pulse given as flags
  stimgrid devices        list the built-in device presets

Exit codes: 0 fitted with no errors (and no warnings with --strict), 1 errors
(or warnings with --strict), 2 bad input or usage, 3 internal error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from .devices import PRESETS, Device, override, preset
from .fit import Delivered, FitReport, Pulse, Rates, fit
from .loader import InputError, load

EXIT_OK, EXIT_ISSUES, EXIT_INPUT, EXIT_INTERNAL = 0, 1, 2, 3
NOTE = "Values are what the device grid allows; check them on your own hardware. Research use only."


def _f(x: Optional[float], digits: int = 6) -> str:
    return "-" if x is None else f"{x:.{digits}g}"


def _row(label: str, d: Delivered) -> str:
    second = f"{_f(d.second_amplitude_ua)} µA x {_f(d.second_width_us)} µs" if d.biphasic else "(monophasic)"
    return (f"  {label:<9} {_f(d.amplitude_ua)} µA x {_f(d.width_us)} µs | gap {_f(d.gap_us)} µs | {second}\n"
            f"  {'':<9} charge {_f(d.first_charge_nc, 5)} nC ({d.charge_change:+.2%} vs plan), "
            f"net {_f(d.net_charge_nc, 4)} nC, imbalance {d.imbalance:.4%}")


def render(report: FitReport) -> str:
    t = report.target
    second = (f"{_f(t['second_amplitude_ua'] or t['amplitude_ua'])} µA x {_f(t['second_width_us'] or t['width_us'])} µs"
              if t["biphasic"] else "(monophasic)")
    dev = report.device
    amp = "continuous" if dev["amplitude_step_ua"] is None else f"{_f(dev['amplitude_step_ua'])} µA steps"
    lines = [
        f"device: {dev['name']}: {_f(dev['time_step_us'])} µs time steps, {amp}"
        + (f", max {_f(dev['max_amplitude_ua'])} µA" if dev["max_amplitude_ua"] else ""),
        f"  plan      {_f(t['amplitude_ua'])} µA x {_f(t['width_us'])} µs | gap {_f(t['gap_us'])} µs | {second}",
        _row("rounded", report.rounded),
    ]
    if report.balanced is not None:
        lines.append(_row("balanced", report.balanced))
        for i, alt in enumerate(report.alternatives, 1):
            lines.append(_row(f"option {i}", alt))
    for r in report.rates:
        lines.append(f"  rate      {r.name} {_f(r.target_hz)} Hz -> {_f(r.delivered_hz)} Hz "
                     f"({r.period_ticks} steps per period, {r.change:+.3%})")
    lines.append("")
    for i in report.issues:
        lines.append(f"{i.severity.upper():<7} {i.code} {i.message}")
    if report.issues:
        lines.append("")
    lines.append(f"best pulse: {json.dumps(report.best.as_pulse())}")
    lines.append(f"result: {'OK' if report.ok else 'ERROR'}")
    lines.append(NOTE)
    return "\n".join(lines)


def _emit(report: FitReport, fmt: str, strict: bool) -> int:
    if fmt == "json":
        data = report.to_dict()
        data.update({"tool": "stimgrid", "tool_version": __version__, "note": NOTE})
        print(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))
    elif fmt == "pulse":
        print(json.dumps(report.best.as_pulse()))
    else:
        print(render(report))
    failed = (not report.ok) or (strict and bool(report.issues))
    return EXIT_ISSUES if failed else EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stimgrid", description="Fit stimulation pulses to what a stimulator can "
                                     "actually produce, and find the nearest exactly charge-balanced pulse.")
    parser.add_argument("--version", action="version", version=f"stimgrid {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def output(p: argparse.ArgumentParser) -> None:
        p.add_argument("--format", choices=("text", "json", "pulse"), default="text",
                       help="pulse prints only the best pulse as JSON")
        p.add_argument("--strict", action="store_true", help="treat warnings as failures (exit code 1)")

    p_fit = sub.add_parser("fit", help="fit the pulse in a request file ('-' reads stdin)")
    p_fit.add_argument("file")
    output(p_fit)

    q = sub.add_parser("quick", help="fit a pulse given as flags")
    q.add_argument("--device", choices=sorted(PRESETS), help="start from a preset")
    q.add_argument("--step-ua", type=float, help="intan-rhs2116: current step size")
    q.add_argument("--sample-rate-hz", type=float, help="intan-rhs2116: host sampling rate")
    q.add_argument("--time-step-us", type=float)
    q.add_argument("--amplitude-step-ua", type=float)
    q.add_argument("--max-amplitude-ua", type=float)
    q.add_argument("--max-charge-per-phase-nc", type=float)
    q.add_argument("--amplitude-ua", type=float, required=True)
    q.add_argument("--width-us", type=float, required=True)
    q.add_argument("--second-amplitude-ua", type=float)
    q.add_argument("--second-width-us", type=float)
    q.add_argument("--gap-us", type=float, default=0.0)
    q.add_argument("--monophasic", action="store_true")
    q.add_argument("--frequency-hz", type=float)
    q.add_argument("--intra-burst-hz", type=float)
    q.add_argument("--burst-rate-hz", type=float)
    q.add_argument("--max-imbalance", type=float, default=0.0)
    q.add_argument("--keep-first", action="store_true", help="balance with the second phase only")
    output(q)

    sub.add_parser("devices", help="list the built-in device presets")
    return parser


def _quick_device(args: argparse.Namespace) -> Device:
    changes = dict(time_step_us=args.time_step_us, amplitude_step_ua=args.amplitude_step_ua,
                   max_amplitude_ua=args.max_amplitude_ua, max_charge_per_phase_nc=args.max_charge_per_phase_nc)
    if args.device:
        options = {k: v for k, v in (("step_ua", args.step_ua), ("sample_rate_hz", args.sample_rate_hz))
                   if v is not None}
        if options and args.device != "intan-rhs2116":
            raise ValueError("--step-ua and --sample-rate-hz only apply to intan-rhs2116")
        return override(preset(args.device, **options), **changes)
    if args.time_step_us is None:
        raise ValueError("give --device or at least --time-step-us")
    return Device(name="command line", **{k: v for k, v in changes.items() if v is not None})


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)  # usage errors exit with 2
    try:
        if args.command == "devices":
            for name in sorted(PRESETS):
                d = preset(name)
                amp = "continuous amplitude" if d.continuous_amplitude else f"{d.amplitude_step_ua:g} µA steps"
                print(f"{name}: {d.time_step_us:.6g} µs time steps, {amp}\n  source: {d.source}")
                if d.note:
                    print(f"  note: {d.note}")
            return EXIT_OK
        if args.command == "fit":
            pulse, device, rates, options = load(args.file)
        else:
            try:
                device = _quick_device(args)
                pulse = Pulse(args.amplitude_ua, args.width_us, biphasic=not args.monophasic,
                              second_amplitude_ua=args.second_amplitude_ua, second_width_us=args.second_width_us,
                              gap_us=args.gap_us)
                rates = Rates(args.frequency_hz, args.intra_burst_hz, args.burst_rate_hz)
            except ValueError as exc:
                raise InputError(str(exc)) from None
            options = {"max_imbalance": args.max_imbalance, "keep_first": args.keep_first}
        try:
            report = fit(pulse, device, rates, **options)
        except ValueError as exc:
            raise InputError(str(exc)) from None
        return _emit(report, args.format, args.strict)
    except (InputError, OSError) as exc:
        print(f"stimgrid: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except Exception as exc:  # noqa: BLE001 - report, don't crash with a traceback
        print(f"stimgrid: internal error: {exc!r}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
