# ADR-0020 — 90 天切片 · 只用 LLM 的全量标注试点：抽取范围、规则预过滤、七维单次调用、Layer B 生成物

- **状态**：已接受（项目负责人 2026-09-14 拍板：「先抽近三个月，一次过用 AI 做完全部功能，不用预训练情感模型」）
- **日期**：2026-09-14
- **取代**：[runbook](../ai-data-integration-runbook.md) §7／§8 的 Hugging Face P0 路线与 §16 Gate 3 的金标／训练／校准各项（**挂起**，不是否决）；§20.3 中「词表命中才排队」的 L1 闸门角色（词表改为召回审计）。
- **保留**：[ADR-0017](0017-ai-annotation-pipeline-production.md)（判定单元、五张表、`gpt-5.6-luna`、证据程序定位、Alembic、口径归属）与 [ADR-0019](0019-ai-auto-publish-no-human-gate.md)（自动发布、徽章、`aiValidation=none`）全部不变。
- **相关**：PRD §3.4／§3.5／§4.1 S8／§4.2 P7–P14／§4.4 M7；CLAUDE.md 铁律 1、2

## 背景

Gate 0–2 之后管线通了、ADR-0019 之后读路径通了，页面上却只有 129 个影子判定单元。要让五个页面在真实数据上亮起来，缺的是四件事：

1. **一个按 ETF × 时间段抽取、且把不该发给模型的评论先剔掉的入口。** `annotate.enqueue_comments` 会把范围内一切非空评论排进队 —— 富途 ETF 讨论区里大量评论只聊個股（騰訊、阿里）、纯表情、重复灌水，每一条都要花一次请求。
2. **模型不知道产品叫什么。** `_build_payload` 只发 `{"code": "3033"}`，`redact.comment_payload` 却早就允许 `name` 与 `aliases`。首轮 40% `needs_context` 有一部分是这么来的。
3. **五个页面模块没有写入方**：热议总结、当前舆情总结、观点主题、负面类别、话题情绪、阶段观点、关联竞品候选、产品相关 KOL、KOL 其他产品观点。runbook §9 给它们的 P0 实现是 BGE 向量聚类＋GPT 命名，需要本地模型与 GPU。
4. **配了 `AI_CONCURRENCY=4` 但 `run()` 是串行的。** 26 万条串行约一天。

负责人的三条决定：只抽近 90 天；全部用 `gpt-5.6-luna`，不引入任何预训练情感模型（「效果都很一般」）；分析前先剔除个股／无关标的的评论，只保留该 ETF 的评论。

## 决策

### 1. 抽取范围是一等对象：`analysis_scopes` ＋ `annotation_jobs.scope_id`

`worker/jobs/extract.py` 按 `--codes/--own/--all` 与 `--from/--to`（或 `--range d7` 相对锚点）取候选，建一条 `analysis_scopes`，任务打上 `scope_id`；`annotate.run(scope_id=…)` 只领本范围的任务。时间口径沿用市场域「评论归属帖子日期」。`--with-baseline` 把紧邻的上一等长区间一并纳入（环比与生命周期要基准期），基准期任务优先级低于当前期。

`annotation_runs` 记的是一次执行，`analysis_scopes` 记的是一个业务窗口：覆盖率挂在窗口上。

### 2. 规则预过滤五条，剔除的以 `provider="rule"` 落库

`worker/ai/prefilter.py`，顺序判定、命中即停：`empty` → `tag_only`（只剩标的标签）→ `sticker_only`（表情／数字／标点）→ `exact_duplicate`（同作者同帖同正文，作者未知不判）→ `offpool_stock_only`（正文提到的标的**全部**是池外个股，且没有该 ETF 的任何别名／族叫法／指代词／产品属性词）。

