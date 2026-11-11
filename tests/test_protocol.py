import copy
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
EXAMPLE = os.path.join(ROOT, "examples", "mcs_stg4000_protocol.json")
RAMP_EXAMPLE = os.path.join(ROOT, "examples", "intan_ramp_protocol.json")
sys.path.insert(0, SRC)

from stimgrid import (Device, Electrode, Protocol, Pulse, Ramp, Step, fit_protocol, from_dict,  # noqa: E402
                      load_protocol, mcs_stg4000, protocol_from_dict)
from stimgrid.loader import InputError  # noqa: E402

ASYM = {"amplitude_ua": 7.3, "width_us": 117, "second_amplitude_ua": 1.9, "second_width_us": 450}
BASE = {"stimgrid": 1, "device": {"preset": "mcs-stg4000"},
        "protocol": [{"pulse": {"amplitude_ua": 5, "width_us": 100}, "pulses": 10, "frequency_hz": 100}]}


def run(*args, stdin=None):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run([sys.executable, "-m", "stimgrid", *args], capture_output=True, text=True, env=env,
                          input=stdin)


def fitted(data, **options):
    protocol, device, opts = protocol_from_dict(data)
    return fit_protocol(protocol, device, **{**opts, **options})


class ProtocolTotalsTests(unittest.TestCase):
    def test_example(self):
        protocol, device, options = load_protocol(EXAMPLE)
        r = fit_protocol(protocol, device, **options)
        self.assertTrue(r.ok)
        # 130 Hz on a 20 µs grid is 385 steps (7700 µs): 1300 pulses take 10.01 s, not 10 s
        self.assertAlmostEqual(r.steps[0].delivered_us, 10.01e6)
        self.assertAlmostEqual(r.delivered_us, 3 * (10.01 + 10 + 2) * 1e6)
        self.assertAlmostEqual(r.planned_us, 66e6)
        ch = {c.channel: c for c in r.channels()}
        self.assertEqual((ch["1"].pulses, ch["2"].pulses), (3 * 1340, 3 * 1300))
        self.assertEqual((ch["1"].net_charge_nc, ch["2"].net_charge_nc), (0, 0))
        self.assertAlmostEqual(ch["2"].phase_charge_nc, 3 * 1300 * 7.2 * 120e-3)

    def test_rounded_shows_the_leftover_charge(self):
        protocol, device, _ = load_protocol(EXAMPLE)
        r = fit_protocol(protocol, device, use="rounded")
        self.assertFalse(r.ok)
        per_pulse = r.steps[0].used.net_charge_nc
        self.assertNotEqual(per_pulse, 0)
        for c in r.channels():
            self.assertAlmostEqual(c.net_charge_nc, 3 * 1300 * per_pulse)
            self.assertFalse(c.balanced)

    def test_steps_without_channels(self):
        r = fitted(BASE)
        self.assertEqual(r.channels(), [])
        self.assertEqual(r.pulses, 10)
        data = copy.deepcopy(BASE)
        data["protocol"].append({"pulse": {"amplitude_ua": 5, "width_us": 100}, "channels": ["A-000"]})
        self.assertEqual([c.channel for c in fitted(data).channels()], ["", "A-000"])
        json.dumps(fitted(data).to_dict(), allow_nan=False)


