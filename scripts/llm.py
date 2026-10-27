#!/usr/bin/env python3
"""Model client: OpenAI-compatible chat, Anthropic Messages, or a subscription CLI; stdlib only.

Settings live in the workspace (`llm.json`), never in the repository. The key is
never returned to any surface; callers only see whether one is set. The CLI
protocols run `claude -p` or `codex exec` with tools disabled, so an existing
Claude or ChatGPT subscription answers without an API key.
"""

from __future__ import annotations

import glob
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
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
    "claude-cli": {"label": "Claude 订阅（Claude Code）", "protocol": "claude-cli", "base_url": "",
                   "model": "", "key_env": "", "local": False},
    "codex-cli": {"label": "ChatGPT 订阅（Codex CLI）", "protocol": "codex-cli", "base_url": "",
                  "model": "", "key_env": "", "local": False},
    "custom": {"label": "自定义兼容接口", "protocol": "openai", "base_url": "", "model": "",
               "key_env": "", "local": False},
}
FIELDS = ("provider", "protocol", "base_url", "model", "api_key", "temperature", "max_attempts",
          "json_mode")
TIMEOUT = 180
CLI_PROTOCOLS = ("claude-cli", "codex-cli")


def find_cli(protocol, configured=""):
    """The CLI executable: the configured path, PATH, or Claude desktop's bundled claude.exe."""
    if configured and os.path.isfile(configured):
        return configured
    name = "claude" if protocol == "claude-cli" else "codex"
    found = shutil.which(name)
    if found:
        return found
    if protocol == "claude-cli" and os.environ.get("APPDATA"):
        bundled = glob.glob(os.path.join(os.environ["APPDATA"], "Claude", "claude-code", "*", "*", "claude.exe"))
        if bundled:
            return max(bundled, key=os.path.getmtime)
    return ""


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
    ready = bool(cfg["model"] and cfg["base_url"] and (key or local))
    if cfg["protocol"] in CLI_PROTOCOLS:
        command = find_cli(cfg["protocol"], cfg["base_url"])
        cfg["command"] = command
        ready = bool(command)
    cfg.update({"key_set": bool(key), "key_hint": ("…" + key[-4:]) if len(key) >= 8 else "",
                "local": local, "ready": ready})
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
        if key == "protocol" and value not in ("openai", "anthropic") + CLI_PROTOCOLS:
            raise ValueError("protocol must be openai, anthropic, claude-cli or codex-cli")
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


