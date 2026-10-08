import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { withTodayPlaceholder } from '../src/lib/datePresets.js'

const source = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('today placeholder is prepended without changing backend presets', () => {
  const presets = [
    { k: 'd1', days: 1, label: '昨日' },
    { k: 'd2', days: 2, label: '近 2 天' },
  ]

  const result = withTodayPlaceholder(presets)

  assert.deepEqual(result.map(item => item.label), ['当天', '昨日', '近 2 天'])
  assert.equal(result[0].k, 'd0')
  assert.equal(result[0].days, 1)
  assert.equal(result[0].placeholder, true)
  assert.deepEqual(presets.map(item => item.label), ['昨日', '近 2 天'])
})

test('real today preset replaces the frontend placeholder once available', () => {
  const presets = [
    { k: 'd0', label: '当天', go() {} },
    { k: 'd1', label: '昨日', go() {} },
  ]

  assert.strictEqual(withTodayPlaceholder(presets), presets)
  assert.equal(withTodayPlaceholder(presets)[0].placeholder, undefined)
})

test('all business pages use the shared date preset tabs', () => {
  const screens = [
    'screens/sectorOverview/FilterBar.jsx',
    'screens/productMonitor/FilterBar.jsx',
    'screens/OfficialActivity.jsx',
    'screens/KolActivity.jsx',
    'screens/KolDetail.jsx',
  ]

  for (const path of screens) {
    assert.match(source(path), /<DatePresetTabs presets=\{v\.presets\} \/>/, path)
  }

  const tabs = source('components/DatePresetTabs.jsx')
  assert.match(tabs, /aria-label="日期区间"/)
  assert.match(tabs, /aria-pressed=\{Boolean\(preset\.active\)\}/)
  assert.match(tabs, /onClick=\{preset\.go\}/)
  assert.match(tabs, /data-screen-diff-new=\{preset\.placeholder \|\| preset\.screenDiffNew \? 'today-range-placeholder'/)
  assert.match(source('data/radar.js'), /screenDiffNew: p\.key === 'd0'/)
  assert.doesNotMatch(tabs, /useState|data-date-preset-notice|数据接入中/)
})

test('an advertised d0 still renders full empty shapes before facts arrive', () => {
  const radar = source('data/radar.js')
  assert.match(radar, /function todayReady\(rangeKey, loadRemote, loadUnavailable\)/)
  assert.match(radar, /isTodayRange\(rangeKey\) && value == null \? loadUnavailable\(\) : value/)
  for (const adapter of [
    'todayPool', 'todayRanks', 'todayBenchmark', 'todayKolImpact',
    'todayOfficialPosts', 'todayEtfMentions', 'todayCandles', 'todayStages',
  ]) assert.match(radar, new RegExp(`\\(\\) => ${adapter}\\(`), adapter)
})
