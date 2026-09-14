/* 数据门面 —— 屏幕唯一的取数入口。**这一层之下只有 HTTP，没有数据。**
 *
 * ## 这里曾经是混合态，现在不是了（expand–contract 走完了）
 *
 * 把 window.RADAR 一次性换成 API，五个屏幕会同时变红，没有一张工单能单独落绿。所以：
 *
 *   1. 门面先变成混合态：已迁移的成员走后端，其余仍读设计源镜像（工单 01）
 *   2. 按工单逐组迁移（05–11）
 *   3. 删掉镜像 import（工单 12，← 现在在这里）
 *
 * 第 3 步是**唯一**能让「前端只承接后端字段」这句话成立的一步。停在第 2 步的话，这个
 * 文件永远还有一条通往本地演示数据的路：漏迁一个成员不会报错，只会静悄悄地回落到镜像，
 * 页面照常渲染、diff 照常绿——因为镜像里的数就是 diff 的对照组，两边当然一致。少写一个
 * 端点的代价是零，这是最危险的一种零。现在 `window.RADAR` 不存在了，漏一个就是 undefined
 * is not a function，在第一次渲染就炸。
 *
 * 契约函数 20 个在下面 `migrated` 里，口径常量 15 个在 `constants` 里（全部来自 /meta）。
 *
 * `heatSeriesFor` / `dailyFor` **有端点但不在这里**：屏幕不直接读它们。热度折线读的是
 * `stagesFor().series`（与 /heat-series 同一份数，内嵌下发省一次串行往返），趋势图读的
 * 是 pool 上的桶。端点仍然存在，因为 PRD §5 要求 24 个契约函数 1:1 对应端点；门面不加
 * 没人调的包装函数——加了只会让人以为屏幕在读它。
 *
 * `observe` 在这里但**没有端点**：它是 `pool().list` 的一个元素，理由见下方注释。
 * `delta` 同样不是端点：它是内嵌在数值旁边的形状，由后端随 benchmark / pool.own 下发。
 *
 * `kolProfile` **不在这里，也不会有端点**：它是已下发帖子之上的纯聚合，且按 PRD §4.3 M5
 * 必须随前端筛选重算。实现在 lib/profile.js，理由见 ADR-0015。
 *
 * `design/` 目录**不删**：它仍是逐字节只读镜像，仍是 `npm run diff` 的对照组。删掉它，
 * 「移植版和设计源一模一样」这句话就再也验不了了（ADR-0006）。变的只是运行时不再读它
 * ——`styles/tokens.css` 仍从那里 @import 设计系统的色板与字体，那是样式不是数据。
 *
 * ## 铁律
 *
 * - **函数签名一个都不变。** 屏幕里的调用点原封不动，改的只是这些函数背后从哪儿取数。
 * - **同步语义不变。** read() 未命中会抛 Promise，由 ScreenBoundary 的 Suspense 接住；
 *   屏幕的 renderVals() 不需要变成 async（ADR-0005）。
 * - **不做任何默认值兜底。** 没有 `?? 0`、`|| 0`、`|| []`：后端的 null 必须原样穿到
 *   渲染层去触发「暂不可用」，在这一层抹平就是撒谎（铁律 2）。
 *   下面出现的 `key || 'd7'` 是**参数默认值**，与设计源 `rangeKey || DEFAULT_KEY` 逐字
 *   一致，改的是「你没告诉我要哪段区间」，不是「后端没给我值」—— 两回事。
 */
import { read, qs } from '../lib/api'

/* 设计源的 DEFAULT_KEY。屏幕不传区间时用它。 */
const DEFAULT_RANGE = 'd7'

/* 单产品端点的路径。产品代码进 URL 路径段，一律编码 —— 现在的代码都是 '3033' 这种
   纯数字，但编码一次的成本是零，漏了一次的成本是一个能被拼进路径的字符串。 */
const product = (code, tail, rangeKey) =>
  `/products/${encodeURIComponent(code)}/${tail}` + qs({ range: rangeKey || DEFAULT_RANGE })

/* ── 契约函数（PRD §5） ──────────────────────────────────────────────
   屏幕读到的全部 20 个函数，签名与设计源逐字一致。 */
