/* 工单 08 的「排名稳定性验收」：一次性人工核对脚本，不进 npm test。
 *
 * 静态部分已经有守卫：后端 test_ranks_do_not_take_filter_parameters 钉住 /ranks 不吃筛选参数，
 * 屏幕代码里排名只有 rk.map[code] 一处读法。这里补的是**交互**那一段——
 * 真的在浏览器里点一遍板块、范围、搜索、开关，看那个数字有没有动。
 *
 *   cd frontend && npm run dev            # :5173，连演示后端 :8008
 *   node ../.scratch/phase-1-api-integration/rank-stability.mjs
 */
/* 脚本不在 frontend/ 里，node 从**脚本自己的目录**往上找 node_modules，找不到 playwright；
   .scratch 是 gitignore 的临时地，不为了它建一份 package.json。 */
const { chromium } = await import(
  new URL('../../frontend/node_modules/playwright/index.mjs', import.meta.url).href
)

const WEB = process.env.WEB || 'http://localhost:5173'
/* 挑 3133：全市场第 25 名、板块是 A股。挑中游而不是第 1 名，是为了让「按可见集重算」
   这个错误真的能被看见 —— 第 1 名在几乎任何筛选下都还是第 1 名，测不出东西来。
   切到 A股 后可见集只剩十来只，重算的话它会掉到个位数。 */
const CODE = '3133'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 1400 } })
const seen = []
const fail = []

async function rank(label) {
  await page.waitForTimeout(600)
  const txt = await page.evaluate(() => document.body.innerText)
  const m = txt.match(/全市场评论量排名\s*\n?\s*第\s*(\d+)\s*\n?\s*／\s*(\d+)\s*只/)
  if (!m) { fail.push(`${label}：页面上找不到排名`); return }
  seen.push({ label, rank: m[1], total: m[2] })
}

await page.goto(`${WEB}/product?code=${CODE}`, { waitUntil: 'networkidle', timeout: 60_000 })
await rank('初始')

/* 板块／新品筛选与搜索框都在「切换产品」抽屉里，不打开就点不到。
   而且必须在抽屉**内部**取元素：板块名在页头身份卡上也有一个同名标签（「A股」），
   直接按文字找会点到那个死标签上，什么都没发生，测试却一路绿。 */
await page.getByText('切换产品').click()
/* 这一页有两个同占位符的搜索框：页顶筛选条的快捷搜索，和抽屉里的产品搜索。都要点到。 */
const boxes = page.locator('input[placeholder="搜索产品代码或名称"]')
const quick = boxes.first()
const box = boxes.last()
const bar = box.locator('xpath=..')          // 搜索框 + 板块 chips + 新品分段器，一整条筛选栏
/* 「全部」在这条栏里有两个（板块一个、新品一个），nth 指明是哪一个。 */
const chip = (t, i = 0) => bar.getByText(t, { exact: true }).nth(i)

for (const [label, act] of [
  ['切板块 → A股', () => chip('A股').click()],
  ['切新品 → 存量产品', () => chip('存量产品').click()],
  ['切新品 → 新品', () => chip('新品').click()],
  ['切新品 → 全部', () => chip('全部', 1).click()],
  ['抽屉搜索 → 沪深', () => box.fill('沪深')],
  ['清空抽屉搜索', () => box.fill('')],
  ['切板块 → 全部', () => chip('全部', 0).click()],
  ['页顶快捷搜索 → 沪深', () => quick.fill('沪深')],
  ['清空快捷搜索', () => quick.fill('')],
]) {
  try { await act() } catch (e) { fail.push(`${label}：点不动 —— ${e.message}`); continue }
  await rank(label)
}

const first = seen[0]
for (const s of seen) {
  if (s.rank !== first.rank || s.total !== first.total) {
    fail.push(`${s.label}：排名从「第 ${first.rank}／${first.total}」变成「第 ${s.rank}／${s.total}」`)
  }
}

for (const s of seen) console.log(`  ${s.label.padEnd(14)} 第 ${s.rank} ／ ${s.total} 只`)
console.log(fail.length ? `\n✗ ${fail.join('\n✗ ')}` : `\n✓ ${seen.length} 次筛选切换，${CODE} 的全市场评论量排名始终是第 ${first.rank}`)

await browser.close()
process.exit(fail.length ? 1 : 0)
