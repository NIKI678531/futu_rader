import test from 'node:test'
import assert from 'node:assert/strict'

import { DEFAULT_PRODUCT_CODE, productCodeOrDefault } from '../src/lib/productSelection.js'

test('product monitor defaults to 7709 but preserves valid deep links', () => {
  const master = { 7709: { code: '7709' }, 3033: { code: '3033' } }

  assert.equal(DEFAULT_PRODUCT_CODE, '7709')
  assert.equal(productCodeOrDefault(master, undefined), '7709')
  assert.equal(productCodeOrDefault(master, 'missing'), '7709')
  assert.equal(productCodeOrDefault(master, '3033'), '3033')
})
