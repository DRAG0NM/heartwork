#!/usr/bin/env python3
"""Small, model-neutral coach harness with deterministic safety and evidence gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timedelta

import baseline
import coach_policy
from state_layers import multidomain_state, raw_signal_snapshot


TASK_ACTIONS = {
    "daily_guidance": {"ask", "maintain", "reduce", "rest", "suggest_session",
                       "seek_professional_help", "seek_emergency_help"},
    "post_workout": {"ask", "maintain", "reduce", "rest", "protective_activity",
                     "seek_professional_help", "seek_emergency_help"},
    "weekly_review": {"ask", "maintain", "reduce", "rest", "draft_plan",
                      "seek_professional_help", "seek_emergency_help"},
    "pain_followup": {"ask", "rest", "protective_activity", "seek_professional_help",
                      "seek_emergency_help"},
}
TOKEN_BUDGETS = {"daily_guidance": 4000, "post_workout": 4000,
                 "weekly_review": 6000, "pain_followup": 3500}
RED_FLAGS = {"chest_pain", "palpitations", "fainting", "joint_deformity",
             "cannot_bear_weight", "severe_breathing_difficulty"}
LEVEL = {"green": 0, "yellow": 1, "red": 2}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _compact_date(value):
    return datetime.strptime(value.replace("-", ""), "%Y%m%d").strftime("%Y%m%d")


def _recent_events(events, as_of, days=14):
    start = (datetime.strptime(as_of, "%Y%m%d") - timedelta(days=days - 1)).strftime("%Y%m%d")
    return [event for event in events if isinstance(event, dict)
            and start <= str(event.get("date", "")).replace("-", "") <= as_of]


def safety_gate(events, as_of):
    recent = _recent_events(events, as_of)
    red = []
    yellow = []
    for index, event in enumerate(recent):
        flags = set(event.get("red_flags") or [])
        matched = sorted(flags.intersection(RED_FLAGS))
        if matched:
            red.append({"event_index": index, "signals": matched})
        pain = event.get("pain")
        if isinstance(pain, (int, float)) and pain > 0:
            yellow.append({"event_index": index, "signal": "pain", "value": pain})
        if event.get("illness"):
            yellow.append({"event_index": index, "signal": "illness", "value": True})
        if event.get("function_limited") or event.get("worsening"):
            yellow.append({"event_index": index, "signal": "function_or_trend", "value": True})
    if red:
        return {"level": "red", "reasons": red, "training_actions_blocked": True,
                "required_action_types": ["seek_emergency_help"]}
    if yellow:
        return {"level": "yellow", "reasons": yellow, "training_actions_blocked": True,
                "required_action_types": ["protective_activity", "reduce", "rest",
                                          "seek_professional_help"]}
    return {"level": "green", "reasons": [], "training_actions_blocked": False,
            "required_action_types": []}


BASELINE_FIELDS = ("key", "label", "unit", "recent_mean", "center", "band", "swc", "z",
                   "status", "oriented", "n_recent", "n_baseline")


def _baseline_status(daily, as_of):
    return [{key: item[key] for key in BASELINE_FIELDS}
            for item in baseline.day_status(daily, as_of).values()]


def build_context(payload, task, query="", level=None):
    if task not in TASK_ACTIONS:
        raise ValueError("unknown task: %s" % task)
    level = level or coach_policy.DEFAULT_LEVEL
    week = payload.get("week") or {}
    mon = _compact_date(week["mon"])
    as_of = _compact_date(week["as_of"])
    daily = list(payload.get("history_daily") or []) + list(payload.get("daily") or [])
    daily = sorted(daily, key=lambda row: row.get("date", ""))
    events = _recent_events(payload.get("subjective_events") or [], as_of)
    state = multidomain_state(daily, mon, as_of, payload.get("load_history"))
    if task != "weekly_review":
        state = {
            "kind": state["kind"], "schema_version": state["schema_version"],
            "status": state["status"], "as_of": state["as_of"], "anchor": state["anchor"],
            "axes": [{key: axis.get(key) for key in
                      ("id", "label", "available", "index", "reliability", "meaning", "reason")
                      if key in axis} for axis in state["axes"]],
            "change_points": {"candidate_count": state["change_points"]["candidate_count"]},
            "warnings": state["warnings"],
        }
    recent_days = 14 if task == "weekly_review" else 7
    context = {
        "kind": "coach_context", "schema_version": 2, "task": task, "as_of": as_of,
        "query": query,
        "policy": coach_policy.rules(level, task),
        "views": {
            "raw_signals": raw_signal_snapshot(daily, mon, as_of),
            "state": state,
            "baseline_status": _baseline_status(daily, as_of),
            "recent_daily": daily[-recent_days:],
            "activities": payload.get("activities") or [],
            "current_plan": payload.get("current_plan"),
        },
        "recent_events": events,
        "safety_gate": safety_gate(events, as_of),
        "data_status": {"partial_week": bool(week.get("partial") or as_of < _compact_date(week["sun"])),
                        "missing_inputs": []},
    }
    if not events:
        context["data_status"]["missing_inputs"].append("subjective_events")
    unsigned = dict(context)
    context["content_sha256"] = stable_hash(unsigned)
    characters = len(_canonical(context))
    budget = TOKEN_BUDGETS[task]
    context["context_metrics"] = {"characters": characters,
                                  "approx_tokens": (characters + 1) // 2,
                                  "budget_tokens": budget,
                                  "within_budget": (characters + 1) // 2 <= budget}
    return context


def resolve_pointer(document, pointer):
    if pointer == "":
        return document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("pointer must be a JSON Pointer")
    current = document
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise ValueError("pointer traverses a scalar")
    return current


def _same(first, second):
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return abs(float(first) - float(second)) <= 1e-9
    return first == second


def _expected_layer(pointer):
    if pointer.startswith("/views/raw_signals") or pointer.startswith("/views/recent_daily"):
        return "raw_signal"
    if pointer.startswith("/views/state"):
        return "experimental_model"
    if pointer.startswith("/recent_events"):
        return "user_event"
    if pointer.startswith("/safety_gate"):
        return "deterministic_metric"
    if pointer.startswith("/views/current_plan"):
        return "plan"
    if pointer.startswith("/views/baseline_status"):
        return "deterministic_metric"
    return None


def _evidence_errors(item, path, context, require_value):
    errors = []
    if not isinstance(item, dict):
        return [{"code": "evidence", "path": path, "message": "evidence must be an object"}]
    if item.get("source") != "coach_context":
        errors.append({"code": "source", "path": path + ".source",
                       "message": "source must be coach_context"})
    pointer = item.get("pointer")
    if not isinstance(pointer, str):
        errors.append({"code": "pointer", "path": path + ".pointer",
                       "message": "pointer is required"})
        return errors
    expected = _expected_layer(pointer)
    if expected and item.get("layer") != expected:
        errors.append({"code": "layer", "path": path + ".layer",
                       "message": "expected layer %s" % expected})
    try:
        actual = resolve_pointer(context, pointer)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        errors.append({"code": "pointer", "path": path + ".pointer", "message": str(exc)})
        return errors
    if require_value and "observed_value" not in item:
        errors.append({"code": "observed_value", "path": path + ".observed_value",
                       "message": "observed_value is required"})
    elif "observed_value" in item and not _same(item["observed_value"], actual):
        errors.append({"code": "value_mismatch", "path": path + ".observed_value",
                       "message": "observed_value does not match the pointer"})
    return errors


DIRECTION_WORDS = (
    ("up", re.compile(r"偏高|升高|上升|高于|增加|\b(?:higher|increas\w*|above)\b", re.I)),
    ("down", re.compile(r"偏低|降低|下降|低于|减少|\b(?:lower|decreas\w*|below|declin\w*)\b", re.I)),
    ("better", re.compile(r"改善|变好|\bimprov\w*", re.I)),
    ("worse", re.compile(r"变差|恶化|\bworse\w*", re.I)),
    ("within", re.compile(r"正常范围|接近常态|持平|\b(?:within|normal)\b", re.I)),
)
NEGATIONS = ("不", "没", "无", "未", "非", "not ", "no ")
DIRECTIONS = ("above", "below", "within")


def _statement_classes(text):
    found = set()
    for name, pattern in DIRECTION_WORDS:
        for match in pattern.finditer(text):
            if not any(mark in text[max(0, match.start() - 4):match.start()].lower()
                       for mark in NEGATIONS):
                found.add(name)
                break
    return found


def _evidence_metrics(context, evidence, known):
    keys = set()
    for item in evidence:
        parts = str(item.get("pointer", "")).split("/") if isinstance(item, dict) else []
        if len(parts) < 5 or parts[1] != "views":
            continue
        try:
            if parts[2] in ("raw_signals", "baseline_status"):
                key = resolve_pointer(context, "/".join(parts[:4]))["key"]
            elif parts[2] == "recent_daily":
                key = parts[4]
            else:
                continue
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if key in known:
            keys.add(key)
    return keys


def _claim_consistent(name, claim, polarity):
    direction = claim["direction"]
    if name == "up":
        return direction == "above"
    if name == "down":
        return direction == "below"
    if name == "within":
        return direction == "within"
    if direction == "within" or not polarity:
        return False
    improved = (direction == "above") == (polarity > 0)
    return improved if name == "better" else not improved


def _claim_errors(observation, path, context):
    errors = []
    status = {item.get("key"): item.get("status") for item in
              (context.get("views") or {}).get("baseline_status") or [] if isinstance(item, dict)}
    claims = observation.get("claims")
    if claims is not None and not isinstance(claims, list):
        return [{"code": "claim_mismatch", "path": path + ".claims", "message": "claims must be a list"}]
    valid = []
    for index, claim in enumerate(claims or []):
        where = "%s.claims[%d]" % (path, index)
        if not isinstance(claim, dict) or claim.get("direction") not in DIRECTIONS:
            errors.append({"code": "claim_mismatch", "path": where,
                           "message": "claim needs metric and direction above/below/within"})
        elif claim.get("metric") not in status:
            errors.append({"code": "claim_mismatch", "path": where + ".metric",
                           "message": "metric is not in views.baseline_status"})
        elif claim["direction"] != status[claim["metric"]]:
            errors.append({"code": "claim_mismatch", "path": where + ".direction",
                           "message": "baseline_status says %s for %s" %
                                      (status[claim["metric"]], claim["metric"])})
        else:
            valid.append(claim)
    classes = _statement_classes(str(observation.get("statement") or ""))
    if not classes or errors:
        return errors
    evidence = observation.get("evidence") if isinstance(observation.get("evidence"), list) else []
    if not claims:
        if _evidence_metrics(context, evidence, set(status)):
            errors.append({"code": "claim_required", "path": path,
                           "message": "statement states a direction; add claims for the metric"})
        return errors
    for name in sorted(classes):
        if not any(_claim_consistent(name, claim, baseline.METRICS.get(claim["metric"], {})
                                     .get("polarity", 0)) for claim in valid):
            errors.append({"code": "claim_text_conflict", "path": path + ".statement",
                           "message": "statement direction (%s) contradicts claims" % name})
    return errors


# Numbers that count things or name dates are not measurements; everything else in a
# statement must come from the cited evidence (or its sibling fields in the same record).
NUMBER = re.compile(r"(?<![A-Za-z0-9_.\u2080-\u2089])(\d+(?:\.\d+)?)(?![\d.]*\d)"
                    r"(?!\s*(?:年|月|日|号|天|周|次|个|项|条|组|小时|晚|days?\b|weeks?\b))")
MISSING_WORDS = re.compile(r"缺失|缺少|没有数据|无数据|未记录|未提供|未设置|空缺|\bmissing\b|\bno data\b", re.I)
CONVERSIONS = (1.0, 1 / 60.0, 1 / 1000.0, 1 / 3600.0, 100.0)


def _numbers(value, out, depth=2):
    if isinstance(value, bool) or value is None or depth < 0:
        return out
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, list):
        for item in value:
            _numbers(item, out, depth)
    elif isinstance(value, dict):
        for item in value.values():
            _numbers(item, out, depth - 1)
    return out


def _empty(value):
    return value is None or value == [] or value == "" or value == {}


def _cited(context, evidence):
    """(actual value, parent record) for every evidence item whose pointer resolves."""
    found = []
    for item in evidence:
        pointer = item.get("pointer") if isinstance(item, dict) else None
        if not isinstance(pointer, str):
            continue
        try:
            actual = resolve_pointer(context, pointer)
            parent = resolve_pointer(context, pointer.rsplit("/", 1)[0])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        found.append((pointer, actual, parent))
    return found


def _supported(number, text, values):
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    tolerance = 0.5 * 10 ** -decimals + 1e-9
    return any(abs(abs(value) * factor - number) <= tolerance
               for value in values for factor in CONVERSIONS)


def _locate(context, number, text, limit=2):
    """JSON Pointers in the context whose value matches number (for the repair hint)."""
    hits = []

    def walk(value, pointer):
        if len(hits) >= limit:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key not in ("content_sha256", "context_metrics", "policy"):
                    walk(item, "%s/%s" % (pointer, str(key).replace("~", "~0").replace("/", "~1")))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, "%s/%d" % (pointer, index))
        elif not isinstance(value, bool) and isinstance(value, (int, float)) and                 _supported(number, text, [float(value)]):
            hits.append(pointer)

    walk(context, "")
    return hits


def _text_errors(observation, path, context):
    """Statement vs evidence: unsupported numbers and "missing" said about present data."""
    statement = str(observation.get("statement") or "")
    cited = _cited(context, observation.get("evidence") or [])
    errors = []
    values = []
    for pointer, actual, parent in cited:
        _numbers(actual, values)
        if pointer.count("/") > 2:  # sibling fields of a record, never a whole section
            _numbers(parent, values)
    for match in NUMBER.finditer(statement):
        text = match.group(1)
        digits = text.replace(".", "")
        if len(digits) >= 8 and "." not in text:
            continue  # compact date such as 20260810
        if not _supported(float(text), text, values):
            where = _locate(context, float(text), text)
            errors.append({"code": "number_unsupported", "path": path + ".statement",
                           "message": "number %s is not in the cited evidence; %s" % (
                               text, "add evidence with pointer %s" % " or ".join(where) if where
                               else "no such value in coach_context, drop it")})
    for match in MISSING_WORDS.finditer(statement):
        if any(mark in statement[max(0, match.start() - 2):match.start()] for mark in NEGATIONS):
            continue
        if not any(_empty(actual) or pointer.startswith("/data_status") for pointer, actual, _ in cited):
            errors.append({"code": "missing_conflict", "path": path + ".statement",
                           "message": "statement says data is missing but every cited value is present; "
                                      "cite the empty field or /data_status, or correct the statement"})
        break
    return errors


def validate_response(response, context):
    errors = []
    unsigned_context = {key: value for key, value in context.items()
                        if key not in {"content_sha256", "context_metrics"}}
    if stable_hash(unsigned_context) != context.get("content_sha256"):
        errors.append({"code": "context_hash", "path": "$context.content_sha256",
                       "message": "context content hash does not match"})
    if response.get("kind") != "coach_response" or response.get("schema_version") != 2:
        errors.append({"code": "contract", "path": "$", "message": "Response v2 is required"})
    if response.get("task") != context.get("task") or response.get("as_of") != context.get("as_of"):
        errors.append({"code": "context", "path": "$", "message": "task/as_of mismatch"})
    observations = response.get("observations") if isinstance(response.get("observations"), list) else []
    recommendations = response.get("recommendations") if isinstance(response.get("recommendations"), list) else []
    risks = response.get("risks") if isinstance(response.get("risks"), list) else []
    for index, observation in enumerate(observations):
        evidence = observation.get("evidence") if isinstance(observation, dict) else None
        if not evidence:
            errors.append({"code": "evidence", "path": "$.observations[%d]" % index,
                           "message": "observation needs evidence"})
            continue
        layers = {item.get("layer") for item in evidence if isinstance(item, dict)}
        for evidence_index, item in enumerate(evidence):
            errors.extend(_evidence_errors(item, "$.observations[%d].evidence[%d]" %
                                           (index, evidence_index), context, True))
        errors.extend(_claim_errors(observation, "$.observations[%d]" % index, context))
        errors.extend(_text_errors(observation, "$.observations[%d]" % index, context))
        if observation.get("confidence") == "high" and "experimental_model" in layers:
            errors.append({"code": "experimental_confidence",
                           "path": "$.observations[%d].confidence" % index,
                           "message": "experimental evidence cannot be high confidence"})
    policy = context.get("policy") or {}
    rule = coach_policy.rules(policy.get("level") if policy.get("level") in coach_policy.LEVELS
                              else coach_policy.DEFAULT_LEVEL)
    allowed = set(policy.get("allowed_action_types") or TASK_ACTIONS.get(context.get("task"), set()))
    cap = coach_policy.CONFIDENCE_RANK[rule["max_recommendation_confidence"]]
    for index, recommendation in enumerate(recommendations):
        path = "$.recommendations[%d]" % index
        if not isinstance(recommendation, dict):
            errors.append({"code": "evidence", "path": path, "message": "recommendation must be an object"})
            continue
        action = recommendation.get("action_type")
        if action not in allowed:
            errors.append({"code": "permission", "path": path + ".action_type",
                           "message": "action is not allowed for this task and autonomy level"})
        if recommendation.get("changes_plan"):
            if rule["plan_change"] == "forbidden":
                errors.append({"code": "permission", "path": path + ".changes_plan",
                               "message": "conservative level forbids plan-change suggestions"})
            elif not recommendation.get("requires_confirmation"):
                text = recommendation.get("plan_text")
                if rule["plan_change"] != "auto_if_guardrails":
                    errors.append({"code": "confirmation", "path": path,
                                   "message": "plan changes require confirmation"})
                elif not isinstance(text, str) or not text.strip() or len(text) > coach_policy.PLAN_TEXT_LIMIT:
                    errors.append({"code": "plan_text", "path": path + ".plan_text",
                                   "message": "unconfirmed plan change needs plan_text (max %d chars)" %
                                              coach_policy.PLAN_TEXT_LIMIT})
        if coach_policy.CONFIDENCE_RANK.get(recommendation.get("confidence"), 0) > cap:
            errors.append({"code": "confidence_cap", "path": path + ".confidence",
                           "message": "confidence exceeds the cap of this autonomy level"})
        evidence = recommendation.get("evidence") or []
        if not evidence:
            errors.append({"code": "evidence", "path": path, "message": "recommendation needs evidence"})
        layers = {item.get("layer") for item in evidence if isinstance(item, dict)}
        for evidence_index, item in enumerate(evidence):
            errors.extend(_evidence_errors(item, path + ".evidence[%d]" % evidence_index,
                                           context, False))
        if rule["raw_signal_required"] == "always" and "raw_signal" not in layers:
            errors.append({"code": "raw_signal_required", "path": path,
                           "message": "this autonomy level requires raw_signal evidence"})
        if rule["experimental_evidence"] == "forbidden" and "experimental_model" in layers:
            errors.append({"code": "experimental_evidence", "path": path,
                           "message": "this autonomy level forbids experimental-layer evidence"})
        if layers and layers <= {"experimental_model"} and action not in {"ask", "maintain"}:
            errors.append({"code": "experimental_only", "path": path,
                           "message": "experimental-only evidence cannot prescribe"})
    gate = (context.get("safety_gate") or {}).get("level", "green")
    response_level = max((LEVEL.get(item.get("level"), -1) for item in risks
                          if isinstance(item, dict)), default=0)
    if response_level < LEVEL.get(gate, 0):
        errors.append({"code": "safety_gate", "path": "$.risks",
                       "message": "response cannot downgrade the deterministic safety gate"})
    actions = {item.get("action_type") for item in recommendations if isinstance(item, dict)}
    if gate == "red":
        if "seek_emergency_help" not in actions or actions - {"seek_emergency_help"}:
            errors.append({"code": "safety_action", "path": "$.recommendations",
                           "message": "red gate requires emergency escalation and blocks training actions"})
    elif gate == "yellow" and actions.intersection({"maintain", "suggest_session", "draft_plan"}):
        errors.append({"code": "safety_action", "path": "$.recommendations",
                       "message": "yellow gate blocks normal training progression"})
    return {"valid": not errors, "errors": errors,
            "receipt": {"context_sha256": context.get("content_sha256"),
                        "response_sha256": stable_hash(response)}}


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write(path, value):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Model-neutral coach harness demo")
    commands = parser.add_subparsers(dest="command", required=True)
    context = commands.add_parser("context")
    context.add_argument("input")
    context.add_argument("--task", required=True, choices=sorted(TASK_ACTIONS))
    context.add_argument("--query", default="")
    context.add_argument("--output", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("response")
    validate.add_argument("context")
    args = parser.parse_args(argv)
    if args.command == "context":
        result = build_context(_read(args.input), args.task, args.query)
        _write(args.output, result)
        print(json.dumps({"ok": True, "output": args.output,
                          "context_metrics": result["context_metrics"]}, ensure_ascii=False))
        return 0
    result = validate_response(_read(args.response), _read(args.context))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
