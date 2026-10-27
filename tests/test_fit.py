import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from stimgrid import Device, Pulse, Rates, cortical_labs_cl, fit, intan_rhs2116, preset  # noqa: E402


def codes(report):
    return [i.code for i in report.issues]


class DeviceTests(unittest.TestCase):
    def test_intan_presets(self):
        d = intan_rhs2116(0.1, 30000)
        self.assertAlmostEqual(d.time_step_us, 1e6 / 30000)
        self.assertAlmostEqual(d.max_amplitude_ua, 25.5)
        self.assertEqual(d.max_amplitude_steps, 255)
        with self.assertRaises(ValueError):
            intan_rhs2116(0.3)

    def test_cl_preset(self):
        d = cortical_labs_cl()
        self.assertEqual((d.time_step_us, d.max_amplitude_ua, d.max_charge_per_phase_nc), (20.0, 3.0, 3.0))
        self.assertTrue(d.continuous_amplitude)

    def test_round_half_up(self):
        d = Device("t", time_step_us=20, amplitude_step_ua=1)
        self.assertEqual(d.ticks(90), 5)  # 4.5 -> 5 (Python's round() would give 4)
        self.assertEqual(d.ticks(110), 6)  # 5.5 -> 6
        self.assertEqual(d.amp_steps(2.5), 3)

    def test_invalid_devices(self):
        for bad in (dict(time_step_us=0), dict(time_step_us=-1), dict(time_step_us=float("nan")),
                    dict(time_step_us=10, amplitude_step_ua=2, max_amplitude_ua=1),
                    dict(time_step_us=10, min_phase_us=50, max_phase_us=20)):
            with self.assertRaises(ValueError):
                Device("x", **bad)
        with self.assertRaises(ValueError):
            preset("nope")


class FitTests(unittest.TestCase):
    def test_symmetric_pulse_stays_put(self):
        r = fit(Pulse(20, 100), Device("t", 10, 0.5))
        self.assertEqual(r.rounded.imbalance, 0)
        self.assertEqual(r.balanced.as_pulse(), r.rounded.as_pulse())
        self.assertEqual(r.issues, [])

    def test_exact_balance_on_a_grid(self):
        # 7.3 µA x 117 µs / 1.9 µA x 450 µs on Intan 0.1 µA steps at 30 kS/s
        r = fit(Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450), intan_rhs2116(0.1, 30000))
        self.assertGreater(r.rounded.imbalance, 0.05)
        b = r.balanced
        self.assertEqual(b.imbalance, 0)
        self.assertEqual(b.amplitude_steps * b.width_ticks, b.second_amplitude_steps * b.second_width_ticks)
        self.assertEqual(b.net_charge_nc, 0.0)
        self.assertIn("SG201", codes(r))
        self.assertIn("SG203", codes(r))  # charge moved more than 5%
        self.assertTrue(r.ok)
        for alt in r.alternatives:
            self.assertEqual(alt.imbalance, 0)

    def test_continuous_amplitude_keeps_charge(self):
        r = fit(Pulse(2.5, 110, second_amplitude_ua=0.7, second_width_us=390), cortical_labs_cl())
        self.assertAlmostEqual(r.rounded.width_us, 120)
        self.assertEqual(r.balanced.imbalance, 0)
        self.assertAlmostEqual(r.balanced.charge_change, 0, places=9)
        self.assertEqual(r.balanced.width_us % 20, 0)
        self.assertEqual(r.balanced.second_width_us % 20, 0)

    def test_keep_first(self):
        r = fit(Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450), intan_rhs2116(0.1, 30000),
                keep_first=True)
        self.assertEqual((r.balanced.amplitude_steps, r.balanced.width_ticks),
                         (r.rounded.amplitude_steps, r.rounded.width_ticks))

    def test_tolerance(self):
        p = Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450)
        loose = fit(p, intan_rhs2116(0.1, 30000), max_imbalance=0.5)
        self.assertNotIn("SG201", codes(loose))  # rounding's 8.9% is within 50%

    def test_impossible_balance_is_an_error(self):
        # 251 steps x 200 ticks = 50200: no second phase of 1-30 ticks reaches it within 255 steps
        d = Device("t", time_step_us=1, amplitude_step_ua=1, max_amplitude_ua=255)
        r = fit(Pulse(251, 200, second_amplitude_ua=200, second_width_us=10), d, search_steps=0)
        self.assertIn("SG202", codes(r))
        self.assertFalse(r.ok)

    def test_device_limits(self):
        d = intan_rhs2116(0.1, 30000)
        self.assertIn("SG101", codes(fit(Pulse(30, 100), d)))  # above 25.5 µA
        self.assertIn("SG103", codes(fit(Pulse(0.04, 100), d)))  # below one 0.1 µA step
        self.assertIn("SG102", codes(fit(Pulse(1, 100), Device("t", 10, min_phase_us=200))))
        self.assertIn("SG104", codes(fit(Pulse(3, 2000), cortical_labs_cl())))  # 6 nC > 3 nC
        self.assertFalse(fit(Pulse(3, 2000), cortical_labs_cl()).ok)

    def test_gap_and_moved_values(self):
        r = fit(Pulse(20, 100, gap_us=4), Device("t", 10, 0.5))
        self.assertIn("SG105", codes(r))
        r = fit(Pulse(20, 100, gap_us=25), Device("t", 10, 0.5))
        self.assertIn("SG106", codes(r))

    def test_rates(self):
        r = fit(Pulse(2, 100), cortical_labs_cl(), Rates(frequency_hz=130))
        rate = r.rates[0]
        self.assertEqual(rate.period_ticks, 385)
        self.assertAlmostEqual(rate.delivered_hz, 1e6 / (385 * 20))
        r = fit(Pulse(2, 100), Device("t", 10, 0.5), Rates(intra_burst_hz=333))
        self.assertIn("SG301", codes(r))
        r = fit(Pulse(2, 400), Device("t", 10, 0.5), Rates(frequency_hz=2000))  # 800 µs pulse, 500 µs period
        self.assertIn("SG303", codes(r))
        r = fit(Pulse(2, 100), Device("t", 10, 0.5, max_frequency_hz=100), Rates(frequency_hz=150))
        self.assertIn("SG304", codes(r))

    def test_monophasic(self):
        r = fit(Pulse(2, 100, biphasic=False), Device("t", 10, 0.5))
        self.assertIsNone(r.balanced)
        self.assertEqual(r.best.as_pulse(), {"amplitude_ua": 2, "width_us": 100, "biphasic": False})

    def test_bad_options(self):
        with self.assertRaises(ValueError):
            fit(Pulse(1, 100), Device("t", 10), max_imbalance=1)
        with self.assertRaises(ValueError):
            fit(Pulse(1, 100), Device("t", 10), search_steps=50)

    def test_long_pulses_are_fast(self):
        import time
        t = time.time()
        r = fit(Pulse(10, 2000, second_amplitude_ua=1.3, second_width_us=20000), intan_rhs2116(0.1, 30000))
        self.assertLess(time.time() - t, 5)
        self.assertEqual(r.balanced.imbalance, 0)

    def test_exported_values_are_clean(self):
        p = fit(Pulse(25, 100, second_amplitude_ua=5, second_width_us=500), intan_rhs2116(1.0, 30000)).best.as_pulse()
        self.assertEqual(p, {"amplitude_ua": 25, "width_us": 100, "second_amplitude_ua": 5, "second_width_us": 500})
        self.assertTrue(all(not isinstance(v, float) or math.isfinite(v) for v in p.values()))


if __name__ == "__main__":
    unittest.main()
