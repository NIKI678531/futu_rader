# 09: 市场域叙述组——摘要 / 主题 / 负面类别 / 竞品 / 合规

**What to build:** 板块总览剩下的叙述类内容改由后端供数，`/sector` 整页脱离设计源镜像并逐字比对干净。这一页印着最多的逐字口径说明文字，它们从此全部由 `/meta` 下发，前端零硬编码。

**Blocked by:** 08

**Status:** ready-for-agent

## 验收标准

- [ ] `summaryFor` `hotSummaryFor` `themesFor(code, range, polarity)` `negCatsFor` `competitorsFor` `complianceFor` 实现并暴露端点
- [ ] 板块总览整页走 API，02 的 `/sector` diff clean
- [ ] 六态图例区块与 `/meta` 下发的 `STATUS_LEGEND` **逐字**一致
- [ ] S6 评论去重口径说明块**逐字**保留（PRD §3.2）
- [ ] `HEAT_FORMULA` / `HEAT_NOTE` **逐字来自 `/meta`，前端不得硬编码**（PRD §3.3）。热度＝评论量 ＋ 0.3 × 点赞 ＋ 1 × 转发，权重也由 `/meta` 下发
- [ ] 总览抽屉的 `negCatsFor` 全链路可用
- [ ] 空数组 → 「暂无内容」／空态「暂无相关内容」，**不是** `0`、不是留白
- [ ] 04 的六态断言在本页通过；每个 `0` 可追溯
- [ ] 该页所需端点全部 200，无 5xx；断开后端 → 屏级错误条