const migrated = {
  /* buildRange(key) → GET /ranges/{key}
     区间、时间桶、基准区间、各处文案全部后端下发。PRD §5 逐字：前端不自行算桶。 */
  buildRange(key) {
    return read(`/ranges/${encodeURIComponent(key || DEFAULT_RANGE)}`)
  },

  /* kolImpact(range) → GET /kol/impact?range=
     一次拿整个区间的帖子全集：四级级联筛选、类型多选、表内关键词都是同一份帖子上的
     子集运算，按筛选组合切端点会变成组合爆炸（ADR-0003）。 */
  kolImpact(rangeKey) {
    return read('/kol/impact' + qs({ range: rangeKey || DEFAULT_RANGE }))
  },

  /* kolOpinions(kol, range) → GET /kol/{kol}/opinions?range=
     这位 KOL 对发帖记录之外的产品的观点。每行的 `net` 在样本不足时是 null，原样穿到
     渲染层 —— 换成 0 就成了「中性」，而中性是个我们并没有得出的结论（PRD §3.5）。 */
  kolOpinions(kol, rangeKey) {
    return read(
      `/kol/${encodeURIComponent(kol)}/opinions` + qs({ range: rangeKey || DEFAULT_RANGE }),
    )
  },

  /* pool(range) → GET /pool?range=
     整池一次下发（d2 约 1.1 MB），不按筛选组合切端点：板块、范围、搜索、开关都是同一份
     池上的子集运算。`globalMax`（热力图色阶标尺）和 `own`（自家 KPI 汇总及其三个环比）
     都在里面，屏幕不再自己遍历 61 只求和 —— 那段求和里有三处 `|| 0`（铁律 2）。 */
  pool(rangeKey) {
    return read('/pool' + qs({ range: rangeKey || DEFAULT_RANGE }))
  },

  /* ranks(range) → GET /ranks?range=
     全市场评论量排名 `{map, total}`，基于完整活跃 ETF 池（铁律 3）。端点**不接受**板块／
     搜索参数：接了就迟早有人传，而排名按可见集重算的那一刻，「第 12 名」就不再是一个
     能对外引用的事实了。 */
  ranks(rangeKey) {
    return read('/ranks' + qs({ range: rangeKey || DEFAULT_RANGE }))
  },

  /* benchmark(code, range) → GET /products/{code}/benchmark?range=
     每个字段是内嵌 delta `{text, short, abs, pct, dir}`；`base` 是基准期完整观测；
     `buckets` 与 `base.buckets` 逐桶对齐，供趋势图悬停用（悬停不可能每次打一趟接口）。 */
  benchmark(code, rangeKey) {
    return read(product(code, 'benchmark', rangeKey))
  },

  /* observe(code, range, salt?) —— **没有端点**，也不该有。
     它的返回就是 `pool(range).list` 里那一个元素（设计源里连对象身份都相同）；开一个
     `/observe/{code}`，产品监控页那句「按当前筛选列出候选产品」会退化成 120 次往返。
     `salt === 'bench'` 取基准期观测，那份随 benchmark 一起下发。
     池里没这只产品时返回 undefined —— 与设计源一致，**不是** `{}` 兜底。 */
  observe(code, rangeKey, salt) {
    if (salt === 'bench') return migrated.benchmark(code, rangeKey).base
    return poolIndex(migrated.pool(rangeKey).list)[code]
  },

  /* hotSummaryFor(code, range) → GET /hot-summaries?range= 的一个元素。
     **整池一份**，不按产品切端点：板块总览榜单每一行都调一次，一屏 120 行就是 120 次
     串行往返（read() 未命中抛 Promise，Suspense 解决一个才轮到下一个）。
     `ok` 为 false 时 text 已经是该状态的文案，屏幕直接渲染，不按 status 再拼一遍。 */
  hotSummaryFor(code, rangeKey) {
    /* 整池那份**自己**可能是 null（`DATA_PROVIDER=sql` 下热议总结要 AI 归纳，
       `providers/sql.py::hot_summaries` 整块返回 None）。`null[code]` 是硬 TypeError，
       整个板块总览当场白屏、错误边界报「页面渲染失败」—— 而后端好好的，只是这一栏
       还没生成。容器不知道 ⇒ 里面每一个也不知道，所以这里发 null 而不是 `{}` 或
       `{ok:false,text:'…'}`：后者是在这一层替页面编文案，六态判定不在取数层
       （api.js 硬约束 1）。 */
    const all = read('/hot-summaries' + qs({ range: rangeKey || DEFAULT_RANGE }))
    return all == null ? null : all[code]
  },

  /* summaryFor(code, range) → GET /products/{code}/summary?range= */
  summaryFor(code, rangeKey) {
    return read(product(code, 'summary', rangeKey))
  },

  /* themesFor(code, range, polarity) → GET /products/{code}/themes?range=
     端点一次给 `{positive, negative}`，这里按极性取。设计源的三参签名原样保留 ——
     调用点从来都是正负各取一次，拆成两个端点等于白挨一次串行往返。 */
  themesFor(code, rangeKey, polarity) {
    /* 同 hotSummaryFor：主题聚类整块要 AI（`providers/sql.py::themes_for` 返回 None），
       `null['positive']` 是硬 TypeError，产品监控整页白屏。 */
    const both = read(product(code, 'themes', rangeKey))
    return both == null ? null : both[polarity]
  },

  /* negCatsFor(code, range) → GET /products/{code}/negative-categories?range= */
  negCatsFor(code, rangeKey) {
    return read(product(code, 'negative-categories', rangeKey))
  },

  /* competitorsFor(code, range) → GET /products/{code}/competitors?range=
     `{status, list}`：'unavailable'（传播关系取不到）与 'empty'（确实没有对位竞品）
     在页面上是两句话，别在这一层合并成「list 为空」。 */
  competitorsFor(code, rangeKey) {
    return read(product(code, 'competitors', rangeKey))
  },

  /* complianceFor(code, range) → GET /products/{code}/compliance?range=
     `{status, list}`：同业产品是 'na'（字段不适用），风险识别没接上是 'unavailable'。 */
  complianceFor(code, rangeKey) {
    return read(product(code, 'compliance', rangeKey))
  },

  /* topicsFor(code, range) → GET /products/{code}/topics?range=
     热议话题，按提及数降序（排序在后端）。d1 有八成产品是空数组——那是「这段时间
     没人聊它」，页面渲染「暂无内容」，不是 0 条话题里挑出来的 0。 */
  topicsFor(code, rangeKey) {
    return read(product(code, 'topics', rangeKey))
  },

  /* kolMentionsFor(code, range) → GET /products/{code}/kol-mentions?range=
     `{status, scope, list}`。每行的 `dominantAttitude` 在有效样本 < 3 条时是 null，
     **原样穿到渲染层**去触发「暂不可用」：这里补一个极性，就是替这位 KOL 说了一句
     他没说过的话（PRD §5 表 P8）。 */
  kolMentionsFor(code, rangeKey) {
    return read(product(code, 'kol-mentions', rangeKey))
  },

  /* evidenceFor(code, ctxKey, polarity, count) → GET /products/{code}/evidence?ctx=&polarity=&n=
     四个参数全部参与选取，一个都不能省。ctxKey 形如 `<区间>|<面板 id>`（区间在里面，
     所以这个端点不吃 ?range=），竖线交给 encodeURIComponent。
     count 的钳位（默认 6、上限 12）在后端，这里原样转发——设计源那句
     `Math.max(1, Math.min(12, count || 6))` 属于口径，前端不再算一遍（铁律 1）。
     关联竞品面板传的 code 是**竞品自己**的代码，取的是竞品的原文，不是本产品的。 */
  evidenceFor(code, ctxKey, polarity, count) {
    return read(
      `/products/${encodeURIComponent(code)}/evidence` +
        qs({ ctx: ctxKey, polarity: polarity, n: count }),
    )
  },

  /* candlesFor(code, range) → GET /products/{code}/candles?range=
     `{status, granularity, granLabel, currency, list, missing}`，每根 K 对着
     `buildRange(range).buckets` 的一个桶。空 K 的 open/high/low/close 是 **null**，
     一路穿到画布 —— 这一层补一个 0，图上就会画出一根掉到零的 K 线（铁律 2）。
     `status` 为 'ok' 时仍可能有空桶（休市），两种空的区别在每根 K 的 `note` 上。 */
  candlesFor(code, rangeKey) {
    return read(product(code, 'candles', rangeKey))
  },

  /* stagesFor(code, range) → GET /products/{code}/stages?range=
     `{status, series, stages, granularity, granLabel, rule, threshold, unitCount}`。
     **合并已经在后端做完**，屏幕只画色带和表格，不得自行分组（PRD §5、铁律 1）。
     `series` 与 `/products/{code}/heat-series` 是同一份数，内嵌下发是因为热度折线与
     阶段色带画在同一套几何上，分两次取数就是白挨一次串行往返。
     `threshold` 随响应下发而不是取 `R.LOW_SAMPLE`：它取决于粒度（半日 5、整日 10）。
     `status` 为 'unavailable' 时没有 `threshold`／`unitCount` 这两个键 —— 屏幕里那句
     `SG.threshold || R.LOW_SAMPLE` 兜的正是这一态。 */
  stagesFor(code, rangeKey) {
    return read(product(code, 'stages', rangeKey))
  },

  /* officialPosts(range) → GET /officials/posts?range= */
  officialPosts(rangeKey) {
    return read('/officials/posts' + qs({ range: rangeKey || DEFAULT_RANGE }))
  },

  /* etfMentionsFor(account, range) → GET /officials/{account}/etf-mentions?range=
     口径按出现次数累加（ETF_MENTION_RULE），**与市场域的评论去重语义相反**，别混用。 */
  etfMentionsFor(account, rangeKey) {
    return read(
      `/officials/${encodeURIComponent(account)}/etf-mentions` +
        qs({ range: rangeKey || DEFAULT_RANGE }),
    )
  },
}

