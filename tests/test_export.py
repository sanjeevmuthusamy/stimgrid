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

from stimgrid import (Device, Pulse, Rates, cortical_labs_cl, fit, intan_rhs2116, load,  # noqa: E402
                      mcs_stg4000, mcs_stg5)
from stimgrid.formats import ExportError, export, to_intan_rhx, to_mcs_ascii  # noqa: E402

ASYM = Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450)


def run(*args):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run([sys.executable, "-m", "stimgrid", *args], capture_output=True, text=True, env=env)


def rhx_channels(text):
    root = ET.fromstring(text.split("\n", 1)[1])  # skip the XML declaration
    return root, root.find("StimParameters").findall("StimChannel")


def mcs_rows(text, channel=1):
    lines = text.splitlines()
    start = lines.index(f"channel: {channel}") + 2
    rows = []
    for line in lines[start:]:
        if line.startswith("channel:"):
            break
        value, time = line.split("\t")
        rows.append((float(value), int(time)))
    return lines, rows


class IntanRhxTests(unittest.TestCase):
    def test_round_trip_matches_the_grid(self):
        # RHX sends round(duration / sample period) samples and round(µA / step) steps
        for rate in (20000, 25000, 30000):
            for step in (0.1, 1.0):
                d = intan_rhs2116(step, rate)
                r = fit(Pulse(7.3 * step * 10, 117, second_amplitude_ua=1.9 * step * 10, second_width_us=450), d)
                b = r.balanced
                root, chans = rhx_channels(export(r, d, "intan-rhx"))
                self.assertEqual(root.get("SampleRateHertz"), str(rate))
                self.assertEqual(root.get("StimStepSizeMicroAmps"), f"{step:g}")
                c = chans[0].attrib
                ts = 1e6 / rate
                self.assertEqual(round(float(c["FirstPhaseDurationMicroseconds"]) / ts), b.width_ticks)
                self.assertEqual(round(float(c["SecondPhaseDurationMicroseconds"]) / ts), b.second_width_ticks)
                self.assertEqual(round(float(c["FirstPhaseAmplitudeMicroAmps"]) / step), b.amplitude_steps)
                self.assertEqual(round(float(c["SecondPhaseAmplitudeMicroAmps"]) / step),
                                 b.second_amplitude_steps)

    def test_attributes(self):
        d = intan_rhs2116(0.1, 30000)
        r = fit(Pulse(5, 100, gap_us=66.7), d, Rates(frequency_hz=40))
        root, chans = rhx_channels(export(r, d, "intan-rhx", channels=["a-000", "B-015"], pulses=10,
                                          negative_first=False))
        self.assertEqual((root.tag, root.get("Type")), ("IntanRHX", "ControllerStimRecord"))
        self.assertEqual([c.get("NativeChannelName") for c in chans], ["A-000", "B-015"])
        c = chans[0].attrib
        self.assertEqual((c["Shape"], c["Polarity"], c["StimEnabled"]),
                         ("BiphasicWithInterphaseDelay", "PositiveFirst", "True"))
        self.assertEqual(c["InterphaseDelayMicroseconds"], "66.666667")
        self.assertEqual((c["PulseOrTrain"], c["NumberOfStimPulses"], c["PulseTrainPeriodMicroseconds"]),
                         ("PulseTrain", "10", "25000"))
        self.assertNotIn("Source", c)  # trigger settings stay as set in RHX
        single = rhx_channels(export(r, d, "intan-rhx"))[1][0].attrib
        self.assertEqual((single["Shape"], single["Polarity"], single["PulseOrTrain"]),
                         ("BiphasicWithInterphaseDelay", "NegativeFirst", "SinglePulse"))
        self.assertNotIn("NumberOfStimPulses", single)

    def test_rejections(self):
        d = intan_rhs2116(0.1, 30000)
        r = fit(ASYM, d)
        cases = [
            (lambda: export(r, intan_rhs2116(0.1, 24000), "intan-rhx"), "samples/s"),
            (lambda: export(fit(ASYM, mcs_stg4000()), mcs_stg4000(), "intan-rhx"), "samples/s"),
            (lambda: export(fit(Pulse(2, 100), cortical_labs_cl()), cortical_labs_cl(), "intan-rhx"), "Intan"),
            (lambda: export(fit(Pulse(2, 100, biphasic=False), d), d, "intan-rhx"), "monophasic"),
            (lambda: export(fit(Pulse(1, 6000), intan_rhs2116(1.0)), intan_rhs2116(1.0), "intan-rhx"), "5000"),
            (lambda: export(r, d, "intan-rhx", channels=["A0"]), "A-000"),
            (lambda: export(r, d, "intan-rhx", channels=["A-000", "a-000"]), "twice"),
            (lambda: export(r, d, "intan-rhx", pulses=2), "rate"),
            (lambda: export(r, d, "intan-rhx", pulses=257), "256"),
            (lambda: export(r, d, "intan-rhx", use="option-9"), "option"),
            (lambda: export(r, d, "nope"), "unknown"),
            (lambda: export(fit(Pulse(30, 100), d), d, "intan-rhx"), "errors"),
        ]
        for make, text in cases:
            with self.assertRaises(ExportError) as ctx:
                make()
            self.assertIn(text, str(ctx.exception))

    def test_use_picks_the_pulse(self):
        d = intan_rhs2116(0.1, 30000)
        r = fit(ASYM, d)
        c = rhx_channels(export(r, d, "intan-rhx", use="rounded"))[1][0].attrib
        self.assertEqual(c["FirstPhaseDurationMicroseconds"], "133.333333")
        c = rhx_channels(export(r, d, "intan-rhx", use="option-1"))[1][0].attrib
        self.assertEqual(c["SecondPhaseDurationMicroseconds"], "533.333333")


