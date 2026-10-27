#!/usr/bin/env python3
"""heartwork command line: workspace, import, check-in, coach jobs, web, MCP."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api  # noqa: E402
import llm  # noqa: E402


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _read_json_file(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


SKILL_TARGETS = {"claude": ".claude/skills", "dsh": ".dsh/skills", "agents": ".agents/skills",
                 "gemini": ".gemini/skills"}
GEMINI_MARK = ("<!-- heartwork:begin -->", "<!-- heartwork:end -->")


def install_skills(target, dest=None):
    """Copy skills/coach-* into a project; gemini also gets a GEMINI.md block using @imports."""
    dest = os.path.abspath(dest or os.getcwd())
    source = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
    folder = os.path.join(dest, *SKILL_TARGETS[target].split("/"))
    names = sorted(name for name in os.listdir(source)
                   if name.startswith("coach-") and os.path.isdir(os.path.join(source, name)))
    for name in names:
        shutil.copytree(os.path.join(source, name), os.path.join(folder, name), dirs_exist_ok=True)
    result = {"target": target, "skills_dir": folder, "installed": names}
    if target == "gemini":
        lines = [GEMINI_MARK[0], "# 运动恢复教练（heartwork）",
                 "优先通过 MCP 工具 start_coach_job / submit_coach_response 工作；教练技能如下。"]
        lines += ["@./.gemini/skills/%s/SKILL.md" % name for name in names] + [GEMINI_MARK[1]]
        path = os.path.join(dest, "GEMINI.md")
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        if GEMINI_MARK[0] in old and GEMINI_MARK[1] in old:
            head, rest = old.split(GEMINI_MARK[0], 1)
            old = head.rstrip("\n") + rest.split(GEMINI_MARK[1], 1)[1].rstrip("\n")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write((old.rstrip("\n") + "\n\n" if old.strip() else "") + "\n".join(lines) + "\n")
        result["gemini_md"] = path
    return result


def _coach(home, args):
    import coach_agent

    def emit(event):
        kind = event["type"]
        if kind == "job":
            print("[%s · %s] 任务 %s　安全门 %s　技能 %s" % (
                event["provider"], event["model"], event["task"], event["gate"],
                ", ".join(event["skills"])), file=sys.stderr)
        elif kind == "attempt":
            print("第 %d 次作答…" % event["n"], file=sys.stderr, flush=True)
        elif kind == "text":
            sys.stderr.write(".")
            sys.stderr.flush()
        elif kind == "rejected":
            print("\n核验拒绝：" + "；".join("[%s] %s" % (item["code"], item["message"])
                                         for item in event["errors"][:5]), file=sys.stderr)
        elif kind == "done":
            print("\n通过核验（第 %d 次）\n" % event["attempts"], file=sys.stderr)

    result = coach_agent.run(home, args.question, args.task, args.as_of, emit)
    if result["type"] != "done":
        print("\n%s" % result["message"], file=sys.stderr)
        return 1
    print(result["markdown"])
    pending = [item["key"] for item in result["suggestions"] if item["status"] == "pending"]
    if pending:
        print("待决建议：%s（去网页或用 decide 确认）" % ", ".join(pending))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="heartwork", description="heartwork")
    parser.add_argument("--home", default=None, help="工作区目录（默认 ~/.heartwork 或 HEARTWORK_HOME）")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="创建工作区")
    init.add_argument("--demo", action="store_true", help="写入合成演示数据")
    commands.add_parser("status", help="工作区概况")
    importer = commands.add_parser("import", help="导入 FIT/GPX/TCX/CSV/Apple Health")
    importer.add_argument("paths", nargs="+")
    checkin = commands.add_parser("checkin", help="记录主观打卡")
    checkin.add_argument("--type", default="morning_checkin")
    checkin.add_argument("--date")
    checkin.add_argument("--pain", type=float)
    checkin.add_argument("--rpe", type=float)
    checkin.add_argument("--recovery", type=float, help="主观恢复 1-10")
    checkin.add_argument("--illness", action="store_true")
    checkin.add_argument("--red-flag", action="append", default=[])
    checkin.add_argument("--note")
    report = commands.add_parser("report", help="生成周报告")
    report.add_argument("--as-of")
    ask = commands.add_parser("ask", help="生成教练作业")
    ask.add_argument("question")
    ask.add_argument("--task")
    ask.add_argument("--as-of")
    complete = commands.add_parser("complete", help="提交模型回复并验证")
    complete.add_argument("job_id")
    complete.add_argument("response")
    commands.add_parser("suggestions", help="待决建议")
    decide = commands.add_parser("decide", help="接受或拒绝建议（仅人）")
    decide.add_argument("key")
    group = decide.add_mutually_exclusive_group(required=True)
    group.add_argument("--accept", action="store_true")
    group.add_argument("--reject", action="store_true")
    decide.add_argument("--note", default="")
    apply = commands.add_parser("plan-apply", help="应用已接受的计划建议")
    apply.add_argument("key")
    source = apply.add_mutually_exclusive_group(required=True)
    source.add_argument("--file")
    source.add_argument("--text")
    apply.add_argument("--confirm", action="store_true")
    autonomy = commands.add_parser("autonomy", help="查看或设置教练权限档位")
    autonomy.add_argument("level", nargs="?", choices=["conservative", "balanced", "autonomous"])
    autonomy.add_argument("--confirm", action="store_true", help="升到 autonomous 时必须带上")
    undo = commands.add_parser("undo", help="撤销教练自动应用的计划（仅人，7 天内）")
    undo.add_argument("key")
    commands.add_parser("eval", help="运行固定回放评测")
    install = commands.add_parser("install-skills", help="把教练技能复制进项目目录")
    install.add_argument("--target", required=True, choices=sorted(SKILL_TARGETS))
    install.add_argument("--dest", help="项目根目录（默认当前目录）")
    profile = commands.add_parser("profile", help="查看或修改个人设置")
    profile.add_argument("--hr-max", type=float)
    profile.add_argument("--hr-rest", type=float)
    profile.add_argument("--goal")
    profile.add_argument("--notes")
    coach = commands.add_parser("coach", help="内置 AI 教练：调用模型 API 作答并自动核验")
    coach.add_argument("question", nargs="?", default="")
    coach.add_argument("--task")
    coach.add_argument("--as-of")
    model = commands.add_parser("model", help="查看或设置 AI 内核（服务商、模型、Key）")
    model.add_argument("--provider", choices=sorted(llm.PRESETS))
    model.add_argument("--model")
    model.add_argument("--base-url")
    model.add_argument("--api-key")
    model.add_argument("--attempts", type=int, help="核验失败后最多作答几次（1-6）")
    model.add_argument("--test", action="store_true", help="发一条消息测试连通")
    commands.add_parser("desktop", help="以桌面窗口打开（需要 pywebview）")
    web = commands.add_parser("web", help="启动本机网页（127.0.0.1）")
    web.add_argument("--port", type=int, default=8765)
    mcp = commands.add_parser("mcp", help="启动 MCP 服务")
    mcp.add_argument("--http", metavar="HOST:PORT")
    return parser


def run(args):
    home = args.home
    command = args.command
    if command == "init":
        return _print(api.init(home, args.demo))
    if command == "status":
        return _print(api.status(home))
    if command == "import":
        return _print(api.import_paths(home, args.paths))
    if command == "checkin":
        event = {"type": args.type, "red_flags": args.red_flag, "illness": args.illness}
        for key, value in (("date", args.date), ("pain", args.pain), ("rpe", args.rpe),
                           ("subjective_recovery", args.recovery), ("note", args.note)):
            if value is not None:
                event[key] = value
        return _print(api.log_event(home, event))
    if command == "report":
        result = api.weekly_report(home, args.as_of)
        print(result["markdown"])
        print("已写入：%s" % result["path"])
        return None
    if command == "ask":
        job = api.start_job(home, args.question, args.task, args.as_of)
        path = os.path.join(api.store.home(home), "jobs", job["job_id"] + ".json")
        print("作业：%s\n任务：%s　技能：%s" % (path, job["task"], ", ".join(job["skills"])))
        print("\n--- 给模型的提示词 ---\n%s\n\n--- coach_context ---\n%s" % (
            job["instructions"], json.dumps(job["context"], ensure_ascii=False, indent=2)))
        print("\n拿到回复后运行：sec complete %s <response.json>" % job["job_id"])
        return None
    if command == "complete":
        result = api.complete_job(home, args.job_id, _read_json_file(args.response))
        if result["valid"]:
            print(result["markdown"])
            pending = [item["key"] for item in result["suggestions"] if item["status"] == "pending"]
            if pending:
                print("待决建议：%s" % ", ".join(pending))
            for item in result["auto_applied"]:
                print("已自动应用计划 %s，可在 %s 前用 sec undo %s 撤销" % (
                    item["key"], item["undo_until"], item["key"]))
            return 0
        _print(result["errors"])
        return 1
    if command == "suggestions":
        return _print(api.pending_suggestions(home))
    if command == "decide":
        return _print(api.decide(home, args.key, args.accept, args.note))
    if command == "plan-apply":
        text = args.text if args.text is not None else open(
            args.file, encoding="utf-8").read()
        return _print(api.apply_plan(home, args.key, text, args.confirm))
    if command == "autonomy":
        return _print(api.set_autonomy(home, args.level, args.confirm) if args.level
                      else api.get_autonomy(home))
    if command == "undo":
        return _print(api.undo(home, args.key))
    if command == "eval":
        result = api.run_eval()
        _print(result)
        return 0 if result["passed"] == result["total"] else 1
    if command == "install-skills":
        return _print(install_skills(args.target, args.dest))
    if command == "profile":
        fields = {key: value for key, value in (
            ("hr_max", args.hr_max), ("hr_rest", args.hr_rest),
            ("goal", args.goal), ("notes", args.notes)) if value is not None}
        return _print(api.set_profile(home, fields) if fields else api.get_profile(home))
    if command == "coach":
        return _coach(home, args)
    if command == "model":
        fields = {key: value for key, value in (
            ("provider", args.provider), ("model", args.model), ("base_url", args.base_url),
            ("api_key", args.api_key), ("max_attempts", args.attempts)) if value is not None}
        if fields:
            llm.save(home, fields)
        if args.test:
            try:
                return _print(llm.ping(home))
            except llm.LLMError as exc:
                print("连接失败：%s" % exc, file=sys.stderr)
                return 1
        return _print(llm.public(home)["config"])
    if command == "desktop":
        import desktop
        return desktop.main(["--home", home] if home else [])
    if command == "web":
        import web
        return web.main(["--home", home, "--port", str(args.port)] if home else ["--port", str(args.port)])
    if command == "mcp":
        import mcp_server
        argv = (["--home", home] if home else []) + (["--http", args.http] if args.http else [])
        return mcp_server.main(argv)
    return None


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        code = run(args)
    except (ValueError, KeyError, PermissionError, FileNotFoundError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1
    return code or 0


if __name__ == "__main__":
    sys.exit(main())
