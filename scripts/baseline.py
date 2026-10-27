#!/usr/bin/env python3
"""Single source of truth for "relative to personal normal" (rolling window).

For a day D: recent = D-6..D (7 days, inclusive), baseline = D-28..D-1 (28 days
ending the day before). Center = baseline median, scale = 1.4826 x MAD with a
per-metric floor (MIN_SCALE), SWC (smallest worthwhile change) = 0.5 x scale.
HRV is analysed as ln(RMSSD); displayed values are converted back to ms.
Training load is spiky (rest days are 0), so it is compared as a rolling 7-day
mean: recent = the 7-day mean ending D, baseline = the 7-day means ending
D-34..D-7, so both windows still never overlap.
Descriptive only: not a diagnosis, not a threshold.
"""

from __future__ import annotations

import functools
import math
from datetime import datetime, timedelta

RECENT_DAYS = 7
BASELINE_DAYS = 28
MIN_BASELINE = 7
MIN_RECENT = 3

METRICS = {
    "hrv_rmssd": {"label": "HRV RMSSD", "unit": "ms", "polarity": 1, "transform": "ln"},
    "resting_hr": {"label": "静息心率", "unit": "bpm", "polarity": -1, "transform": "identity"},
    "subjective_recovery": {"label": "主观恢复", "unit": "1-10", "polarity": 1,
                            "transform": "identity"},
    "sleep_duration_min": {"label": "睡眠时长", "unit": "min", "polarity": 1,
                           "transform": "identity"},
    "sleep_efficiency": {"label": "睡眠效率", "unit": "%", "polarity": 1,
                         "transform": "identity"},
    "sleep_score": {"label": "睡眠评分", "unit": "分", "polarity": 1, "transform": "identity"},
    "training_load": {"label": "训练负荷", "unit": "a.u.", "polarity": 0,
                      "transform": "identity", "rolling": RECENT_DAYS},
    "stress": {"label": "压力", "unit": "a.u.", "polarity": -1, "transform": "identity"},
    "vo2max": {"label": "VO₂max", "unit": "ml/kg/min", "polarity": 1, "transform": "identity"},
    "running_ability": {"label": "跑步能力", "unit": "a.u.", "polarity": 1,
                        "transform": "identity"},
}
# Floors in the transformed domain (ln units for HRV).
MIN_SCALE = {"resting_hr": 0.5, "sleep_duration_min": 10.0, "sleep_efficiency": 0.5,
             "hrv_rmssd": 0.02, "stress": 1.0, "vo2max": 0.2, "running_ability": 0.2,
             "sleep_score": 1.0, "subjective_recovery": 0.5, "training_load": 10.0,
             "tsb": 2.0}

FAMILIES = {
    "recovery": ["hrv_rmssd", "resting_hr", "subjective_recovery"],
    "sleep": ["sleep_duration_min", "sleep_efficiency", "sleep_score"],
    "load": ["training_load"],
    "stress": ["stress"],
    "performance": ["vo2max", "running_ability"],
}
FAMILY_LABELS = {"recovery": "恢复", "sleep": "睡眠", "load": "负荷",
                 "stress": "压力", "performance": "表现"}


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _compact(value):
    return str(value).replace("-", "")[:8]


