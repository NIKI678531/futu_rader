import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('business and page navigation share the compact shell header', () => {
  const shell = source('components/Shell.jsx')

  assert.match(shell, /data-shell-header/)
  assert.match(shell, /aria-label="业务域"/)
  assert.match(shell, /aria-label="页面"/)
  assert.ok(shell.indexOf('aria-label="页面"') < shell.indexOf('{rangeText}'))
  assert.doesNotMatch(shell, />数据范围</)
  assert.doesNotMatch(shell, />最近更新</)
  assert.doesNotMatch(shell, /height:42px/)
  assert.match(shell, /data-page-comment-clock/)
  assert.match(shell, /页面数据截至/)
  assert.match(shell, /data-page-refresh-clock/)
  assert.match(shell, /刷新时间/)
  assert.doesNotMatch(shell, /页面更新/)
  assert.doesNotMatch(shell, /pageContext\?\.pagePublishedAt/)
})

test('business pages refresh their cached data at least once an hour', () => {
  const api = source('lib/api.js')

  assert.match(api, /HOURLY_REFRESH_MS\s*=\s*60\s*\*\s*60\s*\*\s*1000/)
  assert.match(api, /Date\.now\(\)\s*-\s*lastReloadAt\s*>=\s*HOURLY_REFRESH_MS/)
  assert.match(api, /if \(reload && !stopped\) \{[\s\S]*clearCache\(\)[\s\S]*onChange\(\)/)
})

test('business screens share the simplified-Chinese Apple font and radius scale', () => {
  const tokens = source('styles/tokens.css')

  assert.match(tokens, /--font-cjk:-apple-system,BlinkMacSystemFont/)
  assert.match(tokens, /"PingFang SC"/)
  assert.match(tokens, /"Microsoft YaHei UI"/)
  assert.match(tokens, /--font-mono:var\(--font-cjk\)/)
  assert.match(tokens, /--radius-card:12px/)
  assert.match(tokens, /--radius-control:10px/)
  assert.match(tokens, /--radius-small:8px/)
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
