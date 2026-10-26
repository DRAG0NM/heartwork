# Changelog

## v0.3 · 2026-10-26

- 新增本地工作区（默认 `~/.sports-eval`），数据不再需要手写 weekly input。
- 新增文件导入：FIT、GPX、TCX、通用 CSV、Apple Health 导出（XML/ZIP）。
- 从完整版移植并通用化教练运行层：打卡事件流、8 个专项 Skill 与路由、签名作业、决策确认与计划授权、行为评测回放。
- 新增统一命令行 `scripts/sec.py`。
- 新增标准库 MCP 服务（stdio 与本机 HTTP），以及 Claude、ChatGPT、Gemini CLI、Codex、DSH 接入说明和 `install-skills`。
- 新增本机网页「体征画」：每天由身体数据生成的画、三层视图与个人基线语义色、证据可溯的教练问答、「复制给任意 AI」模式、人工确认计划。
- `AGENTS.example.md` 改为正式的 `AGENTS.md`。

## v0.2 · 2026-08-12

- 恢复并扩展标准库单元测试。
- 周报告新增原始单指标层和多维个人状态实验层。
- 新增稳健个人基线、个人相对状态和变化候选。
- 新增 Context v2、确定性安全门、Response v2 精确证据验证和计划确认门。
- 新增三份 JSON Schema 与全合成端到端示例。
- 强化发布审计：拒绝绝对本机路径、真实数据容器、凭据赋值和非合成示例。

## v0.1 · 2026-08-11

- 首个厂商无关、Obsidian 友好的确定性周报告核心。
