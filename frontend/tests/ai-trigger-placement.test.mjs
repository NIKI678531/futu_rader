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
  for (const range of ['d1', 'd2', 'd7', 'd14', 'd30']) assert.match(admin, new RegExp(`'${range}'`))
  assert.match(admin, /`运行 \$\{item\.key\}`/)
  assert.match(admin, /仅处理所选区间严格 tag 评论量前 10/)
  assert.match(admin, /AI worker 未运行/)
  assert.match(admin, /AI worker 尚未就绪/)
  assert.match(admin, /workerOnline/)
  assert.match(admin, /workerReady/)
  assert.match(admin, /本轮处理进度/)
  assert.match(admin, /pageCommentCount/)
  assert.match(admin, /按页面评论量从大到小排序/)
  assert.match(admin, /comments\.slice\(0, commentDisplayLimit\)/)
  assert.ok(admin.indexOf('admin-fold-toggle') < admin.indexOf('admin-table-wrap'))
  assert.doesNotMatch(admin, /Airflow|上游采集|Radar 同步|任务中心|查看未就绪原因|按页面补齐/)
  assert.match(shell, /href="\/admin"[\s\S]*数据管理/)
})

test('sector leaderboard is capped at ten products without an expand-all path', () => {
  const overview = read('../src/screens/sectorOverview/index.jsx')
  const productTable = read('../src/screens/sectorOverview/ProductTable.jsx')

  assert.match(overview, /sorted\.slice\(0, 10\)/)
  assert.match(overview, /仅显示前 10 只/)
  assert.doesNotMatch(productTable, /完整榜单/)
})

test('comment refresh and AI runs use the database-only endpoints', () => {
  const client = read('../src/lib/adminApi.js')
  assert.match(client, /\/admin\/pipeline\?range=/)
  assert.match(client, /\/admin\/analysis-runs/)
  assert.doesNotMatch(client, /\/admin\/tasks|Airflow/)
})
