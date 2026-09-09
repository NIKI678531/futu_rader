# Spec — 第一期：后端实现 PRD §5 全部契约函数，五个页面全部走 API

- **Status**: `ready-for-agent`
- **日期**：2026-09-09
- **范围**：`backend/`（24 个契约函数，演示 provider 供数）＋ `frontend/src/data/radar.js`（改造成 API 门面）＋ 五个页面全部走 API
- **上位文档**：[docs/PRD.md](../PRD.md) v2.0（效力最高，「」内文字逐字具约束力）、[CLAUDE.md](../../CLAUDE.md) 三条铁律、[CONTEXT.md](../../CONTEXT.md) 术语表
- **已定案**：[docs/adr/](../adr/) 0001–0014（本 spec 是它们的施工版，不重新讨论已定的事）

---

## Problem Statement — 问题陈述

五个页面现在**能跑但不真**。数据全部来自 `design/radar-data.js` 这个演示数据文件——它在浏览器里以 IIFE 形式挂到 `window.RADAR`，`frontend/src/data/radar.js` 只是把它 re-export 出去（整个文件 12 行）。后果是三件具体的坏事：

1. **接不上真实数据。** 客户已经提供了 10GB 的 `market_insight` 生产 dump（120/120 个产品代码全部命中，见 [ADR-0008](../adr/0008-dump-import-and-slim-db.md)），但现在没有任何一条路径能把它送到页面上。后端只有 `GET /api/v1/meta` 一个端点。

2. **铁律 1 现状是被违反的。** 讨论热度（PRD §3.3）、提及去重（§3.2）、基准区间（§3.1）、情绪净值、赞踩比、环比百分比、阶段合并、样本阈值——这些口径公式此刻**实际活在前端 JS 里**。`backend/core/` 是空的。只要公式还在前端，「全系统唯一定义」就是一句没有落地的话，客户也没法对着一处 SQL 逐条核对口径。

3. **六态（PRD §3.6）无法被验证。** 演示数据是自洽生成的，永远不缺字段，所以「字段级 `null` 一律走暂不可用、绝不落成 0」这条铁律在当前代码里**从来没有被真正执行过一次**。真实数据一接上，大量 AI 派生字段会是 `null`——那一刻才发现渲染成了 `0`，就是在向产品团队撒谎（`0` 是「已取得数据且确实为零」的专用值）。

同时还有两处已知的具体违规：`OfficialActivity.jsx` 直接调 `R.hash`、`KolDetail.jsx` 直接调 `R.addDays`——演示数据生成器泄漏进了屏幕代码（[ADR-0004](../adr/0004-radar-member-split.md) 要求它们从屏幕里消失）。

## Solution — 解决方案

后端把 PRD 第 5 章的 **24 个契约函数**实现一遍，第一期用**演示 provider** 供数（`demo`），响应形状与函数返回完全一致。前端 `frontend/src/data/radar.js` 从「re-export `window.RADAR`」改造成 **API 门面**：**函数签名一个都不变**，屏幕里的 `R.observe(code, range)` 等调用点原封不动，改的只是这些函数背后从哪儿取数。

对使用者（产品／市场团队）来说，第一期上线当天**页面看起来一模一样**——这正是验收标准（可见输出等价）。变化在于：从这一刻起，页面上每一个数字都是后端算出来再下发的，`mysql` provider 一接上，同一批页面立刻显示真实舆情，前端不动一行。

## User Stories — 用户故事

### 产品／市场团队（页面使用者）

