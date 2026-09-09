# 01: 最小闭环——官号清单表接后端

**What to build:** 官号动态页的「官号清单表」不再读浏览器里的演示数据，改由后端 API 供数。页面看上去和现在一模一样，但这条路径已经走通：后端算口径 → HTTP → 前端门面 → 屏幕同步渲染。这是整个改造的第一发曳光弹，用来验证管道通不通，**不追求覆盖面**。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

## 验收标准

- [ ] 后端落地 provider 接缝：端点实现与 provider 解耦，第一期只实现 `demo`（ADR-0001）；`mysql` 只留空壳，本单不实现其供数
- [ ] `buildRange(key)` 与 `officialPosts(range)` 两个契约函数在 `backend/core/` 实现。**演示 provider 也必须走同一份 `core/` 口径**，不得另起一套——否则演示态和真实态会分叉成两份公式，铁律 1 当场破功
- [ ] 暴露 `GET /api/v1/ranges/{key}` 与 `GET /api/v1/officials/posts`，响应形状与 PRD §5 该函数返回一致
- [ ] 响应信封 `{"status","data"}`，`status` 取 `ok`/`empty`/`unavailable`/`low_sample`/`na`；**数据缺失返回 200**，不返回 404/5xx；5xx 只留给传输与程序错误，形状为 `{"error":{...}}`
- [ ] 中文不被 JSON 转义上线
- [ ] 演示 provider 输出确定性：同参数连调两次响应逐字节相同。锚点冻结 `ANCHOR=2026-09-01`、`NOW=2026-09-02 09:00 HKT`，业务逻辑中不出现 `new Date()`（ADR-0012）
- [ ] 前端建立预取垫片：屏幕挂载时一次性预取该屏所需端点填入 store，之后 `R.xxx()` **仍是同步函数**（ADR-0005）。把它改成返回 Promise 会强迫五个屏幕重写成 async ——那是改设计，不是接 API
- [ ] 数据门面进入**混合态**：`buildRange`/`officialPosts` 走 API，其余成员仍来自设计源镜像。**本单不拔镜像**
- [ ] 门面与垫片层不出现 `?? 0`、`|| 0`、`|| []`——它们正是把 `null` 变成 `0` 的那把刀
- [ ] 官号清单表肉眼与设计源静态站（:5174）一致
- [ ] 新端点有后端测试，风格照现有 `/meta` 守卫测试（逐字断言 + 中文不转义 + 404 是 JSON 不是 HTML）
- [ ] `design/` 目录一字未改
