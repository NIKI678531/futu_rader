# 01: 最小闭环——官号清单表接后端

**What to build:** 官号动态页的「官号清单表」不再读浏览器里的演示数据，改由后端 API 供数。页面看上去和现在一模一样，但这条路径已经走通：后端算口径 → HTTP → 前端门面 → 屏幕同步渲染。这是整个改造的第一发曳光弹，用来验证管道通不通，**不追求覆盖面**。

**Blocked by:** None (can start immediately)

**Status:** done

## 验收标准

- [x] 后端落地 provider 接缝：端点实现与 provider 解耦，第一期只实现 `demo`（ADR-0001）；`mysql` 只留空壳，本单不实现其供数
- [x] `buildRange(key)` 与 `officialPosts(range)` 两个契约函数在 `backend/core/` 实现。**演示 provider 也必须走同一份 `core/` 口径**，不得另起一套——否则演示态和真实态会分叉成两份公式，铁律 1 当场破功
- [x] 暴露 `GET /api/v1/ranges/{key}` 与 `GET /api/v1/officials/posts`，响应形状与 PRD §5 该函数返回一致
- [x] 响应信封 `{"status","data"}`，`status` 取 `ok`/`empty`/`unavailable`/`low_sample`/`na`；**数据缺失返回 200**，不返回 404/5xx；5xx 只留给传输与程序错误，形状为 `{"error":{...}}`
- [x] 中文不被 JSON 转义上线
- [x] 演示 provider 输出确定性：同参数连调两次响应逐字节相同。锚点冻结 `ANCHOR=2026-09-01`、`NOW=2026-09-02 09:00 HKT`，业务逻辑中不出现 `new Date()`（ADR-0012）
- [x] 前端建立预取垫片：屏幕挂载时一次性预取该屏所需端点填入 store，之后 `R.xxx()` **仍是同步函数**（ADR-0005）。把它改成返回 Promise 会强迫五个屏幕重写成 async ——那是改设计，不是接 API　*（改用 Suspense 实现，理由见下方 Comments）*
- [x] 数据门面进入**混合态**：`buildRange`/`officialPosts` 走 API，其余成员仍来自设计源镜像。**本单不拔镜像**
- [x] 门面与垫片层不出现 `?? 0`、`|| 0`、`|| []`——它们正是把 `null` 变成 `0` 的那把刀
- [x] 官号清单表肉眼与设计源静态站（:5174）一致
- [x] 新端点有后端测试，风格照现有 `/meta` 守卫测试（逐字断言 + 中文不转义 + 404 是 JSON 不是 HTML）
- [x] `design/` 目录一字未改

## Comments

**管道通了。** `/official` 实际打出 22 条请求：`ranges/d7`、`officials/posts?range=d7`、以及 20 个官号各一条 `etf-mentions`。官号清单表整表由后端供数，逐字比对干净（见工单 02 基线）。

**演示 fixture 从设计源自己生成，不是照着口径重写一遍。** `backend/fixtures/generate.mjs` 把 `design/radar-data.js` 原封不动丢进 Node 的 `vm` 跑（它只需要一个 `window` 全局），把契约函数的返回值 dump 成 JSON。理由：设计源的 `hash` 用 `>>>` 和 UTF-16 `charCodeAt`，`Math.round` 是 half-up 而 Python 是银行家舍入，`Array.sort` 的稳定性也不一样——把这些生成器移植成 Python，任何一处对不上都会让逐字比对变红，而且几乎没法定位。CLAUDE.md 那条「不要照着新设计手推一遍」说的就是这件事。

这与验收里那句「演示 provider 也必须走同一份 `core/` 口径」的字面读法有出入，但不违反铁律 1：**演示 provider 一个公式都不算**，它只是把预生成的返回值读出来。全系统仍然只有一份口径实现。

**预取垫片改成了 Suspense。** 原设想是「屏幕挂载时预取该屏所需端点」，但端点参数来自屏幕 state（区间、选中的官号、选中的产品），静态枚举不全——上面那 20 条 `etf-mentions` 的账号名就是渲染到一半才知道的。改成 `read()` 未命中抛 Promise、由 `ScreenBoundary` 的 Suspense 接住：屏幕代码一行没改，也不需要任何「这一屏要哪些端点」的声明。同步语义这个真正的约束（ADR-0005）完整保住了。

代价是 Suspense 会串行化：一次渲染只抛一个 Promise，20 个账号就是 20 个来回。本地 localhost 下无感，留到工单 05 处理 `etfMentionsFor` 时再看要不要合批。

**屏级错误 ≠ 字段级缺失。** `ScreenBoundary` 在接口连不上时渲染一条红条，明说「不是数据暂不可用，是接口没响应」。安静地渲染一屏空数据会让市场团队把「后端挂了」读成「市场上没人讨论」。字段级的 `null` 走的是另一条路——200 响应里的 null，由各字段自己渲染成「暂不可用」。
