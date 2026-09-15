/* 五页逐字比对 —— 独立测试 React（:5177）对设计源镜像（:5174），演示后端 :8017。
 *
 * ## 为什么是这个 harness，而不是组件单测
 *
 * 这个项目的验收目标只有一句话：**移植版渲染出来的字，和设计源一模一样**。组件级 React
 * 单测测的是「这个组件收到这些 props 会渲染成什么」—— 那是实现细节，重构一次就得重写一次，
 * 而且对上面那句话一点保障都没有。能机器验证「100% 还原」的，只有把两边的文本抓下来逐字比。
 * ADR-0006 定的就是这一条。
 *
 * ## 比的是什么
 *
 * 按文档顺序取出每个非空文本节点，得到一个「文本片段序列」。这就是 textContent 的语义
 * （所有文本节点、按顺序、不含标签），但保留了节点边界，所以差异能定位到「第 N 段文字」，
 * 而不是给你两坨几万字的字符串让你自己找。
 *
 * 段内空白归一化（连续空白→单空格、去首尾）：JSX 的换行缩进和 .dc.html 的模板缩进本来
 * 就不可能一致，那不是还原度问题。
 *
 * 除文本外还比**站外链接**（富途主页、原帖地址）。它们不是文字，但用户会点，指错地方
 * 是实打实的缺陷，而纯文本比对完全看不见。站内导航链接不比：DcLink 会把
 * `official-activity.dc.html` 改写成 `/official`，两边本来就该不一样（README 有意偏差 2）。
 *
 * ## 展开态（views）
 *
 * 一页的字不止默认状态那些。抽屉、弹层里的内容只有点开才渲染，而它们恰恰是数据最密的地方
 * （板块总览抽屉一屏里有舆情总结、正负主题、负面类别、关联竞品、需合规关注五组）。只比
 * 默认状态，等于把这几组排除在「逐字一致」之外，绿灯的含义就比字面上小一圈。
 *
 * 所以一页可以带若干 `views`：一段在**两边各做一遍**的相同操作，做完再抓一次。比的仍然是
 * 「同样操作之后两边的字一不一样」。定位一律走文字（getByText），不走 style 属性 —— 内联
 * 样式在 JSX 里会被 React 重新序列化，两边的属性字符串本来就不同，按它选元素必然假红。
 *
 * ## 白名单
 *
 * 每一条都必须写明出处。没有出处的豁免＝把 bug 洗成规范。
 *
 * 用法：
 *   npm run diff             # 自己拉起两个站点，跑完关掉
 *   npm run diff -- --keep   # 不关站点（本地反复调试时用）
 *   npm run diff -- official # 只比某一页（写路由名即可；别写前导斜杠，
 *                            #   Git Bash 会把 /official 当路径改写成 C:/Program Files/…）
 */
import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { chromium } from 'playwright'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const BACKEND = path.resolve(FRONTEND, '..', 'backend')

const API_ORIGIN = 'http://127.0.0.1:8017'
const REACT_ORIGIN = 'http://localhost:5177'
const DESIGN_ORIGIN = 'http://localhost:5174'

/* 榜单行、热力图色块、Top3 卡都是「点一下把 sel 设成这个代码」，所以按代码的**完整文字**
   选中第一个即可，点到哪一个都打开同一只产品的抽屉。3033 是评论量第一，任何筛选态下都在。
   点完必须等一个**只有抽屉里才有**的字：抽屉内容要现取（移植版这边是五次 read()），
   而下面那个「文本长度连续两次不变」的稳定判据在取数的空档里会当场判稳 —— 于是抓到的是
   没打开的页面，两边一比 212 处差异全在设计源那侧。等一个后置条件，顺带也证明了这一下
   真的点开了：点了个没反应的元素同样会得到「两边一致」的绿灯。 */
const openSectorDrawer = async (page) => {
  await page.getByText('3033', { exact: true }).first().click()
  await page.getByText('当前舆情总结', { exact: true }).first().waitFor({ timeout: 30_000 })
}