/* ── 常量与主数据（GET /meta） ────────────────────────────────────────
 *
 * 这些在屏幕里是**属性**不是函数（`R.PRESETS`、`R.MASTER[code]`），所以只能做成 getter：
 * 属性求值时才 read()，命中缓存同步返回，未命中抛 Promise 交给 Suspense。写成
 * `PRESETS: read('/meta').presets` 会在 import 阶段就打网络，那时还没有 Suspense 边界
 * 接得住抛出的 Promise，整个模块加载当场失败。
 *
 * `/meta` 的字段名与设计源不同（`presets[].key` vs `PRESETS[].k`）。这是整套接口里
 * **唯一**一处形状转换：其余 23 个端点的 data 形状与 PRD §5 的函数返回逐字一致，不需要
 * 适配。之所以留下这一处，是因为 /meta 是我们自己定的契约，`key`/`text` 这种自解释的
 * 命名值得保留；转换收敛在下面这几行里，不散进屏幕。
 */
const meta = () => read('/meta')

/* R.MASTER 在产品监控页一次渲染里被读十几次，R.SECTORS 在 KOL 页每行读两次，每次
   重建一遍是白费力气。按源数组的**身份**记忆：read() 命中缓存时返回的是同一个数组，
   换了数据（清缓存重试、切了 provider）身份就变了，缓存自然失效。 */
