# AI 分析功能与数据链路源码审计

> 审计日期：2026-09-17（Asia/Hong_Kong）  
> 审计对象：当前工作树，分支 `main`，基准提交 `4960b83`  
> 目的：完整定位所有需要 AI／LLM／模型结论的页面功能和后端链路，并标出后续修改“后端数据输入处理逻辑”时真正需要改动的位置。

## 0. 阅读结论

当前系统的 AI 不是由浏览器实时调用，而是离线／后台两层流水线：

1. **Layer A：逐条事实标注**，共 3 个任务：
   - `comment_product`：评论 × 产品；
   - `post_annotation`：KOL／官号帖子；
   - `kol_comment_opinion`：合作 KOL 评论 × 产品。
2. **Layer B：产品 × 区间文字生成**，共 8 个 `kind`：
   - `hot_summary`、`summary`、`theme_label`、`neg_category`、
     `stage_unit`、`stage_summary`、`topic_label`、`competitor_reason`。
3. 页面只读数据库结果。`backend/core/` 和 `backend/providers/sql.py` 把 AI 原子结论与确定性计数、分桶、阈值、环比、生命周期和阶段合并拼成接口响应。

五个业务页面全部存在 AI 依赖；另有一个全局“处理进度”抽屉展示 AI 队列状态。最容易漏掉的隐性 AI 入口是 `/pool` 和 `/benchmark`：接口名称是市场指标，但其中的态度、负面舆情和合规字段来自 AI 标注。

### 状态图例

| 标记 | 含义 |
|---|---|
| **ACTIVE-AI** | 当前代码已从输入、模型调用、落库、API 到页面接通；是否有值仍取决于实际数据库是否跑完。 |
| **ACTIVE-DERIVED** | 不直接调模型，但读取 AI 标注／生成物做确定性聚合，因此没有上游 AI 数据就会显示 `null`／“暂不可用”。 |
| **DETERMINISTIC** | 规则、SQL、词表或前端聚合，不需要模型；列出是为了避免后续把它误改成 AI。 |
| **DEMO-ONLY** | `DATA_PROVIDER=demo` 时由 fixture 提供的演示结果，不证明真实 AI 链路已经运行。 |
| **PRESENT-BUT-UNWIRED/BROKEN** | 源码存在，但未接主流程、入口占位，或按当前函数签名无法运行。 |

## 1. 审计边界与工作树快照

运行时默认 provider 是 `sql`，只有显式设置 `DATA_PROVIDER=demo` 才读取演示 fixture；`mysql` 只是 `sql` 的旧别名（`backend/providers/__init__.py:5-11,22,37-40`）。`DemoProvider` 直接读取 `backend/fixtures/demo/`，不调用模型（`backend/providers/demo.py:44-130`）。因此本文判断“已接 AI”只看 `sql` + worker 链路，不把 demo 文案当真实模型产出。

审计时工作树并非干净，以下文件已有修改／未跟踪；本文结论以这些文件在审计时的实际内容为准，而不是只看 `HEAD`：

```text
M  CONTEXT.md
M  backend/providers/sql.py
M  backend/tests/test_layer_b_readpath.py
M  backend/tests/test_sql_provider.py
M  frontend/src/screens/KolDetail.jsx
M  frontend/src/screens/OfficialActivity.jsx
M  frontend/src/screens/productMonitor/Risk.jsx
M  frontend/src/screens/productMonitor/index.jsx
M  worker/ai/prompts/__init__.py
M  worker/ai/schemas.py
M  worker/jobs/annotate.py
M  worker/tests/test_annotate.py
M  worker/tests/test_kol_opinion.py
M  worker/tests/test_pipeline_audit.py
?? .playwright-cli/
?? docs/adr/0023-kol-opinion-source-of-truth.md
?? worker/ai/prompts/kol_opinion_v2.py
```

页面路由只有五个：`/official`、`/kol`、`/kol/detail`、`/product`、`/sector`；全局再挂一个 `ProgressDrawer`（`frontend/src/App.jsx:31-40`）。前端所有业务数据统一经 `frontend/src/data/radar.js`，其下只有 HTTP（`frontend/src/data/radar.js:1-44`）。

## 2. 总链路

```mermaid
flowchart LR
  A[MySQL dump / 规范化 JSONL] --> B[src_feeds / source_snapshots]
  B --> C[ETL: feeds / comments / mentions / users]
  C --> D[extract: 产品×时间窗候选]
  D --> E[5 条规则预过滤]
  E --> F[近重复折叠]
  F --> G[annotation_jobs]
  G --> H[annotate: 3 类 Layer A 任务]
  H --> I[OpenAI-compatible /responses]
  I --> J[严格 schema + item_id 校验]
  J --> K[证据原文定位]
  K --> L[annotations + annotation_evidence]
  L --> M[synthesize: 8 类 Layer B]
  M --> I
  M --> N[synthesis_outputs]
  L --> O[SqlProvider 现行结论读取]
  N --> O
  O --> P[backend/core 确定性聚合]
  P --> Q[/api/v1]
  Q --> R[radar.js 缓存门面]
  R --> S[五个页面 + 证据抽屉]
```

### 2.1 原始数据进入分析库

- 大 dump 导入分两遍：先股票／用户，再按产品池和时间窗过滤帖子（`worker/jobs/import_dump.py:84-227`）。
- ETL 把 `src_feeds` 拆成 `feeds`、`comments`、`mentions`、`users`；正文和评论富文本由 `rich_text()` 展平（`worker/jobs/etl.py:59-87,123-266`）。
- `mentions.source='anchor'` 是帖子挂载产品，`source='body'` 是正文中的产品提及（`worker/jobs/etl.py:208-220`）。这两者对 AI 入参的影响并不相同，见第 8 节。
- 增量入口 `ingest()` 校验 `FeedRecord`／`CommentRecord`，更新 `feeds/comments/mentions/source_snapshots`，随后 bump 数据 revision 并把相关产品的 synth 标脏（`worker/jobs/ingest.py:21-50,59-109`）。
- **PRESENT-BUT-UNWIRED**：在线采集 `worker/jobs/collect.py` 仍直接抛 `NotImplementedError`（`worker/jobs/collect.py:1-23`）；通用调度器只注册 heartbeat（`worker/scheduler.py:1-7,34-43`）。因此“持续采集 → 自动触发 AI”不是已完成链路。

### 2.2 抽取、预过滤与排队

- `extract.run()` 建立 `analysis_scopes`，按产品和帖子发布时间构造当前期／基准期窗口，并为评论、KOL 评论和 KOL／官号帖子排队（`worker/jobs/extract.py:91-215`）。
- 评论候选先过 5 条确定性规则：`empty`、`sticker_only`、`tag_only`、`exact_duplicate`、`offpool_stock_only`（`worker/ai/prefilter.py:35,59-111`）。被剔除者写规则标注，而不是送模型（`worker/ai/prefilter.py:117-199`）。
- 近重复使用同产品、同帖子日内的 64 位 SimHash，汉明距离 ≤3 折叠（`worker/ai/neardup.py:17,38-39,66-136`）；只给代表评论排模型任务。
- `annotation_jobs` 的幂等键包含目标、产品、任务和 `input_hash`；指纹覆盖真实 payload、模型、prompt、taxonomy、schema 版本（`radar_db/schema.py:219-250`；`worker/ai/schemas.py:404-425`）。
- 当前 `job_row_for_comment()` 和帖子排队均显式写 `stage='llm'`（`worker/jobs/annotate.py:215-244,315-333`）。

### 2.3 模型输入、调用、解析和落库

