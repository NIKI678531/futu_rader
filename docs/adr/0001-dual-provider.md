# ADR-0001 — 后端双 provider 供数

- **状态**：已接受
- **日期**：2026-09-09
- **相关**：[ADR-0006](0006-dual-acceptance-criteria.md)、[ADR-0008](0008-dump-import-and-slim-db.md)、[ADR-0010](0010-annotations-and-ai-pipeline.md)、[ADR-0016](0016-sqlite-local-mysql-prod.md)

## 背景

第一期的目标是「后端立起来 ＋ 五个页面全部接 API」，同时客户提供了一份 10.3 GB 的生产库 dump（`market_insight`，MySQL 8 / AWS RDS）作为真实数据来源。

两件事同时成立，且互相冲突：

1. 五个页面必须与 `design/*.dc.html` **100% 还原**——设计稿上每一个模块都得有值。
2. 真实数据里，态度、观点主题、动态负面类别、帖子双标签、AI 摘要、重点舆情这些字段**在原始 schema 里一个都不存在**，要靠 AI 标注管线产出。

若一刀切到真实数据，市场域两个屏在标注管线跑完之前会大面积「暂不可用」，「五个页面全部接 API」当场变成「五个页面全部显示暂不可用」，无从验收。

## 决策

后端提供**两个 provider**，端点与响应外形**完全一致**，只有底下的数据来源不同：

| provider | 数据来源 | 职责 |
|---|---|---|
| `demo` | 由 `design/radar-data.js` 导出的 fixture | 保证五屏 **100% 还原**，可验收 |
| `mysql` | 瘦库（真实数据 ＋ `annotations`） | 保证**诚实**：有的字段给真值，没有的走六态 |

> **2026-09-10 更名**：`mysql` → **`sql`**（`backend/providers/sql.py`）。本地跑的是 SQLite，叫 mysql 会让人以为本地也得起一个 MySQL —— 见 [ADR-0016](0016-sqlite-local-mysql-prod.md)。`DATA_PROVIDER=mysql` 作为别名保留。本 ADR 及 0006／0007／0008／0010／0012 里的 `mysql` provider 一律指它。

部署形态：**同一镜像、两个 compose 服务、不同环境变量与端口**——`backend-demo:8008`、`backend-mysql:8009`。前端用 `VITE_API_BASE` 指向其中之一。

## 理由

- 两套验收标准（见 [ADR-0006](0006-dual-acceptance-criteria.md)）**本来就要同时跑**：一边做像素 diff，一边看真实数据诚不诚实。单进程 ＋ 环境变量切换要来回重启，比对没法做。
- 这与仓库已有的惯例同构：`npm run dev`(5173) 与 `npm run design`(5174) 就是并排开着比对的。
- 前端**一个字都不用改**——它只看端点契约。真实通道成熟后停掉 `demo` 服务即可，不改前端、不改契约。

## 后果

- compose 多一个服务；镜像只有一个，构建成本不变。
- 演示时必须**说清楚当前看的是哪个 provider**。截图、录屏、汇报材料都要标注，否则 `demo` 的漂亮数字会被误当成真实结论——这是本决策最大的风险。
- `backend/core/` 的口径实现只有一份，两个 provider 共用；provider 只负责**取数**，不碰公式（铁律 1）。

## 否决的备选

- **一刀切真实数据**：见背景，验收无从谈起。
- **dump 只作字段对照、供数仍走 fixture**：第一期结束时仍然不知道真实通道通不通，把风险整体推到第二期。
- **单进程 ＋ 请求级参数切换**：会让「这个数从哪来」变成运行时状态，排查困难，且容易漏进生产。