def _openai(cfg, system, messages, on_delta, json_mode, usage, extras=True):
    body = {"model": cfg["model"], "stream": True, "temperature": cfg["temperature"],
            "messages": [{"role": "system", "content": system}] + messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if extras:
        body["stream_options"] = {"include_usage": True}
    headers = {"Authorization": "Bearer %s" % cfg["api_key"]} if cfg["api_key"] else {}
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    parts = []
    for event in _post_stream(url, headers, body):
        if event.get("error"):
            raise LLMError(json.dumps(event["error"], ensure_ascii=False)[:600])
        if event.get("usage"):
            info = event["usage"]
            usage.update(input=info.get("prompt_tokens"), output=info.get("completion_tokens"),
                         cached=info.get("prompt_cache_hit_tokens")
                         or (info.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
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


def _anthropic(cfg, system, messages, on_delta, usage):
    # Cache the system prompt and the context message: repair turns resend both unchanged.
    first = dict(messages[0], content=[{"type": "text", "text": messages[0]["content"],
                                        "cache_control": {"type": "ephemeral"}}])
    body = {"model": cfg["model"], "max_tokens": 8000, "stream": True,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "temperature": min(cfg["temperature"], 1.0), "messages": [first] + messages[1:]}
    headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
    url = cfg["base_url"].rstrip("/") + "/v1/messages"
    parts = []
    for event in _post_stream(url, headers, body):
        if event.get("type") == "error":
            raise LLMError(json.dumps(event.get("error"), ensure_ascii=False)[:600])
        if event.get("type") == "message_start":
            info = (event.get("message") or {}).get("usage") or {}
            cached = info.get("cache_read_input_tokens") or 0
            usage.update(input=(info.get("input_tokens") or 0) + cached
                         + (info.get("cache_creation_input_tokens") or 0), cached=cached)
        elif event.get("type") == "message_delta" and event.get("usage"):
            usage["output"] = event["usage"].get("output_tokens")
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            parts.append(delta["text"])
            if on_delta:
                on_delta("text", delta["text"])
        elif delta.get("type") == "thinking_delta" and on_delta:
            on_delta("thinking", delta.get("thinking", ""))
    return "".join(parts)


def _transcript(messages):
    """CLIs take one prompt: earlier turns become a labelled transcript."""
    if len(messages) == 1:
        return messages[0]["content"]
    parts = []
    for message in messages:
        role = "用户" if message["role"] == "user" else "你上一次的回答"
        parts.append("### %s\n%s" % (role, message["content"]))
    return "\n\n".join(parts)


def _run_cli(argv, prompt, on_line):
    """Run a CLI with the prompt on stdin in an empty folder; feed each stdout line to on_line."""
    # A parent Claude Code session's variables would redirect or confuse the child CLI.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CLAUDE", "ANTHROPIC_BASE_URL"))}
    name = os.path.basename(argv[0])
    with tempfile.TemporaryDirectory(prefix="heartwork-cli-") as folder:
        try:
            proc = subprocess.Popen(argv, cwd=folder, env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    encoding="utf-8", errors="replace")
        except OSError as exc:
            raise LLMError("无法启动 %s：%s" % (name, exc)) from None
        errors = []
        threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True).start()
        watchdog = threading.Timer(TIMEOUT, proc.kill)
        watchdog.start()
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                line = line.strip()
                if line:
                    try:
                        on_line(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            code = proc.wait()
        finally:
            watchdog.cancel()
        if code != 0:
            detail = (errors[0] if errors else "").strip()[:400]
            raise LLMError("%s 超过 %d 秒没有完成，已停止；先在终端里运行一次 %s 确认已登录" % (
                name, TIMEOUT, name) if code in (-9, 1) and not detail else
                "%s 退出码 %d：%s" % (name, code, detail))


def _claude_cli(cfg, system, messages, on_delta, usage):
    command = find_cli("claude-cli", cfg["base_url"])
    if not command:
        raise LLMError("没找到 Claude Code（claude）；在设置里填它的路径")
    parts, final = [], {}
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(system)
    argv = [command, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--system-prompt-file", handle.name]
    if cfg.get("model"):
        argv += ["--model", cfg["model"]]

    def on_line(event):
        inner = event.get("event") or {}
        delta = inner.get("delta") or {}
        if event.get("type") == "stream_event" and delta.get("type") == "text_delta":
            parts.append(delta["text"])
            if on_delta:
                on_delta("text", delta["text"])
        elif event.get("type") == "result":
            final.update(event)

    try:
        _run_cli(argv, _transcript(messages), on_line)
    finally:
        os.unlink(handle.name)
    if final.get("is_error"):
        raise LLMError("Claude Code：%s" % str(final.get("result"))[:400])
    info = final.get("usage") or {}
    cached = info.get("cache_read_input_tokens") or 0
    usage.update(input=(info.get("input_tokens") or 0) + cached + (info.get("cache_creation_input_tokens") or 0),
                 cached=cached, output=info.get("output_tokens"))
    return final.get("result") or "".join(parts)


def _codex_cli(cfg, system, messages, on_delta, usage):
    command = find_cli("codex-cli", cfg["base_url"])
    if not command:
        raise LLMError("没找到 Codex CLI（codex）；先安装或在设置里填它的路径")
    texts = []
    argv = [command, "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only"]
    if cfg.get("model"):
        argv += ["-m", cfg["model"]]
    argv.append("-")

    def on_line(event):
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            texts.append(item.get("text") or "")
            if on_delta:
                on_delta("text", item.get("text") or "")
        elif event.get("type") == "turn.completed":
            info = event.get("usage") or {}
            usage.update(input=info.get("input_tokens"), cached=info.get("cached_input_tokens") or 0,
                         output=info.get("output_tokens"))

    _run_cli(argv, "%s\n\n---\n\n%s" % (system, _transcript(messages)), on_line)
    return texts[-1] if texts else ""


def chat(cfg, system, messages, on_delta=None, usage=None):
    """Stream one completion; returns the full text.

    on_delta(kind, text) sees text/thinking chunks; usage (a dict) receives
    input/cached/output token counts when the server reports them.
    """
    usage = {} if usage is None else usage
    if cfg["protocol"] == "claude-cli":
        return _claude_cli(cfg, system, messages, on_delta, usage)
    if cfg["protocol"] == "codex-cli":
        return _codex_cli(cfg, system, messages, on_delta, usage)
    if not cfg.get("model") or not cfg.get("base_url"):
        raise LLMError("还没选模型：先在设置里填好服务商和模型")
    if cfg["protocol"] == "anthropic":
        if not cfg["api_key"]:
            raise LLMError("缺少 API Key")
        return _anthropic(cfg, system, messages, on_delta, usage)
    try:
        return _openai(cfg, system, messages, on_delta, cfg.get("json_mode", True), usage)
    except LLMError as exc:
        # Some compatible servers reject response_format or stream_options; retry once without.
        if "response_format" in str(exc) or "stream_options" in str(exc):
            return _openai(cfg, system, messages, on_delta, False, usage, extras=False)
        raise


def ping(base=None):
    cfg = load(base)
    text = chat(dict(cfg, json_mode=False), "你是连通性测试。", [{"role": "user", "content": "只回复 OK"}])
    return {"ok": True, "model": cfg["model"], "reply": text.strip()[:80]}
