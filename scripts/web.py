#!/usr/bin/env python3
"""Local web GUI: a 127.0.0.1-only JSON API over api.py plus one static page.

Decisions (accept / reject / apply plan) live here and in the CLI on purpose:
they are the human side of the confirmation gate and are never exposed to MCP.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import api
import coach_agent
import coach_runtime
import coach_skills
import llm
import store


HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "web", "index.html")
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
MAX_UPLOAD = 200 * 1024 * 1024
SAFE_NAME = re.compile(r"[^\w.\-一-鿿]+")


def history(base, as_of=None):
    """Daily rows for the art gallery and trend views (42 days + current week)."""
    payload = store.weekly_input(base, as_of)
    daily = sorted(payload["history_daily"] + payload["daily"], key=lambda row: row["date"])
    return {"week": payload["week"], "daily": daily,
            "activities": [item for item in store.load_activities(base)
                           if item["date"] >= (daily[0]["date"] if daily else "")],
            "events": payload["subjective_events"], "load_history": payload["load_history"]}


def prompt_text(job):
    """Everything a chat-only AI needs, in one paste."""
    parts = [job["instructions"], ""]
    for skill in job["skills"]:
        parts += ["## Skill: %s" % skill, coach_skills.skill_text(skill), ""]
    parts += ["## Response v2 骨架", "```json",
              json.dumps(coach_runtime.response_template(job["task"]), ensure_ascii=False, indent=2),
              "```", "", "## coach_context", "```json",
              json.dumps(job["context"], ensure_ascii=False, indent=2), "```", "",
              "只回复一个 JSON 对象。"]
    return "\n".join(parts)


def job_view(base, job_id):
    job = coach_runtime.load_job(base, job_id)
    view = {key: job[key] for key in ("job_id", "task", "skills", "created_at")}
    view["context"] = job["context"]
    view["query"] = job["context"].get("query", "")
    answer = store.read_json(os.path.join(coach_runtime._jobs_dir(base), job_id + ".response.json"))
    view["response"] = answer["response"] if answer else None
    view["receipt"] = answer["receipt"] if answer else None
    view["prompt"] = prompt_text(job)
    return view


def make_handler(base):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write("[web] " + fmt % args + "\n")

        def _host_ok(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            origin = self.headers.get("Origin")
            if host not in ALLOWED_HOSTS:
                return False
            return not origin or urlparse(origin).hostname in ALLOWED_HOSTS

        def _send(self, status, body, content_type="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(
                body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}

        def _guard(self, handler):
            if not self._host_ok():
                return self._send(403, {"error": "仅允许本机访问"})
            try:
                return handler()
            except PermissionError as exc:
                return self._send(403, {"error": str(exc)})
            except (ValueError, KeyError, FileNotFoundError, json.JSONDecodeError) as exc:
                return self._send(400, {"error": str(exc)})

        def do_GET(self):
            self._guard(self._get)

        def do_POST(self):
            self._guard(self._post)

        def _get(self):
            url = urlparse(self.path)
            query = {key: values[0] for key, values in parse_qs(url.query).items()}
            as_of = query.get("as_of") or None
            if url.path in ("/", "/index.html"):
                with open(PAGE, "rb") as handle:
                    return self._send(200, handle.read(), "text/html; charset=utf-8")
            routes = {
                "/api/snapshot": lambda: api.snapshot(base, as_of),
                "/api/history": lambda: history(base, as_of),
                "/api/daily": lambda: api.daily_view(base, as_of),
                "/api/status": lambda: api.status(base),
                "/api/report": lambda: api.weekly_report(base, as_of),
                "/api/suggestions": lambda: {"items": api.pending_suggestions(base)},
                "/api/jobs": lambda: {"items": api.list_jobs(base)},
                "/api/job": lambda: job_view(base, query["id"]),
                "/api/profile": lambda: api.get_profile(base),
                "/api/plan": lambda: store.load_plan(base),
                "/api/eval": lambda: api.run_eval(),
                "/api/autonomy": lambda: api.get_autonomy(base),
                "/api/decisions": lambda: api.decisions(base),
                "/api/provenance": lambda: api.provenance(base, query["date"], query.get("metric")),
                "/api/llm": lambda: llm.public(base),
            }
            if url.path not in routes:
                return self._send(404, {"error": "not found"})
            return self._send(200, routes[url.path]())

        def _post(self):
            path = urlparse(self.path).path
            if path == "/api/upload":
                return self._upload()
            body = self._json_body()
            if path == "/api/coach":
                return self._coach_stream(body)
            if path == "/api/llm":
                return self._send(200, llm.save(base, body))
            if path == "/api/llm/test":
                try:
                    return self._send(200, llm.ping(base))
                except llm.LLMError as exc:
                    return self._send(200, {"ok": False, "error": str(exc)})
            if path == "/api/ask":
                job = api.start_job(base, body.get("query", ""), body.get("task") or None,
                                    body.get("as_of") or None)
                return self._send(200, job_view(base, job["job_id"]))
            if path == "/api/complete":
                response = body.get("response")
                if isinstance(response, str):
                    response = coach_agent.parse_loose_json(response)
                return self._send(200, api.complete_job(base, body.get("job_id", ""), response))
            if path == "/api/checkin":
                return self._send(200, api.log_event(base, body))
            if path == "/api/decide":
                return self._send(200, api.decide(base, body["key"], bool(body.get("accept")),
                                                  body.get("note", "")))
            if path == "/api/apply-plan":
                return self._send(200, api.apply_plan(base, body["key"], body.get("text", ""),
                                                      body.get("confirm") is True))
            if path == "/api/autonomy":
                return self._send(200, api.set_autonomy(base, body.get("level"),
                                                        body.get("confirm") is True))
            if path == "/api/undo":
                return self._send(200, api.undo(base, body["key"]))
            if path == "/api/profile":
                return self._send(200, api.set_profile(base, body))
            if path == "/api/demo":
                return self._send(200, api.init(base, demo=True))
            return self._send(404, {"error": "not found"})

        def _coach_stream(self, body):
            """Server-sent events while the built-in coach answers and gets validated."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

            def emit(event):
                try:
                    self.wfile.write(("data: %s\n\n" % json.dumps(event, ensure_ascii=False))
                                     .encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    pass  # the page went away; the job still finishes and is saved

            try:
                coach_agent.run(base, body.get("query", ""), body.get("task") or None,
                                body.get("as_of") or None, emit)
            except (ValueError, KeyError, FileNotFoundError) as exc:
                emit({"type": "error", "message": str(exc)})

        def _upload(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_UPLOAD:
                raise ValueError("文件为空或超过 200MB")
            name = SAFE_NAME.sub("_", os.path.basename(
                unquote(self.headers.get("X-Filename", "upload.bin"))))[:120] or "upload.bin"
            inbox = os.path.join(store.home(base), "inbox")
            os.makedirs(inbox, exist_ok=True)
            target = os.path.join(inbox, name)
            with open(target, "wb") as handle:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
            result = api.import_paths(base, [target])
            result["file"] = name
            return self._send(200, result)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Heartwork 健康画布本机网页")
    parser.add_argument("--home")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    base = args.home
    store.init(base)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(base))
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    print("Heartwork 网页：%s（工作区 %s，Ctrl+C 退出）" % (url, store.home(base)), flush=True)
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