/* 「口径与数据状态」面板默认是收起的，而这一页最要紧的逐字文案全在里面：六态图例、
   评论去重口径、热度公式与权重、热议总结口径。收起状态下比对通过，说明不了它们对。
   按钮里还跟着一个 ▲/▼ 的 caret，所以不能用 exact 匹配整段文字。 */
const openSectorNotes = async (page) => {
  await page.getByText('口径与数据状态').first().click()
  await page
    .getByText('字段应有值，但当前数据源未提供或尚未核验。', { exact: true })
    .first()
    .waitFor({ timeout: 30_000 })
}

/* 产品监控的原文证据侧栏。默认状态下它是空的（挂载着但没内容），而 `evidenceFor` 的
   四个参数——产品代码、ctxKey、极性、条数——全部只在这里生效。不点开就等于这个端点
   一次都没被比对到。「查看原文证据 N 条 →」是当前舆情总结那张卡的入口（ctxKey 尾巴是
   `sum`、极性中性）；等「展开单帖详情」，那是证据行渲染出来才有的字。 */
const openProductEvidence = async (page) => {
  await page.getByText(/^查看原文证据 \d+ 条 →$/).first().click()
  await page.getByText('展开单帖详情', { exact: true }).first().waitFor({ timeout: 30_000 })
}

/* 产品相关 KOL 默认只显示前 5 位，而 3033 的第 9 位（2 条提及）是这一页唯一一个
   `dominantAttitude === null` 的样本 —— 下面白名单那条有意偏差的现场。不展开，它和
   设计源的差异就永远不会被这个 harness 看见。 */
const openProductKols = async (page) => {
  await page.getByText(/^展开全部 \d+ 位$/).first().click()
  await page.getByText('收起，只看前 5 位', { exact: true }).first().waitFor({ timeout: 30_000 })
}

/* 路由 → 设计源文件。与 src/lib/routes.js 的 PATH_BY_FILE 同一份对应关系；
   设计源静态站没有 index.html，必须访问具体 .dc.html 路径。 */
const SCREENS = [
  { path: '/official', file: 'official-activity.dc.html', label: '官号动态' },
  { path: '/kol', file: 'kol-activity.dc.html', label: 'KOL 影响力' },
  { path: '/kol/detail', file: 'kol-detail.dc.html', label: 'KOL 详情' },
  {
    path: '/sector',
    file: 'sector-overview.dc.html',
    label: '板块总览',
    views: [
      { label: '快速详情抽屉', act: openSectorDrawer },
      { label: '口径与数据状态', act: openSectorNotes },
    ],
  },
  {
    path: '/product',
    file: 'product-monitor.dc.html',
    label: '产品监控',
    views: [
      { label: '原文证据侧栏', act: openProductEvidence },
      { label: '产品相关 KOL 全部展开', act: openProductKols },
    ],
  },
]

