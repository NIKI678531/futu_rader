# ETF 评论相关性／态度分析：一手来源核查与候选清单

> 核查日期：2026-09-10（Asia/Hong_Kong）  
> 目标：为富途 ETF 中文／繁简评论的 `relevant / irrelevant / needs_context` 与产品态度 `positive / neutral / negative` 选择高吞吐、成本敏感的模型和处理管线。  
> 说明：价格、模型别名和地域可用性会变化；生产采购前应重新打开官方页面核价，并固定实际模型快照 ID。

## 1. 结论

不建议寻找一个“万能情感模型”直接处理全部评论。应把问题拆成两层：

1. **相关性**：评论是否在评价指定 ETF 产品，而不是只谈指数、价格涨跌、其他股票或闲聊；
2. **产品态度**：仅对相关评论判积极／中性／消极。

推荐的候选组合是：

| 位置 | 首轮 shortlist | 为什么进入测试 | 不能直接假设的事 |
|---|---|---|---|
| 便宜的托管 LLM | `qwen3.7-flash`、`qwen-flash` | 中文、结构化输出；前者支持严格 JSON Schema；百炼官方 Batch 可半价 | 不能因中文能力或价格就假设 ETF 标签准确 |
| 另一家托管基线 | `deepseek-v4-flash`（关闭 thinking） | 官方当前 API、JSON Output、1M context、较高并发 | DeepSeek 官方当前未列出离线 Batch API；JSON 模式偶尔可能空响应 |
| 质量裁决 | `deepseek-v4-pro`，以及项目已定的 Claude Haiku 4.5 | 只用于难例、冲突样本和抽样审计 | 不应未经同一金标集比较就跑全量 |
| 本地相关性 | `Qwen3-Embedding-0.6B` + `Qwen3-Reranker-0.6B`，或 `bge-m3` + `bge-reranker-v2-m3` | 多语、可自部署、适合语义召回／重排 | embedding 相似度不是情感极性 |
| 本地态度分类 | `Qwen3-1.7B-Base` + `Qwen3ForSequenceClassification`；`Qwen3.5-2B-Base` + `Qwen3_5TextForSequenceClassification` | 真正输出类别 logits 的成熟路线与新架构路线 | 仍须项目金标；原有 XLM-R/MacBERT 已实测漏识别严重，只保留失败基线 |
| 本地教师／难例 | `Qwen3.5-4B` non-thinking | 枚举输出，建立质量上限并裁决低置信难例 | 不默认全量运行；生成式输出仍需 schema 校验 |
| 开箱情感 baseline | `Erlangshen-Roberta-110M-Sentiment` | 中文、110M、Apache-2.0，便于快速建立下限 | 训练标签来自通用中文情感数据，不等于 ETF 产品态度 |

最终选型必须以本项目金标集决定。建议目标架构为：**规则／结构化字段保底召回 → 本地相关性模型 → 本地态度模型 → 低置信和上下文依赖样本交给 Flash LLM → 人工抽检和主动学习**。

## 2. 为什么“按词频找无关评论，再一起扔给 AI”只能算起点

### 2.1 词频能做的事

- 识别完全重复、近重复、模板广告和高频灌水短语；
- 建立 TF-IDF／BM25 的廉价、可解释 baseline；
- 按高频词和长尾词分层抽样，帮助建立评测集；
- 在评论级标签完成后统计观点主题。

### 2.2 词频不能安全决定删除

- “溢价这么离谱还怎么买”可能低频但明确针对产品；
- “恒指今天要跌”可能高频但只表达市场方向；
- “跌下来继续加这只”包含负面表面词，却可能是产品积极；
- 繁简体、港式表达、简称、代词、否定和反讽会破坏简单词表。

因此低分评论不应物理删除，而应保留原文并写入 `relevance=irrelevant` 或 `needs_context`。相关性层要优先保证 relevant recall；误删后，后续强模型无法挽回。

## 3. 商业 API：已核实能力与价格

### 3.1 DeepSeek 官方 API

DeepSeek V4 不能凭传闻写入方案，但在本次核查日已有官方一手证据：官方 2026-04-24 的 V4 Preview 公告说明 `deepseek-v4-pro`、`deepseek-v4-flash` 已上线 API；2026-08-13 又发布 V4 Pro GA。因此，截至 2026-09-10 它不是“未发布型号”。若文档要表达更早时点，必须按那个时点重新核查历史资料。

