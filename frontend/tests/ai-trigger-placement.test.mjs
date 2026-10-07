import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')

test('AI analysis actions live only in the database management page', () => {
  const productTable = read('../src/screens/sectorOverview/ProductTable.jsx')
  const overview = read('../src/screens/productMonitor/Overview.jsx')
  const shell = read('../src/components/Shell.jsx')
  const admin = read('../src/screens/Admin.jsx')

  assert.doesNotMatch(productTable, /AiAnalysisButton|分析全市场近 7 天/)
  assert.doesNotMatch(overview, /AiAnalysisButton|分析本产品近 7 天/)
  assert.match(admin, /Layer B · 页面文案/)
  for (const range of ['d1', 'd2', 'd7', 'd14', 'd30', 'mtd']) assert.match(admin, new RegExp(`'${range}'`))
  assert.match(admin, /aria-label="选择 AI 分析时间"/)
  assert.match(admin, /运行所选区间/)
  assert.doesNotMatch(admin, /runAdminFillAll|一键补齐全部/)
  assert.match(admin, /页面数据完整度/)
  assert.match(admin, /链路检查/)
  assert.match(admin, /SOURCE_TEXT/)
  assert.doesNotMatch(admin, /严格 tag 评论量前 10/)
  assert.match(admin, /AI worker 未运行/)
  assert.match(admin, /AI worker 尚未就绪/)
  assert.match(admin, /workerOnline/)
  assert.match(admin, /workerReady/)
  assert.match(admin, /本轮处理进度/)
  assert.match(admin, /pageCommentCount/)
  assert.match(admin, /按页面评论量从大到小排序/)
  assert.match(admin, /comments\.slice\(0, commentDisplayLimit\)/)
  const commentsSection = admin.slice(admin.indexOf('admin-card admin-comments'))
  assert.ok(commentsSection.indexOf('admin-fold-toggle') < commentsSection.indexOf('admin-table-wrap'))
  assert.doesNotMatch(admin, /Airflow|上游采集|Radar 同步|任务中心|查看未就绪原因|按页面补齐/)
  assert.match(shell, /href="\/admin"[\s\S]*数据管理/)
})

test('sector leaderboard shows ten products by default and can reveal the remainder', () => {
  const overview = read('../src/screens/sectorOverview/index.jsx')
  const productTable = read('../src/screens/sectorOverview/ProductTable.jsx')

  assert.match(overview, /s\.listAll \? sorted : sorted\.slice\(0, 10\)/)
  assert.match(overview, /默认显示前 10 只/)
  assert.match(overview, /展开其余/)
  assert.match(overview, /收起，只看前 10 只/)
  assert.match(productTable, /aria-expanded=\{v\.listAll\}/)
})

test('comment refresh and AI runs use the database-only endpoints', () => {
  const client = read('../src/lib/adminApi.js')
  assert.match(client, /\/admin\/pipeline\?range=/)
  assert.match(client, /\/admin\/analysis-runs/)
  assert.doesNotMatch(client, /\/admin\/tasks|Airflow/)
})
