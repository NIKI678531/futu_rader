/* 真实数据冒烟：把五个页面放进真浏览器，对着 `DATA_PROVIDER=sql` 的后端跑一遍。

   为什么 `npm test` 里的三道关口盖不住这件事：
   - `guards` 是静态 grep，看不见运行时；
   - `six-state` 打的是 `DEMO_SCENARIO=sixstate` 那份**手工构造**的缺失态 fixture；
   - `diff` 比的是演示数据下移植版与设计源逐字一致。
   三道都在演示供数下跑。而 `sql` provider 的缺失面**大得多**：13 个帖子字段、
   摘要、主题、负面归类、竞品、合规、话题、K 线、阶段观点、KOL 提及、原文证据
   整块为 `null`（ADR-0017）。一个 `null.slice` 就是整屏白，而三道关口全绿。

   这条检查的完成标准逐字取自 `docs/ai-data-integration-runbook.md` Gate 1：
   「`annotations=0` 时五页仍稳定显示真实事实和准确缺失态。」

   **它不进 `npm test`**：跑它需要本机有那份 5.3 GB 瘦库（仓库外、不进 git），
   CI 与没建库的同事都没有。所以是 opt-in：

    cd backend && .venv/Scripts/python app.py
    cd frontend && node scripts/real-data-check.mjs

   判定分两层：① 页面不许抛（`pageerror` 一条都不行，错误边界兜住的也算）；
   ② 正文里不许出现 POISON 里的任何一串 —— 那些是「没兜住的 null」在页面上的
   长相（`NaN`／`undefined`／`null`／`[object Object]`／`Infinity`），
   外加两句错误边界文案。计数只是给人看的，不参与判定。
   五页之后还有一组针对 /sector 的行为断言（加载态与处理进度侧栏），见文末
   checkLoadingAndDrawer 上方的注释。 */

import { spawn, spawnSync } from 'node:child_process'
import { chromium } from 'playwright'

const API = process.env.API || 'http://127.0.0.1:8008/api/v1'
const PORT = Number(process.env.PORT || 5176)

/* 第三项是「进屏之后还要点开的东西」。板块总览的产品抽屉是这里唯一**必须**点的：
   摘要／主题／负面归类／竞品／合规五块整块 null 全在抽屉里，首屏一个都碰不到。
   产品监控的证据抽屉够不着 —— 打开它的入口（主题卡「证据 N 条 →」、总结的支撑原文、
   风险行）在真实数据下本身就是缺失态，压根不渲染；那条路径这里验不了。 */
const PAGES = [
  ['/official', '官号动态', null],
  ['/kol', 'KOL 影响力', null],
  ['/kol/detail', 'KOL 详情', null],
  ['/sector', '板块总览', { label: '产品抽屉', find: (page) => page.locator('[style*="cursor:pointer"],[style*="cursor: pointer"]').filter({ hasText: /评论\s*[\d,]+/ }).first() }],
  ['/product', '产品监控', null],
]

/* 「没兜住的 null」在页面上的长相。`null` 与 `undefined` 是字面量漏进 JSX；
   `NaN` 是 null 进了算术；`[object Object]` 是对象进了字符串拼接；
   `Infinity` 是除以 0。后两句是错误边界自己的文案 —— 它出现即代表这一屏没了。 */