- 评论源查询带：评论正文、父评论、帖子标题、帖子正文、帖子发布时间和挂载产品（`worker/jobs/annotate.py:815-863`）。
- 实际评论 payload 只含匿名 `item_id`、产品 code/name/aliases、评论、可选标题、父评论、帖子正文开头 200 字（`worker/jobs/annotate.py:80,876-900`；`worker/ai/redact.py:55-83`）。
- 帖子 payload 只含匿名 id、标题、正文和产品块（`worker/ai/redact.py:86-100`）。
- URL、用户主页、@ 用户会被清洗；作者 UID、昵称、IP、简介、粉丝数、连接串等字段被递归禁止，发送前再次断言（`worker/ai/redact.py:41-52,103-126`）。
- **仅在 `grouped_batches` 打开时**，Layer A 批处理才受 `micro_batch_size`、输入 token 和 payload bytes 三重限制（`worker/jobs/annotate.py:665-685`；`worker/ai/batching.py:7-45`）；默认配置入口在 `worker/ai/config.py:127-146`。关闭 grouped 的旧路径和 Layer B 没有同等的请求总量上限，见 8.6。
- 唯一真实模型 adapter 是 `openai_compatible`（`worker/ai/providers/__init__.py:18-31`），通过 Responses API `/responses` 发 strict JSON Schema 请求（`worker/ai/providers/openai_compatible.py:69-96,100-126`），处理超时、429/5xx、指数退避和截断输出（`worker/ai/providers/openai_compatible.py:130-223`）。
- 输出按严格 schema 和输入 item-id 集合校验；失败会二分批次并重试，不能把半个结果写库（`worker/jobs/annotate.py:738-797`；`worker/ai/schemas.py:297-389`）。
- 写入前再次查询源数据；推理期间源变化则 job 记为 `superseded`，不写旧答案（`worker/jobs/annotate.py:763-776`）。
- 模型引文必须能在原文中精确或规范化匹配，偏移由程序计算；找不到会把整条标成 `needs_review`，不会伪造证据（`worker/ai/evidence.py:71-95,115-122`；`worker/jobs/annotate.py:961-1037`）。

### 2.4 发布、缓存和回传

- Layer A 写 `annotation_runs`、`annotation_jobs`、`annotations`、`annotation_evidence`；Layer B 写 `synthesis_outputs`（`radar_db/schema.py:192-250,322-397`）。
- 现行 Layer A 结论的唯一规则是“链末且不为 `rejected`；多链末取最新；被 reject 后不回退旧行”（`radar_db/annotations_read.py:36-118`）。`pending` 和 `needs_review` 都直接可见，没有人工批准门槛。
- Layer B 同样读取链末、非 rejected 行；若 `synth_dirty_<code>_<range>=1`，旧生成物整体不读（`backend/providers/sql.py:628-667`）。
- 标注完成后 bump `annotation` revision 并标脏相关产品汇总（`worker/jobs/annotate.py:601-607`）；完整 synth 成功后清 dirty 并 bump `synthesis` revision（`worker/jobs/synthesize.py:714-754`）。
- 前端 HTTP 层处理 API base、Suspense cache 和错误（`frontend/src/lib/api.js:43,60-147`），并轮询 `/version` 触发页面刷新（`frontend/src/lib/api.js:168-201`）。

### 2.5 前端共享请求／状态链

- `App` 把 `dataVersion` 注入每个 screen；`startLiveUpdates()` 发现 `/version` 变化后用 React transition 递增版本，促使当前页重新读数据（`frontend/src/App.jsx:21-27`；`frontend/src/lib/api.js:168-201`）。
- `prefetchScreen()` 预取 `/meta` 和区间；官号页预取 official posts，KOL 页预取 impact，KOL 详情再预取 opinions，板块页预取 pool/ranks/hot summaries（`frontend/src/data/radar.js:54-67`）。产品页没有单独列入该预取函数，进入 screen 后由同步 `read()` 的 cache miss 触发 Suspense。
- `read()` 把请求 Promise／成功值／错误放在模块级 cache；业务 screen 仍保持同步调用形式（`frontend/src/lib/api.js:60-147`）。
- 每个路由都由 `ScreenBoundary` 接住 loading 和错误（`frontend/src/components/ScreenBoundary.jsx:97-147`），loading 骨架在 `frontend/src/components/LoadingSkeleton.jsx:38-58`。因此某个 AI 子接口返回 `null` 应由页面的六态分支处理；只有请求失败或渲染异常才应进入整屏错误态。

## 3. Layer A：3 类逐条 AI 任务

### 3.1 `comment_product` — 评论 × 指定产品

**状态：ACTIVE-AI。** 这是市场域绝大多数 AI 字段的根。

| 项目 | 源码与说明 |
|---|---|
| 判定单元 | `(comment_id, subject_code)`；同一评论可对不同产品得出不同结论（`worker/jobs/annotate.py:247-256`）。 |
| 当前候选范围 | `comments JOIN feeds`，只使用 `feeds.code` 这个挂载产品；时间按 `feeds.posted_at`（`worker/jobs/annotate.py:164-213`）。 |
| 输入 | product code/name/aliases；评论正文；可选帖子标题、父评论、帖子正文前 200 字（`worker/jobs/annotate.py:815-900`）。 |
| Prompt | `comment-product-v2`，明确 ETF/个股边界、产品态度、市场方向、合规信号和中性偏置（`worker/ai/prompts/comment_product_v2.py:1-28,30-118`）。 |
| Schema | `relevance`、`attitude`、`aspects`、`evidence`、`market_direction`、`compliance_tags`、`compliance_rationale`、`compliance_evidence`、`needs_review`（`worker/ai/schemas.py:123-185`，字段主体 `:144-185`）。 |
| 原子落库 kind | `relevance`、`attitude`、可选 `aspect`、可选 `market_direction`、始终写 `compliance`（空 tags 也写，以区分“查过没有”和“没查”）（`worker/jobs/annotate.py:942-958`）。 |
| 直接下游 | `/pool` 态度／负面／合规、`/benchmark` 态度环比、主题、负面类别、话题、竞品、阶段、证据、产品相关 KOL，以及全部 Layer B 输入。 |

近重复代表的结论只传播 `relevance`、`attitude`、`aspect`（`worker/ai/neardup.py:202-260`）。**不会传播 `market_direction` 或 `compliance`**，所以折叠成员在这两个维度上目前没有等价补全；这是调整预处理时必须明确决定的口径。

### 3.2 `post_annotation` — KOL／官号帖子

**状态：ACTIVE-AI。** `extract` 只给客户维护的活跃 KOL 与官号作者排帖子任务（`worker/jobs/extract.py:190-196,307-339`）。

| 项目 | 源码与说明 |
|---|---|
| 判定单元 | `feed_id`，`subject_code=''`；帖子级结论不针对某一只产品。 |
| 输入 | 帖子标题、完整可读正文、挂载产品 code/name/aliases（`worker/jobs/annotate.py:271-338,857-900`）。 |
| Prompt | 帖子内容类型、操作方向、摘要、证据（`worker/ai/prompts/post_annotation_v2.py:10-59`）。 |
| Schema | `post_type`、`direction`、`direction_pending`、`summary`、`evidence_spans`、`needs_review`（`worker/ai/schemas.py:93-120`）。 |
| 原子落库 kind | `post_type`、`summary`、`direction`；模型明确无摘要／无方向时写 `false` 占位，不与“未跑”混淆（`worker/jobs/annotate.py:912-929`）。 |
| 直接下游 | 官号动态、KOL 影响力、KOL 详情；并间接进入前端 `kolProfile()` 的类型画像。 |

SQL provider 用 `post_type` 是否存在判断整块是否标过，再拼 `hasSummary/summary`、方向状态、review badge 和 evidence index（`backend/providers/sql.py:1511-1560,1894-1954`）。官号入口为 `official_posts()`（`backend/providers/sql.py:450-480`），KOL 入口为 `kol_impact()`（`backend/providers/sql.py:547-579`）。

### 3.3 `kol_comment_opinion` — 合作 KOL 评论 × 产品

**状态：ACTIVE-AI（当前工作树含 v2 接线）。** 它不是 KOL 发帖摘要，而是 KOL 在评论区对产品表达的“其他产品观点／操作”。

| 项目 | 源码与说明 |
|---|---|
| 候选 | 与 `comment_product` 同一评论候选形状，但额外按合作 KOL 名单筛作者（`worker/jobs/annotate.py:341-359`）。 |
| 输入 | 与评论任务相同：产品、评论、标题、父评论、帖子开头（`worker/jobs/annotate.py:866-900`）。 |
| Prompt | 输出简短观点、操作、内容形式和原文证据（`worker/ai/prompts/kol_opinion_v2.py:7-61`）。 |
| Schema | v2 在 `summary`、`action`、`evidence`、`needs_review` 基础上增加 `post_type`（`worker/ai/schemas.py:188-227`，v2 类 `:214-227`）。 |
| 原子落库 kind | `kol_summary`、`kol_action`、v2 的 `post_type`（`worker/jobs/annotate.py:931-940`）。 |
| 直接下游 | 产品页“产品相关 KOL”读取 `kol_summary` 并结合 `comment_product.attitude`；KOL 详情“其他产品观点”读取 `kol_summary`、`kol_action`、评论级 `post_type`（`backend/providers/sql.py:954-1082`）。 |

