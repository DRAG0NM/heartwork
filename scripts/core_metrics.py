#!/usr/bin/env python3
"""Vendor-neutral, deterministic metrics for personal trend analysis.

All functions are pure and use only the Python standard library. Descriptive
metrics are not injury predictors or medical decisions.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Mapping, Optional, Sequence, Union


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _population_sd(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    avg = _mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / len(values))


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _day(value: Union[str, date]) -> date:
    if isinstance(value, date):
        return value
    compact = value.replace("-", "")
    return datetime.strptime(compact, "%Y%m%d").date()


def efficiency_factor(distance_m: float, duration_s: float, avg_hr_bpm: float):
    """Speed (m/min) divided by average heart rate; higher means faster per bpm."""
    if distance_m <= 0 or duration_s <= 0 or avg_hr_bpm <= 0:
        return None
    speed_m_min = distance_m / (duration_s / 60.0)
    return round(speed_m_min / avg_hr_bpm, 3)


def trimp_edwards(zone_minutes: Sequence[float]):
    """Edwards TRIMP from five heart-rate-zone durations in minutes."""
    if len(zone_minutes) != 5 or any(value < 0 for value in zone_minutes):
        return None
    return round(sum((index + 1) * value for index, value in enumerate(zone_minutes)), 1)


def monotony_strain(daily_loads: Sequence[float]):
    """Monotony and strain from seven calendar-day loads, including rest-day zeros."""
    if len(daily_loads) != 7 or any(value < 0 for value in daily_loads):
        return None
    avg = _mean(daily_loads)
    sd = _population_sd(daily_loads)
    if not avg or not sd:
        return None
    monotony = avg / sd
    weekly_load = sum(daily_loads)
    return {
        "monotony": round(monotony, 2),
        "strain": round(weekly_load * monotony, 1),
        "weekly_load": round(weekly_load, 1),
    }


def _load_window(loads_by_date: Mapping[str, float], as_of: Union[str, date], days: int = 28):
    horizon = _day(as_of)
    parsed = {_day(key): float(value) for key, value in loads_by_date.items() if value is not None}
    if not parsed or min(parsed) > horizon - timedelta(days=21):
        return None
    return [parsed.get(horizon - timedelta(days=offset), 0.0)
            for offset in range(days, 0, -1)]


def acwr(loads_by_date: Mapping[str, float], as_of: Union[str, date]):
    """Experimental 7/28-day mean-load ratio, excluding the incomplete as_of day.

    Coupled (the acute week is part of the chronic window); no longer recommended,
    use acwr_uncoupled.
    """
    sequence = _load_window(loads_by_date, as_of)
    if sequence is None:
        return None
    acute = sequence[-7:]
    acute_mean = _mean(acute)
    chronic_mean = _mean(sequence)
    if not chronic_mean:
        return None
    return {
        "ratio": round(acute_mean / chronic_mean, 2),
        "acute_7d": round(sum(acute), 1),
        "chronic_week_equivalent": round(chronic_mean * 7, 1),
        "experimental": True,
    }


def _ewma(values: Sequence[float], smoothing: float):
    if not values:
        return None
    current = float(values[0])
    for value in values[1:]:
        current = smoothing * float(value) + (1.0 - smoothing) * current
    return current


def ewma_acwr(loads_by_date: Mapping[str, float], as_of: Union[str, date],
              acute_lambda: float = 0.25, chronic_lambda: float = 2.0 / 29.0):
    """Experimental dual-time-constant EWMA ratio over the same 28-day sequence.

    Coupled (acute and chronic share data); no longer recommended, use banister.
    """
    sequence = _load_window(loads_by_date, as_of)
    if sequence is None:
        return None
    acute = _ewma(sequence, acute_lambda)
    chronic = _ewma(sequence, chronic_lambda)
    if not chronic:
        return None
    return {
        "ratio": round(acute / chronic, 2),
        "acute_ewma": round(acute, 2),
        "chronic_ewma": round(chronic, 2),
        "acute_lambda": round(acute_lambda, 3),
        "chronic_lambda": round(chronic_lambda, 3),
        "experimental": True,
    }


def acwr_uncoupled(loads_by_date: Mapping[str, float], as_of: Union[str, date]):
    """Acute = mean of the last 7 days (incl. as_of); chronic = mean of days 8-35, no overlap."""
    horizon = _day(as_of)
    parsed = {_day(key): float(value) for key, value in loads_by_date.items() if value is not None}
    if not parsed or min(parsed) > horizon - timedelta(days=21):
        return None
    acute = [parsed.get(horizon - timedelta(days=offset), 0.0) for offset in range(7)]
    chronic = [parsed.get(horizon - timedelta(days=offset), 0.0) for offset in range(7, 35)]
    chronic_mean = _mean(chronic)
    if not chronic_mean:
        return None
    return {"ratio": round(_mean(acute) / chronic_mean, 2),
            "acute_mean": round(_mean(acute), 2), "chronic_mean": round(chronic_mean, 2),
            "experimental": True}


def banister_series(loads_by_date: Mapping[str, float], as_of: Union[str, date],
                    atl_days: int = 7, ctl_days: int = 42):
    """Daily (date, atl, ctl, tsb) from the first load day to as_of; EWMA seeded at 0.

    Coefficient 1-exp(-1/tau); days without a load count as 0; tsb = ctl - atl.
    """
    horizon = _day(as_of)
    parsed = {_day(key): float(value) for key, value in loads_by_date.items()
              if value is not None and _day(key) <= horizon}
    if not parsed:
        return []
    k_atl, k_ctl = 1 - math.exp(-1.0 / atl_days), 1 - math.exp(-1.0 / ctl_days)
    atl = ctl = 0.0
    series = []
    day = min(parsed)
    while day <= horizon:
        load = parsed.get(day, 0.0)
        atl += k_atl * (load - atl)
        ctl += k_ctl * (load - ctl)
        series.append((day.strftime("%Y%m%d"), atl, ctl, ctl - atl))
        day += timedelta(days=1)
    return series


def banister(loads_by_date: Mapping[str, float], as_of: Union[str, date],
             atl_days: int = 7, ctl_days: int = 42):
    """Banister impulse-response: fatigue (atl), fitness (ctl) and form (tsb)."""
    series = banister_series(loads_by_date, as_of, atl_days, ctl_days)
    if not series:
        return None
    _, atl, ctl, tsb = series[-1]
    return {"atl": round(atl, 2), "ctl": round(ctl, 2), "tsb": round(tsb, 2)}


def _iso_datetime(value: str):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def sleep_regularity_sri(sleep_intervals: Iterable[Mapping[str, str]]):
    """Formal SRI: -100 + 200 × agreement of sleep/wake state 24 hours apart."""
    intervals = [(_iso_datetime(item["start"]), _iso_datetime(item["end"]))
                 for item in sleep_intervals]
    intervals = [(start, end) for start, end in intervals if end > start]
    if len(intervals) < 2:
        return None

    # Sleep intervals imply continuous observation only between the first known
    # sleep onset and the last known wake. Extending to a surrounding midnight
    # would invent an unrecorded final sleep interval and bias SRI downward.
    window_start = int(min(start for start, _ in intervals).timestamp() // 60)
    window_end = int(max(end for _, end in intervals).timestamp() // 60)
    if window_end - window_start <= 24 * 60:
        return None

    asleep = set()
    for start, end in intervals:
        first = int(start.timestamp() // 60)
        last = int(math.ceil(end.timestamp() / 60.0))
        asleep.update(range(first, last))

    offset = 24 * 60
    total = window_end - window_start - offset
    agreement = sum((minute in asleep) == (minute + offset in asleep)
                    for minute in range(window_start, window_end - offset))
    return round(-100.0 + 200.0 * agreement / total, 1)


def _circular_sd_minutes(minutes: Sequence[float]):
    if len(minutes) < 2:
        return None
    angles = [2.0 * math.pi * (value % 1440.0) / 1440.0 for value in minutes]
    mean_cos = _mean([math.cos(value) for value in angles])
    mean_sin = _mean([math.sin(value) for value in angles])
    resultant = math.sqrt(mean_cos ** 2 + mean_sin ** 2)
    if resultant <= 1e-12:
        return 720.0
    sd_radians = math.sqrt(max(0.0, -2.0 * math.log(min(1.0, resultant))))
    return sd_radians * 1440.0 / (2.0 * math.pi)


def sleep_consistency(sleep_intervals: Iterable[Mapping[str, str]]):
    """Circular SD of sleep start/end clock times; handles midnight correctly."""
    starts, ends = [], []
    for item in sleep_intervals:
        start, end = _iso_datetime(item["start"]), _iso_datetime(item["end"])
        starts.append(start.hour * 60 + start.minute + start.second / 60.0)
        ends.append(end.hour * 60 + end.minute + end.second / 60.0)
    start_sd = _circular_sd_minutes(starts)
    end_sd = _circular_sd_minutes(ends)
    if start_sd is None or end_sd is None:
        return None
    return {"sleep_start_sd_min": round(start_sd, 1), "wake_sd_min": round(end_sd, 1)}


def recovery_composite(hrv_score: Optional[float], sleep_efficiency_score: Optional[float],
                       stress_recovery_score: Optional[float] = None,
                       weights: Optional[Mapping[str, float]] = None):
    """Transparent custom 0-100 composite; missing components renormalize weights."""
    configured = dict(weights or {"hrv": 0.5, "sleep": 0.3, "stress": 0.2})
    values = {"hrv": hrv_score, "sleep": sleep_efficiency_score,
              "stress": stress_recovery_score}
    available = {name: _clamp(float(value), 0.0, 1.0)
                 for name, value in values.items() if value is not None and name in configured}
    total_weight = sum(configured[name] for name in available)
    if not available or total_weight <= 0:
        return None
    normalized = {name: configured[name] / total_weight for name in available}
    score = sum(available[name] * normalized[name] for name in available) * 100.0
    return {"score": round(score, 1), "weights": {k: round(v, 3) for k, v in normalized.items()},
            "experimental": True}
