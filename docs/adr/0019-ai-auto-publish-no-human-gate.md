# ADR-0019 — AI 标注全自动发布：取消人工批准门槛

- **状态**：已接受（项目负责人 2026-09-11 拍板）
- **日期**：2026-09-11
- **取代**：[ADR-0017](0017-ai-annotation-pipeline-production.md) §4 中「`SqlProvider` 只读 `approved` / `corrected`」这一条发布规则，以及「待确认由 *有没有人看过* 驱动」的门槛含义。ADR-0017 的其余各条（判定单元、五张表、`gpt-5.6-luna`、证据程序定位、Alembic、口径归属）**全部保留**。
- **作废**：[runbook](../ai-data-integration-runbook.md) §21.3 提议的 `auto_approved` 状态与双 Prompt 一致性门槛 —— 未实施，不再实施。
- **相关**：[ADR-0010](0010-annotations-and-ai-pipeline.md)、[PRD §3.6／§3.9／§4.2 P10](../PRD.md)、CLAUDE.md 铁律 2

## 背景

Gate 0–2 跑完后的状态是：129 个判定单元、225 行 `annotations` 已在库里，页面上却一个 AI 字段都没亮。原因是 ADR-0017 §4 把 `review_state ∈ {approved, corrected}` 定为发布条件，而没有任何一条被人批过。

项目负责人的决定有三条，逐字记在这里，因为它们是本 ADR 的全部依据：

1. **不设置成需要 approve**——所有 AI 结论都可以直接用。
2. **全部改成 AI 自动识别**——不做人工复核，也不做抽检。
3. 这条决定**单独成文**，不并入 runbook。

这不是工程判断，是产品负责人对「先亮起来」与「先验证」之间的取舍。本 ADR 的工作是把这个取舍落成一套**不撒谎**的规则：结论可以全部上页面，但页面必须说清楚它们是什么。

## 决策

### 1. 发布规则：非 `rejected` 的链末行即为现行结论

对每个判定单元 `(target_type, target_id, subject_code, kind)`：

```text
现行结论 = annotations 里
           ① 没有被任何行 supersede（即链末），且
           ② review_state != 'rejected'
           的那一行；
           若链末有多条（ADR-0017 遗留的双现行情况），人工 approved/corrected 优先，
           同一优先级再取 created_at 最新的一条；
           若链末是 rejected ⇒ 该单元当前没有结论（页面显示「暂不可用」，不回退到旧行）。
```

`SqlProvider` 按这条规则读，**没有别的条件**。`pending` 与 `needs_review` 一样可读。

### 2. `review_state` 从「门槛」变为「徽章」

它仍然是一列事实，只是不再决定能不能显示，只决定显示成什么样：

| `review_state` | 页面徽章（PRD §3.9 徽章体系逐字） | 说明 |
|---|---|---|
| `pending` | 「AI 生成 · 可追溯原文」；无可定位证据时退为「AI 生成」 | 模型给出结论且未举手 |
| `needs_review` | 「AI 生成 · 待确认」 | 模型自报存疑，或证据定位失败（ADR-0017 §5） |
| `approved` / `corrected` | 同 `pending` | 将来若有人用 `review.py` 看过，只在库里留痕，页面**不**另加「已核验」——PRD 没有这枚徽章，不能凭空造一枚 |
| `rejected` | 不显示 | 唯一的下线通道 |
| kind = `compliance`（任何状态） | 「AI 识别 · 待人工确认」 | PRD §4.2 P10 逐字，恒定，不随 `review_state` 变 |

「待确认」的**唯一**触发条件是 `review_state == 'needs_review'`。`calibrated_confidence` 继续全为 NULL（ADR-0017 §4 这一半不变）；`lowConfidence = 0.7` 在校准概率存在之前**不生效**。PRD §3.5／§3.9 中「置信度低于 0.7 标待确认」的表述应改为「校准置信度存在且低于阈值，或模型自报存疑，标待确认」——这是 PRD 维护者的待办，本 ADR 不替 PRD 改字。

