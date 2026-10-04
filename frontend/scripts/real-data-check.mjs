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
   外加两句错误边界文案。计数只是给人看的，不参与判定。 */

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
  ['/sector', '板块总览', { label: '产品抽屉', find: (page) => page.locator('[data-product-code]').first() }],
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
  } finally {
    await browser.close()
    kill(vite)
  }

  if (bad) { console.log(`\n✗ ${bad} 个页面没过`); process.exit(1) }
  console.log('\n✓ 五页在真实数据下都稳定，且没有未兜住的 null')
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
