import test from 'node:test'
import assert from 'node:assert/strict'

import { PRODUCT_SUMMARY_LIMIT, productSummaryPoints } from '../src/lib/productSummary.js'

test('product sentiment summary prefers detailed structured points and shows at most five', () => {
  const points = Array.from({ length: 6 }, (_, index) => ({ text: `具体产品体验 ${index + 1}` }))
  const result = productSummaryPoints({ text: '不应展示的统计句。', points })

  assert.equal(PRODUCT_SUMMARY_LIMIT, 5)
  assert.deepEqual(result, points.slice(0, 5).map((point) => point.text))
})

test('legacy product summary text is also capped at five points', () => {
  const text = [1, 2, 3, 4, 5, 6]
    .map((index) => `第${index}条说明具体产品体验及用户评价细节。`)
    .join('')

  assert.equal(productSummaryPoints({ text }).length, 5)
})
