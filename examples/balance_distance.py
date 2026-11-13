"""How far does the balanced pulse move from the plan? A test on random asymmetric pulses.

Fits 2,000 random asymmetric pulses (first phase 10-200 µA and 50-300 µs, recharge 2-8 times
longer and weaker) to an Intan RHS2116 with 1 µA steps at 30,000 samples/s, and prints how far
the balanced pulse is from simple rounding (in current and time steps) and from the plan (charge).

Run from the repository: python examples/balance_distance.py
"""

import random
import statistics

from stimgrid import Pulse, fit, intan_rhs2116


def at(values, share):
    """The value below which the given share of the values fall."""
    values = sorted(values)
    return values[int(share * (len(values) - 1))]


def main(count=2000, seed=1):
    device = intan_rhs2116(step_ua=1)
    rng = random.Random(seed)
    current, time, charge, short_big, big, rounding_big, long_first = [], [], [], 0, 0, 0, []
    for _ in range(count):
        a1 = rng.uniform(10, 200)
        ratio = rng.choice([2, 3, 4, 5, 6, 8])
        w1 = rng.uniform(50, 300)
        report = fit(Pulse(a1, w1, second_amplitude_ua=a1 / ratio, second_width_us=w1 * ratio), device)
        b, r = report.balanced, report.rounded
        current.append(max(abs(b.amplitude_steps - r.amplitude_steps),
                           abs(b.second_amplitude_steps - r.second_amplitude_steps)))
        time.append(max(abs(b.width_ticks - r.width_ticks), abs(b.second_width_ticks - r.second_width_ticks)))
        change = abs(b.charge_change) * 100
        charge.append(change)
        if change > 10:
            big += 1
            short_big += r.width_ticks <= 3
            rounding_big += abs(r.charge_change) * 100 > 10
        if r.width_us >= 200:
            long_first.append(change)

    print(f"{count} random asymmetric pulses on {device.name}")
    print(f"{'':36}{'median':>8}{'90%':>8}{'worst':>8}")
    print(f"{'current steps moved (either phase)':36}{statistics.median(current):>8g}"
          f"{at(current, 0.9):>8g}{max(current):>8g}")
    print(f"{'time steps moved (either phase)':36}{statistics.median(time):>8g}{at(time, 0.9):>8g}{max(time):>8g}")
    print(f"{'charge change from the plan, %':36}{statistics.median(charge):>8.1f}"
          f"{at(charge, 0.9):>8.1f}{max(charge):>8.1f}")
    print(f"moved more than 10%: {big}; {short_big} had a first phase of 3 time steps or fewer, "
          f"and in {rounding_big} rounding alone moved the charge by more than 10%")
    print(f"first phase of 200 µs or more: median {statistics.median(long_first):.1f}%, "
          f"worst {max(long_first):.1f}%")


if __name__ == "__main__":
    main()
