# ADR-0031 — 每日 08:00 全量更新、单一 AI 队列，页面数据完整优先

- **状态**：已接受
- **日期**：2026-10-06
- **取代**：[ADR-0026](0026-scheduled-ai-budget-and-calibration-gate.md) §1「等待当天
  `comments_all`」、§2 的每日 500 次硬上限、§3「批量校准或人工金标不匹配时零调用」，
  以及 §4 中「治理开关缺失即 fail closed」；[ADR-0030](0030-content-cashtag-comment-routing-and-airflow-analysis.md)
  中「Daily runs cover `d1,d2,d7`」与「All release gates remain in force」。
  按评论量 Top 10 选品的做法也一并取代。
- **保留**：[ADR-0019](0019-ai-auto-publish-no-human-gate.md)，即模型写下即发布、
  `/meta.aiValidation` 恒为 `none`；板块总览 S6 与产品监控 P7 的「未经人工核验」披露不变。

## 背景

页面上的 AI 总结全部空白，管理页 Layer B 全显示「待处理」，最近运行全部失败。排查结果是五处
问题叠加，任何一处单独存在都足以让页面没有 AI 文案：

1. Layer A 切换到 TypeSafe System One（`supports_generation = False`）之后，
   `pipeline.run` 直接跳过 Layer B。热议总结、舆情总结、主题名、阶段观点、竞品原因因此永远
   不会生成。
2. 切到 `comment-product-v4` 后，旧的校准报告和人工金标报告全部失配；治理开关默认又是
   `false`。worker 心跳一直「配置未就绪」，任务全部失败。
3. Airflow 每天 08:00 启动，却等当天 19:30 的 `comments_all`，最长等 8 小时，16:00 超时失败；
   而且这条路径不经过管理页的队列，结果看不到。
4. `_synth` 在 `synth_dirty=1` 或 anchor 前进后直接返回空，每次同步或标注后结论都会消失；
   跑完 d1 还会把页面默认的 d7 标脏。
5. 管理页和 worker 各自挑 Top 10，口径还不一致；KOL 页和官号页的帖子标注依赖分析范围，
   于是也跟着缺数据。

## 决策

### 1. 一个入口、一个队列

`worker/jobs/daily.py` 是唯一的每日入口，依次执行：

1. 配置了 `MARKET_INSIGHT_DATABASE_URL` 时，找今天或前一晚最近一次成功的 `comments_all` 回执，
   经 `sync_once` 同步，让 anchor 前进；未配置时即「本地 dump 模式」，记为跳过；
2. 配置了 `FMP_API_KEY` 时同步自家行情；
3. 把 `d1,d2,d7,d14,d30,mtd` 写进 `ai_analysis_requests`，`requested_by='schedule'`。

同步或行情失败不阻断第 3 步：AI 照常在库里已有的数据上运行。worker 的
`AI_DAILY_CRON`（默认 `0 8 * * *`，香港时间）与 Airflow DAG（`jobs.daily --now --drain`）
调用的是同一个函数，二选一启用。管理页按钮（`requested_by='admin'`）写入同一张队列表，
最近运行里两种来源都能看到。Alembic `0015` 增加 `requested_by` 列，并把 `mtd` 加入区间约束；
worker 启动时对带 `alembic_version` 的库自动执行 `upgrade head`。

### 2. 去掉运行门禁

运行前不再校验校准报告和人工金标报告；`scripts.calibrate` 与 `scripts.evaluate_gold`
保留为离线工具。`AI_DATA_GOVERNANCE_APPROVED` 未设置即视为开启，显式设为 `false` 才停掉
全部 AI 请求。每个香港自然日的 HTTP 上限提高到 100000；同一天内换模型、批策略或上限时，
预算行直接改用新值，不再报错。

### 3. Layer B 使用能写文字的模型

`AI_SYNTH_PROVIDER/BASE_URL/API_KEY/MODEL` 为 Layer B 单独配置生成模型，与 Layer A 共用同一个
`RunControl` 预算；全部留空时沿用 `AI_PRIMARY_*`。只配置了分类型 provider 时，管理页检查清单
会把「Layer B 生成模型」标红，运行摘要写明原因。

### 4. 覆盖全部产品，跑到底

`backend/core/top_products.analysis_products()` 是三个入口共用的选品函数：默认选全部有效
产品，自家在前；`AI_OWN_TOP_LIMIT=N` 时改为自家前 N 只加全部竞品。区间内没有评论的产品
不会产生模型请求，Layer B 直接发布空结果。每个区间开跑前，把所选产品中 failed/dead 的
Layer A 任务重置一次。基准期早于库内 `window_from` 时，自动退回只跑当前窗，不再整轮失败。
运行摘要列出仍缺的「产品 × 类别」；只要还有缺失，状态就记为「部分完成」。

### 5. 有就显示，旧了标「待更新」

`_synth` 读取 `synth_current_*` 指向的最近一次发布：可以是旧 anchor，比当前 anchor 新的则
忽略。被标脏或来自旧 anchor 时照常下发，同时带 `stale: true`，前端显示已有的
「AI 生成 · 待更新」徽章。

## 后果

- 首次全量要把库内全部评论和帖子过一遍模型，请求量与耗时远高于以后；之后输入指纹未变的
  部分直接复用。
- 费用只受评论量约束。需要收紧时调低 `AI_MANUAL_MAX_HTTP_REQUESTS`，或把
  `AI_DATA_GOVERNANCE_APPROVED` 设为 `false`。
- 基准期在数据覆盖之外的对比数（例如 d30 环比）如实显示「数据暂不可用」。
- 页面结论没有经过人工验证，这一点照旧在 `/meta.aiValidation`、S6 和 P7 如实说明。
