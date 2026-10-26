# Claude Desktop

编辑 Claude Desktop 配置（设置 → 开发者 → 编辑配置，即 `claude_desktop_config.json`），加入：

```json
{
  "mcpServers": {
    "heartwork": {
      "command": "python",
      "args": ["scripts/sec.py", "mcp"],
      "cwd": "<你的克隆路径>"
    }
  }
}
```

说明：

- Claude Desktop 不一定以仓库为工作目录，请把 `<你的克隆路径>` 换成你克隆本仓库的目录；若 `cwd` 不生效，改为把 `args[0]` 写成该目录下 `scripts/sec.py` 的完整路径。
- 指定工作区：在 `args` 末尾前加 `"--home", "<工作区路径>"`（全局参数写在 `mcp` 之前：`["scripts/sec.py", "--home", "<工作区路径>", "mcp"]`），或用 `"env": {"SPORTS_EVAL_HOME": "<工作区路径>"}`。
- 重启后在对话里选 prompt `coach_daily` / `coach_weekly`，或直接问“今天该怎么练”。
- 模型无法接受建议或应用计划，需要你在网页或 CLI 确认。
