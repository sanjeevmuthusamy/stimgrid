import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
EXAMPLES = os.path.join(ROOT, "examples")
sys.path.insert(0, SRC)

from stimgrid import Electrode, Pulse, charge_density, check_text, export, fit, from_dict  # noqa: E402
from stimgrid import intan_rhs2116, mcs_stg4000  # noqa: E402
from stimgrid.loader import InputError  # noqa: E402


def run(*args):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run([sys.executable, "-m", "stimgrid", *args], capture_output=True, text=True, env=env)


class DensityMathTests(unittest.TestCase):
    def test_shannon_k(self):
        # 1 µC per phase on 1 mm² (0.01 cm²): density 100 µC/cm², k = log10(1) + log10(100) = 2
        d = charge_density(1000, 1000, Electrode(area_um2=1e6))
        self.assertAlmostEqual(d.density_uc_cm2, 100)
        self.assertAlmostEqual(d.shannon_k, 2)
        self.assertFalse(d.within_limit)
        # the largest charge at the limit gives exactly k_limit back
        top = charge_density(d.max_charge_nc, 0, Electrode(area_um2=1e6))
        self.assertAlmostEqual(top.shannon_k, 1.75)
        self.assertTrue(top.within_limit)

    def test_larger_phase_and_diameter(self):
        e = Electrode(diameter_um=20)
        self.assertAlmostEqual(e.area, math.pi * 100)
        d = charge_density(0.5, 2.0, e)
        self.assertEqual(d.charge_nc, 2.0)
        self.assertAlmostEqual(d.density_uc_cm2, 2e-3 / (math.pi * 100e-8))

    def test_bad_electrodes(self):
        for kwargs in ({}, {"area_um2": 10, "diameter_um": 3}, {"area_um2": 0}, {"diameter_um": -1},
                       {"area_um2": 10, "k_limit": float("nan")}, {"area_um2": True}):
            with self.assertRaises(ValueError):
                Electrode(**kwargs)


class FitDensityTests(unittest.TestCase):
    def test_warning_over_the_limit(self):
        d = mcs_stg4000()
        r = fit(Pulse(100, 200), d, electrode=Electrode(diameter_um=30))
        self.assertAlmostEqual(r.density.charge_nc, 20)
        self.assertEqual([i.code for i in r.issues if i.code == "SG501"], ["SG501"])
        self.assertTrue(r.ok)  # a warning, not an error
        r = fit(Pulse(100, 200), d, electrode=Electrode(diameter_um=30, k_limit=2))
        self.assertNotIn("SG501", [i.code for i in r.issues])
        self.assertIsNone(fit(Pulse(100, 200), d).density)

    def test_uses_the_best_pulse(self):
        d = intan_rhs2116(1.0)
        r = fit(Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450), d,
                electrode=Electrode(area_um2=200))
        self.assertAlmostEqual(r.density.charge_nc, max(r.best.first_charge_nc, r.best.second_charge_nc))
        data = json.loads(json.dumps(r.to_dict()))
        self.assertAlmostEqual(data["density"]["shannon_k"], r.density.shannon_k)

    def test_request_file(self):
        base = {"stimgrid": 1, "device": {"preset": "mcs-stg4000"}, "pulse": {"amplitude_ua": 100, "width_us": 200}}
        _, _, _, options = from_dict({**base, "electrode": {"diameter_um": 30, "k_limit": 1.5}})
        self.assertEqual(options["electrode"], Electrode(diameter_um=30, k_limit=1.5))
        for bad, where in [({"diameter_um": "30"}, "electrode.diameter_um"), ({"radius_um": 3}, "unknown"),
                           ({}, "area_um2"), ([], "object")]:
            with self.assertRaises(InputError) as ctx:
                from_dict({**base, "electrode": bad})
            self.assertIn(where, str(ctx.exception))


class CheckDensityTests(unittest.TestCase):
    def test_check_reports_the_delivered_pulse(self):
        d = intan_rhs2116(1.0, 30000)
        text = export(fit(Pulse(100, 200), d), d, "intan-rhx")
        c = check_text(text, electrode=Electrode(diameter_um=30))
        p = c.checked[0].pulses[0]
        self.assertAlmostEqual(p.report.density.charge_nc, p.report.rounded.first_charge_nc)
        self.assertIn("SG501", [i.code for i in p.report.issues])
        self.assertTrue(c.ok)
        self.assertIsNone(check_text(text).checked[0].pulses[0].report.density)


class CliDensityTests(unittest.TestCase):
    def test_flags(self):
        args = ("quick", "--device", "mcs-stg4000", "--amplitude-ua", "100", "--width-us", "200")
        p = run(*args, "--electrode-diameter-um", "30")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Shannon k 1.753", p.stdout)
        self.assertIn("SG501", p.stdout)
        self.assertEqual(run(*args, "--electrode-diameter-um", "30", "--strict").returncode, 1)
        self.assertNotIn("SG501", run(*args, "--electrode-area-um2", "1e6").stdout)
        p = run(*args, "--k-limit", "2")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--k-limit needs", p.stderr)
        p = run(*args, "--electrode-diameter-um", "30", "--electrode-area-um2", "5")
        self.assertEqual(p.returncode, 2)

    def test_fit_flags_override_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "req.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"stimgrid": 1, "device": {"preset": "mcs-stg4000"},
                           "pulse": {"amplitude_ua": 100, "width_us": 200}, "electrode": {"diameter_um": 30}}, fh)
            self.assertIn("SG501", run("fit", path).stdout)
            self.assertNotIn("SG501", run("fit", path, "--k-limit", "2").stdout)
            p = run("fit", path, "--electrode-area-um2", "1e6", "--format", "json")
            self.assertEqual(json.loads(p.stdout)["density"]["area_um2"], 1e6)

    def test_check_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stim.xml")
            d = intan_rhs2116(1.0, 30000)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(export(fit(Pulse(100, 200), d), d, "intan-rhx"))
            p = run("check", path, "--electrode-diameter-um", "30")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("electrode 706.858 µm²", p.stdout)
            self.assertEqual(run("check", path, "--electrode-diameter-um", "30", "--strict").returncode, 1)


if __name__ == "__main__":
    unittest.main()
