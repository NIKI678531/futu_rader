/* 处理进度侧栏、真实加载态与新增字段渲染的行为检查 —— opt-in，对着 mock 跑。
 *
 * ## 为什么单独一条脚本
 *
 * screen-diff 与 six-state 都对着 demo provider，而侧栏入口与新增字段的渲染分支只在
 * `dataProvider === 'sql'` 下出现 —— 那两条脚本永远走不进这些代码。本机又没有真库，
 * 于是拿 mock-progress-server.mjs（透传 demo 后端 ＋ 自答 /progress ＋ 注入新增字段）
 * 当后端，验证的是**行为**而不是逐字：开关、增量追加、过滤、自动跟随、关闭后不再请求，
 * 以及加载中不再整屏空白。
 *
 * 逐字仍归 screen-diff：这条脚本通过不代表 demo 下五屏没变；反过来 screen-diff 绿也不
 * 代表侧栏能用。两条各管一段。
 *
 * ## 断言清单
 *
 *   ① 首屏：接口人为延迟 800ms 时，300ms 内 Shell 导航已可见、骨架屏在；数据到齐后骨架消失
 *   ② 切区间：旧内容留着（[data-screen-label] 一直在），骨架屏不再整屏出现
 *   ③ 侧栏默认不渲染日志行，也没有一次 /progress 请求；旧的常显横幅不在
 *   ④ 点开：3s 内出现事件行；总览各行有值；吞吐／ETA 按契约格式化
 *   ⑤ 增量：再等两个轮询周期，事件数增加，且请求带了 after=<id>
 *   ⑥ 过滤：按阶段只剩该阶段；按代码只剩该代码；清掉恢复
 *   ⑦ 自动跟随：向上滚出现「回到最新」，点它回到底部并消失
 *   ⑧ Esc 关闭 → 之后 4s 内零次 /progress 请求；点遮罩也能关
 *   ⑨ 新增字段：产品监控 KPI 下限注记、「 · 待更新」徽章、evidenceCount、抽检文案（且无「已核验」）；
 *      板块总览 KPI 备注、榜单「待更新」小徽章
 *   ⑩ 侧栏里没有任何启停作业的按钮（工作台只读）
 *
 * 端口特意跟常规站点（8008／5173）与其他脚本（8017／8018／5174／5175／5177）岔开。
 *
 * 用法：npm run progress-check
 *       DEMO_ORIGIN=http://127.0.0.1:8031 npm run progress-check   # 对着已在跑的 demo 后端
 */
import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { chromium } from 'playwright'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const REPO = path.resolve(FRONTEND, '..')

const DEMO_PORT = 8020
const MOCK_PORT = 8021
const WEB_PORT = 5179
/* 给了 DEMO_ORIGIN 就不自起 demo 后端，mock 透传到它（联调别的分支的后端时用）。 */
const EXTERNAL_DEMO = process.env.DEMO_ORIGIN || null
const DEMO = EXTERNAL_DEMO || `http://127.0.0.1:${DEMO_PORT}`
const MOCK = `http://127.0.0.1:${MOCK_PORT}`
const WEB = `http://localhost:${WEB_PORT}`
const TICK = 700

const fail = []
const ok = (cond, name, detail) => { if (!cond) fail.push({ name, detail }) }

/* 记下这个 page 发出的每一个 /progress 请求：③ 与 ⑧ 都是「零次」断言，靠它数。 */
function trackProgress(page) {
  const hits = []
  page.on('request', (r) => { if (r.url().includes('/api/v1/progress')) hits.push(r.url()) })
  return hits
}

