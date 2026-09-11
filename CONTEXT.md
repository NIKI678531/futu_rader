# CONTEXT — 舆情雷达领域词汇

本仓库是 **single-context**：一份 `CONTEXT.md` ＋ 一个 `docs/adr/`。约定见 [docs/agents/domain.md](docs/agents/domain.md)。

这里只放**词汇**：每个词一个定义、一处出处、以及**明确不要用的同义词**。口径公式本身不在这里，在 [docs/PRD.md](docs/PRD.md) 第 3 章；工程定案在 [docs/adr/](docs/adr/)。

写代码、起变量名、写 issue 标题、写测试名时用这里的词。**需要的词不在表里，是个信号**：要么你在发明这个项目不用的语言（重新想），要么这里真有缺口（补进来）。

---

## 1. 业务对象

| 术语 | 定义 | 出处 | 不要用 |
|---|---|---|---|
| **舆情雷达 / futu-radar** | 面向 CSOP ETF 产品／市场团队的**只读**舆情工作台，监控富途牛牛社区对 ETF 的讨论。 | [CLAUDE.md](CLAUDE.md) | 「监控系统」「预警平台」——本系统无预警推送，见 PRD §7 负面清单 |
| **自家产品（own）** | 客户维护的 61 只 CSOP ETF。 | PRD §3.8 | 「我方」「本司产品」 |
| **竞品（peer）** | 客户维护的 59 只同业 ETF。**由客户维护，不由系统判定**。 | PRD §3.8 | 「对手盘」「友商」 |
| **产品池（pool）** | 自家 61 ＋ 竞品 59 ＝ 120 只，构成全市场活跃 ETF 的完整口径基准。 | `pool(range)` | 「全量列表」 |
| **板块（sector）** | 8 个固定分类：港股 / A股 / 美股 / 单一股票 / 亚太及区域 / 固收 / 商品 / 虚拟资产。 | PRD §3.7 | 「行业」「主题」——「主题」在本系统专指观点主题 |
| **帖子（feed）** | 富途社区的一条动态／文章。**一条帖子是一行数据**，带 `like_count` / `comment_count` / `share_count` / `browse_count`。 | `futu_comments_feeds` | 「文章」「post」混用；中文一律「帖子」 |
| **评论（comment）** | 帖子底下的一条回复。**评论不是帖子**——两者的计数、态度、去重口径全都不同，混用是本项目最贵的错误。 | `raw_json.comment.comment_items` | 「留言」「回帖」 |
| **发帖人 / 评论人** | 社区用户。KOL 与官号是它的子集。 | `futu_comments_users` | 「用户」——「用户」在本仓库指使用本工作台的 CSOP 同事 |
| **KOL** | 有影响力的个人账号，由客户维护名单认定。 | PRD §4.3 | 「大 V」「意见领袖」 |
| **官号** | 发行商／平台的官方账号（如恒生投资、华夏、Global X、牛牛課堂）。 | PRD §4.5 | 「机构号」「蓝 V」 |

## 2. 口径词汇

> 公式只在 `backend/core/` 实现一份，前端只承接字段（铁律 1）。这里只定义**词**。

