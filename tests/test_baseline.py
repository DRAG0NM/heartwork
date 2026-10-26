# -*- coding: utf-8 -*-
import math
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import api
import baseline as B
import core_metrics as M
import state_layers as S
import store


def _days(end, count):
    last = datetime.strptime(end, "%Y%m%d")
    return [(last - timedelta(days=count - 1 - i)).strftime("%Y%m%d") for i in range(count)]


def _rows(key, values, end="20260831"):
    return [{"date": day, key: value} for day, value in zip(_days(end, len(values)), values)]


class TestBaseline(unittest.TestCase):
    def test_hrv_uses_ln_and_converts_back(self):
        base = [40, 42, 44, 46] * 7
        rows = _rows("hrv_rmssd", base + [50] * 7)
        status = B.metric_status(rows, "hrv_rmssd", "20260831")
        self.assertEqual(status["transform"], "ln")
        self.assertAlmostEqual(status["recent_mean"], 50.0, places=1)
        self.assertAlmostEqual(status["center"], math.exp(math.log(43)), delta=1.5)
        self.assertEqual(status["status"], "above")
        self.assertEqual(status["oriented"], "better")
        self.assertLess(status["band"][0], status["center"])
        self.assertGreater(status["band"][1], status["center"])

    def test_swc_decides_status_and_polarity(self):
        base = [60, 61, 59, 60, 62, 58, 60] * 4
        up = B.metric_status(_rows("resting_hr", base + [70] * 7), "resting_hr", "20260831")
        self.assertEqual((up["status"], up["oriented"]), ("above", "worse"))
        flat = B.metric_status(_rows("resting_hr", base + [60] * 7), "resting_hr", "20260831")
        self.assertEqual((flat["status"], flat["oriented"]), ("within", "normal"))
        load = B.metric_status(_rows("training_load", [50, 60] * 14 + [200] * 7),
                               "training_load", "20260831")
        self.assertEqual(load["oriented"], "neutral")
        self.assertEqual(load["status"], "above")

    def test_mad_floor_applies_to_constant_series(self):
        status = B.metric_status(_rows("resting_hr", [55] * 28 + [55.2] * 7), "resting_hr", "20260831")
        self.assertEqual(status["scale"], 0.5)
        self.assertEqual(status["swc"], 0.25)
        self.assertEqual(status["status"], "within")

    def test_insufficient_window_returns_none(self):
        self.assertIsNone(B.metric_status(_rows("resting_hr", [55] * 4 + [56] * 3), "resting_hr", "20260831"))
        rows = _rows("resting_hr", [55] * 28, end="20260810") + [{"date": "20260901", "resting_hr": 60}]
        self.assertIsNone(B.metric_status(rows, "resting_hr", "20260901"))

    def test_window_is_rolling_not_calendar_week(self):
        rows = _rows("stress", [30, 31, 29, 30] * 8 + [50] * 3, end="20260901")
        mid_week = B.metric_status(rows, "stress", "20260901")
        self.assertEqual(mid_week["n_recent"], 7)
        self.assertEqual(mid_week["n_baseline"], 28)

    def test_families(self):
        rows = _rows("resting_hr", [55, 56, 54, 55] * 8 + [48] * 3)
        for row in rows:
            row["hrv_rmssd"] = 45
        statuses = B.day_status(rows, "20260831")
        fam = B.family_status(statuses)
        self.assertTrue(fam["recovery"]["available"])
        self.assertFalse(fam["sleep"]["available"])
        self.assertEqual(fam["recovery"]["oriented"], "better")


class TestLoad(unittest.TestCase):
    def test_banister_single_impulse(self):
        result = M.banister({"20260801": 100}, "20260801")
        self.assertAlmostEqual(result["atl"], 100 * (1 - math.exp(-1 / 7)), places=1)
        self.assertAlmostEqual(result["ctl"], 100 * (1 - math.exp(-1 / 42)), places=1)
        self.assertAlmostEqual(result["tsb"], result["ctl"] - result["atl"], places=1)
        later = M.banister({"20260801": 100}, "20260808")
        self.assertAlmostEqual(later["atl"], 100 * (1 - math.exp(-1 / 7)) * math.exp(-1), places=1)
        self.assertIsNone(M.banister({}, "20260801"))

    def test_acwr_uncoupled_does_not_overlap(self):
        loads = {d: 100 for d in _days("20260831", 35)[:28]}
        loads.update({d: 200 for d in _days("20260831", 7)})
        result = M.acwr_uncoupled(loads, "20260831")
        self.assertEqual(result["acute_mean"], 200)
        self.assertEqual(result["chronic_mean"], 100)
        self.assertEqual(result["ratio"], 2.0)
        self.assertIsNone(M.acwr_uncoupled({"20260831": 50}, "20260831"))

    def test_srpe_from_feedback_and_watchless_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            store.init(tmp)
            store.add_activities(tmp, [
                {"date": "20260810", "start": "2026-08-10T07:00:00+08:00", "type": "run",
                 "duration_s": 3000},
                {"date": "20260811", "start": "2026-08-11T07:00:00+08:00", "type": "run",
                 "duration_s": 3000, "hr_zone_minutes": [10, 20, 10, 5, 5]}], "import")
            import coach_events
            coach_events.log_event(tmp, {"type": "post_workout_feedback", "date": "20260810", "rpe": 6})
            coach_events.log_event(tmp, {"type": "post_workout_feedback", "date": "20260811", "rpe": 9})
            coach_events.log_event(tmp, {"type": "post_workout_feedback", "date": "20260812",
                                         "rpe": 5, "duration_min": 40})
            with self.assertRaises(ValueError):
                coach_events.log_event(tmp, {"type": "note", "duration_min": 601})
            acts, extras, loads = store.load_details(tmp)
            self.assertEqual((acts[0]["load_method"], acts[0]["load"]), ("srpe", 300.0))
            self.assertEqual(acts[1]["load_method"], "trimp_edwards")
            self.assertEqual(extras[0]["load"], 200.0)
            self.assertEqual(loads["20260812"], 200.0)

    def test_demo_load_axis_available_and_daily_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            api.init(tmp, True)
            snap = api.snapshot(tmp)
            axis = next(a for a in snap["state"]["axes"] if a["id"] == "training_load_fatigue")
            self.assertTrue(axis["available"])
            self.assertIn("tsb", axis["components"])
            self.assertIn("acwr_uncoupled", axis["load"])
            self.assertNotIn("pointer", snap["raw_signals"][0])
            self.assertEqual(snap["raw_signals"][0]["source"], "daily.json")
            methods = {item.get("load_method") for item in store.load_details(tmp)[0]}
            self.assertIn("srpe", methods)
            view = api.daily_view(tmp)
            self.assertEqual(len(view["days"]), 56)
            last = view["days"][-1]
            self.assertEqual(last["date"], view["as_of"])
            self.assertIn("status", last["metrics"]["hrv_rmssd"])
            self.assertIn("recovery", last["families"])
            self.assertIn("tsb", last["load"])
            self.assertIn("atl", view["load_summary"])

    def test_load_axis_unavailable_with_short_history(self):
        loads = {d: 50 for d in _days("20260810", 10)}
        axis = S._load_axis([], "x", "20260810", loads)
        self.assertFalse(axis["available"])


if __name__ == "__main__":
    unittest.main()
