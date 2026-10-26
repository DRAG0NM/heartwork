#!/usr/bin/env python3
"""One facade for CLI, MCP and web: every function returns JSON-serializable data."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import coach_decisions
import coach_eval
import coach_events
import coach_runtime
import store
from build_report import render_report
from coach_core import build_context
from state_layers import multidomain_state, raw_signal_snapshot

PROFILE_FIELDS = ("hr_max", "hr_rest", "hr_zones", "goal", "notes")


def status(base=None):
    result = store.status(base)
    result["events"] = len(coach_events.active_events(base))
    result["pending_suggestions"] = len(coach_decisions.pending(base))
    return result


def init(base=None, demo=False):
    root = store.init(base, demo)
    return {"home": root, "demo": bool(demo), "status": status(root)}


def import_paths(base, paths):
    import importers
    store.init(base)
    return importers.import_paths(base, list(paths))


def log_event(base, event):
    store.init(base)
    return coach_events.log_event(base, event)


def weekly_report(base, as_of=None):
    payload = store.weekly_input(base, as_of)
    markdown = render_report(payload)
    week = payload["week"]
    folder = os.path.join(store.home(base), "reports")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "周报告_%s-%s.md" % (week["mon"], week["sun"]))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown)
    return {"markdown": markdown, "path": path}


def snapshot(base=None, as_of=None):
    payload = store.weekly_input(base, as_of)
    week = payload["week"]
    daily = sorted(payload["history_daily"] + payload["daily"], key=lambda row: row["date"])
    context = build_context(payload, "weekly_review")
    state = multidomain_state(daily, week["mon"], week["as_of"])
    cutoff = (datetime.strptime(week["as_of"], "%Y%m%d") - timedelta(days=13)).strftime("%Y%m%d")
    return {
        "week": week,
        "raw_signals": raw_signal_snapshot(daily, week["mon"], week["as_of"]),
        "state": state,
        "safety_gate": context["safety_gate"],
        "recent_events": context["recent_events"],
        "daily": [row for row in daily if row["date"] >= cutoff],
        "activities": payload["activities"],
        "plan": payload["current_plan"],
        "status": status(base),
    }


def start_job(base, query, task=None, as_of=None):
    store.init(base)
    return coach_runtime.start_job(base, query, task, as_of)


def complete_job(base, job_id, response):
    return coach_runtime.complete_job(base, job_id, response)


def list_jobs(base=None):
    return coach_runtime.list_jobs(base)


def pending_suggestions(base=None):
    return coach_decisions.pending(base)


def decide(base, key, accept, note=""):
    return coach_decisions.decide(base, key, accept, note, actor="human")


def apply_plan(base, key, new_plan_text, confirm):
    return coach_decisions.apply_plan(base, key, new_plan_text, confirm)


def run_eval():
    return coach_eval.run_eval()


def get_profile(base=None):
    profile = store.load_profile(base)
    return {key: profile.get(key) for key in PROFILE_FIELDS}


def set_profile(base, fields):
    unknown = sorted(set(fields) - set(PROFILE_FIELDS))
    if unknown:
        raise ValueError("unsupported profile fields: %s" % ", ".join(unknown))
    store.init(base)
    profile = store.load_profile(base)
    for key, value in fields.items():
        if key in ("hr_max", "hr_rest"):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not 30 <= value <= 250):
                raise ValueError("%s must be a number between 30 and 250" % key)
        elif key == "hr_zones":
            if value is not None and (not isinstance(value, list) or not all(
                    isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)):
                raise ValueError("hr_zones must be a list of numbers")
        elif not isinstance(value, str):
            raise ValueError("%s must be text" % key)
        profile[key] = value
    store.write_json(os.path.join(store.home(base), "profile.json"), profile)
    return get_profile(base)