- [DeepSeek V4 Preview 官方公告（2026-04-24）](https://api-docs.deepseek.com/news/news260424/)
- [DeepSeek V4 Pro GA 官方公告（2026-08-13）](https://api-docs.deepseek.com/news/news260813/)
- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)

当前官方价格页列出：

| 模型 | context / 最大输出 | 输入 cache hit | 输入 cache miss | 输出 | 默认账户并发 |
|---|---:|---:|---:|---:|---:|
| `deepseek-v4-flash` | 1M / 384K | 非高峰 $0.007；高峰 $0.014 | 非高峰 $0.22；高峰 $0.44 | 非高峰 $0.66；高峰 $1.32 | 2500 |
| `deepseek-v4-pro` | 1M / 384K | 非高峰 $0.022；高峰 $0.044 | 非高峰 $0.66；高峰 $1.32 | 非高峰 $1.98；高峰 $3.96 | 500 |

价格单位均为每 100 万 tokens；官方定义工作日 01:00–04:00、06:00–10:00 UTC 为高峰，其余为非高峰。并发数字另由 [Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/) 说明。

工程相关能力：

- 官方 [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 说明 thinking 默认开启，可用 `{"thinking":{"type":"disabled"}}` 关闭。窄分类任务应先测 non-thinking，避免无必要的延迟和 reasoning 输出。
- 官方 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 支持 `response_format={"type":"json_object"}`，但明确提示偶尔可能返回空内容，所以仍需 schema 校验、ID 完整性检查和重试。
- 官方 [Context Caching](https://api-docs.deepseek.com/guides/kv_cache/) 说明磁盘缓存默认开启；命中依赖完全相同的已持久化前缀，属于 best-effort，通常数小时至数天后清除。响应中的 `prompt_cache_hit_tokens` 与 `prompt_cache_miss_tokens` 可用于实测。
- 截至核查日，官方文档导航和 API 指南中**未找到离线 Batch API**。这只能表述为“当前没有查到官方 Batch 能力”，不能把阿里云的 Batch 半价套用到 DeepSeek 直连 API。

适合本项目的定位：`deepseek-v4-flash` 做 LLM 基线／疑难路由，`deepseek-v4-pro` 做少量上限对照。不要用 1M context 作为一次塞入数千条评论的理由；长包会扩大格式失败、截断和条目串扰的损失。

### 3.2 阿里云百炼 Qwen

以下是华北 2（北京）价格，来自 [阿里云百炼模型调用价格](https://help.aliyun.com/zh/model-studio/model-pricing)：

| 模型 | 短输入档实时价格（输入／输出，每百万 tokens） | Batch | 备注 |
|---|---:|---:|---|
| `qwen-flash` | ¥0.15 / ¥1.50（输入 ≤128K） | 半价 | 当前等同 `qwen-flash-2025-07-28`；JSON Object 要用非思考模式 |
| `qwen3.7-flash` | ¥0.20 / ¥0.80（输入 ≤32K） | 半价 | 当前等同 `qwen3.7-flash-2026-07-15`；支持 JSON Schema |
| `qwen3.8-flash` | ¥0.80 / ¥2.70（输入 ≤1M） | 价格页标注以实时／所在地域为准 | 更贵的新代候选，只在质量提升足够时采用 |
| `qwen3.7-text-embedding-flash` | ¥0.125 输入，输出不收费 | 半价 | 托管相关性召回候选 |
| `text-embedding-v4` | ¥0.50 输入，输出不收费 | 半价 | 通用托管 embedding 对照 |
| `qwen3.7-text-rerank` | ¥0.50 输入，输出不收费 | 未见半价标记 | 托管相关性重排候选 |

地域会改变币种、价格、模型名、数据部署范围和 Batch 可用性。面向香港的部署决策应再对照同一官方价格页的 Hong Kong／Singapore 表，不要把北京价直接当采购报价。英文版官方 [Model pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing) 也分别列出了 Singapore、China (Beijing)、Hong Kong (China) 等地域。

批处理与结构化输出：

- [OpenAI 兼容 Batch（文件输入）](https://help.aliyun.com/zh/model-studio/batch-interfaces-compatible-with-openai) 明确说明异步文件批量请求按实时调用的 50% 计费，适合评测和非实时大批量分析；该页列出北京地域支持的 Qwen Flash、Plus、Max、部分 DeepSeek 和 embedding 型号。
- 价格页明确说明 **Batch 与上下文缓存折扣不能同时生效**。
- [结构化输出](https://help.aliyun.com/zh/model-studio/json-mode) 区分 JSON Object（保证有效 JSON，但不保证特定结构）和 JSON Schema（可严格约束字段）。当前支持列表含 Qwen3.7/3.8 Flash 的 JSON Schema；旧 `qwen-flash` 应在非思考模式使用 JSON Object。
- [上下文缓存](https://help.aliyun.com/zh/model-studio/context-cache) 说明显式缓存通常按标准输入价 125% 创建、命中通常为 10%，5 分钟有效；隐式缓存自动工作，命中通常为 20%，但命中不确定。两类缓存最少 1024 tokens，且互斥。

对短评论，`qwen3.7-flash` 值得优先进入成本基线：它的输出价比旧 `qwen-flash` 低，且严格 schema 能减少重试。不过两者谁更省，仍取决于真实输入／输出 token 比例和错误重试率。

### 3.3 现有 Claude Haiku 4.5

仓库已经在 [ADR-0010](../adr/0010-annotations-and-ai-pipeline.md) 将 `claude-haiku-4-5-20251001` 设为第一期默认。这个决定应保留为对照组，而不是当作已经验证的赢家。本次来源核查没有取得稳定可解析的 Anthropic 官方价格正文，因此不在此转述二手价格；正式比较时应从 [Anthropic 官方定价页](https://platform.claude.com/docs/en/about-claude/pricing) 重新取数。

## 4. 可自部署模型：模型卡、用途和许可证

### 4.1 本地分类器候选

| 模型 | 官方资料核实 | 建议用途 | 许可证／注意事项 |
|---|---|---|---|
| `Qwen3-1.7B-Base` | [模型卡](https://huggingface.co/Qwen/Qwen3-1.7B-Base)；[Transformers 分类接口](https://huggingface.co/docs/transformers/model_doc/qwen3#transformers.Qwen3ForSequenceClassification) | 以项目金标训练成熟纯文本 sequence-classification head，作为主分类器 | Apache-2.0；公开通用能力不等于港式 ETF 态度成绩 |
| `Qwen3.5-2B-Base` | [模型卡](https://huggingface.co/Qwen/Qwen3.5-2B-Base)；[Transformers 分类接口](https://huggingface.co/docs/transformers/main/en/model_doc/qwen3_5#transformers.Qwen3_5TextForSequenceClassification) | 以同一金标训练纯文本分类头，与 1.7B 正面对比 | Apache-2.0；分类接口与推理栈较新，需实测加载、显存和吞吐 |
| `Qwen3.5-4B` | [模型卡](https://huggingface.co/Qwen/Qwen3.5-4B) | non-thinking 固定标签输出，作为教师／难例裁决和本地质量上限 | Apache-2.0；201 种语言／方言，但没有港式 ETF 态度专项结果 |
| `hfl/chinese-macbert-base` | [模型卡](https://huggingface.co/hfl/chinese-macbert-base)；[MacBERT 官方仓库](https://github.com/ymcui/MacBERT) | 只复现旧结果、形成失败对照 | 项目已反馈漏识别严重；Apache-2.0 |
| `FacebookAI/xlm-roberta-base` | [模型卡](https://huggingface.co/FacebookAI/xlm-roberta-base)；[论文](https://arxiv.org/abs/1911.02116) | 只复现旧结果、形成失败对照 | 项目已反馈漏识别严重；MIT |
| `ckiplab/bert-base-chinese` | [CKIP 官方模型卡](https://huggingface.co/ckiplab/bert-base-chinese) | 专门验证繁体中文 slice | GPL-3.0；商用／分发前需法务确认；它是 MLM 基座，不是情感成品 |
| `IDEA-CCNL/Erlangshen-Roberta-110M-Sentiment` | [官方模型卡](https://huggingface.co/IDEA-CCNL/Erlangshen-Roberta-110M-Sentiment) | 开箱中文情感 baseline、弱标注候选 | Apache-2.0；官方称在 8 个中文情感数据集、227,347 样本上微调，但标签口径不是 ETF 产品态度 |

`ProsusAI/finbert` 不应进入中文生产 shortlist。其 [官方模型卡](https://huggingface.co/ProsusAI/finbert) 明确标为英语，并使用 Financial PhraseBank 做三分类；“金融”领域相近不等于语言和任务口径相符。可把它列入“已排除候选”，避免团队以后因名字再次误选。

### 4.2 Embedding／Reranker 候选

| 模型 | 官方声明 | 许可证 | 在本项目的位置 |
|---|---|---|---|
| `Qwen3-Embedding-0.6B` | [模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) 列出 0.6B、100+ 语言、32K context、最高 1024 维、instruction-aware | Apache-2.0 | 评论与“指定 ETF／产品属性定义”的高召回语义匹配 |
| `Qwen3-Reranker-0.6B` | [模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) 列出 100+ 语言、32K context、instruction-aware | Apache-2.0 | 对 embedding 边界候选做 cross-encoder 重排 |
| `BAAI/bge-m3` | [模型卡](https://huggingface.co/BAAI/bge-m3) 列出 100+ 语言、8192 tokens、1024 维，并统一 dense／sparse／multi-vector | MIT | 语义召回、聚类近重复、与 BM25 混合召回 |
| `BAAI/bge-reranker-v2-m3` | [模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3) 称其为轻量、多语、易部署的 reranker | Apache-2.0 | 相关性重排；需要以本项目 hard negatives 调阈值 |

这些模型解决的是“语义上是否相关／哪个候选更相关”，不是态度极性。“跟踪误差太大”和“跟踪误差控制得很好”可能 embedding 很近但态度相反。

### 4.3 硬件估算边界

官方模型卡给出了参数量，但没有替本项目保证吞吐。可先用权重下限做容量估算：

```text
BF16/FP16 权重约 = 参数量 × 2 bytes
INT8 权重约       = 参数量 × 1 byte
实际显存          = 权重 + KV/activations + 框架开销 + batch/sequence headroom
训练显存          >> 推理显存（还包括梯度与优化器状态）
```

例如 Qwen 0.6B 的权重下限约为 1.2 GB（BF16）或 0.6 GB（INT8），4B 的 BF16 权重下限约 8 GB；这**不代表**相同显存即可实际服务，仍需给 activations、框架、batch 和训练状态留余量。鉴于旧模型已经漏识别严重，本轮先用 4B 建立质量上限，再判断能否蒸馏到 0.6B；不要继续默认“最小模型优先”。

## 5. 建议的判定单元与输出协议

判定单元必须是 `(comment_id, product_code)`，不是只给评论一个全局情绪。比较句可能对产品 A 积极、对产品 B 消极。

建议每个 LLM micro-batch 按 token 上限装入约 20–50 条短评论，每条携带稳定 ID；出现代词或回复关系时才补帖子标题／父评论。输出示例：

```json
{
  "items": [
    {
      "item_id": "comment:123|product:3033.HK",
      "relevance": "relevant",
      "attitude": "negative",
      "evidence": "跟踪误差太大",
      "reason_code": "tracking_error",
      "needs_review": false
    }
  ]
}
```

验证规则：

- 输出 ID 集必须与输入一致，不得少项、重复或多项；
- `irrelevant` 时 `attitude=null`；`neutral` 不能充当“无关”；
- `evidence` 必须是输入原文的连续片段；
- JSON/schema 失败先重试，连续失败则拆包；
- 记录供应商、精确模型快照、prompt 版本、taxonomy 版本、token usage、原始响应 hash、重试次数；
- 模型自报 confidence 只用于排序，不能直接与系统 `lowConfidence=0.7` 等同。

“一次请求装多条评论”的 micro-batching 与供应商的异步 Batch API 是两件事：前者省固定 prompt／HTTP 开销，后者可能有价格折扣但延迟更长。二者都必须单独压测格式错误率与条目串扰。

## 6. 成本公式与可复算示例

设：

- `N`：原始评论数；
- `r`：进入 LLM 的比例（全量基线时为 1，级联方案中通常小于 1）；
- `b`：每个 micro-batch 的评论数；
- `P`：每个请求的固定 prompt tokens；
- `X`：每条评论及必要上下文的平均输入 tokens；
- `Y`：每条评论的平均输出 tokens；
- `R`：重试倍率，例如失败／重试 2% 则为 1.02；
- `h`：输入 token 的实测 cache-hit 比例；
- `d`：Batch 折扣系数，实时为 1，阿里云合资格 Batch 为 0.5。

```text
N_llm = N × r
T_in  = R × (N_llm × X + ceil(N_llm / b) × P)
T_out = R × N_llm × Y

无缓存：C = d × (T_in × P_in + T_out × P_out) / 1,000,000
有缓存：C = ((1-h) × T_in × P_miss + h × T_in × P_hit
            + T_out × P_out) / 1,000,000
```

阿里云文档明确说 Batch 和缓存折扣不能同时生效，所以不能同时代入 `d=0.5` 和缓存价。

### 仅用于量级感知的例子

假设 100 万条评论，每条连同分摊后的固定 prompt 平均 160 输入 tokens、20 输出 tokens，忽略重试和缓存：

| 模型／模式 | 估算 |
|---|---:|
| DeepSeek V4 Flash 直连、非高峰 | `160×$0.22 + 20×$0.66 = $48.40` |
| DeepSeek V4 Flash 直连、高峰 | `$96.80` |
| DeepSeek V4 Pro 直连、非高峰 | `160×$0.66 + 20×$1.98 = $145.20` |
| Qwen 3.7 Flash 北京实时 | `160×¥0.20 + 20×¥0.80 = ¥48.00` |
| Qwen 3.7 Flash 北京 Batch | `¥24.00` |
| Qwen Flash 北京实时 | `160×¥0.15 + 20×¥1.50 = ¥54.00` |

这不是预算报价：各 tokenizer 不同，真实 `r/X/Y/R/h` 未知。先从瘦库随机抽 10,000 条，以每家官方 tokenizer 或 API usage 实测，再报告“每万条正确自动判定”的成本。

自部署成本应写为：

```text
C_local = 标注与训练的一次性成本
        + GPU/CPU 小时 × 单价
        + 存储、监控、重训和运维成本

break_even_N = 一次性成本 / (API 每条边际成本 - 本地每条边际成本)
```

API 单价已经很低时，自部署的主要理由往往是数据治理、可控性、稳定输出和长期主动学习，而不只是 token 费。

## 7. 评测设计

### 7.1 金标集

首版建议 2,000–3,000 个 `(comment, product)` 判定单元：

- 50% 按真实流量随机抽样；
- 50% 定向覆盖 hard cases：繁体／简体／混合、港式表达、英文缩写、纯市场涨跌、费用／流动性／跟踪误差、否定、反讽、比较、多产品、极短回复、依赖父评论；
- 两名标注者独立标注，冲突由第三人或产品负责人裁决；
- 按帖子线程、产品和时间切 train/dev/test，防止近重复泄漏；
- 冻结 test，不能让生成标签的同一 LLM 同时担任裁判。

### 7.2 指标与闸门

| 层 | 必看指标 |
|---|---|
| 相关性 | relevant recall、irrelevant precision、hard-negative slice |
| 态度 | macro-F1、每类 precision/recall、negative recall/precision |
| 繁简鲁棒性 | 简体／繁体／混合／粤语表达分片指标 |
| 校准 | reliability curve、ECE／Brier、coverage-vs-error |
| 工程 | JSON/schema 成功率、漏 ID／重复 ID、重试率、p50/p95、吞吐 |
| 成本 | 每万条总成本、每万条“正确自动通过”成本、人工复核率 |
| 聚合 | 产品级正负比例误差、低样本产品误差和排名稳定性 |

神经模型的 softmax 往往没有天然校准；可参考 Guo 等人的 [On Calibration of Modern Neural Networks](https://proceedings.mlr.press/v70/guo17a.html)，在独立 validation set 上做 temperature scaling 或等价校准。LLM 自报的“0.8 信心”不是 80% 正确率。

### 7.3 首轮实验矩阵

| 组 | 相关性 | 态度 | 目的 |
|---|---|---|---|
| A | char n-gram TF-IDF + Logistic Regression | 同左 | 成本／速度／可解释下限 |
| B | 复现旧 MacBERT／XLM-R | 同左 | 固化失败基线和错误集 |
| C | Qwen3-Reranker-0.6B／BGE-reranker-v2-m3 | Qwen3-1.7B-Base + sequence classification head | 成熟纯文本主候选与实用相关性基线 |
| C2 | Qwen3-Reranker-4B | Qwen3.5-2B-Base + text sequence classification head | 相关性质量档与新架构分类候选 |
| C3 | 与 C/C2 相同 | Qwen3.5-4B non-thinking | 教师、难例裁决和本地质量上限 |
| D | Qwen 3.7 Flash non-thinking | 同一结构化调用 | 最低成本托管基线 |
| E | DeepSeek V4 Flash non-thinking | 同一结构化调用 | 跨供应商基线 |
| F | V4 Pro／Claude Haiku 4.5 | 同一结构化调用 | 难例和质量上限对照 |

上线前不预设哪个模型获胜。可先把相关性召回目标设得很高，再用可接受人工量选择阈值；具体阈值由业务确认，不应伪装成行业标准。

## 8. 推荐实施次序

1. 冻结标签规范，明确“市场看跌 ≠ 产品负面”“无关 ≠ 中性”。
2. 用 300–500 条试标发现定义冲突，再扩至 2,000–3,000 条金标。
3. 同时跑 TF-IDF、旧 MacBERT/XLM-R 复现、Qwen/BGE reranker、Qwen3-1.7B 与 Qwen3.5-2B 分类头，以及 Qwen3.5-4B／Qwen 3.7 Flash／DeepSeek V4 Flash 的教师或托管对照。
4. 以金标指标、真实 token usage、schema 失败率和人工复核率选主模型，不看品牌印象。
5. 第一版可让 Flash LLM 批量产出并由人工抽检；积累足够金标后训练本地 encoder。
6. 生产稳定后由本地模型自动处理高置信大头，Flash LLM 只吃低置信／上下文依赖样本，Pro／人工处理极少数冲突。
7. 任何过滤步骤都只写标签，不删除原始评论；保留版本化结果，支持换模型重跑。

## 9. 来源索引

### DeepSeek

- [V4 Preview Release](https://api-docs.deepseek.com/news/news260424/)
- [V4 Pro GA Release](https://api-docs.deepseek.com/news/news260813/)
- [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/)
- [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [Context Caching](https://api-docs.deepseek.com/guides/kv_cache/)

### 阿里云百炼

- [模型调用价格（中国站）](https://help.aliyun.com/zh/model-studio/model-pricing)
- [Model pricing（国际站，含地域差异）](https://www.alibabacloud.com/help/en/model-studio/model-pricing)
- [OpenAI 兼容 Batch（文件输入）](https://help.aliyun.com/zh/model-studio/batch-interfaces-compatible-with-openai)
- [结构化输出：JSON Object / JSON Schema](https://help.aliyun.com/zh/model-studio/json-mode)
- [上下文缓存](https://help.aliyun.com/zh/model-studio/context-cache)

### 开源模型／论文

- [Qwen3 Embedding 0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [Qwen3 Reranker 0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)
- [Qwen3 Reranker 4B](https://huggingface.co/Qwen/Qwen3-Reranker-4B)
- [Qwen3 1.7B Base](https://huggingface.co/Qwen/Qwen3-1.7B-Base) 与 [Transformers sequence classification](https://huggingface.co/docs/transformers/model_doc/qwen3#transformers.Qwen3ForSequenceClassification)
- [Qwen3.5 2B Base](https://huggingface.co/Qwen/Qwen3.5-2B-Base)
- [Qwen3.5 4B](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Transformers Qwen3.5 text sequence classification](https://huggingface.co/docs/transformers/main/en/model_doc/qwen3_5#transformers.Qwen3_5TextForSequenceClassification)
- [BGE-M3](https://huggingface.co/BAAI/bge-m3)
- [BGE Reranker v2 M3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [MacBERT](https://huggingface.co/hfl/chinese-macbert-base) 与 [官方仓库](https://github.com/ymcui/MacBERT)
- [XLM-RoBERTa base](https://huggingface.co/FacebookAI/xlm-roberta-base) 与 [论文](https://arxiv.org/abs/1911.02116)
- [CKIP BERT Base Chinese](https://huggingface.co/ckiplab/bert-base-chinese)
- [Erlangshen-Roberta-110M-Sentiment](https://huggingface.co/IDEA-CCNL/Erlangshen-Roberta-110M-Sentiment)
- [ProsusAI FinBERT](https://huggingface.co/ProsusAI/finbert)
- [On Calibration of Modern Neural Networks](https://proceedings.mlr.press/v70/guo17a.html)
