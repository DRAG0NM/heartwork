#!/usr/bin/env python3
"""One facade for CLI, MCP and web: every function returns JSON-serializable data."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import baseline
import coach_decisions
import coach_eval
import coach_events
import coach_policy
import coach_runtime
import store
from build_report import render_report
from coach_core import build_context
from core_metrics import acwr_uncoupled, banister_series
from state_layers import multidomain_state, raw_signal_snapshot

PROFILE_FIELDS = ("hr_max", "hr_rest", "hr_zones", "goal", "notes")


def status(base=None):
    result = store.status(base)
    result["events"] = len(coach_events.active_events(base))
    result["pending_suggestions"] = len(coach_decisions.pending(base))
    result["autonomy"] = coach_policy.get_level(base)
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
    state = multidomain_state(daily, week["mon"], week["as_of"], payload["load_history"])
    cutoff = (datetime.strptime(week["as_of"], "%Y%m%d") - timedelta(days=13)).strftime("%Y%m%d")
    raw_signals = raw_signal_snapshot(daily, week["mon"], week["as_of"])
    for card in raw_signals:
        card.pop("pointer", None)
        card["source"], card["date"] = "daily.json", card["latest_date"]
    return {
        "week": week,
        "raw_signals": raw_signals,
        "state": state,
        "safety_gate": context["safety_gate"],
        "recent_events": context["recent_events"],
        "daily": [row for row in daily if row["date"] >= cutoff],
        "activities": payload["activities"],
        "plan": payload["current_plan"],
        "status": status(base),
    }


def daily_view(base=None, as_of=None, days=56):
    """Per-day metric status (rolling baseline), family status, load and activities."""
    as_of = store.compact_date(as_of) if as_of else (
        store.latest_date(base) or datetime.now().strftime("%Y%m%d"))
    last = datetime.strptime(as_of, "%Y%m%d")
    first = (last - timedelta(days=days - 1)).strftime("%Y%m%d")
    details = store.load_details(base)
    activities, extras, _ = details
    rows, loads = store.daily_rows(base, (last - timedelta(
        days=days + baseline.BASELINE_DAYS)).strftime("%Y%m%d"), as_of, details)
    index = baseline.index_rows(rows)
    series = {day: (atl, ctl, tsb) for day, atl, ctl, tsb in banister_series(loads, as_of)}
    events = coach_events.active_events(base)
    out = []
    for offset in range(days):
        date = (last - timedelta(days=days - 1 - offset)).strftime("%Y%m%d")
        statuses = {}
        metrics = {}
        for key in baseline.METRICS:
            status = baseline.metric_status(None, key, date, _rows=index)
            if status is not None:
                statuses[key] = status
                metrics[key] = status
            elif isinstance(index.get(date, {}).get(key), (int, float)):
                metrics[key] = {"value": index[date][key]}
        day_acts = [item for item in activities + extras if item["date"] == date]
        method = next((item["load_method"] for item in day_acts if item.get("load_method")), None)
        if method is None and loads.get(date):
            method = "device"
        atl, ctl, tsb = [round(x, 2) for x in series[date]] if date in series else (None,) * 3
        out.append({
            "date": date, "metrics": metrics, "families": baseline.family_status(statuses),
            "load": {"value": loads.get(date), "method": method,
                     "atl": atl, "ctl": ctl, "tsb": tsb},
            "activities": day_acts,
            "events": [event for event in events if event.get("date") == date]})
    summary = banister_series(loads, as_of)
    load_summary = {"as_of": as_of, "acwr_uncoupled": acwr_uncoupled(loads, as_of),
                    "atl": None, "ctl": None, "tsb": None}
    if summary:
        load_summary.update(zip(("atl", "ctl", "tsb"), [round(x, 2) for x in summary[-1][1:]]))
    return {"as_of": as_of, "first": first, "days": out,
            "families": {name: {"label": baseline.FAMILY_LABELS[name], "keys": keys}
                         for name, keys in baseline.FAMILIES.items()},
            "load_summary": load_summary}


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


def get_autonomy(base=None):
    level = coach_policy.get_level(base)
    return {"level": level, "label": coach_policy.LEVEL_LABELS[level],
            "levels": [{"level": name, "label": coach_policy.LEVEL_LABELS[name]}
                       for name in coach_policy.LEVELS],
            "rules": coach_policy.rules(level)}


def set_autonomy(base, level, confirm=False):
    """Human surfaces only. Raising to autonomous needs confirm=True; lowering never does."""
    store.init(base)
    change = coach_policy.set_level(base, level, confirm)
    if change["from"] != change["to"]:
        coach_decisions.record_autonomy(base, change)
    return get_autonomy(base)


def undo(base, key):
    plan = coach_decisions.undo(base, key, actor="human")
    return {"key": key, "plan": plan.get("current_plan", "")}


def decisions(base=None):
    return {"suggestions": coach_decisions.all_suggestions(base),
            "log": coach_decisions.records(base)}


def provenance(base, date, metric=None):
    return store.provenance(base, date, metric)


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
