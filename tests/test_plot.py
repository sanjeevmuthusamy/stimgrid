import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
EXAMPLES = os.path.join(ROOT, "examples")
sys.path.insert(0, SRC)

from stimgrid import Pulse, fit, intan_rhs2116, pulse_svg  # noqa: E402

NS = "{http://www.w3.org/2000/svg}"


def run(*args):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run([sys.executable, "-m", "stimgrid", *args], capture_output=True, text=True, env=env)


def paths(svg):
    return [p for p in ET.fromstring(svg).iter(NS + "path")]


class PlotTests(unittest.TestCase):
    def test_pulse_and_plan(self):
        report = fit(Pulse(47, 100, second_amplitude_ua=11, second_width_us=430), intan_rhs2116(step_ua=1))
        svg = pulse_svg(report.best, plan=report.target, title="a & b")
        drawn = paths(svg)
        self.assertEqual(len(drawn), 2)
        self.assertEqual(drawn[0].get("stroke-dasharray"), "6 4")  # plan underneath, dashed
        self.assertIn("a &amp; b", svg)
        # the pulse path: baseline, down to -48 µA for 100 µs, up to +12 µA for 400 µs, baseline
        points = drawn[1].get("d")[2:].split(" L ")
        self.assertEqual(len(points), 10)
        ys = [float(p.split()[1]) for p in points]
        self.assertGreater(ys[2], ys[0])  # first phase below the baseline (negative first)
        self.assertLess(ys[6], ys[0])

    def test_dict_monophasic_and_polarity(self):
        svg = pulse_svg({"amplitude_ua": 30, "width_us": 200, "biphasic": False}, negative_first=False)
        drawn = paths(svg)
        self.assertEqual(len(drawn), 1)
        points = drawn[0].get("d")[2:].split(" L ")
        self.assertEqual(len(points), 6)
        self.assertLess(float(points[2].split()[1]), float(points[0].split()[1]))  # positive first

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "p.svg")
            r = run("quick", "--device", "intan-rhs2116", "--amplitude-ua", "47", "--width-us", "100",
                    "--second-amplitude-ua", "11", "--second-width-us", "430", "--plot", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(len(paths(fh.read())), 2)
            out2 = os.path.join(tmp, "d.svg")
            r = run("design", "--device", "intan-rhs2116", "--charge-nc", "4.7", "--ratio", "4", "--width-us", "100",
                    "--plot", out2)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out2, encoding="utf-8") as fh:
                self.assertEqual(len(paths(fh.read())), 1)
            r = run("fit", os.path.join(EXAMPLES, "mcs_stg4000_protocol.json"), "--plot", out)
            self.assertEqual(r.returncode, 2)
            self.assertIn("--plot draws one pulse", r.stderr)


if __name__ == "__main__":
    unittest.main()
