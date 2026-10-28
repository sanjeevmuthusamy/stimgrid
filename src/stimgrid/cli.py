"""Command line.

  stimgrid fit FILE|-     fit the pulse in a request file (or stdin)
  stimgrid quick ...      fit a pulse given as flags
  stimgrid check FILE     check an existing Intan RHX or MC_Stimulus file
  stimgrid devices        list the built-in device presets

fit, quick and check take --electrode-diameter-um (or --electrode-area-um2) to also
report charge density and the Shannon k value.

fit and quick take --export intan-rhx|mcs-ascii --out FILE to write the pulse in a
file the stimulator's software can load.

Exit codes: 0 fitted with no errors (and no warnings with --strict), 1 errors
(or warnings with --strict), 2 bad input or usage, 3 internal error.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from typing import List, Optional

from . import __version__
from .check import KINDS, CheckError, CheckReport, check_file
from .devices import PRESETS, Device, override, preset
from .electrode import Density, Electrode
from .formats import FORMATS, ExportError, export
from .fit import Delivered, FitReport, Pulse, Rates, fit
from .loader import InputError, load

EXIT_OK, EXIT_ISSUES, EXIT_INPUT, EXIT_INTERNAL = 0, 1, 2, 3
NOTE = "Values are what the device grid allows; check them on your own hardware. Research use only."


def _f(x: Optional[float], digits: int = 6) -> str:
    return "-" if x is None else f"{x:.{digits}g}"


def _row(label: str, d: Delivered, ref: str = "plan") -> str:
    second = f"{_f(d.second_amplitude_ua)} µA x {_f(d.second_width_us)} µs" if d.biphasic else "(monophasic)"
    return (f"  {label:<9} {_f(d.amplitude_ua)} µA x {_f(d.width_us)} µs | gap {_f(d.gap_us)} µs | {second}\n"
            f"  {'':<9} charge {_f(d.first_charge_nc, 5)} nC ({d.charge_change:+.2%} vs {ref}), "
            f"net {_f(d.net_charge_nc, 4)} nC, imbalance {d.imbalance:.4%}")


def _density(d: Optional[Density]) -> List[str]:
    if d is None:
        return []
    return [f"  electrode {_f(d.area_um2)} µm²: {_f(d.charge_nc, 4)} nC per phase, "
            f"{_f(d.density_uc_cm2, 4)} µC/cm², Shannon k {d.shannon_k:.3f} "
            f"(limit {d.k_limit:g} allows {_f(d.max_charge_nc, 4)} nC)"]


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
    lines += _density(report.density)
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


def _export(report: FitReport, device: Device, args: argparse.Namespace) -> int:
    if not args.out:
        raise InputError("--export needs --out FILE (or --out - to print it)")
    if not report.ok:
        print(render(report))
        print("stimgrid: nothing exported: the fit has errors", file=sys.stderr)
        return EXIT_ISSUES
    channels = [c for c in args.channels.split(",") if c.strip()] if args.channels else None
    text = export(report, device, args.export, use=args.use, pulses=args.pulses, channels=channels,
                  negative_first=args.first_phase == "negative")
    if args.out == "-":
        sys.stdout.write(text)
        failed = args.strict and bool(report.issues)
        return EXIT_ISSUES if failed else EXIT_OK
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    code = _emit(report, args.format, args.strict)
    print(f"stimgrid: wrote {args.export} file {args.out}", file=sys.stderr)
    return code


def render_check(report: CheckReport, path: str) -> str:
    lines = [f"file: {path}: {report.description}"]
    for ch in report.channels:
        if ch.status != "checked":
            lines.append(f"{'channel ' if ch.channel.isdigit() else ''}{ch.channel}: {ch.status}: {ch.note}")
            continue
        lines.append(f"{'channel ' if ch.channel.isdigit() else ''}{ch.channel}:")
        for p in ch.pulses:
            t = p.report.target
            second = (f"{_f(t['second_amplitude_ua'])} µA x {_f(t['second_width_us'])} µs" if t["biphasic"]
                      else "(monophasic)")
            times = f"{p.count} pulse{'s' if p.count != 1 else ''}"
            if p.period_us:
                times += f" every {_f(p.period_us)} µs"
            first = f"{_f(t['amplitude_ua'])} µA x {_f(t['width_us'])} µs"
            lines.append(f"  in file   {first} | gap {_f(t['gap_us'])} µs | {second}  ({times})")
            lines.append(_row("delivered", p.report.rounded, "file"))
            if not p.balanced and p.report.balanced is not None:
                lines.append(_row("balanced", p.report.balanced, "file"))
            lines += _density(p.report.density)
            for i in p.report.issues:
                if i.code != "SG201":
                    lines.append(f"  {i.severity.upper():<7} {i.code} {i.message}")
        lines.append(f"  net charge over the whole channel: {_f(ch.net_charge_nc, 4)} nC "
                     f"({'balanced' if ch.balanced else 'NOT balanced'})")
        for i in ch.issues:
            lines.append(f"  {i.severity.upper():<7} {i.code} {i.message}")
    counts = {s: sum(c.status == s for c in report.channels) for s in ("checked", "off", "skipped")}
    lines += ["", f"summary: {len(report.channels)} channel(s): {counts['checked']} checked "
                  f"({len(report.unbalanced)} not balanced), {counts['off']} off, {counts['skipped']} skipped",
              f"result: {'OK' if report.ok else 'NOT OK'}", NOTE]
    return "\n".join(lines)


def _emit_check(report: CheckReport, path: str, fmt: str, strict: bool) -> int:
    if fmt == "json":
        data = report.to_dict()
        data.update({"tool": "stimgrid", "tool_version": __version__, "file": path, "note": NOTE})
        print(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))
    else:
        print(render_check(report, path))
    warnings = any(i.severity == "warning" for c in report.channels
                   for i in c.issues + [j for p in c.pulses for j in p.report.issues if j.code != "SG201"])
    return EXIT_ISSUES if (not report.ok or (strict and warnings)) else EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stimgrid", description="Fit stimulation pulses to what a stimulator can "
                                     "actually produce, and find the nearest exactly charge-balanced pulse.")
    parser.add_argument("--version", action="version", version=f"stimgrid {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def electrode(p: argparse.ArgumentParser) -> None:
        g = p.add_argument_group("electrode (charge density and Shannon k)")
        g.add_argument("--electrode-area-um2", type=float, help="geometric area of the electrode")
        g.add_argument("--electrode-diameter-um", type=float, help="or the diameter of a disc electrode")
        g.add_argument("--k-limit", type=float, help="warn above this Shannon k (default 1.75)")

    def output(p: argparse.ArgumentParser) -> None:
        p.add_argument("--format", choices=("text", "json", "pulse"), default="text",
                       help="pulse prints only the best pulse as JSON")
        p.add_argument("--strict", action="store_true", help="treat warnings as failures (exit code 1)")
        e = p.add_argument_group("export")
        e.add_argument("--export", choices=FORMATS, help="write the pulse for the stimulator's own software")
        e.add_argument("--out", help="file to write ('-' prints it instead of the report)")
        e.add_argument("--use", default="best", help="best (default), rounded, balanced or option-N")
        e.add_argument("--channels", help="comma-separated: RHX names such as A-000,A-001, or MCS numbers such as 1,2")
        e.add_argument("--pulses", type=int, default=1, help="pulses in the train (needs a rate when above 1)")
        e.add_argument("--first-phase", choices=("negative", "positive"), default="negative",
                       help="sign of the first phase (default negative)")

    p_fit = sub.add_parser("fit", help="fit the pulse in a request file ('-' reads stdin)")
    p_fit.add_argument("file")
    electrode(p_fit)
    output(p_fit)

    q = sub.add_parser("quick", help="fit a pulse given as flags")
    q.add_argument("--device", choices=sorted(PRESETS), help="start from a preset")
    q.add_argument("--step-ua", type=float, help="intan-rhs2116: current step size")
    q.add_argument("--sample-rate-hz", type=float, help="intan-rhs2116, tdt-iz2: sampling rate")
    q.add_argument("--range-ua", type=float, help="mcs-stg4000, mcs-stg5, tdt-iz2: current range (max µA)")
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
    electrode(q)
    output(q)

    c = sub.add_parser("check", help="check an existing Intan RHX or MC_Stimulus ASCII file")
    c.add_argument("file")
    c.add_argument("--kind", choices=KINDS, help="the file type (detected from the file by default)")
    c.add_argument("--sample-rate-hz", type=float, help="Intan RHX: when the file does not give it")
    c.add_argument("--step-ua", type=float, help="Intan RHX: when the file does not give it")
    c.add_argument("--device", choices=("mcs-stg4000", "mcs-stg5"),
                   help="MC_Stimulus: the stimulator (default: STG4000 for II files, STG5 for III)")
    c.add_argument("--range-ua", type=float, help="MC_Stimulus: the stimulator's current range (max µA)")
    c.add_argument("--max-imbalance", type=float, default=0.0)
    c.add_argument("--format", choices=("text", "json"), default="text")
    c.add_argument("--strict", action="store_true", help="treat warnings as failures (exit code 1)")
    electrode(c)

    sub.add_parser("devices", help="list the built-in device presets")
    return parser


def _quick_device(args: argparse.Namespace) -> Device:
    changes = dict(time_step_us=args.time_step_us, amplitude_step_ua=args.amplitude_step_ua,
                   max_amplitude_ua=args.max_amplitude_ua, max_charge_per_phase_nc=args.max_charge_per_phase_nc)
    if args.device:
        given = (("step_ua", args.step_ua), ("sample_rate_hz", args.sample_rate_hz), ("range_ua", args.range_ua))
        options = {k: v for k, v in given if v is not None}
        accepted = inspect.signature(PRESETS[args.device]).parameters
        wrong = [k for k in options if k not in accepted]
        if wrong:
            flags = ", ".join("--" + k.replace("_", "-") for k in wrong)
            raise ValueError(f"{flags} does not apply to {args.device}")
        return override(preset(args.device, **options), **changes)
    if args.time_step_us is None:
        raise ValueError("give --device or at least --time-step-us")
    return Device(name="command line", **{k: v for k, v in changes.items() if v is not None})


def _electrode(args: argparse.Namespace, given: Optional[Electrode] = None) -> Optional[Electrode]:
    """The electrode from the flags; for fit, flags replace the request file's electrode."""
    area, diameter, k = args.electrode_area_um2, args.electrode_diameter_um, args.k_limit
    if area is None and diameter is None:
        if k is None:
            return given
        if given is None:
            raise InputError("--k-limit needs --electrode-area-um2 or --electrode-diameter-um")
        area, diameter = given.area_um2, given.diameter_um
    options = {"area_um2": area, "diameter_um": diameter}
    if k is not None:
        options["k_limit"] = k
    elif given is not None:
        options["k_limit"] = given.k_limit
    try:
        return Electrode(**options)
    except ValueError as exc:
        raise InputError(str(exc)) from None


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
        if args.command == "check":
            report = check_file(args.file, args.kind, sample_rate_hz=args.sample_rate_hz, step_ua=args.step_ua,
                                device=args.device, range_ua=args.range_ua, max_imbalance=args.max_imbalance,
                                electrode=_electrode(args))
            return _emit_check(report, args.file, args.format, args.strict)
        if args.command == "fit":
            pulse, device, rates, options = load(args.file)
            options["electrode"] = _electrode(args, options.get("electrode"))
        else:
            try:
                device = _quick_device(args)
                pulse = Pulse(args.amplitude_ua, args.width_us, biphasic=not args.monophasic,
                              second_amplitude_ua=args.second_amplitude_ua, second_width_us=args.second_width_us,
                              gap_us=args.gap_us)
                rates = Rates(args.frequency_hz, args.intra_burst_hz, args.burst_rate_hz)
            except ValueError as exc:
                raise InputError(str(exc)) from None
            options = {"max_imbalance": args.max_imbalance, "keep_first": args.keep_first,
                       "electrode": _electrode(args)}
        try:
            report = fit(pulse, device, rates, **options)
        except ValueError as exc:
            raise InputError(str(exc)) from None
        if args.export:
            return _export(report, device, args)
        return _emit(report, args.format, args.strict)
    except (InputError, ExportError, CheckError, OSError, UnicodeDecodeError) as exc:
        print(f"stimgrid: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except Exception as exc:  # noqa: BLE001 - report, don't crash with a traceback
        print(f"stimgrid: internal error: {exc!r}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
