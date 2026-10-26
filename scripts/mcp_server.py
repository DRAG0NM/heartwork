#!/usr/bin/env python3
"""Stdlib MCP server (stdio, optional local streamable HTTP) over the sports-eval facade.

decide / apply_plan / set_autonomy / undo are deliberately not exposed: only a human confirms
plan changes, raises the coach's autonomy or reverts an automatic change.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api  # noqa: E402
import coach_runtime  # noqa: E402
import coach_skills  # noqa: E402

DEFAULT_PROTOCOL = "2025-06-18"
CONFIRM_HINT = "计划变更需要用户本人确认：请在网页（sec.py web）或 CLI（sec.py decide / plan-apply）中操作。"


def _schema(properties=None, required=None):
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


TOOLS = [
    ("status", "工作区概况：数据天数、活动数、事件数、待决建议数。", _schema()),
    ("import_files", "导入本机文件或目录（FIT/GPX/TCX/CSV/Apple Health export.xml 或 zip）。",
     _schema({"paths": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                        "description": "本机文件或目录路径"}}, ["paths"])),
    ("log_event", "记录主观打卡/疼痛/训练反馈事件（只追加）。",
     _schema({"event": {"type": "object", "description":
              "type 取 morning_checkin/post_workout_feedback/pain_followup/weekly_checkin/note；"
              "可含 date、pain(0-10)、rpe(0-10)、subjective_recovery(1-10)、illness、red_flags[]、note"}},
             ["event"])),
    ("weekly_report", "生成周报告 Markdown 并写入工作区 reports/。",
     _schema({"as_of": {"type": "string", "description": "截止日期 YYYYMMDD 或 YYYY-MM-DD，缺省为最新数据日"}})),
    ("start_coach_job", "创建带签名的教练作业，返回 context、给模型的 instructions、技能正文与 Response v2 骨架。"
     "拿到后只输出 Response v2 JSON，再用 submit_coach_response 提交。",
     _schema({"query": {"type": "string", "description": "用户的问题"},
              "task": {"type": "string", "enum": ["daily_guidance", "post_workout", "pain_followup",
                                                  "weekly_review"]},
              "as_of": {"type": "string"}}, ["query"])),
    ("submit_coach_response", "提交 Response v2 JSON 进行验证；通过后返回渲染结果与待决建议。",
     _schema({"job_id": {"type": "string"}, "response": {"type": "object"}}, ["job_id", "response"])),
    ("list_pending_suggestions", "列出待用户决定的计划建议（本服务不提供接受/应用，需用户在网页或 CLI 确认）。",
     _schema()),
    ("run_eval", "运行固定场景回放评测（硬门）。", _schema()),
    ("get_autonomy", "读取教练权限档位（只读；档位只能由用户在网页或 CLI 修改）。", _schema()),
    ("get_profile", "读取个人设置（hr_max、hr_rest、hr_zones、goal、notes）。", _schema()),
]
TOOL_NAMES = {item[0] for item in TOOLS}

PROMPTS = {
    "coach_daily": ("今日教练建议", "daily_guidance", "今天该怎么练？"),
    "coach_weekly": ("每周复盘", "weekly_review", "请做本周复盘。"),
}


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def log(text):
    print("[sports-eval-mcp] %s" % text, file=sys.stderr, flush=True)


class Server:
    def __init__(self, home=None):
        self.home = home

    def call_tool(self, name, args):
        home = self.home
        if name == "status":
            return api.status(home)
        if name == "import_files":
            return api.import_paths(home, args.get("paths") or [])
        if name == "log_event":
            return api.log_event(home, args.get("event") or {})
        if name == "weekly_report":
            return api.weekly_report(home, args.get("as_of"))
        if name == "start_coach_job":
            job = api.start_job(home, args.get("query", ""), args.get("task"), args.get("as_of"))
            return {"job_id": job["job_id"], "task": job["task"], "skills": job["skills"],
                    "skill_texts": {skill: coach_skills.skill_text(skill) for skill in job["skills"]},
                    "instructions": job["instructions"], "context": job["context"],
                    "response_template": coach_runtime.response_template(job["task"]),
                    "next": "只输出 Response v2 JSON，然后调用 submit_coach_response(job_id, response)。"}
        if name == "submit_coach_response":
            result = api.complete_job(home, args.get("job_id", ""), args.get("response"))
            if result["suggestions"]:
                result["note"] = CONFIRM_HINT
            if result.get("auto_applied"):
                result["note"] = "部分计划已按激进档自动应用；用户可在 7 天内于网页或 CLI（sec.py undo）撤销。"
            return result
        if name == "list_pending_suggestions":
            return {"suggestions": api.pending_suggestions(home), "note": CONFIRM_HINT}
        if name == "run_eval":
            return api.run_eval()
        if name == "get_autonomy":
            return api.get_autonomy(home)
        return api.get_profile(home)

    def handle(self, message):
        """Return a response dict, or None for notifications."""
        msg_id = message.get("id")
        if msg_id is None:
            return None
        try:
            result = self._dispatch(message.get("method"), message.get("params") or {})
        except RpcError as exc:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:  # keep the server alive on any bug
            log("internal error in %s: %r" % (message.get("method"), exc))
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method, params):
        if method == "initialize":
            return {"protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL,
                    "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False},
                                     "resources": {"listChanged": False}},
                    "serverInfo": {"name": "sports-eval-core", "version": "0.4.0"},
                    "instructions": "先 start_coach_job，按 instructions 只输出 Response v2 JSON，再 "
                                    "submit_coach_response。计划变更需用户在网页或 CLI 确认。"}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": name, "description": text, "inputSchema": schema}
                              for name, text, schema in TOOLS]}
        if method == "tools/call":
            name = params.get("name")
            if name not in TOOL_NAMES:
                raise RpcError(-32602, "unknown tool: %s" % name)
            try:
                data = self.call_tool(name, params.get("arguments") or {})
                error = False
            except (ValueError, KeyError, PermissionError, FileNotFoundError, TypeError) as exc:
                data, error = {"error": str(exc)}, True
            return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                    "structuredContent": data, "isError": error}
        if method == "prompts/list":
            return {"prompts": [{"name": key, "description": value[0],
                                 "arguments": [{"name": "question", "description": "你的问题",
                                                "required": False}]}
                                for key, value in PROMPTS.items()]}
        if method == "prompts/get":
            name = params.get("name")
            if name not in PROMPTS:
                raise RpcError(-32602, "unknown prompt: %s" % name)
            title, task, default = PROMPTS[name]
            question = (params.get("arguments") or {}).get("question") or default
            text = ("请调用 start_coach_job(query=%s, task=%s)，严格按返回的 instructions 与技能正文只输出 "
                    "Response v2 JSON，再用 submit_coach_response 提交；通过后把渲染结果讲给我。"
                    "计划变更只能由我在网页或 CLI 确认。" % (json.dumps(question, ensure_ascii=False), task))
            return {"description": title,
                    "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
        if method == "resources/list":
            return {"resources": [{"uri": "skill://%s" % item["name"], "name": item["name"],
                                   "description": item["description"], "mimeType": "text/markdown"}
                                  for item in coach_skills.list_skills()]}
        if method == "resources/read":
            uri = str(params.get("uri", ""))
            name = uri[len("skill://"):] if uri.startswith("skill://") else ""
            try:
                text = coach_skills.skill_text(name)
            except KeyError:
                raise RpcError(-32002, "resource not found: %s" % uri)
            return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}
        raise RpcError(-32601, "method not found: %s" % method)

    def handle_text(self, line):
        """Handle one raw JSON-RPC payload (object or batch); return the reply or None."""
        try:
            message = json.loads(line)
        except ValueError:
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if isinstance(message, list):
            replies = [reply for reply in (self.handle(item) for item in message if isinstance(item, dict))
                       if reply]
            return replies or None
        if not isinstance(message, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        return self.handle(message)


def serve_stdio(server):
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        reply = server.handle_text(line)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def serve_http(server, spec):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    host, _, port = spec.rpartition(":")
    if host != "127.0.0.1" or not port.isdigit():
        raise ValueError("--http 只允许 127.0.0.1:PORT")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log(fmt % args)

        def _send(self, status, body=b""):
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.split("?")[0] != "/mcp":
                return self._send(404)
            origin = self.headers.get("Origin")
            if origin and origin.split("://")[-1].split(":")[0] not in ("127.0.0.1", "localhost"):
                return self._send(403)
            length = int(self.headers.get("Content-Length") or 0)
            reply = server.handle_text(self.rfile.read(length).decode("utf-8", "replace"))
            if reply is None:
                return self._send(202)
            return self._send(200, json.dumps(reply, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):
            self._send(405)

    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    log("MCP over HTTP: http://%s:%s/mcp (Ctrl+C 停止)" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="mcp_server", description="sports-eval-core MCP server")
    parser.add_argument("--home", default=None)
    parser.add_argument("--http", metavar="127.0.0.1:PORT")
    args = parser.parse_args(argv)
    server = Server(args.home)
    if args.http:
        return serve_http(server, args.http)
    return serve_stdio(server)


if __name__ == "__main__":
    sys.exit(main())
