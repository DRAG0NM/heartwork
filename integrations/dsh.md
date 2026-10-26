# DSH (DeepSeek Harness)

DSH 读取项目内的 `AGENTS.md` 与 `.dsh/skills/`。

1. 技能：在目标项目根目录运行
   `python <你的克隆路径>/scripts/heartwork.py install-skills --target dsh`
   （复制 `coach-*` 到 `.dsh/skills/`）。
2. 规则：把本仓库 `AGENTS.md` 放到（或并入）项目根的 `AGENTS.md`。
3. MCP（若你的 DSH 版本支持 stdio MCP）：按它的 MCP 配置格式登记命令
   `python scripts/heartwork.py mcp`，工作目录设为 `<你的克隆路径>`。具体配置文件格式请以 DSH 自带文档为准。
4. 不用 MCP 时：按 `AGENTS.md` 的 CLI 流程（`heartwork.py ask` → 产出 Response v2 → `heartwork.py complete`）。

DeepSeek 类模型务必遵守：只输出一个 JSON 对象；`observed_value` 必须与指针处的值逐字一致。
