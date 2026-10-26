#!/usr/bin/env python3
"""Build an Obsidian-friendly weekly report from vendor-neutral JSON input."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from core_metrics import (acwr, efficiency_factor, ewma_acwr, monotony_strain,
                          sleep_consistency, sleep_regularity_sri, trimp_edwards)
from state_layers import multidomain_state, raw_signal_snapshot

AUTO_BEGIN = "<!-- 自动报告开始 -->"
AUTO_END = "<!-- 自动报告结束 -->"
NOTES_HEADER = "## 用户备注"
NOTES_PLACEHOLDER = "<!-- 主观感受、RPE、疼痛、天气与计划调整；自动重建不会覆盖此区域 -->"


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _atomic_write(path, content):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(temporary, path)


def _date_label(compact):
    return datetime.strptime(compact, "%Y%m%d").strftime("%Y-%m-%d")


def validate_input(payload):
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    week = payload.get("week") or {}
    for field in ("mon", "sun", "as_of"):
        if not isinstance(week.get(field), str) or len(week[field].replace("-", "")) != 8:
            raise ValueError("week.%s must be YYYYMMDD" % field)
    if week["as_of"] < week["mon"] or week["as_of"] > week["sun"]:
        raise ValueError("week.as_of must be inside the week")
    for activity in payload.get("activities", []):
        zones = activity.get("hr_zone_minutes")
        if zones is not None and (len(zones) != 5 or any(value < 0 for value in zones)):
            raise ValueError("activity.hr_zone_minutes must contain five non-negative values")
    return payload


def render_report(payload):
    validate_input(payload)
    week = payload["week"]
    partial = bool(week.get("partial") or week["as_of"] < week["sun"])
    activities = payload.get("activities", [])
    daily = payload.get("daily", [])
    all_daily = list(payload.get("history_daily", [])) + list(daily)
    intervals = payload.get("sleep_intervals", [])
    loads = payload.get("load_history", {})

    lines = ["---", "type: weekly-report", "week: %s-%s" % (week["mon"], week["sun"]),
             "as_of: %s" % week["as_of"], "partial: %s" % str(partial).lower(),
             "synthetic: %s" % str(bool(payload.get("synthetic"))).lower(),
             "tags: [运动评估, 周报告]", "---", "",
             "# 周报告 %s-%s" % (week["mon"], week["sun"][-2:]), "",
             "> 自动生成 · %s ~ %s · 截至 %s%s" % (
                 _date_label(week["mon"]), _date_label(week["sun"]), _date_label(week["as_of"]),
                 "，当前周未完" if partial else ""), ""]
    if payload.get("synthetic"):
        lines.extend(["> [!NOTE] 合成示例数据", "> 本页只用于演示结构和算法，不对应任何真实个人。", ""])

    lines.extend(["## 每日概览", "", "| 日期 | 睡眠h | 睡眠分 | 静息心率 | 主观恢复 |",
                  "|---|---:|---:|---:|---:|"])
    for row in daily:
        duration = row.get("sleep_duration_min")
        lines.append("| %s | %s | %s | %s | %s |" % (
            _date_label(row["date"]), "%.1f" % (duration / 60.0) if duration is not None else "-",
            row.get("sleep_score", "-"), row.get("resting_hr", "-"),
            row.get("subjective_recovery", "-")))
    if not daily:
        lines.append("| 无数据 | - | - | - | - |")

    lines.extend(["", "## 原始单指标（无阈值、无加权）", "",
                  "| 指标 | 最新值 | 本周均值 | 前28日均值 | 差值 | 样本 |",
                  "|---|---:|---:|---:|---:|---:|"])
    raw_signals = raw_signal_snapshot(all_daily, week["mon"], week["as_of"])
    for card in raw_signals:
        lines.append("| %s | %s %s | %s | %s | %s | %d/%d |" % (
            card["label"], card["latest"], card["unit"], card["week_mean"],
            card["baseline_28d_mean"] if card["baseline_28d_mean"] is not None else "-",
            card["delta_vs_baseline"] if card["delta_vs_baseline"] is not None else "-",
            card["n_week"], card["n_baseline"]))
    if not raw_signals:
        lines.append("| 无本周信号 | - | - | - | - | 0/0 |")

    lines.extend(["", "## 活动", "", "| 日期 | 类型 | 距离km | 时长min | 平均心率 | EF | Edwards TRIMP |",
                  "|---|---|---:|---:|---:|---:|---:|"])
    activity_loads = []
    for activity in activities:
        trimp = trimp_edwards(activity["hr_zone_minutes"]) if activity.get("hr_zone_minutes") else None
        if trimp is not None:
            activity_loads.append(trimp)
        ef = efficiency_factor(activity.get("distance_m", 0), activity.get("duration_s", 0),
                               activity.get("avg_hr", 0))
        lines.append("| %s | %s | %.2f | %.1f | %s | %s | %s |" % (
            _date_label(activity["date"]), activity.get("type", "运动"),
            activity.get("distance_m", 0) / 1000.0, activity.get("duration_s", 0) / 60.0,
            activity.get("avg_hr", "-"), "%.3f" % ef if ef is not None else "-",
            "%.1f" % trimp if trimp is not None else "-"))
    if not activities:
        lines.append("| 无活动 | - | - | - | - | - | - |")

    lines.extend(["", "## 负荷趋势", ""])
    rolling = acwr(loads, week["as_of"])
    ewma = ewma_acwr(loads, week["as_of"])
    if rolling:
        lines.append("- ACWR（实验）：%.2f（近 7 天 %.1f / 近 28 天周当量 %.1f）" % (
            rolling["ratio"], rolling["acute_7d"], rolling["chronic_week_equivalent"]))
    else:
        lines.append("- ACWR：历史跨度不足或慢性负荷为 0，不计算")
    if ewma:
        lines.append("- EWMA ACWR（实验）：%.2f（λa=%.3f / λc=%.3f）" % (
            ewma["ratio"], ewma["acute_lambda"], ewma["chronic_lambda"]))
    current_week_daily = payload.get("current_week_daily_loads")
    mono = monotony_strain(current_week_daily) if current_week_daily is not None else None
    if mono:
        lines.append("- 单调性/应变（实验）：%.2f / %.1f（周负荷 %.1f）" % (
            mono["monotony"], mono["strain"], mono["weekly_load"]))
    elif current_week_daily is not None:
        lines.append("- 单调性/应变：负荷全零、完全相同或输入不足，不计算")
    lines.append("- 这些负荷指标只描述变化，不提供通用安全区，也不能单独决定增减量。")

    state = multidomain_state(all_daily, week["mon"], week["as_of"],
                              payload.get("load_history"))
    lines.extend(["", "## 多维个人状态（实验）", "",
                  "| 维度 | 个人相对指数 | 可靠度 | 状态 |", "|---|---:|---:|---|"])
    for axis in state["axes"]:
        lines.append("| %s | %s | %s | %s |" % (
            axis["label"], axis.get("index", "-"), axis.get("reliability", "-"),
            "可用" if axis.get("available") else "历史不足"))
    lines.append("- 50 是个人历史中性锚点，不是健康及格线；多轴共享输入，不是独立生理证据。")
    lines.append("- 本层和变化候选均不得单独触发训练处方或医学判断。")

    lines.extend(["", "## 睡眠规律", ""])
    sri = sleep_regularity_sri(intervals) if intervals else None
    consistency = sleep_consistency(intervals) if intervals else None
    if sri is not None:
        lines.append("- SRI：%.1f（理论范围 -100~100；与个人基线比较）" % sri)
    else:
        lines.append("- SRI：有效睡眠区间不足")
    if consistency:
        lines.append("- 作息一致性：入睡 SD %.1f min / 醒来 SD %.1f min" % (
            consistency["sleep_start_sd_min"], consistency["wake_sd_min"]))
    else:
        lines.append("- 作息一致性：有效睡眠区间不足")

    lines.extend(["", "## 数据边界", "",
                  "- 本报告只使用输入 JSON 中的字段；缺失值不会被推断或填造。",
                  "- 示例与公开贡献不得包含真实健康、账号或位置数据。",
                  "- 结论属于个人趋势描述，不是医学诊断或训练处方。", ""])
    return "\n".join(lines)


def upsert_report(path, generated):
    frontmatter = ""
    body = generated
    if generated.startswith("---\n"):
        boundary = generated.find("\n---\n", 4)
        if boundary >= 0:
            frontmatter = generated[:boundary + 5].rstrip()
            body = generated[boundary + 5:].lstrip("\n")
    block = AUTO_BEGIN + "\n" + body.rstrip() + "\n" + AUTO_END
    try:
        with open(path, encoding="utf-8") as handle:
            previous = handle.read()
    except OSError:
        previous = ""
    if AUTO_BEGIN in previous and AUTO_END in previous:
        tail = previous.split(AUTO_END, 1)[1].lstrip("\n")
    else:
        tail = NOTES_HEADER + "\n\n" + NOTES_PLACEHOLDER + "\n"
    if NOTES_HEADER not in tail:
        tail = NOTES_HEADER + "\n\n" + NOTES_PLACEHOLDER + "\n\n" + tail
    prefix = frontmatter + "\n\n" if frontmatter else ""
    content = prefix + block + "\n\n" + tail.rstrip() + "\n"
    _atomic_write(path, content)


def main(argv=None):
    parser = argparse.ArgumentParser(description="从通用 JSON 生成 Obsidian 周报告")
    parser.add_argument("input", help="输入 JSON 路径")
    parser.add_argument("--output", help="输出 Markdown 路径")
    args = parser.parse_args(argv)
    payload = validate_input(_read_json(args.input))
    root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    week = payload["week"]
    output = args.output or os.path.join(root, "06_周报告", "周报告_%s-%s.md" % (
        week["mon"], week["sun"][-2:]))
    upsert_report(output, render_report(payload))
    print("已生成: %s" % os.path.abspath(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
