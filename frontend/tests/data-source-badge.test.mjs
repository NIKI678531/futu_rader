import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { dataSourceBadge } from '../src/lib/dataSource.js'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('account pages distinguish SQL facts from demo fixtures', () => {
  assert.equal(dataSourceBadge('sql'), '真实数据')
  assert.equal(dataSourceBadge('demo'), '演示数据')

  for (const screen of ['KolActivity.jsx', 'KolDetail.jsx', 'OfficialActivity.jsx']) {
    const code = source(`screens/${screen}`)
    assert.match(code, /dataSourceBadge\(R\.DATA_PROVIDER\)/)
    assert.doesNotMatch(code, />演示数据</)
  }
})
