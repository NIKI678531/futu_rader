import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { narrowOpinionRisk, opinionRiskCounts } from '../src/lib/opinionRisk.js'

const item = (id, riskTags, sourceKind = '评论') => ({
  id,
  sourceKind,
  riskTags,
  riskLabels: riskTags,
})

test('opinion risk keeps only the four requested comment categories', () => {
  const result = narrowOpinionRisk({
    status: 'ok',
    list: [
      item('insult', ['abusive_attack']),
      item('report', ['regulatory_complaint']),
      item('allegation', ['serious_allegation', 'unverified_claim']),
      item('compliance', ['compliance_concern']),
      item('uncertain', ['unverified_claim']),
      item('spread', ['mobilization']),
      item('post', ['serious_allegation'], '帖子'),
      item('movement', []),
    ],
  })

  assert.deepEqual(result.list.map((entry) => entry.id), [
    'insult', 'report', 'allegation', 'compliance',
  ])
  assert.deepEqual(result.list[0].riskLabels, ['辱骂攻击'])
  assert.deepEqual(result.list[2].riskLabels, ['严重指控'])
})

test('opinion risk changes an emptied ok result to empty without collapsing other states', () => {
  assert.deepEqual(
    narrowOpinionRisk({ status: 'ok', list: [item('uncertain', ['unverified_claim'])] }),
    { status: 'empty', list: [] },
  )
  assert.deepEqual(
    narrowOpinionRisk({ status: 'unavailable', list: [] }),
    { status: 'unavailable', list: [] },
  )
})

test('sector opinion counts reuse known zeroes and narrow positive legacy counts', () => {
  const requested = []
  const counts = opinionRiskCounts({
    list: [{ code: 'zero' }, { code: 'risk' }, { code: 'unknown' }],
    complianceCount: { zero: 0, risk: 8, unknown: null },
  }, 'd30', (code) => {
    requested.push(code)
    return { status: 'ok', list: [{ id: 1 }, { id: 2 }] }
  })

  assert.deepEqual(counts, { zero: 0, risk: 2, unknown: null })
  assert.deepEqual(requested, ['risk'])
})

test('the two opinion headings no longer render the compliance-attention tag', () => {
  const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
  assert.doesNotMatch(source('screens/productMonitor/Risk.jsx'), />需合规关注</)
  assert.doesNotMatch(source('screens/sectorOverview/Drawer.jsx'), />需合规关注</)
})