### 3. 不做人工复核、不做抽检、不做一致性门槛

- `worker/jobs/review.py` **保留**，降级为可选工具：`--reject` 是唯一能让一条结论下线的手段，`--correct` 是唯一能改值的手段。它不再是「标注到页面之间的那道门」；它的模块 docstring 需要相应改写。
- runbook §21 提议的双 Prompt 一致性、200 条抽检、`auto_approved` 三项**都不做**。理由不是它们没用，是负责人不要为一个不能对外陈述的数字多付一倍 API 费用和人力。

### 4. 页面与接口必须如实声明「未经人工验证」

这是铁律 2（不用看起来像数字的东西冒充未知）在文案层面的推论。三处必须一致：

| 位置 | 内容 |
|---|---|
| `/meta` | 新增 `aiValidation: "none"`（枚举：`none` / `spot_check` / `gold`；本 ADR 下恒为 `none`） |
| 口径与数据状态面板（板块总览 S6、产品监控 P7 元信息） | 新增一句：「AI 结论由模型自动生成，未经人工验证；每条可回到原文。」 |
| 演示／汇报 | 截图必须说明 provider 与 `aiValidation`，同 README「Which provider is serving」的要求 |

不允许出现「已核验」「准确率 xx%」或任何暗示人工确认过的表述。

### 5. 重跑保护保留，但只保护人改过的行

自动重跑写新行时：

- 旧行是 `pending` / `needs_review` ⇒ 新行 supersede 旧行（现有行为）。
- 旧行是 `approved` / `corrected` ⇒ 新行**不** supersede，写成 `pending`（现有行为）。这条本来是为人工复核设计的；在本 ADR 下它只在有人用过 `review.py` 时才会触发，保留它没有成本。
- 旧行是 `rejected` ⇒ 新行 supersede 它，链末回到有结论的状态。人否决的是**那一次**的结论，不是这个判定单元永远不能有结论；换了 Prompt 或模型版本重跑出来的新结论应当重新上线。

### 6. 口径归属不变

模型写事实（「这条评论对 3033 是负面的」），`backend/core/` 把事实聚合成指标（情绪净值、赞踩比、样本阈值、`complianceCount`）。`SqlProvider` 只负责按第 1 条规则把行取出来并**数**出来，公式一律在 `core/`。铁律 1 不破，ADR-0017 §7 不变。

## 后果

- **页面能亮**：跑全量标注后，态度、帖子三件套、合规关注即从「暂不可用」变为真实输出，中间不再等人。
- **准确率是未知的，并且会一直未知**：没有金标就没有分母。这一点在 `/meta`、面板文案与所有汇报里都必须写出来，不能省略。
- **错误结论会直接出现在页面上**。缓解手段有且只有三样：每条可回到原文（证据侧栏）、徽章如实标「AI 生成」、`review.py --reject` 可随时下线一条。它们缓解的是后果，不是概率。
- **`needs_review` 不藏**：把模型最不确定的那部分藏起来，页面会显得比实际更确定——那是另一种形式的撒谎。它们照常显示，只是徽章不同。
- **合规关注模块不受本 ADR 影响**：PRD 对它的定义本来就是「AI 识别 · 待人工确认」（只标信号、给原文与命中依据，不判真伪），本 ADR 只是让其余模块向它对齐。
- **全量标注的前置条件没有变**：runbook §6.1 的数据治理四项（区域、日志保留、训练使用、删除机制）仍未答复。本 ADR 决定的是「结论能不能上页面」，不是「评论能不能发出去」——后者是另一个问题，授权在负责人手里。
- 以下文件中「`SqlProvider` 只读 `approved` / `corrected`」的描述**已过时**，以本 ADR 为准：CLAUDE.md、`worker/jobs/review.py` docstring、runbook §16 Gate 4 与 §21.3、`backend/providers/sql.py` 模块头。它们由实施本 ADR 的提交一并修正。

## 实施清单

按依赖顺序，每一项都可独立验收：

