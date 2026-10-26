#!/usr/bin/env python3
"""Pending coach suggestions and human-only decisions (decisions.jsonl).

A model can register suggestions but never accept them; changing the plan needs
an accepted suggestion plus an explicit ``confirm=True`` from a human surface.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import coach_policy
import store
from coach_core import stable_hash

AUTO_ACTOR = "coach_auto"


def _file(base):
    return os.path.join(store.home(base), "decisions.jsonl")


def _append(base, record):
    record.setdefault("at", datetime.now().isoformat(timespec="seconds"))
    os.makedirs(store.home(base), exist_ok=True)
    with open(_file(base), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _records(base):
    try:
        with open(_file(base), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        return []


def records(base=None, limit=200):
    """Raw decision log, newest first (includes auto-applied plans and autonomy changes)."""
    return list(reversed(_records(base)))[:limit]


def all_suggestions(base=None, now=None):
    """Fold the log into one record per suggestion with its latest status."""
    items = {}
    for record in _records(base):
        key = record.get("key")
        if record.get("event") == "register":
            items.setdefault(key, {"key": key, "job_id": record["job_id"],
                                   "response_sha256": record["response_sha256"],
                                   "recommendation": record["recommendation"],
                                   "status": "pending", "created_at": record["at"],
                                   "note": "", "auto_blocked": record.get("auto_blocked") or []})
        elif key in items:
            if record.get("event") == "decide":
                items[key]["status"] = "accepted" if record["accept"] else "rejected"
                items[key]["note"] = record.get("note", "")
            elif record.get("event") == "apply":
                items[key]["status"] = "applied"
            elif record.get("event") == "auto_apply":
                items[key]["status"] = "auto_applied"
                items[key]["auto"] = {"actor": record.get("actor"), "applied_at": record["at"],
                                      "old_plan": record.get("old_plan", ""),
                                      "plan_text": record.get("new_plan", ""),
                                      "undo_until": record.get("undo_until")}
            elif record.get("event") == "undo":
                items[key]["status"] = "undone"
    now = (now or datetime.now()).isoformat(timespec="seconds")
    for item in items.values():
        if "auto" in item:
            item["auto"]["can_undo"] = item["status"] == "auto_applied" and now <= item["auto"]["undo_until"]
    return list(items.values())


def register(base, job_id, response_sha256, recommendation, auto_blocked=None):
    rec_id = recommendation.get("id") if isinstance(recommendation, dict) else None
    key = stable_hash([response_sha256, rec_id, recommendation])[:10]
    for item in all_suggestions(base):
        if item["key"] == key:
            return item
    record = {"event": "register", "key": key, "job_id": job_id,
              "response_sha256": response_sha256, "recommendation": recommendation}
    if auto_blocked:
        record["auto_blocked"] = list(auto_blocked)
    _append(base, record)
    return next(item for item in all_suggestions(base) if item["key"] == key)


def pending(base=None):
    return [item for item in all_suggestions(base) if item["status"] == "pending"]


def _get(base, key):
    for item in all_suggestions(base):
        if item["key"] == key:
            return item
    raise KeyError("unknown suggestion key: %s" % key)


def decide(base, key, accept, note="", actor="human"):
    if actor != "human":
        raise PermissionError("only a human can accept or reject a suggestion")
    item = _get(base, key)
    if item["status"] != "pending":
        raise ValueError("suggestion already %s" % item["status"])
    _append(base, {"event": "decide", "key": key, "accept": bool(accept),
                   "note": note or "", "actor": "human"})
    return _get(base, key)


def apply_plan(base, key, new_plan_text, confirm):
    item = _get(base, key)
    if item["status"] != "accepted" or confirm is not True:
        raise PermissionError("plan changes need an accepted suggestion and confirm=True")
    if not isinstance(new_plan_text, str) or not new_plan_text.strip():
        raise ValueError("new plan text is required")
    plan = store.save_plan(base, new_plan_text, "suggestion %s" % key)
    _append(base, {"event": "apply", "key": key})
    return plan


def rate_blockers(base, now=None):
    now = now or datetime.now()
    since = (now - timedelta(hours=coach_policy.AUTO_WINDOW_HOURS)).isoformat(timespec="seconds")
    if any(r.get("event") == "auto_apply" and r.get("at", "") >= since for r in _records(base)):
        return ["rate_limited"]
    return []


def auto_blockers(base, context, recommendation, now=None):
    """Every autonomous-level guardrail for one unconfirmed plan change; empty means it may apply."""
    return coach_policy.static_blockers(context, recommendation) + rate_blockers(base, now)


def auto_apply(base, key, plan_text, now=None):
    """Apply a pending suggestion on the coach's own authority; call only after auto_blockers is empty."""
    item = _get(base, key)
    if item["status"] != "pending":
        raise ValueError("suggestion already %s" % item["status"])
    now = now or datetime.now()
    old = store.load_plan(base).get("current_plan") or ""
    store.save_plan(base, plan_text, "auto %s" % key)
    _append(base, {"event": "auto_apply", "key": key, "actor": AUTO_ACTOR, "old_plan": old,
                   "new_plan": plan_text, "at": now.isoformat(timespec="seconds"),
                   "undo_until": (now + timedelta(days=coach_policy.UNDO_DAYS)).isoformat(timespec="seconds")})
    return _get(base, key)


def undo(base, key, actor="human", now=None):
    """Restore the plan that an auto-applied suggestion replaced. Humans only, within 7 days."""
    if actor != "human":
        raise PermissionError("only a human can undo an automatic plan change")
    item = _get(base, key)
    if item["status"] != "auto_applied":
        raise ValueError("suggestion is %s, nothing to undo" % item["status"])
    if (now or datetime.now()).isoformat(timespec="seconds") > item["auto"]["undo_until"]:
        raise ValueError("撤销期（7 天）已过")
    plan = store.save_plan(base, item["auto"]["old_plan"], "undo %s" % key)
    _append(base, {"event": "undo", "key": key, "actor": "human"})
    return plan


def record_autonomy(base, change):
    _append(base, {"event": "autonomy", "from": change["from"], "to": change["to"], "actor": "human"})
