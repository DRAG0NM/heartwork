# Gemini CLI

`~/.gemini/settings.json`（全局）或项目 `.gemini/settings.json`：

```json
{
  "mcpServers": {
    "heartwork": {
      "command": "python",
      "args": ["scripts/heartwork.py", "mcp"],
      "cwd": "<你的克隆路径>"
    }
  }
}
```

也可用命令：`gemini mcp add heartwork python scripts/heartwork.py mcp`（在仓库根目录）。

上下文与技能：在目标项目根目录运行

```
python <你的克隆路径>/scripts/heartwork.py install-skills --target gemini
```

它把 `coach-*` 复制到 `.gemini/skills/`，并在 `GEMINI.md` 里写入一段用 `@./.gemini/skills/<name>/SKILL.md` 引用技能的标记块（重复运行只会替换该块，不动你的其他内容）。`@file` 导入是 Gemini CLI 的 GEMINI.md 导入语法；该做法与你所用版本的兼容性未在此验证，若版本不支持，可直接让 Gemini 读 `AGENTS.md`。

Gemini CLI 默认只读 `GEMINI.md`；要让它读 `AGENTS.md`，在 settings 里把 `context.fileName` 设为 `["GEMINI.md", "AGENTS.md"]`（字段名以你的版本文档为准）。
