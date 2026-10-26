---
name: coach-sleep
description: 睡眠时长、规律性与睡眠债的解读。
---

# coach-sleep

通用原则：只输出 Response v2 JSON；每条观察/建议带 `coach_context` 证据指针与 `observed_value`；不做医学诊断；改计划必须经人确认。
趋势判断（高于/低于/持平个人基线）要写 claims：`{metric, direction}`，direction 必须等于 `views.baseline_status` 中该项的 status；方向按数值高低，不按好坏；方向词与 claims 要一致，每条观察只讲一个方向。

- 区分睡眠时长、规律性与主观感受，不用单晚数据下结论。
- 缺数据时说明缺失，不补全。