@functools.lru_cache(maxsize=16384)
def _shift(day, days):
    return (datetime.strptime(day, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0


def robust_scale(values, center, key=None):
    """1.4826 x MAD, floored by the metric's minimum scale (no sample-SD term)."""
    mad = median([abs(value - center) for value in values]) or 0.0
    return max(1.4826 * mad, MIN_SCALE.get(key, 1e-6))


def forward(key, value):
    """Raw value -> analysis domain; None when it cannot be transformed."""
    if not _finite(value):
        return None
    if METRICS.get(key, {}).get("transform") == "ln":
        return math.log(value) if value > 0 else None
    return float(value)


def inverse(key, value):
    return math.exp(value) if METRICS.get(key, {}).get("transform") == "ln" else value


def index_rows(daily):
    """Accept a list of rows ({"date": ...}) or a {date: row} mapping."""
    if isinstance(daily, dict):
        return {_compact(day): row for day, row in daily.items() if isinstance(row, dict)}
    return {_compact(row["date"]): row for row in daily
            if isinstance(row, dict) and isinstance(row.get("date"), str)}


def window(rows, key, first, last):
    """Transformed values for compact dates first..last (inclusive)."""
    values = []
    day = first
    while day <= last:
        value = forward(key, (rows.get(day) or {}).get(key))
        if value is not None:
            values.append(value)
        day = _shift(day, 1)
    return values


def rolling_mean(rows, key, day, days):
    """Mean over day-days+1..day; a day with a row but no value counts as 0 (no session)."""
    values = []
    for offset in range(days):
        row = rows.get(_shift(day, -offset))
        if row is not None:
            value = forward(key, row.get(key))
            values.append(0.0 if value is None else value)
    return sum(values) / len(values) if len(values) >= MIN_RECENT + 1 else None


def _rolling_windows(rows, key, day, days):
    recent = rolling_mean(rows, key, day, days)
    base = []
    for offset in range(RECENT_DAYS, RECENT_DAYS + BASELINE_DAYS):
        value = rolling_mean(rows, key, _shift(day, -offset), days)
        if value is not None:
            base.append(value)
    return ([] if recent is None else [recent]), base


def metric_status(daily, key, day, _rows=None):
    meta = METRICS.get(key)
    if meta is None:
        return None
    rows = _rows if _rows is not None else index_rows(daily)
    day = _compact(day)
    if meta.get("rolling"):
        recent, base = _rolling_windows(rows, key, day, meta["rolling"])
        n_recent = sum(1 for offset in range(RECENT_DAYS) if _shift(day, -offset) in rows)
        if len(base) < MIN_BASELINE or not recent:
            return None
    else:
        recent = window(rows, key, _shift(day, -(RECENT_DAYS - 1)), day)
        base = window(rows, key, _shift(day, -(RECENT_DAYS + BASELINE_DAYS - 1)),
                      _shift(day, -RECENT_DAYS))
        n_recent = len(recent)
        if len(base) < MIN_BASELINE or n_recent < MIN_RECENT:
            return None
    center = median(base)
    scale = robust_scale(base, center, key)
    swc = 0.5 * scale
    recent_mean = sum(recent) / len(recent)
    diff = recent_mean - center
    status = "above" if diff > swc else "below" if diff < -swc else "within"
    polarity = meta["polarity"]
    if polarity == 0:
        oriented = "neutral"
    elif status == "within":
        oriented = "normal"
    else:
        oriented = "better" if (diff > 0) == (polarity > 0) else "worse"
    value = (rows.get(day) or {}).get(key)
    return {
        "key": key, "label": meta["label"], "unit": meta["unit"], "date": day,
        "value": value if _finite(value) else None,
        "recent_mean": round(inverse(key, recent_mean), 2),
        "center": round(inverse(key, center), 2),
        "band": [round(inverse(key, center - swc), 2), round(inverse(key, center + swc), 2)],
        "scale": round(scale, 4), "swc": round(swc, 4), "z": round(diff / scale, 2),
        "status": status, "oriented": oriented,
        "n_recent": n_recent, "n_baseline": len(base), "transform": meta["transform"],
        "aggregate": "rolling_%d_day_mean" % meta["rolling"] if meta.get("rolling") else "daily",
    }


def day_status(daily, day, keys=None):
    rows = index_rows(daily)
    result = {}
    for key in keys or METRICS:
        status = metric_status(None, key, day, _rows=rows)
        if status is not None:
            result[key] = status
    return result


def family_status(statuses):
    result = {}
    for family, keys in FAMILIES.items():
        present = [statuses[key] for key in keys if key in statuses]
        if not present:
            result[family] = {"available": False, "z": None, "oriented": None}
            continue
        zs = [METRICS[item["key"]]["polarity"] * item["z"] if METRICS[item["key"]]["polarity"]
              else item["z"] for item in present]
        z = sum(zs) / len(zs)
        neutral = all(METRICS[item["key"]]["polarity"] == 0 for item in present)
        oriented = "neutral" if neutral else "better" if z > 0.5 else "worse" if z < -0.5 else "normal"
        result[family] = {"available": True, "z": round(z, 2), "oriented": oriented}
    return result
