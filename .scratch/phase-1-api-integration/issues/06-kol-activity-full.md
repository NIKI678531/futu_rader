# 06: KOL 影响力整页接 API

**What to build:** `/kol` 整页脱离设计源镜像，KOL 列表、声量排名、发帖内容与阵地分布全部由后端供数，逐字比对干净。

**Blocked by:** 05

**Status:** done

## 验收标准

- [x] `kolImpact(range)` 与 `kolProfile(name, posts, campFn?)` 实现并暴露端点 —— **有偏差，见下方 Comments「`kolProfile` 不做端点」**
- [x] 页面全部内容由 `buildRange` / `kolImpact` / `kolProfile` 驱动
- [x] 帖子类型**双标签**按 `TYPE_RULE` 逐字：内容形式必有一枚；操作方向仅在表达了明确操作时出现；判不出方向标「方向待确认」。第一期按双标签实现，`typeScheme` 开关保留（PRD O1 / ADR-0013）
- [x] **KOL 声量排名是另一套指标，不受铁律 3 约束**（PRD §4.3 M5）。验收时不要拿全市场评论量排名那条「筛选不重算」的规则去卡它
- [x] 阵营筛选「提自家」**含 both**（与官号页的三分互斥不同，这是设计而非 bug）
- [x] 02 的 `/kol` diff clean
- [x] 04 的六态断言在本页通过；页面上每个 `0` 可追溯到真实为零的后端字段
- [x] 断开后端 → 屏级错误条

## Comments

### `kolProfile` 不做端点 —— 偏离验收标准 1

验收标准 1 与 spec 的接口清单都写了要给 `kolProfile` 暴露端点（spec 里是 `GET /kols/{name}/profile`）。**没有照做**，理由记在 [ADR-0015](../../../docs/adr/0015-view-scoped-aggregation.md)，摘要：

PRD §4.3 M5 逐字要求声量排名「随 ETF 与类型筛选变化、不随 KOL 筛选变化」——页面要的是**当前可见帖子子集**上的画像，而那个子集由四级级联筛选 × 类型多选 × `campRule` 开关在屏幕里算出来。做成端点就得把这套筛选语义在 Python 里再实现一遍，那正是 CLAUDE.md 禁止的「照着行为手推一遍」；做成「每次筛选变化请求一次」则撞上 ADR-0003 已经否掉的组合爆炸。

所以 `kolProfile` 归入 ADR-0015 新立的第五类「视图内聚合」，实现留在 `frontend/src/lib/profile.js`，`backend/core/kol.py` 的模块 docstring 里写明后端不实现它。**它不违反铁律 1**：铁律 1 管的是口径公式，这里数的是「这批帖子里提自家的有几篇」，而「这批」按设计本来就该随筛选重算。真正属于口径的那一份——**全量** leaders——由后端在 `kolImpact` 响应里算好下发，KOL 详情页（工单 07）用的是那一份。

**后果：PRD §5 的 23 组契约函数实际落成 22 个端点。**

### 端点粒度：一次发整个区间的帖子全集

`GET /kol/impact?range=` 一次把区间内全部合作 KOL 的帖子（含 AI 标注）发下来，d7 约 580 KB。四级级联筛选、类型多选、表内关键词都是同一份帖子上的子集运算，按筛选组合切端点会变成组合爆炸（ADR-0003）。`?range=` 的解析从 `officials.py` 提到了 `api/v1/__init__.py` 的 `range_key()`，两个域共用；不认识的区间值**不做兜底**，一路带到 `core` 判成 `MISSING` → 404。

### 修掉一个铁律 2 的漏洞：合计把 `null` 当 0 加

设计源的 `kolProfile` 写的是 `eng += p.engagement; cm += p.comments`。JS 的 `+` 把 `null` 当 `0`——一篇取不到评论数的帖子会被静悄悄算成「那篇 0 条」，声量排名那一列照样显示一个看着完全正常、只是偏小的数字。

**这比渲染成 `NaN` 坏得多**：`NaN` 一眼看得出坏了，少算的合计看不出来。改成「有一篇不知道，合计就是不知道」（`lib/profile.js` 的 `add`），这是 `lib/profile.js` 相对设计源的**唯一**有意偏差，文件头写明了。演示数据里计数字段永远齐全，这一支不会走到，五页逐字比对仍然全绿。

