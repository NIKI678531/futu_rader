import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  productMarketOutlookRows,
  productMarketOutlookView,
} from '../src/lib/productMarketOutlook.js'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('every product market outlook is limited to that product', () => {
  const rows = productMarketOutlookRows({
    scope: 'market',
    list: [
      { code: '7709', summary: '海力士讨论' },
      { code: '3033', summary: '恒生科技讨论' },
      { code: '2800', summary: '盈富基金讨论' },
    ],
  }, '3033')

  assert.deepEqual(rows, [{ code: '3033', summary: '恒生科技讨论' }])
})

test('market outlook renders purchase attitude even when market direction is opposite', () => {
  const view = productMarketOutlookView({
    code: '7709',
    name: '海力士七倍目标',
    status: 'ok',
    attitude: 'negative',
    attitudeLabel: '消极偏多',
    sentiment: 'bullish',
    sentimentLabel: '看多',
    summary: '市场讨论预期芯片板块反弹。',
  })

  assert.equal(view.attitude, '消极偏多')
  assert.equal(view.attitudeBg, 'var(--negative-100)')
  assert.equal(view.summary, '市场讨论预期芯片板块反弹。')
  assert.equal(Object.hasOwn(view, 'sentiment'), false)
})

test('market outlook supports all five purchase-attitude levels', () => {
  const cases = [
    ['positive', '积极偏多', 'var(--positive-100)'],
    ['neutral_positive', '中性偏积极', 'var(--positive-100)'],
    ['neutral', '中性', 'var(--ink-100)'],
    ['neutral_negative', '中性偏消极', 'var(--negative-100)'],
    ['negative', '消极偏多', 'var(--negative-100)'],
  ]

  for (const [attitude, attitudeLabel, expectedBackground] of cases) {
    const view = productMarketOutlookView({ attitude, attitudeLabel, attitudeStatus: 'ok', status: 'ok', summary: '摘要' })
    assert.equal(view.attitude, attitudeLabel)
    assert.equal(view.attitudeBg, expectedBackground)
  }
})

test('market outlook distinguishes low sample from unavailable purchase attitude', () => {
  assert.equal(productMarketOutlookView({
    attitudeStatus: 'low_sample',
    attitudeCounts: { positive: 0, negative: 0, neutral: 12 },
    status: 'ok',
  }).attitude, '样本不足')
  assert.equal(productMarketOutlookView({ attitudeStatus: 'unavailable', status: 'unavailable' }).attitude, '暂不可用')
})

test('product monitor removes market outlook and moves existing topics below the header', () => {
  const index = source('screens/productMonitor/index.jsx')
  const overview = source('screens/productMonitor/Overview.jsx')
  const radar = source('data/radar.js')
  const productPrefetch = radar.slice(radar.indexOf("if (screen === 'product')"), radar.indexOf("if (screen === 'kol')"))

  assert.doesNotMatch(index, /R\.marketOutlook|productMarketOutlookRows|marketOutlookRows:/)
  assert.doesNotMatch(index, /产品市场观点与总结/)
  assert.ok(index.indexOf('<Topics v={v} />') < index.indexOf('<KolList v={v} />'))
  assert.doesNotMatch(overview, /data-market-outlook|marketOutlook|AI 摘要/)
  assert.doesNotMatch(productPrefetch, /marketOutlook/)
})

test('only discussion heat exposes a click-to-expand KPI annotation', () => {
  const index = source('screens/productMonitor/index.jsx')
  const note = source('screens/productMonitor/KpiNote.jsx')
  const kpis = index.slice(index.indexOf('kpis: ['), index.indexOf('].map(function (k)', index.indexOf('kpis: [')))

  assert.equal(kpis.match(/\bnote:/g)?.length, 1)
  assert.match(kpis, /label: '讨论热度'[\s\S]*note: R\.HEAT_FORMULA/)
  assert.match(note, /\{k\.note && \(/)
  assert.match(note, /aria-label=\{`查看\$\{k\.label\}说明`\}/)
  assert.match(note, /aria-expanded=\{noteOpen\}/)
  assert.match(note, /role="note"/)
})
