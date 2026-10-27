"""Stream-derived metrics from per-point activity data (time, HR, distance, altitude).

Points are dicts {"t": epoch s, "hr": int|None, "d": cumulative m|None, "alt": m|None}.
Everything is resampled to 1 Hz first; gaps longer than 30 s count as pauses.
"""
from __future__ import annotations

import math
from operator import sub

MIN_POINTS = 60
MIN_SECONDS = 600
MAX_GAP = 30
MOVING_MPS = 0.5
EFFORT_WINDOWS = (180, 360, 720, 1200)
EFFORT_DISTANCES = (1000, 5000, 10000, 21097)


def _resample(pts, t0, n):
    """1 Hz linear interpolation of (t, v) pairs; returns (values, ok) with ok False in gaps/outside."""
    values, ok = [0.0] * n, bytearray(n)
    if not pts:
        return values, ok
    k, last = 0, len(pts) - 1
    for s in range(n):
        t = t0 + s
        if t <= pts[0][0]:
            values[s] = pts[0][1]
            ok[s] = t == pts[0][0]
            continue
        if t >= pts[last][0]:
            values[s] = pts[last][1]
            ok[s] = t == pts[last][0]
            continue
        while pts[k + 1][0] < t:
            k += 1
        (ta, va), (tb, vb) = pts[k], pts[k + 1]
        if tb == t:
            values[s], ok[s] = vb, 1
        elif tb - ta <= MAX_GAP:
            values[s], ok[s] = va + (vb - va) * (t - ta) / (tb - ta), 1
        else:
            values[s] = va + (vb - va) * (t - ta) / (tb - ta)
    return values, ok


def _smooth(values, radius):
    """Centred moving average with edge-truncated windows via prefix sums."""
    n = len(values)
    prefix = [0.0] * (n + 1)
    for i, v in enumerate(values):
        prefix[i + 1] = prefix[i] + v
    out = []
    for i in range(n):
        lo, hi = max(0, i - radius), min(n, i + radius + 1)
        out.append((prefix[hi] - prefix[lo]) / (hi - lo))
    return out


def _cost(i):
    return 155.4 * i ** 5 - 30.4 * i ** 4 - 43.3 * i ** 3 + 46.3 * i ** 2 + 19.5 * i + 3.6


def _grades(dist, alt):
    """Per-second grade from altitude change over >=10 m distance steps, clipped to +-0.45."""
    n = len(dist)
    grade = [0.0] * n
    j = 0
    for i in range(1, n):
        span = dist[i] - dist[j]
        if span >= 10:
            g = max(-0.45, min(0.45, (alt[i] - alt[j]) / span))
            for k in range(j, i):
                grade[k] = g
            j = i
    return grade


def _ef(speed, hr, idx):
    """Efficiency factor (m/min per bpm) over the seconds in idx that have HR."""
    pairs = [(speed[i], hr[i]) for i in idx if hr[i] is not None]
    if not pairs:
        return None
    mean_hr = sum(h for _, h in pairs) / len(pairs)
    return sum(s for s, _ in pairs) / len(pairs) * 60 / mean_hr if mean_hr else None


def _best_times(dist):
    n, out = len(dist), {}
    for target in EFFORT_DISTANCES:
        if dist[-1] - dist[0] < target:
            continue
        j, best = 0, None
        for i in range(1, n):
            while j + 1 <= i and dist[i] - dist[j + 1] >= target:
                j += 1
            if dist[i] - dist[j] >= target and (best is None or i - j < best):
                best = i - j
        if best:
            out[str(target)] = best
    return out


def _hr_speed(gap, hr, ok_d):
    xs, ys = [], []
    for s in range(0, len(gap) - 29, 30):
        window = range(s, s + 30)
        if any(hr[i] is None or not ok_d[i] for i in window):
            continue
        speed = sum(gap[s:s + 30]) / 30
        if 1.5 <= speed <= 7:
            xs.append(speed)
            ys.append(sum(hr[s:s + 30]) / 30)
    n = len(xs)
    if n < 10:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    r2 = sxy * sxy / (sxx * syy) if syy > 0 else 0.0
    return {"intercept": round(my - slope * mx, 2), "slope": round(slope, 3), "r2": round(r2, 3), "n": n}


def analyze(points, profile=None):
    """Compute stream metrics for one activity; None when too short or too sparse."""
    pts = sorted((p for p in points if p.get("t") is not None), key=lambda p: p["t"])
    if len(pts) < MIN_POINTS or pts[-1]["t"] - pts[0]["t"] < MIN_SECONDS:
        return None
    t0 = math.ceil(pts[0]["t"])
    n = int(math.floor(pts[-1]["t"]) - t0) + 1
    if n < MIN_SECONDS:
        return None
    d_pts = [(p["t"], p["d"]) for p in pts if p.get("d") is not None]
    a_pts = [(p["t"], p["alt"]) for p in pts if p.get("alt") is not None]
    h_pts = [(p["t"], p["hr"]) for p in pts if p.get("hr")]
    hr_raw, hr_ok = _resample(h_pts, t0, n)
    hr = [int(round(v)) if ok else None for v, ok in zip(hr_raw, hr_ok)]
    result = {"moving_s": None, "elevation_gain_m": None, "gap_speed_mps": None, "ef": None,
              "decoupling_pct": None, "best_efforts": {}, "best_times": {}, "hr_speed": None,
              "hr_coverage": None}
    alt = None
    if len(a_pts) >= 2:
        alt_raw, _ = _resample(a_pts, t0, n)
        alt = _smooth(alt_raw, 7)
        result["elevation_gain_m"] = round(sum(max(0.0, b - a) for a, b in zip(alt, alt[1:])), 1)
    if len(d_pts) < 2 or d_pts[-1][1] <= d_pts[0][1]:
        have = sum(1 for v in hr if v is not None)
        result["hr_coverage"] = round(have / n, 3)
        return result
    top, mono = d_pts[0][1], []
    for t, v in d_pts:
        top = max(top, v)
        mono.append((t, top))
    dist, ok_d = _resample(mono, t0, n)
    speed = [dist[i + 1] - dist[i] if ok_d[i] and ok_d[i + 1] else 0.0 for i in range(n - 1)]
    speed.append(speed[-1])
    moving = [i for i in range(n) if speed[i] > MOVING_MPS]
    result["best_efforts"] = {str(w): round(max(map(sub, dist[w:], dist)), 1)
                              for w in EFFORT_WINDOWS if n - 1 >= w}
    result["best_times"] = _best_times(dist)
    if not moving:
        result["moving_s"] = 0
        result["hr_coverage"] = 0.0
        return result
    if alt is not None:
        grade = _grades(dist, alt)
        gap = [v * _cost(g) / 3.6 for v, g in zip(speed, grade)]
    else:
        gap = speed
    covered = [i for i in moving if hr[i] is not None]
    coverage = len(covered) / len(moving)
    result["moving_s"] = len(moving)
    result["gap_speed_mps"] = round(sum(gap[i] for i in moving) / len(moving), 3)
    result["hr_coverage"] = round(coverage, 3)
    if coverage >= 0.8:
        ef = _ef(gap, hr, moving)
        result["ef"] = round(ef, 3) if ef else None
        if len(moving) >= 2400 and ef:
            half = len(moving) // 2
            first, second = _ef(gap, hr, moving[:half]), _ef(gap, hr, moving[half:])
            if first and second:
                result["decoupling_pct"] = round((first - second) / first * 100, 2)
    result["hr_speed"] = _hr_speed(gap, hr, ok_d)
    return result
