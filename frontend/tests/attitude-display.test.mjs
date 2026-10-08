import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  issuerOpinionView,
  sectorAttitudeBar,
} from '../src/lib/attitudeDisplay.js'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('issuer opinions preview shows the first four negative evaluations in supplied order', () => {
  const rows = [
    { id: 'n1', attitude: 'negative' },
    { id: 'p1', attitude: 'positive' },
    { id: 'n2', attitude: 'negative' },
    { id: 'u1', attitude: 'neutral' },
    { id: 'n3', attitude: 'negative' },
    { id: 'n4', attitude: 'negative' },
    { id: 'n5', attitude: 'negative' },
  ]

  const collapsed = issuerOpinionView(rows, false)
  assert.equal(collapsed.hasRows, true)
  assert.deepEqual(collapsed.rows.map((row) => row.id), ['n1', 'n2', 'n3', 'n4'])
  assert.equal(collapsed.canToggle, true)
  assert.equal(collapsed.expanded, false)
  assert.equal(collapsed.toggleLabel, '展开其余 3 条')

  const expanded = issuerOpinionView(rows, true)
  assert.deepEqual(expanded.rows.map((row) => row.id), rows.map((row) => row.id))
  assert.equal(expanded.expanded, true)
  assert.equal(expanded.toggleLabel, '收起')

  const collapsedAgain = issuerOpinionView(rows, false)
  assert.deepEqual(collapsedAgain.rows.map((row) => row.id), ['n1', 'n2', 'n3', 'n4'])

  assert.equal(issuerOpinionView([], false).hasRows, false)
})

test('sector attitude bar uses a five-filtered-comment display threshold', () => {
  assert.deepEqual(
    sectorAttitudeBar({ comments: 60, attitude: { positive: 3, negative: 5, sampleSufficient: false } }),
    { kind: 'colored', positiveWidth: '37.5', negativeWidth: '62.5' },
  )

  assert.deepEqual(
    sectorAttitudeBar({ comments: 27, attitude: { positive: 1, negative: 3, sampleSufficient: false } }),
    { kind: 'colored', positiveWidth: '25.0', negativeWidth: '75.0' },
  )

  assert.deepEqual(
    sectorAttitudeBar({ comments: 4, attitude: { positive: 1, negative: 3, sampleSufficient: false } }),
    { kind: 'low_sample', positiveWidth: '25.0', negativeWidth: '75.0' },
  )
})

test('sector attitude bar distinguishes an analysed gray bar from unavailable attitude data', () => {
  assert.deepEqual(
    sectorAttitudeBar({ comments: 8, attitude: { positive: 0, negative: 0, neutral: 0, sampleSufficient: false } }),
    { kind: 'gray', grayLabel: '未识别到积极或消极态度', positiveWidth: '0.0', negativeWidth: '0.0' },
  )
  assert.deepEqual(
    sectorAttitudeBar({ comments: 8, attitude: null }),
    { kind: 'unavailable', positiveWidth: '0.0', negativeWidth: '0.0' },
  )
  assert.equal(sectorAttitudeBar({ comments: 4, attitude: { positive: 0, negative: 0, neutral: 0, sampleSufficient: false } }).kind, 'low_sample')
  assert.equal(sectorAttitudeBar({ comments: 4, attitude: null }).kind, 'unavailable')
})

test('issuer disclosure is accessible and the product table omits its stale badge', () => {
  const attitude = source('screens/productMonitor/Attitude.jsx')
  const product = source('screens/productMonitor/index.jsx')
  const sector = source('screens/sectorOverview/index.jsx')
  const table = source('screens/sectorOverview/ProductTable.jsx')

  assert.match(attitude, /<button[^>]+type="button"[^>]+aria-expanded=\{v\.issuerExpanded\}[^>]+aria-controls="issuer-opinion-list"/)
  assert.match(attitude, /id="issuer-opinion-list"/)
  assert.match(product, /riskMore: false, issuerMore: false/)
  assert.match(sector, /sectorAttitudeBar\(o\)/)
  assert.match(table, /r\.grayAttitude/)
  assert.doesNotMatch(table, />待更新<\/span>/)
})

test('issuer opinions are fetched for the current ETF and an empty result hides the module', () => {
  const radar = source('data/radar.js')
  const attitude = source('screens/productMonitor/Attitude.jsx')
  const product = source('screens/productMonitor/index.jsx')

  assert.match(radar, /issuerOpinions\(code, rangeKey\)/)
  assert.match(radar, /product\(code, 'issuer-opinions', rangeKey\)/)
  assert.match(product, /R\.issuerOpinions\(code, s\.rangeKey\)/)
  assert.match(product, /showIssuerOpinions: issuerView\.hasRows/)
  assert.doesNotMatch(attitude, /issuerUnavailable|issuerEmpty/)
})
