import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('7709 market outlook is limited to its own sentiment and summary', () => {
  const index = source('screens/productMonitor/index.jsx')
  const overview = source('screens/productMonitor/Overview.jsx')

  assert.match(index, /R\.marketOutlook\(code, s\.rangeKey\)/)
  assert.match(index, /marketOutlook\.list\.filter/)
  assert.match(index, /row\.code === code/)
  assert.match(index, /code === '7709'/)
  assert.match(index, /marketOutlookRows:/)
  assert.match(index, /市场风向 · ' \+ code \+ ' 相关市场情绪与讨论/)
  assert.match(index, /产品市场风向与热议/)
  assert.match(overview, /data-market-outlook/)
  assert.match(overview, /v\.marketOutlookTitle/)
  assert.match(overview, /v\.marketOutlookProductScoped \? \['情绪', '总结'\] : \['产品', '情绪', '总结'\]/)
  assert.match(overview, /v\.marketOutlookRows\.map/)

  assert.doesNotMatch(overview, /当前舆情总结|summaryPoints|openSummaryEvidence|查看原文证据/)
  assert.doesNotMatch(overview, /🌐/)
})

test('only discussion heat exposes a click-to-expand KPI annotation', () => {
  const index = source('screens/productMonitor/index.jsx')
  const overview = source('screens/productMonitor/Overview.jsx')
  const kpis = index.slice(index.indexOf('kpis: ['), index.indexOf('].map(function (k)', index.indexOf('kpis: [')))

  assert.equal(kpis.match(/\bnote:/g)?.length, 1)
  assert.match(kpis, /label: '讨论热度'[\s\S]*note: R\.HEAT_FORMULA/)
  assert.match(overview, /\{k\.note && \(/)
  assert.match(overview, /aria-label=\{`查看\$\{k\.label\}说明`\}/)
  assert.match(overview, /aria-expanded=\{noteOpen\}/)
  assert.match(overview, /role="note"/)
})