async function checkLoading(browser) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } })
  try {
    /* 把接口都拖慢 800ms：本机 demo 后端几十毫秒就回，骨架屏一闪而过测不到。 */
    await page.route('**/api/v1/**', async (route) => {
      await new Promise((r) => setTimeout(r, 800))
      await route.continue()
    })
    const t0 = Date.now()
    await page.goto(WEB + '/sector', { waitUntil: 'commit' })
    await page.getByText('板块总览', { exact: true }).first().waitFor({ timeout: 5000 })
    const navAt = Date.now() - t0
    ok(navAt < 1500, '① 首屏 Shell 导航要在数据到达前可见', `导航出现用了 ${navAt}ms（接口延迟 800ms，导航理应不等它）`)
    const skeleton = await page.locator('[data-loading-skeleton]').count()
    ok(skeleton === 1, '① 首屏取数期间是骨架屏，不是空白', `[data-loading-skeleton] 数量 ${skeleton}`)
    const hasLoadingText = await page.getByText(/正在加载 \d+ \/ \d+ 个数据块/).count()
    ok(hasLoadingText === 1, '① 骨架屏带「正在加载 x / y 个数据块」', `匹配 ${hasLoadingText} 处`)

    await page.locator('[data-screen-label]').waitFor({ timeout: 30_000 })
    ok((await page.locator('[data-loading-skeleton]').count()) === 0, '① 数据到齐后骨架消失', '')

    /* ② 切区间：观察 2 秒，整屏骨架不得再出现，屏幕根节点不得消失。 */
    await page.unroute('**/api/v1/**')
    await page.route('**/api/v1/**', async (route) => {
      await new Promise((r) => setTimeout(r, 500))
      await route.continue()
    })
    await page.getByText('近 14 天', { exact: true }).first().click()
    let sawSkeleton = false
    let lostScreen = false
    for (let i = 0; i < 20; i++) {
      const [sk, sc] = await Promise.all([
        page.locator('[data-loading-skeleton]').count(),
        page.locator('[data-screen-label]').count(),
      ])
      if (sk > 0) sawSkeleton = true
      if (sc === 0) lostScreen = true
      await page.waitForTimeout(100)
    }
    ok(!sawSkeleton, '② 切区间时不退回整屏骨架', '切区间过程中出现了 [data-loading-skeleton]')
    ok(!lostScreen, '② 切区间时旧内容保持可见', '切区间过程中 [data-screen-label] 消失过')
    await page.getByText('近 14 天', { exact: true }).first().waitFor()
  } finally {
    await page.close()
  }
}

