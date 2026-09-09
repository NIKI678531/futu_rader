# 10: 产品监控——主题 / 证据侧栏 / KOL 提及

**What to build:** 产品监控页的观点主题、证据侧栏和 KOL 提及改由后端供数。证据侧栏是全站参数最多的契约（四个参数），单独拎出来是因为它的全链路容易在某个参数上断掉而不被发现。

**Blocked by:** 09

**Status:** ready-for-agent

## 验收标准

- [ ] `topicsFor` `evidenceFor(code, ctxKey, polarity, n)` `kolMentionsFor` 实现并暴露端点
- [ ] 证据侧栏**四个参数全链路打通**：换 `ctxKey`、换 `polarity`、换 `n` 都正确取数，不出现某个参数被静默忽略
- [ ] 证据为空数组 → 「暂无内容」／空态「暂无相关内容」，**不是** `0`、不是留白
- [ ] 产品监控页复用 08/09 已落地的 `buildRange` `observe` `ranks` `benchmark` `summaryFor` `themesFor` `competitorsFor` `complianceFor`，**不得为本页另写一份口径**（铁律 1）
- [ ] 04 的六态断言在这些字段上通过；每个 `0` 可追溯
- [ ] 本单**不要求** `/product` 整页 diff clean——K 线与阶段在 11