注意：同名 `post_type` 同时可用于 `target_type='feed'` 和 `target_type='comment'`，读取时必须带 target type；`current_annotations()` 正是为此强制要求 `target_type`（`radar_db/annotations_read.py:36-43`）。

## 4. Layer B：8 类产品 × 区间生成物

**状态：全部具有 ACTIVE-AI 写入路径。** 但只有对应 Layer A 已完整、区间达到阈值且 pipeline 实际运行后，页面才有结果。

Layer B 的原则是“模型只写字，不写数”：计数和桶先由程序生成 `facts`，模型只能引用输入里的 evidence id，且禁止写比例（`worker/ai/synth.py:1-28,40-45,217-247,252-260`）。

| kind | AI 输出 | 输入事实 | 页面／接口 | 关键代码 |
|---|---|---|---|---|
| `hot_summary` | ≤30 字“现象＋主流观点”+ evidence IDs | 正负中、主题桶、负面类别、合规、基准 | 板块榜单 `/hot-summaries` | schema `worker/ai/synth.py:61-73`；prompt `:262-270`；provider `backend/providers/sql.py:757-785` |
| `summary` | 1–4 条、每条 ≤60 字要点 | 同上 | 产品页／板块抽屉 `/summary` | schema `worker/ai/synth.py:76-97`；prompt `:271-279`；provider `backend/providers/sql.py:787-831` |
| `theme_label` | 每个“极性×aspect”桶的标题和摘要 | Layer A attitude + aspect 桶、抽样证据 | `/themes` | schema `worker/ai/synth.py:100-120`；payload `worker/jobs/synthesize.py:292-329`；provider `backend/providers/sql.py:833-845` |
| `neg_category` | 每个负面产品问题桶的名称和摘要 | negative + 可行动 aspect | `/negative-categories` | prompt `worker/ai/synth.py:292-300`；payload `worker/jobs/synthesize.py:292-329`；provider `backend/providers/sql.py:851-866` |
| `stage_unit` | 每个足量时段 7 类之一 + digest | 每时段正负中计数、tone、证据 | `/stages` | schema `worker/ai/synth.py:122-140`；payload `worker/jobs/synthesize.py:384-422`；provider `backend/providers/sql.py:887-908` |
| `stage_summary` | 合并阶段的一句总结 | 相邻同类时段及其 digest／证据 | `/stages` | schema `worker/ai/synth.py:142-158`；生成 `worker/jobs/synthesize.py:532-568` |
| `topic_label` | 市场方向话题标题和摘要 | bullish/bearish/neutral 计数和证据 | `/topics` | schema `worker/ai/synth.py:160-173`；payload `worker/jobs/synthesize.py:371-381`；provider `backend/providers/sql.py:868-885` |
| `competitor_reason` | 每只竞品喜欢／质疑原因各最多 3 条 | 固定映射 + 产品别名共现候选 + 证据 | `/competitors` | schema `worker/ai/synth.py:175-208`；payload `worker/jobs/synthesize.py:425-456`；provider `backend/providers/sql.py:910-950` |

共用原料 `Material` 读取当前区间的 `relevance/attitude/aspect/market_direction/compliance` 以及基准期计数（`worker/jobs/synthesize.py:163-225`）。证据优先使用程序已定位的 `annotation_evidence`，否则用脱敏评论正文，并截至 140 字（`worker/jobs/synthesize.py:227-263`）；抽样按天轮询并随机化，保留长短和少数意见，不按点赞排序（`worker/jobs/synthesize.py:271-286`）。

生成入口按产品 × 区间循环：先生成主题／负面标签，再生成 summary，随后 topic、stage、competitor（`worker/jobs/synthesize.py:462-568,730-755`）。输入指纹覆盖 annotation IDs、facts、prompt/model/provider/taxonomy/schema；相同输入复用，底层标注变化会生成新行并 supersede 旧行（`worker/jobs/synthesize.py:150-157,592-699`）。

## 5. 逐页完整链路

### 5.1 `/sector` — 板块总览

入口：`frontend/src/App.jsx:37`，页面 `frontend/src/screens/sectorOverview/index.jsx`。

#### A. 全市场表格、热力图与顶部 KPI

| 页面显示 | 分类 | 前端 → API | 后端读取与 AI 来源 |
|---|---|---|---|
| 正面／负面数量、样本是否足够、舆情净值热力色 | ACTIVE-DERIVED | `R.pool()`，门面 `frontend/src/data/radar.js:95-101` → `GET /pool`（`backend/api/v1/market.py:21-28`） | `backend/core/market.py:40-49` → `SqlProvider.pool()`；`_scan()` 聚合 Layer A `attitude/relevance`，输出 observation.attitude 和桶三态（`backend/providers/sql.py:213-243,283-330,1268-1336`）。页面处理空态在 `sectorOverview/index.jsx:332-370`，热力图在 `:387-470`。 |
| “舆情”条数／高关注类别数 | ACTIVE-DERIVED | 同 `/pool` | `pool()` 对 `neg_cats_for()` 做 `negative_rollup()`（`backend/providers/sql.py:303-313`）；类别成员来自 Layer A aspect + attitude，名字来自 Layer B `neg_category`，但计数本身是确定性的（`backend/core/themes.py:130-174,185-194`）。 |
| “需合规关注”产品数和条数 | ACTIVE-DERIVED | 同 `/pool` | `pool.complianceCount` 读取 Layer A `compliance`（`backend/providers/sql.py:307-325,1168-1249`）。页面 KPI `sectorOverview/index.jsx:472-492`。 |
| 每只 ETF 的“热议总结” | ACTIVE-AI | `R.hotSummaryFor()` `frontend/src/data/radar.js:128-140` → `GET /hot-summaries?range=` `backend/api/v1/narrative.py:39-44` | `core.narrative.hot_summaries()` `backend/core/narrative.py:43-52` → Layer B `hot_summary` `backend/providers/sql.py:757-785`。页面读取／badge `sectorOverview/index.jsx:340-378`，表组件 `frontend/src/screens/sectorOverview/ProductTable.jsx:20-73`。 |
| AI 结论验证程度说明 | ACTIVE-DERIVED | `GET /meta` → `R.AI_VALIDATION` | 元数据，不调用模型；页面 `sectorOverview/index.jsx:591-604`。当前前端还引用未暴露的 `R.AI_VALIDATION_DETAIL`，见第 9 节。 |

`/pool` 中评论数、账号数、互动、热度、全市场排名、筛选和热力面积均是 **DETERMINISTIC**；只有 attitude、负面归类和 compliance 子字段依赖 AI。`/ranks` 纯评论量排序，不需要 AI（`backend/providers/sql.py:332-343`）。

#### B. 产品抽屉（打开行后懒取）

页面在 `sectorOverview/index.jsx:612-800` 调用：

| 页面模块 | 门面／HTTP | Provider 输入 | Layer A／B |
|---|---|---|---|
| 当前舆情总结 | `summaryFor` `frontend/src/data/radar.js:143-146` → `/products/{code}/summary` `backend/api/v1/narrative.py:45-48` | `SqlProvider.summary_for()` `backend/providers/sql.py:787-831` | A: relevance/attitude/aspect/compliance；B: `summary` |
| 正／负观点主题 | `themesFor` `frontend/src/data/radar.js:148-160` → `/themes` `backend/api/v1/narrative.py:51-54` | `SqlProvider.themes_for()` `backend/providers/sql.py:833-845` | A: attitude/aspect；B: `theme_label` |
| 负面舆情类别 | `negCatsFor` `frontend/src/data/radar.js:163-166` → `/negative-categories` `backend/api/v1/narrative.py:57-60` | `SqlProvider.neg_cats_for()` `backend/providers/sql.py:851-866` | A: negative/aspect；B: `neg_category`；生命周期／关注程度规则计算 |
| 关联竞品 | `competitorsFor` `frontend/src/data/radar.js:168-173` → `/competitors` `backend/api/v1/narrative.py:63-66` | `SqlProvider.competitors_for()` `backend/providers/sql.py:910-950` | 候选由固定映射＋词表共现确定性选出；B: `competitor_reason` 写喜欢／质疑原因 |
| 需合规关注 | `complianceFor` `frontend/src/data/radar.js:175-179` → `/compliance` `backend/api/v1/narrative.py:69-72` | `SqlProvider.compliance_for()` `backend/providers/sql.py:1146-1249` | A: `compliance`；证据来自 `annotation_evidence` |