async function checkDrawer(browser) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } })
  const hits = trackProgress(page)
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  try {
    await page.goto(WEB + '/sector', { waitUntil: 'networkidle', timeout: 60_000 })
    await page.locator('[data-screen-label]').waitFor({ timeout: 30_000 })

    /* ③ 默认态 */
    const btn = page.locator('[data-progress-button]')
    ok((await btn.count()) === 1, '③ sql 下 Shell 右上角有「处理进度」入口', `按钮数量 ${await btn.count()}`)
    ok((await page.locator('[data-progress-drawer]').count()) === 0, '③ 侧栏默认不渲染', '')
    ok((await page.locator('[data-progress-event]').count()) === 0, '③ 默认 DOM 里没有日志行', '')
    ok(hits.length === 0, '③ 没打开之前零次 /progress 请求', `发出了 ${hits.length} 次：${hits.join(' ')}`)
    const body0 = await page.evaluate(() => document.body.innerText)
    ok(!/自家分析 \d+\/61/.test(body0), '③ 旧的常显进度横幅已去掉', '页面正文里仍有「自家分析 x/61」')
    const dot = await page.locator('[data-progress-dot]').getAttribute('data-progress-dot')
    ok(dot === 'running', '③ 状态点按 /meta.analysisProgress 着色（运行中）', `data-progress-dot=${dot}`)

    /* ④ 打开 */
    await btn.click()
    await page.locator('[data-progress-drawer]').waitFor({ timeout: 3000 })
    await page.locator('[data-progress-event]').first().waitFor({ timeout: 3000 })
    const n0 = await page.locator('[data-progress-event]').count()
    ok(n0 >= 40, '④ 打开后 3s 内出现事件行（首屏带最近 200 条）', `只有 ${n0} 行`)
    const ov = await page.locator('[data-progress-overview]').innerText()
    ok(/自家分析完成\s+\d+ \/ 61/.test(ov), '④ 总览：自家分析完成 x / 61', ov)
    ok(/L1 学生队列\s+pending \d+ \/ done \d+/.test(ov), '④ 总览：L1 队列 pending/done', ov)
    ok(/L2 Luna 队列\s+pending \d+ \/ done \d+/.test(ov), '④ 总览：L2 队列 pending/done', ov)
    ok(/帖子标注任务\s+pending 37/.test(ov), '④ 总览：帖子任务计数', ov)
    ok(/KOL 评论观点任务\s+暂无内容/.test(ov), '④ 总览：空字典是「暂无内容」', ov)
    ok(/待更新汇总产品\s+\d+ 只/.test(ov), '④ 总览：待更新汇总产品数', ov)
    ok(/当前吞吐（5 分钟）\s+2\.35 条\/秒/.test(ov), '④ 总览：吞吐两位小数 条/秒', ov)
    ok(/预计剩余\s+[\d.]+ 分钟/.test(ov), '④ 总览：ETA 秒换算成分钟', ov)
    const first = await page.locator('[data-progress-event]').first().innerText()
    ok(/^\[\d\d:\d\d:\d\d\] \[(规则与近重复|学生模型|Luna|汇总|编排)\]/.test(first), '④ 日志行格式 [HH:MM:SS] [阶段中文名]', first)
    ok((await page.locator('[data-progress-event][data-level="error"]').count()) > 0, '④ 有 error 行', '')
    ok((await page.locator('[data-progress-event][data-level="warn"]').count()) > 0, '④ 有 warn 行', '')
    const errColor = await page.locator('[data-progress-event][data-level="error"]').first().evaluate((el) => getComputedStyle(el).color)
    const infoColor = await page.locator('[data-progress-event][data-level="info"]').first().evaluate((el) => getComputedStyle(el).color)
    ok(errColor !== infoColor, '④ error 行与 info 行颜色不同', `${errColor} vs ${infoColor}`)

    /* ⑩ 只读 */
    const buttons = await page.locator('[data-progress-drawer] button').allInnerTexts()
    const forbidden = buttons.filter((t) => /启动|停止|暂停|重跑|重试|开始|运行|start|stop|run|retry/i.test(t))
    ok(forbidden.length === 0, '⑩ 侧栏没有启停作业的按钮', forbidden.join(' | '))

    /* ⑤ 增量 */
    const hitsBefore = hits.length
    await page.waitForTimeout(3500)
    const n1 = await page.locator('[data-progress-event]').count()
    ok(n1 > n0, '⑤ 3s 轮询后事件追加', `${n0} → ${n1}`)
    const inc = hits.slice(hitsBefore).filter((u) => /\/progress\/events\?/.test(u))
    ok(inc.length >= 1, '⑤ 走的是 /progress/events 增量端点', hits.slice(hitsBefore).join(' '))
    ok(inc.every((u) => /after=[1-9]\d*/.test(u)), '⑤ 增量请求带 after=<latestEventId>', inc.join(' '))

    /* ⑥ 过滤 */
    await page.locator('[data-progress-stage]').selectOption('L2')
    await page.waitForTimeout(100)
    const stages = await page.locator('[data-progress-event]').evaluateAll((els) => els.map((e) => e.getAttribute('data-stage')))
    ok(stages.length > 0 && stages.every((s) => s === 'L2'), '⑥ 按阶段过滤只剩 L2', `${stages.length} 行，阶段集合 ${[...new Set(stages)]}`)
    await page.locator('[data-progress-stage]').selectOption('all')
    await page.locator('[data-progress-code]').fill('3067')
    await page.waitForTimeout(100)
    const rows = await page.locator('[data-progress-event]').allInnerTexts()
    ok(rows.length > 0 && rows.every((t) => t.includes('3067')), '⑥ 按产品代码过滤只剩该代码', `${rows.length} 行`)
    await page.locator('[data-progress-code]').fill('')
    await page.waitForTimeout(100)
    ok((await page.locator('[data-progress-event]').count()) >= n1, '⑥ 清掉过滤恢复全部', '')

    /* ⑦ 自动跟随 */
    const log = page.locator('[data-progress-log]')
    const atBottom = await log.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 24)
    ok(atBottom, '⑦ 默认贴底跟随', '')
    await log.evaluate((el) => { el.scrollTop = 0 })
    await page.locator('[data-progress-follow]').waitFor({ timeout: 2000 })
    await page.waitForTimeout(TICK * 2)
    const stillTop = await log.evaluate((el) => el.scrollTop < 40)
    ok(stillTop, '⑦ 用户上翻后新事件不再把视口拽回底部', '')
    await page.locator('[data-progress-follow]').click()
    await page.waitForTimeout(100)
    ok((await page.locator('[data-progress-follow]').count()) === 0, '⑦ 点「回到最新」后按钮消失', '')
    ok(await log.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 24), '⑦ 点「回到最新」后回到底部', '')

    /* ⑧ 关闭 */
    await page.keyboard.press('Escape')
    await page.waitForTimeout(50)
    ok((await page.locator('[data-progress-drawer]').count()) === 0, '⑧ Esc 关闭侧栏', '')
    const hitsClosed = hits.length
    await page.waitForTimeout(4000)
    ok(hits.length === hitsClosed, '⑧ 关闭后零次 /progress 请求', `多了 ${hits.length - hitsClosed} 次：${hits.slice(hitsClosed).join(' ')}`)
    await btn.click()
    await page.locator('[data-progress-drawer]').waitFor({ timeout: 3000 })
    await page.locator('[data-progress-overlay]').click({ position: { x: 20, y: 300 } })
    await page.waitForTimeout(50)
    ok((await page.locator('[data-progress-drawer]').count()) === 0, '⑧ 点遮罩关闭侧栏', '')

    for (const e of errors) fail.push({ name: '侧栏 · JS 报错', detail: e })
  } finally {
    await page.close()
  }
}

