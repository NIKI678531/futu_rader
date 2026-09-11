# 富途 ETF 评论态度分析：模型与处理管线选型调查

> 调查日期：2026-09-10  
> 目标：在评论量较大、成本敏感的前提下，可靠识别一条评论是否在讨论指定 ETF，并在相关时判断其产品态度为积极／中性／消极。  
> 价格具有时效性；本文所有价格均以调查日的官方页面为准，上线前必须再次核对。

## 1. 结论先行

现在不应直接拍板“用某一个情感分析模型跑全量”。推荐的生产方案是一个级联管线：

1. **规则与结构化字段预处理**：精确去重、空文本／纯表情／模板广告过滤；使用富途已经提供的挂载标的和正文结构化标的，不让模型重复做实体识别。
2. **相关性判断**：把评论分成 `relevant / irrelevant / needs_context`。词频、TF-IDF 或 BM25 只作为可解释基线和高频噪声发现工具；生产过滤优先比较本地 encoder 分类器、embedding／reranker 与小型 LLM。
3. **产品态度分类**：只对 `relevant` 评论输出 `positive / neutral / negative`；`irrelevant` 绝不能混进 `neutral`。
4. **疑难升级**：本地模型置信度低、反讽、指代不清或需要帖子／父评论上下文的记录，交给托管 LLM。
5. **聚合与摘要**：评论级标签完成后，再按产品／时间桶计算比例、主题和摘要。不要让 LLM 直接看一大包原文后报一个总体比例，否则无法追溯、补跑或校准。

若今天必须指定 PoC 候选：