/* ── 白名单 ────────────────────────────────────────────────────────────
 *
 * README「Deliberate deviations」记了 4 处有意偏差。逐条对照它们**会不会产生文本差异**：
 *
 *   1. 无异步等待轮询 —— 移植版同步 import radar-data.js，去掉了设计源等 window.RADAR
 *      的轮询。轮询期间设计源渲染的是空壳，稳定后两边一致；抓取时已等到稳定态，不产生
 *      文本差异。**不需要白名单条目。**
 *   2. URL 改写 —— history.replaceState 改的是地址栏，不是页面文本。**不需要。**
 *   3. 删掉的死代码 —— renderVals() 算了但模板不读的值（domains、subItems、heatBg、
 *      negCats…）。模板不读＝不渲染＝不在文本里。**不需要。**
 *   4. 补 key —— riskRows / evidence / treemap tiles 等补的 id、key 字段是 JSX 的
 *      列表键，不渲染。**不需要。**
 *
 * 也就是说这 4 处按设计就都是文本中性的。真出现差异说明偏差比 README 记的更深，
 * 那是 bug，不是豁免。
 *
 * 于是白名单里能有的只剩两类，往里加东西之前先问一句：出处是什么。
 *
 *   A. **设计源自己算错了，我们照抄就是撒谎**（前两条）。
 *   B. **设计源之后有人改了定案，页面必须跟着改口**（AI 徽章与验证声明那几条）。
 *      这一类不是「移植得不像」，是「设计源画的时候那条规则还不存在」——
 *      [ADR-0019](../../docs/adr/0019-ai-auto-publish-no-human-gate.md) 2026-09-11 由项目
 *      负责人裁决取消人工批准门槛，设计稿定稿于此之前。A 类只会越来越少，B 类只会随定案
 *      增加；两类都必须写明出处，区别在于 B 类的出处是一份**比设计源新**的文档。
 *      B 类条目意味着设计源那一页已经过时了：等设计稿按 ADR-0019 重画并重新镜像之后，
 *      对应条目应当删掉，而不是永久留着。
 */
const WHITELIST = [
  {
    screen: '/sector',
    match: /其中需合规关注/,
    source: 'CLAUDE.md 铁律 2 ／ PRD §3.6 六态表',
    note:
      '设计源这句写的是「其中需合规关注 27 条」，而 27 是把一只没扫过合规的自家产品'
      + '（3153，设计源 RISK_UNAVAILABLE）按 0 条加进去得到的 —— 那句 `|| 0` 让'
      + '「不知道」变成了「零条」。移植版渲染「数据暂不可用」。这是两边唯一一处'
      + '有意的文本差异，也是唯一一处移植版比设计源更准。',
  },
  {
    screen: '/product',
    match: /^(暂不可用|样本不足)$/,
    source: 'PRD §3.6 六态表 ／ §5 表 P8「主要态度（<3 条不输出）」／ 本期 spec',
    note:
      '产品相关 KOL 的「主要态度」列：有效提及少于 3 条时后端下发 null。设计源把这个'
      + 'null 渲染成「样本不足」，而 PRD §3.6 把「样本不足」留给了另一态——有样本、'
      + '低于判定阈值、因此不下结论；字段级 null 一律是「暂不可用」。两句话在六态表里'
      + '是不同的两行，混用会让人以为我们量过了。移植版按 PRD 渲染「暂不可用」。'
      + '（同一页脚注那句「提及少于 3 条不输出主要态度」逐字保留，它解释的是规则本身。）'
      + '3033 · d7 只有第 9 位 KOL（2 条）落在这一态，所以只有在「产品相关 KOL 全部'
      + '展开」这个视图里才会各豁免一处。',
  },
  {
    screen: '/product',
    match: /^AI 结论由模型自动生成，未经人工验证；每条可回到原文。$/,
    source: 'ADR-0019 §4「如实声明」／PRD §4.2 P7',
    note:
      'P7 右栏「统计区间／基准区间／有效样本／更新时间」之后多出来的一句。设计源没有它，'
      + '因为设计稿定稿时的规则还是「AI 结论要人批准才上页面」——那条规则下这句话是多余的。'
      + 'ADR-0019 取消了批准门槛，页面上的每一条 AI 结论都没有经过人工验证，于是这句话从'
      + '多余变成必需：不写它，读的人会按旧规则默认有人看过。三处必须一致（`/meta.aiValidation`、'
      + '板块总览 S6、产品监控 P7），这是第三处。',
  },
  {
    screen: '/sector',
    match: /^(AI 结论的验证程度|AI 结论由模型自动生成，未经人工验证；每条可回到原文。本期未安排人工复核.*)$/,
    source: 'ADR-0019 §4「如实声明」／PRD §4.1 S6',
    note:
      '同上一条，这是 S6「口径与数据状态」面板里的那一处：一个标题片段 ＋ 一段正文片段。'
      + '正文的前半句与 P7 逐字相同（同一个 `aiValidationNote()`，跟着 `/meta` 走而不是'
      + '两边各写死一份），后半句是这一页特有的补充。只在「口径与数据状态」展开态里出现。',
  },
  ...['/kol', '/kol/detail', '/official'].map((screen) => ({
    screen,
    /* /kol 的行徽章带置信度（「待确认 0.62」）、详情弹层里是光秃秃的「待确认」；
       /kol/detail 的发帖表是光秃秃的，/official 的带置信度。三页各取所需，不写成一条
       大而全的正则：`待确认 0.62` 这个形态在 /kol/detail 的「提及其他产品」表里两边**都**
       渲染（那一块还按老的置信度规则走，见 KolDetail.jsx:266），把它一并豁免会顺手把那块
       将来的回归也一起盖掉。 */
    match: screen === '/kol/detail'
      ? /^(AI 生成( · (可追溯原文|待确认))?|待确认)$/
      : screen === '/official'
        ? /^(AI 生成( · (可追溯原文|待确认))?|待确认 \d+(\.\d+)?)$/
        : /^(AI 生成( · (可追溯原文|待确认))?|待确认( \d+(\.\d+)?)?)$/,
    source: 'ADR-0019 §2「徽章而非门槛」／PRD §3.9 徽章词表',
    note:
      '帖子卡上那枚小徽章。设计源的规则是「置信度低于 0.7 就标『待确认』，否则什么都不标」；'
      + 'ADR-0019 把它换成了「每条都说清楚自己是怎么来的」：`pending` ⇒「AI 生成 · 可追溯原文」'
      + '（没有可定位证据时退为「AI 生成」），`needs_review` ⇒「AI 生成 · 待确认」，'
      + '`rejected` ⇒ 不显示。所以移植版这边的徽章**比设计源多得多**（设计源只给低置信的那几条'
      + '挂牌，移植版每条都挂），豁免条数会是三位数，那是预期。'
      + '两件事没跟着变，因此不在豁免范围内：一是「仅看待确认」筛选与各处「N 篇类型待确认」'
      + '计数——demo fixture 里 `needs_review` 与 `confidence < 0.7` 是同一批记录（429/2306 条'
      + '逐条核对过，零处不一致），两边数出来的数字仍然逐字相同，它们真差了就是真 bug；'
      + '二是合规那枚「AI 识别 · 待人工确认」，两边都是写死的常量，ADR-0019 §2 也要求它恒定。'
      + '真库下 `calibrated_confidence` 整列是 NULL，0.7 这个阈值在校准概率存在之前不生效。',
  })),
]