### 5.2 `/product` — 产品监控

入口：`frontend/src/App.jsx:36`，编排页 `frontend/src/screens/productMonitor/index.jsx`。

| 页面显示模块 | 前端调用／组件 | API → provider | AI 来源与确定性部分 |
|---|---|---|---|
| 当前舆情总结、要点、AI badge | `index.jsx:587-603,687-702`；`Overview.jsx:20-53` | `summaryFor` → `/summary` → `SqlProvider.summary_for()` | A: relevance/attitude/aspect/compliance；B: `summary`。总提及和样本句由后端拼，模型只写要点（`sql.py:787-831`）。 |
| 态度占比与趋势 | `index.jsx:344-377,687-749`；`Attitude.jsx:51-165`、`Trend.jsx:1-58` | `pool/observe` + `/benchmark` | A: relevance/attitude；分桶、占比、delta 为确定性（`sql.py:213-243,345-370,1268-1336`）。 |
| 积极／消极观点主题 | `index.jsx:430-460`；`Attitude.jsx:51-165` | `themesFor` → `/themes` | A: attitude/aspect；B: `theme_label`；mentions/share/delta/lifecycle/severity 是 core 规则（`backend/core/themes.py:73-127`）。 |
| 产品话题情绪 | `index.jsx:461-485`；`Topics.jsx:3-58` | `topicsFor` `radar.js:181-186` → `/topics` `backend/api/v1/evidence.py:39-47` → `SqlProvider.topics_for()` | A: `market_direction`；B: `topic_label`；多空计数和 split 为确定性（`backend/core/topics.py:24-68`）。 |
| 关联竞品 | `index.jsx:486-516`；`Competitors.jsx:4-61` | `competitorsFor` → `/competitors` | 固定对位／词表共现候选 + B `competitor_reason`；mentions/delta 是 SQL/core 计数。 |
| 产品相关 KOL | `index.jsx:517-542`；`KolList.jsx:3-54` | `kolMentionsFor` `radar.js:188-194` → `/kol-mentions` `evidence.py:48-56` → `SqlProvider.kol_mentions_for()` `sql.py:954-1025` | KOL `kol_summary` 来自 `kol_comment_opinion`；dominantAttitude 来自 `comment_product.attitude`，少于 3 条不输出；排序和聚合确定性。 |
| 重点舆情／合规 | `index.jsx:543-586`；`Risk.jsx:3-65` | `complianceFor` → `/compliance` | A: compliance tags/rationale/evidence；UI 固定显示“AI 识别 · 待人工确认”。 |
| 热度变化与阶段观点 | `index.jsx:838-924`；`Stages.jsx:5-138` | `stagesFor` `radar.js:218-228` → `/stages` `backend/api/v1/prices.py:43-51` → `SqlProvider.stages_for()` `sql.py:887-908` | A: attitude；B: `stage_unit` 分类/digest + `stage_summary`；时段切分、样本阈值、相邻合并、情绪由 `backend/core/stages.py:117-172,180-255` 确定。热度 series 是非 AI。 |
| 原文证据抽屉 | `index.jsx:926-982`；`EvidenceDrawer.jsx:1-106` | `evidenceFor` `radar.js:196-207` → `/evidence` `evidence.py:57-67` → `SqlProvider.evidence_for()` `sql.py:1086-1144` | 当前只按 Layer A `attitude` 的产品×区间×极性选评论；不直接按 Layer B evidence IDs 取。见第 9 节的降级。 |

### 5.3 `/kol` — KOL 影响力

入口：`frontend/src/App.jsx:34`；数据调用 `frontend/src/screens/KolActivity.jsx:35-37` → `R.kolImpact()` `frontend/src/data/radar.js:79-84` → `GET /kol/impact` `backend/api/v1/kol.py:20-28` → `backend/core/kol.py:28-36` → `SqlProvider.kol_impact()` `backend/providers/sql.py:547-579`。

所有以下字段都由 `post_annotation` 提供：

- 帖子内容类型 `postType`，用于类型多选、类型计数、KPI、表格 badge、CSV 和侧栏（`KolActivity.jsx:210-262,289-322,339-374,458-510,638,679,711,727`）。
- 操作方向 `direction`／`directionPending`，用于筛选和“操作方向 · AI 判定”展示（`KolActivity.jsx:210-262,352-374,749`）。
- `summary/hasSummary`，用于搜索、列表“AI 摘要”和帖子抽屉；明确区分“图片帖无摘要”与“AI 尚未运行”（`KolActivity.jsx:271-322,352-374,488-491,761-768,926-927`）。
- `reviewState` + `evidenceIdx`，用于“待确认／可追溯”徽章（`KolActivity.jsx:41-56`）。

KOL 声量排名和画像 `kolProfile()` 是 **ACTIVE-DERIVED**：它不调用模型，只对当前已下发帖子子集做计数；但 `typeCounts/topType/styleTag` 依赖 AI `postType`，只要一篇未标注就整块未知（`frontend/src/lib/profile.js:1-35,42-69`；调用 `KolActivity.jsx:376-455`）。发帖数、自家／竞品阵营、互动和排序本身是确定性的。

### 5.4 `/kol/detail` — KOL 详情

入口：`frontend/src/App.jsx:35`。页面同时使用两条 AI 链：

1. `R.kolImpact()`：取得该 KOL 的发帖 `postType/direction/summary/reviewState/evidence`；页面在 `frontend/src/screens/KolDetail.jsx:23-50,149-183,357-420` 显示。
2. `R.kolOpinions(kol, range)`：`frontend/src/screens/KolDetail.jsx:257` → `frontend/src/data/radar.js:86-93` → `GET /kol/{name}/opinions` `backend/api/v1/kol.py:29-37` → `backend/core/kol.py:39-51` → `SqlProvider.kol_opinions()` `backend/providers/sql.py:1027-1082`。输出其他产品观点摘要、操作、评论级内容形式、原文摘录和 review state，页面在 `KolDetail.jsx:231-287,502-567` 显示。

“AI 画像” `styleTag` 和 8 类内容构成是对 `post_annotation.post_type` 的前端确定性聚合，不是第四个模型任务（`KolDetail.jsx:94-115,231-247,323-332,469-495`；`frontend/src/lib/profile.js:42-69`）。

### 5.5 `/official` — 官号动态

入口：`frontend/src/App.jsx:33`；`frontend/src/screens/OfficialActivity.jsx:101` → `R.officialPosts()` `frontend/src/data/radar.js:230-233` → `GET /officials/posts` `backend/api/v1/officials.py:18-26` → `SqlProvider.official_posts()` `backend/providers/sql.py:450-480`。

AI 功能全部来自 `post_annotation`：

- 帖子类型、操作方向、AI 摘要、是否待确认、证据高亮；
- 类型菜单和类型计数；
- “已生成摘要” KPI、搜索摘要、卡片展开详情。

页面代码集中在 `OfficialActivity.jsx:103-111,211-233,304-340,366-409,635,679,691-720`。

`R.etfMentionsFor()`（`OfficialActivity.jsx:133`；`radar.js:235-242` → `GET /officials/{account}/etf-mentions` `backend/api/v1/officials.py:27-33`）是 **DETERMINISTIC**：按 `mentions`／产品池规则数 ETF，并非 AI（`backend/providers/sql.py:482-543`）。官号阵营也由产品池 own/peer 映射决定，不应改成模型分类。

### 5.6 全局处理进度抽屉

