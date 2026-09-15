# ADR-0022 — 讨论热度的下限口径：转发数未知时按已知项计算并披露未知帖数

- **状态**：已接受（项目负责人 2026-09-15 裁决：「按已知项计算并披露未知帖数」）
- **日期**：2026-09-15
- **修订**：[PRD §3.3](../PRD.md) 加一句补充；`backend/core/heat.py` 的 `heat_of()` 签名与 docstring；`backend/providers/sql.py` 的扫描聚合。公式本身（评论量 ＋ 0.3 × 点赞 ＋ 1 × 转发）与权重**不变**。
- **不改**：[ADR-0011](0011-comment-volume-caliber.md)（评论量口径）、CLAUDE.md 铁律 2 的原则；runbook §23.3 里「不得用 AI、当前累计转发、平均数或 0 替代当时的未知计数」这一句仍然成立 —— 本 ADR 不替代未知数，只是不再让它抹掉已知的部分。
- **相关**：PRD §3.3／§3.6、CLAUDE.md 铁律 1、2；`backend/tests/test_heat.py`、`tests/test_sql_provider.py::TestUnknownSharesAreDisclosedAsLowerBound`

## 背景

`feeds.share_count` 在源侧 `raw_json` 被 TEXT 列截断的行上是 NULL。真库里这样的行占 **0.09%**（d7 窗口 31,054 篇里 9 篇；全库 167 条），`worker/jobs/repair_feed_metrics.py` 按保守结构校验去救，**写回 0 条**（runbook §23.3）：坏 JSON 里找不到一个能安全恢复的完整计数对象，缺失只能由完整历史导出补采。

`backend/core/heat.py` 原来的规则是「三项里任何一项未知，热度就是未知」，`providers/sql.py` 的 `_bump` / `_finish` 又把它抬到窗口级：窗口内**任一帖**转发未知 ⇒ 该产品整个窗口的 `shares` / `interactions` / `discussionHeat` 全部 None；`pool()` 再用 `_add_all` 把 61 只自家产品的热度求和，None 传染 ⇒ `own.heat` None ⇒ 板块总览顶部第一张 KPI 卡「数据暂不可用」。

规则的初衷是对的（铁律 2：拿 0 冒充未知会让热度静默偏低），但代价与缺口的比例失衡。用真实规模的合成瘦库（37.8 万帖／19 万评论／90 万提及，0.09% 帖子 `share_count` 为 NULL）跑改前的读路径：

| 区间 | 热度为 None 的产品 | 其中自家 | 评论量前十名里热度为 None | `own.heat` |
|---|---|---|---|---|
| d7 | 40 / 120 | 17 | 7 / 10 | None |
| d30 | 99 / 120 | 49 | 10 / 10 | None |

窗口越长、产品越热，撞上坏行的概率越高 —— 于是**头部产品必灰**，而热度恰恰是「只用于排序与快速扫描」（PRD §3.3）的那一列。一个只影响千分之一帖子的缺口，把页面上最常看的一列整个抹掉了。

## 决策

### 1. 按已知项计算，并把未知帖数一起下发

窗口（或桶）内转发数未知的帖子**不进**转发和，只把**未知帖数**加一。`shares` / `interactions` / `discussionHeat` 按已知项计算；同一层级并排下发 `heatUnknownPosts:int`：

- `heatUnknownPosts == 0` ⇒ 三个数是完整的数；
- `heatUnknownPosts > 0` ⇒ 三个数是**下限**，差的是这几帖的转发数。

PRD §3.3 新增逐字：「窗口内存在转发数未知的帖子时，热度按已知项计算并标注为下限，同时披露未知帖子数；不以 0 代替未知转发数。」

这不是把 None 当 0。铁律 2 反对的是用一个看着完整的数字冒充未知；这里未知的那部分被逐字说了出来，数字只是被如实标成了下限。读的人拿到的是「热度 ≥ 11，差 1 帖的转发」，而不是「11」或「暂不可用」。

### 2. 口径落点

- `core/heat.py`：`heat_of(comments, likes, shares, unknown_posts=0)`。`comments` / `likes` 为 None 仍返回 None（它们是 dump 的列，没有「已知部分」可言）；`shares` 为 None 且 `unknown_posts == 0` 仍返回 None（没有任何披露就没有资格给数）；`unknown_posts > 0` 时结果**等于**只用已知项算出的数。JS 式四舍五入不变。
- `providers/sql.py`：`_blank` / `_bump` / `_finish` 累计 `shares`（已知和）与未知帖数；观测（`pool().list[]`、`benchmark().base`）、观测桶、`heat_series_for` 的每桶各带 `heatUnknownPosts`；`pool().own.heatUnknownPosts` 是 61 只自家的合计，`own.heat` 用 `_add_all` 正常求和；`benchmark().heatUnknownPosts = {current, base}` 两侧分说，`heat` / `interactions` / `shares` 的 delta 两侧同口径。
- demo provider 不改：设计源的转发数永远已知，这些键在 demo 下不存在，进 `test_provider_parity.py` 的 `EXTENSION_KEYS`。
- 帖子级字段**不变**：单篇帖子的 `shares` / `engagement`（官号／KOL 帖子、证据卡的 `interactions`）在那一帖转发未知时仍是 None —— 一篇帖子没有「已知部分」，也没有可披露的帖数。KOL 画像 `leaders[].engagement` 的 None 传染随之保留。

### 3. 前端的义务

`heatUnknownPosts > 0` 的位置必须把「下限」与帖数一起显示（例如「≥ 11 · 1 帖转发未知」），不许只显示数字。这条与 ADR-0019 §4 的「未经人工验证」声明同理：数字可以给，但它是什么必须一起说。

## 后果

- 板块总览 KPI 卡、榜单热度列、热力图面积、产品监控热度卡与趋势图在真库上重新有数；合成库 d7 / d30 的 None 产品数从 40 / 99 降到 0。
- 页面多了一个要渲染的键；在它落地之前，前端看到的是一个**没有标注的下限** —— 所以本 ADR 与前端渲染改动要在同一个发布窗口合入。
- `test_heat.py` 里「任一项未知则整体未知」的断言改为新语义；`TestUnknownSharesPropagate` 改名为 `TestUnknownSharesAreDisclosedAsLowerBound`。
- runbook §23.3 末句「`backend/core/heat.py` 公式及未知传播保持不变，因此仍可能有热度暂不可用」不再成立，待 runbook 维护者更新（本 ADR 不改 runbook）。

## 否决的备选

1. **维持 None 传染。** 诚实，但让千分之一的坏行决定整列的可见性；补采无望（runbook §23.3），意味着这一列长期灰。
2. **只在桶级 None、产品级照算。** 桶级 None 会让趋势图上的断点与产品级的数对不上（桶之和 ≠ 产品总数），而且产品级要么仍传染、要么就得偷偷按已知项算 —— 后者正是没有披露的下限。
3. **用平均数或邻近帖子补。** 估算出来的数字和真的长得一样，页面上没有任何迹象；runbook §23.3 明写不得如此。
4. **把坏帖整条剔出窗口。** 那一帖的评论量、点赞、提及都是真的；为了一个字段丢掉四个，比 None 传染更不诚实。