/* mock 改过之后的池：评论量第 1 名正数（列上红标）、第 2 名 null、第 3 名 0；再找一只
   `alerts > 0` 的当「仅有舆情」的正例（列上的红标看 negMentions，筛选看 alerts，两个不是
   同一个数）。直接问 mock 而不是把代码写死：demo fixture 换一版这里就不用跟着改。 */
async function negTriple() {
  const body = await (await fetch(MOCK + '/api/v1/pool?range=d7')).json()
  const d = body.data
  const sorted = d.list.slice().sort((a, b) => b.comments - a.comments)
  const [pos, nul, zero] = sorted.map((o) => o.code)
  if (d.negMentions[nul] !== null || d.negMentions[zero] !== 0 || !(d.negMentions[pos] > 0)) {
    throw new Error(`mock 的舆情三态没按约定注入：${nul}=${d.negMentions[nul]} ${zero}=${d.negMentions[zero]} ${pos}=${d.negMentions[pos]}`)
  }
  const firm = sorted.find((o) => d.alerts[o.code] > 0)
  if (!firm) throw new Error('demo 池里没有 alerts > 0 的产品，「仅有舆情」正例找不到')
  return { pos, nul, zero, posN: d.negMentions[pos], firm: firm.code }
}

async function checkFields(browser) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1400 } })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  try {
    await page.goto(WEB + '/product?code=3033&range=d7', { waitUntil: 'networkidle', timeout: 60_000 })
    await page.locator('[data-screen-label]').waitFor({ timeout: 30_000 })
    await page.waitForTimeout(500)
    let text = await page.evaluate(() => document.body.innerText)
    ok(text.includes('（3 帖转发数未知 · 下限）'), '⑨ 产品监控 KPI 备注带下限注记', '')
    ok(text.includes('AI 生成 · 可追溯原文 · 待更新'), '⑨ 舆情总结徽章追加「 · 待更新」', '')
    ok(text.includes('查看原文证据 7 条'), '⑨ 证据条数用 evidenceCount', '')
    ok(text.includes('人工核对 200 条（2026-09-12），态度准确率 87.0%、相关性准确率 93.5%'), '⑨ spot_check 抽检文案', '')
    /* 只查那一句：设计源里「KOL 身份来自已核验名单」说的是名单，不是 AI 结论，那是合法文案。 */
    const note = (text.match(/AI 结论由模型自动生成[^\n]*/) || [''])[0]
    ok(note.length > 0 && !note.includes('已核验'), '⑨ 验证程度那句里不得出现「已核验」', note)
    /* 「待更新」该出现在：舆情总结徽章（summary.stale）、正负观点（themes 顶层）、话题
       （topics **逐行** stale）、关联竞品（competitors 顶层）、阶段观点（stages 顶层）—— 五处。
       少一处就是某条契约的读法错了。 */
    const staleN = (text.match(/待更新/g) || []).length
    ok(staleN === 5, '⑨ 总结／主题／话题（逐行）／竞品／阶段五处都挂「待更新」', `出现 ${staleN} 次，期望 5`)

    await page.goto(WEB + '/sector', { waitUntil: 'networkidle', timeout: 60_000 })
    await page.locator('[data-screen-label]').waitFor({ timeout: 30_000 })
    await page.waitForTimeout(500)
    text = await page.evaluate(() => document.body.innerText)
    ok(text.includes('（5 帖转发数未知 · 下限）'), '⑨ 板块总览 KPI 备注带自家合计下限注记', '')
    ok(text.includes('待更新'), '⑨ 榜单热议总结格有「待更新」小徽章', '')

    /* ⑪ 舆情三态。mock 把评论量第 2 名的 alerts／negMentions 改成 null、第 3 名改成 0，
       第 1 名（3033）保持正数。列上：正数红标、null 灰字「暂不可用」、0 走设计源原路径
       （不出红标也不出灰字 —— 0 是「查过了、没有」，不是缺失）。 */
    const { nul, zero, pos, posN, firm } = await negTriple()
    const row = (code) => page.locator(`xpath=//div[contains(@style,"54px")]/span[normalize-space(text())="${code}"]/parent::div`).first()
    const NA_TITLE = 'span[title="舆情条数暂不可用：该产品尚未做负面类别标注"]'
    const BADGE = 'span[title="可归类为需关注问题的内容条数"]'
    ok((await row(nul).locator(NA_TITLE).count()) === 1 && (await row(nul).locator(BADGE).count()) === 0,
      '⑪ negMentions 为 null 的行：灰字「暂不可用」、无红标', `code ${nul}`)
    ok((await row(zero).locator(NA_TITLE).count()) === 0 && (await row(zero).locator(BADGE).count()) === 0,
      '⑪ negMentions 为 0 的行：无红标、也不写「暂不可用」', `code ${zero}`)
    ok((await row(pos).locator(BADGE).count()) === 1 && (await row(pos).locator(BADGE).innerText()) === String(posN),
      '⑪ negMentions 为正数的行：红标带数字', `code ${pos} 期望 ${posN}`)
    ok((await page.locator(NA_TITLE).count()) === 1, '⑪ 整表只有那一行是「暂不可用」', `实际 ${await page.locator(NA_TITLE).count()} 处`)

    /* 抽屉：总结徽章（summary.stale）、正负观点（themes 顶层）、负面舆情摘要（negCats **逐行**）、
       关联竞品（competitors 顶层）四处「待更新」。 */
    await row(pos).click()
    await page.getByText('当前舆情总结', { exact: true }).first().waitFor({ timeout: 30_000 })
    await page.waitForTimeout(300)
    const drawerStale = (await page.evaluate(() => document.body.innerText).then((t) => t.match(/待更新/g)) || []).length
    ok(drawerStale === 5, '⑪ 抽屉里总结／主题／负面摘要（逐行）／竞品四处「待更新」（加榜单那枚共 5）', `出现 ${drawerStale} 次`)
    await page.getByText('✕', { exact: true }).first().click()
    await page.getByText('当前舆情总结', { exact: true }).first().waitFor({ state: 'hidden', timeout: 5000 })

    /* 「仅有舆情」按 `alerts > 0` 判：null 与 0 都被筛掉，alerts 为正的留下。 */
    await page.getByText('仅有舆情', { exact: true }).first().click()
    await row(firm).waitFor({ timeout: 5000 })
    ok((await row(nul).count()) === 0, '⑪ 「仅有舆情」筛掉 alerts 为 null 的产品', `code ${nul}`)
    ok((await row(zero).count()) === 0, '⑪ 「仅有舆情」筛掉 alerts 为 0 的产品', `code ${zero}`)
    ok((await row(firm).count()) === 1, '⑪ 「仅有舆情」留下 alerts 为正的产品', `code ${firm}`)
    for (const e of errors) fail.push({ name: '新增字段 · JS 报错', detail: e })
  } finally {
    await page.close()
  }
}

