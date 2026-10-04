import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('product monitor keeps the relevant lower-bound text in the visible funnel', () => {
  const index = source('screens/productMonitor/index.jsx')
  const overview = source('screens/productMonitor/Overview.jsx')

  assert.match(index, /step\.key === 'relevant' \? funnel\.relevantText/)
  assert.match(index, /var partialData = funnel\.partial;/)
  assert.match(index, /commentFunnelQualifyingFeeds/)
  assert.match(index, /commentFunnelFilterExcluded/)
  assert.match(overview, /合格父帖/)
  assert.match(overview, /父帖筛选剔除平台评论/)
})

test('sector product surfaces show filtered and related comment counts together', () => {
  const index = source('screens/sectorOverview/index.jsx')
  const table = source('screens/sectorOverview/ProductTable.jsx')
  const kpis = source('screens/sectorOverview/Kpis.jsx')
  const drawer = source('screens/sectorOverview/Drawer.jsx')

  assert.match(index, /commentFunnelView/)
  assert.match(index, /relatedComments:/)
  assert.match(table, /r\.relatedComments/)
  assert.match(kpis, /t\.relatedComments/)
  assert.match(drawer, /sel\.relatedComments/)
  assert.match(table, /筛后 \/ 相关/)
})

test('sector attitude and theme surfaces disclose partial comment analysis', () => {
  const index = source('screens/sectorOverview/index.jsx')
  const table = source('screens/sectorOverview/ProductTable.jsx')
  const kpis = source('screens/sectorOverview/Kpis.jsx')
  const drawer = source('screens/sectorOverview/Drawer.jsx')

  assert.match(index, /partialData: ownPartial/)
  assert.match(table, /r\.partialData/)
  assert.match(kpis, /k2\.partialData/)
  assert.match(drawer, /sel\.partialData/)
  assert.match(table, /r\.coverageLabel/)
  assert.match(drawer, /sel\.coverageLabel/)
})