| 术语 | 定义 | 出处 | 不要用 |
|---|---|---|---|
| **讨论热度（heat）** | `讨论热度 ＝ 评论量 ＋ 0.3 × 点赞 ＋ 1 × 转发`。点赞含帖子获赞与评论获赞，转发为相关帖子的转发数。**这串字符逐字印在页面上**。 | PRD §3.3、`HEAT_FORMULA` | 「热度值」「活跃度」；**不要**改写公式文案 |
| **评论量** | 相关帖子的平台评论计数之和（`Σ comment_count`）。**不等于**我们解析到的评论条数——见 [ADR-0011](docs/adr/0011-comment-volume-caliber.md)。 | PRD §3.3 ＋ ADR-0011 | 「评论数」与「评论条数」混用 |
| **提及（mention）** | 一条帖子指向某只标的。**账号域按出现次数累加**（`ETF_MENTION_RULE`），**市场域按评论去重**（PRD §3.2）——**两者语义相反，永远不要互相引用**。 | PRD §3.2 / `ETF_MENTION_RULE` | 把两套口径都叫「提及数」而不注明域 |
| **挂载标的** | 帖子被采集时所属的个股讨论区（`futu_comments_feeds.stock_id`）。 | ADR-0013 (O2) | 「归属股票」 |
| **正文提及** | 帖子正文里出现的产品代码／名称。**上游已结构化提供**（`raw_json.summary.rich_text[].stock`），不需要我们做文本匹配。 | ADR-0013 (O2) | 「文本命中」 |
| **阵营（camp）** | 一条帖子按「挂载标的 ∪ 正文提及」对照产品池后的归属：自家 / 竞品 / both。**不经模型判断**。 | `CAMP_RULE` | 「阵地」——「阵地」在设计稿里指 KOL 的发帖平台分布 |
| **态度（attitude）** | 积极 / 中性 / 消极三分，**评论级**。有效样本少于 `LOW_SAMPLE` 不出倾向结论。 | PRD §3.4 | 「情绪」「sentiment」——中文一律「态度」 |
| **观点主题（theme）** | 从评论里归纳出的讨论主题，分正负极性。 | `themesFor` | 「话题」——「话题」是富途的 `topic_items` |
| **动态负面类别（negCat）** | 帖子级的负面分类，带新增／持续／消退生命周期。 | `negCatsFor` | 「负面标签」 |
| **重点舆情（需合规关注）** | 需合规关注的信号，AI 识别后**未经人工确认前一律标「待确认」**。 | PRD §3.4、`complianceFor` | 「风险事件」「预警」 |
| **基准区间（benchmark）** | 与当前区间等长的前一段，用于算环比。**后端下发，前端不自行算桶**。 | PRD §3.1、`buildRange` | 「对比期」「上期」 |
| **全市场评论量排名** | 基于完整活跃 ETF 池计算，**板块筛选不重算**，同一产品名次必须稳定；热力图色阶标尺同理。 | 铁律 3、PRD §3.8/§4.1 | 「排名」不加限定——KOL 声量排名是另一套，不受此约束 |
| **KOL 声量排名** | 账号域的独立指标，**不受全市场排名约束**。 | PRD §4.3 M5 | 与上一条混称「排名」 |

## 3. 状态与缺失

| 术语 | 定义 | 出处 |
|---|---|---|
| **六态** | `0` / `—` / `暂不可用` / `暂无内容` / `样本不足` / `待确认`。**字段级**，不是响应级。 | PRD §3.6、`STATUS_LEGEND` |
| **status 枚举** | 接口侧的 `ok` / `empty` / `unavailable` / `low_sample` / `na`。只用于 **200 响应**描述数据可得性；传输层错误走 `{"error": {...}}`。 | plan.md Q7 |
| **`0` 的专用语义** | 「已取得数据且统计值确实为零」。**用 0 代替未知就是撒谎**（铁律 2）。 | PRD §3.6 |
| **三套缺失文案（不可互换）** | 图例／短徽章用「暂不可用」「暂无内容」；数值位与环比位用「**数据暂不可用**」；空态用「**暂无相关内容**」。 | PRD §3.6/§3.1/§4.1 |
| **样本不足** | 有效产品态度评论少于 `LOW_SAMPLE`（当前 10）。**这个数字逐字印在页面上**，改它等于改可见文案。 | PRD §3.5 |
| **待确认** | AI 自动识别、尚未人工确认的结论。**口径有冲突，待项目负责人裁决**：PRD §3.5/§3.9 与 `backend/fixtures/meta.json` 仍写「置信度低于 `lowConfidence`（0.7）」，而 [ADR-0017](docs/adr/0017-ai-annotation-pipeline-production.md) §4 废止了该阈值（模型自报的信心不是校准概率，拿它画线等于给随机数画线），改由 `review_state` 驱动。CLAUDE.md 定 PRD 效力最高，所以这里不自行取舍。 | PRD §3.6、ADR-0017（取代 ADR-0010 §3） |

## 4. 工程词汇

