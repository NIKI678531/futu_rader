import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { compareKolReach, kolProductComposition, sumPostViews } from '../src/lib/kolMetrics.js'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('KOL reach sums every post view and propagates an unknown value', () => {
  assert.equal(sumPostViews([{ views: 120 }, { views: 80 }]), 200)
  assert.equal(sumPostViews([{ views: '120' }, { views: 80 }]), 200)
  assert.equal(sumPostViews([{ views: 120 }, { views: null }]), null)
  assert.equal(sumPostViews([{ views: 120 }, { views: 'not-a-number' }]), null)
})

test('KOL reach ranks by total views and places unknown totals last', () => {
  const rows = [
    { kol: 'B', views: null, n: 9, comments: 99 },
    { kol: 'D', views: null, n: 1, comments: 100 },
    { kol: 'C', views: 200, n: 1, comments: 2 },
    { kol: 'A', views: 200, n: 2, comments: 1 },
  ].sort(compareKolReach)

  assert.deepEqual(rows.map((row) => row.kol), ['A', 'C', 'B', 'D'])
})

test('product composition uses one primary ETF per post and groups other companies', () => {
  const result = kolProductComposition([
    { id: '1', ownership: 'own', code: '3033', name: '恒生科技指数ETF', issuer: 'CSOP 南方东英' },
    { id: '2', ownership: 'peer', code: '2800', name: '盈富基金', issuer: '恒生投资' },
    { id: '3', ownership: 'peer', code: '2800', name: '盈富基金', issuer: '恒生投资' },
    { id: '4', ownership: 'peer', code: '3067', name: '恒生科技ETF', issuer: 'BlackRock' },
    { id: '5', ownership: null, code: null, name: null, issuer: null },
  ])

  assert.deepEqual(
    { total: result.total, own: result.own, other: result.other, unknown: result.unknown },
    { total: 5, own: 1, other: 3, unknown: 1 },
  )
  assert.deepEqual(
    { ownPct: result.ownPct, otherPct: result.otherPct, unknownPct: result.unknownPct },
    { ownPct: 20, otherPct: 60, unknownPct: 20 },
  )
  assert.deepEqual(
    result.companies.map((company) => [company.issuer, company.n, company.products.map((product) => [product.code, product.n])]),
    [
      ['恒生投资', 2, [['2800', 2]]],
      ['BlackRock', 1, [['3067', 1]]],
    ],
  )
  assert.equal(result.companies[0].products[0].pct, 40)
})

test('KOL screens expose the new ownership and reach UI without the removed opinions block', () => {
  const activity = source('screens/KolActivity.jsx')
  const detail = source('screens/KolDetail.jsx')
  const radar = source('data/radar.js')

  assert.match(activity, /leaderSort: 'views'/)
  assert.match(activity, /s\.leaderSort === 'views'\) leaders\.sort\(compareKolReach\)/)
  assert.match(activity, />浏览量</)
  assert.match(activity, />ETF 归属</)
  assert.match(activity, /ownership: ownershipOf\(p\)/)
  assert.doesNotMatch(activity, /r\.campBadges\.map/)
  assert.doesNotMatch(activity, />阵营</)

  assert.match(detail, />产品归属构成</)
  assert.match(detail, />ETF 归属</)
  assert.match(detail, /ownership: ownershipOf\(p\)/)
  assert.match(detail, /rankedProfiles\(\)[\s\S]*?\.sort\(compareKolReach\)/)
  assert.match(detail, /conic-gradient/)
  assert.match(detail, />其他公司及产品</)
  assert.doesNotMatch(detail, /帖子类型构成|其他产品观点及操作|R\.kolOpinions/)
  assert.doesNotMatch(radar, /screen === 'kolDetail' && kol/)
})