/* 抓一页的文本片段序列 ＋ 站外链接序列。act 是可选的展开操作（见顶部「展开态」）。 */
async function grab(page, url, act) {
  await page.goto(url, { waitUntil: 'networkidle', timeout: 60_000 })
  /* 操作要在稳定等待**之前**做：点击本身会触发新的取数（移植版这边是新的 read()），
     等待的对象是操作之后的终态。Playwright 的 click 自带可见性等待，够它渲染出来。 */
  if (act) await act(page)

  /* 两边都不是「DOM 就绪＝内容就绪」：设计源要等 radar-data.js 这个 <script> 跑完，
     移植版要等 Suspense 把接口数据取回来。等文本长度连续两次不变，比猜一个 sleep 靠谱。 */
  let last = -1
  let stable = 0
  for (let i = 0; i < 60 && stable < 2; i++) {
    const len = await page.evaluate(() => document.body.textContent.length)
    stable = len === last ? stable + 1 : 0
    last = len
    await page.waitForTimeout(250)
  }

  return page.evaluate(() => {
    const text = []
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      /* <script> / <style> 的内容也是文本节点，但它们不是页面上的字。 */
      const tag = n.parentElement && n.parentElement.tagName
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'TEMPLATE') continue
      /* Suspense fallback 的骨架屏（LoadingSkeleton）里有一行「正在加载 x / y 个数据块」。
         上面的稳定等待正常情况下已经等到它消失；这里再按属性排除一次，是为了它万一还在
         （后端慢）时差异指向真正的页面内容，而不是这行过场文案。 */
      if (n.parentElement && n.parentElement.closest('[data-loading-skeleton]')) continue
      const t = n.textContent.replace(/\s+/g, ' ').trim()
      if (t) text.push(t)
    }

    /* 只收站外链接。取 getAttribute 而不是 .href：后者会把相对地址补成绝对地址，
       两个站点端口不同，站内链接必然假红。以 http(s):// 开头的才是站外。 */
    const links = []
    for (const a of document.querySelectorAll('a[href]')) {
      const href = a.getAttribute('href')
      if (/^https?:\/\//i.test(href)) links.push(href)
    }
    return { text, links }
  })
}

