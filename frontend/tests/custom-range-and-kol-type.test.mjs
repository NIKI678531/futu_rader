import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  CUSTOM_ANALYSIS_MESSAGE,
  customRangeValidation,
  inheritCurrentRange,
  listenForRangeNavigation,
  makeCustomRange,
  rangeFromSearch,
  rangeQuery,
} from '../src/lib/customRange.js'
import { kolDisplayTypeChoice } from '../src/lib/kolDisplayType.js'

const capability = [{
  k: 'custom', custom: true,
  earliestDate: '2026-08-03', latestDate: '2026-09-01', maxDays: 30,
}, { k: 'd7' }]

test('custom range deep links round-trip and reject invalid or out-of-window dates', () => {
  const key = rangeFromSearch('?range=custom&from=2026-08-20&to=2026-08-25', capability)
  assert.equal(key, makeCustomRange('2026-08-20', '2026-08-25'))
  assert.deepEqual(rangeQuery(key), { range: 'custom', from: '2026-08-20', to: '2026-08-25' })
  assert.equal(rangeFromSearch('?range=custom&from=2026-08-25&to=2026-08-20', capability), 'd7')
  assert.equal(rangeFromSearch('?range=custom&from=2026-02-30&to=2026-03-01', capability), 'd7')
  assert.equal(rangeFromSearch('?range=custom&from=2026-08-02&to=2026-08-03', capability), 'd7')
  assert.equal(CUSTOM_ANALYSIS_MESSAGE, 'AI 分析按所选日期区间重新聚合')
})

test('a short historical interval remains selectable when its facts are retained', () => {
  assert.deepEqual(
    customRangeValidation('2026-08-03', '2026-08-10', {
      earliestDate: '2026-07-01', latestDate: '2026-10-26', maxDays: 30,
    }),
    { days: 8, error: '' },
  )
  assert.match(
    customRangeValidation('2026-08-03', '2026-09-02', capability[0]).error,
    /31 个自然日/,
  )
})

test('cross-page links retain the validated custom range tuple', () => {
  try {
    global.window = { location: { search: '?range=custom&from=2026-08-20&to=2026-08-25' } }
    assert.equal(
      inheritCurrentRange('sector-overview.dc.html', capability),
      'sector-overview.dc.html?range=custom&from=2026-08-20&to=2026-08-25',
    )
  } finally {
    delete global.window
  }
})

test('browser history replays and cleans up custom date navigation', () => {
  let popstate = null
  let removed = null
  const seen = []
  try {
    global.window = {
      location: { search: '?range=custom&from=2026-08-20&to=2026-08-25' },
      addEventListener(name, handler) { if (name === 'popstate') popstate = handler },
      removeEventListener(name, handler) { if (name === 'popstate') removed = handler },
    }
    const stop = listenForRangeNavigation(capability, value => seen.push(value))
    assert.equal(typeof popstate, 'function')
    popstate()
    assert.deepEqual(seen, [makeCustomRange('2026-08-20', '2026-08-25')])
    stop()
    assert.equal(removed, popstate)
  } finally {
    delete global.window
  }
})

test('custom ranges request every AI-backed endpoint instead of being short-circuited', () => {
  const source = readFileSync(new URL('../src/data/radar.js', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /CUSTOM_SYNTHESIS_MESSAGE|status: 'unsupported'/)
  assert.match(source, /hotSummaryFor[\s\S]*?read\('\/hot-summaries'/)
  assert.match(source, /summaryFor[\s\S]*?read\(product\(code, 'summary', rangeKey\)\)/)
  assert.match(source, /marketOutlook[\s\S]*?read\(marketOutlookProduct\(code, rangeKey\)\)/)
  assert.match(source, /competitorsFor[\s\S]*?read\(product\(code, 'competitors', rangeKey\)\)/)
  assert.match(source, /topicsFor[\s\S]*?read\(product\(code, 'topics', rangeKey\)\)/)
})

test('KOL displayed type prioritises every operation direction and pending state', () => {
  for (const direction of ['add', 'open', 'reduce', 'close', 'hold']) {
    assert.deepEqual(
      kolDisplayTypeChoice({ direction, postType: 'market' }),
      { source: 'direction', key: direction },
    )
  }
  assert.deepEqual(
    kolDisplayTypeChoice({ directionPending: true, postType: 'showcase' }),
    { source: 'direction', key: 'pending' },
  )
  assert.deepEqual(
    kolDisplayTypeChoice({ direction: null, postType: 'edu' }),
    { source: 'content', key: 'edu' },
  )
})

test('KOL ranking uses all roster tags and filter matching is inclusive', () => {
  const source = readFileSync(new URL('../src/screens/KolActivity.jsx', import.meta.url), 'utf8')
  assert.match(source, /l\.rosterTags\.indexOf\(s\.lType\) >= 0/)
  assert.match(source, /l\.rosterTags\.forEach/)
  assert.match(source, />KOL 类型</)
  assert.doesNotMatch(source, />主要类型</)
})
