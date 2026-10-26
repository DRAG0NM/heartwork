#!/usr/bin/env python3
"""Fixed synthetic replay cases: the hard gate for evidence and safety rules."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

import coach_decisions
import store
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


def _claim_case(context):
    """A raw-signal card whose metric has a non-within baseline status, plus the matching words."""
    cards = {card["key"]: index for index, card in enumerate(context["views"]["raw_signals"])}
    for item in context["views"]["baseline_status"]:
        if item["status"] != "within" and item["key"] in cards:
            return item, _evidence(context, "/views/raw_signals/%d/latest" % cards[item["key"]])
    raise RuntimeError("synthetic data has no off-baseline metric")


def _claim_response(context, statement, claims):
    response = _response(context)
    response["observations"][0].update(statement=statement, claims=claims)
    return response


def _plan_response(context, action="reduce", level="green", **rec):
    response = _response(context, action, level)
    response["recommendations"][0].update(
        changes_plan=True, requires_confirmation=False, plan_text="合成新计划", confidence="medium", **rec)
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

    item, evidence = _claim_case(daily)
    up = item["status"] == "above"
    right, wrong = ("above", "below") if up else ("below", "above")
    word, opposite = ("升高", "下降") if up else ("下降", "升高")
    cases.append(("claim_direction_reversed", daily, _claim_response(
        daily, "%s%s" % (item["label"], word), [{"metric": item["key"], "direction": wrong}]),
        False, ["claim_mismatch"]))
    cases.append(("direction_words_without_claims", daily, _claim_response(
        daily, "%s%s" % (item["label"], word), None), False, ["claim_required"]))
    cases.append(("claim_text_conflict", daily, _claim_response(
        daily, "%s%s" % (item["label"], opposite), [{"metric": item["key"], "direction": right}]),
        False, ["claim_text_conflict"]))
    cases.append(("claims_consistent", daily, _claim_response(
        daily, "%s%s" % (item["label"], word), [{"metric": item["key"], "direction": right}]),
        True, []))

    conservative = build_context(base, "weekly_review", level="conservative")
    plan = _response(conservative, "reduce")
    plan["recommendations"][0].update(changes_plan=True, requires_confirmation=True)
    cases.append(("conservative_plan_change_rejected", conservative, plan, False, ["permission"]))

    conservative_daily = build_context(base, "daily_guidance", level="conservative")
    state_ev = [_evidence(conservative_daily), _evidence(
        conservative_daily, "/views/state/axes/0/index", "experimental_model")]
    cases.append(("conservative_experimental_rejected", conservative_daily,
                  _response(conservative_daily, "reduce", evidence=state_ev), False,
                  ["experimental_evidence"]))

    auto_yellow = build_context(_with_event(base, pain=4), "daily_guidance", level="autonomous")
    cases.append(("autonomous_yellow_auto_apply_blocked", auto_yellow,
                  _plan_response(auto_yellow, "reduce", "yellow"), True, [], ["safety_gate_not_green"]))

    auto_green = build_context(base, "daily_guidance", level="autonomous")
    cases.append(("autonomous_rate_limited", auto_green, _plan_response(auto_green, "reduce"),
                  True, [], ["rate_limited"], True))

    auto_state = [_evidence(auto_green, "/views/state/axes/0/index", "experimental_model")]
    cases.append(("autonomous_experimental_only_increase_rejected", auto_green,
                  _response(auto_green, "suggest_session", evidence=auto_state), False,
                  ["experimental_only"]))
    return cases


def _blockers(context, response, seeded):
    recommendation = response["recommendations"][0]
    with tempfile.TemporaryDirectory() as tmp:
        store.init(tmp)
        if seeded:
            first = coach_decisions.register(tmp, "job_eval", "eval", {"id": "seed"})
            coach_decisions.auto_apply(tmp, first["key"], "合成旧计划",
                                       now=datetime.now() - timedelta(hours=10))
        return coach_decisions.auto_blockers(tmp, context, recommendation)


def run_eval():
    results = []
    for name, context, response, expect_valid, expect_codes, *extra in _cases():
        outcome = validate_response(response, context)
        codes = sorted({item["code"] for item in outcome["errors"]})
        ok = outcome["valid"] == expect_valid and set(expect_codes) <= set(codes)
        row = {"name": name, "passed": ok, "expected_valid": expect_valid,
               "expected_codes": expect_codes, "valid": outcome["valid"], "codes": codes}
        if extra:
            blockers = _blockers(context, response, len(extra) > 1)
            row.update(expected_blockers=extra[0], blockers=blockers)
            row["passed"] = ok and set(extra[0]) <= set(blockers)
        results.append(row)
    passed = sum(1 for item in results if item["passed"])
    return {"passed": passed, "total": len(results), "cases": results}


def main():
    result = run_eval()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
