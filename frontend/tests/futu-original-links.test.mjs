import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')


test('original-post actions render only when a source URL exists', () => {
  const product = source('screens/productMonitor/index.jsx')
  const drawer = source('screens/productMonitor/EvidenceDrawer.jsx')
  const kol = source('screens/KolActivity.jsx')
  const detail = source('screens/KolDetail.jsx')
  const official = source('screens/OfficialActivity.jsx')

  assert.doesNotMatch(product, /sourceUrl\s*\|\|\s*['"]#['"]/)
  assert.match(drawer, /e\.hasUrl/)
  assert.match(kol, /r\.hasUrl/)
  assert.match(kol, /v\.sel\.hasUrl/)
  assert.match(detail, /p\.hasUrl/)
  assert.match(official, /r\.hasUrl/)
  assert.match(official, /c\.hasUrl/)
})
