import test from 'node:test'
import assert from 'node:assert/strict'
import {
  todayBenchmark,
  todayCandles,
  todayCompliance,
  todayEtfMentions,
  todayKolImpact,
  todayKolMentions,
  todayPool,
  todayRange,
  todayRanks,
  todayStages,
} from '../src/data/todayPlaceholder.js'

const meta = {
  updatedAt: '2026-10-08 09:00 HKT',
  rules: { stage: '测试阶段规则' },
  products: [
    { code: '3033', name: '自家产品', ownership: 'own', sector: 'hk' },
    { code: '2800', name: '同业产品', ownership: 'peer', sector: 'hk' },
  ],
}

test('today range exposes a full 24-hour frontend-only interval', () => {
  const range = todayRange(meta)

  assert.equal(range.key, 'd0')
  assert.equal(range.label, '当天')
  assert.equal(range.from, '2026-10-08')
  assert.equal(range.to, '2026-10-08')
  assert.equal(range.gran, 'hour')
  assert.equal(range.buckets.length, 24)
  assert.deepEqual(range.dates, ['2026-10-08'])
})

test('today market placeholder preserves products while keeping unknown metrics null', () => {
  const pool = todayPool(meta)
  const ranks = todayRanks(meta)
  const benchmark = todayBenchmark(meta, '3033')

  assert.equal(pool.list.length, meta.products.length)
  assert.equal(pool.list[0].buckets.length, 24)
  assert.equal(pool.list[0].comments, null)
  assert.equal(pool.list[0].discussionHeat, null)
  assert.equal(pool.list[0].attitude, null)
  assert.equal(ranks.map['3033'], '—')
  assert.equal(benchmark.base.code, '3033')
  assert.equal(benchmark.buckets.length, 24)
  assert.equal(benchmark.comments.short, '数据暂不可用')
})

test('today content endpoints use empty or unavailable render-safe contracts', () => {
  const impact = todayKolImpact(meta)
  const mentions = todayEtfMentions()
  const kolMentions = todayKolMentions()
  const candles = todayCandles()
  const stages = todayStages(meta, '3033')

  assert.deepEqual(impact.posts, [])
  assert.equal(impact.range.days, 1)
  assert.deepEqual(mentions.list, [])
  assert.equal(kolMentions.status, 'unavailable')
  assert.equal(candles.status, 'unavailable')
  assert.equal(stages.status, 'unavailable')
  assert.deepEqual(stages.series, [])
  assert.equal(todayCompliance(meta, '3033').status, 'unavailable')
  assert.equal(todayCompliance(meta, '2800').status, 'na')
})
