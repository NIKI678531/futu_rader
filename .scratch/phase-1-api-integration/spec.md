# Spec — 第一期：后端实现 PRD §5 全部契约函数，五个页面全部走 API

**正文在 [docs/specs/phase-1-api-integration.md](../../docs/specs/phase-1-api-integration.md)**（用户指定存放位置，与 `docs/agents/issue-tracker.md` 默认的 `.scratch/<slug>/spec.md` 不同）。

本目录只放工单：`issues/01` … `issues/12`，按依赖顺序编号（阻塞者在前）。

## 依赖图

```
01 最小闭环（官号清单表）──┬─→ 03 展示助手迁出 ─┬─→ 05 官号动态整页 ─→ 06 KOL 影响力 ─→ 07 KOL 详情 ─┐
                          │                     │                                                    │
02 Playwright harness ────┴─→ 04 六态守卫 ──────┴─→ 08 市场域基础组 ─→ 09 市场域叙述组 ─┬───────────┤
                                                                                        │            │
                                                                    10 监控·证据 ─→ 11 监控·K线阶段 ─┴─→ 12 收口·拔镜像
```

01 与 02 无阻塞，可并行开工。

## 契约函数覆盖（累计 24）

| 工单 | 新增函数 | 累计 |
|---|---|---|
| 01 | `buildRange` `officialPosts` | 2 |
| 05 | `etfMentionsFor` | 3 |
| 06 | `kolImpact` `kolProfile` | 5 |
| 07 | `kolOpinions` | 6 |
| 08 | `pool` `ranks` `benchmark` `observe` + `delta`（形状） | 11 |
| 09 | `summaryFor` `hotSummaryFor` `themesFor` `negCatsFor` `competitorsFor` `complianceFor` | 17 |
| 10 | `topicsFor` `evidenceFor` `kolMentionsFor` | 20 |
| 11 | `candlesFor` `stagesFor` `heatSeriesFor` `dailyFor` | **24** |

## 为什么是 expand–contract

数据门面是一次**宽重构**：一口气把 `window.RADAR` 换成 API，五个屏幕会同时变红，没有一张工单能单独落绿。所以 01 先让门面变成**混合态**（一个函数走 API，其余仍读设计源镜像），05–11 逐组迁移，12 才删掉镜像 import。中途每一张都保持绿灯。
