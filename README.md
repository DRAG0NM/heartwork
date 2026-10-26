<p align="center">
  <img src="docs/images/banner.svg" alt="Sports Eval Core · 把身体数据画成画，再交给你的 AI 教练" width="100%">
</p>

<p align="center">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/Python-3.9%2B-1f2326?style=flat-square&logo=python&logoColor=f4f1ea">
  <img alt="Zero dependencies" src="https://img.shields.io/badge/dependencies-0-868b90?style=flat-square">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-stdio%20%7C%20http-2f8f8a?style=flat-square">
  <img alt="Local first" src="https://img.shields.io/badge/data-local%20only-6d5fd0?style=flat-square">
  <a href="LICENSE"><img alt="License Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-c98a25?style=flat-square"></a>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ｜ <a href="#接到你的-agent">接到你的 agent</a> ｜ <a href="#它怎么保证不胡说">验证机制</a> ｜ <a href="integrations/">各家配置</a> ｜ <a href="CHANGELOG.md">更新记录</a>
</p>

手表每天都在记录 HRV、静息心率、睡眠和训练负荷，但大多数人只看一眼分数就划走了。把这些数字直接丢给 AI，它又很容易顺口编一个"你的恢复不错"。

**Sports Eval Core 是一个本地优先的 AI 运动教练内核。** 它把你从手表导出的文件整理成相对"你自己常态"的指标，每天生成一幅由身体数据画成的画，再把确定性计算好的上下文交给你已经在用的 AI：Claude、ChatGPT、Gemini、Codex、DeepSeek Harness 都行。AI 的每一句结论都必须指回具体数据，数值对不上、越权给处方、无视疼痛红旗，都会被代码拦下，不会展示给你。改训练计划，只有你能拍板。

<p align="center">
  <img src="docs/images/web-dark.jpg" alt="体征画：一次节奏跑后的那一天，负荷带翻涌，红圈是疼痛打卡" width="100%">
</p>
<p align="center"><sub>本机网页「体征画」。图中为 8 周合成示例数据：7 月 22 日节奏跑后，负荷带出现高心率区的结，红圈是当天的疼痛打卡。</sub></p>

## 亮点

- **每天一幅画，每一笔都能溯源。** 恢复、睡眠、负荷、压力、表现各是一束丝带；比你的常态好就舒展成束，紧绷就散乱缠绕。悬停任一笔触，看到它来自哪天哪个指标、你的常态是多少、是哪个导入文件（含 SHA-256）写进来的；没有的数据画成灰色虚线，不做填补。第一次打开有「怎么读」图解，还能把一周七幅导出成海报。
- **相对你自己，而不是全民及格线。** 每天用近 7 天均值对比再往前 28 天的中位数，离散度用 MAD，只有超过最小有意义变化（SWC）才算高或低；HRV 先取对数（lnRMSSD）。基线只在 Python 里算一次，网页、上下文和报告共用。负荷用 ATL/CTL/TSB（Banister），没戴表的训练可用 sRPE（RPE×分钟）记录。
- **挂到任何 agent 上。** 标准库实现的 MCP 服务，Claude Desktop / Claude Code / Gemini CLI / Codex / DSH 一条命令接入；只有网页聊天的 AI，就在网页里一键复制提示词、把回复贴回来。
- **AI 不能自说自话。** 每条观察都要带 JSON Pointer 和观测值，代码逐条比对；实验层不能单独开处方；红黄灯由确定性规则判定，模型不能降级。
- **教练权限由你定。** 保守档只观察提醒；标准档（默认）每条改计划都要你盖章；激进档下，安全门绿灯、有原始数据证据、置信度够、72 小时内没改过时，教练可以直接改计划，你 7 天内一键撤销。档位只能你在网页或命令行改，MCP 只能读，AI 不能给自己提权；红黄灯底线在任何档位都不放开。
- **数据不出本机。** 工作区默认在 `~/.sports-eval`，在仓库之外；网页只监听 127.0.0.1 并校验来源；零第三方依赖、零外部请求。

