# Changelog

## v0.4.1 · 2026-10-26

- 项目更名为 Heartwork 健康画布，仓库改名为 `heartwork`。
- 命令行改为 `scripts/heartwork.py`；`scripts/sec.py` 保留为别名，已有的 MCP 配置无需修改。
- 工作区默认改为 `~/.heartwork`，环境变量改为 `HEARTWORK_HOME`；已存在的 `~/.sports-eval` 和 `SPORTS_EVAL_HOME` 继续生效。
- README 重写。

## v0.4 · 2026-10-26

- 统一个人基线（`scripts/baseline.py`）：近 7 天对比再往前 28 天（不重叠）的滚动窗口，MAD 稳健尺度，SWC 判定高/低/正常；HRV 用 lnRMSSD。网页不再自己算基线，改用 `/api/daily`。
- 负荷：Banister ATL/CTL/TSB、非耦合急慢比；无心率区的训练可用 sRPE；每次活动标注 `load_method`。负荷轴改用 TSB，演示数据下可用。
- 网页：滚动时的迷你画、「怎么读」图解、一周七幅海报（PNG 导出）、教练权限三档切换、自动改计划的撤销按钮、结论方向标签、证据溯源到导入文件。

- 新增教练权限档位（保守/标准/激进，`scripts/coach_policy.py`）：写入作业签名；激进档在护栏内可自动应用计划，7 天内可撤销；MCP 只读，不能改档。
- Response v2 新增 observation `claims`，核对结论方向（高于/低于/持平个人基线）及与文字方向词的一致性。
- 新增文件级溯源：`imports.json`、每日指标 `_files`、活动 `source_file`、`api.provenance`。
- `coach_eval` 扩充到 17 个回放案例。

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
