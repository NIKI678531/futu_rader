# 12: 收口——拔掉演示数据镜像

**What to build:** 数据门面不再 import 设计源的演示数据文件，前端从此没有任何自己造数的能力。这是 expand–contract 的 contract 一步：01 让门面变成混合态，05–11 逐组把函数迁到 API，本单删掉旧形态。做完之后，前端**只承接后端字段**这句话才真正成立。

**Blocked by:** 05, 07, 09, 11

**Status:** ready-for-agent

## 验收标准

- [ ] 数据门面不再 import 设计源的演示数据文件；`window.RADAR` 不再是前端的数据来源
- [ ] 前端不残留任何演示数据与生成器（`hash` `rnd` `pick` `pickN` `addDays`）
- [ ] 口径常量（`PRESETS` `SECTORS` `LOW_SAMPLE` `HEAT_FORMULA` `HEAT_NOTE` `STATUS_LEGEND` `HOT_RULE` `STAGE_RULE` `TYPE_RULE` `ETF_MENTION_RULE` `ORDER` `UPDATED`）全部来自 `/meta`，前端零硬编码
- [ ] 04 的三条静态守卫全绿
- [ ] 02 的五页 diff 仍 clean
- [ ] 断开后端 → 五页**均**出屏级错误条；屏级错误与字段级「暂不可用」在 UI 上是两种不同的东西
- [ ] 页面上印着的数据更新时间来自后端下发，不是前端硬编码
- [ ] `design/` 目录一字未改——它仍是逐字节只读镜像，仍是 02 逐字比对的对照组，**不删**
- [ ] 复核：口径公式在前端一处都不剩，`backend/core/` 是唯一实现处（铁律 1）
