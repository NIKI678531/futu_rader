# 07: KOL 详情整页接 API

**What to build:** `/kol/detail` 整页脱离设计源镜像。这一页带着 PRD §5 逐字点名的第一个 `null` 字段（`dominantAttitude` 在样本 < 3 条时），是铁律 2 的第一个真实验收现场。

**Blocked by:** 06

**Status:** done

## 验收标准

- [x] `kolOpinions(kol, range)` 实现并暴露端点
- [x] 页面全部内容由 `kolImpact` / `kolProfile` / `kolOpinions` 驱动
- [x] 移除 `R.addDays`，**移除后输出逐字不变**；04 的静态守卫 ① 对本页转绿
- [x] **`dominantAttitude` 在样本 < 3 条时后端返回 `null`，前端渲染「暂不可用」，绝不渲染 `0` 或空白**（PRD §5 状态语义逐字点名了这个字段）
  - **有偏差，见下方 Comments①**：这个字段不在本页。本页的等价现场是 `kolOpinions` 的 `net`。
- [x] 入口仍只从 KOL 影响力「详情 →」进入，**不进一级导航**（一级导航只有市场／账号两个域）
- [x] 02 的 `/kol/detail` diff clean
- [x] 04 的六态断言在本页通过；每个 `0` 可追溯
- [x] 断开后端 → 屏级错误条

## Comments

### ① `dominantAttitude` 挂错了工单

写工单时以为它在 KOL 详情页。它不在。它是 `kolMentionsFor` 的产出字段
（`design/radar-data.js`），只渲染在**产品监控页**的「KOL 提及」区
（`design/product-monitor.dc.html`）—— KOL 详情页从头到尾没有它。本单没有把它
硬塞进来，而是把这条验收标准原样搬到了工单 10（`kolMentionsFor` 真正落地的地方）。

本页承接这条标准的**意图**（PRD §5 逐字点名的 null 字段必须显示成「暂不可用」）的，
是 `kolOpinions` 每行的 `net`：样本不足时后端下发 `null`，前端渲染长文案
「数据暂不可用」。后端侧由 `test_kol.py::test_net_is_null_when_the_sample_is_too_thin`
钉住（同时断言演示数据里 null 与非 null 都存在，否则断言会自己满足自己），
前端侧由六态用例 ②⑦⑧ 钉住。

### ② `/ranges/{key}` 新增 `dates`，`buckets` 顶不上它

拔 `R.addDays` 时发现日历轴没处可取。`buckets` 不行 —— 它的粒度随区间在时/日/周
之间变（d1 是 24 个小时桶，d30 是 5 个周桶），而本页右上那根双色堆叠柱**固定按天**：
d1 要 1 根柱不是 24 根，d30 要 30 根不是 5 根。所以 `dates` 是区间资源上的独立字段，
由 `generate.mjs` 调设计源自己的 `addDays` 生成 —— 全仓没有第二份日历实现。
`test_ranges.py::test_dates_is_the_day_axis_and_buckets_cannot_stand_in_for_it`
把这条区别钉死。

顺带踩了一个坑：`kolImpact` 载荷里内嵌的 `M.range` 是设计源那份四字段裁剪副本
（`{days, from, text, to}`），没有 `dates`，直接读会 `Cannot read properties of
undefined`。修法是改读区间资源本身（`R.buildRange(s.rangeKey)`）—— `shell()` 已经
取过，`read()` 命中缓存，不多发请求。**没有**把 `dates` 反规范化进内嵌 range：
那会让区间的形状在两个地方各自正确一次。

### ③ 第二处 `sum + null` 修正

头部「互动合计」原本是设计源的 `likes += p.likes`。JS 里 `sum + null === sum + 0`，
所以少加的那一篇会**悄悄消失**，合计看着还挺像样 —— 这不是 `NaN` 那种会自己喊出来的
错，是铁律 2 点名的那类撒谎。改成 `add()`：有一篇不知道，合计就是不知道。
与 `lib/profile.js` 里同一处偏差同源、同理由（ADR-0015 末段）。

变异测试证明这条红线是真的：把 `add()` revert 回 `+=`，页面渲染出
「赞 48 · 评 10 · 转 3」，六态用例 ⑥ 立刻转红。

### ④ `KOLS` 从三元组换成命名实体

设计源里是 `[名字, '标签,标签', 1]`。三元组是手写数据表的形状，不是接口形状 ——
没有理由让屏幕靠下标位置认字段。`/meta` 下发 `{name, tags, active}`，
`test_meta.py::test_kol_roster_is_named_entities_not_tuples` 钉住。
唯一的消费者是本页的标签行。

### ⑤ 六态守卫长到三页 / 24 条

新增 KOL 详情 8 条用例。加了一个逃生口 `page: true`：页级 `body` 会先把页头切掉，
而「互动合计」在页头里，所以这几条用例在完整页面文本上划窗。

差点埋进去一条自我满足的断言：lowconf 帖子的 fixture 摘要里原本带着「待确认」三个字，
而本页的置信度徽章只写「待确认」不带数字（影响力页写「待确认 0.42」），
`want: ['待确认']` 会被摘要自己满足。改写了摘要。同一类陷阱在工单 05 用例 ⑧
与工单 06 用例 ③ 出现过。

### ⑥ 验收数字

- `pytest` → 184 passed
- `npm run build` → clean
- `npm run guards` → 三条静态守卫全过（**守卫 ① 首次转绿** —— `R.addDays` 已拔除，
  全程没设豁免名单）
- `npm run diff` → 五页逐字一致：`/official` 998·50、`/kol` 530·19、
  `/kol/detail` 300·12、`/sector` 693·0、`/product` 632·0
- `npm run six-state` → 24 条全过（本页加入前是 15 条）
- 浏览器并排（:5173/kol/detail vs :5174/kol-detail.dc.html）：整页截图无肉眼差异，
  394,500 B vs 394,485 B
