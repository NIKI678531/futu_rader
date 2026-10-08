import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('business and page navigation share the compact shell header', () => {
  const shell = source('components/Shell.jsx')

  assert.match(shell, /data-shell-header/)
  assert.match(shell, /aria-label="业务域"/)
  assert.match(shell, /aria-label="页面"/)
  assert.ok(shell.indexOf('aria-label="页面"') < shell.indexOf('数据范围'))
  assert.doesNotMatch(shell, /height:42px/)
})

test('collection and pool progress disclosures stay globally hidden', () => {
  const shell = source('components/Shell.jsx')

  assert.match(shell, /hidden\s+data-collection-disclosure/)
  assert.match(shell, /<div hidden role="status"/)
})

test('all shell filter rows include their padding inside the compact height', () => {
  const filters = [
    'screens/sectorOverview/FilterBar.jsx',
    'screens/productMonitor/FilterBar.jsx',
    'screens/KolActivity.jsx',
    'screens/KolDetail.jsx',
    'screens/OfficialActivity.jsx',
  ]

  for (const path of filters) {
    const file = source(path)
    assert.match(file, /data-shell-filter[^\n]*box-sizing:border-box/, path)
  }
})