1. 作为 ETF 产品团队成员，我希望板块总览、产品监控、KOL 影响力、KOL 详情、官号动态五个页面在接 API 之后**与现在肉眼完全一致**，这样我不需要重新学习任何东西，也不必怀疑改造过程中悄悄改了口径。
2. 作为产品团队成员，我希望页面上显示 `0` 的地方**一定意味着「真的是零」**，这样我看到「近 7 天负面评论 0 条」时可以放心，而不用去问「是零还是没采到」。
3. 作为产品团队成员，我希望字段没取到时页面明确写「数据暂不可用」而不是留白或补零，这样我知道该去追数据源，而不是误以为产品无人讨论。
4. 作为产品团队成员，我希望样本不足 10 条时页面直接告诉我「有效产品态度评论少于 10 条，不输出倾向结论」，这样我不会拿 3 条评论去开产品会。
5. 作为产品团队成员，我希望 AI 判断置信度不够时标「待确认」而不是硬给一个结论，这样我知道哪些结论需要人工复核。
6. 作为产品团队成员，我希望某个产品的**全市场评论量排名在我切换板块筛选时保持不变**，这样「第 12 名」是一个我能对外引用的稳定事实，而不是随手一点就变的数字。
7. 作为产品团队成员，我希望热力图色阶在筛选前后保持同一把标尺，这样两次截图之间颜色深浅可比。
8. 作为市场团队成员，我希望后端挂了的时候页面明确告诉我「服务连不上」，而不是安静地显示一屏空数据让我误判市场没声音。
9. 作为市场团队成员，我希望页面上印着的数据更新时间是后端下发的真实时间，这样我知道自己看的是什么时候的快照。

### 后端工程师

10. 作为后端工程师，我希望 PRD §5 的每个契约函数对应一个明确的端点、响应形状与函数返回一致，这样我不用逐个去猜前端要什么形状。
11. 作为后端工程师，我希望所有口径公式只在 `backend/core/` 实现一份，这样口径变更只有一个落点，不会前后端各改一半。
12. 作为后端工程师，我希望口径以**手写 SQL** 的形式存在而不是散进 Python 循环，这样评审时能整段贴出来对着 PRD 第 3 章逐条核。
13. 作为后端工程师，我希望数据缺失一律返回 **HTTP 200 + status 枚举**，这样「没有数据」和「服务出错」在协议层就是两件事，前端不用靠猜。
14. 作为后端工程师，我希望 provider 是可切换的（`demo` / `mysql`），这样同一份端点实现既能做 100% 还原验收，又能接真实库。
15. 作为后端工程师，我希望演示 provider 的输出是**确定性**的（同输入同输出），这样 Playwright 逐字比对才可能通过，测试也不会随机抖动。
16. 作为后端工程师，我希望常量集合（预设区间、板块、阈值、状态图例、热度公式）由 `/meta` 一次性下发，这样阈值调整不需要发前端版本。

### 前端工程师

17. 作为前端工程师，我希望 `radar.js` 改造后**函数签名完全不变**，这样五个屏幕的调用点一处都不用改，改造范围可控、回归面可控。
18. 作为前端工程师，我希望屏幕代码保持**同步调用**（`R.observe(...)` 直接返回值，不是 Promise），这样我不必把五个屏幕全部改写成 async 渲染——那才是真正会破坏还原度的改动。
19. 作为前端工程师，我希望门面层**禁止任何默认值兜底**（不写 `?? 0`、`|| 0`、`|| []`），这样后端的 `null` 能原样到达渲染层并触发「暂不可用」，而不是被前端悄悄抹平。
20. 作为前端工程师，我希望演示数据生成器（`hash`/`rnd`/`pick`/`pickN`/`addDays`）从屏幕代码里彻底消失并有自动检查兜底，这样不会有人在演示期之后还在前端造数。
21. 作为前端工程师，我希望展示助手（`rgba`/`typeStyle`/`num`/`shell` 等纯渲染函数）留在前端，这样不会为了纯样式逻辑去打一趟网络。
22. 作为前端工程师，我希望「网络错误」与「字段暂不可用」在 UI 上是两种不同的东西——前者是屏级错误条，后者是字段级文案，这样用户能区分「系统坏了」和「这个数没有」。

### 客户 / 评审人

