# ADR-0021 — 蒸馏漏斗：规则与近重复折叠 → CPU 学生模型 → Luna 难例 → Layer B 逐区间并行

- **状态**：已接受（项目负责人 2026-09-15 定：CPU-only、可访问 Hugging Face；400 条人工核对集只做量尺不做门槛；LLM 通道批 5 × 并发 16–24，一致率 ≥0.9 才切批；端到端 ≤30 分钟）
- **日期**：2026-09-15
- **取代**：[ADR-0020](0020-llm-only-90d-pilot.md) 中「全部用 `gpt-5.6-luna`，不引入任何预训练情感模型」这一条。ADR-0020 的其余各条（抽取范围 `analysis_scopes`、五条规则预过滤、v2 七维单次调用、Layer B 生成物、读路径三态、并发与预算、范围与授权）**全部保留**；本文只是在规则层与 Luna 之间插入一层，并把 Luna 之后的汇总改成逐区间并行。
- **保留**：[ADR-0017](0017-ai-annotation-pipeline-production.md)（判定单元、五张表、证据程序定位、Alembic、口径归属）、[ADR-0019](0019-ai-auto-publish-no-human-gate.md)（模型写下即发布、徽章由 `review_state` 驱动、`aiValidation` 如实披露）。
- **相关**：PRD §3.6／§3.9／§4.2 P7；CLAUDE.md 铁律 1、2；[runbook §24](../ai-data-integration-runbook.md)；[gold-labeling-guide](../gold-labeling-guide.md)

## 背景

ADR-0020 的试点在本机跑出了三个数字，一起决定了「逐条发 Luna」走不到头：

1. **30 条合批一致性不达标**，`AI_MICRO_BATCH_SIZE` 退回 1（runbook §23、`docs/august-ai-pilot-2026-09-14.md`）。批 1 意味着每条评论一次请求，26 万判定单元 = 26 万次请求。
2. **`full_own.py --watch` 按产品轮询、`pipeline.run` 要等 scope 全部 done 才 synthesize**：61 只产品 × 60 天的队列里，昨天的评论早判完了，「昨日」那块页面却要等 60 天前的评论也判完才亮；`_close_run` 又把该产品六个区间全部标脏，`_synth()` 见脏返回空 —— 页面在跑的大部分时间里是空的。
3. Luna 是推理模型，单条请求 p50 在秒级；即便并发放到 24，26 万条也是小时级。

同时库里已有约 5.2 万判定单元的 Luna 现行标注（provider ≠ rule），这是一份现成的、与生产 Prompt 同分布的训练集。负责人 2026-09-14 说「预训练情感模型效果都很一般」，说的是**通用**情感模型直接拿来用；用 Luna 自己的标注蒸馏一个学生，学的是 Luna 在这个评论区的判法，不是别处的情感词典。

## 决策

### 1. 四层漏斗

```
全部评论候选
  └─ L0  规则五条（ADR-0020 §2 不变）＋ 近重复折叠（新）           CPU，千条/秒
       └─ L1  学生模型 jobs/classify.py（新）                      CPU，ONNX int8，批 64
            ├─ 高置信 ⇒ annotations(provider=local_model)，任务 done
            └─ 命中任一路由规则 ⇒ 任务 stage=llm，学生行仍落库
                 └─ L2  Luna 七维（ADR-0020 §3 不变），批 5 × 并发 N   与 L1 并行
                      └─ Luna 行 supersede 学生行
  └─ L3  Layer B synthesize（ADR-0020 §5 不变）                    按 (code, range) 就绪逐对触发，8 线程
```

任务表加一列 **`annotation_jobs.stage ∈ {student, llm}`**（Alembic 0008，默认 `student`）。`claim(stage=…)` 按段领取：`jobs.classify` 只领 `student`，`pipeline.run` 对评论任务只领 `llm`。帖子与 KOL 评论任务只有 `llm` 一段（迁移把它们全部置 `llm`）。两段领的是**不相交**的任务集，所以学生通道与 Luna 通道可以并行：`full_own.py --watch` 下学生是一个独立线程，按产品轮转跑 `classify.run`，调度器的 tick 只跑 `pipeline.run(stage=llm)`；学生边产出 `stage=llm` 的任务，Luna 边消费。不带 `--watch` 时没有线程，每个 tick 顺序做 classify → pipeline，行为可预测。

