#!/usr/bin/env python3
"""intervals.icu sync: wellness, activities (with streams), planned workouts; stdlib only.

API: https://intervals.icu/api/v1, HTTP basic auth (user "API_KEY", password = personal key),
athlete id "0" = the key's own athlete. Settings live in the workspace (`intervals.json`);
the key is never returned to any surface and never appears in errors.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import store

BASE_URL = "https://intervals.icu/api/v1"
TIMEOUT = 30
BASIC_USER = "API_KEY"
FIELDS = ("athlete_id", "api_key", "last_sync", "since", "base_url")
STREAM_CAP = 40
STREAM_TYPES = ("time", "heartrate", "distance", "altitude", "fixed_altitude", "velocity_smooth")
WELLNESS_MAP = (("hrv", "hrv_rmssd"), ("restingHR", "resting_hr"), ("sleepScore", "sleep_score"),
                ("weight", "weight_kg"), ("steps", "steps"), ("respiration", "respiration"),
                ("spO2", "spo2"))


class IntervalsError(RuntimeError):
    pass


def _path(base):
    return os.path.join(store.home(base), "intervals.json")


def _planned_path(base):
    return os.path.join(store.home(base), "planned.json")


def load(base=None):
    saved = store.read_json(_path(base), {}) or {}
    cfg = {"athlete_id": "0", "api_key": "", "last_sync": None, "since": None, "base_url": BASE_URL}
    cfg.update({key: value for key, value in saved.items() if key in FIELDS and value not in (None, "")})
    return cfg


def public(base=None):
    """Settings for UI/CLI: the key is reduced to a flag and its last 4 characters."""
    cfg = load(base)
    key = cfg.pop("api_key")
    cfg.update({"key_set": bool(key), "key_hint": ("…" + key[-4:]) if len(key) >= 8 else ""})
    return cfg


def save(base, fields):
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise ValueError("unsupported intervals fields: %s" % ", ".join(unknown))
    saved = store.read_json(_path(base), {}) or {}
    for key, value in fields.items():
        if key == "api_key" and not value:
            continue  # blank key field means "keep the current one"
        saved[key] = value.strip() if isinstance(value, str) else value
    store.write_json(_path(base), saved)
    return public(base)


# --------------------------------------------------------------- network

def _request(cfg, method, path, query=None, body=None):
    if not cfg["api_key"]:
        raise IntervalsError("还没有设置 intervals.icu API Key（设置 → 数据源）。")
    url = cfg["base_url"].rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    token = base64.b64encode(("%s:%s" % (BASIC_USER, cfg["api_key"])).encode("utf-8")).decode("ascii")
    headers = {"Authorization": "Basic " + token, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise IntervalsError("intervals.icu 拒绝了 API Key（401），请检查 Key 是否正确。") from None
        if exc.code == 403:
            raise IntervalsError("intervals.icu 拒绝访问（403），Key 无权访问该运动员或数据。") from None
        if exc.code == 429:
            raise IntervalsError("intervals.icu 请求过于频繁（429），请稍后再同步。") from None
        raise IntervalsError("intervals.icu 返回错误 HTTP %d。" % exc.code) from None
    except urllib.error.URLError as exc:
        raise IntervalsError("连不上 intervals.icu：%s" % exc.reason) from None
    except (http.client.HTTPException, OSError) as exc:
        raise IntervalsError("intervals.icu 连接中断：%s" % exc) from None
    try:
        return json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        raise IntervalsError("intervals.icu 返回了无法解析的内容。") from None


def _athlete(cfg):
    return "/athlete/%s" % urllib.parse.quote(str(cfg["athlete_id"] or "0"), safe="")


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ------------------------------------------------------------------ sync

def _wellness_rows(items):
    rows = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        row = {"date": str(item["id"])[:10]}
        for source, target in WELLNESS_MAP:
            if _num(item.get(source)) is not None:
                row[target] = item[source]
        if _num(item.get("sleepSecs")):
            row["sleep_duration_min"] = round(item["sleepSecs"] / 60.0)
        if len(row) > 1:
            rows.append(row)
    return rows


def _is_run(kind):
    return "run" in (kind or "").lower()


def _activity(item):
    start = item.get("start_date_local")
    if not item.get("id") or not start:
        return None  # e.g. Strava stubs the API cannot serve
    out = {"date": start[:10].replace("-", ""), "start": start, "type": item.get("type") or "other",
           "intervals_id": str(item["id"])}
    if item.get("name"):
        out["name"] = item["name"]
    if _num(item.get("distance")):
        out["distance_m"] = round(float(item["distance"]), 1)
    duration = _num(item.get("moving_time")) or _num(item.get("elapsed_time"))
    if duration:
        out["duration_s"] = duration
    if _num(item.get("average_heartrate")):
        out["avg_hr"] = int(round(item["average_heartrate"]))
    # icu_training_load is intervals.icu's own HR/power model: kept apart, not mixed with
    # the device / TRIMP / sRPE loads the coach compares.
    if _num(item.get("icu_training_load")) is not None:
        out["icu_training_load"] = item["icu_training_load"]
    return out


def _points(streams_, start):
    by_type = {item.get("type"): item.get("data") for item in streams_ or [] if isinstance(item, dict)}
    times = by_type.get("time") or []
    if not times:
        return []
    try:
        t0 = datetime.fromisoformat(start).timestamp()
    except ValueError:
        t0 = 0.0
    altitude = by_type.get("fixed_altitude") or by_type.get("altitude") or []
    heart, dist = by_type.get("heartrate") or [], by_type.get("distance") or []
    points = []
    for index, second in enumerate(times):
        point = {"t": t0 + second}
        for key, series in (("hr", heart), ("d", dist), ("alt", altitude)):
            if index < len(series) and series[index] is not None:
                point[key] = series[index]
        points.append(point)
    return points


def _analyze(points, profile):
    try:
        import streams
        analyze = streams.analyze
    except (ImportError, AttributeError):
        return None
    try:
        return analyze(points, profile)
    except TypeError:
        return analyze(points)


def _planned(item):
    start = item.get("start_date_local")
    if not start or item.get("category") != "WORKOUT":
        return None
    return {"date": start[:10].replace("-", ""), "name": item.get("name") or "", "type": item.get("type") or "",
            "description": item.get("description") or "", "moving_time": item.get("moving_time"),
            "distance": item.get("distance"), "load": item.get("icu_training_load"),
            "intervals_id": item.get("id")}


def sync(base, days=60, now=None):
    cfg = load(base)
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    first = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    athlete = _athlete(cfg)
    counts = {"wellness_days": 0, "activities_added": 0, "streams_analyzed": 0, "planned": 0}

    wellness = _request(cfg, "GET", athlete + "/wellness", {"oldest": first, "newest": today}) or []
    rows = _wellness_rows(wellness)
    if rows:
        store.upsert_daily(base, rows, "device", {"name": "intervals.icu", "sha256": _sha(wellness)})
    counts["wellness_days"] = len(rows)

    raw = _request(cfg, "GET", athlete + "/activities", {"oldest": first, "newest": today}) or []
    known = {item.get("intervals_id") for item in store.load_activities(base)}
    profile = store.load_profile(base)
    fresh, budget = [], STREAM_CAP
    for item in raw:
        activity = _activity(item) if isinstance(item, dict) else None
        if not activity or activity["intervals_id"] in known:
            continue
        if _is_run(activity["type"]) and budget > 0:
            budget -= 1
            found = _request(cfg, "GET", "/activity/%s/streams.json" % urllib.parse.quote(activity["intervals_id"]),
                             {"types": STREAM_TYPES})
            analysis = _analyze(_points(found, activity["start"]), profile)
            if analysis:
                activity["analysis"] = analysis
                counts["streams_analyzed"] += 1
        fresh.append(activity)
    if fresh:
        counts["activities_added"] = store.add_activities(
            base, fresh, "device", {"name": "intervals.icu", "sha256": _sha(raw)})

    events = _request(cfg, "GET", athlete + "/events", {
        "oldest": (now - timedelta(days=14)).strftime("%Y-%m-%d"),
        "newest": (now + timedelta(days=28)).strftime("%Y-%m-%d"), "category": "WORKOUT"}) or []
    planned = [p for p in (_planned(e) for e in events if isinstance(e, dict)) if p]
    planned.sort(key=lambda p: (p["date"], p["name"]))
    store.write_json(_planned_path(base), planned)
    counts["planned"] = len(planned)

    saved = store.read_json(_path(base), {}) or {}
    saved["last_sync"] = now.isoformat(timespec="seconds")
    store.write_json(_path(base), saved)
    return counts


# ------------------------------------------------------------ compliance

def _family(kind):
    text = (kind or "").lower()
    if "run" in text or "跑" in text:
        return "run"
    if "ride" in text or "cycl" in text or "bike" in text or "骑" in text:
        return "ride"
    return "other"


def compliance(base, as_of, days=28):
    """Planned vs actual over the `days` ending at as_of; matches by date and run/ride/other family."""
    end = store.compact_date(as_of)
    first = store._shift(end, -(days - 1))
    plans = [p for p in store.read_json(_planned_path(base), []) or [] if first <= p["date"] <= end]
    pool = {}
    for item in store.load_activities(base):
        if first <= item["date"] <= end:
            pool.setdefault(item["date"], []).append(item)
    done, missed, by_week = 0, [], {}
    for plan in plans:
        mon = store._shift(plan["date"], -datetime.strptime(plan["date"], "%Y%m%d").weekday())
        week = by_week.setdefault(mon, {"week": mon, "planned": 0, "done": 0})
        week["planned"] += 1
        same = pool.get(plan["date"], [])
        match = next((a for a in same if _family(a.get("type")) == _family(plan.get("type"))), None)
        if match:
            same.remove(match)
            done += 1
            week["done"] += 1
        else:
            missed.append({"date": plan["date"], "name": plan["name"], "type": plan["type"]})
    extra = sum(len(items) for items in pool.values())
    return {"planned": len(plans), "done": done, "missed": missed, "extra": extra,
            "rate": round(done / len(plans), 2) if plans else None,
            "by_week": [by_week[key] for key in sorted(by_week)]}


# ------------------------------------------------------------------ push

def push_workout(base, workout, confirm=False):
    """Create one planned workout on the intervals.icu calendar. Humans only: confirm must be True."""
    if confirm is not True:
        raise PermissionError("推送训练到 intervals.icu 需要使用者本人确认。")
    cfg = load(base)
    date = str(workout.get("date") or workout.get("start_date_local") or "")[:10]
    date = datetime.strptime(date.replace("-", ""), "%Y%m%d").strftime("%Y-%m-%d")
    body = {"category": "WORKOUT", "start_date_local": date + "T00:00:00",
            "name": workout.get("name") or "Heartwork 训练", "type": workout.get("type") or "Run"}
    for key in ("description", "moving_time", "distance"):
        if workout.get(key) not in (None, ""):
            body[key] = workout[key]
    result = _request(cfg, "POST", _athlete(cfg) + "/events", body=body) or {}
    return {"intervals_id": result.get("id"), "date": date, "name": body["name"]}