23. 作为 CSOP 评审人，我希望能同时打开设计源静态站和 React 版做并排比对，并有机器化的逐字差异报告，这样「100% 还原」是可验证的说法而不是口头承诺。
24. 作为 CSOP 评审人，我希望六态的每一态在验收时都有一个**真实可见的样例**，这样我能确认缺失态的文案确实按 PRD §3.6 逐字落地，而不是只在文档里存在。

### 后续接手的 AFK agent

25. 作为接手的 agent，我希望页面按依赖数量从少到多的顺序逐屏接入，这样每一步都有独立可验收的产出，出问题时定位面很小。
26. 作为接手的 agent，我希望每个端点都有对应的后端测试，这样我改口径实现时能立刻知道有没有踩到已定的契约。

---

## Implementation Decisions — 实现决策

### D1. 后端契约面：24 个函数 → 23 个端点 + `/meta`

PRD §5 逐字规定「生产实现时每个函数对应一个（组）REST 端点，**响应形状与函数返回一致**」。24 个契约函数的落点：

| # | 契约函数 | 端点（`/api/v1` 前缀） | 消费页面 |
|---|---|---|---|
| 1 | `buildRange(key)` | `GET /ranges/{key}` | 官号 / KOL影响力 / 总览 / 监控 |
| 2 | `observe(code, range, salt?)` | `GET /products/{code}/observe` | 总览 / 监控 |
| 3 | `pool(range)` | `GET /pool` | 总览 |
| 4 | `ranks(range)` | `GET /ranks` | 总览 / 监控 |
| 5 | `benchmark(code, range)` | `GET /products/{code}/benchmark` | 总览 / 监控 |
| 6 | `delta(cur, base)` | **不是端点** —— 见 D4 | 总览 / 监控 |
| 7 | `summaryFor` | `GET /products/{code}/summary` | 总览 / 监控 |
| 8 | `hotSummaryFor` | `GET /products/{code}/hot-summary` | 总览 |
| 9 | `themesFor(code, range, polarity)` | `GET /products/{code}/themes` | 总览 / 监控 |
| 10 | `negCatsFor` | `GET /products/{code}/negative-categories` | 总览抽屉 |
| 11 | `topicsFor` | `GET /products/{code}/topics` | 监控 |
| 12 | `competitorsFor` | `GET /products/{code}/competitors` | 总览 / 监控 |
| 13 | `evidenceFor(code, ctxKey, polarity, n)` | `GET /products/{code}/evidence` | 证据侧栏 |
| 14 | `complianceFor` | `GET /products/{code}/compliance` | 总览 / 监控 |
| 15 | `kolMentionsFor` | `GET /products/{code}/kol-mentions` | 监控 |
| 16 | `candlesFor` | `GET /products/{code}/candles` | 监控 |
| 17 | `stagesFor` | `GET /products/{code}/stages` | 监控 |
| 18 | `heatSeriesFor` | `GET /products/{code}/heat-series` | **无屏幕直接调用**（`stagesFor` 的输入） |
| 19 | `dailyFor(code\|'ALL')` | `GET /products/{code}/daily` | **无屏幕直接调用**（底层序列） |
| 20 | `kolImpact(range)` | `GET /kols/impact` | KOL 两页 |
| 21 | `kolProfile(name, posts, campFn?)` | `GET /kols/{name}/profile` | KOL 两页 |
| 22 | `kolOpinions(kol, range)` | `GET /kols/{name}/opinions` | KOL 详情 |
| 23 | `officialPosts(range)` | `GET /officials/posts` | 官号 |
| 24 | `etfMentionsFor(account, range)` | `GET /officials/{account}/etf-mentions` | 官号 |
| — | 常量集合 | `GET /meta`（**已实现**） | 全部 |

23 个端点 + `/meta`。`heatSeriesFor` 与 `dailyFor` 必须实现并暴露（PRD §5 要求 1:1），但第一期没有屏幕直接调用它们——它们是 `stagesFor` / `observe` 的上游输入。门面层可以不为这两个提供包装函数。