**状态：ACTIVE-DERIVED，非模型分析结果。** 所有页面 Shell 都显示入口（`frontend/src/components/Shell.jsx:11,47,67`），按钮和抽屉在 `frontend/src/components/ProgressDrawer.jsx:116-145`。

- 全量快照：轮询 `GET /progress`（`ProgressDrawer.jsx:196-207`；`backend/api/v1/progress.py:15-19`）。
- 增量日志：每 3 秒轮询 `GET /progress/events`，全量每 15 秒（`ProgressDrawer.jsx:208-230`；`backend/api/v1/progress.py:20-22`）。
- 显示 L1 学生、L2 LLM、帖子任务、KOL 评论任务、待更新汇总、吞吐和 ETA（`ProgressDrawer.jsx:321-338`），并可按阶段／产品过滤日志（`:341-400`）。
- 统计来自 `annotation_jobs`、`worker_events`、`synthesis_outputs` 和 `meta_kv`，没有新模型调用（`backend/core/progress.py:48-49,93-178,196-216`）。其中“L1 学生”目前与真实排队状态不一致，见第 7 节。

## 6. API／后端功能总表

| HTTP | Controller | Core | SQL provider | AI 依赖分类 |
|---|---|---|---|---|
| `GET /api/v1/pool` | `backend/api/v1/market.py:20-26` | `backend/core/market.py:40-49` | `backend/providers/sql.py:283-330` | 混合：基础量 deterministic；attitude、负面、合规 ACTIVE-DERIVED |
| `GET /api/v1/ranks` | `backend/api/v1/market.py:29-35` | `backend/core/market.py:52-62` | `backend/providers/sql.py:332-343` | DETERMINISTIC |
| `GET /api/v1/products/{code}/benchmark` | `backend/api/v1/market.py:38-46` | `backend/core/market.py:65-73` | `backend/providers/sql.py:345-370` | 混合：态度 delta 依赖 AI，其余指标 deterministic |
| `GET /api/v1/hot-summaries` | `backend/api/v1/narrative.py:39-42` | `backend/core/narrative.py:43-52` | `backend/providers/sql.py:757-785` | ACTIVE-AI：`hot_summary` |
| `GET /api/v1/products/{code}/summary` | `backend/api/v1/narrative.py:45-48` | `backend/core/narrative.py:55-63` | `backend/providers/sql.py:787-831` | ACTIVE-AI：`summary` + deterministic 计数句 |
| `.../themes` | `backend/api/v1/narrative.py:51-54` | `backend/core/narrative.py:66-73` | `backend/providers/sql.py:833-845` | A attitude/aspect + B theme_label |
| `.../negative-categories` | `backend/api/v1/narrative.py:57-60` | `backend/core/narrative.py:76-84` | `backend/providers/sql.py:851-866` | A negative/aspect + B neg_category + rules |
| `.../competitors` | `backend/api/v1/narrative.py:63-66` | `backend/core/narrative.py:87-97` | `backend/providers/sql.py:910-950` | deterministic candidate + B competitor_reason |
| `.../compliance` | `backend/api/v1/narrative.py:69-72` | `backend/core/narrative.py:100-109` | `backend/providers/sql.py:1146-1249` | Layer A compliance |
| `.../topics` | `backend/api/v1/evidence.py:39-46` | `backend/core/evidence.py:40-49` | `backend/providers/sql.py:868-885` | A market_direction + B topic_label |
| `.../kol-mentions` | `backend/api/v1/evidence.py:48-55` | `backend/core/evidence.py:52-63` | `backend/providers/sql.py:954-1025` | KOL opinion summary + comment attitude |
| `.../evidence` | `backend/api/v1/evidence.py:57-67` | `backend/core/evidence.py:66-78` | `backend/providers/sql.py:1086-1144` | Layer A attitude；当前粗粒度降级 |
| `.../stages` | `backend/api/v1/prices.py:43-50` | `backend/core/prices.py:65-74` | `backend/providers/sql.py:887-908` | A attitude + B stage_unit/stage_summary + deterministic merge |
| `.../candles` | `backend/api/v1/prices.py:25-32` | `backend/core/prices.py:41-49` | 行情读路径 | DETERMINISTIC／外部行情，非 AI |
| `.../heat-series` | `backend/api/v1/prices.py:34-41` | `backend/core/prices.py:52-62` | 热度公式 | DETERMINISTIC；阶段图上游但不需要 AI |
| `.../daily` | `backend/api/v1/prices.py:52-57` | `backend/core/prices.py:77-83` | 日度统计 | DETERMINISTIC；当前页面不直接读 |
| `GET /api/v1/kol/impact` | `backend/api/v1/kol.py:20-27` | `backend/core/kol.py:28-36` | `backend/providers/sql.py:547-579,1511-1560` | Layer A post_annotation |
| `GET /api/v1/kol/{name}/opinions` | `backend/api/v1/kol.py:29-37` | `backend/core/kol.py:39-51` | `backend/providers/sql.py:1027-1082` | Layer A kol_comment_opinion |
| `GET /api/v1/officials/posts` | `backend/api/v1/officials.py:18-25` | `backend/core/officials.py:21-25` | `backend/providers/sql.py:450-480,1511-1560` | Layer A post_annotation |
| `GET /api/v1/officials/{account}/etf-mentions` | `backend/api/v1/officials.py:27-33` | `backend/core/officials.py:28-32` | `backend/providers/sql.py:482-543` | DETERMINISTIC |
| `GET /api/v1/progress[/{events}]` | `backend/api/v1/progress.py:15-22` | `backend/core/progress.py:195-229` | DB 状态 | DETERMINISTIC 运行状态 |
| `GET /api/v1/meta`、`/version` | `backend/api/v1/meta.py:13-20` | `backend/core/meta.py:52-93` | meta/revision | DETERMINISTIC；控制 AI 说明和前端缓存刷新 |

所有 v1 blueprint 统一注册在 `backend/app.py:36-47`。

## 7. 后台编排、异步链路与运维入口

### 7.1 当前可用的主流程

- `worker/jobs/pipeline.py` 固定顺序为 `comment_product` → `kol_comment_opinion` → `post_annotation` → Layer B → audit（`worker/jobs/pipeline.py:1-24,52,87-143`）。
- Layer B 只对当前 scope 中所有 Layer A job 已完成、不覆盖未完成日期的产品×区间执行（`worker/jobs/pipeline.py:55-84,114-137`）。
- `worker/jobs/full_own.py` 是常驻／轮询编排入口：准备各产品 scope、按优先级循环调用 pipeline、记录 `own_analysis_progress`（`worker/jobs/full_own.py:35-73,116-188`）；`--watch` 每 5 秒 tick，行情可选每小时同步（`worker/jobs/full_own.py:263-333`）。
- `worker/jobs/analyze.py` 是计划、预算预览、run/status/resume 的人工入口；实际执行转给 `full_own.run_manual()`（`worker/jobs/analyze.py:175-262`）。
- `audit` 输出完成率、dead letter、needs_review、证据定位率、合规词表漏检和人工抽样表，不调用模型（`worker/jobs/audit.py:1-15,62-169,171-232`）。
- `review` 可 approve/reject/correct；只有 reject 会让现行结论下线，correct 插入新 annotation 并保留审计轨迹（`worker/jobs/review.py:1-42,111-272`）。

### 7.2 PRESENT-BUT-UNWIRED/BROKEN：本地 student 模型路径

仓库里有完整的 student 数据集、训练、ONNX 导出和推理代码：

- 从 Luna 现行标注构造训练集，并排除 rule/local_model/propagated（`worker/models/dataset.py:1-26,93-164`）；
- 训练／温度校准（`worker/models/train.py:57-309`）；
- ONNX／Torch 推理（`worker/models/infer.py:30-96`）；
- `worker/jobs/classify.py` 设计为处理 `stage='student'` 后按阈值路由至 LLM（`worker/jobs/classify.py:1-31,193-209,323-376`）。

但当前主链路不能按这套设计工作：

