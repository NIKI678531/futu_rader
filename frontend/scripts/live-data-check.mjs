import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const browser = await chromium.launch()
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  let revision = 0
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/meta', async route => {
    const response = await route.fetch()
    const body = await response.json()
    body.data.dataRevision = String(revision)
    body.data.analysisProgress = { text: `Refresh verification ${revision}` }
    await route.fulfill({ response, json: body })
  })
  await page.route('**/api/v1/version', route => route.fulfill({ json: {
    status: 'ok', data: { dataProvider: 'sql', dataRevision: String(revision) },
  } }))
  await page.goto('http://127.0.0.1:5173/product?code=3033&range=mtd', { waitUntil: 'domcontentloaded' })
  /* `analysisProgress.text` 不再是 Shell 底下的常显横幅，而是右上角「处理进度」入口按钮的
     title（ProgressDrawer.jsx）；按属性等它，验证的仍是「版本刷新后 /meta 重新读了一遍」。 */
  const progressTitle = (n) => page.locator(`[data-progress-button][title="Refresh verification ${n}"]`)
  await progressTitle(0).waitFor({ timeout: 90000 })
  await page.getByText('2026-08-01 ～ 2026-08-25', { exact: true }).first().waitFor()
  await page.waitForLoadState('networkidle')
  revision = 1
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await progressTitle(1).waitFor({ timeout: 90000 })
  assert.equal(page.url(), 'http://127.0.0.1:5173/product?code=3033&range=mtd')
  assert.equal(errors.length, 0, errors.join('\n'))
  const pixels = await page.locator('canvas').first().evaluate(canvas => {
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
    let count = 0
    for (let index = 0; index < data.length; index += 4) {
      if (data[index + 3] && Math.abs(data[index] - data[index + 1]) > 25) count++
    }
    return count
  })
  assert.ok(pixels > 100, 'Trend canvas must remain nonblank after refresh')
  console.log('PASS: visible-page version refresh preserves product/month selection and redraws canvas')
} finally {
  await browser.close()
}