**不做页面级聚合端点**（[ADR-0003](../adr/0003-endpoint-granularity.md)）：六态是**字段级**的，聚合端点会逼着我们在一个响应里表达十几个字段各自的状态，那等于把状态语义重新发明一遍。

### D2. 响应信封与状态语义

- 成功：HTTP 200，`{"status": "...", "data": ...}`；`data` 的形状 == PRD §5 该函数的返回。
- `status` 枚举：`ok` / `empty` / `unavailable` / `low_sample` / `na`（PRD §3.6）。
- **数据缺失永远是 200**，不是 404、不是 5xx。5xx 只留给传输／程序错误，形状为 `{"error": {...}}`。
- 字段级 `null` ＝「暂不可用」；空数组 ＝「暂无内容」。后端**不得**把 `null` 写成 `0`，也不得把「无数据」写成空字符串。
- 中文不得被 JSON 转义上线（`ensure_ascii=False`），已有守卫测试。

### D3. `frontend/src/data/radar.js` → API 门面（签名不变）

现状 12 行：`import 'design/radar-data.js'` → `export default window.RADAR`。改造后它成为门面模块，对外仍 `export default R`，`R` 上的成员名与签名**逐个不变**。三类成员分流（[ADR-0004](../adr/0004-radar-member-split.md)）：

| 类别 | 举例 | 去向 |
|---|---|---|
| **契约函数** | `observe` `ranks` `themesFor` `kolImpact` … | 走 API；签名不变，实现改为读取已预取的响应 |
| **口径常量** | `PRESETS` `SECTORS` `LOW_SAMPLE` `HEAT_FORMULA` `HEAT_NOTE` `STATUS_LEGEND` `HOT_RULE` `STAGE_RULE` `TYPE_RULE` `ETF_MENTION_RULE` `ORDER` `UPDATED` | 由 `GET /meta` 下发 |
| **展示助手** | `rgba` `typeStyle` `dirStyle` `shell` `navGroups` `md` `dowOf` `num` `pct1` `shortName` | **留在前端**，从设计源拷进 `frontend/src/lib/view/`（纯渲染，打网络毫无意义） |
| **演示生成器** | `hash` `rnd` `pick` `pickN` `addDays` | **必须从屏幕代码消失**（现存两处违规：`OfficialActivity.jsx` 用 `R.hash`、`KolDetail.jsx` 用 `R.addDays`） |

### D4. `delta()` 一分为二

`delta` 是环比字段的**形状**，不是端点。后端在各端点内嵌返回 `{abs, pct, dir}`，输入缺失时整体返回 `null`；前端只负责把它渲染成 `text` / `short`：

- 有值 → 前端渲染成设计源既有的文案形态。
- `null` → 数值位与环比位渲染**长文案**「数据暂不可用」，短徽章位渲染「暂不可用」。

**前端不得自己算环比**（铁律 1）。

### D5. 同步垫片（Sync Shim）——保住 100% 还原的关键

屏幕代码是同步渲染的。把 `R.xxx()` 改成返回 Promise 会强迫五个屏幕全部重写成 async，那是**改设计**，不是接 API。

因此：屏幕挂载时 `await loadScreen(<screen>, <params>)` 一次性预取该屏所需的全部端点，填入 `RadarStore`；之后 `R.xxx(...)` 依然是**同步函数**，从 store 读已到的响应（[ADR-0005](../adr/0005-sync-shim-and-fidelity.md)）。

**垫片里禁止出现 `?? 0`、`|| 0`、`|| []` 这类兜底**——它们正是把 `null` 变成 `0` 的那把刀。

好消息：`productMonitor/` 与 `sectorOverview/` 的所有子组件**完全不碰 `R.*`**（数据全部由 `index.jsx` 以 props 下传）。所以接线点只有 **5 个**：三个扁平屏 + 两个 `index.jsx`。

### D6. 错误分层

