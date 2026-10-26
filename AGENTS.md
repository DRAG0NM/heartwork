# Sports Eval Core · AI 协作规则

面向 Codex、DSH(DeepSeek Harness)、OpenCode、Gemini CLI 等读取 `AGENTS.md` 的 agent。个人数据全部在本机工作区（默认 `~/.sports-eval`，可用 `SPORTS_EVAL_HOME` 或 `--home` 指定），不在仓库里。

## 硬规则

1. 个人数据任务只读本地输入，不联网、不上传。
2. 先取教练上下文（MCP `start_coach_job` 或 `python scripts/sec.py ask`），不要自行扫描或拼接私人数据目录。
3. 只输出 Response v2；每条关键观察必须包含 `source/pointer/layer/observed_value`。
4. 回答展示前必须通过验证（MCP `submit_coach_response` 或 `python scripts/sec.py complete`）；失败不得绕过。
5. 实验层不能单独触发训练处方；红黄灯不得由模型降级。
6. 训练计划变更必须等待使用者明确确认；你不能替用户接受建议或应用计划。
7. 公开贡献只能使用 `synthetic: true` 的人工合成数据。

## 优先：MCP

已配置 MCP 服务（`python scripts/sec.py mcp`，配置见 `integrations/`）时：

1. `status` 看工作区概况；有新文件用 `import_files`，有主观信息用 `log_event`。
2. `start_coach_job(query)` 拿到 `context`、`instructions`、`skill_texts`、`response_template`。
3. 只按 `instructions` 产出一个 Response v2 JSON，调用 `submit_coach_response(job_id, response)`。
4. `valid=false` 就按 `errors` 修正后重交；`valid=true` 把渲染结果讲给用户。若有待决建议，告诉用户去网页（`python scripts/sec.py web`）或 CLI 确认，不要自己确认。

## 没有 MCP：用 CLI

```
python scripts/sec.py ask "今天该怎么练？"          # 生成作业，打印提示词与 coach_context
# 你读取输出，产出 Response v2 JSON，保存为 response.json
python scripts/sec.py complete <job_id> response.json   # 验证；失败打印错误码
python scripts/sec.py suggestions                       # 查看待决建议（只读）
```

其他可用命令：`init [--demo]`、`status`、`import <路径...>`、`checkin ...`、`report`、`eval`。`decide` 与 `plan-apply` 只能由用户本人运行。

## 技能

`skills/coach-*/SKILL.md` 是各场景的判断要点（恢复、疼痛、跑步、计划、睡眠、减重、体态、复盘）。`start_coach_job` 会返回被选中的技能正文；也可用 `python scripts/sec.py install-skills --target agents` 复制到项目 `.agents/skills`。
