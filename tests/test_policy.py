# -*- coding: utf-8 -*-
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import api
import coach_decisions as D
import coach_policy as P
import coach_runtime as R
import mcp_server
import sec
import store
import web
from coach_core import resolve_pointer, validate_response

RAW = "/views/raw_signals/0/latest"


def evidence(context, pointer=RAW, layer="raw_signal"):
    return {"source": "coach_context", "pointer": pointer, "layer": layer,
            "observed_value": resolve_pointer(context, pointer)}


def response(context, action="maintain", level="green", confidence="low", evidence_list=None, **rec):
    items = evidence_list or [evidence(context)]
    recommendation = {"id": "r1", "action_type": action, "action": "合成建议", "confidence": confidence,
                      "evidence": items, "requires_confirmation": False, "changes_plan": False}
    recommendation.update(rec)
    return {"kind": "coach_response", "schema_version": 2, "task": context["task"],
            "as_of": context["as_of"], "data_status": {"complete": False, "missing": []},
            "observations": [{"id": "o1", "statement": "合成观察", "confidence": "medium",
                              "evidence": items}],
            "recommendations": [recommendation],
            "risks": [{"level": level, "signal": "无", "action": "观察"}],
            "questions": [], "limitations": ["合成"]}


def plan_rec(**changes):
    values = {"changes_plan": True, "requires_confirmation": False, "plan_text": "合成新计划",
              "confidence": "medium"}
    values.update(changes)
    return values


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = os.path.join(self._tmp.name, "ws")
        api.init(self.base, True)

    def job(self, level, query="今天练什么", task=None):
        if P.get_level(self.base) != level:
            api.set_autonomy(self.base, level, confirm=True)
        return R.start_job(self.base, query, task)

    def codes(self, context, resp):
        return {item["code"] for item in validate_response(resp, context)["errors"]}


class TestLevels(Base):
    def test_default_and_settings_file(self):
        self.assertEqual(P.LEVELS, ("conservative", "balanced", "autonomous"))
        self.assertEqual(api.get_autonomy(self.base)["level"], "balanced")
        api.set_autonomy(self.base, "conservative")
        self.assertEqual(store.read_json(os.path.join(self.base, "settings.json")),
                         {"coach_autonomy": "conservative"})
        self.assertEqual(api.status(self.base)["autonomy"], "conservative")
        with self.assertRaises(ValueError):
            api.set_autonomy(self.base, "yolo")

    def test_raising_to_autonomous_needs_confirm_lowering_does_not(self):
        with self.assertRaises(PermissionError):
            api.set_autonomy(self.base, "autonomous")
        with self.assertRaises(PermissionError):
            api.set_autonomy(self.base, "autonomous", confirm=False)
        self.assertEqual(api.get_autonomy(self.base)["level"], "balanced")
        self.assertEqual(api.set_autonomy(self.base, "autonomous", confirm=True)["level"], "autonomous")
        self.assertEqual(api.set_autonomy(self.base, "balanced")["level"], "balanced")
        log = [r for r in D.records(self.base) if r["event"] == "autonomy"]
        self.assertEqual([(r["from"], r["to"]) for r in reversed(log)],
                         [("balanced", "autonomous"), ("autonomous", "balanced")])

    def test_cli_autonomy(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sec.main(["--home", self.base, "autonomy", "autonomous"]), 1)
            self.assertEqual(sec.main(["--home", self.base, "autonomy", "autonomous", "--confirm"]), 0)
            self.assertEqual(P.get_level(self.base), "autonomous")
            self.assertEqual(sec.main(["--home", self.base, "autonomy", "conservative"]), 0)

    def test_policy_is_in_context_and_instructions(self):
        for level in P.LEVELS:
            job = self.job(level)
            self.assertEqual(job["context"]["policy"]["level"], level)
            self.assertIn(level, job["instructions"])
        self.assertIn("plan_text", job["instructions"])
        self.assertIn("claims", job["instructions"])

    def test_policy_signed_with_job(self):
        job = self.job("conservative")
        path = os.path.join(store.home(self.base), "jobs", job["job_id"] + ".json")
        stored = store.read_json(path)
        stored["context"]["policy"] = P.rules("autonomous", stored["task"])
        store.write_json(path, stored)
        result = R.complete_job(self.base, job["job_id"], response(job["context"]))
        self.assertFalse(result["valid"])

    def test_level_fixed_at_job_creation(self):
        job = self.job("balanced", "下周计划", "weekly_review")
        api.set_autonomy(self.base, "conservative")
        resp = response(job["context"], "draft_plan", requires_confirmation=True, changes_plan=True)
        self.assertTrue(R.complete_job(self.base, job["job_id"], resp)["valid"])


