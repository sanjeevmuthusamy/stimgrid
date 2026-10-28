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

from stimgrid import Pulse, Rates, fit, intan_rhs2116, load, mcs_stg4000, mcs_stg5  # noqa: E402
from stimgrid.check import CheckError, check_text, detect  # noqa: E402
from stimgrid.formats import export  # noqa: E402

HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<IntanRHX Type="ControllerStimRecord"{root}>\n<StimParameters>\n'
TAIL = "</StimParameters>\n</IntanRHX>\n"
ASYM = ('FirstPhaseDurationMicroseconds="117" SecondPhaseDurationMicroseconds="450" '
        'FirstPhaseAmplitudeMicroAmps="7.3" SecondPhaseAmplitudeMicroAmps="1.9"')


def rhx(*channels, root=' SampleRateHertz="30000" StimStepSizeMicroAmps="0.1"'):
    return HEAD.format(root=root) + "".join(f"<StimChannel {c}/>\n" for c in channels) + TAIL


def mcs(rows, program="II", extra=""):
    body = "\n".join(f"{v}\t{t}" for v, t in rows)
    return (f"Multi Channel Systems MC_Stimulus {program}\nASCII import Version 1.10\nchannels: 1\n"
            f"output mode: current\nformat: 4\n{extra}channel: 1\nvalue\ttime\n{body}\n")


def run(*args):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run([sys.executable, "-m", "stimgrid", *args], capture_output=True, text=True, env=env)


class IntanCheckTests(unittest.TestCase):
    def test_exported_files_check_as_balanced(self):
        for rate in (20000, 25000, 30000):
            d = intan_rhs2116(0.1, rate)
            r = fit(Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450, gap_us=50), d,
                    Rates(frequency_hz=40))
            c = check_text(export(r, d, "intan-rhx", channels=["A-000", "B-001"], pulses=10))
            self.assertTrue(c.ok, rate)
            self.assertEqual([ch.channel for ch in c.checked], ["A-000", "B-001"])
            p = c.checked[0].pulses[0]
            self.assertEqual((p.count, p.report.rounded.as_pulse()), (10, r.best.as_pulse()))

    def test_unbalanced_file(self):
        c = check_text(rhx(f'NativeChannelName="A-000" StimEnabled="True" Shape="Biphasic" {ASYM} '
                           'PulseOrTrain="PulseTrain" NumberOfStimPulses="100" PulseTrainPeriodMicroseconds="25000"'))
        self.assertFalse(c.ok)
        ch = c.unbalanced[0]
        self.assertAlmostEqual(ch.net_charge_nc, 100 * ch.pulses[0].net_charge_nc)
        self.assertAlmostEqual(ch.pulses[0].report.rounded.width_us, 400 / 3)
        self.assertEqual(ch.pulses[0].report.balanced.imbalance, 0)

    def test_channel_states(self):
        c = check_text(rhx('NativeChannelName="A-000" StimEnabled="False"',
                           f'NativeChannelName="A-001" stimenabled="true" Shape="Triphasic" {ASYM}',
                           'NativeChannelName="A-002" StimEnabled="True" Shape="Biphasic"',
                           'NativeChannelName="A-003"',
                           f'NativeChannelName="A-004" StimEnabled="true" Shape="biphasic" {ASYM} '
                           'InterphaseDelayMicroseconds="N/A"'))
        self.assertEqual([ch.status for ch in c.channels], ["off", "skipped", "skipped", "off", "checked"])
        self.assertIn("keeps its current values", c.channels[2].note)

    def test_rhx_rejections_in_the_file(self):
        on = 'NativeChannelName="A-000" StimEnabled="True" Shape="Biphasic" '
        c = check_text(rhx(on + 'FirstPhaseDurationMicroseconds="200" SecondPhaseDurationMicroseconds="200" '
                                'FirstPhaseAmplitudeMicroAmps="5" SecondPhaseAmplitudeMicroAmps="5" '
                                'PulseOrTrain="PulseTrain" NumberOfStimPulses="3" PulseTrainPeriodMicroseconds="300"'))
        self.assertEqual([i.code for i in c.channels[0].issues], ["SG403"])
        self.assertTrue(c.channels[0].balanced)
        self.assertFalse(c.ok)
        c = check_text(rhx(on + ASYM.replace('"450"', '"6000"')))
        self.assertIn("SG401", [i.code for i in c.channels[0].issues])
        c = check_text(rhx(on + ASYM.replace('"1.9"', '"0"')))
        self.assertIn("SG402", [i.code for i in c.channels[0].issues])

    def test_rates_and_steps(self):
        c = check_text(rhx(f'NativeChannelName="A-000" StimEnabled="True" Shape="Biphasic" {ASYM}', root=""),
                       sample_rate_hz=20000, step_ua=1)
        self.assertEqual((c.device.time_step_us, c.device.amplitude_step_ua), (50.0, 1.0))
        for root, opts, text in [
            ("", {}, "--sample-rate-hz"),
            (' SampleRateHertz="30000"', {}, "--step-ua"),
            (' SampleRateHertz="30000" StimStepSizeMicroAmps="Max"', {}, "--step-ua"),
            (' SampleRateHertz="15000" StimStepSizeMicroAmps="1"', {}, "not a rate"),
            (' SampleRateHertz="30000" StimStepSizeMicroAmps="0.3"', {}, "step size"),
            (' SampleRateHertz="30000" StimStepSizeMicroAmps="1"', {"step_ua": 0.1}, "--step-ua"),
            (' SampleRateHertz="30000" StimStepSizeMicroAmps="1"', {"device": "mcs-stg5"}, "MC_Stimulus"),
        ]:
            with self.assertRaises(CheckError) as ctx:
                check_text(rhx('NativeChannelName="A-000"', root=root), **opts)
            self.assertIn(text, str(ctx.exception))

    def test_bad_files(self):
        for text, msg in [("<foo/>", "IntanRHX"), ("hello", "not a known"), ("<IntanRHX><oops", "XML"),
                          (rhx(), "StimChannel"),
                          ('<IntanRHX Type="ControllerRecordUSB3"><StimChannel/></IntanRHX>', "Stim/Record")]:
            with self.assertRaises(CheckError) as ctx:
                check_text(text)
            self.assertIn(msg, str(ctx.exception))


