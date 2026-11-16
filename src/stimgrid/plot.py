"""Draw a pulse as an SVG image: the plan dashed, the pulse on the device solid.

No plotting library is needed; the SVG is plain text. The first phase is drawn negative
(cathodic first) unless negative_first is False.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape

from .fit import Delivered

WIDTH, HEIGHT = 640, 300
LEFT, RIGHT, TOP, BOTTOM = 64, 24, 60, 44
PLAN_COLOR, PULSE_COLOR, AXIS_COLOR, TEXT_COLOR = "#8a8f96", "#1f6feb", "#c9ccd1", "#3d4146"


def _phases(p: dict) -> Tuple[float, float, float, float, float]:
    """(a1, w1, gap, a2, w2) from a pulse in the common JSON shape."""
    a1, w1 = float(p["amplitude_ua"]), float(p["width_us"])
    if p.get("biphasic", True) is False:
        return a1, w1, 0.0, 0.0, 0.0
    a2 = p.get("second_amplitude_ua")
    w2 = p.get("second_width_us")
    return (a1, w1, float(p.get("gap_us") or 0.0), float(a1 if a2 is None else a2), float(w1 if w2 is None else w2))


def _nice(x: float) -> float:
    """A round number (1, 2, 2.5 or 5 times a power of ten) at or just above x, for axis ticks."""
    if x <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if m * power >= x * (1 - 1e-12):
            return m * power
    return 10 * power


def _f(x: float) -> str:
    return f"{x:.6g}"


def pulse_svg(pulse, plan: Optional[dict] = None, title: str = "", negative_first: bool = True) -> str:
    """SVG of a pulse (a Delivered, or a dict like {"amplitude_ua": .., "width_us": .., ...}).

    plan: the planned pulse in the same dict shape, drawn dashed underneath.
    """
    shown = pulse.as_pulse() if isinstance(pulse, Delivered) else dict(pulse)
    if isinstance(pulse, Delivered):
        shown["gap_us"] = pulse.gap_us
    best = _phases(shown)
    drawn = [best] + ([_phases(plan)] if plan else [])
    total = max(w1 + gap + w2 for _, w1, gap, _, w2 in drawn) or 1.0
    amax = max(max(a1, a2) for a1, _, _, a2, _ in drawn) or 1.0
    lead = total * 0.06
    t0, t1 = -lead, total + lead
    top = _nice(amax)
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM
    sign = -1 if negative_first else 1

    def x(us: float) -> float:
        return LEFT + (us - t0) / (t1 - t0) * plot_w

    def y(ua: float) -> float:
        return TOP + (top - ua) / (2 * top) * plot_h

    def path(phases) -> str:
        a1, w1, gap, a2, w2 = phases
        pts: List[Tuple[float, float]] = [(t0, 0), (0, 0), (0, sign * a1), (w1, sign * a1), (w1, 0)]
        if w2:
            pts += [(w1 + gap, 0), (w1 + gap, -sign * a2), (w1 + gap + w2, -sign * a2), (w1 + gap + w2, 0)]
        pts.append((t1, 0))
        return "M " + " L ".join(f"{x(t):.2f} {y(a):.2f}" for t, a in pts)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" '
           f'height="{HEIGHT}" font-family="Helvetica, Arial, sans-serif" font-size="12">',
           f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>']
    if title:
        out.append(f'<text x="{LEFT}" y="22" font-size="14" fill="{TEXT_COLOR}">{escape(title)}</text>')
    for v in (-top, -top / 2, 0, top / 2, top):  # current axis
        out.append(f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y(v):.2f}" y2="{y(v):.2f}" '
                   f'stroke="{AXIS_COLOR}" stroke-width="{1 if v == 0 else 0.5}"/>')
        out.append(f'<text x="{LEFT - 8}" y="{y(v) + 4:.2f}" text-anchor="end" fill="{TEXT_COLOR}">{_f(v)}</text>')
    out.append(f'<text x="16" y="{TOP + plot_h / 2:.2f}" fill="{TEXT_COLOR}" text-anchor="middle" '
               f'transform="rotate(-90 16 {TOP + plot_h / 2:.2f})">current (µA)</text>')
    step = _nice(total / 5)
    t = 0.0
    while t <= total + 1e-9:  # time axis
        out.append(f'<text x="{x(t):.2f}" y="{HEIGHT - BOTTOM + 18}" text-anchor="middle" '
                   f'fill="{TEXT_COLOR}">{_f(t)}</text>')
        t += step
    out.append(f'<text x="{LEFT + plot_w / 2:.2f}" y="{HEIGHT - 8}" text-anchor="middle" '
               f'fill="{TEXT_COLOR}">time (µs)</text>')
    if plan:
        out.append(f'<path d="{path(_phases(plan))}" fill="none" stroke="{PLAN_COLOR}" stroke-width="2" '
                   f'stroke-dasharray="6 4"><title>plan</title></path>')
    out.append(f'<path d="{path(best)}" fill="none" stroke="{PULSE_COLOR}" stroke-width="2.5">'
               f'<title>pulse on the device</title></path>')
    lx, ly = LEFT, 44 if title else 22
    if plan:
        out.append(f'<line x1="{lx}" x2="{lx + 22}" y1="{ly}" y2="{ly}" stroke="{PLAN_COLOR}" stroke-width="2" '
                   f'stroke-dasharray="6 4"/><text x="{lx + 28}" y="{ly + 4}" fill="{TEXT_COLOR}">plan</text>')
        lx += 80
    out.append(f'<line x1="{lx}" x2="{lx + 22}" y1="{ly}" y2="{ly}" stroke="{PULSE_COLOR}" stroke-width="2.5"/>'
               f'<text x="{lx + 28}" y="{ly + 4}" fill="{TEXT_COLOR}">pulse on the device</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
