# 12: 收口——拔掉演示数据镜像

**What to build:** 数据门面不再 import 设计源的演示数据文件，前端从此没有任何自己造数的能力。这是 expand–contract 的 contract 一步：01 让门面变成混合态，05–11 逐组把函数迁到 API，本单删掉旧形态。做完之后，前端**只承接后端字段**这句话才真正成立。

**Blocked by:** 05, 07, 09, 11

**Status:** done

## 验收标准

- [x] 数据门面不再 import 设计源的演示数据文件；`window.RADAR` 不再是前端的数据来源
  - 删掉的是三行：`import '../../../design/radar-data.js'`、`const MIRROR = window.RADAR` 与它下面那句「镜像没加载」的 throw。出口从「镜像铺底 + 迁移覆盖」改成 `Object.assign({}, migrated)` 再 `defineProperties` 挂常量。
- [x] 前端不残留任何演示数据与生成器（`hash` `rnd` `pick` `pickN` `addDays`）
  - `src/lib/dc.js` 里那个 `hash()` 是重名不是残留：它把 CSS 声明串哈希成类名，与演示数据无关。见 Comments。
- [x] 口径常量（`PRESETS` `SECTORS` `LOW_SAMPLE` `HEAT_FORMULA` `HEAT_NOTE` `STATUS_LEGEND` `HOT_RULE` `STAGE_RULE` `TYPE_RULE` `ETF_MENTION_RULE` `ORDER` `UPDATED`）全部来自 `/meta`，前端零硬编码
  - 实际是 16 个 getter（另有 `DEFAULT_RANGE` `MASTER` `OFFICIAL` `KOLS`），每一个都只读 `/meta` 的一个键；其中 5 个额外做一次形状转换（`presets` → `{k,days,label}`、`statusLegend` → `{k,v}`、`sectors` → `{k,...}`、`products` → `MASTER`/`ORDER` 两份），不含任何计算。
- [x] 04 的三条静态守卫全绿
  - 现在是**五条**：本单新增守卫⑤「前端不得从设计源镜像取数」，并改写了守卫①的失效说明（它的原文写的是「等镜像被拔掉就会当场炸」，镜像已经拔了）。
- [x] 02 的五页 diff 仍 clean（5 页 9 次比对，逐字一致）
- [x] 断开后端 → 五页**均**出屏级错误条；屏级错误与字段级「暂不可用」在 UI 上是两种不同的东西
- [x] 页面上印着的数据更新时间来自后端下发，不是前端硬编码
- [x] `design/` 目录一字未改——它仍是逐字节只读镜像，仍是 02 逐字比对的对照组，**不删**
- [x] 复核：口径公式在前端一处都不剩，`backend/core/` 是唯一实现处（铁律 1）

## Comments

**验证**：后端 361 个测试，六态红线 49 条，五页 9 次逐字比对全部一致，5 条静态守卫（新增①的兄弟⑤），`npm run build` 干净。产物 574.79 kB → **486.13 kB**（gzip 162.69 → 126.23），Vite 那条 500 kB 分块告警随之消失——少掉的 88 kB 就是一直被打进包里的整份演示数据。

**停在第 5–11 单就收工，是这次改造最容易犯的错。** 那时候每一页都在走 API、每一条断言都是绿的，看起来已经做完了。但门面里还留着一条通往镜像的路：`migrated` 覆盖在 `MIRROR` 上面，漏迁一个成员**不会报错**，只会静悄悄地落回镜像，页面照常渲染。而且逐字比对也拦不住——**镜像里的数就是逐字比对的对照组**，回落回去反而更「一致」。整套验证在这个场景下会一致地说「做完了」。拔掉之后，同一个漏迁是首次渲染就 `R.xxx is not a function`。所以 contract 这一步的价值不在删代码，在于**把一类静默失败换成一次响亮的崩溃**。

