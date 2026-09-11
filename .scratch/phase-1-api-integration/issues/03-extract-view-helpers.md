# 03: 展示助手迁出 `window.RADAR`（prefactor）

**What to build:** 把纯渲染用的展示助手从设计源镜像里搬进前端自己的 view 层，五个页面改从那里取用。页面输出一个字都不变——这是纯粹的搬家。做在前面，是为了让后面的门面改造能安全地拔掉镜像：「先让改动变容易，再做那个容易的改动」。

**Blocked by:** 01, 02

**Status:** done

## 验收标准

- [x] `rgba` `typeStyle` `dirStyle` `shell` `navGroups` `md` `dowOf` `num` `pct1` `shortName` 迁入前端 view 层（ADR-0004）
- [x] 从设计源**逐字拷贝**，不照着行为手写一遍——手推一遍是这个仓库最容易出还原度事故的地方
- [x] 五个屏幕改从 view 层取用这些助手
- [x] `num()` 的 `null` 分支逐字保留「数据暂不可用」，`delta()` 的 `null` 分支逐字保留「数据暂不可用」/「暂不可用」。**不得被"顺手简化"**
- [x] 这些助手**不走网络**：它们是纯渲染，为纯样式逻辑打一趟 API 毫无意义
- [x] 02 的五页 diff 仍 clean
- [x] `design/` 目录一字未改

## Comments

10 个助手迁入前端 view 层，五个屏幕改成具名 import（`R.num(` → `num(`），门面上不再有任何 `R.<助手>` 引用。

**唯一会间接触网的是 `shell()`**：它要拿区间文案，而 `buildRange` 已经迁到后端了。它自己不算任何东西，只把后端下发的 `text`/`from`/`to` 摆到外壳上。其余九个是纯函数。

`num()` 的 `null` 分支原样是「数据暂不可用」（长文案，用在数值位与环比位），没有被顺手改成状态图例那套短文案「暂不可用」——两套文案不可互换（PRD §3.1、§3.6）。`delta()` 不在迁移清单里：按 spec D1 它是嵌在响应里的形状（`{abs,pct,dir}` 或 `null`），不是端点也不是展示助手，留在门面上等工单 09／11 把两处调用点改成直接消费后端下发的 delta。

口径常量（`TYPE_BY_KEY`、`DIR_BY_KEY`、`PRESETS`、`STATUS_LEGEND`、`UPDATED`、`SECTOR_AGG`）在调用时从门面读，不在 view 层复制一份。等它们迁到 `/meta`（工单 12）时这里一行都不用改。纯样式表（`DOW`、`TYPE_GROUP`、`DIR_TONE`、`NAV`）属展示层，直接拷进来。

**「逐字拷贝，不要手推」这条当场应验了。** 我把 `NAV` 的头两行按显示名写成了 `k: 'market'` / `k: 'account'`，设计源里其实是 `k: 'portfolio'` / `k: 'accounts'`。屏幕传的正是后者，于是 `shell()` 里 `NAV.filter(...)[0].items` 直接抛 `TypeError`，三个账号域页面整屏挂掉。工单 02 的 harness 一跑就报了出来：`JS 报错 TypeError` + 「移植版 3 段 / 设计源 998 段」。**没有这个 harness，这种错要么上线才发现，要么靠肉眼在 998 段文字里找。**

跑完 `npm run build` 干净、五页 diff 全绿。