/* ── 进程 ───────────────────────────────────────────────────────────── */

function python() {
  return path.join(REPO, 'backend', '.venv', 'Scripts', process.platform === 'win32' ? 'python.exe' : 'python')
}
function npm() { return process.platform === 'win32' ? 'npm.cmd' : 'npm' }

/* POSIX 上 `npm run dev` 是 npm → sh → vite 三层，只 kill 最外层会留下 vite 孤儿占着端口，
   下一次跑直接「已被占用」。所以 detached 起成自己的进程组，收尾时杀整组（负 pid）。
   Windows 走 taskkill /t，同样是整棵树。 */
async function boot(label, origin, cmd, args, { cwd, env }) {
  if (await alive(origin)) throw new Error(`${origin} 已经被别的进程占着（${label} 需要独占它）`)
  const win = process.platform === 'win32'
  const child = spawn(cmd, args, { cwd, env: { ...process.env, ...env }, stdio: 'ignore', shell: win, detached: !win })
  for (let i = 0; i < 160; i++) {
    await new Promise((r) => setTimeout(r, 500))
    if (await alive(origin)) return child
  }
  kill(child)
  throw new Error(`${label} 起不来：${origin}`)
}
async function alive(origin) {
  try { await fetch(origin, { signal: AbortSignal.timeout(1500) }); return true } catch { return false }
}
function kill(child) {
  if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(child.pid), '/f', '/t'], { stdio: 'ignore' })
  else { try { process.kill(-child.pid, 'SIGTERM') } catch { child.kill() } }
}

