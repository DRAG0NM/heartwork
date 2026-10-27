#!/usr/bin/env python3
"""Morning brief: one thing to act on today plus three supporting lines, all deterministic.

Built from the safety gate, the body judgments and the baseline view; no model call.
`notify` shows it as a desktop notification; `schedule_command` prints how to run it daily.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys

import api

TREND_TEXT = {
    "fatigue": "疲劳在累积：今天休息或只做轻松活动",
    "saturation": "HRV 偏高但表现和主观感受在下降，疑似功能性过量：先减量",
    "maladaptation": "身体对近期训练适应不佳：降低强度，保证睡眠",
    "coping": "负荷较高但身体应对良好：按计划练，别再加量",
}


def _one_thing(gate, body, recovery):
    if gate["level"] == "red":
        return "出现危险信号：停止训练，尽快就医"
    illness = body.get("illness") or {}
    if illness.get("level") in ("alert", "watch"):
        return "夜间心率和 HRV 偏离常态，身体可能在对抗异常：今天休息，留意症状"
    if gate["level"] == "yellow":
        return "有疼痛或不适记录：只做保护性活动，不加量"
    trend = (body.get("trend") or {}).get("kind")
    if trend in TREND_TEXT:
        return TREND_TEXT[trend]
    if recovery == "better":
        return "恢复好于你的常态：可以按计划完成今天的训练"
    if recovery == "worse":
        return "恢复低于你的常态：把强度降一档"
    return "身体在正常波动内：按计划训练"


def build(base=None, as_of=None):
    snap = api.snapshot(base, as_of)
    as_of = snap["week"]["as_of"]
    body = api.insights(base, as_of)
    view = api.daily_view(base, as_of, days=1)
    day = view["days"][-1] if view["days"] else {}
    recovery = ((day.get("families") or {}).get("recovery") or {}).get("oriented")
    lines = []
    for key in ("hrv", "sleep_regularity", "trend", "aerobic", "compliance", "race"):
        section = body.get(key) or {}
        if section.get("available") and section.get("label"):
            lines.append(section["label"])
    return {"as_of": as_of, "gate": snap["safety_gate"]["level"],
            "one_thing": _one_thing(snap["safety_gate"], body, recovery), "lines": lines[:3]}


def text(brief):
    return "\n".join(["今日一件事：%s" % brief["one_thing"]] + ["· %s" % line for line in brief["lines"]])


def notify(brief):
    """Best-effort desktop notification; returns False when the platform has no way to show one."""
    title, body = "Heartwork · %s" % brief["one_thing"][:40], "\n".join(brief["lines"]) or brief["one_thing"]
    system = platform.system()
    try:
        if system == "Windows":
            def ps(value):
                return value.replace("'", "''").replace("&", "&amp;").replace("<", "&lt;")
            script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null;"
                "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
                "ContentType = WindowsRuntime] > $null;"
                "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
                "$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>%s</text>"
                "<text>%s</text></binding></visual></toast>');"
                "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app)"
                ".Show([Windows.UI.Notifications.ToastNotification]::new($x))" % (ps(title), ps(body)))
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           check=True, capture_output=True, timeout=20)
        elif system == "Darwin":
            subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s"' % (
                body.replace('"', "'"), title.replace('"', "'"))], check=True, timeout=20)
        else:
            subprocess.run(["notify-send", title, body], check=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def schedule_command(at="07:30", home=None):
    """The command that registers a daily brief; printed for the user to run, never run here."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heartwork.py")
    args = '"%s" "%s"%s brief --notify' % (sys.executable, script, ' --home "%s"' % home if home else "")
    if platform.system() == "Windows":
        return 'schtasks /Create /SC DAILY /ST %s /TN "Heartwork 晨报" /TR "%s" /F' % (at, args.replace('"', '\\"'))
    hour, minute = at.split(":")
    return '(crontab -l; echo "%s %s * * * %s") | crontab -' % (int(minute), int(hour), args)
