/* 一次性检查：/sector（含抽屉与口径面板）打的每一个接口都是 200。
   .scratch 是 gitignore 的草稿区，没有自己的 package.json，所以按路径 import playwright。
   抽屉与口径面板分两次加载：抽屉开着时它的遮罩会挡住页头的「口径与数据状态」按钮。 */
const { chromium } = await import(
  new URL('../../frontend/node_modules/playwright/index.mjs', import.meta.url).href
)
const b = await chromium.launch()
const seen = []
for (const [label, act] of [
  ['默认', null],
  ['抽屉', async (p) => {
    await p.getByText('3033', { exact: true }).first().click()
    await p.getByText('当前舆情总结', { exact: true }).first().waitFor()
  }],
  ['口径面板', async (p) => {
    await p.getByText('口径与数据状态').first().click()
    await p.getByText('字段应有值，但当前数据源未提供或尚未核验。', { exact: true }).first().waitFor()
  }],
]) {
  const p = await b.newPage({ viewport: { width: 1600, height: 1400 } })
  p.on('response', (r) => { if (r.url().includes('/api/v1/')) seen.push([label, r.status(), r.url().split('/api/v1')[1]]) })
  p.on('pageerror', (e) => console.log('PAGEERROR', String(e)))
  await p.goto('http://localhost:5173/sector', { waitUntil: 'networkidle' })
  if (act) await act(p)
  await p.waitForTimeout(800)
  await p.close()
}
const bad = seen.filter(([, s]) => s !== 200)
console.log(`${seen.length} 次接口调用：`)
for (const [l, s, u] of seen) console.log(' ', l, s, u)
console.log(bad.length ? `✗ ${bad.length} 个非 200` : '✓ 全部 200')
await b.close()