| 情况 | 表现 |
|---|---|
| 网络不通 / 5xx / 预取失败 | **屏级**错误条，明确说服务连不上 |
| 字段 `null` | **字段级**「数据暂不可用」／「暂不可用」 |
| 空数组 | 「暂无内容」／空态「暂无相关内容」 |

两者不可混用。三套缺失文案**并存且不可互换**（CLAUDE.md 铁律 2、CONTEXT.md §3）：状态图例与短徽章用「暂不可用」／「暂无内容」；数值位与环比位用「数据暂不可用」；空态用「暂无相关内容」。页面级专用文案（价格、传播关系、阶段观点等）按字段单独对齐 PRD 第 4 章。

### D7. 第一期供数 = `demo` provider

后端端点从**演示 provider** 取数：确定性哈希生成（`hash`/`rnd`/`pick`/`pickN`），演示锚点冻结在 `ANCHOR = 2026-09-01`、`NOW = 2026-09-02 09:00 HKT`（[ADR-0012](../adr/0012-frozen-demo-anchor.md)）。**业务逻辑里出现 `new Date()` 即视为违规**——它会让 Playwright 逐字比对在跨日时随机失败。

`mysql` provider 同镜像、同端点、另一个 compose 服务（[ADR-0001](../adr/0001-dual-provider.md)），前端靠 `VITE_API_BASE` 切换。它的**真实供数不在本 spec 范围内**（见 Out of Scope）。

### D8. 全市场排名由后端算

`GET /ranks` 基于**完整活跃 ETF 池**计算并下发排名 map + total。板块筛选、范围、搜索、开关**只改变可见范围，不重算排名，也不重算热力图色阶标尺**（PRD §4.1 逐字、§3.8、铁律 3）。前端只过滤显示。

例外：KOL 声量排名是另一套指标，不受此条约束（PRD §4.3 M5）。

### D9. 接入顺序（按依赖数量升序，[ADR-0007](../adr/0007-rollout-order.md)）

| 序 | 页面 | 路由 | 契约函数依赖数 | 依赖 |
|---|---|---|---|---|
| 1 | 官号动态 | `/official` | 3 | `buildRange` `officialPosts` `etfMentionsFor` |
| 2 | KOL 影响力 | `/kol` | 3 | `buildRange` `kolImpact` `kolProfile` |
| 3 | KOL 详情 | `/kol/detail` | 3 | `kolImpact` `kolProfile` `kolOpinions` |
| 4 | 板块总览 | `/sector` | 11 | `buildRange` `observe` `pool` `ranks` `benchmark` `summaryFor` `hotSummaryFor` `themesFor` `negCatsFor` `competitorsFor` `complianceFor`（+ `delta` 形状） |
| 5 | 产品监控 | `/product` | 13 | `buildRange` `observe` `ranks` `benchmark` `summaryFor` `themesFor` `topicsFor` `competitorsFor` `evidenceFor` `complianceFor` `kolMentionsFor` `candlesFor` `stagesFor`（+ `delta` 形状） |

每一屏接完即可独立验收，不必等下一屏。

### D10. 数据访问层

`backend/core/` 里口径以**手写 SQL** 存在，通过 **SQLAlchemy Core**（不用 ORM）执行（[ADR-0014](../adr/0014-data-access-layer.md)）。数据库为 **MySQL 8**（[ADR-0002](../adr/0002-mysql-over-clickhouse.md)，已推翻 plan.md §6 Q4 的 ClickHouse 决定）。`worker/` **不得重算任何口径**（[ADR-0009](../adr/0009-worker-scope.md)）。

`demo` provider 不走 SQL，但**必须走同一个 `core/` 口径函数**——否则演示态和真实态会分叉成两份公式，铁律 1 当场破功。

---

## Testing Decisions — 测试策略

**什么是好测试**：只测**外部可见行为**——用户看得见的文字、端点返回的 JSON。不测内部实现（不测某个 helper 被调了几次、不测 store 内部结构、不 mock 内部模块）。口径公式的正确性通过端点响应验证，不通过直接调用私有函数验证。

