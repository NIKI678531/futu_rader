# ADR-0029 — 父帖 cashtag 资格统一限定产品级指标与 AI 输入

- **状态**：已接受
- **日期**：2026-09-29
- **相关**：[ADR-0011](0011-comment-volume-caliber.md)、[ADR-0025](0025-online-fact-reconciliation-and-comment-coverage.md)、[ADR-0028](0028-exact-comment-eligibility-and-official-attribution.md)

## 背景

讨论区归属只说明帖子从哪里采集，不足以证明父帖及其回复在讨论该 ETF。此前产品页仍把讨论区全部父帖的 `comment_count` 作为主评论量，并允许回复正文“自救”或改路由到另一产品，导致评论量、热度、趋势、排名和 AI 输入使用了不同集合。3037 的冻结库因此显示筛选前的 3,494，而不是父帖筛选后的数量。

外部 `opinion-radar` 只作为父帖 cashtag 事实与确定性筛选的参考；其直连 FUTU 采集、模型、提示词和后续 Magnitude 模块均不成为本项目依赖。

## 决策

1. MarketInsight/Airflow 继续是采集入口。每篇父帖保存来源讨论区 ticker `feeds.source_ticker`，并把父帖 `title + content` 中的严格 cashtag 作为权威快照写入 `feed_mentions(feed_id, raw_ticker, market, occurrences)`。回复、`summary.rich_text`、产品名和别名不生成这些事实；正文修订时事务性替换快照并删除陈旧 ticker。
2. 版本化文件 `radar_db/comment_filters.json` 是唯一策略配置。默认 `exact`；3037 使用 `exclude ["800000.HK"]`。配置必须校验、转大写、去重并生成摘要，非法配置直接失败。
3. `exact` 要求父帖明确提及自己的 `source_ticker`。`exclude` 中自身提及始终保留；否则命中任一排除 ticker 才剔除，空排除列表全部通过。匹配大小写不敏感，且始终由原 `feeds.code` 限定产品归属。
4. 父帖一旦合格，其已抓取回复全部只进入该父帖所属产品的现有 AI 流程。回复正文不能救回不合格父帖，也不能把回复或整串回复改路由到其他产品。模型、prompt、预过滤、分类和合成算法不变。
5. 产品级 `comments` 是合格父帖的 `SUM(feeds.comment_count)`；`mentions` 是合格父帖数。评论量、帖子数、点赞、转发、热度、活跃账号、趋势、环比、排名、benchmark、证据及 AI 结果读取都必须从同一合格父帖集合出发。全局采集元数据仍保留筛选前原始量。
6. 评论漏斗公开 `rawPlatformCount`、`platformCount`、`qualifyingFeedCount`、`filterExcludedPlatformCount`，再接既有正文与 AI 阶段。筛选前平台量只用于审计，不再作为主页面、榜单或热度输入。
7. 历史回填完成后，readiness 必须同时匹配配置版本与摘要才切换读取。readiness 缺失、配置不一致或存在无法解析的 `source_ticker` 时，筛选指标返回“暂不可用”，不得伪装成 0。旧跨产品结果保留审计，但读取层阻断；失效任务标为 `superseded`，只为新增合格评论补建现有任务。

## 上线与验收

上线顺序是 schema/双写、可恢复回填、逐产品对账、写 readiness 并统一切读、升级规则版本与刷新派生数据。冻结锚点 `2026-08-25` 的 3037 验收基线：原始父帖 4,423、原始平台评论 3,494、排除 `$800000.HK$` 后合格父帖 238、筛后评论量 195、已抓回复正文 177。

## 后果

- 3037 不采用严格 exact；这是对该讨论区父帖不自指标的显式配置，而不是代码特例。
- 页面主数会显著低于旧值；筛选前数量仍可从漏斗与回填审计追溯。
- ADR-0011 的计数器来源仍成立，但适用集合收窄为合格父帖；ADR-0025 的覆盖事实仍成立；ADR-0028 中“回复可自救/改路由”和“原始平台量为主指标”的部分由本 ADR 取代，官号归属决策不变。