function memoBy(build) {
  let memo = null
  return (src) => {
    if (!memo || memo.src !== src) memo = { src: src, out: build(src) }
    return memo.out
  }
}

const masterOf = memoBy((products) => {
  const map = {}
  for (const p of products) map[p.code] = p
  return map
})

const orderOf = memoBy((products) => products.map((p) => p.code))

/* code → 该区间的观测。observe() 在产品监控页一次渲染里被调上百次（候选产品列表、
   对比产品、传播关系……），每次线性扫一遍 120 只是白费力气。按 pool().list 的身份记忆。 */
const poolIndex = memoBy((list) => {
  const map = {}
  for (const o of list) map[o.code] = o
  return map
})

const legendOf = memoBy((legend) =>
  legend.map((x) => ({ k: x.key, v: x.text, bg: x.bg, fg: x.fg })),
)

const sectorsOf = memoBy((sectors) =>
  sectors.map((x) => ({ k: x.key, name: x.name, hue: x.hue, tint: x.tint })),
)

const constants = {
  get DEFAULT_RANGE() { return meta().defaultRange },
  get UPDATED() { return meta().updatedAt },
  get PRESETS() {
    return meta().presets.map((p) => ({ k: p.key, days: p.days, label: p.label }))
  },
  get ETF_MENTION_RULE() { return meta().rules.etfMention },
  get TYPE_RULE() { return meta().rules.postType },
  get HOT_RULE() { return meta().rules.hotSummary },

  /* 讨论热度公式（PRD §3.3，全系统唯一定义）。板块总览把这行字**原样印在页面上**，
     所以它必须与后端算热度用的那条公式同源 —— 前端硬编码一份，改公式的那天页面上
     写的还是旧的，而数字已经是新的了。 */
  get HEAT_FORMULA() { return meta().heat.formula },
  get HEAT_NOTE() { return meta().heat.note },

  /* 有效态度样本阈值（PRD §3.5）。屏幕只拿它拼说明文字，判定本身在后端
     （观测上的 attitude.sampleSufficient）—— 前端不重算一遍「够不够」。 */
  get LOW_SAMPLE() { return meta().thresholds.lowSample },

  /* 阶段观点口径（PRD §5 `stagesFor` 的 `rule`）。屏幕里写作 `SG.rule || R.STAGE_RULE`，
     而 `rule` 在**四态全都有**（设计源把它放在 base 上），所以那个 `||` 右边其实取不到。
     留着它、并且让这个常量与响应里那份同源（都是 /meta 的 `rules.stage`），是因为两处
     写着同一句口径的两个版本比取不到更糟：面板说明位印的是这段字，改了口径却只改一处
     不会有任何东西变红。 */
  get STAGE_RULE() { return meta().rules.stage },

  /* 页面上的 AI 结论验证到什么程度（ADR-0019 §4，枚举 none/spot_check/gold，本期恒为
     `none`）。它不是口径也不是数据，是一句**关于数据可信度的声明**，所以由 /meta 下发
     而不是写在屏幕里：面板上那句、`/meta`、汇报时的说法必须是同一个来源。
     文案映射在 lib/view.js 的 `aiValidationNote`。 */
  get AI_VALIDATION() { return meta().aiValidation },

  /* 六态图例（PRD §3.6 逐字）。`key`/`text` → `k`/`v` 是 /meta 的形状转换之一。
     底色随图例一起下发，理由同 SECTORS 的 hue：「暂不可用是警示色、暂无内容不是」
     本身就是这套状态体系的一部分，不是一张前端配色表。 */
  get STATUS_LEGEND() { return legendOf(meta().statusLegend) },

  /* 板块表。名字与色值都后端下发（ADR-0004 归在口径常量）：产品实体上带 sector: 'hk'，
     板块是那个键的定义方，不是前端的一张配色表。`key` → `k` 是 /meta 的形状转换之一。 */
  get SECTORS() { return sectorsOf(meta().sectors) },

  /* MASTER / ORDER 由同一个数组还原。products 在线上是数组而不是 {code: {...}} 字典，
     因为产品代码是 '3033' 这样的纯数字字符串——JS 对象会把它们当整数键按数值升序重排，
     ORDER（自家 61 只在前、竞品 59 只在后）当场丢失，而下拉、热力图都按这个顺序渲染。 */
  get MASTER() { return masterOf(meta().products) },
  get ORDER() { return orderOf(meta().products) },

  /* 官号名单。`url` 由后端下发，不再由屏幕用 hash() 现算（ADR-0004）。 */
  get OFFICIAL() { return meta().officials },

  /* 合作 KOL 名单（标签来自合作名单，PRD §4.4）。形状从设计源的三元组
     `[名字, '标签,标签', 1]` 换成 `{name, tags, active}` —— 三元组是手写数据表的
     形状，接口没有理由让屏幕靠下标位置认字段。唯一的消费者是 KOL 详情页的标签行。 */
  get KOLS() { return meta().kols },
}

/* 常量必须走 defineProperties 而不是展开进对象字面量：它们是 getter，展开会当场求值，
   而那时还没有 Suspense 边界接得住 read() 抛出的 Promise。 */
const R = Object.assign({}, migrated)
Object.defineProperties(R, Object.getOwnPropertyDescriptors(constants))

export default R
