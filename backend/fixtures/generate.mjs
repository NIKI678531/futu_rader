/* 演示 provider 的取数器：把设计源 design/radar-data.js 的契约函数返回值导出成 JSON。
 *
 * 为什么是「跑设计源」而不是「用 Python 重写一遍生成器」——
 * 验收标准是 React 版与设计源静态站的 textContent 逐字相同（ADR-0006）。把 hash / rnd /
 * pickN / spread / toFixed / sort 稳定性这些逐位行为在 Python 里重新实现一遍，等于
 * CLAUDE.md 明令禁止的「照着设计手推一遍」：JS 的 >>> 、charCodeAt 的 UTF-16 语义、
 * Math.round 的 half-up（Python 是 banker's rounding）、Array.sort 的稳定性——任何一处
 * 对不齐，逐字比对就红，而且极难定位。
 *
 * 这里没有第二份口径实现：数值**逐字**来自设计源，本文件只负责枚举参数、序列化。
 * 真实数据的口径实现在 backend/core/（SQL），与本文件无关（铁律 1 不受影响）。
 *
 * 用法：node backend/fixtures/generate.mjs
 * 产出：backend/fixtures/demo/<function>.json，形如 {"<参数键>": <该函数返回值>}
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createContext, runInContext } from 'node:vm'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const repo = join(here, '..', '..')
const outDir = join(here, 'demo')

/* design/radar-data.js 是浏览器 IIFE，只依赖一个 window 全局（已核对：无 document /
   navigator / localStorage）。给它一个空 window 就能在 Node 里原样求值。 */
const ctx = createContext({ window: {}, console })
runInContext(readFileSync(join(repo, 'design', 'radar-data.js'), 'utf8'), ctx, {
  filename: 'design/radar-data.js',
})
const R = ctx.window.RADAR
if (!R) throw new Error('design/radar-data.js 没有定义 window.RADAR')

const RANGE_KEYS = R.PRESETS.map((p) => p.k)

mkdirSync(outDir, { recursive: true })

let files = 0
let entries = 0

/** 把 {参数键: 返回值} 写成一个 JSON 文件。参数键即 URL 上的查询组合，后端按同样规则拼。 */
function dump(name, pairs) {
  const obj = {}
  for (const [k, v] of pairs) obj[k] = v
  // 缩进 0：这些文件是机器读的，d30 的官号帖子流有上千条，缩进会让体积翻倍。
  writeFileSync(join(outDir, name + '.json'), JSON.stringify(obj), 'utf8')
  files++
  entries += pairs.length
  console.log(`  ${name}.json  ${pairs.length} 条`)
}

console.log('从设计源导出演示数据：')

/* ── buildRange(key) ── 5 个预设区间。前端不自行算桶（PRD §5 逐字），桶在这里下发。
 *
 * 额外下发 `dates`：区间内每一个自然日。**桶不够用** —— buckets 随粒度变（d1/d2 是
 * 24/48 个小时桶，d30 是 5 个周桶），而 KOL 详情的发帖日历是按天排的，跟桶对不上。
 * 设计源在屏里用 `addDays(range.from, i)` 现算，那是演示生成器泄漏进屏幕代码
 * （ADR-0004），跟着「前端不自行算桶」的同一条理由走：日历边界由后端定。
 * 这里调的是设计源自己的 addDays，没有第二份日历实现。 */
dump('ranges', RANGE_KEYS.map((k) => {
  const r = R.buildRange(k)
  const dates = []
  for (let i = 0; i < r.days; i++) dates.push(R.addDays(r.from, i))
  return [k, { ...r, dates }]
}))

