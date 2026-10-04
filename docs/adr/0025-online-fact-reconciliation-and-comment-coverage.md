# ADR-0025 — 在线事实按语义对账，并显式记录评论覆盖与 24 小时计数观察

- **状态**：已接受
- **日期**：2026-09-25
- **相关**：[ADR-0009](0009-worker-scope.md)、[ADR-0011](0011-comment-volume-caliber.md)、[ADR-0022](0022-heat-lower-bound-disclosure.md)、[ADR-0024](0024-airflow-source-boundary-and-dataset-sync.md)

> **2026-09-29 修订**：[ADR-0029](0029-parent-feed-comment-qualification.md) 保留本文的在线对账与评论覆盖机制，但页面评论量改为合格父帖的 `comment_count` 之和；筛选前平台量只用于采集与漏斗审计。

## 背景

历史导入把一份静态 `raw_json` 拆成 facts；在线同步则会反复看到同一帖子：正文可能被
详情任务补长，评论可能晚到或只返回一部分，平台计数会继续变化，用户资料也会更新。
若把任意字段变化都当成新内容，会产生不必要的 AI 调用；若把部分评论快照当成完整集合，
又会错误删除已经抓到的评论。

同时，ADR-0011 已经裁定：页面“评论量”来自平台 `comment_count`，AI 只分析实际取得的
评论正文。在线采集必须保留这两个数的差异，而不是让后一次快照掩盖它。

## 决策

### 1. 三条增量流使用稳定 keyset 游标

- 新帖子：`(scraped_at, feed_id)`
- 详情更新：`(detail_updated_at, feed_id)`
- 用户更新：`(scraped_at, user_id)`

每次运行先固定 high-watermark，再按上述二元键分页。只接纳当前 120 个产品；源库中的
其他产品计入 `ignored`，不写入 Radar。`feed_id` 与 `comment_id` 是幂等 upsert 的稳定键。

### 2. 失效按语义变化，而不是按“行被写过”

帖子正文、评论正文／归属、mention 的新增、修改或可证明的删除会推进 AI 数据版本，并把
受影响的产品区间标成 stale。可解析的详情流把当前标题、正文和正文提及作为同一份权威
快照事务性替换，因此作者缩短或改写正文时旧 mention 会同步清除；载荷损坏时三者都保留
旧值，不能用不完整输入做删除证明。

用户昵称、粉丝数等资料变化，以及点赞、浏览、评论量等计数变化，只推进事实数据版本，
不重新调用评论分类模型。它们仍会刷新 API，但不会制造无意义的 AI 账单。

### 3. 评论覆盖是独立事实

帖子内部保存四态：

```text
complete | partial | retryable_incomplete | unknown
```

- `complete` 且载荷可解析时，快照可以证明集合完整，才允许删除该帖已不存在的旧评论。
- `partial`、`retryable_incomplete` 或 `unknown` 只能 upsert 已取得的评论，绝不删除旧评论。
- 外部 API 将 `retryable_incomplete` 合并展示为 `partial`，因此兼容字段保持
  `complete | partial | unknown`。

页面把合格父帖的 `comment_count` 之和作为筛后评论量；`parsedCommentCount` 只表示合格
父帖下 AI 实际可读的评论正文数，并明确披露“正文可能为部分覆盖”。筛选前平台总量单列
为审计字段，不进入热度或排名。

### 4. 计数以观察序列保留，并冻结约 24 小时值

日常采集使用 checkpoint 加最近 48 小时重叠窗口，以接住晚到评论和仍在变化的计数。
源库在每次成功重抓时推进该帖的观察时间，即使业务字段和计数恰好未变；业务字段本身仍是
条件 upsert。这样目标 keyset 能看到“数值未变但确实再次观察”的事实。每次平台计数写入
`feed_counter_observations`：

- 帖子发布未满 24 小时时，读最新观察，状态为 provisional；
- 发布满 24 小时后，取第一条不早于 24 小时的观察作为 settled；
- settled 后普通同步不再替换该值；只有显式 `repair` 模式可修复历史。

这给“发布后约 24 小时”的业务文案一个可复现的事实来源，也不以当前累计值冒充历史值。
转发数缺失时仍按 ADR-0022 的下限披露规则处理。

## 理由

- 二元 keyset 能在同一时间戳有多行时稳定续跑，固定 high-watermark 防止无限追逐新写入。
- 语义版本把“页面事实刷新”和“需要重新推理”拆开，避免用户资料或计数变化触发 AI。
- 覆盖状态使删除具有可证明前提；“没有在这页看到”不等于“源站已经删除”。
- 观察表同时保存 provisional 与 settled 过程，后续审计可以还原页面为何采用某个计数。

## 后果

- `feeds` 增加来源观察时间与 `comment_coverage_status`；目标库增加
  `collector_checkpoints`、`ingestion_runs` 与 `feed_counter_observations`。
- API 的 `dataCollection` 会同时暴露数据新鲜度、评论覆盖、完整日期、最后同步时间、
  平台评论量与已解析评论量。
- backfill、incremental 与 repair 使用独立语义；repair 不能被日常调度误触发。

## 否决的备选

- **按更新时间单列游标**：同一时间戳的行可能被跳过或重复，无法稳定恢复。
- **任何 upsert 都让 AI stale**：用户资料和计数更新会反复重算同一文本。
- **快照未带某评论就删除**：部分分页会永久丢数据。
- **直接覆盖帖子计数**：无法兑现“发布后约 24 小时”，也无法区分 provisional 与 settled。