/* 最长公共子序列，用来把两个片段序列对齐。页面片段数在数千量级，O(n·m) 的表跑得动。 */
function lcsDiff(a, b) {
  const n = a.length
  const m = b.length
  const dp = new Uint32Array((n + 1) * (m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * (m + 1) + j] =
        a[i] === b[j]
          ? dp[(i + 1) * (m + 1) + j + 1] + 1
          : Math.max(dp[(i + 1) * (m + 1) + j], dp[i * (m + 1) + j + 1])
    }
  }
  const ops = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ op: '=', i, j, text: a[i] })
      i++
      j++
    } else if (dp[(i + 1) * (m + 1) + j] >= dp[i * (m + 1) + j + 1]) {
      ops.push({ op: '-', i, j, text: a[i] })
      i++
    } else {
      ops.push({ op: '+', i, j, text: b[j] })
      j++
    }
  }
  while (i < n) ops.push({ op: '-', i, j, text: a[i++] })
  while (j < m) ops.push({ op: '+', i, j, text: b[j++] })
  return ops.filter((o) => o.op !== '=')
}

function exempt(screenPath, text) {
  return WHITELIST.find((w) => (!w.screen || w.screen === screenPath) && w.match.test(text))
}

async function up(origin, command, args, { cwd, env = {} }) {
  if (await alive(origin)) throw new Error(`${origin} 已被占用，测试需要独立服务`)
  const child = spawn(command, args, {
    cwd,
    env: { ...process.env, ...env },
    stdio: 'ignore',
    shell: process.platform === 'win32',
  })
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 500))
    if (await alive(origin)) return child
  }
  kill(child)
  throw new Error(`${origin} 起不来（${command}）`)
}

/* Windows 上 spawn 的是 npm.cmd（shell:true），child.kill() 只杀得掉那层 cmd，真正占着
   端口的 node 会活下来，下次跑 diff 就会连到一个跑着旧代码的站点。/t 连整棵进程树一起收。 */
function kill(child) {
  if (!child) return
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/f', '/t'], { stdio: 'ignore' })
  } else {
    child.kill()
  }
}

async function alive(origin) {
  try {
    await fetch(origin, { signal: AbortSignal.timeout(1500) })
    return true
  } catch {
    return false
  }
}

const args = process.argv.slice(2)
const keep = args.includes('--keep')
/* 按名字挑页：`official`、`kol/detail`、`sector`…… 也认前导斜杠，但别在 Git Bash 里那么写。
   给了名字却一页都没匹配上时直接报错——静默跑全量会让人以为自己只比了一页。 */
const only = args.filter((a) => !a.startsWith('--'))
const screens = only.length
  ? SCREENS.filter((s) => only.some((a) => s.path === '/' + a.replace(/^\/+/, '')))
  : SCREENS
if (only.length && !screens.length) {
  console.error(`没有这些页：${only.join(', ')}\n可选：${SCREENS.map((s) => s.path.slice(1)).join(', ')}`)
  process.exit(2)
}

const spawned = []
let browser
let failures = 0
let comparisons = 0