/* ── 帖子上的 reviewState（ADR-0019 §2） ──────────────────────────────────
 *
 * 设计源没有这一列 —— 它是 2026-09-11 才随 ADR-0019 进入契约的：徽章文案由
 * `review_state` 驱动，不再由 `confidence` 驱动（校准置信度当前全为 NULL）。
 * `sql` provider 从 `annotations.review_state` 直接取；演示这边没有标注表，所以按设计源
 * 自己的那条规则翻译过来：它的 `annotate()` 里「置信度低于阈值 ⇒ 标待确认」
 * （`radar-data.js:1233` 的 `low`），对应的就是 `needs_review`。
 *
 * 这样翻译的结果是**被标「待确认」的帖子集合和从前逐字相同**，于是 KPI 小字、脚注、
 * 「仅看待确认」筛选、CSV 列一个都不用动，逐字比对也不会因此多出白名单条目。
 *
 * 阈值不在这里写死：它在 meta.json 的 thresholds.lowConfidence，前端从 /meta 拿的
 * 也是那一个（铁律 1 的同一个理由 —— 一个数只有一个来源）。
 *
 * 这是本文件对设计源返回值的第四处**有意不一致**（前三处见下面 pool/ranks 那段）。 */
const LOW_CONF = JSON.parse(readFileSync(join(here, 'meta.json'), 'utf8')).thresholds.lowConfidence
/** 就地补键，**不复制对象**：`kolImpact` 的 `leaders[].posts` / `leaders[].top` 与
 *  `posts` 是同一批对象引用，换成 `{...post}` 会让同一篇帖子在两处序列化成两个值。 */
function stampReview(posts) {
  for (const p of posts) {
    // confidence 为 null 的帖子在演示数据里不存在；真出现时给 null 而不是 'pending' ——
    // 「不知道复核状态」和「模型给了结论且未举手」是两回事（铁律 2）。
    p.reviewState = p.confidence == null ? null : p.confidence < LOW_CONF ? 'needs_review' : 'pending'
  }
  return posts
}

/* ── officialPosts(range) ── 官号帖子级内容流。 */
dump('official_posts', RANGE_KEYS.map((k) => [k, stampReview(R.officialPosts(k))]))

/* ── etfMentionsFor(account, range) ── 官号 × ETF 提及统计。
   参数键 "<account>|<range>"；账号全集来自 OFFICIAL 主数据，20 × 5 = 100 条。 */
dump(
  'etf_mentions',
  R.OFFICIAL.flatMap((o) => RANGE_KEYS.map((k) => [o[0] + '|' + k, R.etfMentionsFor(o[0], k)])),
)

/* ── kolImpact(range) ── 全部合作 KOL 的帖子全集（含 AI 标注）＋后端算好的画像榜。
   一次把整个区间的帖子发下去，是因为 KOL 页的四级级联筛选、类型多选、表内关键词都在
   前端对同一份帖子做子集运算（PRD §4.3）——按筛选组合切端点会变成组合爆炸。 */
dump('kol_impact', RANGE_KEYS.map((k) => {
  const v = R.kolImpact(k)
  stampReview(v.posts)
  return [k, v]
}))

/* ── kolOpinions(kol, range) ── 这位 KOL 对**发帖记录之外**的产品的观点与操作。
   参数键 "<kol>|<range>"；KOL 全集来自 KOLS 主数据，32 × 5 = 160 条。
   与 kolImpact 分开是因为它按 KOL 取、且只有详情页用；塞进 kolImpact 会让 KOL 影响力页
   每次都白拉 32 个人的观点表。 */
dump(
  'kol_opinions',
  R.KOLS.flatMap((k) => RANGE_KEYS.map((rk) => [k[0] + '|' + rk, R.kolOpinions(k[0], rk)])),
)

