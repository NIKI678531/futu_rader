# 07: KOL 详情整页接 API

**What to build:** `/kol/detail` 整页脱离设计源镜像。这一页带着 PRD §5 逐字点名的第一个 `null` 字段（`dominantAttitude` 在样本 < 3 条时），是铁律 2 的第一个真实验收现场。

**Blocked by:** 06

**Status:** ready-for-agent

## 验收标准

- [ ] `kolOpinions(kol, range)` 实现并暴露端点
- [ ] 页面全部内容由 `kolImpact` / `kolProfile` / `kolOpinions` 驱动
- [ ] 移除 `R.addDays`，**移除后输出逐字不变**；04 的静态守卫 ① 对本页转绿
- [ ] **`dominantAttitude` 在样本 < 3 条时后端返回 `null`，前端渲染「暂不可用」，绝不渲染 `0` 或空白**（PRD §5 状态语义逐字点名了这个字段）
- [ ] 入口仍只从 KOL 影响力「详情 →」进入，**不进一级导航**（一级导航只有市场／账号两个域）
- [ ] 02 的 `/kol/detail` diff clean
- [ ] 04 的六态断言在本页通过；每个 `0` 可追溯
- [ ] 断开后端 → 屏级错误条
