import json
import os
import random
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from stimgrid import (Device, Pulse, am_systems_4100, blackrock_cerestim_r96, design, fit,  # noqa: E402
                      intan_rhs2116, mcs_stg4000, tdt_iz2)


def run(*args):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run([sys.executable, "-m", "stimgrid", *args], capture_output=True, text=True, env=env)


class DesignTests(unittest.TestCase):
    def assert_on_grid_and_balanced(self, device, option):
        d = option.pulse
        self.assertEqual(d.imbalance, 0.0)
        self.assertEqual(d.net_charge_nc, 0.0)
        # fitting the designed pulse back changes nothing: it is already on the grid and balanced
        back = fit(Pulse(**d.as_pulse()), device)
        self.assertEqual(back.rounded.imbalance, 0.0)
        self.assertEqual(back.rounded.width_ticks, d.width_ticks)
        self.assertEqual(back.rounded.second_width_ticks, d.second_width_ticks)
        if not device.continuous_amplitude:
            self.assertEqual(back.rounded.amplitude_steps, d.amplitude_steps)
            self.assertEqual(back.rounded.second_amplitude_steps, d.second_amplitude_steps)
            self.assertEqual(d.amplitude_steps * d.width_ticks, d.second_amplitude_steps * d.second_width_ticks)

    def test_intan_with_width(self):
        device = intan_rhs2116(step_ua=1)
        report = design(device, 4.7, ratio=4, width_us=100)
        self.assertTrue(report.ok)
        best = report.best
        self.assertEqual(best.pulse.as_pulse(), {"amplitude_ua": 48, "width_us": 100, "second_amplitude_ua": 12,
                                                 "second_width_us": 400})
        self.assertAlmostEqual(best.pulse.charge_change, 0.8 / 37.6 * 1, places=9)  # 4.8 nC vs 4.7 nC
        self.assertEqual(best.ratio_change, 0)
        for o in report.options:
            self.assertEqual(o.pulse.width_ticks, 3)
            self.assert_on_grid_and_balanced(device, o)

    def test_intan_without_width_is_ranked(self):
        device = intan_rhs2116(step_ua=1)
        report = design(device, 4.7, ratio=4, count=10)
        self.assertEqual(len(report.options), 10)
        costs = [abs(o.pulse.charge_change) + abs(o.ratio_change) for o in report.options]
        self.assertEqual(costs, sorted(costs))
        self.assertAlmostEqual(report.best.pulse.first_charge_nc, 140 / 30, places=9)
        for o in report.options:
            self.assert_on_grid_and_balanced(device, o)

    def test_continuous_amplitude_keeps_the_charge(self):
        device = tdt_iz2()
        report = design(device, 4.7, ratio=4, width_us=100, count=3)
        best = report.best.pulse
        self.assertAlmostEqual(best.first_charge_nc, 4.7, places=9)
        self.assertAlmostEqual(best.width_us, 81.92, places=9)
        self.assertAlmostEqual(best.second_width_us, 4 * 81.92, places=9)
        self.assertEqual(best.imbalance, 0.0)
        self.assertEqual([i.code for i in report.issues], ["SG106"])

    def test_symmetric_default(self):
        device = mcs_stg4000()
        report = design(device, 1.0, width_us=100)
        best = report.best.pulse
        self.assertEqual((best.amplitude_ua, best.second_amplitude_ua), (10.0, 10.0))
        self.assertEqual(best.width_us, best.second_width_us)
        self.assertEqual(report.best.ratio, 1.0)

    def test_ratio_below_one(self):
        device = intan_rhs2116(step_ua=1)
        report = design(device, 4.7, ratio=0.25, width_us=400)
        best = report.best.pulse
        self.assertEqual((best.amplitude_ua, best.second_amplitude_ua), (12, 48))
        self.assert_on_grid_and_balanced(device, report.best)

    def test_limits(self):
        device = intan_rhs2116(step_ua=1)  # 255 µA maximum
        for o in design(device, 20, ratio=0.5, count=20).options:
            self.assertLessEqual(max(o.pulse.amplitude_ua, o.pulse.second_amplitude_ua), 255)
        r96 = blackrock_cerestim_r96()
        report = design(r96, 40)
        self.assertFalse(report.ok)
        self.assertEqual([i.code for i in report.issues], ["SG104"])
        report = design(r96, 20, width_us=100)
        self.assertGreaterEqual(report.best.pulse.gap_us, 53)  # raised to the minimum gap
        report = design(r96, 20, width_us=100, gap_us=10)
        self.assertIn("SG107", [i.code for i in report.issues])

    def test_nothing_fits(self):
        device = Device(name="tiny", time_step_us=10, amplitude_step_ua=1, max_amplitude_ua=2, max_phase_us=20)
        report = design(device, 50, ratio=2)
        self.assertFalse(report.ok)
        self.assertEqual([i.code for i in report.issues], ["SG701"])
        self.assertIsNone(report.best)
        self.assertIsNone(report.to_dict()["best_pulse"])

    def test_far_charge_warns(self):
        device = Device(name="coarse", time_step_us=100, amplitude_step_ua=10)
        report = design(device, 1.5, width_us=100)  # 1 or 2 nC are the choices
        self.assertIn("SG702", [i.code for i in report.issues])

    def test_random_aims_are_always_exact(self):
        rng = random.Random(3)
        devices = [intan_rhs2116(step_ua=1), intan_rhs2116(step_ua=0.1), mcs_stg4000(), am_systems_4100()]
        for _ in range(60):
            device = rng.choice(devices)
            width = rng.choice([None, rng.uniform(50, 400)])
            report = design(device, rng.uniform(0.5, 20), ratio=rng.choice([1, 2, 3, 4, 5, 8, 0.5]), width_us=width,
                            count=3)
            if not report.options:  # only when the charge cannot fit at that width within the maximum current
                self.assertIsNotNone(width)
                self.assertIn("above the device's", report.issues[0].message)
                continue
            for o in report.options:
                self.assert_on_grid_and_balanced(device, o)

    def test_bad_input(self):
        device = intan_rhs2116()
        for kwargs in ({"charge_nc": 0}, {"charge_nc": 1, "ratio": -1}, {"charge_nc": 1, "count": 0},
                       {"charge_nc": 1, "width_us": 0}, {"charge_nc": 1, "gap_us": -1},
                       {"charge_nc": float("nan")}):
            with self.assertRaises(ValueError):
                design(device, **kwargs)


