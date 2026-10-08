import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { priceSourceDisclosure, priceSourceLabel } from '../src/lib/priceDisplay.js'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('price source display uses the successful candles payload verbatim', () => {
  assert.equal(priceSourceLabel({ status: 'ok', source: 'FMP' }), 'FMP')
  assert.equal(priceSourceLabel({ status: 'ok', source: 'Yahoo Finance' }), 'Yahoo Finance')
  assert.equal(priceSourceLabel({ status: 'unavailable', source: 'Yahoo Finance' }), '')

  assert.match(
    priceSourceDisclosure({ status: 'ok', source: 'Yahoo Finance' }, 'sql'),
    /行情来源 Yahoo Finance/,
  )
  assert.match(
    priceSourceDisclosure({ status: 'ok', source: 'FMP' }, 'sql'),
    /行情来源 FMP/,
  )
})

test('price source display stays truthful when a successful payload lacks source metadata', () => {
  assert.equal(priceSourceLabel({ status: 'ok' }), '')
  assert.doesNotMatch(priceSourceDisclosure({ status: 'ok' }, 'sql'), /行情来源 FMP/)
  assert.match(priceSourceDisclosure({ status: 'ok' }, 'sql'), /行情来源暂不可用/)
  assert.equal(
    priceSourceDisclosure({ status: 'ok', source: 'Yahoo Finance' }, 'demo'),
    '行情与舆情数值均为演示数据。',
  )
})

test('product trend consumes the API-derived source disclosure without a fixed provider', () => {
  const monitor = source('screens/productMonitor/index.jsx')
  const trend = source('screens/productMonitor/Trend.jsx')

  assert.match(monitor, /priceSourceDisclosure\(px, R\.DATA_PROVIDER\)/)
  assert.match(trend, /\{v\.pxSourceDisclosure\}/)
  assert.doesNotMatch(trend, /行情来源 FMP/)
})

test('all product surfaces call related products by their product-neutral name', () => {
  const competitors = source('screens/productMonitor/Competitors.jsx')
  const sectorDrawer = source('screens/sectorOverview/Drawer.jsx')

  assert.match(competitors, />关联产品观点</)
  assert.doesNotMatch(competitors, /关联竞品观点/)
  assert.match(sectorDrawer, />关联产品观点 · 前 3</)
  assert.doesNotMatch(sectorDrawer, /关联竞品观点/)
})