| 术语 | 定义 | 出处 |
|---|---|---|
| **设计源镜像** | `design/` 目录，Claude Design 项目的**逐字节只读镜像**。永远不手改；设计变更**重新拷贝**而非照着手推。 | [README.md](README.md) |
| **移植版** | `frontend/`，从镜像移植的 React + Vite 版本。 | README.md |
| **可见输出等价** | 「100% 还原」的判定标准：同一 URL 参数下，移植版与设计源渲染出的**可见文本与数值完全一致**。不要求源码等价。 | [ADR-0005](docs/adr/0005-sync-shim-and-fidelity.md) |
| **provider** | 后端供数实现。`demo` 读 fixture（负责 100% 还原），`sql` 读瘦库真实数据（负责诚实）。**端点契约完全一致**。`sql` 原名 `mysql`，早期 ADR 里仍这么写。 | [ADR-0001](docs/adr/0001-dual-provider.md)、[ADR-0016](docs/adr/0016-sqlite-local-mysql-prod.md) |
| **垫片（shim）** | `frontend/src/data/radar.js`，把 API 响应喂成屏内期望的同步返回形状。**不补任何默认值**。 | [ADR-0005](docs/adr/0005-sync-shim-and-fidelity.md) |
| **契约函数 / 展示助手 / 生成器** | `window.RADAR` 成员的三分法：契约函数走 API；展示助手（`rgba`/`typeStyle`/`dirStyle`/`shell`/`navGroups`）留前端；生成器（`hash`/`rnd`/`pick`/`pickN`）接 API 后**必须从屏内消失**。 | [ADR-0004](docs/adr/0004-radar-member-split.md) |
| **视图内聚合** | 三分法之外的第五类：对**已下发响应**再数一遍，且结果必须随前端筛选重算，因此没有端点。目前只有 `kolProfile`（`frontend/src/lib/profile.js`）。不是口径公式，不受铁律 1 约束。 | [ADR-0015](docs/adr/0015-view-scoped-aggregation.md) |
| **原始事实表** | worker 从 `raw_json` 拆出的 feeds / comments / mentions / users 四张表。**只落事实，不算口径**。 | [ADR-0009](docs/adr/0009-worker-scope.md) |
| **瘦库** | 从 10GB 全量 dump 派生出的项目库：120 只标的 × 最近 N 天（实际 504,400 篇帖子 × 120 天）。本地是 SQLite 文件，生产是 MySQL 8，schema 共用 `radar_db/schema.py`。原始 dump 与瘦库**都不进 git**（含真实用户数据），默认落在仓库树外。 | [ADR-0008](docs/adr/0008-dump-import-and-slim-db.md)、[ADR-0016](docs/adr/0016-sqlite-local-mysql-prod.md) |
| **判定单元** | AI 标注的最小粒度：`(comment_id, subject_code)`。一条评论可以对 3033 正面、对 2800 负面 —— 竞品对比场景下这是常态，不是边缘情况。不涉及具体标的时 `subject_code = ''`（不用 `NULL`，否则唯一索引失效、幂等失效）。 | [ADR-0017](docs/adr/0017-ai-annotation-pipeline-production.md) |
| **`annotations` 表** | AI 标注结果的唯一落点，`backend/core/` 的 AI 派生字段一律从这里读。**没有「模型自报 confidence」这一列** —— 只有 `calibrated_confidence`，且只能由校准过的模型写入，当前全为 NULL。「待确认」态由 `review_state` 驱动（那是事实：有没有人看过），不由伪概率驱动。 | [ADR-0017](docs/adr/0017-ai-annotation-pipeline-production.md)（取代 [ADR-0010](docs/adr/0010-annotations-and-ai-pipeline.md)） |
| **证据（evidence）** | 结论在原文里的 `(start, end)` 偏移，**由程序定位**而非模型自报。模型给的引文只是线索：能在原文精确匹配上才存，匹配不上则不存证据并把结论标 `needs_review`。存偏移不存文本 —— 原文一旦漂移，对不上会立刻炸而不是静默出错。 | [ADR-0017](docs/adr/0017-ai-annotation-pipeline-production.md) |
| **锚点（ANCHOR）** | 「今天」＝**最近一个完整自然日**。由 `/meta` 下发，前端不自算，系统时间从不参与。值随 provider 走：`demo` 冻结在 `2026-09-01`（`NOW` ＝ `2026-09-02 09:00 HKT`），`sql` 取导入实测的 `2026-08-25`（数据实际止于 `2026-08-26 03:00`，另记在 `meta_kv.data_max_ts`）。 | [ADR-0012](docs/adr/0012-frozen-demo-anchor.md) |

## 5. 明确不属于本系统的词

以下来自 PRD §7 负面清单。出现在代码、issue 或界面里都是**错的**：

**关注** · **指派** · **处置** · **预警推送** · **告警** · **工单** · **传播关系图**（转发链数据源层面不可得，见 [ADR-0013](docs/adr/0013-prd-open-items-o1-o8.md) O7）
