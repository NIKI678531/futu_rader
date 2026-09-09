# 03: 展示助手迁出 `window.RADAR`（prefactor）

**What to build:** 把纯渲染用的展示助手从设计源镜像里搬进前端自己的 view 层，五个页面改从那里取用。页面输出一个字都不变——这是纯粹的搬家。做在前面，是为了让后面的门面改造能安全地拔掉镜像：「先让改动变容易，再做那个容易的改动」。

**Blocked by:** 01, 02

**Status:** ready-for-agent

## 验收标准

- [ ] `rgba` `typeStyle` `dirStyle` `shell` `navGroups` `md` `dowOf` `num` `pct1` `shortName` 迁入前端 view 层（ADR-0004）
- [ ] 从设计源**逐字拷贝**，不照着行为手写一遍——手推一遍是这个仓库最容易出还原度事故的地方
- [ ] 五个屏幕改从 view 层取用这些助手
- [ ] `num()` 的 `null` 分支逐字保留「数据暂不可用」，`delta()` 的 `null` 分支逐字保留「数据暂不可用」/「暂不可用」。**不得被"顺手简化"**
- [ ] 这些助手**不走网络**：它们是纯渲染，为纯样式逻辑打一趟 API 毫无意义
- [ ] 02 的五页 diff 仍 clean
- [ ] `design/` 目录一字未改
