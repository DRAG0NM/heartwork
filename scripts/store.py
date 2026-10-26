#!/usr/bin/env python3
"""Local workspace: the single place imported data, events, plans and jobs live.

The workspace sits outside the repository (default ``~/.sports-eval``, or
``SPORTS_EVAL_HOME``) so real data can never be committed by accident.
Everything downstream (reports, coach context, MCP, web) reads the standard
weekly input assembled by :func:`weekly_input`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from core_metrics import trimp_edwards


DAILY_KEYS = ("sleep_duration_min", "sleep_score", "sleep_efficiency", "hrv_rmssd",
              "resting_hr", "stress", "training_load", "vo2max", "running_ability",
              "subjective_recovery")
EXTRA_DAILY_KEYS = ("weight_kg", "steps")
SOURCE_PRIORITY = {"manual": 3, "device": 2, "import": 1, "demo": 0}
EVENT_FIELDS = ("date", "type", "pain", "illness", "function_limited", "worsening",
                "rpe", "subjective_recovery", "red_flags")
HISTORY_DAYS = 42


def home(path=None):
    path = path or os.environ.get("SPORTS_EVAL_HOME") or os.path.join(
        os.path.expanduser("~"), ".sports-eval")
    return os.path.abspath(path)


def _path(base, *parts):
    return os.path.join(home(base), *parts)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def write_json(path, value):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def compact_date(value):
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    return datetime.strptime(str(value).replace("-", "")[:8], "%Y%m%d").strftime("%Y%m%d")


def _shift(day, days):
    return (datetime.strptime(day, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def init(base=None, demo=False):
    root = home(base)
    for folder in ("jobs", "reports", "inbox"):
        os.makedirs(os.path.join(root, folder), exist_ok=True)
    if read_json(os.path.join(root, "profile.json")) is None:
        write_json(os.path.join(root, "profile.json"), {
            "kind": "athlete_profile", "schema_version": 1,
            "hr_max": None, "hr_rest": None,
            "hr_zones": None, "goal": "", "notes": ""})
    if read_json(os.path.join(root, "plan.json")) is None:
        write_json(os.path.join(root, "plan.json"), {
            "current_plan": "", "updated_at": None, "history": []})
    if demo:
        _seed_demo(root)
    return root


def _synthetic_history(end="20260809", days=55, seed=33):
    """Eight weeks of deterministic synthetic data: build, overreach, recover."""
    import math
    import random
    rng = random.Random(seed)
    rows, activities, intervals = [], [], []
    plan = {0: ("轻松跑", 5200, 148), 2: ("节奏跑", 8000, 162), 5: ("长距离", 14000, 151)}
    for offset in range(days, 0, -1):
        day = datetime.strptime(end, "%Y%m%d") - timedelta(days=offset - 1)
        date = day.strftime("%Y%m%d")
        phase = (days - offset) / float(days)
        strain = math.exp(-((phase - 0.55) / 0.12) ** 2)
        hrv = 44 - 7 * strain + rng.gauss(0, 2)
        rows.append({
            "date": date,
            "sleep_duration_min": round(440 - 35 * strain + rng.gauss(0, 18)),
            "sleep_score": round(81 - 6 * strain + rng.gauss(0, 3)),
            "sleep_efficiency": round(min(98, 93 - 3 * strain + rng.gauss(0, 1.5))),
            "hrv_rmssd": round(hrv),
            "resting_hr": round(51 + 4 * strain + rng.gauss(0, 1)),
            "stress": round(29 + 9 * strain + rng.gauss(0, 3)),
            "vo2max": round(46.2 + 1.3 * phase + rng.gauss(0, 0.1), 1),
            "running_ability": round(45.0 + 1.2 * phase + rng.gauss(0, 0.1), 1)})
        session = plan.get(day.weekday())
        if not session:
            rows[-1]["training_load"] = 0
        if session:
            kind, distance, hr = session
            distance = round(distance * (1 + 0.35 * strain) + rng.gauss(0, 300))
            pace = 330 if kind == "长距离" else 300 if kind == "轻松跑" else 265
            duration = round(distance / 1000.0 * (pace + rng.gauss(0, 8)))
            minutes = duration / 60.0
            high = 0.12 + 0.25 * strain if kind == "节奏跑" else 0.03 + 0.08 * strain
            zones = [round(minutes * share, 1) for share in
                     (0.25, 0.45 - high / 2, 0.30 - high / 2, high * 0.7, high * 0.3)]
            activities.append({"date": date, "start": day.strftime("%Y-%m-%dT07:10:00+08:00"),
                               "type": kind, "distance_m": distance, "duration_s": duration,
                               "avg_hr": round(hr + 6 * strain), "hr_zone_minutes": zones})
        bed = day - timedelta(minutes=rng.randint(-40, 50) + 30)
        intervals.append({"start": bed.strftime("%Y-%m-%dT23:%M:00+08:00").replace(
            day.strftime("%Y-%m-%d"), (day - timedelta(days=1)).strftime("%Y-%m-%d")),
            "end": day.strftime("%Y-%m-%dT07:") + "%02d:00+08:00" % rng.randint(0, 40)})
    return rows, activities, intervals


def _seed_demo(root):
    here = os.path.dirname(os.path.abspath(__file__))
    sample = read_json(os.path.join(here, "..", "examples", "synthetic_week.json"))
    rows, activities, intervals = _synthetic_history()
    upsert_daily(root, rows, "demo")
    add_activities(root, activities, "demo")
    add_sleep_intervals(root, intervals)
    rows = list(sample.get("daily") or [])
    upsert_daily(root, rows, "demo")
    from coach_events import load_events, log_event
    if not load_events(root):
        for event in ({"type": "post_workout_feedback", "date": "20260722", "rpe": 8, "pain": 3,
                       "note": "合成示例：节奏跑后右膝外侧轻微不适"},
                      {"type": "morning_checkin", "date": "20260725", "pain": 0,
                       "subjective_recovery": 6},
                      {"type": "morning_checkin", "date": "20260811", "pain": 0,
                       "subjective_recovery": 7}):
            log_event(root, event)
    add_activities(root, sample.get("activities") or [], "demo")
    add_sleep_intervals(root, sample.get("sleep_intervals") or [])
    plan = read_json(os.path.join(root, "plan.json"))
    if not plan.get("current_plan"):
        plan["current_plan"] = sample.get("current_plan") or ""
        write_json(os.path.join(root, "plan.json"), plan)


# ---------------------------------------------------------------- daily

def load_daily(base=None):
    return read_json(_path(base, "daily.json"), {}) or {}


def upsert_daily(base, rows, source="import"):
    """Merge metric rows by date; a lower-priority source never overwrites a higher one."""
    daily = load_daily(base)
    changed = 0
    for row in rows:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        day = compact_date(row["date"])
        record = daily.setdefault(day, {"_sources": {}})
        sources = record.setdefault("_sources", {})
        for key in DAILY_KEYS + EXTRA_DAILY_KEYS:
            value = row.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            current = sources.get(key)
            if current and SOURCE_PRIORITY.get(current, 1) > SOURCE_PRIORITY.get(source, 1):
                continue
            if record.get(key) != value or current != source:
                record[key] = value
                sources[key] = source
                changed += 1
    write_json(_path(base, "daily.json"), dict(sorted(daily.items())))
    return changed


# ----------------------------------------------------------- activities

def load_activities(base=None):
    return read_json(_path(base, "activities.json"), []) or []


def _activity_key(activity):
    return "%s|%s|%s" % (compact_date(activity["date"]), activity.get("start") or "",
                         round(float(activity.get("duration_s") or 0)))


def add_activities(base, activities, source="import"):
    existing = load_activities(base)
    keys = {_activity_key(item) for item in existing}
    added = 0
    for activity in activities:
        if not isinstance(activity, dict) or not activity.get("date"):
            continue
        item = dict(activity)
        item["date"] = compact_date(item["date"])
        item.setdefault("source", source)
        key = _activity_key(item)
        if key in keys:
            continue
        keys.add(key)
        existing.append(item)
        added += 1
    existing.sort(key=lambda item: (item["date"], item.get("start") or ""))
    write_json(_path(base, "activities.json"), existing)
    return added


def load_sleep_intervals(base=None):
    return read_json(_path(base, "sleep_intervals.json"), []) or []


def add_sleep_intervals(base, intervals):
    existing = load_sleep_intervals(base)
    seen = {(item["start"], item["end"]) for item in existing}
    added = 0
    for item in intervals:
        if not isinstance(item, dict) or not item.get("start") or not item.get("end"):
            continue
        pair = (item["start"], item["end"])
        if pair in seen:
            continue
        seen.add(pair)
        existing.append({"start": item["start"], "end": item["end"]})
        added += 1
    existing.sort(key=lambda item: item["start"])
    write_json(_path(base, "sleep_intervals.json"), existing)
    return added


# ------------------------------------------------------- profile / plan

def load_profile(base=None):
    return read_json(_path(base, "profile.json"), {}) or {}


def load_plan(base=None):
    return read_json(_path(base, "plan.json"), {"current_plan": "", "history": []})


def save_plan(base, text, reason):
    """Only coach_decisions.apply_plan may call this: plan changes need human consent."""
    plan = load_plan(base)
    if plan.get("current_plan"):
        plan.setdefault("history", []).append({
            "plan": plan["current_plan"], "replaced_at": datetime.now().isoformat(timespec="seconds"),
            "reason": reason})
    plan["current_plan"] = text
    plan["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(_path(base, "plan.json"), plan)
    return plan


# -------------------------------------------------------- weekly input

def activity_load(activity):
    if isinstance(activity.get("training_load"), (int, float)):
        return float(activity["training_load"])
    zones = activity.get("hr_zone_minutes")
    if isinstance(zones, list) and len(zones) == 5:
        return trimp_edwards(zones)
    return None


def _events(base):
    try:
        from coach_events import active_events
    except ImportError:
        return []
    projected = []
    for event in active_events(base):
        item = {key: event[key] for key in EVENT_FIELDS if key in event}
        if "date" in item and "type" in item:
            projected.append(item)
    return projected


def latest_date(base=None):
    candidates = list(load_daily(base)) + [item["date"] for item in load_activities(base)]
    return max(candidates) if candidates else None


def weekly_input(base=None, as_of=None):
    """Assemble the vendor-neutral weekly input (schema v1) for the week containing as_of."""
    as_of = compact_date(as_of) if as_of else (latest_date(base) or datetime.now().strftime("%Y%m%d"))
    day = datetime.strptime(as_of, "%Y%m%d")
    mon = (day - timedelta(days=day.weekday())).strftime("%Y%m%d")
    sun = _shift(mon, 6)
    first = _shift(mon, -HISTORY_DAYS)
    daily = load_daily(base)
    activities = [item for item in load_activities(base) if mon <= item["date"] <= as_of]
    loads = {}
    for item in load_activities(base):
        load = activity_load(item)
        if load is not None and first <= item["date"] <= as_of:
            loads[item["date"]] = round(loads.get(item["date"], 0.0) + load, 1)
    for date, record in daily.items():
        if first <= date <= as_of and date not in loads and record.get("training_load"):
            loads[date] = record["training_load"]

    def row(date):
        record = {"date": date}
        for key in DAILY_KEYS:
            if key in daily.get(date, {}):
                record[key] = daily[date][key]
        if "training_load" not in record and date in loads:
            record["training_load"] = loads[date]
        return record

    dates = sorted(set(daily) | set(loads))
    history = [row(d) for d in dates if first <= d < mon]
    current = [row(d) for d in dates if mon <= d <= as_of]
    public_activities = []
    for item in activities:
        public = {key: item[key] for key in ("date", "type", "distance_m", "duration_s", "avg_hr",
                                             "hr_zone_minutes") if item.get(key) is not None}
        public_activities.append(public)
    intervals = [item for item in load_sleep_intervals(base)
                 if _shift(as_of, -28) <= compact_date(item["end"][:10]) <= as_of]
    return {
        "schema_version": 1,
        "synthetic": False,
        "week": {"mon": mon, "sun": sun, "as_of": as_of, "partial": as_of < sun},
        "history_daily": history,
        "daily": current,
        "subjective_events": [event for event in _events(base) if first <= event["date"] <= as_of],
        "current_plan": load_plan(base).get("current_plan") or "",
        "activities": public_activities,
        "sleep_intervals": intervals,
        "load_history": {key: loads[key] for key in sorted(loads)},
    }


def status(base=None):
    daily = load_daily(base)
    activities = load_activities(base)
    return {
        "home": home(base),
        "days": len(daily),
        "first_date": min(daily) if daily else None,
        "last_date": max(daily) if daily else None,
        "activities": len(activities),
        "sleep_intervals": len(load_sleep_intervals(base)),
        "has_plan": bool(load_plan(base).get("current_plan")),
        "profile": {key: load_profile(base).get(key) for key in ("hr_max", "hr_rest", "goal")},
    }