护栏两头都加了，并且**做过变异验证**——把 `add` 改回 `+=` 再跑，六态红线当场报「期望出现『数据暂不可用』，实际渲染 `10`」：

- `frontend/scripts/six-state.mjs` KOL ⑥（渲染层）
- `backend/tests/test_six_states.py::test_leader_totals_are_null_when_any_post_count_is_null`（后端下发的全量 leaders）

**相关但没有动的**：`b.engagement - a.engagement` 排序在 `null` 上也会把它当 0（于是未知互动的帖子沉底）。这不是铁律 2 的事——页面上没有把 `null` 显示成 `0`——但「互动未知的帖子该排在哪」是个产品问题，留给客户确认，不在这张工单里悄悄改掉。

### 六态护栏扩到两页

`six-state.mjs` 原来写死了 `/official`，现在是 `PAGES` 表，每接一页加一项。这一页比官号页多演一态：**操作方向判不出来**（`directionPending=True` + `direction=None`，不是 `'hold'`）。共 15 条：官号 7＋1、KOL 6＋1。

两个坑，都是**断言被自己的文案绊倒**：

1. 我给 `sixstate-kol-dirpending` 写的摘要原文是「标『方向待确认』，不是『持有观望』」——反向断言 `reject: ['持有观望']` 当场撞上样本自己的摘要。样本文案里不能出现被断言的词。（工单 05 的案例 ⑧ 是同一个坑：错误条自己引用了「数据暂不可用」。）
2. 发帖记录是表格，第一行没有自己的起始分隔符，按「原帖 ↗」切开时会连着整个页头成为第一段——而页头 KPI 那句就写着「加仓 0 · 减仓 0 · 方向待确认 1」。行级断言会被页头的字绊倒，**假绿假红都可能**。加了页级 `body` 锚点从表头「赞 / 评 / 转」之后开始看。

顺带把失败报告从「窗口开头 200 字」改成「命中处前后各 90 字」。窗口可以很长，命中在第 900 个字符时贴前 200 字等于什么都没说，而那正是最需要看清现场的时候。

### 六态样本的 `leaders` 是手写的

`make_sixstate.py` 里那一条 leader 是手写常量，没有在 Python 里跑一遍 `kolProfile`——那正是 ADR-0015 拒绝的事。5 篇同一类型，数值一眼可核。「方向待确认」徽章的样式对象则从演示数据里借了一份现成的（`dirStyle(null, true)` 的产物），同理不在 Python 里重拼。

`/kol` 屏幕本身从不读 `M.leaders`（它按可见集重算），但 `KolDetail.jsx` 读，所以这里留个空数组会变成工单 07 的陷阱。

### `/meta` 新增 `rules.postType`

双标签口径（`TYPE_RULE`）印在 KOL 影响力页页脚，与 `rules.etfMention` 一样逐字下发，`test_meta.py::test_post_type_rule_is_verbatim` 钉住。两段都用 node/vm 跑设计源比对过，逐字节一致。

**别把这两条口径「统一」**：`etfMention` 按出现次数累加，与市场域的评论去重语义相反。

### `/meta` 的 getter 加了按身份记忆

`R.MASTER` 在产品监控页一次渲染里被读十几次，`R.SECTORS` 在 KOL 页每行读两次。按源数组的**身份**记忆（`memoBy`）：`read()` 命中缓存返回同一个数组，换了数据身份就变，缓存自然失效。

### 收尾状态

- 后端 `pytest -q` → **148 passed**
- `npm run build` 干净
- `npm run diff` → **五页逐字一致**（`/official` 998 文字 · 50 链接；`/kol` 530 · 19；`/kol/detail` 300 · 12；`/sector` 693 · 0；`/product` 632 · 0）
- `npm run six-state` → **15 条全过**
- `npm run guards` → ①红 ②✓ ③✓。①只剩 `KolDetail.jsx:159` 的 `R.addDays`，是工单 07 的活
- `KolActivity.jsx` 里剩下的 `R.*` 全部有后端支撑：`R.kolImpact` / `R.buildRange` / `R.SECTORS` / `R.TYPE_RULE` / `R.MASTER`