被剔除的评论写两行 `annotations`：`relevance=irrelevant` 与 `text_quality={"rule": …}`，`annotation_runs.provider='rule'`。它们与模型结论走同一条读取规则（ADR-0019），可追溯、可 `review.py --reject` 回滚、可按规则分组审计。**丢掉不写**会让「没标过」和「被规则剔了」在库里变成同一个样子。

第 5 条带 `--no-prefilter-offpool` 开关；`scripts/calibrate.py` 抽 100 条被它剔掉的评论给人看误杀率，>5% 就关掉改由模型判。其余四条无语义判断。

判不出就放行。模型 Prompt 里的 `relevance` 是第二道、也是最终的一道过滤。

### 3. 评论标注 v2：一次调用七个维度

`CommentAnnotationV2`（`schema_version=v2`）在 v1 的相关性／态度／aspect／证据之外加：`market_direction`（对大盘的方向，与产品态度独立，喂 P13）、`compliance_tags[]`／`compliance_rationale`／`compliance_evidence`（runbook §20 五类，空数组必须落库）。写库拆五个 kind：`relevance` / `attitude` / `aspect` / `market_direction` / `compliance`；合规证据挂在合规行上。

依据：arXiv 2604.03684（2026，8 个生产模型、96 万次分类）—— 每批 25–100 条、每条同时判 ≤10 个维度，精度损失 <2pp。这里最初保留的 30 条方案已被 [ADR-0028](0028-exact-comment-eligibility-and-official-attribution.md) 取代：只比较 b=1 与 b=5，达到质量门槛且实测吞吐至少 3 倍才启用 b=5；System One 固定单条，batch=30 不启用。

`compliance_signal` 不再是独立任务；runbook §20.3 的词表退为召回审计（`audit.py --lexicon-recall`）。

### 4. Prompt 针对富途评论区改写；产品有名字

`worker/ai/prompts/comment_product_v2.py`：产品块发 `code + name + aliases`（`ai/lexicon/product_aliases.py`，120 只的代码写法／简繁全名／社区俗称／族叫法）；个股 vs ETF 的边界（只聊個股 ⇒ 无关；用成分股解释这只 ETF 的涨跌 ⇒ 相关）；杠反产品术语；粤语短回复配父评论；中性偏置对策（评价了就得选正／负，拿不准用 `needs_review` 不用 `neutral` 兜底）；合规五类定义与正反例；证据一字不改。评论上下文新增帖子正文开头 200 字（`post_context`），Prompt 交代它只用于理解语境。

`post_annotation_v2.py` 小改：payload 带挂载产品、官号活动帖与产品推介的边界、纯图帖 `summary=null`。新增 `kol_opinion_v1.py`（PRD §4.4 M7）：只跑合作 KOL 的评论，输出 ≤30 字观点＋设计源 `ACTIONS` 8 枚举。

Prompt 与 schema 都按版本注册（`ai/prompts/get(task, version)`、`schemas.model_for(task, version)`），v1 保留给回放与对照实验。

### 5. Layer B：产品 × 区间生成物，模型只写字，数在 `backend/core/`

新表 `synthesis_outputs(code, range_key, anchor, kind, subkey, input_fingerprint, value_json, evidence_ids_json, …)`，七种 kind：`hot_summary`（≤30 字）、`summary`（1–4 条要点）、`theme_label`（极性 × aspect 桶的标题＋摘要）、`neg_category`、`stage_unit`（时段 7 类＋≤40 字）、`stage_summary`、`topic_label`、`competitor_reason`。

**主题＝极性 × aspect 桶。** 没有向量聚类，主题的成员关系只有一个确定性依据：模型逐条给的 `aspects`。`backend/core/themes.py` 分桶计数、`core/lifecycle.py` 算新增／持续／消退与关注程度、`core/stages.py` 按 `STAGE_RULE` 切时段与合并（设计源算法逐字移植，唯一替换是分类来自模型）、`core/topics.py` 数市场方向三色。模型只给桶起名、写句子。

