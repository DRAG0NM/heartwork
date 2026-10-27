#!/usr/bin/env python3
"""Model API client: OpenAI-compatible chat completions plus Anthropic Messages, stdlib only.

Settings live in the workspace (`llm.json`), never in the repository. The key is
never returned to any surface; callers only see whether one is set.
"""

from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request

import store

PRESETS = {
    "deepseek": {"label": "DeepSeek", "protocol": "openai", "base_url": "https://api.deepseek.com",
                 "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY", "local": False},
    "openai": {"label": "OpenAI", "protocol": "openai", "base_url": "https://api.openai.com/v1",
               "model": "gpt-4.1", "key_env": "OPENAI_API_KEY", "local": False},
    "anthropic": {"label": "Anthropic Claude", "protocol": "anthropic",
                  "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5",
                  "key_env": "ANTHROPIC_API_KEY", "local": False},
    "moonshot": {"label": "Kimi", "protocol": "openai", "base_url": "https://api.moonshot.cn/v1",
                 "model": "kimi-k2-turbo-preview", "key_env": "MOONSHOT_API_KEY", "local": False},
    "qwen": {"label": "通义千问", "protocol": "openai",
             "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
             "key_env": "DASHSCOPE_API_KEY", "local": False},
    "ollama": {"label": "Ollama（本机）", "protocol": "openai", "base_url": "http://127.0.0.1:11434/v1",
               "model": "qwen3:8b", "key_env": "", "local": True},
    "custom": {"label": "自定义兼容接口", "protocol": "openai", "base_url": "", "model": "",
               "key_env": "", "local": False},
}
FIELDS = ("provider", "protocol", "base_url", "model", "api_key", "temperature", "max_attempts",
          "json_mode")
TIMEOUT = 180


class LLMError(RuntimeError):
    pass


def _path(base):
    return os.path.join(store.home(base), "llm.json")


def load(base=None):
    saved = store.read_json(_path(base), {}) or {}
    provider = saved.get("provider") or "deepseek"
    preset = PRESETS.get(provider, PRESETS["custom"])
    cfg = {"provider": provider, "protocol": preset["protocol"], "base_url": preset["base_url"],
           "model": preset["model"], "api_key": "", "temperature": 0.2, "max_attempts": 3,
           "json_mode": True}
    cfg.update({key: value for key, value in saved.items() if key in FIELDS and value not in (None, "")})
    if not cfg["api_key"] and preset["key_env"]:
        cfg["api_key"] = os.environ.get(preset["key_env"], "")
    return cfg


def public(base=None):
    """Settings for UI/CLI: the key is reduced to a flag and its last 4 characters."""
    cfg = load(base)
    key = cfg.pop("api_key")
    preset = PRESETS.get(cfg["provider"], PRESETS["custom"])
    local = preset["local"] or cfg["base_url"].startswith(("http://127.0.0.1", "http://localhost"))
    cfg.update({"key_set": bool(key), "key_hint": ("…" + key[-4:]) if len(key) >= 8 else "",
                "local": local, "ready": bool(cfg["model"] and cfg["base_url"] and (key or local))})
    return {"config": cfg, "presets": [dict(name=name, **{k: v for k, v in spec.items()})
                                       for name, spec in PRESETS.items()]}


def save(base, fields):
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise ValueError("unsupported llm fields: %s" % ", ".join(unknown))
    saved = store.read_json(_path(base), {}) or {}
    if fields.get("provider") and fields["provider"] != saved.get("provider"):
        if fields["provider"] not in PRESETS:
            raise ValueError("unknown provider: %s" % fields["provider"])
        preset = PRESETS[fields["provider"]]
        saved = {"provider": fields["provider"], "protocol": preset["protocol"],
                 "base_url": preset["base_url"], "model": preset["model"],
                 "api_key": saved.get("api_key", "") if fields["provider"] == saved.get("provider") else ""}
    for key, value in fields.items():
        if key == "api_key" and not value:
            continue  # blank key field in a form means "keep the current one"
        if key == "protocol" and value not in ("openai", "anthropic"):
            raise ValueError("protocol must be openai or anthropic")
        if key == "max_attempts":
            value = max(1, min(6, int(value)))
        if key == "temperature":
            value = max(0.0, min(1.5, float(value)))
        saved[key] = value.strip() if isinstance(value, str) else value
    store.write_json(_path(base), saved)
    return public(base)


def _post_stream(url, headers, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers=dict(headers, **{"Content-Type": "application/json",
                                                              "Accept": "text/event-stream"}))
    try:
        response = urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise LLMError("HTTP %d：%s" % (exc.code, detail)) from None
    except urllib.error.URLError as exc:
        raise LLMError("连不上模型服务：%s" % exc.reason) from None
    except (http.client.HTTPException, OSError) as exc:
        raise LLMError("模型服务连接中断：%s" % exc) from None
    with response:
        for raw in _safe_lines(response):
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue


def _safe_lines(response):
    try:
        yield from response
    except (http.client.HTTPException, OSError) as exc:
        raise LLMError("模型服务连接中断：%s" % exc) from None


def _openai(cfg, system, messages, on_delta, json_mode):
    body = {"model": cfg["model"], "stream": True, "temperature": cfg["temperature"],
            "messages": [{"role": "system", "content": system}] + messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": "Bearer %s" % cfg["api_key"]} if cfg["api_key"] else {}
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    parts = []
    for event in _post_stream(url, headers, body):
        if event.get("error"):
            raise LLMError(json.dumps(event["error"], ensure_ascii=False)[:600])
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            thinking = delta.get("reasoning_content") or delta.get("reasoning")
            if thinking and on_delta:
                on_delta("thinking", thinking)
            if delta.get("content"):
                parts.append(delta["content"])
                if on_delta:
                    on_delta("text", delta["content"])
    return "".join(parts)


def _anthropic(cfg, system, messages, on_delta):
    body = {"model": cfg["model"], "max_tokens": 8000, "stream": True, "system": system,
            "temperature": min(cfg["temperature"], 1.0), "messages": messages}
    headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
    url = cfg["base_url"].rstrip("/") + "/v1/messages"
    parts = []
    for event in _post_stream(url, headers, body):
        if event.get("type") == "error":
            raise LLMError(json.dumps(event.get("error"), ensure_ascii=False)[:600])
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            parts.append(delta["text"])
            if on_delta:
                on_delta("text", delta["text"])
        elif delta.get("type") == "thinking_delta" and on_delta:
            on_delta("thinking", delta.get("thinking", ""))
    return "".join(parts)


def chat(cfg, system, messages, on_delta=None):
    """Stream one completion; returns the full text. on_delta(kind, text) sees text/thinking chunks."""
    if not cfg.get("model") or not cfg.get("base_url"):
        raise LLMError("还没选模型：先在设置里填好服务商和模型")
    if cfg["protocol"] == "anthropic":
        if not cfg["api_key"]:
            raise LLMError("缺少 API Key")
        return _anthropic(cfg, system, messages, on_delta)
    try:
        return _openai(cfg, system, messages, on_delta, cfg.get("json_mode", True))
    except LLMError as exc:
        # Some compatible servers reject response_format; retry once without it.
        if cfg.get("json_mode", True) and "response_format" in str(exc):
            return _openai(cfg, system, messages, on_delta, False)
        raise


def ping(base=None):
    cfg = load(base)
    text = chat(dict(cfg, json_mode=False), "你是连通性测试。", [{"role": "user", "content": "只回复 OK"}])
    return {"ok": True, "model": cfg["model"], "reply": text.strip()[:80]}