const POISON = ['NaN', 'undefined', 'null', '[object Object]', 'Infinity', '后端服务连不上', '页面渲染失败']

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  /* 起法与 `six-state.mjs::boot` 一致：`npm run dev`＋`shell: true`。别图省事直接
     spawn `vite`／`npx vite` —— Windows 上那两条路径会静默起不来，而浏览器报出来的是
     ERR_CONNECTION_REFUSED，看着像页面挂了，其实服务器根本没启动。 */
  const vite = spawn(
    process.platform === 'win32' ? 'npm.cmd' : 'npm',
    ['run', 'dev', '--', '--port', String(PORT), '--strictPort'],
    {
      env: { ...process.env, VITE_API_BASE: API },
      stdio: 'ignore',
      shell: process.platform === 'win32',
    },
  )
  /* `localhost` 而不是 `127.0.0.1`：vite 默认只监听 `[::1]`，拿 IPv4 字面量去探活
     会一直连不上，然后报成「前端起不来」。同 `six-state.mjs` 的 `WEB`。 */
  const base = `http://localhost:${PORT}`
  let up = false
  for (let i = 0; i < 120 && !up; i++) {
    await sleep(500)
    try { await fetch(base + '/', { signal: AbortSignal.timeout(1500) }); up = true } catch { /* 还没起来 */ }
  }
  if (!up) { kill(vite); throw new Error(`前端起不来：${base}`) }

  const browser = await chromium.launch()
  let bad = 0
  try {
    for (const [route, label, open] of PAGES) {
      const page = await browser.newPage()
      const errs = []
      page.on('pageerror', (e) => errs.push(String(e.message || e)))
      await page.goto(base + route, { waitUntil: 'domcontentloaded' })
      await page.locator('[data-screen-label], [data-screen-error], [data-screen-crash]').first().waitFor({ state: 'visible', timeout: 90000 })
      await sleep(600)
      if (open) {
        const target = open.find(page)
        if (await target.count()) { await target.click(); await sleep(600) }
        else errs.push(`点不开「${open.label}」：找不到入口`)
      }
      const text = await page.evaluate(() => document.body.innerText)
      const hits = POISON.filter((w) => text.includes(w))
      const na = (text.match(/暂不可用/g) || []).length
      const empty = (text.match(/暂无相关内容|暂无内容/g) || []).length
      const ok = text.trim().length > 0 && errs.length === 0 && hits.length === 0
      if (!ok) bad++
      console.log(
        `${ok ? '✓' : '✗'} ${route.padEnd(12)} ${label.padEnd(12)}` +
        ` 文字 ${String(text.length).padStart(5)} 字 · 「暂不可用」${String(na).padStart(3)} 处` +
        ` · 空态 ${String(empty).padStart(3)} 处`,
      )
      errs.forEach((e) => console.log('    抛错: ' + e))
      hits.forEach((w) => console.log('    命中禁词: ' + w))
      await page.close()
    }
    bad += await checkLoadingAndDrawer(browser, base)
  } finally {
    await browser.close()
    kill(vite)
  }

  if (bad) { console.log(`\n✗ ${bad} 个页面没过`); process.exit(1) }
  console.log('\n✓ 五页在真实数据下都稳定，且没有未兜住的 null')
}

/* ── 加载态与处理进度侧栏 ──────────────────────────────────────────────
 *
 * 这几条只有对着 `sql` 后端才有意义：侧栏入口按钮只在 `dataProvider === 'sql'` 时渲染，
 * 而「首屏不空白」在真库的取数时长下才看得出来（demo 后端几十毫秒就回）。断言：
 *
 *   ① 首屏：从第一个 /api/v1 请求发出算起，300ms 内 Shell 导航可见（导航不等数据）
 *   ② 切区间：旧内容一直在（[data-screen-label] 不消失），整屏骨架不再出现
 *   ③ 侧栏默认不渲染，DOM 里没有日志行，也没有一次 /progress 请求
 *   ④ 打开后 3s 内出现事件行（后端确有事件时）；后端一条事件都没有则应显示「暂无事件」
 *   ⑤ 关闭后 4s 内零次 /progress 请求
 *
 * ① 从第一个接口请求起算而不是从 domcontentloaded 起算：dev 模式下 Vite 首次转译模块
 * 可能要一两秒，那是构建工具的事，不是页面在等数据。 */