### 2. 近重复折叠（L0 第六条，是折叠不是判无关）

`worker/ai/neardup.py`：同 `(subject_code, 帖子日)` 内，正文归一化（去空白、全角转半角、去表情占位与标的标签）后算 64 位 simhash，汉明距 ≤3 成簇；自己实现，不引第三方。簇代表进 L1/L2；成员写 `duplicate_cluster={"of": <代表 comment_id>}`，并在代表拿到结论后复制其 `relevance`／`attitude` 行（`annotation_runs.provider='propagated'`，`input_hash` 含代表的 `annotation_id`），成员任务直接 done。

它放在五条规则之后、排队之前。与 `exact_duplicate`（同作者同帖同正文）的区别：这里跨作者、跨帖、允许三位以内的差异。为什么按日分桶：同一句话隔了一个月再出现，语境已经不同，不该抄一个月前的结论。

### 3. 学生模型（`worker/models/`）

| 项 | 决定 |
|---|---|
| 主选 | `Langboat/mengzi-bert-base-fin`（金融语料继续预训练的中文 BERT，12 层） |
| 备选 | `hfl/chinese-roberta-wwm-ext`（通用强基线）、`hfl/rbt3`（3 层，CPU 约 3 倍快，吞吐不够时换它） |
| revision | 锁定在 `models/registry.py`；落库 `model_id='<hf id>@<revision>'`。不锁的后果是两台机器不同日期拉到不同权重而库里分不出 |
| 训练集 | `models/dataset.py`：`current_annotations()` 里 provider ∉ {rule, local_model, propagated} 的现行判定单元；文本＝与 Luna **完全相同**的 `ai.redact.comment_payload` 序列化（正文＋产品块＋父评论＋标题＋帖子上下文）。学生看到的必须是 Luna 看到的，否则学的是另一个任务 |
| 标签 | relevance 三类；attitude 三类（仅 relevant 有）；aspect 多标签（训练集正样本 ≥5,000 才训） |
| 对照集 | 按 `(subject_code, ISO 周)` 分组切 10%：同一产品同一周的评论互相复读，按行随机切会泄漏 |
| 训练 | seq 128，两头分训，CPU 可跑（1–2 小时）；`--tiny` 用随机初始化的极小 BERT 供离线测试 |
| 校准 | 训练后在对照集上做温度缩放，每头一个温度，写 `calibration.json`（含对照集一致率与混淆矩阵） |
| 导出 | optimum → ONNX → int8 动态量化；int8 与 fp32 在对照集上预测一致 ≥0.99 才用 int8，否则保留 fp32 并在 `export.json` 里写明 |
| 推理 | onnxruntime，批 64，`intra_op_num_threads=os.cpu_count()`，输出经温度缩放的各头概率 |

**`calibrated_confidence` 的含义**：学生写进 `annotations.calibrated_confidence` 的是**预测类的温度缩放概率**，它的语义是「与 Luna 一致的校准概率」—— 温度是在「学生 vs Luna 对照集」上拟合的，所以 0.9 读作「这条判成这样，与 Luna 会判的一致的概率约九成」。它**不是**「与人工一致的概率」；与人工的关系由 §6 的人工核对集单独度量。ADR-0017 §4 说过没有校准概率之前 `confidence` 一律 None；现在学生行有了，Luna 行与规则行仍是 NULL，前端 `lowConfidence = 0.7` 只对有值的行生效。

### 4. 路由规则与阈值

`classify.run` 对每条学生结论按序判四条，**任一命中 ⇒ 任务 `stage='llm'`、`status='pending'`，留给 Luna**；学生行仍落库，Luna 行会 supersede 它（同一判定单元同一 kind 的链末规则不变，ADR-0019 §1）：

| 规则 | 阈值 | 环境变量 | 为什么 |
|---|---|---|---|
| 任一头 max prob 低 | < 0.85 | `STUDENT_ROUTE_THRESHOLD` | 学生没把握的交给 Luna；0.85 是起点，按对照集一致率与人工核对结果调 |
| relevance = needs_context | — | — | 学生说「看不出」就是 Luna 的活（Luna 拿到父评论与帖子上下文的完整 Prompt） |
| 态度前两类概率差小 | < 0.15 | `STUDENT_MARGIN_THRESHOLD` | 正／负之间摇摆的评论正是最容易错的一类 |
| 合规词表命中 | `ai/lexicon/compliance_zh.py` | — | 合规五类只有 Luna 判（学生没有合规头）；词表在这里是召回，不是判定 |

