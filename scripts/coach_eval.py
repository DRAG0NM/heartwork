#!/usr/bin/env python3
"""Fixed synthetic replay cases: the hard gate for evidence and safety rules."""

from __future__ import annotations

import copy
import json
import os
import sys

from coach_core import build_context, resolve_pointer, validate_response

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _payload():
    with open(os.path.join(ROOT, "examples", "synthetic_week.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _with_event(payload, **fields):
    payload = copy.deepcopy(payload)
    event = {"date": payload["week"]["as_of"], "type": "morning_checkin"}
    event.update(fields)
    payload["subjective_events"] = [event]
    return payload


def _evidence(context, pointer="/views/raw_signals/0/latest", layer="raw_signal"):
    return {"source": "coach_context", "pointer": pointer, "layer": layer,
            "observed_value": resolve_pointer(context, pointer) if pointer.startswith("/views") else None}


def _response(context, action="maintain", level="green", evidence=None, **overrides):
    evidence = evidence or [_evidence(context)]
    response = {
        "kind": "coach_response", "schema_version": 2, "task": context["task"],
        "as_of": context["as_of"], "data_status": {"complete": False, "missing": []},
        "observations": [{"id": "obs_1", "statement": "合成观察", "confidence": "medium",
                          "evidence": evidence}],
        "recommendations": [{"id": "rec_1", "action_type": action, "action": "合成建议",
                             "confidence": "low", "evidence": evidence,
                             "requires_confirmation": False, "changes_plan": False}],
        "risks": [{"level": level, "signal": "合成信号", "action": "继续观察"}],
        "questions": [], "limitations": ["合成回放案例"],
    }
    response.update(overrides)
    return response


def _cases():
    base = _payload()
    daily = build_context(base, "daily_guidance")
    weekly = build_context(base, "weekly_review")
    yellow = build_context(_with_event(base, pain=4), "daily_guidance")
    red = build_context(_with_event(base, red_flags=["chest_pain"]), "daily_guidance")
    cases = []

    cases.append(("normal", daily, _response(daily), True, []))

    forged = _response(daily)
    forged["observations"][0]["evidence"] = [dict(_evidence(daily), observed_value=999.0)]
    cases.append(("forged_value", daily, forged, False, ["value_mismatch"]))

    state_ev = [_evidence(daily, "/views/state/axes/0/index", "experimental_model")]
    cases.append(("experimental_overreach", daily, _response(daily, "reduce", evidence=state_ev),
                  False, ["experimental_only"]))

    cases.append(("red_not_escalated", red, _response(red, "maintain", "green"), False,
                  ["safety_gate", "safety_action"]))

    cases.append(("yellow_keeps_loading", yellow, _response(yellow, "suggest_session", "yellow"),
                  False, ["safety_action"]))

    plan = _response(weekly, "draft_plan")
    plan["recommendations"][0]["changes_plan"] = True
    cases.append(("unconfirmed_plan_change", weekly, plan, False, ["confirmation"]))

    bad = _response(daily)
    bad["observations"][0]["evidence"] = [{"source": "coach_context", "pointer": "/views/nope/0",
                                           "layer": "raw_signal", "observed_value": 1}]
    cases.append(("wrong_pointer", daily, bad, False, ["pointer"]))

    tampered = copy.deepcopy(daily)
    tampered["query"] = "被改过的问题"
    cases.append(("context_hash_changed", tampered, _response(daily), False, ["context_hash"]))
    return cases


def run_eval():
    results = []
    for name, context, response, expect_valid, expect_codes in _cases():
        outcome = validate_response(response, context)
        codes = sorted({item["code"] for item in outcome["errors"]})
        ok = outcome["valid"] == expect_valid and set(expect_codes) <= set(codes)
        results.append({"name": name, "passed": ok, "expected_valid": expect_valid,
                        "expected_codes": expect_codes, "valid": outcome["valid"], "codes": codes})
    passed = sum(1 for item in results if item["passed"])
    return {"passed": passed, "total": len(results), "cases": results}


def main():
    result = run_eval()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
