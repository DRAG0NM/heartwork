# -*- coding: utf-8 -*-
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import coach_core as C


def fake_context():
    """Hand-made context: only the views that the claim checks read."""
    status = [("hrv_rmssd", "above"), ("resting_hr", "above"), ("sleep_duration_min", "within"),
              ("stress", "below"), ("training_load", "above")]
    return {"views": {"baseline_status": [{"key": key, "status": value} for key, value in status],
                      "raw_signals": [{"key": key} for key, _ in status],
                      "recent_daily": [{"date": "20260811", "hrv_rmssd": 60}]}}


def observation(statement, claims=None, pointer="/views/raw_signals/0/latest"):
    item = {"id": "o", "statement": statement, "confidence": "medium",
            "evidence": [{"source": "coach_context", "pointer": pointer, "layer": "raw_signal"}]}
    if claims is not None:
        item["claims"] = claims
    return item


def claim(metric, direction):
    return {"metric": metric, "direction": direction}


class TestClaims(unittest.TestCase):
    def codes(self, item):
        return [error["code"] for error in C._claim_errors(item, "$.o", fake_context())]

    def test_consistent_claims_pass(self):
        self.assertEqual(self.codes(observation("HRV 升高", [claim("hrv_rmssd", "above")])), [])
        self.assertEqual(self.codes(observation("HRV is higher than usual",
                                                [claim("hrv_rmssd", "above")])), [])
        self.assertEqual(self.codes(observation("睡眠处于正常范围",
                                                [claim("sleep_duration_min", "within")])), [])

    def test_direction_must_equal_baseline_status(self):
        self.assertEqual(self.codes(observation("HRV", [claim("hrv_rmssd", "below")])),
                         ["claim_mismatch"])

    def test_unknown_metric_and_malformed_claims(self):
        self.assertEqual(self.codes(observation("x", [claim("weight_kg", "above")])), ["claim_mismatch"])
        self.assertEqual(self.codes(observation("x", [{"metric": "hrv_rmssd", "direction": "up"}])),
                         ["claim_mismatch"])
        self.assertEqual(self.codes(observation("x", "above")), ["claim_mismatch"])

    def test_direction_words_need_claims_when_metric_in_evidence(self):
        self.assertEqual(self.codes(observation("HRV 偏高")), ["claim_required"])
        self.assertEqual(self.codes(observation("sleep fell below usual")), ["claim_required"])
        self.assertEqual(self.codes(observation("HRV 数值如下",)), [])

    def test_words_about_unrelated_evidence_need_no_claims(self):
        item = observation("计划已改善", pointer="/views/current_plan")
        item["evidence"][0]["layer"] = "plan"
        self.assertEqual(self.codes(item), [])
        daily = observation("体重上升", pointer="/views/recent_daily/0/weight_kg")
        self.assertEqual(self.codes(daily), [])
        daily = observation("HRV 上升", pointer="/views/recent_daily/0/hrv_rmssd")
        self.assertEqual(self.codes(daily), ["claim_required"])

    def test_text_conflicts_with_claim(self):
        self.assertEqual(self.codes(observation("HRV 下降", [claim("hrv_rmssd", "above")])),
                         ["claim_text_conflict"])
        self.assertEqual(self.codes(observation("HRV 在正常范围", [claim("hrv_rmssd", "above")])),
                         ["claim_text_conflict"])
        self.assertEqual(self.codes(observation("压力 higher", [claim("stress", "below")])),
                         ["claim_text_conflict"])

    def test_negation_is_not_a_direction(self):
        self.assertEqual(self.codes(observation("睡眠没有下降，处于正常范围",
                                                [claim("sleep_duration_min", "within")])), [])
        self.assertEqual(self.codes(observation("HRV 并未偏低", [claim("hrv_rmssd", "above")])), [])

    def test_polarity_follows_numeric_direction_not_good_or_bad(self):
        # resting_hr above its baseline is numerically higher but physiologically worse
        self.assertEqual(self.codes(observation("静息心率升高", [claim("resting_hr", "above")])), [])
        self.assertEqual(self.codes(observation("静息心率变差", [claim("resting_hr", "above")])), [])
        self.assertEqual(self.codes(observation("静息心率改善", [claim("resting_hr", "above")])),
                         ["claim_text_conflict"])
        self.assertEqual(self.codes(observation("压力下降", [claim("stress", "below")])), [])
        self.assertEqual(self.codes(observation("压力改善", [claim("stress", "below")])), [])
        self.assertEqual(self.codes(observation("HRV 改善", [claim("hrv_rmssd", "above")])), [])
        self.assertEqual(self.codes(observation("训练负荷改善", [claim("training_load", "above")])),
                         ["claim_text_conflict"])

    def test_validate_response_reports_claim_codes(self):
        with open(os.path.join(ROOT, "examples", "synthetic_week.json"), encoding="utf-8") as handle:
            payload = json.load(handle)
        with open(os.path.join(ROOT, "examples", "synthetic_response.json"), encoding="utf-8") as handle:
            response = json.load(handle)
        context = C.build_context(payload, "daily_guidance")
        self.assertTrue(C.validate_response(response, context)["valid"])
        response["observations"][0]["claims"][0]["direction"] = "above"
        result = C.validate_response(response, context)
        self.assertIn("claim_mismatch", {item["code"] for item in result["errors"]})
        del response["observations"][0]["claims"]
        result = C.validate_response(response, context)
        self.assertIn("claim_required", {item["code"] for item in result["errors"]})


if __name__ == "__main__":
    unittest.main()
