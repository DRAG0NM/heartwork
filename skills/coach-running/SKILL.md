---
name: coach-running
description: 跑步训练与运动后反馈的解读：配速、心率、间歇、长跑。
---

# coach-running

通用原则：只输出 Response v2 JSON；每条观察/建议带 `coach_context` 证据指针与 `observed_value`；不做医学诊断；改计划必须经人确认。

- 引用活动的距离、时长、平均心率与心率区间分钟数，不编造缺失字段。
- 区间分钟缺失表示未设置最大心率，应提示在 profile 中设置，而不是估算。
- 单次训练不足以改计划，只给运动后恢复与下一次训练的保守提示。
