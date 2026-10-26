# ADR-0037 — 池外 ETF 展示与产品池归属分离

- **状态**：已接受
- **日期**：2026-10-22
- **扩展**：[ADR-0036](0036-kol-body-product-attribution.md)

## 背景

ADR-0036 修正了挂载讨论区被误当成正文提及的问题，但读路径随后只保留
`mentions.in_pool=true`，导致正文明确提到的池外 ETF（例如 QQQ、ARKK、SOXL）也被隐藏。
同时，ticker 的外形不足以区分 ETF、指数、股票和衍生品。生产样例中 `800000.HK` 是
恒生指数，`68882.HK` 是牛熊证；二者都不应进入 ETF 列。

## 决策

1. `mentions` 保留富途结构化 stock 片段的 `display_symbol`、`display_name`、
   `instrument_type_v2` 与 `sub_instrument_type_v2` 原始事实。
2. 新增展示契约 `mentionedEtfs`。池内正文产品直接纳入；池外代码须由下列任一来源
   明确确认为 ETF：正文结构化片段 `instrument_type_v2=4`、MarketInsight 证券目录
   `instrument_type=etf`，或完整正文中富途严格 cashtag 的平台展示名明确以 `ETF`／
   「交易所买卖基金」结尾。裸 ticker、普通正文名称和 AI 推断均不构成 ETF 身份。
3. `mentionedEtfs` 不参与产品归属。既有 `code`、`mentioned`、`camp`、自家/竞品 KPI、
   官号归属汇总及产品页相关 KOL 继续只认当前产品池。
4. 页面 ETF 列可展示多个 ticker，池外 ETF 使用中性「池外 ETF」标记；指数、股票、
   牛熊证、窝轮及其他产品不显示。
5. `feed_details` 是长文正文的权威来源。线上同步必须具备
   `(detail_updated_at, feed_id)` 复合索引；缺失时启动检查失败，不得静默退化为帖子预览。

## 后果

- 正文真实提到但尚未进入竞品池的 ETF 不再漏报。
- 产品池口径和历史 KPI 不因展示范围扩大而变化。
- 旧数据需重新执行 feed/detail 规范化，以全文 cashtag 和证券目录回填池外 ETF 身份；
  仍无法由上述三类事实确认的代码继续 fail closed。
