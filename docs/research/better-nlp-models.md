# ETF 评论相关性与产品态度：更强本地 NLP 模型专项调查

> 调查日期：2026-09-10  
> 调查范围：替代项目中实测效果不佳的 XLM-R / MacBERT，重点考察指定 ETF 的相关性过滤、产品态度分类、繁简中文与港式表达、短回复上下文，以及本地高吞吐部署。  
> 来源原则：模型能力、参数、许可和接口只引用模型发布方的模型卡、官方仓库、框架文档或论文。公开 benchmark 不是本项目效果保证。

## 1. 修正结论

先修正此前的推荐：**XLM-R 和 MacBERT 不再是生产主候选，只保留为“已失败基线”**。既然项目真实文本上已经出现大量无法有效识别或明显误判，继续把同一类模型作为默认终点没有意义；保留它们只是为了量化新模型究竟提升了多少。

本项目也没有一个可直接下载、无需适配便能正确工作的“ETF 情感模型”。真正要解的是两个不同任务：

1. **相关性／作用域**：评论是在评价指定 ETF 产品，还是只预测市场涨跌、谈别的标的或闲聊？
2. **产品态度**：只有在产品相关时，才判断 `positive / neutral / negative`。

新的首选评测组合是：

- **主分类器（成熟纯文本路线）**：`Qwen3-1.7B-Base + sequence-classification head`；`Qwen3-0.6B-Base`作为吞吐下限，`Qwen3-4B-Base`作为质量上限。
- **主分类器（最新小模型路线）**：`Qwen3.5-2B-Base + text sequence-classification head`；`Qwen3.5-0.8B-Base`作为最低成本下限。
- **本地教师／疑难样本裁决**：`Qwen3.5-4B` post-trained，非思考模式、限定枚举输出；它不应先跑全量。
- **相关性模型**：同场比较 `Qwen3-Reranker-0.6B`、`Qwen3-Reranker-4B` 与 `BAAI/bge-reranker-v2-m3`。
- **召回、去重和聚类**：按需比较 `Qwen3-Embedding-0.6B/4B` 与 `BGE-M3`；它们不是情感分类器。

若只能先做一个最小 PoC：**先微调 Qwen3-1.7B-Base 的分类头，同时用 Qwen3.5-2B-Base 做同标签实验；相关性旁路比较 Qwen3-Reranker-0.6B 与 BGE-reranker-v2-m3。** 不应先花算力跑 4B embedding，也不应再投入大量时间调 XLM-R/MacBERT。

## 2. “很多文本无法识别”要先分清是哪种失败

XLM-R 和 MacBERT 都使用子词 tokenizer，通常不会像词典模型那样遇到新词就完全不能编码。因此“无法识别”可能至少有三种含义，修复方式不同：

- **数据／管线失败**：空文本、HTML、emoji、繁体字被预处理删掉，编码错误，截断错误，或批处理异常导致没有输出。应记录 `empty_after_cleaning`、字符长度、token 长度、`[UNK]` 比例、异常类型和原始文本哈希。
- **模型低置信或乱判**：能编码，但没有学过“产品态度 vs 市场方向”这一业务边界。这是训练目标不匹配，不是换 tokenizer 就能解决。
- **上下文不足**：例如“係囉”“唔掂”“佢又嚟”“这个可以”必须结合帖子、父评论和目标产品才能判断。只给单条评论时，任何模型都会有不可约的不确定性。

新实验必须分别统计这三种情况。否则换成更大的模型后，即使表面覆盖率提高，也无法知道究竟修复了模型还是掩盖了数据问题。

## 3. 模型角色不能混用

