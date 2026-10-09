import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { productKolDisplay } from '../src/lib/productKol.js'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('product KOL content displays the post summary only', () => {
  assert.deepEqual(productKolDisplay({
    latestPostAt: '2026-10-08 09:30',
    representativeSummary: '  AI\n摘要  ',
    representativeExcerpt: '原文摘录',
  }), {
    latestPost: '2026-10-08 09:30',
    postSummary: 'AI 摘要',
  })

  assert.equal(productKolDisplay({ representativeExcerpt: '原文摘录' }).postSummary, '暂不可用')
  assert.equal(productKolDisplay({}).postSummary, '暂不可用')
})

test('comment-only KOL rows receive an explicit latest-post label', () => {
  assert.equal(productKolDisplay({ latestPostAt: null }).latestPost, '仅评论观点')
})

test('product KOL UI preserves API order and uses the new columns', () => {
  const monitor = source('screens/productMonitor/index.jsx')
  const list = source('screens/productMonitor/KolList.jsx')
  const kolBlock = monitor.slice(
    monitor.indexOf('var kolRes ='),
    monitor.indexOf('var cr ='),
  )

  assert.match(kolBlock, /s\.kolMore \? kolAll : kolAll\.slice\(0, 5\)/)
  assert.doesNotMatch(kolBlock, /\.sort\(/)
  assert.match(kolBlock, /productKolDisplay\(k\)/)
  assert.match(list, /按最新相关原帖发布时间倒序/)
  assert.match(list, />最新发帖</)
  assert.match(list, />帖子摘要</)
  assert.doesNotMatch(list, /代表内容摘要／摘录/)
  assert.match(monitor, /相关原帖与评论观点/)
  assert.match(monitor, /相关帖子评论区中已确认的评论观点/)
  assert.doesNotMatch(monitor, /只计该 KOL 在相关帖子评论区/)
})
