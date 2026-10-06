import test from 'node:test'
import assert from 'node:assert/strict'

import { ownManagementView } from '../src/lib/ownManagement.js'

test('partially analysed own products show honest lower bounds and coverage', () => {
  const view = ownManagementView({
    neg: null,
    pos: null,
    negLowerBound: 333,
    posLowerBound: 734,
    dNeg: { short: '暂不可用', dir: 0 },
    dPos: { short: '暂不可用', dir: 0 },
    managementCoverage: {
      negativeProducts: 12,
      positiveProducts: 12,
      totalProducts: 64,
      complete: false,
    },
  })

  assert.equal(view.neg, '≥ 333')
  assert.equal(view.pos, '≥ 734')
  assert.equal(view.negDelta, '待补齐')
  assert.equal(view.posDelta, '待补齐')
  assert.equal(view.partial, true)
  assert.equal(view.coverageLabel, 'AI 12/64')
})

test('fully analysed own products keep exact values and deltas', () => {
  const view = ownManagementView({
    neg: 4,
    pos: 6,
    negLowerBound: 4,
    posLowerBound: 6,
    dNeg: { short: '-20%', dir: -1 },
    dPos: { short: '+50%', dir: 1 },
    managementCoverage: {
      negativeProducts: 64,
      positiveProducts: 64,
      totalProducts: 64,
      complete: true,
    },
  })

  assert.equal(view.neg, '4')
  assert.equal(view.pos, '6')
  assert.equal(view.negDelta, '-20%')
  assert.equal(view.posDelta, '+50%')
  assert.equal(view.partial, false)
  assert.equal(view.coverageLabel, '')
})
