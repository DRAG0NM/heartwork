#!/usr/bin/env python3
"""Derived insight sections computed from daily rows, activities, events and sleep.

compute(...) returns independent, JSON-serializable sections; each has
available / reason / label / level ("ok" | "watch" | "alert" | "info") plus the
numbers it used. Descriptive only: nothing here is a diagnosis or a prescription.

Implementation choices (not fixed by the literature):
- illness: nights = as_of, -1, -2; baseline = prior 28 days (>= 21 values);
  watch needs the latest two nights both flagged; respiration / skin are optional.
- hrv: normal range = mean +/- 0.5 SD of daily lnRMSSD over as_of-60..as_of-1
  (>= 30 values); CV7 uses sample SD of ln values; typical CV = mean of CV7 ending
  on each of those 60 days (>= 10 values); "elevated" = CV7 > typical + 1 SD of it.
- trend: high load = uncoupled ACWR > 1.3 or TSB < -20. Order of checks:
  fatigue (HRV baseline below range AND (CV elevated OR RHR7 > median28 + 3)),
  saturation (high load AND HRV above range AND RHR7 < median28 AND (recovery7 <=
  median28 - 1 OR mean RPE7 >= 7 OR same-pace HR not lowered by >= 2 bpm)),
  maladaptation (below range OR CV elevated), coping (high load), else stable.
- sleep_regularity: SRI >= 80 ok, 65-80 info, < 65 watch; social jetlag > 1 h watch.
  Night = local date of the evening the sleep began.
- aerobic: runs = type contains "run"/"跑" (or no type); trends compare the last 3
  runs with up to 10 earlier ones (>= 4 runs total, >= 1 earlier); long run =
  >= 45 min with decoupling_pct within 42 days.
- habits: items are (tag, metric) pairs sorted by |diff| / SD of the other days.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta

import baseline
import core_metrics

SECTIONS = [
    ("illness", "身体异常预警"),
    ("hrv", "HRV 趋势"),
    ("trend", "恢复状态判断"),
    ("sleep_regularity", "睡眠规律"),
    ("weight", "体重趋势"),
    ("aerobic", "有氧能力"),
    ("race", "成绩预测"),
    ("habits", "习惯影响"),
]

_shift = baseline._shift
_T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201,
         2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074,
         2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045]
_FLOORS = {"respiration": 0.3, "skin_temp_dev": 0.1}


def _na(reason, label="数据不足"):
    return {"available": False, "reason": reason, "label": label, "level": "info"}


def _mean(values):
    return sum(values) / len(values) if values else None


def _sd(values):
    if len(values) < 2:
        return None
    avg = _mean(values)
    return math.sqrt(sum((v - avg) ** 2 for v in values) / (len(values) - 1))


def _r(value, digits=2):
    return None if value is None else round(value, digits)


def _span(rows, key, as_of, back_first, back_last):
    """Transformed values for days as_of-back_first .. as_of-back_last."""
    return baseline.window(rows, key, _shift(as_of, -back_first), _shift(as_of, -back_last))


def _pace(speed):
    if not speed or speed <= 0:
        return None
    seconds = int(round(1000.0 / speed))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def _hms(seconds):
    seconds = int(round(seconds))
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def _is_run(item):
    kind = str(item.get("type") or "").lower()
    return not kind or "run" in kind or "跑" in kind


def _compact(value):
    return baseline._compact(value)


def _nightly_z(rows, key, day, minimum=21):
    """Value vs the prior 28 days: dict(value, center, scale, z, diff) or None."""
    value = baseline.forward(key, (rows.get(day) or {}).get(key))
    base = _span(rows, key, day, 28, 1)
    if value is None or len(base) < minimum:
        return None
    center = baseline.median(base)
    scale = max(baseline.robust_scale(base, center, key), _FLOORS.get(key, 0.0))
    return {"value": value, "center": center, "scale": scale,
            "z": (value - center) / scale, "diff": value - center}


# 1. illness ---------------------------------------------------------------

def illness(rows, as_of):
    nights = []
    for offset in range(3):
        day = _shift(as_of, -offset)
        rhr = _nightly_z(rows, "resting_hr", day)
        hrv = _nightly_z(rows, "hrv_rmssd", day)
        resp = _nightly_z(rows, "respiration", day, 14)
        skin = _nightly_z(rows, "skin_temp_dev", day, 14)
        flagged = bool(rhr and hrv and (rhr["z"] >= 1.5 or rhr["diff"] >= 3) and hrv["z"] <= -1)
        nights.append({
            "date": day, "flagged": flagged,
            "rhr": _r(rhr and rhr["value"], 1), "rhr_diff": _r(rhr and rhr["diff"], 1),
            "rhr_z": _r(rhr and rhr["z"]),
            "hrv_ms": _r(hrv and baseline.inverse("hrv_rmssd", hrv["value"]), 1),
            "hrv_z": _r(hrv and hrv["z"]),
            "resp_diff": _r(resp and resp["diff"]), "skin_diff": _r(skin and skin["diff"]),
            "_resp": resp, "_skin": skin, "_ok": bool(rhr and hrv)})
    if not nights[0]["_ok"]:
        return _na("最新一晚缺少静息心率或 HRV，或个人基线不足 21 天")
    latest = nights[0]
    two = latest["flagged"] and nights[1]["flagged"]
    extra = bool((latest["_resp"] and latest["_resp"]["diff"] >= 1)
                 or (latest["_skin"] and latest["_skin"]["diff"] >= 0.5))
    consecutive = 0
    for night in nights:
        if not night["flagged"]:
            break
        consecutive += 1
    for night in nights:
        for key in ("_resp", "_skin", "_ok"):
            night.pop(key)
    if two and extra:
        level, label = "alert", ("多项生理指标连续两晚明显偏离个人基线，身体可能在对抗异常"
                                 "（生病、饮酒、旅行或高强度训练后也会出现）")
    elif two:
        level, label = "watch", ("静息心率偏高且 HRV 偏低已连续两晚，身体可能在对抗异常"
                                 "（生病、饮酒、旅行或高强度训练后也会出现）")
    elif latest["flagged"]:
        level, label = "info", "最近一晚指标有偏离，尚未连续，先观察"
    else:
        level, label = "ok", "近三晚静息心率与 HRV 在个人正常范围内"
    return {"available": True, "label": label, "level": level, "nights": nights,
            "consecutive_flagged": consecutive, "extra_signal": extra}


# 2. hrv -------------------------------------------------------------------

def _cv7(rows, day):
    values = _span(rows, "hrv_rmssd", day, 6, 0)
    if len(values) < 5:
        return None
    avg = _mean(values)
    return _sd(values) / avg * 100.0 if avg else None


def hrv(rows, as_of):
    recent = _span(rows, "hrv_rmssd", as_of, 6, 0)
    prior = _span(rows, "hrv_rmssd", as_of, 60, 1)
    if len(recent) < 3 or len(prior) < 30:
        return _na("需要最近 7 天至少 3 个、此前 60 天至少 30 个 HRV 值")
    base, spread = _mean(recent), _sd(prior)
    center = _mean(prior)
    low, high = center - 0.5 * spread, center + 0.5 * spread
    status = "below" if base < low else "above" if base > high else "within"
    cv = _cv7(rows, as_of)
    cvs = [c for c in (_cv7(rows, _shift(as_of, -k)) for k in range(1, 61)) if c is not None]
    typical = _mean(cvs) if len(cvs) >= 10 else None
    cv_sd = _sd(cvs) if typical is not None else None
    elevated = bool(cv is not None and typical is not None and cv_sd is not None
                    and cv > typical + cv_sd)
    norms = []
    for k in range(7):
        row = rows.get(_shift(as_of, -k)) or {}
        rmssd, rhr = row.get("hrv_rmssd"), row.get("resting_hr")
        if baseline._finite(rmssd) and baseline._finite(rhr) and rhr > 0:
            norms.append(rmssd / (60000.0 / rhr))
    inv = lambda v: baseline.inverse("hrv_rmssd", v)
    parts = {"below": "7 天 HRV 基线低于 60 天正常范围", "above": "7 天 HRV 基线高于 60 天正常范围",
             "within": "7 天 HRV 基线在 60 天正常范围内"}
    label = parts[status] + ("，波动（CV）高于平常" if elevated else "")
    level = "watch" if status == "below" or elevated else "ok"
    latest = (rows.get(as_of) or {}).get("hrv_rmssd")
    return {
        "available": True, "label": label, "level": level, "status": status,
        "latest_ms": _r(latest if baseline._finite(latest) else None, 1),
        "baseline7_ln": _r(base, 3), "baseline7_ms": _r(inv(base), 1),
        "range_ln": [_r(low, 3), _r(high, 3)], "range_ms": [_r(inv(low), 1), _r(inv(high), 1)],
        "mean60_ms": _r(inv(center), 1), "sd60_ln": _r(spread, 3),
        "cv7": _r(cv), "cv_typical": _r(typical), "cv_typical_sd": _r(cv_sd),
        "cv_elevated": elevated,
        "normalized_hrv": _r(_mean(norms), 3) if len(norms) >= 3 else None,
        "n_recent": len(recent), "n_prior60": len(prior)}


# 6. aerobic (before trend, which uses it) ----------------------------------

def _compare(runs, value_of, lower_is_better, threshold):
    """Mean of the last 3 vs up to 10 earlier values -> dict or None."""
    values = [v for v in (value_of(run) for run in runs) if v is not None]
    if len(values) < 4:
        return None
    recent, prior = values[-3:], values[-13:-3]
    delta = _mean(recent) - _mean(prior)
    better = -delta if lower_is_better else delta
    status = ("improving" if better >= threshold else "declining" if better <= -threshold
              else "flat")
    return {"recent": _r(_mean(recent), 3), "prior": _r(_mean(prior), 3), "delta": _r(delta, 3),
            "status": status, "n_recent": len(recent), "n_prior": len(prior)}


def aerobic(activities, as_of):
    first = _shift(as_of, -89)
    runs = sorted((a for a in activities if isinstance(a.get("analysis"), dict) and _is_run(a)
                   and first <= _compact(a.get("date", "")) <= as_of),
                  key=lambda a: _compact(a["date"]))
    speeds = [a["analysis"]["gap_speed_mps"] for a in runs
              if baseline._finite(a["analysis"].get("gap_speed_mps")) and a["analysis"]["gap_speed_mps"] > 0]
    out = {"same_pace": None, "ef": None, "long_run": None}
    if speeds:
        ref = baseline.median(speeds)

        def predicted(run):
            fit = run["analysis"].get("hr_speed") or {}
            if not (baseline._finite(fit.get("intercept")) and baseline._finite(fit.get("slope"))):
                return None
            hr = fit["intercept"] + fit["slope"] * ref
            return hr if 80 <= hr <= 210 else None

        cmp_hr = _compare(runs, predicted, True, 2.0)
        if cmp_hr:
            cmp_hr.update(ref_speed_mps=_r(ref, 3), ref_pace=_pace(ref))
            out["same_pace"] = cmp_hr
    ef = _compare(runs, lambda a: a["analysis"].get("ef") if baseline._finite(a["analysis"].get("ef")) else None,
                  False, 0.02)
    out["ef"] = ef
    long_first = _shift(as_of, -41)
    longs = [a for a in runs if _compact(a["date"]) >= long_first
             and baseline._finite(a["analysis"].get("decoupling_pct"))
             and (a["analysis"].get("moving_s") or a.get("duration_s") or 0) >= 2700]
    if longs:
        run = longs[-1]
        pct = run["analysis"]["decoupling_pct"]
        text = "有氧耐久良好" if pct < 5 else "后程有漂移" if pct <= 10 else "耐久不足/环境影响"
        out["long_run"] = {"date": _compact(run["date"]), "decoupling_pct": _r(pct, 1),
                           "label": text, "distance_km": _r((run.get("distance_m") or 0) / 1000.0, 1)}
    if not any(out.values()):
        return _na("需要至少 4 次带心率/速度分析的跑步（近 90 天）")
    notes, level = [], "info"
    if out["same_pace"]:
        s = out["same_pace"]
        notes.append({"improving": "同配速心率下降", "declining": "同配速心率上升",
                      "flat": "同配速心率持平"}[s["status"]])
        level = "ok" if s["status"] == "improving" else "watch" if s["delta"] >= 3 else level
    if out["ef"]:
        notes.append({"improving": "效率因子上升", "declining": "效率因子下降",
                      "flat": "效率因子持平"}[out["ef"]["status"]])
    if out["long_run"]:
        notes.append(out["long_run"]["label"])
        if out["long_run"]["decoupling_pct"] > 10:
            level = "watch"
    out.update(available=True, label="，".join(notes), level=level)
    return out


# 3. trend -----------------------------------------------------------------

def trend(rows, events, loads, as_of, hrv_sec, aerobic_sec):
    if not hrv_sec.get("available"):
        return _na("需要 HRV 趋势数据：" + hrv_sec.get("reason", ""))
    basis, ratio, tsb = [], None, None
    acwr = core_metrics.acwr_uncoupled(loads or {}, as_of) if loads else None
    series = core_metrics.banister_series(loads, as_of) if loads else []
    if acwr:
        ratio = acwr["ratio"]
    if series:
        tsb = series[-1][3]
    high_load = bool((ratio is not None and ratio > 1.3) or (tsb is not None and tsb < -20))
    if high_load:
        basis.append("训练负荷偏高（ACWR %s，TSB %s）" % (ratio, _r(tsb, 1)))
    below, above = hrv_sec["status"] == "below", hrv_sec["status"] == "above"
    cv_up = hrv_sec["cv_elevated"]
    rhr7 = _mean(_span(rows, "resting_hr", as_of, 6, 0))
    rhr_med = baseline.median(_span(rows, "resting_hr", as_of, 28, 1))
    rhr_delta = rhr7 - rhr_med if rhr7 is not None and rhr_med is not None else None
    rec7 = _mean(_span(rows, "subjective_recovery", as_of, 6, 0))
    rec_med = baseline.median(_span(rows, "subjective_recovery", as_of, 28, 1))
    rpes = [e["rpe"] for e in events or [] if baseline._finite(e.get("rpe"))
            and _shift(as_of, -6) <= _compact(e.get("date", "")) <= as_of]
    rpe7 = _mean(rpes)
    sp = (aerobic_sec.get("same_pace") if aerobic_sec.get("available") else None)
    hr_flat = bool(sp and sp["delta"] > -2)
    if below:
        basis.append("HRV 基线低于正常范围")
    if above:
        basis.append("HRV 基线高于正常范围")
    if cv_up:
        basis.append("HRV 波动（CV）高于平常")
    rhr_up = rhr_delta is not None and rhr_delta > 3
    rhr_down = rhr_delta is not None and rhr_delta < 0
    if rhr_up:
        basis.append("7 天静息心率比 28 天中位数高 %.1f bpm" % rhr_delta)
    if rhr_down and above:
        basis.append("7 天静息心率低于 28 天中位数")
    rec_down = rec7 is not None and rec_med is not None and rec7 <= rec_med - 1
    rpe_high = rpe7 is not None and rpe7 >= 7
    if above and high_load:
        if rec_down:
            basis.append("主观恢复明显低于平常")
        if rpe_high:
            basis.append("近 7 天平均 RPE %.1f" % rpe7)
        if hr_flat:
            basis.append("同配速心率没有下降")
    if below and (cv_up or rhr_up):
        kind = "fatigue"
    elif high_load and above and rhr_down and (rec_down or rpe_high or hr_flat):
        kind = "saturation"
    elif below or cv_up:
        kind = "maladaptation"
    elif high_load:
        kind = "coping"
    else:
        kind = "stable"
    names = {"stable": ("稳定", "ok"), "coping": ("应对良好", "ok"),
             "maladaptation": ("适应不良", "watch"), "fatigue": ("疲劳累积", "alert"),
             "saturation": ("疑似功能性过量", "watch")}
    name, level = names[kind]
    return {"available": True, "kind": kind, "label": name, "level": level, "basis": basis,
            "high_load": high_load, "acwr": ratio, "tsb": _r(tsb, 1),
            "rhr7": _r(rhr7, 1), "rhr_median28": _r(rhr_med, 1), "rhr_delta": _r(rhr_delta, 1),
            "recovery7": _r(rec7, 1), "recovery_median28": _r(rec_med, 1), "rpe7": _r(rpe7, 1),
            "same_pace_not_improving": hr_flat}


# 4. sleep regularity --------------------------------------------------------

def _parse(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def sleep_regularity(intervals, as_of):
    first = _shift(as_of, -13)
    kept, nights = [], {}
    for item in intervals or []:
        try:
            start, end = _parse(item["start"]), _parse(item["end"])
        except (KeyError, ValueError, TypeError):
            continue
        if end <= start or not first <= end.strftime("%Y%m%d") <= as_of:
            continue
        kept.append(item)
        end_local = end.astimezone(start.tzinfo) if start.tzinfo and end.tzinfo else end
        night = start.date() if start.hour >= 12 else start.date() - timedelta(days=1)
        mid = start + (end_local - start) / 2
        ref = datetime.combine(night + timedelta(days=1), time(0), tzinfo=start.tzinfo)
        hours = (mid - ref).total_seconds() / 3600.0
        best = nights.get(night)
        if best is None or (end_local - start) > best[1]:
            nights[night] = (hours, end_local - start)
    days = {_parse(i["end"]).strftime("%Y%m%d") for i in kept}
    if len(days) < 5:
        return _na("需要最近 14 天内至少 5 天的主睡眠起止时间")
    sri = core_metrics.sleep_regularity_sri(kept)
    if sri is None:
        return _na("睡眠区间跨度不足 24 小时，无法计算规律指数")
    weekend = [h for n, (h, _) in nights.items() if n.weekday() in (4, 5)]
    other = [h for n, (h, _) in nights.items() if n.weekday() not in (4, 5)]
    jet = abs(_mean(weekend) - _mean(other)) if len(weekend) >= 2 and len(other) >= 2 else None
    clock = lambda h: "%02d:%02d" % (int(h % 24), int(round(h % 1 * 60)) % 60)
    level = "ok" if sri >= 80 else "info" if sri >= 65 else "watch"
    label = "睡眠很规律" if sri >= 80 else "睡眠规律性一般" if sri >= 65 else "睡眠不太规律"
    if jet is not None and jet > 1:
        level, label = "watch", label + "，周末作息偏移超过 1 小时"
    mids = [{"night": n.strftime("%Y%m%d"), "midpoint": clock(h)} for n, (h, _) in sorted(nights.items())]
    return {"available": True, "label": label, "level": level, "sri": sri, "n_days": len(days),
            "social_jetlag_h": _r(jet), "midpoints": mids,
            "mean_midpoint": clock(_mean([h for h, _ in nights.values()]))}


# 5. weight ---------------------------------------------------------------

def weight(rows, as_of):
    first = _shift(as_of, -89)
    weights = {d: r["weight_kg"] for d, r in rows.items()
               if first <= d <= as_of and baseline._finite(r.get("weight_kg")) and r["weight_kg"] > 0}
    if len(weights) < 10:
        return _na("需要近 90 天内至少 10 次体重记录")
    last = max(weights)
    if last < _shift(as_of, -14):
        return _na("最近 14 天没有体重记录")
    trend_by_day, current, day = {}, None, min(weights)
    while day <= as_of:
        w = weights.get(day)
        if w is not None:
            current = w if current is None else current + 0.1 * (w - current)
        trend_by_day[day] = current
        day = _shift(day, 1)
    now, week_ago = trend_by_day[as_of], trend_by_day.get(_shift(as_of, -7))
    if week_ago is None:
        return _na("体重记录跨度不足 7 天")
    change = now - week_ago
    pct = change / week_ago * 100.0
    level = "watch" if pct < -1 else "info"
    label = ("体重趋势每周下降超过 1%，减得偏快" if pct < -1 else
             "体重趋势基本持平" if abs(pct) < 0.3 else "体重趋势在缓慢%s" % ("下降" if pct < 0 else "上升"))
    return {"available": True, "label": label, "level": level, "latest_kg": _r(weights[last], 1),
            "trend_kg": _r(now, 2), "week_change_kg": _r(change), "week_change_pct": _r(pct),
            "n_weighins": len(weights)}


# 7. race -----------------------------------------------------------------

def _vdot(distance, seconds):
    minutes, v = seconds / 60.0, distance / (seconds / 60.0)
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v
    frac = 0.8 + 0.1894393 * math.exp(-0.012778 * minutes) + 0.2989558 * math.exp(-0.1932605 * minutes)
    return vo2 / frac


def race(activities, as_of):
    first = _shift(as_of, -111)
    runs = [a for a in activities if isinstance(a.get("analysis"), dict) and _is_run(a)
            and first <= _compact(a.get("date", "")) <= as_of]
    note = "预测基于接近比赛强度的数据；日常训练数据会低估能力"
    out = {"critical_speed": None, "riegel": None, "vdot": None}
    best = {}
    for run in runs:
        for key, metres in (run["analysis"].get("best_efforts") or {}).items():
            if baseline._finite(metres) and metres > 0 and str(key).isdigit():
                best[int(key)] = max(best.get(int(key), 0), metres)
    if len(best) >= 3:
        xs, ys = list(best), list(best.values())
        mx, my = _mean(xs), _mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        icpt = my - slope * mx
        ss_tot = sum((y - my) ** 2 for y in ys)
        r2 = 1 - sum((y - icpt - slope * x) ** 2 for x, y in zip(xs, ys)) / ss_tot if ss_tot else 0
        if r2 >= 0.95 and slope > 0 and icpt > 0:
            out["critical_speed"] = {
                "cs_mps": _r(slope, 3), "cs_pace": _pace(slope), "dprime_m": _r(icpt, 0),
                "r2": _r(r2, 3), "durations_s": xs,
                "marathon_pace": _pace(0.848 * slope), "marathon_speed_mps": _r(0.848 * slope, 3),
                "half_pace_ceiling": _pace(0.94 * slope), "half_speed_mps": _r(0.94 * slope, 3)}
    times = {}
    for run in runs:
        for key, seconds in (run["analysis"].get("best_times") or {}).items():
            if baseline._finite(seconds) and seconds > 0 and str(key).isdigit():
                times[int(key)] = min(times.get(int(key), seconds), seconds)
    if times:
        long_ones = [d for d in times if d >= 5000]
        dist = max(long_ones) if long_ones else max(times)
        t1 = times[dist]
        d1 = 21097.5 if dist == 21097 else float(dist)
        preds = {}
        for name, d2 in (("5k", 5000.0), ("10k", 10000.0), ("half", 21097.5), ("marathon", 42195.0)):
            t2 = t1 * (d2 / d1) ** 1.06
            preds[name] = {"seconds": round(t2), "time": _hms(t2), "pace": _pace(d2 / t2)}
        out["riegel"] = {"from_distance_m": dist, "from_time": _hms(t1), "predictions": preds}
        out["vdot"] = {"value": _r(_vdot(d1, t1), 1), "from_distance_m": dist}
    if not any(out.values()):
        return _na("需要近 112 天内 3 个以上时长的最佳努力，或 1 km 以上的最佳用时")
    parts = []
    if out["riegel"]:
        p = out["riegel"]["predictions"]
        parts.append("10 公里约 %s、半马约 %s" % (p["10k"]["time"], p["half"]["time"]))
    if out["critical_speed"]:
        parts.append("临界配速 %s/km" % out["critical_speed"]["cs_pace"])
    out.update(available=True, level="info", label="估算：" + "，".join(parts), note=note)
    return out


# 8. habits ---------------------------------------------------------------

def _welch(a, b):
    """(diff, low, high, sd_b) or None."""
    va, vb = _sd(a), _sd(b)
    if va is None or vb is None:
        return None
    sa, sb = va * va / len(a), vb * vb / len(b)
    if sa + sb <= 0:
        return None
    df = (sa + sb) ** 2 / ((sa * sa / (len(a) - 1) if sa else 0) + (sb * sb / (len(b) - 1) if sb else 0))
    crit = 1.96 if df >= 30 else _T975[max(1, int(df)) - 1]
    diff, half = _mean(a) - _mean(b), crit * math.sqrt(sa + sb)
    return diff, diff - half, diff + half, vb


def habits(rows, events, as_of):
    first = _shift(as_of, -89)
    tagged = {}
    for event in events or []:
        day = _compact(event.get("date", ""))
        if first <= day <= as_of:
            for tag in event.get("tags") or []:
                if isinstance(tag, str) and tag:
                    tagged.setdefault(tag, set()).add(day)
    tagged = {t: d for t, d in tagged.items() if len(d) >= 3}
    if not tagged:
        return _na("需要某个标签在近 90 天内出现在至少 3 个不同日期")
    zs = {}
    day = _shift(first, 1)
    while day <= as_of:
        z = _nightly_z(rows, "hrv_rmssd", day, 14)
        zs[day] = z["z"] if z else None
        day = _shift(day, 1)
    sleeps = {d: (rows.get(d) or {}).get("sleep_duration_min") for d in zs}
    metrics = (("hrv_z", "次日 HRV（相对个人基线的 z）", zs, 2),
               ("sleep_min", "次日睡眠时长（分钟）", sleeps, 1))
    items = []
    for tag, dates in sorted(tagged.items()):
        after = {_shift(d, 1) for d in dates}
        for key, title, series, digits in metrics:
            a = [series[d] for d in after if d in series and baseline._finite(series[d])]
            b = [v for d, v in series.items() if d not in after and baseline._finite(v)]
            if len(a) < 3 or len(b) < 3:
                continue
            res = _welch(a, b)
            if res is None:
                continue
            diff, low, high, sd_b = res
            significant = low > 0 or high < 0
            items.append({
                "tag": tag, "metric": key, "title": title, "diff": _r(diff, digits),
                "ci_low": _r(low, digits), "ci_high": _r(high, digits),
                "n_tag": len(a), "n_other": len(b), "significant": significant,
                "level": "watch" if significant else "info",
                "_sort": abs(diff) / sd_b if sd_b else 0})
    if not items:
        return _na("带标签的日子缺少足够的次日 HRV 或睡眠数据")
    items.sort(key=lambda i: -i["_sort"])
    for item in items:
        item.pop("_sort")
    hits = [i for i in items if i["significant"]]
    label = ("%d 项习惯与次日指标有明显关联" % len(hits)) if hits else "暂未发现习惯与次日指标有明显关联"
    return {"available": True, "label": label, "level": "watch" if hits else "info", "items": items}


# entry -------------------------------------------------------------------

def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as error:  # a broken section must not take the others down
        return _na("计算失败：%s" % type(error).__name__, "暂无结果")


def compute(daily, activities, events, sleep_intervals, loads, as_of, profile=None):
    """All sections for as_of; never raises."""
    as_of = _compact(as_of)
    rows = baseline.index_rows(daily or [])
    activities, events = list(activities or []), list(events or [])
    loads = {_compact(k): v for k, v in (loads or {}).items() if baseline._finite(v)}
    hrv_sec = _safe(hrv, rows, as_of)
    aero = _safe(aerobic, activities, as_of)
    return {
        "as_of": as_of,
        "illness": _safe(illness, rows, as_of),
        "hrv": hrv_sec,
        "trend": _safe(trend, rows, events, loads, as_of, hrv_sec, aero),
        "sleep_regularity": _safe(sleep_regularity, sleep_intervals, as_of),
        "weight": _safe(weight, rows, as_of),
        "aerobic": aero,
        "race": _safe(race, activities, as_of),
        "habits": _safe(habits, rows, events, as_of),
    }
