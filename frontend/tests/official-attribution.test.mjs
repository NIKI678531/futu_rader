import test from 'node:test'
import assert from 'node:assert/strict'

import { attributedCampOf, attributedCodesOf } from '../src/lib/officialAttribution.js'

test('official-product filters use attributed products and never the source anchor', () => {
  const post = {
    sourceAnchorCode: '3068',
    attributedProducts: [{ code: '3042' }, { code: '3046' }],
    attributionStatus: 'inferred',
    attributedCamp: 'competitor',
  }

  assert.deepEqual(attributedCodesOf(post), ['3042', '3046'])
  assert.equal(attributedCampOf(post), 'competitor')
})

test('an unattributed official post remains in the timeline but has no product or camp', () => {
  const post = {
    sourceAnchorCode: '3068',
    attributedProducts: [],
    attributionStatus: 'unattributed',
    attributedCamp: 'none',
  }

  assert.deepEqual(attributedCodesOf(post), [])
  assert.equal(attributedCampOf(post), 'none')
})

test('legacy anchor fields are not a fallback for attribution', () => {
  const post = { code: '3068', mentioned: [{ code: '3068' }] }

  assert.deepEqual(attributedCodesOf(post), [])
  assert.equal(attributedCampOf(post), 'none')
})
