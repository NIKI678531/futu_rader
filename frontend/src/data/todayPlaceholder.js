import { TODAY_RANGE_KEY } from '../lib/datePresets.js'

const UNAVAILABLE_DELTA = Object.freeze({
  text: '数据暂不可用',
  short: '数据暂不可用',
  abs: null,
  pct: null,
  dir: 0,
})

const EMPTY_LIST = Object.freeze([])

export function isTodayRange(rangeKey) {
  return rangeKey === TODAY_RANGE_KEY
}

function dateFromMeta(meta) {
  const match = String(meta.updatedAt || '').match(/^\d{4}-\d{2}-\d{2}/)
  return match ? match[0] : '—'
}

function hourBuckets(date) {
  const shortDate = date.length === 10 ? date.slice(5) : date
  return Array.from({ length: 24 }, (_, hour) => {
    const from = String(hour).padStart(2, '0')
    const to = String((hour + 1) % 24).padStart(2, '0')
    return {
      i: hour,
      span: 1 / 24,
      hour,
      day: date,
      label: hour % 6 === 0 ? from : '',
      tip: `${shortDate} ${from}:00–${to}:00`,
    }
  })
}

function observationBuckets(range) {
  return range.buckets.map((bucket) => ({
    start: null,
    label: bucket.label,
    tip: bucket.tip,
    i: bucket.i,
    mentions: null,
    comments: null,
    interactions: null,
    likes: null,
    shares: null,
    active: null,
    positive: null,
    negative: null,
    neutral: null,
    heatUnknownPosts: null,
  }))
}

function observationFor(product, buckets, updatedAt) {
  return {
    ...product,
    buckets,
    mentions: null,
    comments: null,
    interactions: null,
    likes: null,
    shares: null,
    discussionHeat: null,
    heatUnknownPosts: null,
    activeAccounts: null,
    activeByBucket: buckets.map(() => null),
    attitude: null,
    commentFunnel: null,
    maxBucket: null,
    updatedAt,
    placeholder: true,
  }
}

let memo = null

function model(meta) {
  if (memo && memo.meta === meta) return memo

  const date = dateFromMeta(meta)
  const buckets = hourBuckets(date)
  const range = {
    key: TODAY_RANGE_KEY,
    days: 1,
    from: date,
    to: date,
    label: '当天',
    text: `${date} ～ ${date}`,
    gran: 'hour',
    granLabel: '按小时',
    buckets,
    benchFrom: '—',
    benchTo: '—',
    benchText: '数据暂不可用',
    benchLabel: '较昨日同期',
    trendTitle: '日内舆情与价格趋势',
    dates: [date],
    placeholder: true,
  }
  const values = observationBuckets(range)
  const list = meta.products.map((item) => observationFor(item, values, meta.updatedAt))
  const byCode = Object.fromEntries(list.map((item) => [item.code, item]))
  const nullByCode = Object.fromEntries(list.map((item) => [item.code, null]))
  const rankByCode = Object.fromEntries(list.map((item) => [item.code, '—']))
  const ownCount = list.filter((item) => item.ownership === 'own').length
  const pool = {
    list,
    globalMax: null,
    alerts: { ...nullByCode },
    negMentions: { ...nullByCode },
    baseMentions: { ...nullByCode },
    baseComments: { ...nullByCode },
    baseHeat: { ...nullByCode },
    complianceCount: { ...nullByCode },
    own: {
      count: ownCount,
      heat: null,
      neg: null,
      pos: null,
      risk: null,
      dHeat: UNAVAILABLE_DELTA,
      dNeg: UNAVAILABLE_DELTA,
      dPos: UNAVAILABLE_DELTA,
      managementCoverage: {
        complete: false,
        totalProducts: ownCount,
        negativeProducts: null,
        positiveProducts: null,
      },
    },
    placeholder: true,
  }

  memo = {
    meta,
    range,
    pool,
    ranks: { map: rankByCode, total: list.length, placeholder: true },
    byCode,
    benchmarks: new Map(),
    kolImpact: {
      posts: EMPTY_LIST,
      leaders: EMPTY_LIST,
      kolActive: null,
      range: { from: date, to: date, text: range.text, days: 1 },
      updated: meta.updatedAt,
      status: 'unavailable',
      placeholder: true,
    },
  }
  return memo
}

export function todayRange(meta) {
  return model(meta).range
}

export function todayPool(meta) {
  return model(meta).pool
}

export function todayRanks(meta) {
  return model(meta).ranks
}

export function todayBenchmark(meta, code) {
  const state = model(meta)
  if (state.benchmarks.has(code)) return state.benchmarks.get(code)

  const base = state.byCode[code]
  const result = {
    mentions: UNAVAILABLE_DELTA,
    comments: UNAVAILABLE_DELTA,
    interactions: UNAVAILABLE_DELTA,
    likes: UNAVAILABLE_DELTA,
    shares: UNAVAILABLE_DELTA,
    heat: UNAVAILABLE_DELTA,
    positive: UNAVAILABLE_DELTA,
    negative: UNAVAILABLE_DELTA,
    neutral: UNAVAILABLE_DELTA,
    accounts: UNAVAILABLE_DELTA,
    heatUnknownPosts: null,
    base,
    buckets: state.range.buckets.map(() => ({
      comments: UNAVAILABLE_DELTA,
      active: UNAVAILABLE_DELTA,
      interactions: UNAVAILABLE_DELTA,
      positive: UNAVAILABLE_DELTA,
      negative: UNAVAILABLE_DELTA,
    })),
    placeholder: true,
  }
  state.benchmarks.set(code, result)
  return result
}

export function todayKolImpact(meta) {
  return model(meta).kolImpact
}

export function todayOfficialPosts() {
  return EMPTY_LIST
}

export function todayEtfMentions() {
  return {
    status: 'unavailable',
    list: EMPTY_LIST,
    own: EMPTY_LIST,
    peer: EMPTY_LIST,
    etfCount: 0,
    total: 0,
    postCount: 0,
    placeholder: true,
  }
}

export function todayCompetitors() {
  return { status: 'unavailable', list: EMPTY_LIST, placeholder: true }
}

export function todayCompliance(meta, code) {
  const product = meta.products.find((item) => item.code === code)
  return {
    status: product?.ownership === 'peer' ? 'na' : 'unavailable',
    list: EMPTY_LIST,
    placeholder: true,
  }
}

export function todayKolMentions() {
  return {
    status: 'unavailable',
    scope: '产品相关 KOL',
    list: EMPTY_LIST,
    placeholder: true,
  }
}

export function todayCandles() {
  return {
    status: 'unavailable',
    source: null,
    granularity: '60m',
    granLabel: '60 分钟 K',
    currency: 'HKD',
    list: EMPTY_LIST,
    missing: 24,
    placeholder: true,
  }
}

export function todayStages(meta, code) {
  return {
    code,
    granularity: 'half_day',
    granLabel: '按上午／下午／盘后归纳',
    series: EMPTY_LIST,
    stages: EMPTY_LIST,
    rule: meta.rules.stage,
    status: 'unavailable',
    placeholder: true,
  }
}

export function todayIssuerOpinions() {
  return { status: 'unavailable', list: EMPTY_LIST, placeholder: true }
}