/* ── 市场域基础组：pool / ranks / benchmark ──────────────────────────────
 *
 * 三者都以「完整活跃 ETF 池」为底（PRD §3.8、§4.1）：板块、范围、搜索与开关只改变
 * **可见范围**，排名与色阶标尺始终来自全市场。所以池是一次性整份下发的，前端只过滤，
 * 不重算 —— 铁律 3 在接口这一侧就是这句话。
 *
 * 与设计源返回值的三处**有意不一致**，每一处都是为了「同一个数只有一个来源」：
 *
 *   1. `ranks` 不下发 `list`。设计源的 `ranks().list` 与 `pool().list` 是同一个数组
 *      （同一批 observe 对象），前端一处都没读过它。留着就是同一份 120 只产品的观测
 *      在两个端点上各发一遍（d2 单份 1.1 MB）。
 *   2. `pool` 不下发 `rankMap`。它与 `ranks().map` 逐字相同。铁律 3 的那个数只能有一个
 *      来源 —— 两份一旦不一致，页面上会同时出现两个「第 12 名」，而看的人无从判断
 *      哪个对。排名的唯一来源是 /ranks。
 *   3. `pool` 多一个 `own`：CSOP 自家产品的 KPI 汇总（板块总览顶部两张卡）。设计源在
 *      屏幕里现算，包括三次 `delta()` —— 那是 PRD 第 3 章的全局口径，铁律 1 要求只实现
 *      一份。算在这里而不是 Python 侧，是因为 delta 的文案带 `toFixed(1)`：JS 与 Python
 *      的舍入在 .05 边界上不一致，重写一遍等于埋一个只在极少数数值上现形的差异。
 *
 * `observe(code, range)` **没有端点**。它的返回就是 `pool().list` 的元素（设计源里
 * 连对象身份都相同），前端按 code 取即可；单独开一个端点，产品监控页那句
 * `ORDER.map(observe)` 就会变成 120 次往返。这不是聚合（≠ ADR-0015），是取集合里的一个元素。
 */

/* 合计时 null 会传染。JS 里 `sum + null === sum + 0` —— 少加的那一只悄悄消失，
   合计看着还挺像样，比 NaN 难发现得多（铁律 2）。有一只不知道，合计就是不知道。 */
function addN(sum, v) {
  return sum == null || v == null ? null : sum + v
}

/* 自家产品 KPI 汇总。口径逐字来自设计源 sector-overview 顶部两张卡：
   只统计 ownership === 'own'，**不随榜单筛选变化**，基准期为顶部预设的前一等长区间。 */
function ownRollup(R, P, rangeKey) {
  const own = P.list.filter((o) => o.ownership === 'own')
  let heat = 0, baseHeat = 0, neg = 0, negBase = 0, pos = 0, posBase = 0, risk = 0
  for (const o of own) {
    heat = addN(heat, o.discussionHeat)
    baseHeat = addN(baseHeat, P.baseHeat[o.code])
    neg = addN(neg, P.negMentions[o.code])
    for (const c of R.negCatsFor(o.code, rangeKey)) {
      /* 基准期舆情条数没有单独的字段，只能从当期条数减去环比增量还原。
         `c.delta.abs` 为 null 时基准期就是未知，不是「没变化」—— 设计源那句
         `c.delta.abs != null ? c.delta.abs : 0` 把未知当成了 0。 */
      negBase = addN(negBase, c.delta ? addN(c.mentions, c.delta.abs == null ? null : -c.delta.abs) : null)
    }
    pos = addN(pos, o.attitude.positive)
    const bo = R.observe(o.code, rangeKey, 'bench')
    posBase = addN(posBase, bo && bo.attitude ? bo.attitude.positive : null)
    /* 需合规关注条数：同业产品是 na、扫描未接入的是 unavailable，两者都进不了合计。
       设计源写的是 `|| 0`，于是「有一只没扫过」被读成「那只是零条」。 */
    risk = addN(risk, P.complianceCount[o.code])
  }
  return {
    count: own.length,
    heat, neg, pos, risk,
    dHeat: R.delta(heat, baseHeat),
    dNeg: R.delta(neg, negBase),
    dPos: R.delta(pos, posBase),
  }
}

dump('pool', RANGE_KEYS.map((k) => {
  const { rankMap, ...rest } = R.pool(k)   // rankMap → /ranks，见上文第 2 点
  return [k, { ...rest, own: ownRollup(R, R.pool(k), k) }]
}))

dump('ranks', RANGE_KEYS.map((k) => {
  const r = R.ranks(k)
  return [k, { map: r.map, total: r.total }]   // list → /pool，见上文第 1 点
}))

/* ── benchmark(code, range) ── 当期 vs 基准期的环比，参数键 "<code>|<range>"。
 *
 * 多一个 `buckets`：与 `base.buckets` 逐桶对齐的 delta 束。产品监控页的趋势图鼠标悬停
 * 要显示「这一桶较基准同位 +12（+25.0%）」，设计源在屏幕里对五条序列各调一次 delta()。
 * 悬停不可能每次打一趟接口（同步 Suspense 会当场挂起），所以整段随 benchmark 一起下发。
 * 五条序列的键与图例键一一对应，前端只按图例开关取用。 */