**守卫⑤补的正是这条路，而且让它真红过一次。** 它 grep `frontend/src` 里的 `design/radar-data` 与 `window.RADAR`；把那两行 import 临时贴回去，它报 2 处并打印出行号，还原后转绿。守卫①同时改了说明：镜像还在的时候，写 `R.hash` 的坏处是「页面上有一部分数字是前端编的，看着和后端下发的一模一样」；现在它的坏处只是崩溃，所以这条守卫的价值改成了「在 grep 阶段就说清为什么它不存在」——运行时报错只会说 `R.hash is not a function`，不会说这个值本该由后端下发，而后者才是要改的东西。

**`src/lib/dc.js` 里还有一个 `hash()`，它不是漏网的。** 它把一串 CSS 声明哈希成 `dc-hover-1a2b3c` 这样的类名，是 dc 语法垫片的一部分，跟演示数据没有半点关系。守卫①的 pattern 写的是 `R.(hash|rnd|...)` 而不是裸的 `hash`，就是为了不误伤它——一条会误报的守卫活不过两个月，第三次被人骂之后就会被注释掉，然后真的违规也没人管了。记在这里是因为验收时 grep `hash` 一定会撞见它，下一个人不该再判断一次。

**常量必须走 `defineProperties`，不能展开进对象字面量。** 16 个常量全是 getter，`{...constants}` 会在模块求值时**当场调用**它们，也就是在还没有任何 Suspense 边界的时候触发 `read()` 抛 Promise —— 表现是白屏加一个 "A component suspended while responding to synchronous input" 之类跟真实原因毫不相干的报错。镜像还在的时候这个坑被遮住了（展开出来的是镜像上的静态值），拔掉的当天才会露出来。

**断网是屏级错误，不是满屏「暂不可用」，这两者在 UI 上不能长得一样。** 六态脚本里 7 个页面入口（/sector、/product 三种、/official、/kol、/kol/detail）每个都跑一遍：把 `**/api/v1/**` 全部 abort，要求出现「后端服务连不上」的屏级错误条，并且**刨掉错误条之后**的页面文本里不许再出现发帖卡、表头、「原帖 ↗」这类还在装作有数据的东西。把断网渲染成一屏「数据暂不可用」，市场团队会读成「采到了数据，只是缺几个字段」，接着照常去看那些还在的数字——那是最坏的一种误导。

**`design/` 不删，而且一个字节都没动**（`git status -- design/` 空）。它现在的身份从「数据来源」变成了纯粹的**对照组**：02 的逐字比对每次都要起 `npm run design` 拿它当基准，删了就等于把这套验证的另一半扔掉。另外 `src/styles/tokens.css` 里还有一条 `@import` 指向设计系统的 CSS——那是样式不是数据，守卫⑤的 pattern 只认 `radar-data`，注释里写明了这一点。

**口径公式在前端确实一处不剩，两处「像公式」的东西是有意留下的**：`lib/view.js` 是纯格式化（`num()` 把 `null` 渲染成「数据暂不可用」、`pct1()` 保留一位小数），它决定的是**怎么显示**，不是**算什么**；`lib/profile.js` 是 ADR-0015 定的「视图内聚合」——KOL 详情的画像随前端筛选实时重算，后端下发的是明细而不是聚合结果。两者都不碰 PRD 第 3 章的任何一条口径，守卫④（`R.delta` / `R.heatOf` 不得出现在屏幕里）继续钉着真正的那条线。

**没做的**：`backend/providers/mysql.py` 仍是第一期的空壳，demo provider 的 27 个数据方法它只有 6 个——演示期走的是 demo provider，选到 mysql 会在缺失的方法上抛 `AttributeError`。这是有意的（第一期不接真实库），但它是个**运行时**才发现的缺口，接库那一单第一件事应该是把 provider 接口钉成抽象基类，让缺方法在导入时就报出来。另：`heatSeriesFor` / `dailyFor` 有端点无门面包装（工单 11 的决定），拔镜像时因此少了两个需要判断的成员。
