---
name: coach-review
description: 一周复盘的结构与措辞：先事实、再解读、再建议，并列出不确定性。
---

# coach-review

通用原则：只输出 Response v2 JSON；每条观察/建议带 `coach_context` 证据指针与 `observed_value`；不做医学诊断；改计划必须经人确认。
趋势判断（高于/低于/持平个人基线）要写 claims：`{metric, direction}`，direction 必须等于 `views.baseline_status` 中该项的 status；方向按数值高低，不按好坏；方向词与 claims 要一致，每条观察只讲一个方向。

- 结构：本周事实（带证据）-> 解读（标注置信度）-> 建议（标注是否改计划）-> 不确定性与待确认问题。
- 每条观察和建议必须有 coach_context 证据指针和 observed_value。