class DesignCliTests(unittest.TestCase):
    def test_text(self):
        r = run("design", "--device", "intan-rhs2116", "--step-ua", "1", "--charge-nc", "4.7", "--ratio", "4",
                "--width-us", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("48 µA x 100 µs | gap 0 µs | 12 µA x 400 µs", r.stdout)
        self.assertIn("result: OK", r.stdout)

    def test_pulse_feeds_quick(self):
        r = run("design", "--device", "mcs-stg4000", "--charge-nc", "0.855", "--ratio", "4", "--width-us", "117",
                "--format", "pulse")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(r.stdout)
        q = run("quick", "--device", "mcs-stg4000", "--amplitude-ua", str(p["amplitude_ua"]),
                "--width-us", str(p["width_us"]), "--second-amplitude-ua", str(p["second_amplitude_ua"]),
                "--second-width-us", str(p["second_width_us"]), "--format", "json")
        data = json.loads(q.stdout)
        self.assertEqual(data["rounded"]["imbalance"], 0.0)
        self.assertEqual(data["issues"], [])

    def test_json_and_errors(self):
        r = run("design", "--device", "blackrock-cerestim-r96", "--charge-nc", "40", "--format", "json")
        self.assertEqual(r.returncode, 1)
        data = json.loads(r.stdout)
        self.assertFalse(data["ok"])
        self.assertEqual(data["options"], [])
        r = run("design", "--device", "intan-rhs2116", "--charge-nc", "-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("charge_nc must be a positive number", r.stderr)
        r = run("design", "--device", "tdt-iz2", "--charge-nc", "4.7", "--width-us", "100", "--strict")
        self.assertEqual(r.returncode, 1)  # SG106 warning


if __name__ == "__main__":
    unittest.main()
