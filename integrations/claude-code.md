# Claude Code

在仓库（或你的项目）根目录运行：

```
claude mcp add sports-eval -- python scripts/sec.py mcp
```

或在项目 `.mcp.json` 中写：

```json
{
  "mcpServers": {
    "sports-eval": { "command": "python", "args": ["scripts/sec.py", "mcp"] }
  }
}
```

在别的项目中使用时，把 `scripts/sec.py` 换成你克隆路径下的 `scripts/sec.py`。

技能（可选）：在目标项目根目录运行 `python <你的克隆路径>/scripts/sec.py install-skills --target claude`，把 `coach-*` 复制到 `.claude/skills/`。

指定工作区：加 `--home <工作区路径>`（写在 `mcp` 之前）或设置环境变量 `SPORTS_EVAL_HOME`。