const BUCKET_SERIES = ['comments', 'active', 'interactions', 'positive', 'negative']

dump(
  'benchmark',
  R.ORDER.flatMap((code) => RANGE_KEYS.map((k) => {
    const b = R.benchmark(code, k)
    const cur = R.observe(code, k).buckets
    const buckets = cur.map((cb, i) => {
      const bb = b.base.buckets[i]
      const row = {}
      for (const f of BUCKET_SERIES) row[f] = R.delta(cb[f], bb[f])
      return row
    })
    return [code + '|' + k, { ...b, buckets }]
  })),
)

/* ── 市场域叙述组：摘要 / 热议 / 主题 / 负面类别 / 竞品 / 合规 ─────────────
 *
 * 与基础组的两处**有意不一致**，都是为了同步 Suspense 下的往返次数（ADR-0005）：
 *
 *   1. `hot_summaries` 按**区间**下发整池，不按产品。板块总览的榜单里每一行都调一次
 *      `hotSummaryFor(o.code, range)` —— 逐个开端点，那一屏 120 行就是 120 次**串行**
 *      往返（read() 未命中抛 Promise，Suspense 解决一个才轮到下一个）。整池一份不到
 *      100 KB，比一次往返的握手还便宜。
 *   2. `themes` 一次给两个极性。设计源的签名是 `themesFor(code, range, polarity)`，
 *      而两处调用点（总览抽屉、产品监控）都是正负各取一次，分开就是白挨一次串行往返。
 *      门面保留三参签名，取 `[polarity]` —— 屏幕代码一个字不用改。
 *
 * 其余四个按 "<code>|<range>" 下发：它们只在抽屉／面板打开时取一次，不在循环里。
 */
dump('hot_summaries', RANGE_KEYS.map((k) => {
  const map = {}
  for (const code of R.ORDER) map[code] = R.hotSummaryFor(code, k)
  return [k, map]
}))

const perProduct = (name, fn) =>
  dump(name, R.ORDER.flatMap((code) => RANGE_KEYS.map((k) => [code + '|' + k, fn(code, k)])))

perProduct('summaries', (code, k) => R.summaryFor(code, k))
perProduct('themes', (code, k) => ({
  positive: R.themesFor(code, k, 'positive'),
  negative: R.themesFor(code, k, 'negative'),
}))
perProduct('neg_cats', (code, k) => R.negCatsFor(code, k))
perProduct('competitors', (code, k) => R.competitorsFor(code, k))
perProduct('compliance', (code, k) => R.complianceFor(code, k))

/* ── 产品监控证据组：话题 / KOL 提及 / 原文证据 ────────────────────────── */

perProduct('topics', (code, k) => R.topicsFor(code, k))
perProduct('kol_mentions', (code, k) => R.kolMentionsFor(code, k))

/* ── evidenceFor(code, ctxKey, polarity, n) ── 全站参数最多的契约。
 *
 * ctxKey 是证据侧栏的「从哪儿点进来的」，形如 `<range>|<面板 id>`。它看着开放，其实
 * **有限可枚举**：产品监控页只有五种面板会走 evidenceFor，每种的 id 都由已经落地的契约
 * 函数产出（当前舆情总结 `sum`、正负主题 `themesFor().id`、产品话题 `topicsFor().id`、
 * 关联竞品 `<竞品代码><极性>`、阶段观点 `stage-<n>-<起始日>`）。KOL 提及与需合规关注
 * 两种面板自带 `items`，不打这个端点。
 *
 * 枚举漏了的后果是 404 → 屏级错误条：响一声，而不是悄悄给出一份别的产品的证据。
 *
 * 按区间切成 5 个文件（整份约 17 MB）：侧栏只在点开时取一次，provider 没必要为了它
 * 把五个区间全读进内存。
 *
 * ## 存的是「生成顺序 ＋ 每个 n 的次序」，不是排好序的 12 条
 *
 * 设计源生成 n 条后按发布时间倒序排，而它的比较器是
 * `a.publishedAt < b.publishedAt ? 1 : -1` —— 相等时返回 -1 而不是 0，同分钟两条谁在前
 * 取决于 V8 排序的实现细节。d1 只有一天、分钟数是 `h % 60`，12 条里撞上同一分钟的概率
 * 过半，这不是边角情况。Python 的 sorted 稳定、JS 的 Array.sort 在比较器不自洽时不保证，
 * 所以次序由设计源自己给出，Python 只按下标取。
 *
 * 顺带这也让 `n` 这个参数真的通到底：12 个次序覆盖 n=1..12 全部取值，而不是把某一个 n
 * 的结果切一刀充数（切出来的次序是错的，且错得不显眼）。
 */