try {
  const python = path.join(BACKEND, '.venv', 'Scripts', process.platform === 'win32' ? 'python.exe' : 'python')
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
  spawned.push(await up(API_ORIGIN, python, [path.join(BACKEND, 'app.py')], {
    cwd: BACKEND,
    env: { DATA_PROVIDER: 'demo', DEMO_SCENARIO: 'demo', APP_PORT: '8017', APP_ENV: 'production' },
  }))
  spawned.push(await up(REACT_ORIGIN, npm, ['run', 'dev', '--', '--port', '5177', '--strictPort'], {
    cwd: FRONTEND,
    env: { VITE_API_BASE: `${API_ORIGIN}/api/v1` },
  }))
  spawned.push(await up(DESIGN_ORIGIN, npm, ['run', 'design', '--', '--strictPort'], { cwd: FRONTEND }))
  browser = await chromium.launch()

  for (const s of screens) {
    /* 默认状态永远比一次，然后每个展开态各比一次。 */
    for (const view of [null, ...(s.views || [])]) {
      comparisons++
      const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } })
      const errors = []
      page.on('pageerror', (e) => errors.push(String(e)))

      const react = await grab(page, REACT_ORIGIN + s.path, view && view.act)
      const design = await grab(page, `${DESIGN_ORIGIN}/${s.file}`, view && view.act)
      await page.close()

      const textDiff = lcsDiff(react.text, design.text)
      const linkDiff = lcsDiff(react.links, design.links)
      const real = {
        文字: textDiff.filter((d) => !exempt(s.path, d.text)),
        站外链接: linkDiff.filter((d) => !exempt(s.path, d.text)),
      }
      const waived = textDiff.length + linkDiff.length - real.文字.length - real.站外链接.length
      const bad = real.文字.length + real.站外链接.length

      const head = `${s.path}  ${s.label}${view ? ' · ' + view.label : ''}  ←→  ${s.file}`
      if (!bad && !errors.length) {
        const counts = `${react.text.length} 段文字 · ${react.links.length} 条站外链接`
        console.log(`\x1b[32m✓\x1b[0m ${head}  (${counts} 一致${waived ? `，白名单豁免 ${waived}` : ''})`)
        continue
      }

      failures++
      console.log(`\x1b[31m✗\x1b[0m ${head}`)
      for (const e of errors) console.log(`  \x1b[31mJS 报错\x1b[0m ${e}`)
      for (const [kind, ds] of Object.entries(real)) {
        if (!ds.length) continue
        const [a, b] = kind === '文字' ? [react.text, design.text] : [react.links, design.links]
        console.log(`  ${kind}：移植版 ${a.length} / 设计源 ${b.length}，差异 ${ds.length} 处`)
        for (const d of ds.slice(0, 40)) {
          const where = d.op === '-' ? `移植版第 ${d.i + 1} 项` : `设计源第 ${d.j + 1} 项`
          const sign = d.op === '-' ? '\x1b[31m只在移植版\x1b[0m' : '\x1b[33m只在设计源\x1b[0m'
          console.log(`    ${sign} ${where}: ${JSON.stringify(d.text.slice(0, 120))}`)
        }
        if (ds.length > 40) console.log(`    …… 另有 ${ds.length - 40} 处`)
      }
      if (waived) console.log(`  （另有 ${waived} 处白名单豁免）`)
      console.log()
    }
  }
} finally {
  if (browser) await browser.close()
  if (!keep) for (const c of spawned) kill(c)
}

/* 报「几页几次」而不是「五页」：展开态也是一次比对，只说页数会把覆盖面说小。 */
const scope = `${screens.length} 页 ${comparisons} 次比对`
console.log(
  failures
    ? `\n\x1b[31m${scope}，${failures} 次有差异\x1b[0m`
    : `\n\x1b[32m${scope}，逐字一致\x1b[0m`,
)
process.exit(failures ? 1 : 0)
