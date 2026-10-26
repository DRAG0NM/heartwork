#!/usr/bin/env python3
"""Vendor-neutral personal baseline, multidomain state, and change candidates.

All outputs are descriptive. A score of 50 is the individual's historical
anchor, not a health threshold. Experimental layers must not independently
trigger training or medical decisions.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import baseline
from core_metrics import acwr_uncoupled, banister_series


SIGNALS = (
    {"key": "sleep_duration_min", "label": "睡眠时长", "unit": "min",
     "axis": "sleep_circadian", "polarity": 1},
    {"key": "sleep_efficiency", "label": "睡眠效率", "unit": "%",
     "axis": "sleep_circadian", "polarity": 1},
    {"key": "hrv_rmssd", "label": "HRV RMSSD", "unit": "ms",
     "axis": "recovery_autonomic", "polarity": 1},
    {"key": "resting_hr", "label": "静息心率", "unit": "bpm",
     "axis": "recovery_autonomic", "polarity": -1},
    {"key": "training_load", "label": "训练负荷", "unit": "a.u.",
     "axis": None, "polarity": 0},
    {"key": "vo2max", "label": "VO₂max", "unit": "ml/kg/min",
     "axis": "running_performance", "polarity": 1},
    {"key": "running_ability", "label": "跑步能力", "unit": "a.u.",
     "axis": "running_performance", "polarity": 1},
    {"key": "stress", "label": "压力", "unit": "a.u.",
     "axis": "health_stress", "polarity": -1},
)

AXES = (
    ("recovery_autonomic", "自主神经与恢复"),
    ("sleep_circadian", "睡眠与昼夜节律"),
    ("training_load_fatigue", "训练负荷与残余疲劳"),
    ("running_performance", "跑步表现与能力"),
    ("health_stress", "日常压力与健康信号"),
)


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _day(value):
    return datetime.strptime(value.replace("-", ""), "%Y%m%d")


_median = baseline.median


def _score(z):
    return round(50.0 + 50.0 * math.tanh(max(-4.0, min(4.0, z)) / 2.0), 1)


def _records(daily):
    return [(index, row) for index, row in enumerate(daily)
            if isinstance(row, dict) and isinstance(row.get("date"), str)]


def raw_signal_snapshot(daily, mon, as_of, baseline_days=28):
    """Keep raw metrics separate from interpretation and model scores."""
    mon_day = _day(mon)
    baseline_start = (mon_day - timedelta(days=baseline_days)).strftime("%Y%m%d")
    baseline_end = (mon_day - timedelta(days=1)).strftime("%Y%m%d")
    cards = []
    rows = _records(daily)
    for spec in SIGNALS:
        current = [(index, row["date"], float(row[spec["key"]])) for index, row in rows
                   if mon <= row["date"] <= as_of and _finite(row.get(spec["key"]))]
        if not current:
            continue
        baseline = [float(row[spec["key"]]) for _, row in rows
                    if baseline_start <= row["date"] <= baseline_end
                    and _finite(row.get(spec["key"]))]
        current_values = [value for _, _, value in current]
        current_mean = sum(current_values) / len(current_values)
        baseline_mean = sum(baseline) / len(baseline) if baseline else None
        latest_index, latest_date, latest_value = current[-1]
        cards.append({
            "key": spec["key"], "label": spec["label"], "unit": spec["unit"],
            "latest_date": latest_date, "latest": round(latest_value, 2),
            "week_mean": round(current_mean, 2),
            "baseline_28d_mean": round(baseline_mean, 2) if baseline_mean is not None else None,
            "delta_vs_baseline": round(current_mean - baseline_mean, 2)
            if baseline_mean is not None else None,
            "n_week": len(current), "n_baseline": len(baseline),
            "source": "input", "pointer": "/daily/%d/%s" % (latest_index, spec["key"]),
        })
    return cards


def _axis_state(daily, axis_id, label, mon, as_of, baseline_days=42, load_history=None):
    if axis_id == "training_load_fatigue":
        return _load_axis(daily, label, as_of, load_history)
    rows = baseline.index_rows(daily)
    components, weighted, weights = {}, [], []
    for spec in (item for item in SIGNALS if item["axis"] == axis_id):
        status = baseline.metric_status(None, spec["key"], as_of, _rows=rows)
        if status is None:
            continue
        reliability = (min(1.0, status["n_baseline"] / 28.0)
                       * min(1.0, status["n_recent"] / 7.0))
        oriented_z = spec["polarity"] * status["z"]
        component_score = _score(oriented_z)
        components[spec["key"]] = {
            "score": component_score, "oriented_z": round(oriented_z, 2),
            "current_mean": status["recent_mean"], "baseline_median": status["center"],
            "n_current": status["n_recent"], "n_baseline": status["n_baseline"],
            "reliability": round(reliability, 3),
        }
        weighted.append(component_score * reliability)
        weights.append(reliability)
    return _axis_result(axis_id, label, components, weighted, weights)


def _axis_result(axis_id, label, components, weighted, weights, **extra):
    if not weights or sum(weights) <= 0:
        return {"id": axis_id, "label": label, "available": False,
                "reason": "insufficient_history_or_current_data", "components": {}}
    index = sum(weighted) / sum(weights)
    result = {"id": axis_id, "label": label, "available": True,
              "index": round(index, 1), "centered_delta": round(index - 50.0, 1),
              "reliability": round(sum(weights) / len(weights), 3),
              "components": components, "baseline_days": 28}
    result.update(extra)
    return result


def load_rows(daily, load_history=None):
    """Daily load map: explicit load_history, else training_load found in the daily rows."""
    if load_history:
        return dict(load_history)
    return {day: row["training_load"] for day, row in baseline.index_rows(daily).items()
            if _finite(row.get("training_load"))}


def _load_axis(daily, label, as_of, load_history, min_span=21, window=42):
    loads = load_rows(daily, load_history)
    series = banister_series(loads, as_of)
    if len(series) < min_span:
        return _axis_result("training_load_fatigue", label, {}, [], [])
    today = series[-1]
    start = baseline._shift(as_of, -window)
    prior = [tsb for day, _, _, tsb in series[:-1] if day >= start]
    if len(prior) < 7:
        return _axis_result("training_load_fatigue", label, {}, [], [])
    center = _median(prior)
    z = (today[3] - center) / baseline.robust_scale(prior, center, "tsb")
    reliability = min(1.0, len(prior) / 28.0)
    score = _score(z)
    components = {"tsb": {
        "score": score, "oriented_z": round(z, 2), "current_mean": round(today[3], 2),
        "baseline_median": round(center, 2), "n_current": 1, "n_baseline": len(prior),
        "reliability": round(reliability, 3)}}
    uncoupled = acwr_uncoupled(loads, as_of)
    return _axis_result("training_load_fatigue", label, components, [score * reliability],
                        [reliability], load={
                            "atl": round(today[1], 2), "ctl": round(today[2], 2),
                            "tsb": round(today[3], 2), "acwr_uncoupled": uncoupled},
                        baseline_days=window)


def change_candidates(daily, as_of, recent_days=7, baseline_days=28):
    """Robust recent-vs-prior shifts; candidates are not alerts or causes."""
    as_of_day = _day(as_of)
    recent_start = (as_of_day - timedelta(days=recent_days - 1)).strftime("%Y%m%d")
    baseline_end = (as_of_day - timedelta(days=recent_days)).strftime("%Y%m%d")
    baseline_start = (as_of_day - timedelta(days=recent_days + baseline_days - 1)).strftime("%Y%m%d")
    signals = []
    for spec in SIGNALS:
        key = spec["key"]
        base = [baseline.forward(key, row.get(key)) for _, row in _records(daily)
                if baseline_start <= row["date"] <= baseline_end]
        base = [value for value in base if value is not None]
        recent = [baseline.forward(key, row.get(key)) for _, row in _records(daily)
                  if recent_start <= row["date"] <= as_of]
        recent = [value for value in recent if value is not None]
        if len(base) < 14 or len(recent) < 3:
            continue
        center = _median(base)
        recent_center = _median(recent)
        shift_z = (recent_center - center) / baseline.robust_scale(base, center, key)
        signals.append({
            "key": key, "baseline_median": round(baseline.inverse(key, center), 2),
            "recent_median": round(baseline.inverse(key, recent_center), 2),
            "robust_shift_z": round(shift_z, 2), "candidate": abs(shift_z) >= 2.5,
            "n_baseline": len(base), "n_recent": len(recent),
        })
    return {"kind": "change_candidates", "status": "descriptive_not_validated",
            "as_of": as_of, "candidate_count": sum(row["candidate"] for row in signals),
            "signals": signals,
            "warning": "Statistical shifts are not medical alerts, causes, or prescriptions."}


def multidomain_state(daily, mon, as_of, load_history=None):
    axes = [_axis_state(daily, axis_id, label, mon, as_of, load_history=load_history)
            for axis_id, label in AXES]
    available = [axis for axis in axes if axis["available"]]
    elapsed = max(1, (_day(as_of) - _day(mon)).days + 1)
    mean_reliability = (sum(axis["reliability"] for axis in available) / len(available)
                        if available else 0.0)
    confidence = round(100.0 * (0.6 * mean_reliability + 0.4 * len(available) / len(AXES))
                       * math.sqrt(min(1.0, elapsed / 7.0)), 1)
    axes.append({"id": "data_confidence", "label": "数据质量与模型可信度",
                 "available": True, "index": confidence,
                 "meaning": "Coverage and history sufficiency; not body state."})
    return {"kind": "multidomain_state", "schema_version": 1, "status": "experimental",
            "as_of": as_of, "anchor": 50, "axes": axes,
            "change_points": change_candidates(daily, as_of),
            "warnings": ["50 is a personal historical anchor, not a health threshold.",
                         "Shared inputs are not independent physiological evidence.",
                         "This layer must not independently trigger a prescription."]}