function evidencePack(code, ctxKey, polarity) {
  const prefix = code + ctxKey
  /* id 逐字是 `code + ctxKey + i`，i 即生成序号。这里只是把它读回来，没有重算任何东西。 */
  const seq = (e) => Number(e.id.slice(prefix.length))
  const items = R.evidenceFor(code, ctxKey, polarity, 12)
    .slice()
    .sort((a, b) => seq(a) - seq(b))

  const orders = []
  for (let n = 1; n <= 12; n++) {
    const want = R.evidenceFor(code, ctxKey, polarity, n)
    const order = want.map(seq)
    /* 自检：按这份次序还原出来的，必须与设计源逐字相同。fixture 是机器产物，
       它对不对只能在产出的这一刻证明 —— 到了 Python 那边已经没有对照物了。 */
    if (JSON.stringify(order.map((i) => items[i])) !== JSON.stringify(want)) {
      throw new Error(`证据次序还原失败：${prefix} ${polarity} n=${n}`)
    }
    orders.push(order)
  }
  return { items, orders }
}

/** 一只产品在一个区间里，证据侧栏所有可能的入口。 */
function evidenceContexts(code, rangeKey) {
  const out = []
  const ctx = (id) => rangeKey + '|' + id

  out.push([code, ctx('sum'), 'neutral'])                       // 当前舆情总结
  for (const polarity of ['positive', 'negative']) {
    for (const t of R.themesFor(code, rangeKey, polarity)) out.push([code, ctx(t.id), polarity])
  }
  for (const t of R.topicsFor(code, rangeKey)) out.push([code, ctx(t.id), 'neutral'])
  /* 关联竞品：证据取的是**竞品自己**的原文，所以第一个参数是竞品代码不是本产品。
     同一只竞品会被多只自家产品关联到，跨产品去重后条数少很多。 */
  for (const c of R.competitorsFor(code, rangeKey).list) {
    for (const polarity of ['positive', 'negative']) {
      out.push([c.code, ctx(c.code + polarity), polarity])
    }
  }
  for (const st of R.stagesFor(code, rangeKey).stages) {
    out.push([code, ctx('stage-' + st.n + '-' + st.from), st.sentiment || 'neutral'])
  }
  return out
}

for (const rangeKey of RANGE_KEYS) {
  const pairs = []
  const seen = new Set()
  for (const code of R.ORDER) {
    for (const [c, ctxKey, polarity] of evidenceContexts(code, rangeKey)) {
      const key = c + '|' + ctxKey + '|' + polarity
      if (seen.has(key)) continue
      seen.add(key)
      pairs.push([key, evidencePack(c, ctxKey, polarity)])
    }
  }
  dump('evidence_' + rangeKey, pairs)
}

/* ── 价格与阶段组：K 线 / 热度序列 / 阶段观点 / 日度序列 ────────────────
 *
 * `stages` 里内嵌 `series`，与 `heat_series` 是**同一份数**。这看着违反了上面 ranks /
 * pool 那条「同一个数只有一个来源」，其实相反：
 *
 *   - 那两处砍掉的是**没人读**的那一份（`ranks.list`、`pool.rankMap`），留下唯一被读的。
 *   - 这里被读的恰恰是 `stages.series`（热度折线与阶段色带画在同一套几何上，分成两次
 *     取数就是白挨一次串行往返），而 `/heat-series` 是**没有屏幕读**的那一个。
 *
 * 它之所以还得存在，是 PRD §5 要求每个契约函数 1:1 对应端点。两者不会发散：演示期
 * 同一次 `heatSeriesFor()` 调用导出两份，真实期 `stagesFor` 的实现本身就调 `heatSeriesFor`。
 *
 * `dailyFor(code)` 是四个里唯一**不吃区间**的（它固定给 SERIES 那 42 天），所以参数键
 * 就是产品代码。`'ALL'` 是它自己的伪代码（全市场合计），一并导出。同样没有屏幕读它。
 */
