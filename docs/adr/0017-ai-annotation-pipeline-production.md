# ADR-0017 — AI 标注管线落地：判定单元、证据、迁移，与 `gpt-5.6-luna`

- **状态**：已接受
- **日期**：2026-09-11
- **取代**：[ADR-0010](0010-annotations-and-ai-pipeline.md) 的第 1 条（`annotations` 单表形态）、第 2 条的模型选择（Claude Haiku 4.5）、第 3 条（`confidence < 0.7` → 「待确认」）。ADR-0010 的第 4 条（口径归属）**不变，且被本 ADR 加强**。
- **相关**：[ADR-0009](0009-worker-scope.md)、[ADR-0014](0014-data-access-layer.md)、[ADR-0016](0016-sqlite-local-mysql-prod.md)、[runbook](../ai-data-integration-runbook.md) §10/§11/§16

## 背景

[ADR-0010](0010-annotations-and-ai-pipeline.md) 在 2026-09-09 定了标注管线的方向，但它是在**没有 API、没有跑过一条真实评论**的情况下写的。2026-09-11 拿到了可用的网关凭证（CSOP 内部 `amao-prd`，模型 `gpt-5.6-luna`），第一次能用实测代替推断。

实测推翻了 ADR-0010 的三处设计。runbook §2.3 要求「**新建 ADR 取代，而不是修改历史 ADR**」——ADR-0010 记录的是当时基于当时信息做出的正确判断，改掉它就抹掉了「我们为什么曾经那样想」。

## 决策

### 1. 判定单元是 `(comment_id, subject_code)`，不是 `comment_id`

ADR-0010 的表结构里没有 subject。这在只判「这条评论是积极还是消极」时够用，在本项目里不够用：

> 「3033 比 2800 好太多了」

这一条对 3033 是正面、对 2800 是负面。按 `comment_id` 存，两个结论只能留一个 —— 而**产品监控页正是按产品分别展示态度的**。一条评论对不同标的有不同态度，不是边缘情况，是竞品对比场景的常态。

不涉及具体标的的结论（如帖子类型）用 `subject_code = ''`（`NO_SUBJECT` 常量）而不是 `NULL` —— MySQL 的唯一索引不把两个 `NULL` 当重复，用 `NULL` 会让幂等失效。

### 2. 五张表取代单表

| 表 | 存什么 | 为什么不能塞进 `annotations` |
|---|---|---|
| `annotation_runs` | 一次运行的 provider / model / 三个版本 / 用量 | 用量和模型是**运行**的属性，不是单条结论的属性 |
| `annotation_jobs` | 待标注任务、租约、attempts、dead-letter | 队列状态会高频更新，和只读的结论混在一张表里会互相拖累 |
| `annotations` | 结论本身，按 `kind` 一行一个 | — |
| `annotation_evidence` | 原文证据的 `(start, end)` 偏移 | 一个结论可以有多条证据；而且证据必须能**切回原文**，存文本就会和原文漂移 |
| `review_decisions` | 人工复核动作 | 复核是另一条时间线，不能覆盖模型说过什么 |

### 3. 模型改为 `gpt-5.6-luna`，走 Responses API

ADR-0010 定的是 Claude Haiku 4.5，理由是「量大、单条短、结构化输出」。那个理由现在仍然成立，但项目负责人提供的是 CSOP 网关的 `gpt-5.6-luna`，而**没有** Anthropic 凭证。这是可用性决定的，不是对比评测决定的 —— Haiku 4.5 从未在本项目数据上跑过。

实测三处偏离标准（详见 runbook §6.4）：`temperature` 带上就 400；`response_format: json_object` 在这个网关上坏掉；这是**推理模型**，`output[0]` 是 reasoning 项不是答案。结论：用 `/responses` ＋ `text.format.json_schema` ＋ `strict: true`。

`strict` schema 比 `json_object` 强：后者只保证「是合法 JSON」，前者保证「字段、枚举、必填都符合我们的定义」。所以失去 `temperature` 并不影响确定性 —— 确定性来自输出空间被锁死，不来自采样温度。

**换模型不需要改 schema 或 prompt**：provider 是一层接口，`annotations` 记的是 run 而不是厂商。将来拿到 Anthropic 凭证要做对比评测，加一个 provider 实现即可。

### 4. `calibrated_confidence` 一律为 NULL，废除 `confidence < 0.7` 阈值

**这是本 ADR 里最重要的一条。**

ADR-0010 第 3 条让 `confidence < 0.7` 渲染成「待确认」。这条在 LLM 上是错的：模型自报的「我有 0.85 的把握」**不是概率**，它和真实正确率之间没有经过任何校准。拿它当阈值，等于给随机数画一条线。

所以：`annotations` 里根本没有「模型自报 confidence」这一列。有的是 `calibrated_confidence`，而它**只能由在独立校准集上校准过的模型写入**。当前没有校准集，因此 129 条标注的这一列全部是 NULL。

