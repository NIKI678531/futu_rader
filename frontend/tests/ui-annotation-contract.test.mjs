import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('sector summary cards omit auxiliary annotations', () => {
  const index = source('screens/sectorOverview/index.jsx')
  const kpis = source('screens/sectorOverview/Kpis.jsx')

  assert.doesNotMatch(index, /仅统计 CSOP 自家/)
  assert.doesNotMatch(index, /正面＝AI 判定积极态度/)
  assert.doesNotMatch(index, /topNote:/)
  assert.doesNotMatch(kpis, /\{k1\.sub\}/)
  assert.doesNotMatch(kpis, /\{k2\.sub\}/)
  assert.doesNotMatch(kpis, /note=\{v\.topNote\}/)
})

test('sector keeps useful legends dark and bold', () => {
  const table = source('screens/sectorOverview/ProductTable.jsx')
  const heatmap = source('screens/sectorOverview/Heatmap.jsx')

  assert.match(table, /font:600 12px\/1\.4[^']*color:var\(--ink-900\)[^>]*>热度＝筛后评论量＋0\.3×点赞＋转发 · 点表头排序/)
  assert.match(heatmap, /font:600 12px\/1\.4[^']*color:var\(--ink-900\)/)
  assert.match(heatmap, /font:700 11px\/1\.4[^']*color:var\(--ink-900\)[^>]*>面积/)
  assert.match(heatmap, /font:700 11px\/1\.4[^']*color:var\(--ink-900\)[^>]*>颜色/)
})

test('kol and official cards omit the requested explanatory copy', () => {
  const kol = source('screens/KolActivity.jsx')
  const official = source('screens/OfficialActivity.jsx')

  assert.doesNotMatch(kol, /「阵营」按帖子挂载/)
  assert.doesNotMatch(kol, /「自家 \/ 竞品」是该 KOL 区间内/)
  assert.doesNotMatch(official, /提及 ETF 按出现次数统计，只含 ETF，不含个股/)
})