dump('candles', R.ORDER.flatMap((code) => RANGE_KEYS.map((k) => [code + '|' + k, R.candlesFor(code, k)])))
dump('heat_series', R.ORDER.flatMap((code) => RANGE_KEYS.map((k) => [code + '|' + k, R.heatSeriesFor(code, k)])))
dump('stages', R.ORDER.flatMap((code) => RANGE_KEYS.map((k) => [code + '|' + k, R.stagesFor(code, k)])))
dump('daily', ['ALL', ...R.ORDER].map((code) => [code, R.dailyFor(code)]))

/* ── 主数据 ── /meta 里「不是口径、是实体」的那部分：产品池与官号名单。
 *
 * 口径常量（预设区间、板块、阈值、六态图例、热度公式、口径说明文字）在 fixtures/meta.json
 * 里手写，逐字对齐 PRD 并有守卫测试；主数据是**数据**，来源应当是库，演示期从设计源导出。
 * 两者在 /meta 的响应里合并，前端看到的是一个端点。
 */
function master() {
  /* products 必须是**数组**，不能是 {code: {...}} 对象：产品代码是 '3033' 这样的纯数字
     字符串，JS 对象会把它们当整数键、按数值升序重排，ORDER（自家 61 只在前、竞品在后）
     当场丢失。门面那边再由数组还原出 MASTER 与 ORDER。 */
  const products = R.ORDER.map((code) => R.MASTER[code])

  /* 官号主页地址：设计源里由屏幕现算（`R.hash(o.full) % 80000000`），那是演示数据生成器
     泄漏进屏幕代码（ADR-0004）。地址是账号的属性，应当随主数据下发——接真实库后它来自
     库里的账号表。这里逐字保留原表达式，保证迁移后页面输出不变。
     OFFICIAL 的 o[2]/o[3] 是生成器的发帖量／互动量基准，属演示内部量，不下发。 */
  const officials = R.OFFICIAL.map((o) => ({
    short: o[0],
    full: o[1],
    comps: o[4],
    url: 'https://www.futunn.com/user/' + (10000000 + (R.hash(o[1]) % 80000000)),
  }))

  /* 合作 KOL 名单。设计源存成三元组 [名字, 标签, 活跃合作]，下发时展开成有名字的字段 ——
     `k[1]` 在屏幕里读起来完全看不出是什么。`active` 前端此刻不用（详情页只读标签），
     但它是名单本身的属性（区分在册与在跑），接真实库后同样来自库表。 */
  const kols = R.KOLS.map((k) => ({ name: k[0], tags: k[1], active: k[2] === 1 }))

  /* 「数据截至」。它长得像口径常量，但它是**数据的属性**：这批数据最后一条帖子发在
     什么时候。原来它手写在 fixtures/meta.json 里，于是 `DATA_PROVIDER=sql` 接真库时，
     页面拿演示锚点 `2026-09-02 09:00 HKT` 给真数据落款 —— 而真库的数据到 `2026-08-25`
     就断了，整整虚报一周。那不是缺失，是说谎，比空着更难发现。
     这里逐字取设计源的 `R.UPDATED`（`NOW_DATE + ' ' + NOW_TIME + ' HKT'`），
     演示侧的输出一个字都不变。 */
  const updatedAt = R.UPDATED

  writeFileSync(
    join(outDir, 'master.json'),
    JSON.stringify({ products, officials, kols, updatedAt }),
    'utf8',
  )
  files++
  entries += products.length + officials.length + kols.length
  console.log(
    `  master.json  产品 ${products.length} 只 · 官号 ${officials.length} 个 · KOL ${kols.length} 位`,
  )
}
master()

console.log(`完成：${files} 个文件，${entries} 条。`)