`worker/jobs/synthesize.py` 以只读方式 import 这几个叶子模块组「事实 JSON」（`backend/core/__init__.py` 提议的 `common/` 抽包留待下一次重构；这里**没有**在 worker 里重写任何公式），按（时间桶 × aspect × 极性）分层抽样带 id 的证据引文，发给模型；`ai/synth.py` 的 strict schema ＋ validator 保证：字数上限、**不许出现比例／占比类表述**（`%`「六成」「过半」「所有人一致」）、`evidence_ids ⊆ 输入 id`。`input_fingerprint` ＝ 参与的 annotation_id 集合＋事实 JSON＋Prompt 版本：底层标注一变旧生成物自动被 supersede，没变就不花钱。有效态度 < `LOW_SAMPLE` 时 `hot_summary`／`summary` 写 `{"status":"low_sample"}` **不发请求**。

现行结论的读取规则（ADR-0019 §1）从 `sql.py` 下沉到 `radar_db/annotations_read.py`，backend 与 worker 共用那一份。

### 6. 读路径：三态分明

`SqlProvider` 的九个方法接通。每处严格区分：没有一条态度标注 ⇒ 整块 None（暂不可用）；有标注、模型还没写字 ⇒ 数是真的，文字位为 aspect 固定名／None，`labelStatus='unavailable'`；生成过 ⇒ 真值带 `reviewState` 与 `evidenceIds`。`confidence` 一律 None（无校准概率，ADR-0017 §4）。`kol_mentions_for` 由态度标注 ∩ KOL 作者名派生，<3 条不输出主要态度。

`test_provider_parity.py` 增加两条规则：同一路径两边 `status` 不同时不比键集（形状按六态随 status 变是契约本身）；`EXTENSION_KEYS` 白名单（`reviewState` / `evidenceIds` / `labelStatus` / `aiStatus` / `reasonStatus` / `key` / `subkey` / `aspect` / `units` / `points` / `category`）是 sql 侧允许多出的键，其余新键仍判失败。

### 7. 并发与预算

`annotate.run` 用 `ThreadPoolExecutor(cfg.concurrency)` 并发处理批次；`claim` 在进程内加锁串行化，且先取完 SELECT 再 UPDATE（否则 SQLite 在多线程下立刻 BUSY_SNAPSHOT，不经过 `busy_timeout`）。只支持单进程多线程；跨进程要换 `SELECT … FOR UPDATE SKIP LOCKED`。`--dry-run` 给条数／请求数／token 区间（不是报价），`--budget-requests N` 是价格未知时唯一能对着账单核的旋钮。`AI_SERVICE_TIER=flex` 由 `scripts/probe_gateway.py` 探明网关透传后再开。

### 8. 范围与授权

本试点：dump 近 90 天、120 只产品池（自家 61 只优先、d7 窗优先）、评论约 26 万判定单元、帖子只跑 KOL 32＋官号 20 的作者帖。ADR-0017 明写数据治理四项未答复前不应扩到全量；本次按项目负责人 2026-09-14 的显式授权执行，四项答复与网关价格仍应向网关方索取。

## 后果

- **五个页面的 AI 模块有了写入方**：跑完 `extract → pipeline` 后，热议总结、舆情总结、主题、负面类别、话题、阶段、竞品候选、KOL 提及、KOL 观点从「暂不可用」变为真值；未生成的部分仍诚实地显示缺失态。
- **准确率仍然未知**（ADR-0019）。`audit.py` 只报可靠性与成本指标；`calibrate.py` 的一致率只写 `.scratch/`，不进页面。
- **主题不是聚类。** 极性 × aspect 只有 9 个桶，`other` 可能很大；真正的动态主题要等向量聚类（P1）。页面上这一点靠 `title` 具体化（模型起名）缓解，不靠编数。
- **规则第 5 条会误杀。** 「騰訊拖累」类评论靠别名／指代词／产品属性词放行，但覆盖不到的写法会被剔掉；剔掉的都在库里、可回滚、可审计，误杀率由 `calibrate.py` 抽查。
- **worker import 了 backend/core。** 单向、只读、只限叶子模块；`Dockerfile` 相应 COPY。抽 `common/` 包是下一次重构，不是这次。
- **成本可算但未算**：网关没有价格，`estimated_cost` 仍 NULL。按 Gate 2 实测放大 1.4 倍估算，26 万条约 29M 输入／20M 输出 token，请求约 9 千次。

