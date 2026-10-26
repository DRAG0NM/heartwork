#!/usr/bin/env python3
"""Signed coach jobs: build context, hand it to any model, verify what comes back."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime

import coach_decisions
import coach_events
import coach_policy
import coach_skills
import store
from coach_core import RED_FLAGS, TASK_ACTIONS, build_context, stable_hash, validate_response

WEEKLY_WORDS = ("周复盘", "周报", "本周", "上周", "下周", "每周", "weekly", "复盘")
WORKOUT_WORDS = ("刚练完", "练完", "跑完", "运动后", "训练后", "刚跑", "post workout")
PAIN_WORDS = ("疼", "痛", "受伤", "肿", "无法承重", "胸痛", "心悸", "晕厥", "pain")
JOB_ID = re.compile(r"^job_[0-9]{8}_[0-9]{6}_[0-9a-f]{6}$")


def choose_task(query, events=None):
    latest = None
    for event in events or []:
        if latest is None or (event.get("date", ""), event.get("recorded_at", "")) >= (
                latest.get("date", ""), latest.get("recorded_at", "")):
            latest = event
    if latest:
        pain = latest.get("pain")
        if set(latest.get("red_flags") or []) & RED_FLAGS or (
                isinstance(pain, (int, float)) and pain > 0):
            return "pain_followup"
    text = (query or "").lower()
    if any(word in text for word in PAIN_WORDS):
        return "pain_followup"
    if any(word in text for word in WEEKLY_WORDS):
        return "weekly_review"
    if any(word in text for word in WORKOUT_WORDS):
        return "post_workout"
    return "daily_guidance"


def _jobs_dir(base):
    return os.path.join(store.home(base), "jobs")


def _job_path(base, job_id):
    if not JOB_ID.match(str(job_id)):
        raise ValueError("invalid job id")
    return os.path.join(_jobs_dir(base), job_id + ".json")


def _secret(base):
    path = os.path.join(store.home(base), "secret.key")
    try:
        with open(path, encoding="ascii") as handle:
            return handle.read().strip().encode("ascii")
    except FileNotFoundError:
        os.makedirs(store.home(base), exist_ok=True)
        key = secrets.token_hex(32)
        with open(path, "w", encoding="ascii") as handle:
            handle.write(key)
        return key.encode("ascii")


def _sign(base, job):
    body = stable_hash([job["job_id"], job["task"], job["source_sha256"],
                        job["context"].get("content_sha256")])
    return hmac.new(_secret(base), body.encode("ascii"), hashlib.sha256).hexdigest()


def _instructions(task, context):
    policy = context["policy"]
    level = policy["level"]
    lines = [
        "你是运动恢复教练的推理层，只能依据下面的 coach_context 回答。",
        "1. 只输出一个 Response v2 JSON 对象，不要输出其他文字。",
        "2. kind=coach_response, schema_version=2, task=%s, as_of=%s。" % (task, context["as_of"]),
        "3. 每条 observation 与 recommendation 必须带 evidence：{source:\"coach_context\", "
        "pointer:JSON Pointer, layer, observed_value}，observed_value 必须与指针处的值完全一致。",
        "4. layer 对应：/views/raw_signals、/views/recent_daily=raw_signal；/views/state=experimental_model；"
        "/views/baseline_status=deterministic_metric；/recent_events=user_event；"
        "/safety_gate=deterministic_metric；/views/current_plan=plan。",
        "5. 当前权限档位：%s（%s）。" % (level, coach_policy.LEVEL_LABELS[level]),
    ]
    if level == "conservative":
        lines += ["   - 实验层（/views/state）证据不得出现在 recommendation 的 evidence 里。",
                  "   - 禁止提出改计划的建议（changes_plan 一律不可用），不得使用 suggest_session、draft_plan。",
                  "   - 建议置信度最高 medium；每条建议必须含 raw_signal 层证据。"]
    elif level == "balanced":
        lines += ["   - 实验层证据只能辅助，不能单独支撑处方，也不能给 high 置信度。",
                  "   - 修改计划的建议必须 changes_plan=true 且 requires_confirmation=true，由用户在网页或 CLI 确认。"]
    else:
        lines += ["   - 实验层证据可与原始证据一起支撑建议，但不能单独支撑，更不能单独支撑 suggest_session、draft_plan。",
                  "   - 改计划建议默认 requires_confirmation=true。若你有把握，可设 requires_confirmation=false "
                  "并附 plan_text（完整新计划，不超过 %d 字）；仅当安全门为 green、该建议含 raw_signal 证据、"
                  "置信度 medium/high、且 %d 小时内没有自动应用过时才会自动生效，否则退化为待用户确认。"
                  "自动生效后用户 %d 天内可撤销。" % (coach_policy.PLAN_TEXT_LIMIT, coach_policy.AUTO_WINDOW_HOURS,
                                                coach_policy.UNDO_DAYS),
                  "   - daily_guidance / post_workout 也可使用 draft_plan。"]
    lines += [
        "6. action_type 只能取：%s。" % ", ".join(policy["allowed_action_types"]),
        "7. risks 的最高等级不得低于 safety_gate.level（当前 %s）。红灯只能建议 seek_emergency_help，"
        "黄灯不得 maintain/suggest_session/draft_plan。" % context["safety_gate"]["level"],
        "8. 趋势判断（高于/低于/持平个人基线）必须在 observation 里写 claims："
        "[{\"metric\": <baseline_status 的 key>, \"direction\": \"above|below|within\"}]，"
        "direction 必须等于 /views/baseline_status 中该项的 status；方向按数值高低，不按好坏"
        "（静息心率、压力偏高就是 above）。statement 里的方向词（偏高/下降/正常范围等）必须与 claims 一致，"
        "每条观察只讲一个方向。",
        "9. 不做医学诊断；数据缺失就提问，不要编造。",
    ]
    return "\n".join(lines)


def start_job(base, query, task=None, as_of=None):
    payload = store.weekly_input(base, as_of)
    task = task or choose_task(query, coach_events.active_events(base))
    context = build_context(payload, task, query or "", coach_policy.get_level(base))
    now = datetime.now()
    job_id = "job_%s_%s" % (now.strftime("%Y%m%d_%H%M%S"),
                            stable_hash([context["content_sha256"], now.isoformat()])[:6])
    job = {"kind": "coach_job", "job_id": job_id, "task": task,
           "skills": coach_skills.route(task, query), "context": context,
           "source_sha256": stable_hash(payload),
           "created_at": now.isoformat(timespec="seconds"),
           "instructions": _instructions(task, context)}
    job["signature"] = _sign(base, job)
    store.write_json(_job_path(base, job_id), job)
    return job


def load_job(base, job_id):
    job = store.read_json(_job_path(base, job_id))
    if job is None:
        raise FileNotFoundError("job not found: %s" % job_id)
    return job


def list_jobs(base=None):
    folder = _jobs_dir(base)
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    jobs = []
    for name in names:
        if not name.endswith(".json") or name.endswith(".response.json"):
            continue
        job = store.read_json(os.path.join(folder, name)) or {}
        done = os.path.exists(os.path.join(folder, name[:-5] + ".response.json"))
        jobs.append({"job_id": job.get("job_id"), "task": job.get("task"),
                     "as_of": (job.get("context") or {}).get("as_of"),
                     "query": (job.get("context") or {}).get("query", ""),
                     "created_at": job.get("created_at"), "completed": done})
    return jobs


def _fail(code, message):
    return {"valid": False, "errors": [{"code": code, "path": "$", "message": message}],
            "markdown": "", "suggestions": [], "auto_applied": []}


def complete_job(base, job_id, response):
    try:
        job = load_job(base, job_id)
    except (FileNotFoundError, ValueError) as exc:
        return _fail("job_not_found", str(exc))
    if not isinstance(response, dict):
        return _fail("contract", "response must be a JSON object")
    expected = _sign(base, job) if job.get("context") else ""
    if not hmac.compare_digest(str(job.get("signature", "")), expected):
        return _fail("signature", "job signature does not verify; the job file was modified")
    current = stable_hash(store.weekly_input(base, job["context"]["as_of"]))
    if current != job["source_sha256"]:
        return _fail("stale", "workspace data changed after this job was created; start a new job")
    result = validate_response(response, job["context"])
    if not result["valid"]:
        return {"valid": False, "errors": result["errors"], "markdown": "", "suggestions": [],
                "auto_applied": []}
    digest = result["receipt"]["response_sha256"]
    store.write_json(os.path.join(_jobs_dir(base), job_id + ".response.json"),
                     {"response": response, "receipt": result["receipt"]})
    context = job["context"]
    autonomous = context["policy"]["level"] == "autonomous"
    suggestions, auto_applied = [], []
    for item in response.get("recommendations") or []:
        if not (isinstance(item, dict) and item.get("changes_plan")):
            continue
        wants_auto = autonomous and not item.get("requires_confirmation")
        blockers = []
        if wants_auto:
            blockers = coach_decisions.auto_blockers(base, context, item)
            if coach_policy.get_level(base) != "autonomous":
                blockers.append("autonomy_lowered")
        suggestion = coach_decisions.register(base, job_id, digest, item, blockers)
        if wants_auto and not blockers and suggestion["status"] == "pending":
            suggestion = coach_decisions.auto_apply(base, suggestion["key"], item["plan_text"])
            auto_applied.append({"key": suggestion["key"], "plan_text": item["plan_text"],
                                 "undo_until": suggestion["auto"]["undo_until"]})
        suggestions.append(suggestion)
    return {"valid": True, "errors": [], "receipt": result["receipt"],
            "markdown": render_markdown(context, response), "suggestions": suggestions,
            "auto_applied": auto_applied}


def render_markdown(context, response):
    lines = ["# 教练回复（%s）" % context.get("task"), "",
             "日期：%s　安全门：%s" % (context.get("as_of"),
                                    (context.get("safety_gate") or {}).get("level")), ""]

    def evidence_line(items):
        return "；".join("`%s`=%s" % (item.get("pointer"), item.get("observed_value"))
                        for item in items or [] if isinstance(item, dict))

    if response.get("observations"):
        lines += ["## 观察", ""]
        for item in response["observations"]:
            lines.append("- %s（置信度 %s）" % (item.get("statement"), item.get("confidence")))
            lines.append("  - 证据：%s" % evidence_line(item.get("evidence")))
        lines.append("")
    if response.get("recommendations"):
        lines += ["## 建议", ""]
        for item in response["recommendations"]:
            mark = ""
            if item.get("changes_plan"):
                mark = ("（激进档：满足护栏时自动应用，7 天内可撤销，否则等你确认）"
                        if not item.get("requires_confirmation") else "（需你确认后才会改计划）")
            lines.append("- [%s] %s%s" % (item.get("action_type"), item.get("action"), mark))
            lines.append("  - 证据：%s" % evidence_line(item.get("evidence")))
        lines.append("")
    if response.get("risks"):
        lines += ["## 风险", ""]
        lines += ["- %s：%s -> %s" % (item.get("level"), item.get("signal"), item.get("action"))
                  for item in response["risks"]]
        lines.append("")
    if response.get("questions"):
        lines += ["## 需要你回答", ""] + ["- %s" % item for item in response["questions"]] + [""]
    if response.get("limitations"):
        lines += ["## 局限", ""] + ["- %s" % item for item in response["limitations"]] + [""]
    return "\n".join(lines).rstrip() + "\n"


def response_template(task):
    if task not in TASK_ACTIONS:
        raise ValueError("unknown task: %s" % task)
    evidence = {"source": "coach_context", "pointer": "/views/raw_signals/0/latest",
                "layer": "raw_signal", "observed_value": "<与指针处完全一致的值>"}
    return {
        "kind": "coach_response", "schema_version": 2, "task": task, "as_of": "<context.as_of>",
        "data_status": {"complete": False, "missing": [], "note": ""},
        "observations": [{"id": "obs_1", "statement": "<基于证据的事实陈述；写了偏高/下降/正常范围等方向词就要带 claims>",
                          "confidence": "medium", "evidence": [evidence],
                          "claims": [{"metric": "<baseline_status 的 key>",
                                      "direction": "<above|below|within，等于该项 status>"}]}],
        "recommendations": [{"id": "rec_1", "action_type": sorted(TASK_ACTIONS[task])[0],
                             "action": "<具体建议>", "confidence": "low", "evidence": [evidence],
                             "requires_confirmation": False, "changes_plan": False}],
        "risks": [{"level": "<不低于 safety_gate.level>", "signal": "<信号>", "action": "<应对>"}],
        "questions": [], "limitations": ["<数据或方法局限>"],
    }