class TestConservative(Base):
    def test_rules(self):
        job = self.job("conservative")
        context = job["context"]
        self.assertNotIn("suggest_session", context["policy"]["allowed_action_types"])
        self.assertIn("permission", self.codes(context, response(context, "suggest_session")))
        weekly = self.job("conservative", "下周计划", "weekly_review")["context"]
        self.assertIn("permission", self.codes(weekly, response(weekly, "draft_plan")))
        self.assertIn("permission", self.codes(weekly, response(
            weekly, "reduce", requires_confirmation=True, changes_plan=True)))
        state = evidence(context, "/views/state/axes/0/index", "experimental_model")
        self.assertIn("experimental_evidence", self.codes(
            context, response(context, "reduce", evidence_list=[evidence(context), state])))
        self.assertIn("confidence_cap", self.codes(context, response(context, confidence="high")))
        no_raw = evidence(context, "/safety_gate/level", "deterministic_metric")
        self.assertIn("raw_signal_required", self.codes(
            context, response(context, evidence_list=[no_raw])))
        self.assertEqual(self.codes(context, response(context, confidence="medium")), set())


class TestBalanced(Base):
    def test_rules(self):
        context = self.job("balanced")["context"]
        self.assertEqual(self.codes(context, response(context, confidence="high")), set())
        state = evidence(context, "/views/state/axes/0/index", "experimental_model")
        self.assertEqual(self.codes(context, response(
            context, "reduce", evidence_list=[evidence(context), state])), set())
        self.assertIn("experimental_only", self.codes(
            context, response(context, "reduce", evidence_list=[state])))
        weekly = self.job("balanced", "下周计划", "weekly_review")["context"]
        self.assertIn("confirmation", self.codes(weekly, response(
            weekly, "draft_plan", changes_plan=True, plan_text="x")))
        self.assertIn("permission", self.codes(context, response(context, "draft_plan")))


class TestAutonomous(Base):
    def test_actions_and_plan_text(self):
        context = self.job("autonomous")["context"]
        self.assertIn("draft_plan", context["policy"]["allowed_action_types"])
        self.assertEqual(self.codes(context, response(context, "reduce", **plan_rec())), set())
        self.assertIn("plan_text", self.codes(context, response(context, "reduce", **plan_rec(plan_text=""))))
        self.assertIn("plan_text", self.codes(
            context, response(context, "reduce", **plan_rec(plan_text="字" * 2001))))
        state = [evidence(context, "/views/state/axes/0/index", "experimental_model")]
        self.assertIn("experimental_only", self.codes(
            context, response(context, "suggest_session", evidence_list=state)))
        mixed = [evidence(context)] + state
        self.assertEqual(self.codes(context, response(context, "reduce", evidence_list=mixed)), set())

    def test_auto_apply_logs_and_undo(self):
        before = store.load_plan(self.base)["current_plan"]
        job = self.job("autonomous")
        result = R.complete_job(self.base, job["job_id"], response(job["context"], "reduce", **plan_rec()))
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(len(result["auto_applied"]), 1)
        applied = result["auto_applied"][0]
        self.assertEqual(applied["plan_text"], "合成新计划")
        self.assertEqual(store.load_plan(self.base)["current_plan"], "合成新计划")
        self.assertEqual(result["suggestions"][0]["status"], "auto_applied")
        record = [r for r in D.records(self.base) if r["event"] == "auto_apply"][0]
        self.assertEqual((record["actor"], record["old_plan"]), ("coach_auto", before))
        until = datetime.fromisoformat(applied["undo_until"])
        self.assertAlmostEqual((until - datetime.now()).days, 6, delta=1)
        self.assertEqual(D.pending(self.base), [])
        with self.assertRaises(PermissionError):
            D.undo(self.base, applied["key"], actor="coach_auto")
        api.undo(self.base, applied["key"])
        self.assertEqual(store.load_plan(self.base)["current_plan"], before)
        self.assertEqual(D.all_suggestions(self.base)[0]["status"], "undone")
        with self.assertRaises(ValueError):
            api.undo(self.base, applied["key"])

    def test_undo_expires_after_seven_days(self):
        key = D.register(self.base, "job_x", "h", {"id": "r"})["key"]
        D.auto_apply(self.base, key, "新", now=datetime.now() - timedelta(days=8))
        with self.assertRaises(ValueError):
            D.undo(self.base, key)
        self.assertEqual(store.load_plan(self.base)["current_plan"], "新")

    def test_rate_limit_72h_degrades_to_pending(self):
        first = self.job("autonomous")
        self.assertEqual(len(R.complete_job(self.base, first["job_id"], response(
            first["context"], "reduce", **plan_rec()))["auto_applied"]), 1)
        second = self.job("autonomous")
        result = R.complete_job(self.base, second["job_id"], response(
            second["context"], "reduce", **plan_rec(plan_text="第二版")))
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["auto_applied"], [])
        self.assertEqual(result["suggestions"][0]["status"], "pending")
        self.assertIn("rate_limited", result["suggestions"][0]["auto_blocked"])
        self.assertEqual(store.load_plan(self.base)["current_plan"], "合成新计划")
        later = D.rate_blockers(self.base, now=datetime.now() + timedelta(hours=73))
        self.assertEqual(later, [])

    def test_guardrails_degrade_to_pending(self):
        job = self.job("autonomous")
        context = job["context"]
        low = R.complete_job(self.base, job["job_id"], response(
            context, "reduce", **plan_rec(confidence="low")))
        self.assertEqual(low["auto_applied"], [])
        self.assertIn("low_confidence", low["suggestions"][0]["auto_blocked"])
        no_raw = evidence(context, "/safety_gate/level", "deterministic_metric")
        blockers = P.static_blockers(context, response(context, "reduce", evidence_list=[no_raw],
                                                       **plan_rec())["recommendations"][0])
        self.assertEqual(blockers, ["no_raw_signal"])

    def test_yellow_gate_blocks_auto_apply(self):
        api.log_event(self.base, {"type": "morning_checkin", "date": store.latest_date(self.base), "pain": 4})
        job = self.job("autonomous", task="daily_guidance")
        self.assertEqual(job["context"]["safety_gate"]["level"], "yellow")
        result = R.complete_job(self.base, job["job_id"], response(
            job["context"], "reduce", "yellow", **plan_rec()))
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["auto_applied"], [])
        self.assertIn("safety_gate_not_green", result["suggestions"][0]["auto_blocked"])
        keep = response(job["context"], "maintain", "yellow")
        self.assertIn("safety_action", self.codes(job["context"], keep))

    def test_red_gate_floor_unchanged(self):
        api.log_event(self.base, {"type": "morning_checkin", "date": store.latest_date(self.base),
                                  "red_flags": ["chest_pain"]})
        context = self.job("autonomous", task="daily_guidance")["context"]
        self.assertEqual(context["safety_gate"]["level"], "red")
        self.assertIn("safety_action", self.codes(context, response(context, "reduce", "red")))
        self.assertIn("safety_gate", self.codes(context, response(context, "seek_emergency_help", "green")))
        ok = response(context, "seek_emergency_help", "red")
        self.assertEqual(self.codes(context, ok), set())

    def test_lowering_autonomy_blocks_pending_job(self):
        job = self.job("autonomous")
        api.set_autonomy(self.base, "balanced")
        result = R.complete_job(self.base, job["job_id"], response(job["context"], "reduce", **plan_rec()))
        self.assertEqual(result["auto_applied"], [])
        self.assertIn("autonomy_lowered", result["suggestions"][0]["auto_blocked"])


