# ADR-0036 — KOL 产品归属只认正文证据

- **状态**：已接受
- **日期**：2026-10-22
- **取代**：[ADR-0013](0013-prd-open-items-o1-o8.md) O2

## 背景

`mentions` 将采集讨论区记为 `source='anchor'`，将正文结构化产品提及记为
`source='body'`。生产样本 `117468132999172` 挂在 `03033.HK` 讨论区，但正文只提到
指数和个股；旧读路径取两种来源的并集并优先 anchor，因而把该帖错误显示为提及 3033。

讨论区只能证明帖子从哪里被采集，不能证明作者谈到了该产品。两种事实继续混用还会把
错误传播到 KOL 筛选、阵营统计以及产品页的「相关 KOL」。

## 决策

1. KOL 帖子的 `code`、`mentioned`、`camp` 与 `campPrimary` 只使用产品池内的
   `mentions.source='body'`。
2. `anchor` 继续保存在 `feeds.code` / `feeds.source_ticker` 中，仅供采集来源审计。
3. 正文没有命中池内 ETF 时保留帖子，输出 `code=null`、`mentioned=[]`、`camp=none`；
   页面明确显示「未提及」，不得伪装成数据缺失或用 anchor 补位。
4. 产品页的相关 KOL 继续复用 `kolImpact.posts[].mentioned`，从而自动排除只有 anchor、
   没有正文产品证据的帖子。

## 后果

- KOL 全部帖子仍可用于账号活跃度、浏览量与内容类型分析。
- ETF 筛选、产品归属构成和相关 KOL 数量会下降，这是移除错误归属后的预期变化。
- 官号已有独立的正文归属逻辑，不受本次改动影响。