class StepTimingTests(unittest.TestCase):
    def test_single_pulse_and_train(self):
        d = mcs_stg4000()
        single = Step(Pulse(5, 100, gap_us=20))
        r = fit_protocol(Protocol((single,)), d)
        self.assertEqual((r.steps[0].planned_us, r.steps[0].delivered_us), (220, 220))
        r = fit_protocol(Protocol((Step(Pulse(5, 100), pulses=3, frequency_hz=1000),)), d)
        self.assertEqual(r.steps[0].delivered_us, 3000)

    def test_bursts(self):
        d = mcs_stg4000()
        step = Step(Pulse(5, 100), pulses=4, bursts=10, intra_burst_hz=100, burst_rate_hz=5)
        r = fit_protocol(Protocol((step,)), d)
        self.assertEqual((r.steps[0].count, r.steps[0].delivered_us), (40, 2e6))
        self.assertTrue(r.ok)
        one = Step(Pulse(5, 100), pulses=4, bursts=1, intra_burst_hz=100)
        self.assertEqual(fit_protocol(Protocol((one,)), d).steps[0].delivered_us, 40000)
        long = Step(Pulse(5, 100), pulses=20, bursts=3, intra_burst_hz=100, burst_rate_hz=10)
        r = fit_protocol(Protocol((long,)), d)
        self.assertEqual([i.code for i in r.steps[0].issues], ["SG601"])
        self.assertFalse(r.ok)

    def test_electrode_follows_use(self):
        d = mcs_stg4000()
        p = Protocol((Step(Pulse(7.3, 117, second_amplitude_ua=1.9, second_width_us=450)),))
        e = Electrode(diameter_um=10)
        best = fit_protocol(p, d, electrode=e).steps[0]
        rounded = fit_protocol(p, d, electrode=e, use="rounded").steps[0]
        self.assertAlmostEqual(best.report.density.charge_nc, best.used.first_charge_nc)
        self.assertAlmostEqual(rounded.report.density.charge_nc,
                               max(rounded.used.first_charge_nc, rounded.used.second_charge_nc))


class ProtocolInputTests(unittest.TestCase):
    def error(self, change, text):
        data = copy.deepcopy(BASE)
        change(data)
        with self.assertRaises(InputError) as ctx:
            protocol_from_dict(data)
        self.assertIn(text, str(ctx.exception))

    def test_errors_name_the_place(self):
        step = lambda **kw: (lambda d: d["protocol"][0].update(kw))  # noqa: E731
        self.error(step(frequency_hz=None), "protocol[0].frequency_hz: must be a number")
        self.error(lambda d: d["protocol"][0].pop("frequency_hz"), "protocol[0]: more than one pulse")
        self.error(step(bursts=2), "not frequency_hz")
        self.error(step(pulses=0), "protocol[0].pulses: must be a whole number")
        self.error(step(pulses=2.0), "protocol[0].pulses")
        self.error(step(channels=["1", "1"]), "listed twice")
        self.error(step(channels="1"), "protocol[0].channels")
        self.error(step(rest_s=5), "rest in a step of its own")
        self.error(step(colour="red"), "unknown field(s) colour")
        self.error(step(pulse={"amplitude_ua": -1, "width_us": 100}), "protocol[0].pulse:")
        self.error(lambda d: d["protocol"].append({"rest_s": 5, "pulses": 2}), "protocol[1]: a rest takes only")
        self.error(lambda d: d["protocol"].append({"name": "nothing"}), "protocol[1]: give a pulse")
        self.error(lambda d: d.update(protocol=[{"rest_s": 1}]), "at least one step needs a pulse")
        self.error(lambda d: d.update(protocol=[]), "non-empty list")
        self.error(lambda d: d.update(repeat=0), "repeat")
        self.error(lambda d: d.update(use="option-1"), "use:")
        self.error(lambda d: d.update(pulse={"amplitude_ua": 1, "width_us": 1}), "not both")
        burst = {"pulse": {"amplitude_ua": 5, "width_us": 100}, "bursts": 2, "pulses": 3}
        self.error(lambda d: d.update(protocol=[{**burst, "burst_rate_hz": 1}]), "needs intra_burst_hz")
        self.error(lambda d: d.update(protocol=[{**burst, "intra_burst_hz": 100}]), "needs burst_rate_hz")
        self.error(lambda d: d["protocol"][0].update(intra_burst_hz=10), "need bursts")

    def test_single_pulse_reader_points_to_protocol(self):
        with self.assertRaises(InputError) as ctx:
            from_dict(BASE)
        self.assertIn("this is a protocol", str(ctx.exception))

    def test_options(self):
        data = {**copy.deepcopy(BASE), "use": "rounded", "max_imbalance": 0.01, "electrode": {"area_um2": 500}}
        _, _, opts = protocol_from_dict(data)
        self.assertEqual((opts["use"], opts["max_imbalance"], opts["electrode"].area), ("rounded", 0.01, 500))
        with self.assertRaises(ValueError):
            fit_protocol(Protocol((Step(Pulse(1, 100)),)), mcs_stg4000(), use="balanced")


