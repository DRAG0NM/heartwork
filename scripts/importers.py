#!/usr/bin/env python3
"""Import device files and exports into the local workspace.

Supported: ``.fit`` (binary), ``.gpx``, ``.tcx``, daily-metric ``.csv`` and
Apple Health ``export.xml`` (or its zip). Everything is parsed with the
standard library and written through :mod:`store`; nothing is invented, so
heart-rate zones are only computed when the profile has ``hr_max``/``hr_zones``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

import store
import streams


FIT_EPOCH = 631065600
FIT_TYPES = {0: ("B", 1, 0xFF), 1: ("b", 1, 0x7F), 2: ("B", 1, 0xFF), 3: ("h", 2, 0x7FFF),
             4: ("H", 2, 0xFFFF), 5: ("i", 4, 0x7FFFFFFF), 6: ("I", 4, 0xFFFFFFFF),
             10: ("B", 1, 0), 11: ("H", 2, 0), 12: ("I", 4, 0),
             14: ("q", 8, 0x7FFFFFFFFFFFFFFF), 15: ("Q", 8, 0xFFFFFFFFFFFFFFFF),
             16: ("Q", 8, 0)}
FIT_SPORTS = {1: "running", 2: "cycling", 4: "fitness", 5: "swimming", 10: "training",
              11: "walking", 13: "skiing", 15: "rowing", 17: "hiking"}
SESSION_FIELDS = {2: "start", 5: "sport", 7: "elapsed", 8: "timer", 9: "distance", 16: "avg_hr"}

CSV_ALIASES = {
    "date": ("date", "day", "日期", "日", "时间"),
    "sleep_duration_min": ("sleep_duration_min", "sleep_min", "sleep_minutes", "睡眠时长", "睡眠分钟", "睡眠时间"),
    "sleep_hours": ("sleep_h", "sleep_hours", "sleep_duration_h", "睡眠小时"),
    "sleep_score": ("sleep_score", "睡眠评分", "睡眠得分"),
    "sleep_efficiency": ("sleep_efficiency", "睡眠效率"),
    "hrv_rmssd": ("hrv", "hrv_rmssd", "rmssd", "心率变异性", "夜间hrv"),
    "resting_hr": ("resting_hr", "rhr", "resting_heart_rate", "静息心率"),
    "stress": ("stress", "stress_score", "压力", "压力值"),
    "training_load": ("training_load", "load", "训练负荷", "负荷"),
    "vo2max": ("vo2max", "vo2_max", "最大摄氧量"),
    "running_ability": ("running_ability", "跑力", "跑步能力"),
    "subjective_recovery": ("subjective_recovery", "recovery", "主观恢复", "恢复感"),
    "weight_kg": ("weight", "weight_kg", "body_weight", "体重", "体重kg"),
    "steps": ("steps", "step_count", "步数"),
}
APPLE_ASLEEP = ("Asleep", "AsleepCore", "AsleepDeep", "AsleepREM", "AsleepUnspecified")


# ------------------------------------------------------------- shared

def _parse_time(text):
    text = (text or "").strip()
    if not text:
        return None
    text = re.sub(r"(\.\d+)", lambda m: (m.group(1) + "000000")[:7], text)
    text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone()


def _haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742000.0 * math.asin(math.sqrt(h))


def _lower_bounds(profile):
    zones = profile.get("hr_zones")
    if isinstance(zones, (list, tuple)) and all(isinstance(v, (int, float)) for v in zones):
        if len(zones) == 4:
            return [0] + list(zones)
        if len(zones) in (5, 6):
            return list(zones[:5])
    hr_max = profile.get("hr_max")
    if isinstance(hr_max, (int, float)) and hr_max > 0:
        return [hr_max * pct for pct in (0.5, 0.6, 0.7, 0.8, 0.9)]
    return None


def zone_minutes(samples, profile):
    """Minutes per zone from (unix_ts, hr) samples; None without a profile limit."""
    bounds = _lower_bounds(profile or {})
    samples = sorted((s for s in samples if s[1]), key=lambda item: item[0])
    if not bounds or len(samples) < 2:
        return None
    seconds = [0.0] * 5
    for (t0, hr), (t1, _) in zip(samples, samples[1:]):
        step = min(max(t1 - t0, 0), 60)
        for index in range(4, -1, -1):
            if hr >= bounds[index]:
                seconds[index] += step
                break
    return [round(value / 60.0, 1) for value in seconds]


def _activity(kind, start, duration, distance, samples, profile, avg_hr=None):
    if start is None:
        return None
    local = start
    hrs = [hr for _, hr in samples if hr]
    if avg_hr is None and hrs:
        avg_hr = sum(hrs) / len(hrs)
    item = {"date": local.strftime("%Y%m%d"), "start": local.isoformat(timespec="seconds"),
            "type": kind or "other"}
    if distance:
        item["distance_m"] = round(float(distance), 1)
    if duration:
        item["duration_s"] = round(float(duration), 1)
    if avg_hr:
        item["avg_hr"] = int(round(avg_hr))
    zones = zone_minutes(samples, profile)
    if zones:
        item["hr_zone_minutes"] = zones
    return item


def _attach(item, points, profile):
    if item is not None:
        analysis = streams.analyze(points, profile)
        if analysis:
            item["analysis"] = analysis
    return item


# ---------------------------------------------------------------- FIT

def _fit_chunk(data, pos, end):
    defs = {}
    ref_ts = None
    while pos < end:
        header = data[pos]
        pos += 1
        compressed = bool(header & 0x80)
        if not compressed and header & 0x40:
            endian = ">" if data[pos + 1] else "<"
            gnum = struct.unpack_from(endian + "H", data, pos + 2)[0]
            count = data[pos + 4]
            pos += 5
            fields = [tuple(data[pos + 3 * k:pos + 3 * k + 3]) for k in range(count)]
            pos += 3 * count
            dev_size = 0
            if header & 0x20:
                dev_count = data[pos]
                pos += 1
                for k in range(dev_count):
                    dev_size += data[pos + 3 * k + 1]
                pos += 3 * dev_count
            defs[header & 0x0F] = (gnum, endian, fields, dev_size)
            continue
        local = (header >> 5) & 3 if compressed else header & 0x0F
        if local not in defs:
            return
        gnum, endian, fields, dev_size = defs[local]
        values = {}
        if compressed and ref_ts is not None:
            offset = header & 0x1F
            ts = (ref_ts & ~0x1F) + offset
            if offset < (ref_ts & 0x1F):
                ts += 0x20
            ref_ts = ts
            values[253] = ts
        for number, size, base in fields:
            kind = FIT_TYPES.get(base & 0x1F)
            if (number == 253 or gnum in (18, 20)) and kind and kind[1] == size:
                value = struct.unpack_from(endian + kind[0], data, pos)[0]
                if value != kind[2]:
                    values[number] = value
                    if number == 253:
                        ref_ts = value
            pos += size
        pos += dev_size
        yield gnum, values


def _fit_messages(data):
    pos = 0
    while pos + 12 <= len(data) and data[pos] >= 12 and data[pos + 8:pos + 12] == b".FIT":
        size = struct.unpack_from("<I", data, pos + 4)[0]
        end = min(pos + data[pos] + size, len(data))
        for message in _fit_chunk(data, pos + data[pos], end):
            yield message
        pos = end + 2


def parse_fit(data, profile=None):
    sessions, records, points = [], [], []
    for gnum, values in _fit_messages(data):
        if gnum == 18:
            sessions.append({name: values[number] for number, name in SESSION_FIELDS.items() if number in values})
        elif gnum == 20 and 253 in values:
            records.append((values[253], values.get(3)))
            alt = values.get(78, values.get(2))
            points.append((values[253], values.get(3), values[5] / 100.0 if 5 in values else None,
                           alt / 5.0 - 500 if alt is not None else None))
    if not sessions and records:
        sessions = [{"start": records[0][0], "elapsed": (records[-1][0] - records[0][0]) * 1000.0}]
    activities = []
    for session in sessions:
        if "start" not in session:
            continue
        start = session["start"]
        duration = (session.get("timer") or session.get("elapsed") or 0) / 1000.0
        span = (session.get("elapsed") or session.get("timer") or 0) / 1000.0
        samples = [(ts + FIT_EPOCH, hr) for ts, hr in records if hr and start <= ts <= start + span + 1]
        item = _activity(FIT_SPORTS.get(session.get("sport"), "other"),
                         datetime.fromtimestamp(start + FIT_EPOCH, timezone.utc).astimezone(), duration,
                         session.get("distance", 0) / 100.0, samples, profile, session.get("avg_hr"))
        if item:
            stream = [{"t": ts + FIT_EPOCH, "hr": hr, "d": d, "alt": alt}
                      for ts, hr, d, alt in points if start <= ts <= start + span + 1]
            activities.append(_attach(item, stream, profile))
    return activities


# ------------------------------------------------------------ GPX/TCX

def _name(element):
    return element.tag.rsplit("}", 1)[-1]


def _find(element, name):
    return [child for child in element.iter() if _name(child) == name]


def _direct(element, name):
    return next((c.text for c in element if _name(c) == name), None)


def _float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def parse_gpx(root, profile=None):
    points, samples, stream, run = [], [], [], 0.0
    for point in _find(root, "trkpt"):
        time = _parse_time(_direct(point, "time"))
        hr = next((c.text for c in point.iter() if _name(c) == "hr"), None)
        try:
            lat, lon = float(point.get("lat")), float(point.get("lon"))
        except (TypeError, ValueError):
            continue
        if points:
            run += _haversine(points[-1][1:], (lat, lon))
        points.append((time, lat, lon))
        if time:
            ele = _direct(point, "ele")
            stream.append({"t": time.timestamp(), "d": run, "alt": _float(ele) if ele else None,
                           "hr": int(hr) if hr and hr.strip().isdigit() else None})
        if time and hr and hr.strip().isdigit():
            samples.append((time.timestamp(), int(hr)))
    timed = [p for p in points if p[0]]
    if not timed:
        return []
    distance = sum(_haversine(a[1:], b[1:]) for a, b in zip(points, points[1:]))
    kind = next((c.text.strip().lower() for c in _find(root, "type") if c.text), "other")
    duration = (timed[-1][0] - timed[0][0]).total_seconds()
    item = _activity(kind, timed[0][0], duration, distance, samples, profile)
    return [_attach(item, stream, profile)] if item else []


def parse_tcx(root, profile=None):
    activities = []
    for node in _find(root, "Activity"):
        samples, times, dists, stream = [], [], [], []
        laps = _find(node, "Lap")
        lap_seconds = sum(_float(_direct(lap, "TotalTimeSeconds")) for lap in laps)
        lap_dist = sum(_float(_direct(lap, "DistanceMeters")) for lap in laps)
        for point in _find(node, "Trackpoint"):
            time = _parse_time(_direct(point, "Time"))
            if time is None:
                continue
            times.append(time)
            dm, am = _direct(point, "DistanceMeters"), _direct(point, "AltitudeMeters")
            if dm:
                dists.append(_float(dm))
            row = {"t": time.timestamp(), "hr": None, "d": _float(dm) if dm else None,
                   "alt": _float(am) if am else None}
            stream.append(row)
            for child in point:
                if _name(child) == "HeartRateBpm":
                    value = _direct(child, "Value")
                    if value and value.strip().isdigit():
                        samples.append((time.timestamp(), int(value)))
                        row["hr"] = int(value)
        start = _parse_time(_direct(node, "Id")) or (times[0] if times else None)
        duration = lap_seconds or ((times[-1] - times[0]).total_seconds() if len(times) > 1 else 0)
        distance = max(dists) - min(dists) if len(dists) > 1 else lap_dist
        item = _activity((node.get("Sport") or "other").lower(), start, duration, distance or lap_dist,
                         samples, profile)
        if item:
            activities.append(_attach(item, stream, profile))
    return activities


# ---------------------------------------------------------------- CSV

def _norm(text):
    return re.sub(r"[^0-9a-z一-鿿]+", "", re.sub(r"\(.*?\)|（.*?）", "", text.lower()))


def _csv_date(text):
    match = re.match(r"\s*(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", str(text))
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups())).strftime("%Y%m%d")
    except ValueError:
        return None


def _csv_rows(raw):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gb18030", errors="replace")
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = max(",;\t", key=first.count)
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def parse_csv(raw):
    rows = _csv_rows(raw)
    if len(rows) < 2:
        return None
    lookup = {_norm(alias): key for key, aliases in CSV_ALIASES.items() for alias in aliases}
    columns = {index: lookup[_norm(head)] for index, head in enumerate(rows[0]) if _norm(head) in lookup}
    if "date" not in columns.values() or len(columns) < 2:
        return None
    out = []
    for row in rows[1:]:
        record = {}
        for index, key in columns.items():
            if index >= len(row) or not row[index].strip():
                continue
            if key == "date":
                record["date"] = _csv_date(row[index])
                continue
            try:
                value = float(row[index].strip().replace(",", ""))
            except ValueError:
                continue
            if key == "sleep_hours":
                key, value = "sleep_duration_min", value * 60
            record[key] = value
        if record.get("date") and len(record) > 1:
            out.append(record)
    return out


# -------------------------------------------------------- Apple Health

def _apple_time(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
    except (TypeError, ValueError):
        return None


def _apple_kind(text):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", (text or "").replace("HKWorkoutActivityType", "")).lower() or "other"


def _sleep_sessions(spans):
    """Merge overlapping asleep spans, then group spans less than 1h apart into one night."""
    spans.sort(key=lambda item: item[0])
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    sessions = []
    for start, end in merged:
        minutes = (end - start).total_seconds() / 60.0
        if sessions and (start - sessions[-1]["end"]).total_seconds() <= 3600:
            sessions[-1]["end"] = end
            sessions[-1]["minutes"] += minutes
        else:
            sessions.append({"start": start, "end": end, "minutes": minutes})
    return sessions


def _apple_workout(elem, profile):
    start = _apple_time(elem.get("startDate"))
    if not start:
        return None
    seconds = _float(elem.get("duration")) * {"min": 60, "sec": 1, "hr": 3600}.get(elem.get("durationUnit"), 60)
    meters = _float(elem.get("totalDistance")) * {"km": 1000, "mi": 1609.344, "m": 1}.get(
        elem.get("totalDistanceUnit"), 0)
    avg = next((s.get("average") for s in elem.iter("WorkoutStatistics")
                if (s.get("type") or "").endswith("HeartRate")), None)
    return _activity(_apple_kind(elem.get("workoutActivityType")), start, seconds, meters, [], profile,
                     _float(avg) or None)


def parse_apple(stream, profile=None):
    daily, spans, workouts, rhr, sdnn = {}, [], [], {}, 0
    root = None
    for event, elem in ET.iterparse(stream, events=("start", "end")):
        if event == "start":
            root = elem if root is None else root
            continue
        if elem.tag == "Record":
            kind, value = elem.get("type", ""), elem.get("value")
            start, end = _apple_time(elem.get("startDate")), _apple_time(elem.get("endDate"))
            if kind.endswith("RestingHeartRate") and start:
                rhr.setdefault(start.strftime("%Y%m%d"), []).append(_float(value))
            elif kind.endswith("BodyMass") and start:
                weight = _float(value) * (0.45359237 if elem.get("unit") == "lb" else 1.0)
                daily.setdefault(start.strftime("%Y%m%d"), {})["weight_kg"] = round(weight, 2)
            elif kind.endswith("HeartRateVariabilitySDNN"):
                sdnn += 1
            elif kind.endswith("SleepAnalysis") and start and end and end > start:
                if (value or "").replace("HKCategoryValueSleepAnalysis", "") in APPLE_ASLEEP:
                    spans.append((start, end))
        elif elem.tag == "Workout":
            item = _apple_workout(elem, profile)
            if item:
                workouts.append(item)
        else:
            continue
        elem.clear()
        root.clear()
    for day, values in rhr.items():
        daily.setdefault(day, {})["resting_hr"] = round(sum(values) / len(values), 1)
    sessions = _sleep_sessions(spans)
    for session in sessions:
        record = daily.setdefault(session["end"].strftime("%Y%m%d"), {})
        record["sleep_duration_min"] = round(record.get("sleep_duration_min", 0) + session["minutes"], 1)
    intervals = [{"start": s["start"].isoformat(timespec="seconds"), "end": s["end"].isoformat(timespec="seconds")}
                 for s in sessions]
    rows = [dict(values, date=day) for day, values in sorted(daily.items())]
    return rows, intervals, workouts, sdnn


# ------------------------------------------------------------- driver

def _walk(paths):
    for path in paths:
        if os.path.isdir(path):
            for folder, dirs, names in os.walk(path):
                dirs.sort()
                for name in sorted(names):
                    yield os.path.join(folder, name)
        else:
            yield path


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kind(path):
    ext = os.path.splitext(path)[1].lower()
    return "apple_health" if ext in (".xml", ".zip") else ext.lstrip(".")


def _apple_from(handle, base, profile, result, path, ref):
    rows, intervals, workouts, sdnn = parse_apple(handle, profile)
    result["daily_changes"] += store.upsert_daily(base, rows, "device", ref)
    result["activities"] += store.add_activities(base, workouts, "device", ref)
    result["sleep_intervals"] += store.add_sleep_intervals(base, intervals)
    if sdnn:
        result["skipped"].append({"path": path, "reason": "HRV SDNN 与 RMSSD 口径不同，已跳过 %d 条，不写入 hrv_rmssd" % sdnn})


def _import_file(base, path, profile, result, ref):
    """Return None on success, otherwise a Chinese reason the file was skipped."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fit":
        with open(path, "rb") as handle:
            activities = parse_fit(handle.read(), profile)
        if not activities:
            return "没有找到运动记录"
        result["activities"] += store.add_activities(base, activities, "device", ref)
    elif ext in (".gpx", ".tcx"):
        root = ET.parse(path).getroot()
        activities = (parse_gpx if ext == ".gpx" else parse_tcx)(root, profile)
        if not activities:
            return "没有找到带时间的轨迹点"
        result["activities"] += store.add_activities(base, activities, "device", ref)
    elif ext == ".csv":
        with open(path, "rb") as handle:
            rows = parse_csv(handle.read())
        if not rows:
            return "表头里没有可识别的日期或指标列"
        result["daily_changes"] += store.upsert_daily(base, rows, "import", ref)
    elif ext == ".xml":
        with open(path, "rb") as handle:
            if b"<HealthData" not in handle.read(4096):
                return "不是 Apple Health export.xml"
            handle.seek(0)
            _apple_from(handle, base, profile, result, path, ref)
    elif ext == ".zip":
        with zipfile.ZipFile(path) as archive:
            member = next((n for n in archive.namelist() if os.path.basename(n) == "export.xml"), None)
            if member is None:
                return "压缩包里没有 export.xml"
            with archive.open(member) as handle:
                _apple_from(handle, base, profile, result, path, ref)
    else:
        return "不支持的格式"
    return None


def import_paths(base, paths):
    """Import files or directories (recursive); returns counts plus skipped files."""
    store.init(base)
    profile = store.load_profile(base)
    result = {"files": 0, "activities": 0, "daily_changes": 0, "sleep_intervals": 0, "skipped": []}
    for path in _walk([paths] if isinstance(paths, str) else paths):
        if not os.path.exists(path):
            result["skipped"].append({"path": path, "reason": "路径不存在"})
            continue
        counted = ("activities", "daily_changes", "sleep_intervals")
        before = {key: result[key] for key in counted}
        try:
            sha = _sha256(path)
            ref = {"name": os.path.basename(path), "sha256": sha}
            reason = _import_file(base, path, profile, result, ref)
        except Exception as error:
            reason = "解析失败: %s" % error
        if reason:
            result["skipped"].append({"path": path, "reason": reason})
        else:
            result["files"] += 1
            store.add_import(base, {
                "file": ref["name"], "sha256": sha, "size": os.path.getsize(path),
                "imported_at": datetime.now().isoformat(timespec="seconds"), "kind": _kind(path),
                "counts": {key: result[key] - before[key] for key in counted}})
    return result
