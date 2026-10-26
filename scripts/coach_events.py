#!/usr/bin/env python3
"""Append-only subjective event log (events.jsonl) in the local workspace."""

from __future__ import annotations

import json
import os
from datetime import datetime

import store
from coach_core import RED_FLAGS, stable_hash

EVENT_TYPES = {"morning_checkin", "post_workout_feedback", "pain_followup",
               "weekly_checkin", "note"}
BOOLEANS = ("illness", "function_limited", "worsening")
NOTE_LIMIT = 1000


def _file(base):
    return os.path.join(store.home(base), "events.jsonl")


def _number(event, key, low, high):
    value = event.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a number" % key)
    if not low <= value <= high:
        raise ValueError("%s must be within %s-%s" % (key, low, high))
    return value


def load_events(base=None):
    events = []
    try:
        with open(_file(base), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except FileNotFoundError:
        pass
    return events


def active_events(base=None):
    events = load_events(base)
    superseded = {event["supersedes"] for event in events if event.get("supersedes")}
    return [event for event in events if event.get("id") not in superseded]


def log_event(base, event):
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    kind = event.get("type")
    if kind not in EVENT_TYPES:
        raise ValueError("type must be one of %s" % sorted(EVENT_TYPES))
    record = {"type": kind}
    now = datetime.now()
    record["date"] = store.compact_date(event.get("date") or now)
    for key, low, high in (("pain", 0, 10), ("rpe", 0, 10), ("subjective_recovery", 1, 10),
                      ("duration_min", 0, 600)):
        value = _number(event, key, low, high)
        if value is not None:
            record[key] = value
    for key in BOOLEANS:
        if key in event:
            record[key] = bool(event[key])
    flags = event.get("red_flags") or []
    if not isinstance(flags, (list, tuple, set)) or not set(flags) <= RED_FLAGS:
        raise ValueError("red_flags must be a subset of %s" % sorted(RED_FLAGS))
    if flags:
        record["red_flags"] = sorted(set(flags))
    note = event.get("note")
    if note:
        if not isinstance(note, str) or len(note) > NOTE_LIMIT:
            raise ValueError("note must be text of at most %d characters" % NOTE_LIMIT)
        record["note"] = note
    if event.get("supersedes"):
        known = {item.get("id") for item in load_events(base)}
        if event["supersedes"] not in known:
            raise ValueError("supersedes refers to an unknown event id")
        record["supersedes"] = event["supersedes"]
    record["recorded_at"] = now.isoformat(timespec="microseconds")
    record["id"] = "ev_" + stable_hash(record)[:10]
    os.makedirs(store.home(base), exist_ok=True)
    with open(_file(base), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record