| # | 改动 | 落点 | 验收 |
|---|---|---|---|
| 1 | 现行结论查询：按第 1 条规则取链末、排除 `rejected`、同链末优先人工结论后取最新 | `radar_db/annotations_read.py` 的 `current_annotations(...)` | 单测：pending 可读；rejected 不可读；人工结论优先；普通链末双行取最新；rejected 链末 ⇒ 无结论 |
| 2 | 评论态度聚合：按产品×时间桶数 `attitude` 的正／负／中；`sampleSufficient` 用 `LOW_SAMPLE` | `sql.py` 的 `pool` / `benchmark` / `_scan`，阈值判定在 `core/` | 有标注的产品 `attitude` 不再为 `None`；没标注的仍为 `None`（不是 0） |
| 3 | 帖子三件套：`post_type` / `direction` / `summary` 回填到 `_post_common`，替换 `_UNANNOTATED` 中对应键；`summary=false` 占位 ⇒ `hasSummary=false`；`direction="pending"` ⇒ `directionPending=true` | `sql.py` 官号与 KOL 帖子组装 | KOL／官号页显示真实类型与摘要，未标注帖子仍显示缺失态 |
| 4 | 证据：按 `annotation_evidence` 回填 `evidenceIdx` / `typeEvidence` / `evidenceFor` | `sql.py` `evidence_for`，`core/evidence.py` | 证据侧栏引文能在原文里逐字定位 |
| 5 | 合规关注：kind=`compliance` 全部读取（非 rejected），`peer ⇒ na`，无命中 ⇒ `empty`，未扫描 ⇒ `unavailable` | `sql.py` `compliance_for`，`pool().complianceCount` | 四态与 PRD §4.2 P10 一致；每条带 `rationale` 与证据 |
| 6 | `/meta` 增加 `aiValidation: "none"` | `backend/core/meta.py`、`fixtures/meta.json`（demo 也带此键，值同为 `none`——演示数据里的 AI 字段更不是验证过的） | 契约测试 |
| 7 | 徽章映射：`review_state` → 徽章文案；`compliance` 恒定；面板新增未验证声明 | `frontend/src/lib/view.js`、板块总览 S6、产品监控 P7 | 六态测试新增断言：`needs_review` 渲染「AI 生成 · 待确认」，且不出现「已核验」 |
| 8 | 放开测试：`backend/tests/test_sql_provider.py::TestAiAndPriceSurfacesAreNone` 改为「无标注时为 None、有标注时为真值」；行情相关断言不变 | 测试 | 全绿 |
| 9 | 文案与文档：`review.py` docstring、`sql.py` 模块头、CLAUDE.md、runbook §16 Gate 4／§21.3 改为指向本 ADR | 文档 | grep 不再出现「只读 approved」 |
| 10 | 全量排队与运行（own 产品近 30 天 → 全池 120 天），**在 §6.1 数据治理四项答复之后** | `worker/jobs/annotate.py` 现有 CLI | `annotation_runs` 可追溯；页面亮起 |

第 1–9 项不发任何请求、不花钱，可以立刻做；第 10 项等授权。

## 否决的备选

- **维持 ADR-0017 §4 的人工批准门槛**：没有人力去批，页面永远不亮。负责人明确不要。
- **`auto_approved` ＋ 双 Prompt 一致性**（runbook §21.3 原提案）：两次调用换一个「一致率」，而一致率不是准确率——两套 Prompt 一起错的时候它照样高。多付一倍费用买一个不能对外说的数字，负责人不要。
- **200 条人工抽检**：负责人明确不做。本 ADR 如实记录后果：准确率未知。
- **只显示 `pending`、藏掉 `needs_review`**：藏掉的恰是模型最不确定的部分，页面会显得比实际更可靠；铁律 2 反对。
- **给 `approved` 行加「已人工确认」徽章**：PRD §3.9 的徽章体系里没有这一枚，且在「不做人工复核」的决定下它几乎永远不会出现——为一个不会出现的状态造一枚 PRD 外的徽章没有意义。
