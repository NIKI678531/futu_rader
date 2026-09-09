# ADR-0004 — `window.RADAR` 成员三分法

- **状态**：已接受
- **日期**：2026-09-09
- **相关**：[ADR-0003](0003-endpoint-granularity.md)、[ADR-0005](0005-sync-shim-and-fidelity.md)

## 背景

`design/radar-data.js`（2039 行）在 `window.RADAR` 上挂了约 60 个成员。五个屏一共引用其中 50 个左右，调用量最大的几个是 `R.num`(24)、`R.MASTER`(15)、`R.CAMP`(14)、`R.typeStyle`(9)。

它们**不是同一类东西**：有查询函数，有口径常量，也有纯粹的展示助手。接 API 前必须把界划清楚，否则会把颜色计算也搬到后端，或者把公式留在前端。

## 决策

三分：

| 类别 | 成员 | 归属 |
|---|---|---|
| **契约函数** | PRD §5 的 23 组（`observe` / `pool` / `ranks` / `themesFor` / `kolImpact` / `officialPosts` …） | **后端**，走 API |
| **口径常量** | `PRESETS` / `SECTORS` / `STATUS_LEGEND` / `HEAT_*` / `LOW_SAMPLE` / `MASTER` / `CMAP` | **后端**，由 `GET /meta` 下发 |
| **展示助手** | `rgba` / `typeStyle` / `dirStyle` / `shell` / `navGroups` / `md` / `dowOf` / `num` / `pct1` | **前端永久保留**，一次性拷进 `frontend/src/lib/view/` |
| **生成器** | `hash` / `rnd` / `pick` / `pickN` / `addDays` | **接 API 后必须从屏内彻底消失** |

跨界的一个：`delta()`。`abs` / `pct` / `dir` 属口径（铁律 1，后端算）；`text` / `short`（「数据暂不可用」等）属文案，前端按 PRD §3.1 拼。后端下发 `{abs, pct, dir}` 或 `null`。

**护栏**：接 API 完成后，`frontend/src/screens/**` 里出现 `R.hash` / `R.rnd` / `R.pick` / `R.pickN` 即构建失败（一条 grep 断言）。

**断开 import**：`frontend/src/data/radar.js` 不再 import `design/radar-data.js`。

## 理由

- 展示助手产出的是颜色、CSS 变量和文案，不是数据。把它们搬到后端等于让后端知道 `var(--csop-navy-900)`，荒谬。
- 生成器留在前端等于留了一条随时会被误用的旁路：将来真数据接进来，屏里如果还有 `R.rnd()`，就会出现「一半真一半编」而没人察觉。这是本决策最重要的一条。
- 常量走 `/meta` 而非前端硬编码，是因为 `LOW_SAMPLE`、`lowConfidence` 这些值**会随回归调整**，且它们的数值**逐字印在页面文案里**。

## 后果

- **展示助手从镜像拷进前端会引入分叉风险**——镜像更新时它们不会自动跟。对冲：拷贝文件头写明来源与行号，[README.md](../../README.md) 的再导入流程里加一条「这些助手同样要重拷」。这是本决策已知的、接受的代价。
- 镜像本身继续保留，供 `npm run design` 并排比对——那是 [ADR-0006](0006-dual-acceptance-criteria.md) 的验收基准，不受影响。

## 否决的备选

- **全部搬后端**：后端要处理 CSS 变量。
- **保留 import 作为 fallback**：直接废掉本 ADR 的护栏。
