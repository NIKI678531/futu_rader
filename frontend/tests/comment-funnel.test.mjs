import test from 'node:test'
import assert from 'node:assert/strict'

import { commentFunnelView } from '../src/lib/commentFunnel.js'

const complete = {
  rawPlatformCount: 125,
  platformCount: 100,
  qualifyingFeedCount: 42,
  filterExcludedPlatformCount: 25,
  parsedCount: 100,
  ruleEligibleCount: 80,
  ruleExcludedCount: 20,
  aiCompletedCount: 80,
  relevantCount: 50,
  needsContextCount: 0,
  pendingCount: 0,
  sourceCoverage: 1,
  analysisCoverage: 1,
}

test('renders parent-feed filtering before the existing comment-analysis stages', () => {
  const view = commentFunnelView(complete)

  assert.deepEqual(view.steps, [
    { key: 'rawPlatform', label: '筛选前平台', count: 125 },
    { key: 'platform', label: '筛后平台', count: 100 },
    { key: 'parsed', label: '已抓正文', count: 100 },
    { key: 'eligible', label: '规则入围', count: 80 },
    { key: 'completed', label: 'AI 完成', count: 80 },
    { key: 'relevant', label: '相关评论', count: 50 },
  ])
  assert.equal(view.qualifyingFeedCount, 42)
  assert.equal(view.filterExcludedPlatformCount, 25)
  assert.equal(view.relevantText, '50')
  assert.equal(view.partial, false)
})

test('confirmed relevant count is a lower bound while source, AI, or context is incomplete', () => {
  for (const changed of [
    { sourceCoverage: 0.8 },
    { analysisCoverage: 0.75, pendingCount: 20 },
    { needsContextCount: 3 },
  ]) {
    const view = commentFunnelView({ ...complete, ...changed })
    assert.equal(view.relevantText, '≥ 50')
    assert.equal(view.partial, true)
    assert.equal(view.dataLabel, '部分数据')
  }
})

test('missing funnel stays unavailable instead of inventing zeroes', () => {
  const view = commentFunnelView(null)

  assert.equal(view.available, false)
  assert.equal(view.relevantText, '数据暂不可用')
  assert.deepEqual(view.steps, [])
})

test('legacy demo funnel treats its only platform count as both sides of the filter', () => {
  const { rawPlatformCount, qualifyingFeedCount, filterExcludedPlatformCount, ...legacy } = complete
  const view = commentFunnelView(legacy)

  assert.equal(view.steps[0].count, 100)
  assert.equal(view.filterExcludedPlatformCount, 0)
  assert.equal(view.qualifyingFeedCount, undefined)
})
