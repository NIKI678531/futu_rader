import test from 'node:test'
import assert from 'node:assert/strict'

import {
  DEFAULT_PRODUCT_CODE,
  DEFAULT_PRODUCT_RANGE,
  productCodeOrDefault,
  productSelectionFromSearch,
} from '../src/lib/productSelection.js'

test('product monitor defaults to 7709 but preserves valid deep links', () => {
  const master = { 7709: { code: '7709' }, 3033: { code: '3033' } }

  assert.equal(DEFAULT_PRODUCT_CODE, '7709')
  assert.equal(productCodeOrDefault(master, undefined), '7709')
  assert.equal(productCodeOrDefault(master, 'missing'), '7709')
  assert.equal(productCodeOrDefault(master, '3033'), '3033')
})

test('product monitor resolves its deep link before the first render', () => {
  const master = { 7709: { code: '7709' }, 7347: { code: '7347' } }
  const presets = [{ k: 'd1' }, { k: 'd7' }]

  assert.deepEqual(
    productSelectionFromSearch(master, presets, '?code=7347&range=d1'),
    { code: '7347', rangeKey: 'd1' },
  )
  assert.deepEqual(
    productSelectionFromSearch(master, presets, '?code=missing&range=ytd'),
    { code: DEFAULT_PRODUCT_CODE, rangeKey: DEFAULT_PRODUCT_RANGE },
  )
})
