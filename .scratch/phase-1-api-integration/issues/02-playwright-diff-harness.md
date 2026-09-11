# 02: Playwright 逐字比对 harness 与五页基线

**What to build:** 一条命令跑完五个页面的 `textContent` 逐字比对（React 版 :5173 对设计源镜像 :5174），产出人可读的差异报告。**这是「100% 还原」唯一可机器验证的形式**，也是后续每一张页面工单的验收依据。前端目前没有任何测试基建，这是整份 spec 唯一的新基建。

**Blocked by:** None (can start immediately)

**Status:** done

## 验收标准

- [x] harness 能同时拉起两个站点并抓取五个路由的 `textContent`：`/official`、`/kol`、`/kol/detail`、`/sector`、`/product`
- [x] 设计源静态站没有 `index.html`，必须访问具体 `.dc.html` 路径；五个路由与设计源文件的对应关系以现有的文件名→路由映射为准
- [x] 归一化空白后 diff，输出人可读的差异报告（指出是哪一页、哪一段文字）
- [x] README 已记录的 4 处偏差进白名单，**白名单每一条须写明出处**，不允许出现无来由的豁免　*（逐条核对后白名单为空，理由见下方 Comments）*
- [x] 白名单之外差异为空时退出码 0，非空时非 0
- [x] **不写组件级 React 单测**（ADR-0006 / spec 测试策略）：它们测的是实现细节，且对「还原度」这个真正的验收目标一点保障都没有
- [x] 跑一次并把当前基线结果记录到本工单的 `## Comments`，作为后续对照
- [x] 若当前代码就跑不出干净 diff，**先把这件事记下来再继续**——那说明还原度问题在接 API 之前就已存在，与本次改造无关，不要让它污染后面每一张工单的验收

## Comments

**基线：五页逐字一致，退出码 0。** 命令 `cd frontend && npm run diff`，跑的是接完工单 01 之后的代码（`/official` 已经在读后端）：

```
✓ /official   官号动态      ←→  official-activity.dc.html   (998 段文字一致)
✓ /kol        KOL 影响力    ←→  kol-activity.dc.html        (530 段文字一致)
✓ /kol/detail KOL 详情      ←→  kol-detail.dc.html          (300 段文字一致)
✓ /sector     板块总览      ←→  sector-overview.dc.html     (693 段文字一致)
✓ /product    产品监控      ←→  product-monitor.dc.html     (632 段文字一致)
```

后面每一张页面工单的验收都是「这五行还全绿」。开工前的还原度没有历史欠账，所以之后**任何一处变红都是本次改造引入的**，不用再花时间分辨是不是老问题。

**比的是文本片段序列，不是一整坨字符串。** 按文档顺序取出每个非空文本节点（跳过 `<script>`/`<style>`/`<template>`），段内空白归一化。这仍是 `textContent` 的语义，但保住了节点边界，所以差异能报到「移植版第 417 段」而不是丢给你两坨几万字。对齐用 LCS，输出标出「只在移植版」／「只在设计源」。

**两边都不能靠 DOM ready 判断内容就绪**：设计源要等 `radar-data.js` 这个 `<script>` 跑完，移植版要等 Suspense 把接口数据取回来。harness 等 `document.body.textContent.length` 连续两次不变，比拍一个 sleep 靠谱。同时监听 `pageerror`，JS 报错直接判失败——不然一屏白页也可能「零差异」。

**白名单为空，这是核对后的结论，不是漏写。** README「Deliberate deviations」4 条逐条对照，全部是文本中性的：

| README 条目 | 会不会改变页面上的字 |
|---|---|
| 无异步等待轮询（同步 import 掉了等 `window.RADAR` 的轮询） | 不会。轮询期间设计源渲染空壳，稳定后一致；harness 等的就是稳定态 |
| URL 改写（`history.replaceState`） | 不会。改的是地址栏 |
| 删掉的死代码（`domains`、`subItems`、`heatBg`、`negCats`…） | 不会。模板本来就不读，不读即不渲染 |
| 补 `key`（`riskRows`、`evidence`、treemap tiles…） | 不会。JSX 列表键，不渲染 |

所以这 4 处真要在文本上冒出来，说明偏差比 README 记的更深——那是 bug，不是豁免。白名单的位置留在脚本里并写明了「加条目前先问出处」。

**已知坑：** 在 Git Bash 里别写 `npm run diff -- /official`，MSYS 会把 `/official` 改写成 `C:/Program Files/official`。写 `npm run diff -- official`。