未路由的学生结论补一行 `compliance={"tags":[],"rationale":null,"screen":"lexicon"}`（provider 用规则 run）：它说的是「词表没命中，没有送 Luna 判合规」，与 Luna 判过的 `{"tags":[]}` 在库里分得开。`review_state='needs_review'` 当任一头概率 < `STUDENT_REVIEW_THRESHOLD`（默认等于路由阈值）—— 与 ADR-0019 的徽章语义一致：模型自己举手，不是「还没人看过」。

**阈值起点的依据**：0.85／0.15 取自本次方案，目标是进入 Luna 的份额 ≤20%。本文写就时没有真库可数，所以「现有 Luna 标注 relevance 三值占比、attitude 分布、规则剔除占比、同产品同日近重复率」四个量化数字**留空**，由本机在 `alembic upgrade head` 之后跑 runbook §24.5 的 SQL 填进本节；阈值随后按 §24 的验收数字调。合成库上的管线数字见 runbook §24.6，它们只证明管线通、不代表任何准确率。

### 5. LLM 通道提速与顺序

- **`_write_batch`**：`_process` 末段整批一个事务（标注＋证据＋任务 done），语义与逐条写完全一致（人工裁决不覆盖、证据程序定位、`needs_review`），一批只拿一次 SQLite 写锁。
- **最近窗口优先**：`extract.py` 的 `priority = recency_tier(posted_at, anchor)`（距锚点 ≤7 天 +30、≤14 天 +20、≤30 天或 mtd 窗内 +10、更早 0）＋ own +2 ＋ current +1；帖子与 KOL 评论任务同函数。`python -m jobs.annotate --reprioritize` 用同一函数重算全部 pending 任务，幂等。
- **逐区间就绪触发 Layer B**：`pipeline.run` 不再等 scope 全部 done；对 `core.calendar.PRESETS` 六档逐个判「该区间的当前窗＋基准窗内没有 pending/claimed 的评论任务（不分段）」，就绪的 `(code, range)` 立刻进 `synthesize`。`_close_run` 里 `mark_synthesis(ranges=…)` 只标与本轮变更评论 `posted_at` 相交的区间（按 run_id join comments → feeds 取日期范围），不再整只产品六档全脏。
- **Layer B 并行**：`synthesize.run` 用 `ThreadPoolExecutor(8)` 按 `(code, range)` 并行，每对内部顺序不变（主题起名 → 总结 → 话题 → 阶段 → 竞品）；每线程自己拿连接，写库集中在 `_write`。出错的对**不清**脏标，下一轮再来。
- **批与并发**：`scripts/probe_gateway.py --concurrency N` 探网关并发上限（429 数、`Retry-After`、p50/p95）；`scripts/calibrate.py --batch 5` 做 b=1 vs b=5 一致率，报告键随 N 走（`b1_vs_bN`），≥0.9 才把 `AI_MICRO_BATCH_SIZE` 从 1 改到 5。网关并发上限 <24 时 30 分钟目标退为 40 分钟，runbook 如实写。

### 6. 人工核对集与 `aiValidation=spot_check`

`scripts/gold_sample.py` 按 own/peer × 学生预测极性 × 置信带（<0.7／0.7–0.85／≥0.85）× 简/繁/粤 分层抽 400 条，导出 `gold-400.xlsx`（人填）与 `gold-400-model-labels.xlsx`（模型标签，分开放免得看着答案填），两个文件都写到数据目录（仓库外）。`scripts/evaluate_gold.py` 算学生、Luna、组合路由（学生高置信用学生，否则用 Luna）三套的 relevance/attitude 准确率、attitude macro-F1、混淆矩阵，写 `meta_kv.ai_validation`：

```json
{"level":"spot_check","n":400,"date":"YYYY-MM-DD",
 "relevance_accuracy":0.91,"attitude_accuracy":0.87,"attitude_macro_f1":0.85,
 "by_system":{"student":{…同三键},"llm":{…},"combined":{…}}}
```