1. `annotate.py` 排评论和帖子时都显式写 `stage='llm'`（`worker/jobs/annotate.py:241,333`），不会产生 student 待办；
2. `classify.py` 引用 `annotate.STAGE_STUDENT`／`annotate.STAGE_LLM`，但 `annotate.py` 当前没有这两个常量（`worker/jobs/classify.py:196,209,333,359`）；
3. `classify.py` 给 `annotate.pending_count(..., stage=...)` 和 `annotate.claim(..., stage=...)` 传参，但现签名没有 `stage`（`worker/jobs/annotate.py:420,1122`）。

因此进度抽屉的“L1 学生队列”只是按数据库 `stage` 分组展示，并不代表 student 主流程已接通。迁移表的 server default 仍是 `student`（`radar_db/schema.py:243`），与应用显式写 `llm` 也存在语义冲突。

### 7.3 校准与网关工具

- `worker/scripts/probe_gateway.py:98-209` 探测基础请求、service tier、批量、prompt cache、突发和并发；它会调用模型，但不服务任何页面。
- `worker/scripts/calibrate.py:1-25,117-156,164-296` 对不同 batch 策略做一致性实验，只写报告文件，不写 `annotations`。
- `worker/scripts/gold_sample.py:1-32,143-301` 导出盲标金标工作簿；`worker/scripts/evaluate_gold.py:348-428` 计算准确率／F1，并可把 `ai_validation` 元数据写回库。它们是治理链路，不是业务生成链路。

## 8. 后续调整“后端数据输入处理逻辑”的关键改动点

下面按影响面从源头到模型列出。修改任何一处都应同时考虑 input hash、旧 annotation 的 supersede、Layer B dirty 和页面缓存 revision。

### 8.1 产品归属：目前只分析帖子挂载产品

最关键的现状是：

- ETL 虽然同时保存 anchor 与 body mentions（`worker/jobs/etl.py:208-220`）；
- 但 `_comment_candidates()` 直接取 `feeds.code`（`worker/jobs/annotate.py:164-194`）；
- `enqueue_comments()` 注释也明确说只排挂载标的，正文提及先不排，避免一帖多产品导致成本成倍增长（`worker/jobs/annotate.py:247-256`）。

如果后续要让一条评论同时分析正文提及的多只 ETF，主要改点是候选 SQL／展开逻辑，而不是 prompt。应同步修改：

1. `worker/jobs/annotate.py:_comment_candidates()`；
2. `worker/jobs/extract.py:_extract_comments()` 的候选／统计；
3. `(comment_id, subject_code)` 排队和成本估算；
4. 基准期、近重复键与 evidence 的产品归属；
5. `kol_comment_opinion`（它复用同一候选）；
6. input hash 会因 product block 变化自然产生新 job，但需要确认旧结果的 supersede 范围。

### 8.2 时间口径：评论按所在帖子发布时间归桶

- `analysis_scopes.time_basis` 明定 `feed_posted_at`（`radar_db/schema.py:294-309`）。
- `current_annotations(window=...)` 对评论也通过 `feeds.posted_at` 过滤（`radar_db/annotations_read.py:36-43,52-85`）。
- `_comment_candidates()` 同样按 `feeds.posted_at`（`worker/jobs/annotate.py:191-194`）。

若改成评论自身时间，必须成套修改抽取、annotation 读取、Layer B Material、SQL provider 分桶和基准窗口；只改一处会导致“排进来的集合”和“页面读到的集合”不一致。

### 8.3 上下文组成、截断与外发边界

- 评论正文不在 `_build_payload()` 内截断；帖子上下文只取开头 200 字（`worker/jobs/annotate.py:80,886-896`）。
- 父评论只取一层 `reply_to_comment_id`（`worker/jobs/annotate.py:166-184,829-855`）。
- 产品别名最多 12 个，来自固定 master／词表（`worker/ai/lexicon/product_aliases.py:314-375`）。
- Layer B 证据文本截 140 字（`worker/jobs/synthesize.py:257-263`）。

当前脱敏是“**字段白名单 + 正文轻清洗**”：结构上禁止 UID、昵称、IP、简介、粉丝数等键，但 `scrub_text()` 只替换 URL 和 `@mention`（`worker/ai/redact.py:24-52,103-126`）。因此用户自由文本里的电话、邮箱、真实姓名、地址等 PII，以及伪装成系统指令的 prompt-injection 文本，当前没有专门识别／移除。strict schema 能约束输出形状，不能替代输入内容防护；新增输入源时应先明确是否允许把原文送到外部模型，并补脱敏、注入测试和审计记录。

除 `post_context[:200]` 外，comment、parent、title 和帖子正文都不做长度截断（`worker/jobs/annotate.py:78-80,886-900`）。同一帖子下每条评论都会重复带这 200 字上下文；当前 payload 和批打包实现没有“按帖子引用一次、item 复用”的共享上下文结构（`worker/jobs/annotate.py:648-672`）。这既会放大 token，也意味着改上下文时同帖大量 job 的 input hash 一起变化。

要加入完整帖子、更多父链、评论时间、作者角色或其他元数据时，必须先更新 `redact.py` 白名单，再更新 prompt/schema 和 input hash 版本；不能绕过白名单自己拼 payload。

### 8.4 规则预过滤和近重复

- 五条规则及其顺序在 `worker/ai/prefilter.py:35,75-111`；改规则会直接改变付费样本集合。
- off-pool 股票识别由 `worker/ai/lexicon/offpool_stocks.py:112-158`，数据库词表补充在 `:164-193`；产品匹配由 `worker/ai/lexicon/product_aliases.py:314-375`。
- 近重复阈值、分桶和规范化在 `worker/ai/neardup.py:17,38-136`。
- 当前只传播 relevance/attitude/aspect（`worker/ai/neardup.py:202-260`）。若希望 market_direction/compliance 完整，应决定是传播、为成员单独调用，还是在页面明确覆盖率。

### 8.5 Prompt、Schema、taxonomy 与原子 kind

- 任务 → prompt 的注册和默认最新版在 `worker/ai/prompts/__init__.py:22-47`。
- prompt 与 schema 必须配对，否则 `resolve()` 直接报错（`worker/jobs/annotate.py:110-134`）。
- schema 是 strict JSON Schema 的唯一来源（`worker/ai/schemas.py:230-245,350-389`）。
- 修改 prompt 正文必须改模块 `VERSION`；否则 input hash 仍可能复用旧 job（`worker/ai/prompts/comment_product_v2.py:20-28`；`worker/ai/schemas.py:404-425`）。
- 新增输出字段还必须在 `_kinds_for()` 拆成 annotation kind、在 schema 表注释／迁移、SQL reader、Layer B Material 和页面契约逐层接通（`worker/jobs/annotate.py:906-958`）。

### 8.6 批量、预算、并发与失败策略

- 环境入口：模型、base URL/key、timeout、重试、micro batch、token、并发、版本、structured output、reasoning effort、store 和 service tier（`worker/ai/config.py:127-146`）。
- 分组批量受 payload bytes 和输入 token 控制（`worker/ai/config.py:80-82`；`worker/ai/batching.py:7-45`）；单个 item 超限会直接失败，并不会截断（`worker/jobs/annotate.py:665-676`）。关闭 `grouped_batches` 的 Layer A 旧路径没有这一层总 cap。
- schema 错误和截断会二分批次；永久配置错误放回 pending，瞬时失败 retry/dead（`worker/jobs/annotate.py:697-797,1065-1119`）。
- `pipeline --budget-requests` 是调用预算闸门（`worker/jobs/pipeline.py:20-23,87-108`）。
- Layer B 每种 kind 直接把整份 facts + evidence 发给 provider，没有复用 `BatchPolicy` 的 token／byte cap（`worker/jobs/synthesize.py:572-590`）。其 `SchemaError`／`TransientError` 仅记日志并跳过本项，没有 `annotation_jobs` 式持久 retry/dead-letter 队列（`:592-631`）；进程退出后只能靠再次运行 synth 补做。
- provider 自己会重试网络错误，job 层又会 retry；两层共用的 `AI_MAX_RETRIES` 可能让一次逻辑任务产生多轮实际 HTTP 请求（`worker/ai/providers/openai_compatible.py:130-195`；`worker/jobs/annotate.py:1100-1118`）。预算应按最坏实际调用数核算。

### 8.7 Layer B 输入事实和采样

