#!/usr/bin/env python3
"""Built-in coach: signed job -> model API -> deterministic validation -> rewrite until it passes.

The model never bypasses the gate. A rejected answer goes back to it with the
validator's error codes; after max_attempts the job stays open and nothing is shown.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

import coach_runtime
import coach_skills
import llm
import store


def parse_loose_json(text):
    """Accept a reply wrapped in prose or a ```json fence."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("回复里没有找到 JSON 对象")
        text = text[start:end + 1]
    return json.loads(text)


def system_prompt(job):
    parts = [job["instructions"], ""]
    for skill in job["skills"]:
        parts += ["## Skill: %s" % skill, coach_skills.skill_text(skill), ""]
    parts += ["## Response v2 骨架", "```json",
              json.dumps(coach_runtime.response_template(job["task"]), ensure_ascii=False),
              "```", "",
              "篇幅：observations 最多 6 条、recommendations 最多 3 条，statement 和 action 各一两句。",
              "只回复一个 JSON 对象，不要输出解释或代码围栏。"]
    return "\n".join(parts)


def user_prompt(job):
    query = job["context"].get("query") or "（用户没有提问，按任务默认给出）"
    # Compact JSON: same content, far fewer input tokens than indented.
    return "用户问题：%s\n\n## coach_context\n```json\n%s\n```" % (
        query, json.dumps(job["context"], ensure_ascii=False, separators=(",", ":")))


def repair_prompt(errors):
    lines = ["上面的回复没有通过确定性核验，不会展示给用户。错误如下："]
    lines += ["- [%s] %s：%s" % (item.get("code"), item.get("path"), item.get("message"))
              for item in errors[:20]]
    lines.append("请对照 coach_context 修正：observed_value 必须与指针处的值逐字相同，"
                 "方向 claims 必须等于 baseline_status。重新输出完整的 JSON 对象。")
    return "\n".join(lines)


def _totals(record):
    attempts = record["attempts"]
    return {"seconds": round(sum(item.get("seconds", 0) for item in attempts), 1),
            "input_tokens": sum(item.get("usage", {}).get("input") or 0 for item in attempts),
            "cached_tokens": sum(item.get("usage", {}).get("cached") or 0 for item in attempts),
            "output_tokens": sum(item.get("usage", {}).get("output") or 0 for item in attempts)}


def _log(base, job_id, record):
    store.write_json(os.path.join(coach_runtime._jobs_dir(base), job_id + ".agent.json"), record)


def run(base, query, task=None, as_of=None, emit=None):
    """Run the coach end to end. emit(event_dict) receives progress; returns the final event."""
    emit = emit or (lambda event: None)
    cfg = llm.load(base)
    store.init(base)
    job = coach_runtime.start_job(base, query, task, as_of)
    emit({"type": "job", "job_id": job["job_id"], "task": job["task"], "skills": job["skills"],
          "gate": job["context"]["safety_gate"]["level"], "model": cfg["model"],
          "provider": cfg["provider"], "max_attempts": cfg["max_attempts"]})
    system = system_prompt(job)
    messages = [{"role": "user", "content": user_prompt(job)}]
    record = {"job_id": job["job_id"], "provider": cfg["provider"], "model": cfg["model"],
              "started_at": datetime.now().isoformat(timespec="seconds"), "attempts": []}
    for attempt in range(1, cfg["max_attempts"] + 1):
        emit({"type": "attempt", "n": attempt})
        usage, started = {}, time.monotonic()
        try:
            text = llm.chat(cfg, system, messages,
                            lambda kind, chunk: emit({"type": kind, "text": chunk}), usage)
        except llm.LLMError as exc:
            record["attempts"].append({"n": attempt, "error": str(exc),
                                       "seconds": round(time.monotonic() - started, 1)})
            _log(base, job["job_id"], record)
            final = {"type": "error", "job_id": job["job_id"], "message": str(exc)}
            emit(final)
            return final
        try:
            result = coach_runtime.complete_job(base, job["job_id"], parse_loose_json(text))
        except (ValueError, json.JSONDecodeError) as exc:
            result = {"valid": False, "errors": [{"code": "not_json", "path": "$", "message": str(exc)}]}
        record["attempts"].append({"n": attempt, "chars": len(text), "valid": result["valid"],
                                   "errors": result["errors"], "usage": usage,
                                   "text": None if result["valid"] else text,
                                   "seconds": round(time.monotonic() - started, 1)})
        record["totals"] = _totals(record)
        _log(base, job["job_id"], record)
        if result["valid"]:
            final = {"type": "done", "job_id": job["job_id"], "attempts": attempt,
                     "totals": record["totals"],
                     "suggestions": result["suggestions"], "auto_applied": result["auto_applied"],
                     "markdown": result["markdown"]}
            emit(final)
            return final
        emit({"type": "rejected", "n": attempt, "errors": result["errors"],
              "totals": record["totals"]})
        if any(item.get("code") in ("stale", "signature") for item in result["errors"]):
            break
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": repair_prompt(result["errors"])}]
    final = {"type": "failed", "job_id": job["job_id"], "totals": _totals(record),
             "message": "模型 %d 次作答都没有通过核验，回答不展示。" % len(record["attempts"])}
    emit(final)
    return final