| 模型 | 原生输出 | 能否直接做 ETF 相关性 | 能否直接做三分类产品态度 | 正确角色 |
|---|---|---:|---:|---|
| Qwen3-Embedding-0.6B/4B | 一个文本向量 | 不能直接决定；需阈值、原型或另训分类头 | 否 | 召回、聚类、近重复、分类特征 |
| Qwen3-Reranker-0.6B/4B | query-document 的 yes/no 相关分数 | 可以作为相关性打分器 | 否 | 相关性排序／过滤 |
| BGE-M3 | dense、sparse、multi-vector 表示 | 不能直接决定 | 否 | 混合召回，尤其兼顾代码／别名精确词 |
| BGE-reranker-v2-m3 | query-document 单个相关性 logit | 可以作为相关性打分器 | 否 | 快速多语言重排 |
| Qwen3-Base + sequence head | 类别 logits | 微调后可以 | 微调后可以 | 本地判别式分类器 |
| Qwen3.5-Base + text sequence head | 类别 logits | 微调后可以 | 微调后可以 | 新一代本地判别式分类器 |
| Qwen3.5 post-trained | 生成的枚举标签／JSON | 可以 | 可以 | 教师、弱标注、疑难裁决 |
| Erlangshen-Roberta-330M-Sentiment | 通用中文二分类 logits | 不能解决作用域 | 只能给通用正负面基线 | 开箱情感 sanity baseline |
| 现有 XLM-R / MacBERT | 项目旧分类输出 | 实测不合格 | 实测不合格 | 失败基线 |

两个容易误导的名称需要特别说明：

- Qwen3 Embedding 的模型卡列出了 MTEB `Classification` 分数，意思是**用向量做下游分类评测**，不是模型本身会输出 ETF 情感标签。
- BGE reranker 在 Hugging Face 上标为 `Text Classification`，且实现使用 `AutoModelForSequenceClassification`，但官方 checkpoint 只有**一个 query-passage 相关性分数**，不是 positive／neutral／negative 三个类别。

## 4. 候选模型专项评价

### 4.1 Qwen3 Embedding 0.6B / 4B：可用于召回，不能冒充情感模型