`spot_check` 的意义：`/meta` 的 `aiValidation` 从 `none` 变成「抽了 400 条人工核对，数字是这些」。它是**量尺不是门槛**：ADR-0019 不变，模型写下即发布，人工核对不批准任何一行，也不拦任何一行。400 条的 95% 置信区间约 ±5 个百分点，页面声明必须带条数与准确率，仍然**不许**出现「已核验」。判定规则在 [gold-labeling-guide](../gold-labeling-guide.md)。

### 7. 事件流与只读进度端点

新表 `worker_events(event_id, ts, level, stage, code, scope_id, run_id, message, data_json)`；`radar_db/events.py` 的 `emit()` 独立短事务、任何异常只记日志绝不向上抛、超过 6,000 行删到最近 5,000 行。L0／L1／L2／L3／orchestrator 各在批或对完成时记一条。`GET /api/v1/progress` 与 `/progress/events?after=` 只读地暴露队列（按 stage × status）、Layer B 脏标与产出、近 5 分钟吞吐（无样本为 null 不写 0）与事件流；`backend/core/progress.py` 是它唯一的实现处，demo provider 与未迁到 0008 的库返回 `unavailable`。工作台没有任何启动／停止作业的控制。

### 8. provider 取值一览

| `annotation_runs.provider` | 谁写 | 含义 |
|---|---|---|
| `rule` | `extract.py`、`classify.py` 的 compliance 补行 | 规则层结论 |
| `local_model` | `classify.py` | 学生模型结论，`calibrated_confidence` 非空 |
| `propagated` | `neardup.propagate` | 近重复成员抄代表的结论 |
| `openai_compatible` | `annotate.py` | Luna 结论（ADR-0017 不变） |

`dataset.py` 训学生时排除 `rule`／`local_model`／`propagated`：规则行是词表判的，学它等于把词表背下来；学生自己的行与抄来的行不是新信息。

## 否决的备选

- **通用中文情感模型直接用**（负责人 2026-09-14 已否）：它们学的是电商评论／微博的褒贬，对「点差太大」「分派稳定」「杠反产品」这些 ETF 讨论区的判法一无所知，与 Luna 的分布也不一致，供不了 `calibrated_confidence` 该有的语义。蒸馏自 Luna 的学生没有这个问题。
- **批 30 合批**：本机实测一致性不达标（runbook §23）。批 5 是折中：单批小到推理模型不崩，又比批 1 少 80% 的请求；是否切到批 5 由 `calibrate --batch 5` 的一致率决定，不预设。
- **向量聚类做近重复**：需要 embedding 模型与 ANN 索引，而这里要解决的只是同日复读；simhash 分桶纯 Python 就够，且可解释（汉明距是几就是几）。
- **在 tick 里串行 classify → pipeline**：学生是 CPU 活（10–12 分钟判完 61 只），Luna 是网络活（20–25 分钟），串起来 35 分钟超预算；并行的代价是一个线程与按 stage 分段领取，两者都简单可验证。
- **人工核对做成发布门槛**：ADR-0019 已决；400 条只够给出 ±5 个百分点的量尺，拿它当门槛既拦不住错也放不出对。

## 后果

- 进入 Luna 的评论份额从 100% 降到目标 ≤20%，请求数随之降；14 万条积压端到端目标 ≤30 分钟（网关并发 24 达成时）。
- 库里出现三种新的 provider（`local_model`／`propagated`，以及 compliance 补行的 `rule`），读路径的现行结论规则不变，五个页面不需要认识它们；但审计（`audit.py`）与 `gold_sample.py` 按 provider 分组看数字。
- 学生行的 `calibrated_confidence` 是库里**第一批**非空校准概率；前端「待确认」徽章仍由 `review_state` 驱动，`lowConfidence` 阈值开始对学生行生效。
- `worker/requirements-ml.txt` 单独列 ML 依赖（torch CPU 约 200 MB）；backend 一行模型代码都没有，日常 worker 镜像只需 onnxruntime。
- 需要人做的两件事：训练一次学生（CPU 1–2 小时，一次性）；填 400 条核对表（2–3 小时，可两人各 200）。
