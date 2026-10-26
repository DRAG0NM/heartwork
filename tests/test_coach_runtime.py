# -*- coding: utf-8 -*-
import copy
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import api
import coach_decisions as D
import coach_events as E
import coach_eval
import coach_runtime as R
import coach_skills as K
import store
from coach_core import resolve_pointer


def good_response(context, **changes):
    evidence = [{"source": "coach_context", "pointer": "/views/raw_signals/0/latest",
                 "layer": "raw_signal",
                 "observed_value": resolve_pointer(context, "/views/raw_signals/0/latest")}]
    response = {
        "kind": "coach_response", "schema_version": 2, "task": context["task"],
        "as_of": context["as_of"], "data_status": {"complete": False, "missing": []},
        "observations": [{"id": "o1", "statement": "合成观察", "confidence": "medium",
                          "evidence": evidence}],
        "recommendations": [{"id": "r1", "action_type": "maintain", "action": "保持",
                             "confidence": "low", "evidence": evidence,
                             "requires_confirmation": False, "changes_plan": False}],
        "risks": [{"level": "green", "signal": "无", "action": "观察"}],
        "questions": [], "limitations": ["合成"],
    }
    response.update(changes)
    return response


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = os.path.join(self._tmp.name, "ws")


class TestEvents(Base):
    def test_log_validate_and_supersede(self):
        first = E.log_event(self.base, {"type": "morning_checkin", "date": "2026-08-10",
                                        "pain": 3, "subjective_recovery": 6})
        self.assertTrue(first["id"].startswith("ev_"))
        self.assertEqual(first["date"], "20260810")
        for bad in ({"type": "x"}, {"type": "note", "pain": 11},
                    {"type": "note", "subjective_recovery": 0},
                    {"type": "note", "red_flags": ["unknown"]},
                    {"type": "note", "note": "a" * 1001},
                    {"type": "note", "supersedes": "ev_missing"}):
            with self.assertRaises(ValueError):
                E.log_event(self.base, bad)
        second = E.log_event(self.base, {"type": "morning_checkin", "date": "20260810",
                                         "pain": 0, "supersedes": first["id"]})
        self.assertEqual(len(E.load_events(self.base)), 2)
        self.assertEqual([item["id"] for item in E.active_events(self.base)], [second["id"]])


class TestRouting(unittest.TestCase):
    def test_choose_task(self):
        self.assertEqual(R.choose_task("今天练什么", []), "daily_guidance")
        self.assertEqual(R.choose_task("帮我做周复盘", []), "weekly_review")
        self.assertEqual(R.choose_task("刚跑完怎么恢复", []), "post_workout")
        self.assertEqual(R.choose_task("膝盖疼", []), "pain_followup")
        self.assertEqual(R.choose_task("今天练什么", [{"date": "20260810", "pain": 2}]),
                         "pain_followup")
        self.assertEqual(R.choose_task("x", [{"date": "20260810", "red_flags": ["fainting"]}]),
                         "pain_followup")

    def test_route_is_deterministic_and_bounded(self):
        self.assertEqual(K.route("pain_followup", ""), ["coach-pain"])
        picked = K.route("weekly_review", "下周计划和睡眠")
        self.assertEqual(picked, K.route("weekly_review", "下周计划和睡眠"))
        self.assertTrue(1 <= len(picked) <= 2)
        for task in ("daily_guidance", "post_workout", "weekly_review", "pain_followup"):
            self.assertTrue(K.route(task, "") )

    def test_skills_have_frontmatter(self):
        for name in K.registry():
            text = K.skill_text(name)
            self.assertTrue(text.startswith("---\nname: %s\ndescription: " % name))