class TestFloors(Base):
    def test_floors_hold_at_every_level(self):
        for level in P.LEVELS:
            context = self.job(level)["context"]
            forged = response(context)
            forged["observations"][0]["evidence"][0]["observed_value"] = -1
            self.assertIn("value_mismatch", self.codes(context, forged))
            tampered = copy.deepcopy(context)
            tampered["query"] = "x"
            self.assertIn("context_hash", self.codes(tampered, response(context)))

    def test_mcp_cannot_raise_autonomy(self):
        names = {item[0] for item in mcp_server.TOOLS}
        self.assertIn("get_autonomy", names)
        for hidden in ("set_autonomy", "undo", "decide", "apply_plan", "auto_apply"):
            self.assertNotIn(hidden, names)
        server = mcp_server.Server(self.base)
        self.assertEqual(server.call_tool("get_autonomy", {})["level"], "balanced")
        reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "set_autonomy", "arguments": {"level": "autonomous"}}})
        self.assertIn("error", reply)
        self.assertEqual(P.get_level(self.base), "balanced")


class TestWebEndpoints(Base):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.base))
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=data, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(request) as reply:
                return reply.status, json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_autonomy_undo_decisions(self):
        self.assertEqual(self.call("/api/autonomy")[1]["level"], "balanced")
        self.assertEqual(self.call("/api/autonomy", {"level": "autonomous"})[0], 403)
        status, body = self.call("/api/autonomy", {"level": "autonomous", "confirm": True})
        self.assertEqual((status, body["level"]), (200, "autonomous"))
        job = R.start_job(self.base, "今天练什么")
        done = R.complete_job(self.base, job["job_id"], response(job["context"], "reduce", **plan_rec()))
        key = done["auto_applied"][0]["key"]
        decisions = self.call("/api/decisions")[1]
        item = [s for s in decisions["suggestions"] if s["key"] == key][0]
        self.assertTrue(item["auto"]["can_undo"])
        self.assertEqual(decisions["log"][0]["event"], "auto_apply")
        self.assertEqual(self.call("/api/undo", {"key": key})[0], 200)
        self.assertFalse([s for s in self.call("/api/decisions")[1]["suggestions"]
                          if s["key"] == key][0]["auto"]["can_undo"])
        self.assertEqual(self.call("/api/undo", {"key": key})[0], 400)


if __name__ == "__main__":
    unittest.main()