class CliProtocolTests(unittest.TestCase):
    def test_text_json_and_exit_codes(self):
        p = run("fit", EXAMPLE)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("1,300 pulses at 130 Hz on channels 1, 2", p.stdout)
        self.assertIn("10 s planned, 10.01 s delivered (+0.100%)", p.stdout)
        self.assertIn("channel 1: 4,020 pulses", p.stdout)
        p = run("fit", EXAMPLE, "--use", "rounded")
        self.assertEqual(p.returncode, 1)
        self.assertIn("NOT balanced", p.stdout)
        self.assertEqual(run("fit", EXAMPLE, "--strict").returncode, 1)  # SG201 and SG301 warnings
        data = json.loads(run("fit", EXAMPLE, "--format", "json").stdout)
        self.assertEqual((data["ok"], data["delivered_s"], len(data["steps"])), (True, 66.03, 3))
        p = run("fit", "-", stdin=json.dumps(BASE))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("all steps: 10 pulses", p.stdout)

    def test_options_that_do_not_apply(self):
        for args, text in [(("--format", "pulse"), "--format pulse"),
                           (("--export", "mcs-ascii", "--out", "-"), "--export"),
                           (("--use", "option-1"), "best or rounded")]:
            p = run("fit", EXAMPLE, *args)
            self.assertEqual(p.returncode, 2)
            self.assertIn(text, p.stderr)
        p = run("fit", EXAMPLE, "--electrode-diameter-um", "10")
        self.assertIn("Shannon k", p.stdout)