class McsCheckTests(unittest.TestCase):
    def test_exported_file_checks_as_balanced(self):
        d = mcs_stg4000()
        r = fit(Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450, gap_us=40), d, Rates(frequency_hz=130))
        c = check_text(export(r, d, "mcs-ascii", channels=[1, 3], pulses=5))
        self.assertTrue(c.ok)
        self.assertEqual([ch.channel for ch in c.checked], ["1", "3"])
        p = c.checked[0].pulses
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0].count, p[0].report.rounded.as_pulse()), (5, r.best.as_pulse()))

    def test_unbalanced_and_mixed_pulses(self):
        rows = [(-10, 100), (5, 220), (0, 1000)] * 3 + [(-4, 100), (0, 60), (4, 100), (0, 500), (3, 40)]
        c = check_text(mcs(rows))
        ch = c.channels[0]
        self.assertEqual([(p.count, p.balanced) for p in ch.pulses], [(3, False), (1, True), (1, False)])
        self.assertEqual(ch.pulses[1].report.target["gap_us"], 60)
        self.assertFalse(ch.pulses[2].report.target["biphasic"])
        self.assertAlmostEqual(ch.net_charge_nc, 3 * (1.0 - 1.1) + 0 + 0.12)
        self.assertFalse(c.ok)

    def test_same_sign_rows_join(self):
        c = check_text(mcs([(-10, 60), (-10, 40), (10, 100)]))
        p = c.channels[0].pulses[0]
        self.assertEqual((p.report.target["width_us"], p.balanced), (100, True))

    def test_devices_and_grid(self):
        self.assertEqual(check_text(mcs([(-1, 100), (1, 100)], "III")).device.amplitude_step_ua,
                         mcs_stg5().amplitude_step_ua)
        c = check_text(mcs([(-1, 100), (1, 100)]), device="mcs-stg4000", range_ua=16000)
        self.assertEqual(c.device.amplitude_step_ua, 2.0)
        self.assertEqual(c.device.time_step_us, 20)
        c = check_text(mcs([(-1, 105), (1, 105)]))
        self.assertEqual(c.channels[0].issues[0].code, "SG404")
        self.assertFalse(c.ok)
        self.assertEqual(check_text(mcs([(0, 100)])).channels[0].status, "off")

    def test_bad_files(self):
        for text, opts, msg in [
            (mcs([(1, 100)]).replace("current", "voltage"), {}, "voltage"),
            (mcs([(1, 100)]).replace("format: 4", "format: 2"), {}, "format 2"),
            (mcs([(1, 100)]).replace("channel: 1\n", ""), {}, "before any"),
            (mcs([(1, 100)]) + "1 2 3\n", {}, "value time"),
            (mcs([(1, 100)]) + "channel: 1\n", {}, "twice"),
            (mcs([(1, 100)]), {"device": "tdt-iz2"}, "mcs-stg4000"),
            (mcs([(1, 100)]), {"range_ua": 5}, "range_ua"),
            (mcs([(1, 100)]), {"step_ua": 1}, "Intan RHX"),
        ]:
            with self.assertRaises(CheckError) as ctx:
                check_text(text, **opts)
            self.assertIn(msg, str(ctx.exception))

    def test_comments_and_detect(self):
        self.assertEqual(detect("﻿  " + mcs([(1, 100)])), "mcs-ascii")
        c = check_text(mcs([(-2, 100), (2, 100)], extra="# a comment\n"))
        self.assertTrue(c.ok)


class CliCheckTests(unittest.TestCase):
    def test_text_json_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = os.path.join(tmp, "good.xml"), os.path.join(tmp, "bad.xml")
            pulse, device, rates, options = load(os.path.join(EXAMPLES, "intan_asymmetric.json"))
            with open(good, "w", encoding="utf-8") as fh:
                fh.write(export(fit(pulse, device, rates, **options), device, "intan-rhx", pulses=3))
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write(rhx(f'NativeChannelName="A-000" StimEnabled="True" Shape="Biphasic" {ASYM}'))
            p = run("check", good)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("result: OK", p.stdout)
            p = run("check", bad)
            self.assertEqual(p.returncode, 1)
            self.assertIn("NOT balanced", p.stdout)
            p = run("check", bad, "--format", "json")
            data = json.loads(p.stdout)
            self.assertEqual((data["ok"], data["channels"][0]["balanced"]), (False, False))
            p = run("check", bad, "--max-imbalance", "0.1")
            self.assertEqual(p.returncode, 0, p.stdout)
            p = run("check", bad, "--max-imbalance", "0.1", "--strict")
            self.assertEqual(p.returncode, 1)
            p = run("check", os.path.join(EXAMPLES, "intan_asymmetric.json"))
            self.assertEqual(p.returncode, 2)
            self.assertIn("not a known", p.stderr)
            p = run("check", os.path.join(tmp, "missing.xml"))
            self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
