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

test('sector header uses percentage sentiment and omits the methodology toggle', () => {
  const index = source('screens/sectorOverview/index.jsx')
  const filters = source('screens/sectorOverview/FilterBar.jsx')
  const kpis = source('screens/sectorOverview/Kpis.jsx')

  assert.doesNotMatch(filters, /口径与数据状态|toggleNotes/)
  assert.doesNotMatch(index, /notesOpen|toggleNotes|<Notes/)
  assert.match(kpis, /\{k2\.negPercentage\}/)
  assert.match(kpis, /\{k2\.posPercentage\}/)
  assert.doesNotMatch(kpis, /\{k2\.neg\}|\{k2\.pos\}/)
  assert.match(kpis, /grid-template-columns:minmax\(330px,24fr\) 15fr 17fr 22fr 22fr/)
  assert.match(kpis, /全市场活跃 ETF 舆情分布/)
})

test('sector keeps the leaderboard legend compact and the heatmap legends prominent', () => {
  const table = source('screens/sectorOverview/ProductTable.jsx')
  const heatmap = source('screens/sectorOverview/Heatmap.jsx')

  assert.match(table, /font:400 11px\/1\.4[^']*color:var\(--ink-500\)[^>]*>\{v\.boardHeaderNote\} · 点表头排序/)
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

test('product monitor omits AI-generation and manual-confirmation labels', () => {
  const visibleSurfaces = [
    'lib/view.js',
    'screens/KolActivity.jsx',
    'screens/KolDetail.jsx',
    'screens/OfficialActivity.jsx',
    'screens/productMonitor/Stages.jsx',
    'screens/sectorOverview/Drawer.jsx',
    'screens/sectorOverview/index.jsx',
  ]

  for (const path of visibleSurfaces) {
    assert.doesNotMatch(source(path), /AI 生成 · 可追溯原文/, path)
  }

  const badges = source('lib/view.js')
  assert.match(badges, /if \(state === 'needs_review'\) return 'AI 生成 · 待确认'/)
  assert.match(badges, /return hasEvidence \? null : 'AI 生成'/)
  const stages = source('screens/productMonitor/Stages.jsx')
  const productSurfaces = [
    'screens/productMonitor/Attitude.jsx',
    'screens/productMonitor/EvidenceDrawer.jsx',
    'screens/productMonitor/Overview.jsx',
    'screens/productMonitor/Risk.jsx',
    'screens/productMonitor/Stages.jsx',
    'screens/productMonitor/index.jsx',
  ]
  for (const path of productSurfaces) {
    assert.doesNotMatch(source(path), /AI 生成|待人工确认|AI 识别|AI 阶段观点|AI 态度/, path)
  }
  assert.doesNotMatch(stages, /v\.stageUnavailable && <span/)
})

test('visible application surfaces do not use the legacy freshness wording', () => {
  const visibleSurfaces = [
    'components/ProgressDrawer.jsx',
    'screens/Admin.jsx',
    'screens/productMonitor/Attitude.jsx',
    'screens/productMonitor/Competitors.jsx',
    'screens/productMonitor/Topics.jsx',
    'screens/sectorOverview/Drawer.jsx',
    'screens/sectorOverview/index.jsx',
  ]

  for (const path of visibleSurfaces) {
    assert.doesNotMatch(source(path), /待更新/, path)
  }
  assert.match(source('components/ProgressDrawer.jsx'), /需重算汇总产品/)
  assert.match(source('screens/Admin.jsx'), /需重算/)
})

test('product-related KOL rows omit the main-attitude presentation', () => {
  const list = source('screens/productMonitor/KolList.jsx')
  const monitor = source('screens/productMonitor/index.jsx')

  assert.doesNotMatch(list, /主要态度/)
  assert.doesNotMatch(list, /k\.(?:att|abg|afg)\b/)
  assert.doesNotMatch(list, /有效态度少于/)
  assert.doesNotMatch(monitor, /dominantLabel/)
  assert.match(monitor, /var ATT_STYLE = \{/)
  assert.match(monitor, /ATT_STYLE\[st\.sentiment\]/)
})

test('business pages omit the generic partial-data label without erasing coverage state', () => {
  const visibleSurfaces = [
    'screens/productMonitor/Overview.jsx',
    'screens/productMonitor/Attitude.jsx',
    'screens/productMonitor/Topics.jsx',
    'screens/productMonitor/index.jsx',
    'screens/sectorOverview/Drawer.jsx',
    'screens/sectorOverview/Kpis.jsx',
    'screens/sectorOverview/ProductTable.jsx',
    'screens/sectorOverview/index.jsx',
  ]

  for (const path of visibleSurfaces) {
    assert.doesNotMatch(source(path), /部分数据/, path)
  }

  // The product KPI still renders the relevant-comment lower bound; the
  // removed funnel and generic badge no longer leak their auxiliary state.
  assert.doesNotMatch(source('screens/productMonitor/Overview.jsx'), /commentFunnelDataLabel/)
  assert.match(source('screens/productMonitor/index.jsx'), /value: funnel\.relevantText/)
  assert.doesNotMatch(source('screens/productMonitor/index.jsx'), /commentFunnelSteps|commentFunnelAvailable/)
  assert.match(source('screens/sectorOverview/index.jsx'), /partialData: commentFunnel\.partial/)
})
