import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8')

test('post records default to newest-first publishing time order', () => {
  const activity = read('frontend/src/screens/KolActivity.jsx')
  const design = read('design/kol-activity.dc.html')

  assert.match(activity, /rangeKey: rangeFromSearch\(window\.location\.search, R\.PRESETS\), sort: 'time'/)
  assert.match(design, /rangeKey: 'd7', sort: 'time'/)
  for (const source of [activity, design]) assert.match(source, /else list\.sort\(function \(a, b\) \{ return b\.t - a\.t; \}\)/)
})