## 否决的备选

- **本地 HF 模型（Mengzi／BGE／SetFit）做分类与聚类**：负责人明确不要；且需要 GPU、权重下载与金标。挂起，不否决。
- **合规单独任务＋词表预筛**（runbook §20.3 原设计）：每条评论要发两遍；词表做闸门会漏召回。合并进 v2 单次调用，词表改审计。
- **模型直接写总结里的比例**：铁律 1。schema 里没有数值字段，validator 拦比例词。
- **规则层直接丢掉个股评论不落库**：「没标过」与「被剔了」在库里分不开，页面上它们相反。
- **`synthesis_outputs` 塞进 `annotations`**：`target_id`（BigInteger）与 `subject_code` 都要挪用，两列失去原义。
- **让 synthesize 走 backend HTTP API 取事实**：多一层服务依赖，且 API 是按页面契约裁剪过的，缺 Layer B 要的桶级明细。
- **批大小加到 30 或 100**：不启用。当前仅允许经 b=1 vs b=5 门禁验证后的 5 条批量；见 ADR-0028 与操作单。

## 实施清单（本 ADR 随代码一并提交，逐项已完成 ✓／需本机资源 ○）

| # | 内容 | 落点 | 状态 |
|---|---|---|---|
| 1 | 切 90 天：`import_dump --days 90` → `etl` → `alembic upgrade head` | 本机 | ○ 命令见 [docs/llm-90d-operations.md](../llm-90d-operations.md) |
| 2 | 产品别名／个股／合规词表 | `worker/ai/lexicon/` | ✓ |
| 3 | 抽取程序 | `worker/jobs/extract.py` | ✓ |
| 4 | 规则预过滤 | `worker/ai/prefilter.py` | ✓ |
| 5 | Schema v2 | `worker/ai/schemas.py` | ✓ |
| 6–7 | Prompt v2（评论／帖子）、KOL 观点 Prompt | `worker/ai/prompts/` | ✓ |
| 8 | payload 带名称别名与帖子正文开头 | `worker/jobs/annotate.py`、`worker/ai/redact.py` | ✓ |
| 9 | 并发、scope 领取、`--dry-run`、`--budget-requests` | `worker/jobs/annotate.py` | ✓ |
| 10 | 网关探测 | `worker/scripts/probe_gateway.py` | ○ 需 Key |
| 11 | 一致性实验 | `worker/scripts/calibrate.py` | ○ 需 Key |
| 12 | 一次过运行 | `worker/jobs/pipeline.py` | ○ 需 Key |
| 13 | 迁移 0003 | `radar_db/migrations/versions/0003_scopes_and_synthesis.py` | ✓ |
| 14 | core 口径 | `backend/core/{themes,lifecycle,stages,topics}.py` | ✓ |
| 15–16 | Layer B Prompt／schema／作业 | `worker/ai/synth.py`、`worker/jobs/synthesize.py` | ✓ |
| 17 | 读路径 | `backend/providers/sql.py`、`radar_db/annotations_read.py` | ✓ |
| 18 | 报表 | `worker/jobs/audit.py` | ✓ |
| 19 | 验收 | 后端 556、worker 303 项测试全绿；`real-data-check` 需本机库 | ○ 部分 |
| 20 | 文档 | 本 ADR、runbook §16、`CLAUDE.md` | ✓ |