**缝越少越好**。本 spec 用 **2 条缝**，都尽可能高。

### 缝 1（新增，最高缝）：Playwright `textContent` 逐字比对

**这是「100% 还原」唯一可机器验证的形式**（[ADR-0006](../adr/0006-dual-acceptance-criteria.md)）。

- 同时起 `npm run dev`（:5173，React 版走 API）与 `npm run design`（:5174，设计源静态镜像）。
- 五个页面逐个抓 `textContent`，归一化空白后 diff。
- 差异必须为空，或落在 README 已豁免的 4 处偏差之内。
- 一条缝覆盖全部五屏 + 全部渲染路径。**不写组件级 React 单测**——它们测的是实现细节，且对「还原度」这个真正的验收目标一点保障都没有。

前端目前没有测试基建（`frontend/package.json` 无 test 脚本、无 vitest／playwright）。这条缝需要新建，是本 spec 唯一的新基建。

### 缝 2（已有，沿用）：Flask `app.test_client()`

**现成缝，有先例**：`backend/tests/conftest.py` 提供 `client` fixture，`backend/tests/test_meta.py` 已有 10 条守卫测试，包括逐字断言 `STATUS_LEGEND`、逐字断言 `HEAT_FORMULA`、`test_chinese_is_not_escaped_on_the_wire`、以及 404 必须是 JSON 而不是 HTML。**新端点测试照抄这个风格。**

每个新端点至少覆盖：

1. **形状**：200 + `{"status", "data"}`，`data` 形状与 PRD §5 该函数返回一致。
2. **六态**：见下。
3. **确定性**：同参数连调两次，响应逐字节相同（演示 provider 的哈希生成必须可复现）。
4. **口径守卫**：涉及热度／去重／基准区间／环比的端点，断言其逐字口径说明与 PRD 第 3 章一致（照 `test_meta.py` 已有的逐字断言写法）。

### 六态边界测试（**本 spec 的硬要求**）

六态每一态都要在测试里**钉住一个样例**，正态与缺失态都要：

| 态 | 断言 |
|---|---|
| `0` | 确实有数据且为零 → 渲染 `0`。**必须有一条测试证明真零仍然显示 0**，否则「一律不显示 0」会矫枉过正。 |
| `—` | 结构性不适用 → 渲染 `—`。 |
| 暂不可用 | 后端字段为 `null` → 短徽章「暂不可用」、数值位与环比位「数据暂不可用」。**断言页面上不出现 `0`、不出现空白。** |
| 暂无内容 | 后端空数组 → 「暂无内容」；空态区块「暂无相关内容」。 |
| 样本不足 | 有效产品态度评论 < `LOW_SAMPLE`(10) → 不输出倾向结论，显示对应逐字文案。 |
| 待确认 | AI 标注 `confidence < lowConfidence`(0.7) → 「待确认」。 |

**核心红线断言**：对每个可为 `null` 的字段，构造 `null` 响应，断言渲染结果**既不是 `0` 也不是空串**。这是铁律 2 的唯一自动化护栏。

### 静态守卫（跑在测试套件里的 grep 断言）

三条，都是廉价高价值：

1. `frontend/src/screens/**` 中**不得出现** `R.hash` / `R.rnd` / `R.pick` / `R.pickN` / `R.addDays`（[ADR-0004](../adr/0004-radar-member-split.md)）。
2. 门面与垫片层**不得出现** `?? 0` / `|| 0` / `|| []`（[ADR-0005](../adr/0005-sync-shim-and-fidelity.md)）。
3. `backend/` 与 `frontend/` 的**业务逻辑**中不得出现 `new Date()` / `datetime.now()`（[ADR-0012](../adr/0012-frozen-demo-anchor.md)）；展示层格式化除外。

### 每页验收标准