这不是保守，这是铁律 2 的直接推论：**未知就写 NULL，不写一个看起来像数字的东西**。前端的「待确认」态改由 `review_state` 驱动（`pending` / `needs_review` / `approved` / `corrected`）—— 那是一个**事实**（有没有人看过），不是一个伪概率。

### 5. 证据由程序定位，定位不到就不存

模型会「引用」一句原文里并不存在的话 —— 措辞相近、语义相同，但不是逐字。把它存进去，页面上就出现一句用户从没说过的话，而整个产品的可信度建立在「任何结论可以回到原文」上。

所以模型给的证据只是**线索**：程序拿它去原文里找精确匹配，找到就存 `(start, end)` 偏移，找不到就**不存证据，并把这条结论标成 `needs_review`**。结论仍然保留 —— 引用不精确不代表判断是错的，只代表它还没有可展示的凭据。

129 条影子运行的结果：**0 条非逐字引用**。

### 6. 引入 Alembic

`create_all()` 只建缺失的表。它不给已存在的表加列、改类型、加索引，**而且不报错**。本机那个 5.27 GB 的库已经建过表了，在 `create_all` 时代改 `schema.py` 的后果是新环境和老环境结构不同、代码却一样 —— 症状只在某个环境上出现，本机永远复现不了。

`0001` 是基线（冻结迁移引入前的产物），`0002` 建 AI 五张表。已有的库先 `stamp 0001` 再 `upgrade head`。

顺带修掉 runbook §10.3 的 SQLite 主键问题：`BigInteger` 在 SQLite 下渲染成 `BIGINT`，那不是 rowid 别名，autoincrement **静默**失效。用 `AUTO_PK = BigInteger().with_variant(Integer, "sqlite")`。测试不看类型看行为 —— 真插两行，断言主键互异。

### 7. 口径归属不变（ADR-0010 第 4 条保留）

AI 产出**事实**（「这条评论对 3033 是负面的」），把事实聚合成**指标**（情绪净值、赞踩比、热度、排名）仍然只在 `backend/core/`。铁律 1 不破。

本 ADR 加强它：`annotations` 里存的每一行都是可追溯到 run/model/prompt/证据的**观测**，`backend/core/` 消费观测。worker 不做任何聚合。

## 后果

- **可追溯性成为硬约束**：每条结论都能回答「哪次运行、哪个模型、哪版 prompt、哪版标签体系、哪版 schema、原文哪几个字」。`model_id` 记的是**网关返回的**模型名而不是请求里的别名 —— 网关做模型转发时只有前者是事实。
- **幂等基于 `input_hash`**，覆盖正文、上下文、以及 prompt/taxonomy/schema 三个版本。改 prompt 就自动重新入队，不改就一条都不重复发。实测：给 29 条补上下文后重新入队，**恰好**产生 29 个新任务。
- **人工结论不可被模型覆盖**：新行 supersede 旧行时，若旧行是 `approved` / `corrected`，不覆盖。人看过的东西比模型重跑的结果重要。
- **`docs/specs/phase-1-api-integration.md` 与 `CONTEXT.md` 里 `annotations` 带 `confidence`/`model` 的描述已过时**，以本 ADR 为准。
- **成本可算但未算**：用量逐 run 落库（`token_input` / `token_output` / `token_reasoning`），但网关没有给价格，所以 `estimated_cost` 是 NULL。reasoning token 计费却不出现在输出文本里，估算时必须算进输出侧。
- **仍然欠着数据治理**：数据处理地区、日志保留、是否用于训练、删除机制，四项至今未答复（runbook §6.1）。项目负责人已明确授权发送脱敏后的真实评论，据此跑了 129 条。**在这四项补齐之前不应扩大到全量 350k 条评论** —— 这记在这里，是因为授权解决的是「能不能发」，没有解决「发出去之后在哪」。

## 否决的备选

- **保留单表、加一个 `subject_code` 列**：能解决判定单元，解决不了版本、证据、队列状态和人工复核。四个问题各自加两列，最后是一张十几列、一半字段对一半行没有意义的表。
- **存证据文本而不是偏移**：原文一旦被 ETL 重新规整，证据就和原文对不上了，而且看不出来。存偏移的代价是每次展示都要切一次原文，收益是**对不上时立刻炸**。
- **让模型自报 confidence 并直接用**：见第 4 条。
- **模型引用对不上时丢弃整条结论**：过度惩罚。引用不精确是模型的表达问题，判断可能仍然正确 —— 交给人复核，而不是当作没发生。
- **给整批打回**（一条 schema 错就整批失败）：30 条里 1 条坏，重发 30 条既贵又慢。改为**二分**定位坏样本，最多 6 层。
- **继续用 `create_all()` 加迁移脚本约定**：[ADR-0014](0014-data-access-layer.md) 原本这么写。人工 DDL 脚本没有版本表，无法回答「这个库现在是第几版」，也无法回滚。