class McsAsciiTests(unittest.TestCase):
    def test_layout_and_balance(self):
        d = mcs_stg4000()
        r = fit(ASYM, d, Rates(frequency_hz=130))
        lines, rows = mcs_rows(export(r, d, "mcs-ascii", pulses=3))
        self.assertEqual(lines[:5], ["Multi Channel Systems MC_Stimulus II", "ASCII import Version 1.10",
                                     "channels: 1", "output mode: current", "format: 4"])
        self.assertEqual(lines[5:7], ["channel: 1", "value\ttime"])
        self.assertEqual(len(rows), 9)
        self.assertLess(rows[0][0], 0)
        self.assertEqual(sum(v * t for v, t in rows), 0)  # every pulse is exactly balanced
        self.assertEqual(sum(t for _, t in rows[:3]), r.rates[0].period_ticks * 20)
        self.assertTrue(all(t % 20 == 0 for _, t in rows))

    def test_gap_channels_and_polarity(self):
        d = mcs_stg4000()
        r = fit(Pulse(10, 100, gap_us=40), d)
        text = export(r, d, "mcs-ascii", channels=["2", "1"], negative_first=False)
        lines, rows = mcs_rows(text, 2)
        self.assertIn("channels: 2", lines)
        self.assertEqual(rows, [(10.0, 100), (0.0, 40), (-10.0, 100)])
        self.assertEqual(mcs_rows(text, 1)[1], rows)

    def test_stg5_header_and_grid(self):
        d = mcs_stg5()
        r = fit(Pulse(10, 100), d)
        self.assertTrue(export(r, d, "mcs-ascii").startswith("Multi Channel Systems MC_Stimulus III\n"))
        r = fit(Pulse(10, 105), d)  # 105 µs is on the STG5's 5 µs grid but not on the import's 20 µs
        with self.assertRaises(ExportError) as ctx:
            export(r, d, "mcs-ascii")
        self.assertIn("20 µs", str(ctx.exception))

    def test_monophasic_and_rejections(self):
        d = mcs_stg4000()
        _, rows = mcs_rows(to_mcs_ascii(fit(Pulse(5, 100, biphasic=False), d).rounded, d))
        self.assertEqual(rows, [(-5.0, 100)])
        for make, text in [
            (lambda: to_mcs_ascii(fit(Pulse(2, 100), cortical_labs_cl()).best, cortical_labs_cl()), "amplitude step"),
            (lambda: to_mcs_ascii(fit(ASYM, d).best, d, channels=[0]), "from 1"),
            (lambda: to_mcs_ascii(fit(ASYM, d).best, d, channels=["x"]), "number"),
            (lambda: to_mcs_ascii(fit(ASYM, d).best, d, program="IV"), "program"),
            (lambda: to_mcs_ascii(fit(ASYM, d).best, d, pulses=2), "rate"),
            (lambda: to_mcs_ascii(fit(ASYM, d).best, d, period_ticks=3), "longer"),
        ]:
            with self.assertRaises(ExportError) as ctx:
                make()
            self.assertIn(text, str(ctx.exception))

    def test_custom_device_on_20_us(self):
        d = Device("mine", time_step_us=20, amplitude_step_ua=0.5)
        _, rows = mcs_rows(export(fit(ASYM, d), d, "mcs-ascii"))
        self.assertEqual(sum(v * t for v, t in rows), 0)


class CliExportTests(unittest.TestCase):
    def test_writes_a_file_and_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "stim.xml")
            p = run("fit", os.path.join(EXAMPLES, "intan_asymmetric.json"), "--export", "intan-rhx", "--out", out,
                    "--channels", "A-000,A-001", "--pulses", "4")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("balanced", p.stdout)
            self.assertIn("wrote intan-rhx", p.stderr)
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(len(rhx_channels(fh.read())[1]), 2)

    def test_stdout_and_errors(self):
        p = run("fit", os.path.join(EXAMPLES, "mcs_stg4000_asymmetric.json"), "--export", "mcs-ascii", "--out", "-")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(p.stdout.startswith("Multi Channel Systems MC_Stimulus II"))
        p = run("fit", os.path.join(EXAMPLES, "cl_asymmetric.json"), "--export", "mcs-ascii", "--out", "-")
        self.assertEqual(p.returncode, 2)
        self.assertIn("amplitude step", p.stderr)
        p = run("quick", "--device", "intan-rhs2116", "--amplitude-ua", "300", "--width-us", "100",
                "--export", "intan-rhx", "--out", "-")
        self.assertEqual(p.returncode, 1)
        self.assertIn("nothing exported", p.stderr)
        p = run("quick", "--device", "intan-rhs2116", "--amplitude-ua", "3", "--width-us", "100",
                "--export", "intan-rhx")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--out", p.stderr)

    def test_examples_export_where_supported(self):
        targets = {"intan_asymmetric.json": "intan-rhx", "mcs_stg4000_asymmetric.json": "mcs-ascii"}
        for name, fmt in targets.items():
            pulse, device, rates, options = load(os.path.join(EXAMPLES, name))
            self.assertTrue(export(fit(pulse, device, rates, **options), device, fmt, pulses=2))


if __name__ == "__main__":
    unittest.main()
