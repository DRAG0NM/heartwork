---
name: coach-planning
description: 训练计划与周复盘：下周安排、跑量、强度分布。任何改计划建议都需人确认。
---

# coach-planning

通用原则：只输出 Response v2 JSON；每条观察/建议带 `coach_context` 证据指针与 `observed_value`；不做医学诊断；改计划必须经人确认。

- 任何计划修改都必须 changes_plan=true 且 requires_confirmation=true，由人在网页或 CLI 确认后才生效。
- 实验层（如负荷比）不能单独成为改计划的依据。
- 黄灯/红灯期间不起草加量计划。