async function checkLoadingAndDrawer(browser, base) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } })
  const fails = []
  const progressHits = []
  page.on('request', (r) => { if (r.url().includes('/api/v1/progress')) progressHits.push(r.url()) })
  try {
    const firstApi = page.waitForRequest((r) => r.url().includes('/api/v1/'), { timeout: 90000 })
    await page.goto(base + '/sector', { waitUntil: 'domcontentloaded' })
    await firstApi
    const t0 = Date.now()
    try {
      await page.getByText('板块总览', { exact: true }).first().waitFor({ state: 'visible', timeout: 300 })
    } catch {
      fails.push(`① 首屏 Shell 导航在第一个接口请求后 300ms 内不可见（${Date.now() - t0}ms 仍没有）`)
    }
    await page.locator('[data-screen-label], [data-screen-error], [data-screen-crash]').first().waitFor({ state: 'visible', timeout: 90000 })
    if (!(await page.locator('[data-screen-label]').count())) {
      fails.push('板块总览没渲染出来，后面的断言跑不了')
      return report(fails)
    }
    await sleep(400)

    /* ② 切区间。真库下 d14 的整池要取一会儿，观察 3 秒或直到区间文字换掉。 */
    const chip = page.getByText('近 14 天', { exact: true }).first()
    if (await chip.count()) {
      await chip.click()
      let sawSkeleton = false
      let lostScreen = false
      for (let i = 0; i < 30; i++) {
        const [sk, sc] = await Promise.all([
          page.locator('[data-loading-skeleton]').count(),
          page.locator('[data-screen-label]').count(),
        ])
        if (sk > 0) sawSkeleton = true
        if (sc === 0) lostScreen = true
        await sleep(100)
      }
      if (sawSkeleton) fails.push('② 切区间时出现了整屏骨架 [data-loading-skeleton]（应保留旧内容变淡）')
      if (lostScreen) fails.push('② 切区间时 [data-screen-label] 消失过（旧内容没保住）')
    } else {
      fails.push('② 找不到「近 14 天」区间预设，切区间断言跑不了')
    }

    /* ③ 默认态 */
    if (await page.locator('[data-progress-drawer]').count()) fails.push('③ 侧栏默认就渲染了')
    if (await page.locator('[data-progress-event]').count()) fails.push('③ 默认 DOM 里有日志行')
    if (progressHits.length) fails.push(`③ 没打开侧栏就发了 ${progressHits.length} 次 /progress 请求`)
    const btn = page.locator('[data-progress-button]')
    if (!(await btn.count())) {
      fails.push('③ 没有「处理进度」入口按钮（/meta.dataProvider 不是 sql？）')
      return report(fails)
    }

    /* ④ 打开。后端有没有事件从 Node 这边直接问一次 /progress —— 不读浏览器那份响应体：
       侧栏用 `cache: 'no-store'` 取活数据，Chromium 不给 DevTools 保留 no-store 响应的 body，
       Playwright 的 response.json() 会失败。 */
    let n = null
    try {
      const probe = await fetch(API + '/progress', { headers: { Accept: 'application/json' } })
      if (!probe.ok) fails.push(`④ /progress 返回 ${probe.status}（后端还没有这个端点？）`)
      else {
        const body = await probe.json()
        n = body && body.data && Array.isArray(body.data.events) ? body.data.events.length : 0
      }
    } catch (e) { fails.push('④ 探测 /progress 失败：' + (e.message || e)) }
    await btn.click()
    if (n != null) {
      const opened = Date.now()
      if (n > 0) {
        try { await page.locator('[data-progress-event]').first().waitFor({ timeout: 3000 }) }
        catch { fails.push(`④ 后端给了 ${n} 条事件，打开后 3s 内页面上却没有事件行`) }
      } else {
        try { await page.getByText('暂无事件', { exact: true }).waitFor({ timeout: 3000 }) }
        catch { fails.push('④ 后端一条事件都没有，侧栏却没显示「暂无事件」') }
      }
      const rows = await page.locator('[data-progress-event]').count()
      console.log(`  侧栏：/progress 事件 ${n} 条，${Date.now() - opened}ms 后页面上 ${rows} 行`)
    }

    /* ⑤ 关闭 */
    await page.keyboard.press('Escape')
    await sleep(50)
    if (await page.locator('[data-progress-drawer]').count()) fails.push('⑤ Esc 没关掉侧栏')
    const closedAt = progressHits.length
    await sleep(4000)
    if (progressHits.length > closedAt) fails.push(`⑤ 关闭后仍发了 ${progressHits.length - closedAt} 次 /progress 请求`)
    return report(fails)
  } finally {
    await page.close()
  }

  function report(list) {
    console.log(`${list.length ? '✗' : '✓'} 加载态与处理进度侧栏（/sector）`)
    list.forEach((f) => console.log('    ' + f))
    return list.length ? 1 : 0
  }
}

/* 同 `six-state.mjs`：Windows 上 shell:true 起的是一层 cmd，kill 父进程会留下孤儿占住端口。 */
function kill(child) {
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/f', '/t'], { stdio: 'ignore' })
  } else {
    child.kill()
  }
}

main().catch((e) => { console.error(e); process.exit(1) })