async function main() {
  const procs = []
  let browser
  try {
    if (EXTERNAL_DEMO) {
      if (!(await alive(EXTERNAL_DEMO))) throw new Error(`DEMO_ORIGIN=${EXTERNAL_DEMO} 连不上`)
    } else {
      procs.push(await boot('demo 后端', DEMO, python(), [path.join(REPO, 'backend', 'app.py')], {
        cwd: path.join(REPO, 'backend'),
        env: { DATA_PROVIDER: 'demo', APP_PORT: String(DEMO_PORT), APP_ENV: 'production' },
      }))
    }
    procs.push(await boot('mock', MOCK + '/api/v1/progress', process.execPath,
      [path.join(HERE, 'mock-progress-server.mjs'), '--port', String(MOCK_PORT), '--upstream', DEMO, '--tick', String(TICK)],
      { cwd: FRONTEND, env: {} }))
    procs.push(await boot('前端', WEB, npm(), ['run', 'dev', '--', '--port', String(WEB_PORT), '--strictPort'], {
      cwd: FRONTEND,
      env: { VITE_API_BASE: `${MOCK}/api/v1` },
    }))

    browser = await chromium.launch()
    await checkLoading(browser)
    await checkDrawer(browser)
    await checkFields(browser)
  } finally {
    if (browser) await browser.close()
    for (const p of procs) if (p) kill(p)
  }

  if (fail.length) {
    console.log(`\x1b[31m✗ ${fail.length} 条不过\x1b[0m`)
    for (const f of fail) console.log(`  \x1b[31m✗\x1b[0m ${f.name}${f.detail ? '\n      ' + String(f.detail).slice(0, 400) : ''}`)
    process.exit(1)
  }
  console.log('\x1b[32m✓ 处理进度侧栏／加载态／新增字段渲染检查全部通过\x1b[0m')
}

await main()