- **批量 LLM 基线／疑难裁决：同时测试 `deepseek-v4-flash` 与 `qwen3.7-flash`，关闭 thinking**。前者官方当前提供 JSON 输出、1M 上下文和较高并发；后者在阿里云百炼的短输入档价格极低，并支持 Batch 半价。[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)、[阿里云百炼模型调用价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- **质量上限对照：`deepseek-v4-pro` 与项目原定 `claude-haiku-4-5-20251001`**。二者用于同一金标集上的 A/B，不建议未经评测就跑全量。
- **原有 XLM-R／MacBERT：降级为失败基线，不再作为主候选**。项目已有“很多文本识别不出来”的实测反馈；除非能证明过去只是未微调、输入截断或阈值配置错误，否则没有理由继续在它们上面投入主线时间。
- **新的本地相关性质量首选：`Qwen3-Reranker-4B`**，把“指定 ETF 与产品属性定义”作为 query，把评论及必要上下文作为 document，并用项目 hard negatives 校准阈值；`0.6B` 只作为高速档。Qwen 官方自测中，4B 的中文 C-MTEB reranking 分数高于 0.6B，但这仍不是本项目准确率保证。
- **新的本地态度主候选：真正的序列分类器**。成熟纯文本路线测试 `Qwen3-1.7B-Base + Qwen3ForSequenceClassification`，新架构路线测试 `Qwen3.5-2B-Base + Qwen3_5TextForSequenceClassification`；`Qwen3.5-4B` non-thinking 只作教师、难例裁决和质量上限。它们仍需项目金标，不是下载后即可获得正确 ETF 态度口径。

一句话推荐：**先用 DeepSeek V4 Flash 与 Qwen 3.7 Flash 建立低成本基线和辅助标注，再用人工复核数据训练 Qwen3-1.7B 与 Qwen3.5-2B 的分类头；相关性比较 Qwen／BGE reranker，Qwen3.5-4B 只处理疑难和提供质量上限。确认质量后才尝试蒸馏到更小的高速档。生产中让本地分类器吃大头、Flash 模型处理疑难、Pro 级模型只做极少量审计。**

## 2. mentor 建议中正确的部分，以及需要修正的部分

可以把 mentor 的想法解释为：先用便宜方法减少无效输入，再把保留下来的评论成批交给模型。这一方向正确，但“按频率排序后剔除无关评论”需要拆开。

### 2.1 频率适合做什么

- 找完全重复和近重复评论、广告模板、灌水短语；
- 统计高频产品词、费用／流动性／跟踪误差等候选词；
- 构造抽样分层，避免评测集全是最常见的中性句；
- 做 TF-IDF／BM25 的透明基线；
- 在完成评论级态度分类后，给观点主题按出现频率排序。

### 2.2 频率不适合独立决定什么

- 低频不代表无关：“溢价这么离谱还怎么买”可能只出现一次，却是明确产品负面；
- 高频不代表相关：“大盘今天要跌”在 ETF 讨论区很常见，但按本项目口径属于市场方向，不是产品态度；
- 词袋方法难处理否定、反讽、比较和指代；
- 先按频率硬删除，会造成不可恢复的选择偏差，后面的强模型再好也看不到被删掉的内容。

因此，频率层只能做**候选召回和明显噪声处理**。相关性过滤必须以“保住相关评论”为优先目标；低置信度样本进入下一层，不直接丢弃。

## 3. 为什么普通“情感 NLP”开箱效果可能不好

质疑通用 NLP 并非完全没有道理，但问题不是“NLP 天生不行”，而是训练目标经常与这里的标签不一致。

项目定义要求只判断**对产品本身**的态度；费用、流动性、跟踪表现、产品机制、分红和使用体验才进入产品态度，单纯预测指数或价格涨跌进入“产品话题情绪”。见 [PRD §3.4](../PRD.md#34-产品态度分类)。这会产生以下例子：

| 评论 | 通用情感 | 本项目输出 |
|---|---:|---|
| “恒指还要跌” | 消极 | 与产品态度无关 |
| “3033 跟踪误差太大” | 消极 | 产品消极 |
| “跌下来正好继续加这只 ETF” | 可能消极 | 产品积极／至少不能按“跌”判消极 |
| “手续费真良心” | 可能积极，也可能反讽 | 需要上下文／待确认 |
| “它就是不行” | 消极 | 若不知道“它”指什么，需要上下文 |

通用商品评论或微博情感数据集通常不会实现这条“产品态度 vs 市场方向”的边界。因此：

- 开箱 sentiment checkpoint 只能当 baseline，不能因模型名含“金融”或“中文”就直接采用；
- 一个在公开榜单上分数高的模型，可能在本项目标签上系统性出错；
- 经过项目数据微调的小型 encoder，完全可能比未适配的大模型更稳定、更便宜；
- 最终结论只能来自同一份本项目金标集，而不是供应商 benchmark。

## 4. 推荐的端到端管线

```text
原始评论
  │
  ├─ 0. 数据检查：空文本、损坏 JSON、评论截断状态
  ├─ 1. 精确去重／近重复标记／广告与纯表情规则
  ├─ 2. 为每个 (comment_id, product_code) 建立判定单元
  │      └─ 带入产品名、代码、帖子标题、必要时父评论
  ├─ 3. 相关性模型
  │      ├─ 高置信 irrelevant → 留存标签，不做态度
  │      ├─ 高置信 relevant   → 态度模型
  │      └─ needs_context      → LLM
  ├─ 4. 态度模型
  │      ├─ 高置信 positive / neutral / negative → 入库
  │      └─ 低置信、反讽、混合态度 → LLM
  ├─ 5. LLM 疑难裁决（结构化 JSON）
  ├─ 6. 抽样人工复核、漂移监控与主动学习
  └─ 7. 后端按冻结口径聚合，不由模型计算业务指标
```

### 4.1 为什么判定单位必须是 `(comment_id, product_code)`

PRD 明确一条评论可分别计入多只 ETF（[PRD §3.2](../PRD.md#32-提及与去重)）。同一句比较评论可能对 A 产品积极、对 B 产品消极，因此不能只保存“这条评论总体是什么情绪”。

当前 `annotations` 表只有 `target_type + target_id + kind`，没有 `product_code`（[schema.py](../../radar_db/schema.py)）。在实施模型前应补一个 `subject_code`／`scope_key`，或将每个产品的结果放进可验证的结构化 value；前者更容易查询、重跑和建立唯一约束。

### 4.2 不要把无关评论当成中性

建议标签分两步：

```json
{
  "relevance": "relevant | irrelevant | needs_context",
  "attitude": "positive | neutral | negative | null"
}
```

`neutral` 表示“确实在评价该产品，但态度中性”；`irrelevant` 表示“没有对该产品形成可分类的评价”。将两者合并会扩大中性占比，改变页面展示和净值解释。

### 4.3 上下文按需加入，不要一律塞满

默认输入包括产品代码／名称和评论正文。只有出现代词、回复关系、反讽或极短句时，再加入：

- 帖子标题及与该产品相关的一小段正文；
- 父评论；
- 富途的挂载标的和结构化正文提及；
- 原文语言信息。

这样既控制 token 成本，也避免长帖子里无关信息干扰分类。

## 5. 候选模型与各自适用位置

### 5.1 传统词频／线性模型：必须保留的 baseline

候选：字符 n-gram 或词 n-gram TF-IDF + Logistic Regression／Linear SVM。

优点：训练和推理极快、CPU 足够、易解释、概率可校准。缺点：对指代、反讽和复杂否定弱。它的价值是提供成本下限和 sanity check：如果昂贵模型只比它好一点，说明额外成本没有买到足够价值。

### 5.2 本地态度模型：更新后的候选优先级

| 优先级 | 候选 | 用法 | 判断 |
|---|---|---|---|
| P1 成熟分类档 | `Qwen3-1.7B-Base` + `Qwen3ForSequenceClassification` | 对 `[产品信息, 帖子标题, 父评论, 当前评论]` 训练类别 logits | 纯文本、分类接口成熟；是第一轮本地主候选，必须用项目金标微调与校准 |
| P1 新架构分类档 | `Qwen3.5-2B-Base` + `Qwen3_5TextForSequenceClassification` | 与 1.7B 使用同一训练／冻结测试集 | 201 种语言／方言的新架构候选；Transformers 支持较新，需先验证显存、加载与吞吐 |
| P1 教师／质量上限 | `Qwen3.5-4B` non-thinking | 枚举输出；处理分类器低置信、反讽和上下文依赖样本 | 不默认跑全量；作为教师、难例裁决及质量上限 |
| P2 极限吞吐档 | `Qwen3-0.6B-Base` + 分类头 | 蒸馏 1.7B/2B/4B 的已复核标签 | 只在质量过线后采用；不作为第一轮“更好模型”默认答案 |
| P2 | `Erlangshen-Roberta-110M-Sentiment` | 只跑开箱中文情感 smoke test | 110M、Apache-2.0；通用情感训练集与 ETF 产品态度不一致，不能直接上线 |
| Baseline | XLM-RoBERTa / Chinese MacBERT | 复现既有结果，保留错误样本作对照 | 项目已反馈漏识别严重，不再作为生产主候选；先排查是否根本没有正确微调或输入被截断 |

生产上建议训练**两个模型或两个 head**：相关性和态度分开。这样能分别调阈值，也能清楚知道损失发生在哪一层。第一轮主比较是 [Qwen3-1.7B-Base](https://huggingface.co/Qwen/Qwen3-1.7B-Base) 的成熟纯文本分类头与 [Qwen3.5-2B-Base](https://huggingface.co/Qwen/Qwen3.5-2B-Base) 的新分类头；[Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) 仅作生成式教师／质量上限。Qwen3.5 官方列出 201 种语言／方言和 262K 原生上下文，但没有港式 ETF 评论的专项结果；纯文本部署需验证 `Qwen3_5TextForSequenceClassification` 的加载与实际显存。`Qwen3-0.6B-Base` 仅作为后续吞吐／蒸馏候选。

### 5.3 Embedding／Reranker：相关性层候选，不是情感模型

Qwen3 Reranker／Embedding、BGE-M3 和对应 reranker 适合计算“评论与指定产品／产品属性定义是否语义相关”。Qwen3 Reranker 官方支持自定义 classification instruction，可以直接用 yes/no 分数做相关性排序；其 0.6B、4B、8B 版本均支持 100+ 语言和 32K 上下文。官方自测中，4B 的 C-MTEB reranking 为 75.94，0.6B 为 71.31；因此本项目把 4B 作为质量档、0.6B 作为吞吐档，而不是只挑最小模型。[Qwen3 Reranker 4B 模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-4B)

推荐用途：

- embedding 先召回，阈值留宽；
- reranker 对边界样本精排；
- 用标注数据训练一个轻量分类头；
- 聚类近重复表达，辅助人工抽样。

不要直接把 cosine similarity 当态度；“跟踪误差很大”和“跟踪误差控制得好”在语义上都高度相关，极性却相反。Embedding/Reranker 负责“是否相关”，态度仍由监督分类模型或 LLM 判断。

### 5.4 DeepSeek V4：当前最值得测的托管 LLM

截至 2026-09-10，DeepSeek 官方列出 `deepseek-v4-flash`、`deepseek-v4-pro` 和实验性视觉模型；Flash 与 Pro 均支持 non-thinking／thinking、JSON Output，context length 为 1M。官方还显示 Flash 账户默认并发上限 2500、Pro 为 500。[定价与模型](https://api-docs.deepseek.com/quick_start/pricing/)、[并发限制](https://api-docs.deepseek.com/quick_start/rate_limit/)

对本任务的建议：

- 批量三分类使用 `deepseek-v4-flash` 且显式 `thinking: disabled`；这是窄分类，不值得为每条评论支付思维链输出与延迟。
- `deepseek-v4-pro` 只处理 Flash 与本地分类器冲突、反讽、比较、多实体等难例，也可用于抽样审计。
- 使用 JSON Output，但要保留 schema 校验、空响应重试和逐条 ID 对齐。官方明确提示 JSON Output 偶尔可能返回空内容。[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- 将固定 system prompt 和标签定义放在请求前缀；DeepSeek 上下文缓存默认开启，但命中是 best-effort，不能把缓存折扣写成预算保证。[Context Caching](https://api-docs.deepseek.com/guides/kv_cache/)
- 本次未在 DeepSeek 官方文档中找到异步离线 Batch API；需要自行用任务队列控制并发，不能把阿里云的 Batch 半价套到 DeepSeek 直连价格上。

### 5.5 阿里云百炼 Qwen：价格非常有竞争力的中文候选

阿里云百炼官方价格页在调查日列出以下**华北 2（北京）**价格：

- `qwen3.8-flash`：单次输入不超过 1M tokens 时，输入 ¥0.8／百万 tokens、输出 ¥2.7／百万 tokens；支持非思考与思考模式。
- `qwen3.7-flash`：单次输入不超过 32K tokens 时，输入 ¥0.2／百万 tokens、输出 ¥0.8／百万 tokens；支持非思考与思考模式、JSON Schema，并明确标注 Batch 调用半价。
- `qwen3.7-text-embedding-flash`：输入 ¥0.125／百万 tokens，Batch 半价。
- `qwen3.7-text-rerank`：文本输入 ¥0.5／百万 tokens。

来源：[阿里云百炼模型调用价格](https://help.aliyun.com/zh/model-studio/model-pricing)。价格表还说明 Batch 的输入和输出均按实时推理价格的 50% 计费，但 Batch 与上下文缓存折扣不能同时生效。

对本项目，`qwen3.7-flash` 应作为最低成本托管 LLM 主候选，`qwen3.8-flash` 用来检验新一代模型是否带来足以覆盖价格差的质量提升。不要仅因“中文厂商模型”就假定中文金融评论一定更准；依然使用同一金标集实测。

香港／新加坡部署的币种、价格、模型可用性及数据部署范围可能不同，不能把北京价直接当成最终采购报价；正式选区前应在[国际站价格页](https://www.alibabacloud.com/help/en/model-studio/model-pricing)重新核对。

### 5.6 现有 Claude Haiku 4.5 决策

仓库 ADR 已将 `claude-haiku-4-5-20251001` 设为第一期默认，理由是量大、文本短、需要结构化输出，且安排更强模型抽样复核（[ADR-0010](../adr/0010-annotations-and-ai-pipeline.md)）。这应当被视作**待验证 baseline**，而不是已经证明的生产赢家。

最公平的做法是让 Haiku 4.5、DeepSeek V4 Flash、本地 encoder 在完全相同的隐藏测试集上比较；模型名和供应商品牌不参与评分。

### 5.7 OpenAI 模型

OpenAI 可作为托管模型质量／吞吐基准，但本次调查环境无法实际打开官方 OpenAI 定价和模型页面（403／Cloudflare），因此本文不引用搜索摘要，也不写未经核实的型号价格。内置的非权威选型快照将 GPT-5.6 Luna 描述为高吞吐方向，但它不是当前价格、可用性或 API 行为的证据；若进入正式候选，必须在采购前从官方 OpenAI documentation 重新核验并跑同一评测。

## 6. 托管模型的量级成本示例

官方价格单位是每 1M tokens；非高峰价是高峰价的一半。调查日价格如下：[官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)

| 模型 | 输入 cache miss（非高峰／高峰） | 输出（非高峰／高峰） |
|---|---:|---:|
| V4 Flash | $0.22 / $0.44 | $0.66 / $1.32 |
| V4 Pro | $0.66 / $1.32 | $1.98 / $3.96 |

统一公式：

```text
总成本 = 未缓存输入 token / 1M × input_miss_price
       + 缓存命中输入 token / 1M × input_hit_price
       + 输出 token / 1M × output_price
```

示例假设：100 万条评论；平均每条连同必要上下文有 160 个未缓存输入 token；紧凑 JSON 平均 20 个输出 token。固定 prompt 的缓存收益暂不计入，以免高估折扣。

| 模型 | 非高峰估算 | 高峰估算 |
|---|---:|---:|
| V4 Flash | `160×0.22 + 20×0.66 = $48.40` | `$96.80` |
| V4 Pro | `160×0.66 + 20×1.98 = $145.20` | `$290.40` |

这张表不是项目预算，最大的未知数是**真实平均 token 数、上下文升级比例、重试率和相关评论保留率**。导入真实瘦库后，应先随机抽 10,000 条跑 tokenizer，预算才会可靠。

同样假设下，按华北 2（北京）价，`qwen3.7-flash` 的实时推理示例为：

```text
160 × ¥0.2 + 20 × ¥0.8 = ¥48 / 100 万条评论
```

若使用官方 Batch 且满足适用条件，标价为实时推理的 50%，即示例约 ¥24。`qwen3.8-flash` 同样输入输出规模约为 `160 × ¥0.8 + 20 × ¥2.7 = ¥182`。这里没有计入失败重试、额外上下文或活动优惠；不同厂商 tokenizer 也不同，所以不能直接拿同一个 token 假设当最终采购报价。

### 6.1 “一起打包”应该怎样做

把多条评论放在一次请求里属于 micro-batching，建议以 token 而不是固定条数切包：

- 每包先从 20–50 条或约 4k–8k 输入 token 开始压测；
- 每条带不可变的 `item_id`；同一 `(comment_id, product_code)` 只出现一次；
- 输出必须是与输入 ID 一一对应的 JSON array；
- schema 校验失败时先修复／重试整包，连续失败则拆半重试；
- 同一包尽量使用相同产品上下文，利于前缀缓存；
- 不要为了省 prompt token 打包数千条，否则一次截断或格式错误会放大重试成本，也更容易出现条目间串扰。

Micro-batching 主要节省固定指令和请求开销，不会消除评论文本本身的 token 成本。

## 7. 建议的 LLM 输入输出协议

### 7.1 最小输入

```json
{
  "items": [
    {
      "item_id": "comment:123|product:3033",
      "product_code": "3033",
      "product_name": "南方恒生科技",
      "comment": "跌下来正好继续加这只",
      "post_context": "可选；只在需要时提供",
      "parent_comment": "可选；只在回复语义不完整时提供"
    }
  ]
}
```

### 7.2 最小输出

```json
{
  "items": [
    {
      "item_id": "comment:123|product:3033",
      "relevance": "relevant",
      "attitude": "positive",
      "evidence": "继续加这只",
      "needs_review": false,
      "reason_code": "explicit_product_action"
    }
  ]
}
```

约束：

- `evidence` 必须是输入原文中的连续片段，不能让模型生成新的解释性事实；
- 无关时 `attitude = null`；
- 既有积极又有消极且无法确定主导态度时，`needs_review = true`，不要强塞进 neutral；
- LLM 自报 `confidence` 只用于排队，不当作真实概率。真正的 0.7 阈值必须通过金标集校准；
- 固定记录 `model_id`、模型版本、prompt 版本、标签规范版本、原始响应 hash 和重试次数。

## 8. 评测设计：怎样真正决定“用哪个模型”

### 8.1 先做金标，不先买模型

建议建立约 3,000 条的第一版金标集：

- 一半从真实分布随机抽样，反映总体成本和准确率；
- 一半刻意覆盖难例：繁简混合、英文缩写、纯价格观点、产品机制、比较句、否定、反讽、极短回复、多产品、父评论依赖、热门截断帖子；
- 至少两名标注者独立标注，冲突由第三人／产品负责人裁决；
- 按帖子、产品和时间切 train/dev/test，不能把同一线程的近重复句分到训练和测试两边；
- 测试集冻结，模型和 prompt 调参者不可看到答案。

标注顺序也应是两步：先相关性，再对相关样本标态度。另加 `needs_context` 和 `ambiguous` 供误差分析，不强迫所有样本都有三分类答案。

### 8.2 不能只看 accuracy

| 层 | 主指标 | 原因 |
|---|---|---|
| 相关性过滤 | relevant recall、irrelevant precision | 错删相关评论后续无法恢复 |
| 态度分类 | macro-F1、各类 precision/recall | 类别通常不均衡，accuracy 会被多数类掩盖 |
| 负面识别 | negative recall 与 precision | 漏报和误报的业务代价不同 |
| 置信度 | reliability curve、ECE/Brier、coverage-accuracy | 决定哪些样本能自动通过、哪些升级 LLM／人工 |
| 工程 | schema 成功率、重试率、p50/p95 延迟、吞吐 | 结构化输出不稳会直接拉高真实成本 |
| 成本 | 每 1 万条总成本、每 1 万条“正确自动判定”成本 | 便宜但大量转人工不一定便宜 |
| 业务结果 | 产品级积极／消极比例误差及排名稳定性 | 评论级小误差可能在聚合后放大 |

可以先提出内部验收目标，例如相关评论召回不低于 98%，但这只是待产品确认的工程门槛，不应包装成行业标准。

### 8.3 第一轮实验矩阵

| 编号 | 相关性 | 态度 | 目的 |
|---|---|---|---|
| A | TF-IDF + Linear SVM | TF-IDF + Linear SVM | 成本／速度下限 |
| B | 复现旧 XLM-R／MacBERT | 复现旧 XLM-R／MacBERT | 固化失败基线和错误样本，不再默认主选 |
| C | Qwen3 Reranker 0.6B／BGE reranker v2 m3 | Qwen3-1.7B-Base + sequence classification head | 可部署的成熟纯文本主候选 |
| C2 | Qwen3 Reranker 4B | Qwen3.5-2B-Base + text sequence classification head | 相关性质量档与新架构分类候选 |
| C3 | 与 C/C2 相同 | Qwen3.5-4B non-thinking | 教师、难例裁决和本地质量上限 |
| D | DeepSeek V4 Flash，non-thinking | 同一次或第二次调用 | DeepSeek 低成本基线 |
| E | Qwen 3.7/3.8 Flash，non-thinking | 同一次或第二次调用 | 中文托管模型及超低成本 Batch 基线 |
| F | DeepSeek V4 Pro | DeepSeek V4 Pro | 质量上限／裁决候选 |
| G | 与 D/E 相同输入 | Claude Haiku 4.5 | 对照现有 ADR 默认值 |

所有候选使用同一隐藏测试集；记录原始响应和 token usage。不要用一个 LLM 生成的标签，再让同一个 LLM 当裁判。

## 9. 推荐落地路线

### 第 0 周：先修定义和数据结构

- 冻结 `relevance`、`attitude`、`needs_context` 的标注指南与正反例；
- 确认判定单位为 `(comment_id, product_code)`；
- 给 `annotations` 增加产品作用域与版本字段；
- 导入瘦库并测真实评论量、平均长度、截断覆盖率和产品分布。

### 第 1–2 周：金标和离线 benchmark

- 完成 3,000 条双人标注；
- 跑 A–G 候选（含 C2）；
- 输出 confusion matrix、错误类型、置信度曲线、吞吐和每万条成本；
- 根据业务对漏掉负面与误报负面的代价选择阈值。

### 第 3 周：影子运行

- 先不影响正式页面，后台跑一周；
- 每日抽查高置信、低置信和模型冲突样本；
- 检查繁简、热门帖子、不同产品和不同时间段是否发生漂移；
- 评估本地模型自动通过率，以及需交给 V4 Flash／人工的比例。

### 稳定后：蒸馏与主动学习

- 人工金标作为真值；LLM 可以扩充训练集，但不能未经抽检直接当真值；
- 优先标注本地模型最不确定、模型间分歧最大和新词最多的评论；
- 定期重训本地 encoder，让托管 LLM 调用比例逐步下降；
- 保留按模型版本重跑的能力，绝不覆盖旧标注而不留版本。

## 10. 风险与当前项目中的前置阻塞

1. **标注 worker 尚未实现。** 当前 scheduler 只有 heartbeat；schema 注释也写着“标注作业待评论量确认后再跑”。因此模型选型应先做离线 benchmark，不应误称已经接入生产。
2. **评论数据存在截断偏差。** 3.5% 的帖子 `has_more=1`，而这些通常是热帖；被截断评论占总评论量的比例仍待真实导入验证。见 [ADR-0011](../adr/0011-comment-volume-caliber.md)。
3. **一条评论可对应多产品，但 annotation schema 暂无产品维度。** 这是跑模型前必须处理的契约问题。
4. **真实数据包含用户信息。** 调用外部 API 时只发送匿名 item ID、必要产品上下文和评论正文；不要发送昵称、用户 ID、IP 归属地或个人简介。正式采购还需核对供应商的数据留存、训练使用、区域和删除政策。
5. **模型置信度不可直接等同正确率。** 当前 `lowConfidence=0.7` 只能在校准后使用；LLM 的自报分数尤其不能直接当概率。
6. **供应商与价格会变化。** `annotations.model` 已能记录模型，但还应记录精确版本和 prompt／taxonomy 版本，确保结果可解释、可重跑。

## 11. 最终建议

不要把项目定义成“挑一个情感模型”。应定义成：

> 在不漏掉与 ETF 产品有关评论的前提下，以最低的每条可靠标签成本，完成评论—产品级相关性与态度判断，并能追溯到原文证据。

在此定义下，最合理的当前决策是：

1. 接受 mentor 的“先粗筛、再批量送 AI”方向；
2. 将“词频筛选”降级为 baseline／噪声发现，不让它直接删除边界内容；
3. 立即以 DeepSeek V4 Flash 和 Qwen 3.7 Flash non-thinking 做低成本 LLM baseline；
4. 训练 Qwen3-1.7B 与 Qwen3.5-2B 的序列分类头；相关性比较 Qwen3 Reranker 0.6B、4B 与 BGE reranker，Qwen3.5-4B 只作教师／难例上限，XLM-R／MacBERT 只复现旧失败结果；
5. 用 V4 Pro 与 Claude Haiku 4.5 做小规模质量对照；
6. 以 3,000 条双人金标的 macro-F1、负面召回、相关评论召回、校准、吞吐和真实成本决定生产组合；
7. 最终以“本地模型处理大多数 + V4 Flash 裁决少数 + 人工抽检”作为目标架构。

外部候选和一手来源的逐项核查另见 [sentiment-model-selection-sources.md](sentiment-model-selection-sources.md)；本地 NLP 的专项对比、实验设计和输入模板见 [better-nlp-models.md](better-nlp-models.md)。
