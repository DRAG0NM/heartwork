# Codex CLI

`~/.codex/config.toml`：

```toml
[mcp_servers.sports-eval]
command = "python"
args = ["scripts/sec.py", "mcp"]
cwd = "<你的克隆路径>"
```

或：`codex mcp add sports-eval -- python scripts/sec.py mcp`（在仓库根目录）。

Codex 会自动读取仓库根的 `AGENTS.md`，里面已写明工作流与硬规则。在别的项目中使用时，把本仓库的 `AGENTS.md` 内容并入该项目的 `AGENTS.md`，并把命令里的 `scripts/sec.py` 换成你克隆路径下的文件。

可选技能：`python <你的克隆路径>/scripts/sec.py install-skills --target agents`（复制到 `.agents/skills`）。

没有配置 MCP 时，Codex 按 `AGENTS.md` 的 CLI 流程（ask → 产出 Response v2 → complete）也能工作。