- `Material` 只把 `relevance='relevant'` 且有 attitude 的评论放入产品观点；市场方向另成集合，合规只收非空 tags（`worker/jobs/synthesize.py:163-207`）。
- 主题／负面桶有最小桶阈值，见 `build_theme_payload()`（`worker/jobs/synthesize.py:292-329`）。
- summary 证据正负各最多抽 8 条，合规最多 3 条（`worker/jobs/synthesize.py:341-368`）。
- topic 最多抽 10 条（`:371-381`）；stage 每时段按阈值和证据上限（`:384-422`）；竞品由固定映射加共现 top-K 且需最小证据数（`:425-456`）。
- 如果后续希望模型见到点赞、作者类型、更多上下文或全量证据，主要修改这些 payload builder，而不是 API 层。
- 当前 Layer B fingerprint 只覆盖 `kind + annotation_id 集合 + facts + prompt/model/provider/taxonomy/schema`（`worker/jobs/synthesize.py:150-157`），没有直接覆盖 evidence 文本、产品名称、区间 label 或自动识别的 language；而真实 payload 含这些字段（`:585-590`）。若这些值在 annotation IDs／facts 不变时变化，现行缓存可能错误复用旧生成物。改输入逻辑时应把所有会影响模型答案的字段纳入指纹。

### 8.8 版本、脏标记与重跑

- Layer A `input_hash` 覆盖 payload + model + prompt/taxonomy/schema（`worker/ai/schemas.py:404-425`）。
- Layer B fingerprint 覆盖 annotation IDs + facts + prompt/model/provider/taxonomy/schema（`worker/jobs/synthesize.py:150-157`）。
- 新源数据 `ingest` 标脏（`worker/jobs/ingest.py:107-109`），Layer A 变更再次标脏（`worker/jobs/annotate.py:601-607`），只有完整 Layer B 成功才清理（`worker/jobs/synthesize.py:750-754`）。
- 后端 `_synth()` 在 dirty 时拒绝旧结果（`backend/providers/sql.py:628-667`），所以输入逻辑改动后应保证相关产品／区间都正确标脏，否则页面可能继续读旧文案。
- `analysis_scopes` 只存一个 `prompt_version/schema_version`（`radar_db/schema.py:294-310`），而 `extract.run()` 即使同时排三类任务也固定记录 `comment_product` 的版本（`worker/jobs/extract.py:156-180`）。审计某个 scope 时不能据此还原 `post_annotation`／`kol_comment_opinion` 的真实 prompt；应改成按 task 记录，或以 `annotation_jobs/annotation_runs` 为权威。
- `extract` 实际会顺手排 `kol_comment_opinion`（`worker/jobs/extract.py:184-196`），但估算字典只统计请求参数里的 `comment_product`／`post_annotation`（`:202-206`），所以预算预览会漏算 KOL 评论调用。
- Compose 中 worker 只在 `manual` profile 下启用（`docker-compose.yml:61-74`），scheduler 又只注册 heartbeat（`worker/scheduler.py:1-4,34-43`）。输入改造后的 backfill、重跑和持续刷新目前必须通过 `analyze/full_own/pipeline` 显式编排，不能假设部署后会自动跑。

## 9. 已确认的缺口、降级和易误判点

### 9.1 证据抽屉没有按具体结论取证

`evidenceFor(code, ctxKey, polarity, n)` 的 core 契约认为 `ctxKey` 的面板 id 用来定位具体结论（`backend/core/evidence.py:7-25,66-78`），但 SQL provider 当前只使用 `ctxKey` 的区间部分，按产品 + polarity 返回通用评论，并明确说明面板 id 不参与选取（`backend/providers/sql.py:1086-1119`）。

结果是同产品、同极性的主题／阶段／竞品入口可能打开同一批评论。Layer B 已保存 `evidenceIds`，但此接口尚未用它们。后续修正应优先建立 `ctxKey → synthesis_outputs.subkey/evidenceIds → comment_id` 映射。

### 9.2 产品页“总结证据数”和 AI 状态错接

- 产品页把 `Math.round(o.mentions * 0.4)` 当 summary evidence count，并把这个估算值传给证据抽屉（`frontend/src/screens/productMonitor/index.jsx:697-702`）。后端 `summary_for()` 已返回真实、去重后的 `evidenceIds` 以及 `aiStatus`（`backend/providers/sql.py:818-831`），当前实现虽没有独立 `evidenceCount`，仍可用 `evidenceIds.length` 得到真实数量。这是展示层的伪估算。
- 页面 `aiLabel` 只判断 `sum == null` 和 `sum.low`，没有判断 `sum.aiStatus`（`frontend/src/screens/productMonitor/index.jsx:587-603,687-696`）。因此“确定性计数句已生成、Layer B 要点尚未生成”的 `aiStatus='unavailable'` 仍可能显示“AI 生成 · 可追溯原文”；板块抽屉已经有正确分支可参考（`frontend/src/screens/sectorOverview/index.jsx:727-730`）。
- Product 的主题／话题／竞品组件预留 `themesStale/topicsStale/compsStale` 徽章（`frontend/src/screens/productMonitor/Attitude.jsx:59-60`；`Topics.jsx:11`；`Competitors.jsx:12`），但产品页没有组装这些 props。更上游的 `_synth()` 在 dirty 时直接返回空映射，也没有把 `stale` 下发（`backend/providers/sql.py:628-667`），所以这些“待更新”状态当前无法贯通。
- 阶段组件无论 `stageUnavailable`、`stageEmpty` 还是有结果，都先固定显示“AI 生成 · 可追溯原文”（`frontend/src/screens/productMonitor/Stages.jsx:11-24`）；这是状态文案错接，不代表该区间已有 Layer B 结果。

### 9.3 AI 验证记录没有贯通到页面

- 板块说明调用 `aiValidationNote(R.AI_VALIDATION, R.AI_VALIDATION_DETAIL)`（`frontend/src/screens/sectorOverview/index.jsx:600-602`），但 `radar.js` 常量只暴露 `AI_VALIDATION`（`frontend/src/data/radar.js:323-327`）。`view.js` 已支持 detail（`frontend/src/lib/view.js:215-246`），所以 `spot_check` 详情当前无法从门面传入。
- `evaluate_gold.py` 会把抽检结果写到 `meta_kv.ai_validation`（`worker/scripts/evaluate_gold.py:409-427`），但 `/meta` 当前从固定 fixture 读取 `aiValidation`，`SqlProvider.master()` 只补 products/officials/kols/updatedAt，并不读该 DB 键（`backend/core/meta.py:52-71`；`backend/providers/sql.py:179-195`）。因此即使完成抽检，页面仍不会自动切到 `spot_check`。
- 产品页只传 level，不传 detail（`frontend/src/screens/productMonitor/index.jsx:691-693`）；官号、KOL 列表和 KOL 详情页没有页面级 validation 声明。后续接通时要同时修改 `/meta`、`radar.js` 与五页，而不是只改文案 helper。

### 9.4 合规 reader 支持 feed，但当前帖子模型不产 compliance

`_compliance_scan()` 同时读取 `target_type in ('comment','feed')`（`backend/providers/sql.py:1195-1207`），但 `post_annotation` 只写 post_type/summary/direction（`worker/jobs/annotate.py:912-929`）；当前真正的合规写入方只有 `comment_product`（`:942-958`）。因此 feed 合规是预留读路径，不是现有 active 输出。

### 9.5 竞品“AI 自动候选”实际是规则候选 + AI 原因

候选集合由客户固定映射和产品别名在评论正文的共现次数确定（`worker/jobs/synthesize.py:425-456`）；模型只生成 like/dislike reasons。页面和 provider 把非固定关系标为“AI 生成 · 待确认”（`backend/providers/sql.py:936-948`；`frontend/src/screens/productMonitor/index.jsx:486-516`）。后续若修改输入，应区分“候选发现算法”和“理由生成模型”，避免把二者当同一个 AI 决策。

### 9.6 主题不是向量聚类

主题成员关系是 Layer A `aspect` 桶，完全确定性；Layer B 只起标题、写摘要（`backend/core/themes.py:5-14,73-127`）。话题也只有一个“市场方向”桶，模型只命名（`backend/core/topics.py:1-15,24-68`）。若后续要真正多主题聚类，需要新增成员关系／cluster id，不是只改 prompt。

