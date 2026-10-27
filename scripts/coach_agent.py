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

import coach_core
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


FACT_RULES = """## 本内核的引用方式（优先于上文第 3、4 条）
- 证据只写事实编号：evidence: [{"fact": "F12"}]。不要写 pointer、layer、observed_value，系统会按编号展开并核对。
- 结论里写到的每个数值，都要来自你引用的事实（同一条记录的其他字段也算）。
- claims 的 metric 用事实表里「基线·名称[key]」方括号中的 key，direction 等于该项「判定」的值。"""


def _template(task):
    template = coach_runtime.response_template(task)
    for section in ("observations", "recommendations"):
        for item in template[section]:
            item["evidence"] = [{"fact": "F1"}]
    return template


def fact_table(job):
    lines = ["编号|含义|值"]
    for row in coach_core.facts(job["context"]):
        value = row["value"]
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        lines.append("%s|%s|%s" % (row["id"], row["label"], text))
    return "\n".join(lines)


def system_prompt(job):
    parts = [job["instructions"], "", FACT_RULES, ""]
    for skill in job["skills"]:
        parts += ["## Skill: %s" % skill, coach_skills.skill_text(skill), ""]
    parts += ["## Response v2 骨架", "```json",
              json.dumps(_template(job["task"]), ensure_ascii=False),
              "```", "",
              "篇幅：observations 最多 6 条、recommendations 最多 3 条，statement 和 action 各一两句。",
              "只回复一个 JSON 对象，不要输出解释或代码围栏。"]
    return "\n".join(parts)


def user_prompt(job):
    """The question plus a numbered fact table: smaller than the context JSON and cited by id."""
    context = job["context"]
    query = context.get("query") or "（用户没有提问，按任务默认给出）"
    return "用户问题：%s\n任务：%s　日期：%s\n\n## 事实表\n%s" % (
        query, context["task"], context["as_of"], fact_table(job))


def repair_prompt(errors, job=None):
    lines = ["上面的回复没有通过确定性核验，不会展示给用户。错误如下："]
    ids = {row["pointer"]: row["id"] for row in coach_core.facts(job["context"])} if job else {}

    def readable(message):
        return re.sub(r"/[\w~/#.-]+", lambda m: ids.get(m.group(0), m.group(0)), message)

    lines += ["- [%s] %s：%s" % (item.get("code"), item.get("path"), readable(item.get("message", "")))
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
                     {"role": "user", "content": repair_prompt(result["errors"], job)}]
    final = {"type": "failed", "job_id": job["job_id"], "totals": _totals(record),
             "message": "模型 %d 次作答都没有通过核验，回答不展示。" % len(record["attempts"])}
    emit(final)
    return final