官方模型卡给出的共同能力是 100+ 语言、32K 上下文、Apache-2.0 许可和 instruction-aware；0.6B 最大 1024 维，4B 最大 2560 维，并支持 Matryoshka 方式裁剪输出维度。官方还建议为查询写任务 instruction，并报告多数检索任务约有 1%–5% 提升。[0.6B 模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[4B 模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-4B)、[官方仓库](https://github.com/QwenLM/Qwen3-Embedding)、[论文](https://arxiv.org/abs/2506.05176)

官方自报 C-MTEB 平均分中，0.6B 为 66.33，4B 为 72.27；这说明 4B 是值得保留的中文语义质量上限，但这些是通用 embedding benchmark，不是香港 ETF 评论准确率。

适合本项目的三种用法：

1. 给评论做向量，用于近重复、模板灌水和主题聚类；
2. 用产品代码、名称、别名、费用、跟踪误差、流动性等“产品评价原型”召回可能相关评论；
3. 冻结向量后训练 Logistic Regression／小 MLP。此时**分类能力来自项目标注和分类头**，不是 embedding checkpoint 自带的情感能力。

不建议仅凭 cosine 阈值直接删除评论。市场话题和产品评价通常共享大量词汇，例如“恒指”“跌”“加仓”，语义相近并不等于业务标签相同。

### 4.2 Qwen3 Reranker 0.6B / 4B：相关性强候选，不是情感分类器

Qwen3 Reranker 接受 instruction、query、document，并通过 `yes/no` token logits 形成相关性概率；支持 100+ 语言、32K 上下文和 Apache-2.0 许可。[0.6B 模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)、[4B 模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-4B)

官方同一张 reranking 表中：

| 模型 | 参数规模 | CMTEB-R |
|---|---:|---:|
| Qwen3-Reranker-0.6B | 0.6B | 71.31 |
| BGE-reranker-v2-m3 | 约 0.6B | 72.16 |
| Qwen3-Reranker-4B | 4B | 75.94 |

因此不能笼统说 Qwen3 0.6B 一定优于 BGE：在官方这项中文检索评测上，0.6B 略低，4B 才明显更高。三者都没有 ETF、粤语或繁体股评专项结果。

推荐的相关性 instruction 可按以下含义编写，实际部署仍需在验证集上校准阈值：

```text
Decide whether the comment evaluates the specified ETF as a product.
Product evaluation includes fees, tracking quality, liquidity, spread,
dividend, issuer, mechanism and user experience. Market/index direction,
price prediction, unrelated tickers and casual chat are not product evaluation.
```

`Query` 放目标代码、正式名称和常用别名；`Document` 放评论，并在需要时附帖子标题与父评论。Reranker 只输出相关程度，不能从一个高相关分数推导出正负态度。

### 4.3 BGE-M3 / bge-reranker-v2-m3：保留作相关性对照，不回到旧情感路线

BGE-M3 支持 100+ 语言、8192 tokens、1024 维，并可同时输出 dense、sparse 和 ColBERT multi-vector 表示；许可为 MIT。它的优势是一次编码兼顾语义与字面词匹配，适合产品代码、ticker、简称等精确线索。[BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3)、[官方仓库](https://github.com/FlagOpen/FlagEmbedding)、[论文](https://arxiv.org/abs/2402.03216)

但 BGE-M3 的底层仍是 XLM-R 系架构，官方也明确把它定义为 retrieval embedding model。因此它可能因为检索训练而比原始 XLM-R 更适合相关性召回，却没有理由自动解决产品态度。

`bge-reranker-v2-m3` 是轻量多语言 cross-encoder：输入 query 和 passage，输出一个可经 sigmoid 映射到 0–1 的相关性分数。官方将它定位为易部署、快速的多语言 reranker；官方示例把输入限制为 512 tokens。[模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3)

结论：两者都值得进入**相关性**实验，特别是 BGE-M3 的 dense+sparse 混合召回；两者都不进入最终**产品态度模型** shortlist。

### 4.4 Qwen3-Base + 分类头：成熟、纯文本、高吞吐主候选

Qwen3 Base 官方称预训练覆盖 36T tokens 和 119 种语言，0.6B 模型为 28 层、32,768 上下文、Apache-2.0。Transformers 已正式提供 `Qwen3ForSequenceClassification`，可以在最后隐藏状态上训练类别 logits，而不是生成长文本。[Qwen3-0.6B-Base](https://huggingface.co/Qwen/Qwen3-0.6B-Base)、[Qwen3-1.7B-Base](https://huggingface.co/Qwen/Qwen3-1.7B-Base)、[Qwen3-4B-Base](https://huggingface.co/Qwen/Qwen3-4B-Base)、[Transformers Qwen3 文档](https://huggingface.co/docs/transformers/model_doc/qwen3#transformers.Qwen3ForSequenceClassification)、[技术报告](https://arxiv.org/abs/2505.09388)

为什么优先测 1.7B：

- 0.6B 适合找吞吐和显存下限，但复杂否定、反讽及跨上下文能力可能不足；
- 1.7B 在成本和表达能力之间更适合作主候选；
- 4B 用来判断继续增加模型规模是否还有显著收益，不默认全量部署。

这条路线的关键不是“Qwen 天生会情感”，而是更强、更广的语言底座加上**本项目的六类／多头监督标签**。分类头单次前向即可出 logits，一般比生成 JSON 更适合大吞吐；实际速度仍必须在目标显卡上测。

### 4.5 Qwen3.5 0.8B / 2B / 4B：最新小模型路线，值得重点测但工程更新

截至 2026-09-10，Qwen3.5 提供 0.8B、2B、4B 的 Base 与 post-trained 小模型。官方模型卡将 Base 明确定位为 fine-tuning、in-context learning 和研究用途，并说明 control tokens 已为 LoRA-style PEFT 做过准备；该系列宣称覆盖 201 种语言和方言、原生 262,144 上下文、Apache-2.0，并采用 Gated Delta Networks 与注意力混合架构以提升效率。[Qwen3.5-0.8B-Base](https://huggingface.co/Qwen/Qwen3.5-0.8B-Base)、[Qwen3.5-2B-Base](https://huggingface.co/Qwen/Qwen3.5-2B-Base)、[Qwen3.5-4B-Base](https://huggingface.co/Qwen/Qwen3.5-4B-Base)

Transformers `main` 文档已经列出 `Qwen3_5ForSequenceClassification` 和纯文本的 `Qwen3_5TextForSequenceClassification`，说明它可以作为真正的类别 logits 模型训练，而不只做生成。[Transformers Qwen3.5 文档](https://huggingface.co/docs/transformers/main/en/model_doc/qwen3_5#transformers.Qwen3_5TextForSequenceClassification)

需要同时看到工程风险：

- 截至调查日，官方模型卡仍要求 Transformers main、vLLM nightly 或 SGLang main 等较新版本；必须锁定 commit／镜像并做回归，不能直接跟随滚动最新版。
- checkpoint 是带 vision encoder 的统一模型；纯文本分类应验证 `Qwen3_5TextForSequenceClassification` 是否只加载所需语言骨干，以及真实显存占用。
- 生成式部署可用 vLLM 的 `--language-model-only` 跳过视觉编码器 profiling；分类头部署是否能走同一高吞吐引擎，要以所选框架实际支持为准。
- 262K 上下文对短评论没有价值。实验应先 cap 为 256／512 tokens，避免长上下文配置吞掉显存和吞吐。

官方只说 201 种语言和方言，没有给出香港繁体、粤语口语、港股俚语或 ETF 态度成绩。这个数字只能说明值得试，不能当成验证结果。

Qwen3.8 虽然更新，但调查到的官方公开小端为 `Qwen3.8-27B`；27B 不适合本项目低成本全量分类，所以不纳入第一轮本地 shortlist。[Qwen3.8-27B 模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)

### 4.6 开箱中文情感模型：只作 sanity baseline

`IDEA-CCNL/Erlangshen-Roberta-330M-Sentiment` 在 8 个中文情感数据集、共 227,347 个样本上微调，参数约 330M、Apache-2.0，可直接输出通用中文情感二分类。[模型卡](https://huggingface.co/IDEA-CCNL/Erlangshen-Roberta-330M-Sentiment)、[Fengshenbang 论文](https://arxiv.org/abs/2209.02970)

它可以回答“一个开箱中文 sentiment checkpoint 比旧模型好多少”，但不适合作主方案：数据目标是通用正负面，不知道指定 ETF、不区分市场看跌与产品负面，也没有香港语言切片。公开数据集上的 97% 左右结果不能外推到本项目。

## 5. 推荐生产管线

```text
原始评论
  │
  ├─ A. 只做确定性清洗：空文本、损坏记录、精确重复、已确认广告模板
  │      └─ 原文永久保留；繁体、emoji、粤语词不做破坏性归一化
  │
  ├─ B. 构造 (comment_id, target_product) 判定单元
  │      ├─ target: 代码、正式名、简称、杠反方向、发行商
  │      └─ context: 帖子标题、必要时父评论；短回复优先补上下文
  │
  ├─ C. 相关性
  │      ├─ 已知唯一 target、数据量可控：直接跑监督分类器／reranker
  │      └─ 多 target 或候选很多：Embedding/BGE 混合召回 → Reranker
  │
  ├─ D. 产品态度分类
  │      ├─ Qwen3 / Qwen3.5 sequence-classification head
  │      └─ 低置信、反讽、冲突或 needs_context → Qwen3.5 teacher／托管 LLM
  │
  └─ E. 按 P(irrelevant) 排序供抽检；达到验证门槛后才自动过滤
```

如果每条评论已经由页面结构绑定到一只 ETF，就没有必要为“技术上用了 embedding”而先做 top-K 召回；直接分类通常更简单，也避免召回层漏掉真正相关评论。Embedding 的主要价值会变成去重、聚类和弱监督特征。

## 6. 标签与输入设计

### 6.1 推荐联合标签

第一版可以用一个六类分类头，先把最危险的边界显式分开：

```text
IRRELEVANT_MARKET   只谈指数、股价方向、宏观或入市时机
IRRELEVANT_OTHER    其他标的、广告、闲聊、无实质内容
PRODUCT_POSITIVE    对指定 ETF 产品本身正面
PRODUCT_NEUTRAL     对指定 ETF 产品本身有内容但中性
PRODUCT_NEGATIVE    对指定 ETF 产品本身负面
NEEDS_CONTEXT       仅凭当前输入无法可靠判断
```

数据量足够后可改成两个 head：`scope` 与 `attitude`。两个 head 更便于分别调过滤阈值，且不会把 `irrelevant` 错算为 `neutral`。

### 6.2 推荐输入格式

```text
[TARGET]
code=3033; name=南方恒生科技ETF; aliases=恒科ETF,...
[POST_TITLE]
...
[PARENT_COMMENT]
...
[COMMENT]
...
```

- 原评论始终保留；繁简转换结果若要用，只能作为额外字段。
- 产品代码和别名由结构化数据提供，不让模型猜实体。
- 对“佢”“呢隻”“係囉”“唔掂”“可以喎”、单个 emoji 等短回复，比较 `comment only` 与 `comment + parent + title` 两个实验条件。
- 训练时做适量 context dropout，避免模型只凭标题标签泄漏。

### 6.3 用 Reranker 做相关性时

将业务定义放进 instruction，将目标产品放进 query，将评论和必要上下文放进 document。输出仅解释为 `relevance_score`。阈值必须按项目验证集选择；在证明召回率前，只能排序和抽样，不能物理删除低分文本。

### 6.4 用 Sequence Classification 做主分类时

- Qwen3 首测 1.7B；Qwen3.5 首测 2B；`num_labels=6`。
- 输入上限先用 256 tokens，再测 512；绝大部分评论无需 8K／32K／262K。
- 使用类别权重或分层采样，避免模型用多数类“neutral/irrelevant”刷准确率。
- 优先比较全量微调与 LoRA；不要默认 LoRA 一定等价。
- 用 temperature scaling 等方法在独立校准集上校准 logits，拒答阈值不能直接把 softmax 当真实概率。

### 6.5 用 Qwen3.5 post-trained 做教师时

- 关闭 thinking，限定输出一个枚举标签或固定短 JSON；不让模型写解释后再解析。
- 只处理低置信、上下文依赖、讽刺／反问和分类器冲突样本。
- 教师生成的弱标签不能进入最终测试集；抽样人工复核后才可用于蒸馏。

## 7. 实验矩阵

### 7.1 阶段一：相关性过滤

| ID | 模型／方法 | 设置 | 目的 |
|---|---|---|---|
| R0 | 词频、BM25、规则 | 产品代码／别名、费用等词表 | 透明最低基线，只用于发现明显噪声 |
| R1 | BGE-M3 | dense；dense+sparse | 检验精确 ticker + 语义混合召回 |
| R2 | Qwen3-Embedding-0.6B | 原型 cosine；冻结向量 + Logistic Regression | 低成本向量基线 |
| R3 | Qwen3-Embedding-4B | 与 R2 相同 | 判断更大 embedding 是否值得成本 |
| R4 | BGE-reranker-v2-m3 | query-comment pair，128/256/512 tokens | 快速 cross-encoder 对照 |
| R5 | Qwen3-Reranker-0.6B | 自定义 instruction，同长度 | 新一代低成本 reranker |
| R6 | Qwen3-Reranker-4B | 与 R5 同设置 | reranker 质量上限 |
| R7 | Qwen3-1.7B-Base classifier | 相关性 head 或六类联合 head | 监督分类是否优于通用 reranker |
| R8 | Qwen3.5-2B-Base classifier | 与 R7 同标签 | 最新小模型对照 |

### 7.2 阶段二：产品态度

| ID | 模型 | 训练／推理方式 | 定位 |
|---|---|---|---|
| S0 | 现有 XLM-R / MacBERT | 固定原 checkpoint 与原流程 | 失败基线，不继续调参 |
| S1 | Erlangshen-Roberta-330M-Sentiment | zero-shot + 项目数据再微调各一组 | 通用中文 sentiment sanity baseline |
| S2 | Qwen3-0.6B-Base | `Qwen3ForSequenceClassification` | 吞吐下限 |
| S3 | Qwen3-1.7B-Base | 同上 | 成熟纯文本主候选 |
| S4 | Qwen3-4B-Base | 同上 | 纯文本质量上限 |
| S5 | Qwen3.5-0.8B-Base | `Qwen3_5TextForSequenceClassification` | 最新架构最低成本候选 |
| S6 | Qwen3.5-2B-Base | 同上 | 最新架构主候选 |
| S7 | Qwen3.5-4B post-trained | 非思考、枚举生成；zero/few-shot 与 LoRA 各一组 | 教师／疑难裁决上限 |

第一轮不必把所有组合跑到底。先用 15%–20% 开发集做淘汰赛：R4/R5/R7/R8 与 S2/S3/S5/S6；再让胜者进入完整训练和冻结测试集，最后才加入 4B 质量上限。

### 7.3 金标集与切片

最低建议 3,000 个 `(comment, target_product)` 人工金标单元；若产品、年份和语言分布复杂，优先扩到 5,000–10,000。测试集必须按帖子／用户／时间分组切分，近重复评论不能横跨 train/test。

冻结测试集至少单独报告：

- 简体、繁体、繁简混合；
- 粤语／港式口语、英文夹杂；
- 纯 emoji、短回复、代词；
- 否定、双重否定、反讽／反问；
- 市场看跌但产品正面；
- 市场看涨但产品负面；
- ETF 比较句：对 A 正面、对 B 负面；
- 费用、点差、流动性、跟踪误差、分红、发行商／机制；
- 无上下文可判与必须依赖父评论两组。

官方没有这些切片的成绩，因此切片结果比 MTEB 排名更重要。

### 7.4 指标和初始放行门槛

相关性层：

- `PRODUCT_*` 总体 recall，以及 `PRODUCT_NEGATIVE` recall；
- market-only 被误放为 product 的比例；
- PR-AUC、macro-F1、每个切片的 recall；
- 若用于自动过滤，建议在冻结测试集上把“产品相关 recall 的 95% 置信区间下界 ≥ 98%”作为初始门槛；达不到时只能排序，不自动丢弃。

态度层：

- macro-F1，而不是只看 accuracy；
- positive／neutral／negative 各自 precision、recall；
- `market-only → negative` 和 `irrelevant → neutral` 两个关键混淆率；
- Brier score／ECE 与拒答后的 coverage-risk 曲线；
- `comment only` 与 `+context` 的净提升。

工程层：

- 同一台目标机器、同一批输入上的 comments/s、tokens/s、p50/p95 latency、峰值显存；
- batch size 1／8／32／64，max length 128／256／512；
- BF16 与最终量化版本分别测，量化后必须重跑质量集；
- 每百万条总 GPU 小时，并计入相关性、态度、fallback 各层实际通过率。

## 8. 本地吞吐与显存判断

仅按参数计算、未计激活和运行时开销时，BF16 权重下限约为：0.6B 约 1.2 GB、0.8B 约 1.6 GB、1.7B 约 3.4 GB、2B 约 4 GB、4B 约 8 GB。Qwen3.5 的实际总参数略高于型号名，且统一视觉组件、KV cache、batch、优化器状态会继续增加显存；训练显存不能用这些数字估算。

部署建议：

- 评论分类优先短序列、动态 batching；先扩大 batch，再考虑扩大上下文。
- Qwen3 Embedding 官方支持 Transformers、Sentence Transformers、vLLM embedding task 和 TEI，并建议 FlashAttention 2。
- Qwen3 Reranker 每个 query-document pair 都要前向；若一条评论对应很多候选产品，先召回 top-K 再 rerank。若只有一个结构化目标，直接判这一对即可。
- Qwen3.5 生成式 teacher 用 vLLM／SGLang continuous batching；官方模型卡建议高吞吐使用专门 serving engine，并提供 `--language-model-only`。
- 分类头路线先用 Transformers 批推理建立正确性基线，再评估框架是否支持该具体 architecture；不要为了 serving 方便改回生成式并牺牲吞吐。

## 9. 最终 shortlist 与决策顺序

### 必测

1. **Qwen3-1.7B-Base + sequence head**：成熟纯文本主分类器。
2. **Qwen3.5-2B-Base + text sequence head**：最新小模型主挑战者。
3. **Qwen3-Reranker-0.6B 与 BGE-reranker-v2-m3**：相关性低成本对照。
4. **Qwen3.5-4B post-trained**：疑难样本教师／质量上限。

### 有条件再测

5. **Qwen3-Reranker-4B**：若 0.6B 相关性 recall 不够，测试 4B 是否值得。
6. **Qwen3-Embedding-0.6B + 浅层分类头**：如果需要聚类、去重和过滤共享同一向量服务。
7. **BGE-M3 dense+sparse**：如果产品代码和别名的精确召回是主要问题。
8. **Qwen3.5-0.8B / Qwen3-0.6B**：寻找最低成本可接受点。

### 不作为生产主方案

- XLM-R / MacBERT：项目已验证失败，只保留固定基线。
- Qwen3 Embedding / BGE-M3 单独做情感：任务类型错误。
- Qwen3 / BGE Reranker 单独做情感：只能给相关性分数。
- Erlangshen 通用 sentiment 直接上线：标签口径错误。
- Qwen3.8-27B：对本项目全量本地分类过重。

## 10. 一句话建议

**不要再找一个“开箱情感 checkpoint”替换 XLM-R/MacBERT；应以项目金标数据微调 Qwen3-1.7B 与 Qwen3.5-2B 的真正分类头，用 Qwen/BGE reranker 只做产品相关性，用 Qwen3.5-4B 只处理疑难样本。** Embedding 和 reranker 可以减少成本，但不能代替产品态度分类。

## 11. 一手来源清单

- Qwen3.5 小模型：[0.8B Base](https://huggingface.co/Qwen/Qwen3.5-0.8B-Base)、[2B Base](https://huggingface.co/Qwen/Qwen3.5-2B-Base)、[4B Base](https://huggingface.co/Qwen/Qwen3.5-4B-Base)、[2B post-trained](https://huggingface.co/Qwen/Qwen3.5-2B)、[Transformers 分类接口](https://huggingface.co/docs/transformers/main/en/model_doc/qwen3_5)
- Qwen3 纯文本基座：[0.6B Base](https://huggingface.co/Qwen/Qwen3-0.6B-Base)、[1.7B Base](https://huggingface.co/Qwen/Qwen3-1.7B-Base)、[4B Base](https://huggingface.co/Qwen/Qwen3-4B-Base)、[Transformers 分类接口](https://huggingface.co/docs/transformers/model_doc/qwen3)、[技术报告](https://arxiv.org/abs/2505.09388)
- Qwen3 Embedding / Reranker：[官方仓库](https://github.com/QwenLM/Qwen3-Embedding)、[论文](https://arxiv.org/abs/2506.05176)、[Embedding 0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[Embedding 4B](https://huggingface.co/Qwen/Qwen3-Embedding-4B)、[Reranker 0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)、[Reranker 4B](https://huggingface.co/Qwen/Qwen3-Reranker-4B)
- BGE：[FlagEmbedding 官方仓库](https://github.com/FlagOpen/FlagEmbedding)、[BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3)、[BGE-M3 论文](https://arxiv.org/abs/2402.03216)、[bge-reranker-v2-m3 模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- 通用中文情感对照：[Erlangshen-Roberta-330M-Sentiment](https://huggingface.co/IDEA-CCNL/Erlangshen-Roberta-330M-Sentiment)、[Fengshenbang 论文](https://arxiv.org/abs/2209.02970)
- 最新但过大的 Qwen 对照：[Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B)