### 9.7 demo 结果不能用于判断 active 覆盖率

`DATA_PROVIDER=demo` 直接查 fixture（`backend/providers/demo.py:44-130`）；它可显示完整 AI 风格内容，但不会经过 annotation/synthesis。验证真实链路必须使用默认 `sql` provider（`backend/providers/__init__.py:22-40`）。

### 9.8 自动采集未实现，student 路径未接通

详见第 2.1 和第 7.2 节。当前可运行主链是人工／watch 编排的 LLM-only 路径，不是通用 scheduler 自动采集，也不是 student → LLM 两级路由。

### 9.9 `reviewState` 与置信度的页面语义仍不一致

现行写库把 `calibrated_confidence` 固定为 `NULL`，模型是否主动要求复核写在 `review_state`（`worker/jobs/annotate.py:1005-1023`）；共享 helper 也明确规定“待确认”只由 `needs_review` 触发（`frontend/src/lib/view.js:185-202`）。官号与 KOL 主列表的大部分逻辑已经使用 `reviewState`，但仍有旧文案／派生逻辑：

- KOL 页脚仍写“置信度低于阈值标待确认”（`frontend/src/screens/KolActivity.jsx:787`），与实际筛选判据冲突；帖子抽屉还展示 confidence（`:928`）。
- KOL 详情的类型说明和其他产品观点仍按 confidence 阈值构造待确认状态（`frontend/src/screens/KolDetail.jsx:237-238,275,554,573`），会把“未校准／未标注”与“模型举手”混在一起。
- 三个页面头部都硬编码“演示数据”（`frontend/src/screens/OfficialActivity.jsx:435`；`KolActivity.jsx:647`；`KolDetail.jsx:311`），没有随 `R.DATA_PROVIDER` 切换；在真实 SQL provider 下会误导用户。

### 9.10 假 loading、证据占位和不存在的 AI 入口

- 产品切换时的 loading 是固定 520 ms 定时器，不跟任何请求或后台模型状态绑定（`frontend/src/screens/productMonitor/index.jsx:95-107`）；它只是视觉过渡，不能当 AI 正在分析。
- 证据抽屉始终声明“关系数据暂不可用”，并把 Futu 原文说明为演示链接（`frontend/src/screens/productMonitor/EvidenceDrawer.jsx:87-88`）。这与第 9.1 节的证据错链一起属于 PRESENT-BUT-UNWIRED。
- 当前路由只有五个业务页面（`frontend/src/App.jsx:31-40`），没有聊天、预测、个性化建议或独立 AI 报告页面；不要把这些不存在的能力列入后端输入改造范围。
- KOL／KOL 详情 CSV 只是把已加载的页面字段序列化下载，没有额外模型调用（`frontend/src/screens/KolActivity.jsx:59-83`；`frontend/src/screens/KolDetail.jsx:57-85`）。

## 10. 明确不属于 AI 的模块

以下功能虽然与 AI 页面相邻，但源码是规则／事实字段，不应因“要调整 AI 输入”而一起改成模型逻辑：

- 时间范围、桶、基准期：`backend/core/calendar.py`、`backend/core/ranges.py`；
- 评论量、提及量、活跃账号、互动、热度公式、全市场排名：`backend/providers/sql.py:213-370,1268-1336`；
- delta／环比：`backend/core/delta.py`；
- 主题 share、负面生命周期、关注程度：`backend/core/themes.py:56-174`、`backend/core/lifecycle.py:23-43`；
- 阶段时段切分、样本阈值、相邻合并和情绪计数：`backend/core/stages.py:117-172,180-255`；
- 官号 ETF 提及芯片：`backend/providers/sql.py:482-543`；
- KOL 发帖数、互动、阵营、自家／竞品篇数和画像聚合公式：`frontend/src/lib/profile.js:42-69`；其中类型画像的**输入** `postType` 仍是 AI；
- 价格 K 线、热度序列、daily：`backend/core/prices.py:41-83`；
- progress 统计、audit、review、gold evaluation：它们观测／治理 AI 结果，本身不是业务模型推理。

## 11. 推荐的后端输入改造检查表

每次调整输入逻辑，至少按以下顺序核对：

1. **候选集合**：anchor 产品还是 body mentions？评论时间还是帖子时间？KOL／官号名单从哪里来？
2. **判定单元**：`(target_type,target_id,subject_code)` 是否仍唯一且语义稳定？
3. **上下文**：正文、父评论、标题、帖子开头、别名、作者角色哪些允许外发？
4. **预处理**：五条预过滤和近重复会不会丢掉新任务需要的字段？近重复哪些 kind 可安全传播？
5. **隐私**：新增字段先进入 `redact.py` 白名单和 forbidden-key 测试，再进入 payload。
6. **版本**：同步升级 prompt/schema/taxonomy 版本；确认 input hash 会变化。
7. **解析**：strict schema、item-id 集合、evidence 子集／原文定位是否覆盖新字段？
8. **持久化**：新增 kind、target type、`false/null` 占位语义和 supersede 规则是否定义？
9. **Layer B**：Material、事实 JSON、证据采样、阈值和 fingerprint 是否纳入新输入？
10. **读路径**：`current_annotations()` 的窗口和 subject 是否一致；SQL provider 的 `None/[]/0` 六态不能被抹平。
11. **缓存**：annotation/synthesis revision 和 synth dirty 是否会使旧结果失效？
12. **页面**：五页、证据抽屉、CSV、筛选／排序、KPI 和进度抽屉是否都验证了真实 `sql` provider，而非 demo fixture？

## 12. 最短代码导航

如果后续工作的目标就是“改模型看到什么数据”，建议从以下文件依次进入：

1. `worker/jobs/annotate.py:164-359,815-1050` — 候选、上下文、payload、模型输出拆行、证据和写库；
2. `worker/jobs/extract.py:141-339` — scope、预过滤、近重复和三类任务排队；
3. `worker/ai/redact.py:41-126` — 可外发字段与脱敏边界；
4. `worker/ai/prompts/comment_product_v2.py`、`post_annotation_v2.py`、`kol_opinion_v2.py` — 模型任务定义；
5. `worker/ai/schemas.py:58-245,297-425` — 严格输出结构与幂等指纹；
6. `worker/jobs/synthesize.py:163-456` — Layer B 事实和证据如何从 Layer A 组装；
7. `worker/ai/synth.py:40-350` — 8 类生成物 schema/prompt；
8. `radar_db/annotations_read.py:36-118` — 页面真正认哪一条结论；
9. `backend/providers/sql.py:628-1249,1511-1656` — AI 结果如何变成 API 字段；
10. `frontend/src/data/radar.js:54-242` 和五个 screen — 每个字段最终在哪些页面出现。

## 13. 本次验证记录

以下结果来自 2026-09-17 当前工作树，用于核对本文所述的 active read/write path；它们不是全仓测试通过的声明。

| 范围 | 命令 | 结果 |
|---|---|---|
| Layer A、KOL v2、Layer B、pipeline audit | 在 `worker/` 运行 `pytest -q tests/test_annotate_v2.py tests/test_kol_opinion.py tests/test_synthesize.py tests/test_pipeline_audit.py` | **48 passed** |
| Layer B 读路径、SQL provider、进度接口 | 在 `backend/` 运行 `pytest -q tests/test_layer_b_readpath.py tests/test_sql_provider.py tests/test_progress.py` | **115 passed** |
| student 分类路径 | 在 `worker/` 运行 `pytest -q tests/test_classify.py` | **1 passed, 5 failed** |

student 的 5 个失败与第 7.2 节的静态审计一致：新 job 固定进入 `stage='llm'`；`classify.py` 引用当前不存在的 `annotate.STAGE_STUDENT/STAGE_LLM`；并向不接受 `stage` 参数的 `annotate.pending_count()`／`annotate.claim()` 传参。因此不能把仓库里存在 student 代码等同于已可运行。

未以全量测试作为验收依据：完整 worker 测试收集还会受当前环境缺少 `apscheduler`、`ijson`，以及 `radar_db.revisions.ranges_touching` 缺失影响。本文对每条链路的状态结论来自源码追踪、定向测试和前后端字段对照。
