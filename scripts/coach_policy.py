#!/usr/bin/env python3
"""Coach autonomy levels: what the model may do, chosen by the human, never by the model.

The level is stored in the workspace ``settings.json`` and copied into every
job's ``context.policy`` (and so into the signed job). Raising it to
``autonomous`` needs an explicit confirmation; lowering it never does. The
deterministic safety gate, evidence checks and the human undo are not levels.
"""

from __future__ import annotations

import os

import store

LEVELS = ("conservative", "balanced", "autonomous")
DEFAULT_LEVEL = "balanced"
INCREASE_ACTIONS = {"suggest_session", "draft_plan"}
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
PLAN_TEXT_LIMIT = 2000
AUTO_WINDOW_HOURS = 72
UNDO_DAYS = 7
LEVEL_LABELS = {"conservative": "保守", "balanced": "标准", "autonomous": "激进"}
BLOCK_REASONS = {
    "safety_gate_not_green": "安全门不是绿灯",
    "no_raw_signal": "没有原始信号证据",
    "low_confidence": "置信度低于 medium",
    "no_plan_text": "缺少 plan_text（或超过 2000 字）",
    "rate_limited": "72 小时内已自动应用过一次",
    "autonomy_lowered": "当前档位已不是激进档",
}


def allowed_actions(level, task):
    from coach_core import TASK_ACTIONS
    actions = set(TASK_ACTIONS[task])
    if level == "conservative":
        actions -= INCREASE_ACTIONS
    elif level == "autonomous" and task in ("daily_guidance", "post_workout"):
        actions.add("draft_plan")
    return sorted(actions)


def rules(level=DEFAULT_LEVEL, task=None):
    """Policy block for a level; with ``task`` it also lists the allowed action types."""
    if level not in LEVELS:
        raise ValueError("level must be one of %s" % ", ".join(LEVELS))
    result = {
        "level": level,
        "medical_diagnosis": "forbidden",
        "plan_change": {"conservative": "forbidden", "balanced": "confirm",
                        "autonomous": "auto_if_guardrails"}[level],
        "plan_change_requires_confirmation": level != "autonomous",
        "experimental_only_prescription": "forbidden",
        "experimental_evidence": "forbidden" if level == "conservative" else "auxiliary_only",
        "max_recommendation_confidence": "medium" if level == "conservative" else "high",
        "raw_signal_required": {"conservative": "always", "balanced": "never",
                                "autonomous": "when_auto_applying"}[level],
    }
    if level == "autonomous":
        result["auto_apply"] = {"safety_gate": "green", "min_raw_signal_evidence": 1,
                                "min_confidence": "medium", "plan_text_max_chars": PLAN_TEXT_LIMIT,
                                "max_per_hours": AUTO_WINDOW_HOURS, "undo_days": UNDO_DAYS}
    if task is not None:
        result["allowed_action_types"] = allowed_actions(level, task)
    return result


def _settings_path(base):
    return os.path.join(store.home(base), "settings.json")


def get_level(base=None):
    level = (store.read_json(_settings_path(base), {}) or {}).get("coach_autonomy")
    return level if level in LEVELS else DEFAULT_LEVEL


def set_level(base, level, confirm=False):
    """Human surfaces only (CLI, web). Raising to autonomous needs confirm=True."""
    if level not in LEVELS:
        raise ValueError("level must be one of %s" % ", ".join(LEVELS))
    current = get_level(base)
    if level == "autonomous" and current != "autonomous" and confirm is not True:
        raise PermissionError("切换到激进档需要明确确认（confirm=True）")
    settings = store.read_json(_settings_path(base), {}) or {}
    settings["coach_autonomy"] = level
    store.write_json(_settings_path(base), settings)
    return {"from": current, "to": level}


def layers_of(item):
    return {entry.get("layer") for entry in (item.get("evidence") or []) if isinstance(entry, dict)}


def static_blockers(context, recommendation):
    """Guardrails that depend only on the context and the recommendation itself."""
    reasons = []
    if (context.get("safety_gate") or {}).get("level") != "green":
        reasons.append("safety_gate_not_green")
    if "raw_signal" not in layers_of(recommendation):
        reasons.append("no_raw_signal")
    if recommendation.get("confidence") not in ("medium", "high"):
        reasons.append("low_confidence")
    text = recommendation.get("plan_text")
    if not isinstance(text, str) or not text.strip() or len(text) > PLAN_TEXT_LIMIT:
        reasons.append("no_plan_text")
    return reasons
