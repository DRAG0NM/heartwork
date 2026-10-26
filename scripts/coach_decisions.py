#!/usr/bin/env python3
"""Pending coach suggestions and human-only decisions (decisions.jsonl).

A model can register suggestions but never accept them; changing the plan needs
an accepted suggestion plus an explicit ``confirm=True`` from a human surface.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import store
from coach_core import stable_hash


def _file(base):
    return os.path.join(store.home(base), "decisions.jsonl")


def _append(base, record):
    record["at"] = datetime.now().isoformat(timespec="seconds")
    os.makedirs(store.home(base), exist_ok=True)
    with open(_file(base), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _records(base):
    try:
        with open(_file(base), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        return []


def all_suggestions(base=None):
    """Fold the log into one record per suggestion with its latest status."""
    items = {}
    for record in _records(base):
        key = record.get("key")
        if record.get("event") == "register":
            items.setdefault(key, {"key": key, "job_id": record["job_id"],
                                   "response_sha256": record["response_sha256"],
                                   "recommendation": record["recommendation"],
                                   "status": "pending", "created_at": record["at"],
                                   "note": ""})
        elif key in items:
            if record.get("event") == "decide":
                items[key]["status"] = "accepted" if record["accept"] else "rejected"
                items[key]["note"] = record.get("note", "")
            elif record.get("event") == "apply":
                items[key]["status"] = "applied"
    return list(items.values())


def register(base, job_id, response_sha256, recommendation):
    rec_id = recommendation.get("id") if isinstance(recommendation, dict) else None
    key = stable_hash([response_sha256, rec_id, recommendation])[:10]
    for item in all_suggestions(base):
        if item["key"] == key:
            return item
    _append(base, {"event": "register", "key": key, "job_id": job_id,
                   "response_sha256": response_sha256, "recommendation": recommendation})
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