class RampTests(unittest.TestCase):
    def test_fractions(self):
        up = Ramp(up_pulses=5, start_fraction=0.15).fractions()
        self.assertEqual([round(f, 9) for f in up], [0.15, 0.32, 0.49, 0.66, 0.83])
        both = Ramp(up_pulses=4, down_pulses=4, start_fraction=0.2).fractions()
        self.assertEqual([round(f, 9) for f in both[4:]], [round(f, 9) for f in reversed(both[:4])])
        self.assertEqual([round(f, 9) for f in Ramp(down_pulses=2, start_fraction=0.5).fractions()], [0.75, 0.5])

    def test_example_levels_balance(self):
        protocol, device, options = load_protocol(RAMP_EXAMPLE)
        s = fit_protocol(protocol, device, **options).steps[0]
        self.assertEqual((len(s.ramp), [r.pulses for r in s.ramp], s.full_count), (5, [2] * 5, 190))
        self.assertTrue(s.balanced)
        self.assertEqual(s.net_charge_nc, 0)
        # full pulses at 2 nC, plus two of each level (2.8, 6.4, 9.6, 13.2 and 16.4 µA for 100 µs)
        self.assertAlmostEqual(s.phase_charge_nc, 190 * 2 + 2 * (0.28 + 0.64 + 0.96 + 1.32 + 1.64))
        self.assertEqual([i.code for i in s.issues], ["SG201", "SG203", "SG204"])
        self.assertIn("3 ramp levels (15%, 49%, 83%)", s.issues[0].message)
        self.assertIn("ramp level 15%:", s.issues[1].message)

    def test_rounded_levels_leave_charge(self):
        protocol, device, _ = load_protocol(RAMP_EXAMPLE)
        r = fit_protocol(protocol, device, use="rounded")
        s = r.steps[0]
        self.assertTrue(s.used.imbalance == 0)  # the full pulse is fine
        self.assertEqual([round(x.fraction, 2) for x in s.ramp if not x.balanced(0)], [0.15, 0.49, 0.83])
        self.assertAlmostEqual(s.net_charge_nc, 3 * 2 * -0.02)
        self.assertFalse(r.ok)
        self.assertFalse(r.channels()[0].balanced)

    def test_level_longer_than_the_period(self):
        d = Device("t", time_step_us=10, amplitude_step_ua=1)
        step = Step(Pulse(5, 100, second_amplitude_ua=2, second_width_us=250), pulses=10, frequency_hz=1e6 / 350,
                    ramp=Ramp(up_pulses=1, start_fraction=0.5))
        r = fit_protocol(Protocol((step,)), d, keep_first=True)
        self.assertIn("ramp level 50%: the pulse (400 µs) is longer than one period (350 µs)",
                      [i.message for i in r.steps[0].issues if i.code == "SG602"])
        self.assertFalse(r.ok)

    def test_bad_ramps(self):
        pulse = {"amplitude_ua": 5, "width_us": 100}
        for ramp, text in [({"up_pulses": 0}, "needs up_pulses or down_pulses"),
                           ({"up_pulses": 2.0}, "up_pulses must be a whole number"),
                           ({"up_pulses": 2, "start_fraction": 1}, "start_fraction"),
                           ({"up_pulses": 2, "start_fraction": 0}, "start_fraction"),
                           ({"up_pulses": 8, "down_pulses": 3}, "more than the train's 10"),
                           ({"up_pulses": 2, "end_fraction": 1}, "unknown field(s) end_fraction"),
                           ([], "must be an object")]:
            data = {**copy.deepcopy(BASE), "protocol": [{"pulse": pulse, "pulses": 10, "frequency_hz": 100,
                                                         "ramp": ramp}]}
            with self.assertRaises(InputError) as ctx:
                protocol_from_dict(data)
            self.assertIn(text, str(ctx.exception))
            self.assertIn("protocol[0]", str(ctx.exception))
        burst = {"pulse": pulse, "bursts": 2, "pulses": 3, "intra_burst_hz": 100, "burst_rate_hz": 1,
                 "ramp": {"up_pulses": 2}}
        with self.assertRaises(InputError) as ctx:
            protocol_from_dict({**copy.deepcopy(BASE), "protocol": [burst]})
        self.assertIn("not bursts", str(ctx.exception))
        with self.assertRaises(ValueError):
            Step(rest_s=1, ramp=Ramp(up_pulses=1))

    def test_cli_and_json(self):
        p = run("fit", RAMP_EXAMPLE)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ramp      from 15%, up over 5 pulses and down over 5 pulses: 5 level(s), all balanced",
                      p.stdout)
        self.assertIn("15%  2.8 µA x 100 µs | 0.7 µA x 400 µs, net 0 nC", p.stdout)
        p = run("fit", RAMP_EXAMPLE, "--use", "rounded")
        self.assertEqual(p.returncode, 1)
        self.assertIn("3 NOT balanced", p.stdout)
        data = json.loads(run("fit", RAMP_EXAMPLE, "--format", "json").stdout)
        ramp = data["steps"][0]["ramp"]
        self.assertEqual((ramp["up_pulses"], ramp["down_pulses"], len(ramp["levels"])), (5, 5, 5))
        self.assertEqual(ramp["levels"][0]["used_pulse"]["amplitude_ua"], 2.8)
        self.assertIsNone(json.loads(run("fit", EXAMPLE, "--format", "json").stdout)["steps"][0]["ramp"])

    def test_long_ramp_text_is_shortened(self):
        data = {**copy.deepcopy(BASE), "protocol": [{"pulse": {"amplitude_ua": 50, "width_us": 100}, "pulses": 100,
                                                     "frequency_hz": 100, "ramp": {"up_pulses": 20}}]}
        p = run("fit", "-", stdin=json.dumps(data))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("... 11 more (--format json lists every level)", p.stdout)


if __name__ == "__main__":
    unittest.main()
