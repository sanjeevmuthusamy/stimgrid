import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
EXAMPLES = os.path.join(ROOT, "examples")
sys.path.insert(0, SRC)

from stimgrid import InputError, from_dict  # noqa: E402

BASE = {"stimgrid": 1, "device": {"preset": "intan-rhs2116", "step_ua": 0.1},
        "pulse": {"amplitude_ua": 7.3, "width_us": 117, "second_amplitude_ua": 1.9, "second_width_us": 450}}


def run(*args, stdin=None):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run([sys.executable, "-m", "stimgrid", *args], input=stdin, capture_output=True,
                          text=True, env=env)


class LoaderTests(unittest.TestCase):
    def error(self, data, text):
        with self.assertRaises(InputError) as ctx:
            from_dict(data)
        self.assertIn(text, str(ctx.exception))

    def test_reads_presets_and_overrides(self):
        _, device, _, _ = from_dict({**BASE, "device": {"preset": "intan-rhs2116", "step_ua": 0.1,
                                                        "max_amplitude_ua": 10}})
        self.assertEqual(device.max_amplitude_ua, 10)
        _, device, _, options = from_dict({**BASE, "device": {"name": "mine", "time_step_us": 5},
                                           "max_imbalance": 0.01, "keep_first": True})
        self.assertTrue(device.continuous_amplitude)
        self.assertEqual(options, {"max_imbalance": 0.01, "keep_first": True})

    def test_error_paths(self):
        d = copy.deepcopy(BASE)
        d["pulse"]["width_us"] = -3
        self.error(d, "pulse: width_us must be a positive number")
        d = copy.deepcopy(BASE)
        d["pulse"]["colour"] = 1
        self.error(d, "pulse: unknown field(s) colour")
        self.error({**BASE, "device": {"preset": "nope"}}, "device.preset: unknown preset 'nope'")
        self.error({**BASE, "device": {"preset": "intan-rhs2116", "step_ua": 0.3}}, "device: the RHS2116 step size")
        self.error({**BASE, "device": {"preset": "intan-rhs2116", "step_ua": "big"}}, "device.step_ua: must be a number")
        self.error({**BASE, "device": {"time_step_us": 5}}, "device: give a 'preset'")
        self.error({**BASE, "stimgrid": 2}, "stimgrid: must be 1")
        self.error({**BASE, "max_imbalance": 2}, "max_imbalance: must be a number")
        d = copy.deepcopy(BASE)
        del d["pulse"]
        self.error(d, "request: missing 'pulse'")


class CliTests(unittest.TestCase):
    def test_examples(self):
        for name in ("intan_asymmetric.json", "cl_asymmetric.json", "custom_device.json"):
            out = run("fit", os.path.join(EXAMPLES, name))
            self.assertEqual(out.returncode, 0, name + out.stderr)
            self.assertIn("result: OK", out.stdout)

    def test_json_and_pulse_formats(self):
        path = os.path.join(EXAMPLES, "intan_asymmetric.json")
        data = json.loads(run("fit", path, "--format", "json").stdout)
        self.assertEqual(data["balanced"]["imbalance"], 0)
        self.assertTrue(data["ok"])
        self.assertEqual(data["best_pulse"], json.loads(run("fit", path, "--format", "pulse").stdout))

    def test_strict_and_errors(self):
        path = os.path.join(EXAMPLES, "intan_asymmetric.json")
        self.assertEqual(run("fit", path, "--strict").returncode, 1)  # it has warnings
        over = run("quick", "--device", "cortical-labs-cl", "--amplitude-ua", "3", "--width-us", "2000")
        self.assertEqual(over.returncode, 1)
        self.assertIn("SG104", over.stdout)

    def test_input_errors_are_2(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{oops")
        out = run("fit", fh.name)
        os.unlink(fh.name)
        self.assertEqual(out.returncode, 2)
        self.assertIn("not valid JSON", out.stderr)
        self.assertEqual(run("fit", "/no/such/file.json").returncode, 2)
        self.assertEqual(run("quick", "--amplitude-ua", "1", "--width-us", "100").returncode, 2)  # no device
        self.assertEqual(run("quick", "--device", "cortical-labs-cl", "--step-ua", "1", "--amplitude-ua", "1",
                             "--width-us", "100").returncode, 2)
        self.assertEqual(run("fit").returncode, 2)

    def test_stdin_and_quick(self):
        out = run("fit", "-", "--format", "pulse", stdin=json.dumps(BASE))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("amplitude_ua", json.loads(out.stdout))
        out = run("quick", "--time-step-us", "10", "--amplitude-step-ua", "0.5", "--amplitude-ua", "20",
                  "--width-us", "105", "--second-amplitude-ua", "4", "--second-width-us", "520")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("balanced", out.stdout)
        self.assertIn("source:", run("devices").stdout)


if __name__ == "__main__":
    unittest.main()
