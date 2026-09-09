# 06: KOL 影响力整页接 API

**What to build:** `/kol` 整页脱离设计源镜像，KOL 列表、声量排名、发帖内容与阵地分布全部由后端供数，逐字比对干净。

**Blocked by:** 05

**Status:** ready-for-agent

## 验收标准

- [ ] `kolImpact(range)` 与 `kolProfile(name, posts, campFn?)` 实现并暴露端点
- [ ] 页面全部内容由 `buildRange` / `kolImpact` / `kolProfile` 驱动
- [ ] 帖子类型**双标签**按 `TYPE_RULE` 逐字：内容形式必有一枚；操作方向仅在表达了明确操作时出现；判不出方向标「方向待确认」。第一期按双标签实现，`typeScheme` 开关保留（PRD O1 / ADR-0013）
- [ ] **KOL 声量排名是另一套指标，不受铁律 3 约束**（PRD §4.3 M5）。验收时不要拿全市场评论量排名那条「筛选不重算」的规则去卡它
- [ ] 阵营筛选「提自家」**含 both**（与官号页的三分互斥不同，这是设计而非 bug）
- [ ] 02 的 `/kol` diff clean
- [ ] 04 的六态断言在本页通过；页面上每个 `0` 可追溯到真实为零的后端字段
- [ ] 断开后端 → 屏级错误条