## 一次问答长什么样

<p align="center">
  <img src="docs/images/web-coach-light.jpg" alt="教练问答：观察和建议后面的小圆点是证据，悬停会在画上高亮对应笔触" width="100%">
</p>
<p align="center"><sub>问"今天还适合跑间歇吗"，AI 的回复通过验证后才显示。每条后面的小标签是证据，悬停会在上方的画里高亮对应的丝带；改计划的那条进了右侧「待你确认」。</sub></p>

## 快速开始

只需要 Python 3.9 以上，没有任何第三方依赖。

克隆或下载本仓库后，在仓库根目录运行：

```bash
python scripts/sec.py init --demo   # 先用 8 周合成数据看看效果
python scripts/sec.py web           # 打开 http://127.0.0.1:8765
```

换成你自己的数据：去掉 `--demo` 初始化一个空工作区，然后把文件拖进网页右侧的「导入」，或者：

```bash
python scripts/sec.py import ~/Downloads/activities/ export.zip daily.csv
python scripts/sec.py profile --hr-max 188 --hr-rest 48   # 有最大心率才会计算心率区间
```

| 格式 | 读取内容 |
|---|---|
| `.fit` | 运动的开始时间、时长、距离、平均心率、逐秒心率（标准库解析，不依赖 SDK） |
| `.gpx` / `.tcx` | 轨迹时间、距离、心率扩展 |
| `.csv` | 每日指标，表头中英文都认：`date/日期`、`hrv`、`静息心率`、`睡眠时长`、`体重`… |
| Apple Health `export.xml` 或 `.zip` | 静息心率、体重、睡眠（按醒来日归属）、体能训练 |

Apple Health 的 HRV 是 SDNN，和本项目使用的 RMSSD 口径不同，导入时会跳过并说明，不会混用。

## 接到你的 agent

```bash
claude mcp add sports-eval -- python scripts/sec.py mcp        # Claude Code
codex mcp add sports-eval -- python scripts/sec.py mcp         # Codex
gemini mcp add sports-eval python scripts/sec.py mcp           # Gemini CLI
```

Claude Desktop、DSH、ChatGPT 的写法见 [integrations/](integrations/)。接好后直接问 agent"今天适合练吗"，它会调用 `start_coach_job` 拿到上下文，按 Response v2 作答，再调用 `submit_coach_response` 交给验证；通过的回答同时出现在网页里。

| MCP 工具 | 作用 |
|---|---|
| `status` / `get_profile` | 工作区概况、档案 |
| `import_files` / `log_event` | 导入文件、记录晨间恢复、RPE、疼痛 |
| `weekly_report` | 生成 Obsidian 周报告 |
| `start_coach_job` | 生成签名作业：上下文、规则、相关 Skill 正文、回复骨架 |
| `submit_coach_response` | 验签、检查数据是否变化、逐条核对证据 |
| `list_pending_suggestions` / `run_eval` | 查看待确认建议、跑行为评测 |

还可以把 8 个专项 Skill（恢复、疼痛、跑步、计划、睡眠、减重、体态、复盘）装进 agent 的技能目录：

```bash
python scripts/sec.py install-skills --target claude   # 也支持 dsh / agents / gemini
```

**没有 MCP 的 AI**（网页版 ChatGPT、Gemini、DeepSeek 等）：在网页里提问，点「复制提示词」，粘贴给 AI，再把它的回复整段贴回来，带不带 ```` ```json ```` 都行。命令行同理：`sec.py ask "问题"` 生成作业，`sec.py complete <job_id> reply.json` 提交。

## 它怎么保证不胡说

```text
 手表文件 ──► 工作区 ──► 周输入 ──► Context v2 ──► 任意 AI ──► Response v2 ──► 验证 ──► 你
 FIT/GPX/CSV   本机      确定性计算   安全门+裁剪     只出 JSON     带证据指针      逐条比对    接受才改计划
