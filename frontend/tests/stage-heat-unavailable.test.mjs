import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('pending stage AI keeps the raw heat chart and hides opinion output', () => {
  const index = source('screens/productMonitor/index.jsx')
  const panel = source('screens/productMonitor/Stages.jsx')

  assert.match(index, /heatChartVisible: hsr\.length > 0 && \(stageOk \|\| stageUnavailable\)/)
  assert.match(index, /stageBands: \(stageOk \? SG\.stages : \[\]\)/)
  assert.match(index, /stageRows: \(stageOk \? SG\.stages : \[\]\)/)
  assert.match(index, /stageUnavailable \? 'AI 分析待完成'/)

  assert.match(panel, /v\.heatChartVisible/)
  assert.match(panel, /下方仅展示数据库中的原始热度/)
  assert.match(panel, /\{v\.stageOk && <div/)
  assert.doesNotMatch(panel, /下一批次采集/)
})