class TestJobs(Base):
    def setUp(self):
        super().setUp()
        api.init(self.base, True)

    def test_sign_complete_and_replay(self):
        job = R.start_job(self.base, "今天练什么")
        self.assertEqual(job["kind"], "coach_job")
        result = R.complete_job(self.base, job["job_id"], good_response(job["context"]))
        self.assertTrue(result["valid"], result["errors"])
        self.assertIn("教练回复", result["markdown"])
        self.assertTrue(R.list_jobs(self.base)[0]["completed"])

    def test_tampered_job_rejected(self):
        job = R.start_job(self.base, "今天练什么")
        path = os.path.join(store.home(self.base), "jobs", job["job_id"] + ".json")
        stored = store.read_json(path)
        stored["task"] = "weekly_review"
        store.write_json(path, stored)
        result = R.complete_job(self.base, job["job_id"], good_response(job["context"]))
        self.assertFalse(result["valid"])
        self.assertEqual(result["errors"][0]["code"], "signature")

    def test_stale_job_rejected(self):
        job = R.start_job(self.base, "今天练什么")
        api.log_event(self.base, {"type": "note", "date": job["context"]["as_of"], "note": "变化"})
        result = R.complete_job(self.base, job["job_id"], good_response(job["context"]))
        self.assertEqual(result["errors"][0]["code"], "stale")

    def test_bad_response_and_unknown_job(self):
        job = R.start_job(self.base, "今天练什么")
        bad = good_response(job["context"])
        bad["observations"][0]["evidence"][0]["observed_value"] = -1
        self.assertFalse(R.complete_job(self.base, job["job_id"], bad)["valid"])
        self.assertEqual(R.complete_job(self.base, "job_20200101_000000_abcdef", {})
                         ["errors"][0]["code"], "job_not_found")
        self.assertEqual(R.complete_job(self.base, "../x", {})["errors"][0]["code"], "job_not_found")


class TestDecisions(Base):
    def setUp(self):
        super().setUp()
        api.init(self.base, True)

    def _plan_job(self):
        job = R.start_job(self.base, "下周计划", task="weekly_review")
        response = good_response(job["context"])
        rec = response["recommendations"][0]
        rec.update(action_type="draft_plan", changes_plan=True, requires_confirmation=True)
        return R.complete_job(self.base, job["job_id"], response)

    def test_only_human_decides_and_confirm_required(self):
        result = self._plan_job()
        self.assertTrue(result["valid"], result["errors"])
        key = result["suggestions"][0]["key"]
        self.assertEqual(len(D.pending(self.base)), 1)
        with self.assertRaises(PermissionError):
            D.decide(self.base, key, True, actor="model")
        with self.assertRaises(PermissionError):
            D.apply_plan(self.base, key, "新计划", True)
        D.decide(self.base, key, True)
        with self.assertRaises(PermissionError):
            D.apply_plan(self.base, key, "新计划", False)
        before = store.load_plan(self.base)["current_plan"]
        D.apply_plan(self.base, key, "新计划", True)
        self.assertEqual(store.load_plan(self.base)["current_plan"], "新计划")
        self.assertEqual(store.load_plan(self.base)["history"][-1]["plan"], before)
        self.assertEqual(D.pending(self.base), [])

    def test_rejected_cannot_apply(self):
        key = self._plan_job()["suggestions"][0]["key"]
        api.decide(self.base, key, False)
        with self.assertRaises(PermissionError):
            D.apply_plan(self.base, key, "x", True)


class TestEvalAndApi(Base):
    def test_eval_all_pass(self):
        result = coach_eval.run_eval()
        self.assertGreaterEqual(result["total"], 8)
        self.assertEqual(result["passed"], result["total"], result["cases"])

    def test_end_to_end(self):
        self.assertEqual(api.init(self.base, True)["status"]["days"] > 0, True)
        self.assertIn("markdown", api.weekly_report(self.base))
        self.assertTrue(os.path.exists(api.weekly_report(self.base)["path"]))
        snap = api.snapshot(self.base)
        self.assertIn("raw_signals", snap)
        self.assertEqual(snap["safety_gate"]["level"], "green")
        job = api.start_job(self.base, "今天练什么")
        self.assertEqual(job["task"], "daily_guidance")
        self.assertTrue(api.complete_job(self.base, job["job_id"],
                                         good_response(job["context"]))["valid"])
        self.assertEqual(len(api.list_jobs(self.base)), 1)

    def test_pain_event_changes_task_and_gate(self):
        api.init(self.base, True)
        api.log_event(self.base, {"type": "morning_checkin", "date": store.latest_date(self.base),
                                  "pain": 5})
        job = api.start_job(self.base, "今天练什么")
        self.assertEqual(job["task"], "pain_followup")
        self.assertEqual(job["context"]["safety_gate"]["level"], "yellow")

    def test_profile_whitelist(self):
        self.assertEqual(api.set_profile(self.base, {"hr_max": 190})["hr_max"], 190)
        with self.assertRaises(ValueError):
            api.set_profile(self.base, {"secret": 1})
        with self.assertRaises(ValueError):
            api.set_profile(self.base, {"hr_max": 900})


if __name__ == "__main__":
    unittest.main()