**通用（五页共同）**：
- 该页所需端点全部 200，无 5xx。
- 与 :5174 设计源镜像的 `textContent` diff 为空（或仅落在 README 已豁免的 4 处偏差内）。
- 该页出现的所有 `0` 都能追溯到一个真实为零的后端字段。
- 断开后端 → 出现屏级错误条，**不是**一屏空数据、**不是**满屏 `0`。
- 屏幕代码中不残留任何演示生成器调用。

**1. 官号动态 `/official`**
- `officialPosts(range)` 与 `etfMentionsFor(account, range)` 驱动全部内容；`buildRange` 由 `/meta` + `/ranges/{key}` 供给。
- 现有的 `R.hash` 调用被移除，且移除后页面输出逐字不变。
- 阵营三分互斥语义保持（PRD O4，[ADR-0013](../adr/0013-prd-open-items-o1-o8.md)：**不与 KOL 页统一**）。
- 「提及 ETF」按 `ETF_MENTION_RULE`（出现次数累加）——**注意与市场域评论去重语义相反，不得混用**。

**2. KOL 影响力 `/kol`**
- `kolImpact(range)` / `kolProfile(name, posts, campFn?)` 驱动列表与画像。
- 帖子类型双标签按 `TYPE_RULE` 逐字：内容形式必有一枚；操作方向仅在表达了明确操作时出现；判不出方向标「方向待确认」（PRD O1）。
- **KOL 声量排名不受铁律 3 约束**（PRD §4.3 M5），筛选行为与全市场评论量排名不同，验收时不要按同一条规则去卡。
- 阵营筛选「提自家」**含 both**（与官号页不同，这是设计而非 bug）。

**3. KOL 详情 `/kol/detail`**
- `kolImpact` / `kolProfile` / `kolOpinions(kol, range)` 驱动。
- 现有的 `R.addDays` 调用被移除，且移除后页面输出逐字不变。
- 入口只从 KOL 影响力「详情 →」进入，**不进一级导航**。
- `dominantAttitude` 在样本 < 3 条时为 `null` → 渲染「暂不可用」，**不得**渲染 `0` 或空白（PRD §5 状态语义逐字点名了这个字段）。

**4. 板块总览 `/sector`**
- 11 个契约函数全部走 API。
- **排名稳定性验收**：任选一个产品，记下其全市场评论量排名 → 切换板块筛选 → 排名数字不变；再切范围、切搜索、切开关 → 同样不变。
- **色阶标尺验收**：热力图在筛选前后色阶标尺不变（同一格颜色不因可见集变化而变）。
- 六态图例区块与 `/meta` 下发的 `STATUS_LEGEND` 逐字一致。
- S6 口径说明块（评论去重）逐字保留。
- `HEAT_FORMULA` / `HEAT_NOTE` 逐字来自 `/meta`，**前端不得硬编码**。

**5. 产品监控 `/product`**
- 13 个契约函数全部走 API，含证据侧栏 `evidenceFor(code, ctxKey, polarity, n)` 的四个参数全链路。
- **K 线 OHLC 缺失时字段为 `null`** → 走「暂不可用」；这是 PRD §5 状态语义逐字点名的第二个字段，**绝不显示 0**。
- `stagesFor` 的阶段合并口径来自后端（`stageHalfDay = 5`），前端不得自行合并。
- `candlesFor` 与舆情序列共用同一基准区间与时间桶——**桶由后端下发，前端不自行算桶**（PRD §5 逐字）。
- `delta` 为 `null` 时环比位显示「数据暂不可用」。
- 样本 < 10 时显示「有效产品态度评论少于 10 条，不输出倾向结论」逐字文案。

---

## Out of Scope — 不在本 spec 范围