```

| 关卡 | 做了什么 |
|---|---|
| **分层** | 原始指标、确定性指标（TRIMP、ACWR、SRI 等）、实验模型（个人基线、多维状态、变化候选）分开存放，互不覆盖 |
| **安全门** | 模型调用之前，代码先看近 14 天的疼痛、生病和红旗：红灯只允许建议就医，黄灯禁止正常加量 |
| **签名作业** | 作业用本机密钥签名；工作区数据在作答期间变化，提交会被判过期 |
| **证据核对** | 每条证据的 JSON Pointer 必须存在、层级必须对、`observed_value` 必须与原值完全一致 |
| **方向核对** | 说"偏高/下降/改善"这类趋势，必须附 `claims`，方向要和确定性基线的判定一致，文字和 claim 也不能自相矛盾 |
| **权限** | 每类任务只允许特定动作；实验层证据不能单独支撑处方；保守/标准/激进三档的规则写进签名作业，作业创建后改档不追溯 |
| **人工确认** | 标准档下改计划只登记为待决；激进档自动应用的每一次都留痕（含旧计划），7 天内可撤销 |
| **文件溯源** | 每个导入文件记入 `imports.json`（文件名、SHA-256、条数），每个指标都能追到来源文件 |
| **行为评测** | 17 个固定场景（伪造数值、方向说反、红灯不升级、激进档黄灯自动改计划、72 小时限频等）全部判对才算通过：`sec.py eval` |

## 命令一览

```bash
python scripts/sec.py status                 # 工作区概况
python scripts/sec.py checkin --type morning_checkin --recovery 6 --pain 2 --note "右小腿紧"
python scripts/sec.py report                 # 写出本周 Obsidian 周报告
python scripts/sec.py suggestions            # 待确认建议
python scripts/sec.py decide <key> --accept
python scripts/sec.py plan-apply <key> --text "周四间歇取消，改轻松跑" --confirm
python scripts/sec.py autonomy autonomous --confirm   # 教练权限：conservative / balanced / autonomous
python scripts/sec.py undo <key>             # 撤销一次自动改计划
python -m unittest discover -s tests -p "test_*.py"
python scripts/release_audit.py              # 发布前隐私审计
```

所有命令都接受 `--home <目录>` 或环境变量 `SPORTS_EVAL_HOME` 指定工作区。

## 边界

- 只用于个人趋势、教育和研究，**不用于诊断、治疗或紧急决策**。出现胸痛、晕厥、无法承重等情况请直接就医。
- 多维状态、变化候选都是实验层，只描述统计偏移，不是健康告警。设备负荷、心率区 TRIMP 和 sRPE 三种负荷口径尺度不同，系统只标注口径、不做换算。
- 心率区间只在你填写最大心率或区间边界后才计算，不会估算。
- 不包含任何厂商账号、抓取器或私有接口；仓库内示例数据全部为人工合成，发布审计会拒绝真实数据文件、绝对路径和凭据。

完整规则见 [PRIVACY](PRIVACY.md)、[SECURITY](SECURITY.md) 和 [DISCLAIMER](DISCLAIMER.md)；方法与指标说明在 [08_方法与边界](08_方法与边界/)。这个仓库同时是一个 Obsidian 库，入口页是 [00_首页](00_首页.md)。

## 目录

```text
scripts/            标准库实现：store、importers、coach_*、api、sec（CLI）、mcp_server、web
scripts/web/        本机网页（单文件，无外部资源）
skills/             8 个专项 Skill 与路由注册表
integrations/       Claude / ChatGPT / Gemini / Codex / DSH 接入说明
schemas/            weekly input、Context v2、Response v2
examples/           仅合成示例
tests/              算法、导入、教练安全、MCP、网页与隐私回归
01–08_*/            Obsidian 模板、周报告与方法文档
```

## 许可

代码与文档按 [Apache License 2.0](LICENSE) 发布。科学方法、第三方名称和引用归各自权利人所有。