- **`mysql` provider 的真实供数**。架构（[ADR-0001](../adr/0001-dual-provider.md)）与验收标准（[ADR-0006](../adr/0006-dual-acceptance-criteria.md) 的三条诚实性检查）已定，但本 spec 只交付 `demo` 供数。
- **10GB dump 导入与瘦库派生**（[ADR-0008](../adr/0008-dump-import-and-slim-db.md)），含其两条导入后验证。
- **AI 标注管线**（[ADR-0010](../adr/0010-annotations-and-ai-pipeline.md)）：`annotations` 表、Claude Haiku 4.5 调用、摘要语言策略。第一期要做，但不是本 spec。
- **`worker/` 的采集实现**（[ADR-0009](../adr/0009-worker-scope.md)）：`raw_json` → 四张原始事实表。
- **传播关系图**（PRD O7）：数据源层面就没有「谁转了谁」的字段，第一期不做，相关字段走「关系数据暂不可用」。
- **关注 / 指派 / 处置 / 预警推送 / 告警 / 工单**：本系统是**只读**工作台，这些词不属于本系统（CONTEXT.md §5）。
- **前端容器镜像**（plan.md Q5：不做，本地 `npm run dev` 即可）。
- **`docs/architecture.md`**：待补，不由本 spec 交付。
- **阈值调整**（`LOW_SAMPLE=10`、`lowConfidence=0.7`）：保留现值，等真实分布数据再谈（[ADR-0013](../adr/0013-prd-open-items-o1-o8.md) O5/O6）。
- **组件级 React 单测**：明确不做，理由见 Testing Decisions 缝 1。

---

## Further Notes — 其他说明

### 关于本文件的存放位置

`docs/agents/issue-tracker.md` 规定 spec 应发布到 `.scratch/<feature-slug>/spec.md`。**用户明确要求存 `docs/specs/`，以用户指示为准**。这处偏离是有意的，记录在此以免后人以为是疏忽。`.scratch/phase-1-api-integration/spec.md` 留了一个指回本文件的指路牌，tracker 约定不至于断掉。

### 工单

本 spec 已切成 **12 张工单**，在 `.scratch/phase-1-api-integration/issues/`（`01`–`12`，按依赖顺序编号，阻塞者在前），依赖图与契约函数覆盖表见 `.scratch/phase-1-api-integration/spec.md`。

门面改造是一次**宽重构**，因此按 expand–contract 排序：01 让门面变成混合态（一个函数走 API，其余仍读镜像），05–11 逐组迁移，12 才删掉镜像 import —— 中途每一张都能保持绿灯。

### 关于「24 个函数 / 23 组端点」的数字差异

`plan.md` §2.1 写的是「23 组」，本 spec 写「24 个契约函数」。两者都对，口径不同：PRD §5 表格有 24 个函数行 + 1 个常量行；`plan.md` 把 `delta()` 归为响应形状而非端点，又把 `summaryFor` / `hotSummaryFor` 并成一行。**以 PRD §5 的 24 个函数为实现清单，以 23 个端点 + `/meta` 为接口清单。**

### 仍待客户确认（不阻塞本 spec）

- **O1** 帖子类型双标签 vs 合并单标签的**形态**——第一期按双标签实现，`typeScheme` 开关保留。真正的截止时间是标注 schema 定稿前，不是现在。
- **O3** AI 摘要输出语言——实现默认值取「随原文语言」（真实数据繁简混杂，`raw_json` 自带 `original_lang`）。**这是实现默认值，不是替客户拍板。**
- **[ADR-0011](../adr/0011-comment-volume-caliber.md)** 的两项评论量口径细节。

### 数据安全（硬约束）

10GB dump 与任何派生瘦库含**真实用户昵称、IP 归属地、个人简介**。**不得进 git，不得粘进 issue 或聊天**。`.gitignore` 已挡 `*.sql`（放行 `init_db.sql`）与 `*.7z`。

### 与设计源的关系

`design/` 是逐字节只读镜像，**永远不要手改**。设计变更一律从设计源重新拷贝，不要照着新设计手推一遍（再导入流程见 README.md 的 *Re-importing from Claude Design*）。本 spec 的所有改动都发生在 `frontend/src/`、`backend/`，不触碰 `design/`。
