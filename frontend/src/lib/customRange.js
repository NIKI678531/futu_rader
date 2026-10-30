export const CUSTOM_PREFIX = 'custom:'
export const CUSTOM_ANALYSIS_MESSAGE = 'AI 分析按所选日期区间重新聚合'

function validIsoDay(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return false
  const parsed = new Date(value + 'T00:00:00Z')
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

export function makeCustomRange(from, to) {
  return `${CUSTOM_PREFIX}${from}:${to}`
}

export function parseCustomRange(value) {
  const match = /^custom:(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$/.exec(String(value || ''))
  return match ? { from: match[1], to: match[2] } : null
}

export function customRangeValidation(from, to, capability = {}) {
  const maxDays = capability.maxDays
  if (!from || !to) return { days: 0, error: '请选择开始日和结束日' }
  if (!validIsoDay(from) || !validIsoDay(to)) return { days: 0, error: '日期格式无效' }
  const days = Math.round((Date.parse(to + 'T00:00:00Z') - Date.parse(from + 'T00:00:00Z')) / 86400000) + 1
  if (from > to) return { days, error: '结束日不能早于开始日' }
  if (Number.isInteger(maxDays) && days > maxDays) {
    return { days, error: `首尾合计 ${days} 个自然日，最多可选 ${maxDays} 天` }
  }
  if (capability.maxYears === 1) {
    const anniversary = nextAnniversary(from)
    if (anniversary && to >= anniversary) {
      return { days, error: `首尾合计 ${days} 个自然日，账号页面最长可选 1 个自然年` }
    }
  }
  if (capability.earliestDate && from < capability.earliestDate) {
    return { days, error: `开始日最早可选 ${capability.earliestDate}` }
  }
  if (capability.latestDate && to > capability.latestDate) {
    return { days, error: `结束日最晚可选 ${capability.latestDate}` }
  }
  return { days, error: '' }
}

function nextAnniversary(value) {
  if (!validIsoDay(value)) return null
  const [year, month, day] = value.split('-').map(Number)
  const target = new Date(Date.UTC(year + 1, month - 1, day))
  // JavaScript rolls 29 February to 1 March, which is the desired exclusive
  // boundary for a full calendar-year interval.
  return target.toISOString().slice(0, 10)
}

export function rangeQuery(rangeKey) {
  const custom = parseCustomRange(rangeKey)
  return custom ? { range: 'custom', from: custom.from, to: custom.to } : { range: rangeKey }
}

export function rangeFromSearch(search, presets, fallback = 'd7') {
  const params = new URLSearchParams(search || '')
  const requested = params.get('range')
  if (requested === 'custom') {
    const from = params.get('from')
    const to = params.get('to')
    const capability = presets.find(item => item.custom || item.k === 'custom')
    if (!capability) return fallback
    const validation = customRangeValidation(from, to, capability)
    if ((Number.isInteger(capability.maxDays) || capability.maxYears === 1) && !validation.error) {
      return makeCustomRange(from, to)
    }
    return fallback
  }
  return presets.some(item => item.k === requested && item.k !== 'custom') ? requested : fallback
}

export function rangeSearch(rangeKey) {
  const params = new URLSearchParams(rangeQuery(rangeKey))
  return params.toString()
}

export function setRangeInUrl(rangeKey, { replace = false } = {}) {
  const params = new URLSearchParams(window.location.search)
  const values = rangeQuery(rangeKey)
  params.set('range', values.range)
  if (values.from) {
    params.set('from', values.from)
    params.set('to', values.to)
  } else {
    params.delete('from')
    params.delete('to')
  }
  const target = window.location.pathname + '?' + params.toString() + window.location.hash
  window.history[replace ? 'replaceState' : 'pushState'](null, '', target)
  rememberRange(scopeForPath(window.location.pathname), rangeKey)
}

export function hrefWithRange(href, rangeKey) {
  const [path, query = ''] = String(href).split('?')
  const params = new URLSearchParams(query)
  const values = rangeQuery(rangeKey)
  Object.entries(values).forEach(([key, value]) => params.set(key, value))
  if (!values.from) {
    params.delete('from')
    params.delete('to')
  }
  return path + '?' + params.toString()
}

export function inheritCurrentRange(href, presets) {
  if (!href || /(?:^|[?&])range=/.test(href)) return href
  const capability = presets.find(item => item.custom || item.k === 'custom')
  const targetScope = capability?.scope || scopeForHref(href)
  const currentScope = scopeForPath(window.location.pathname)
  let current
  if (targetScope !== currentScope) {
    rememberSearch(currentScope, window.location.search)
    current = recalledRange(targetScope, presets)
  }
  if (!current) current = rangeFromSearch(window.location.search, presets)
  return hrefWithRange(href, current)
}

export function listenForRangeNavigation(presets, onRange) {
  const handle = () => onRange(rangeFromSearch(window.location.search, presets))
  window.addEventListener('popstate', handle)
  return () => window.removeEventListener('popstate', handle)
}

function scopeForPath(path) {
  return /(?:\/|^)(?:official|kol)(?:\/|$)|(?:official|kol)-(?:activity|detail)\.dc\.html/.test(path || '')
    ? 'accounts'
    : 'market'
}

function scopeForHref(href) {
  return /(?:official|kol)(?:-|\/|\.|$)/.test(String(href || '')) ? 'accounts' : 'market'
}

function rememberRange(scope, rangeKey) {
  try { window.sessionStorage?.setItem(`radar.range.${scope}`, rangeSearch(rangeKey)) } catch { /* unavailable */ }
}

function rememberSearch(scope, search) {
  try {
    const source = new URLSearchParams(search || '')
    if (!source.get('range')) return
    const stored = new URLSearchParams()
    for (const name of ['range', 'from', 'to']) {
      if (source.has(name)) stored.set(name, source.get(name))
    }
    window.sessionStorage?.setItem(`radar.range.${scope}`, stored.toString())
  } catch { /* unavailable */ }
}

function recalledRange(scope, presets) {
  try {
    const stored = window.sessionStorage?.getItem(`radar.range.${scope}`)
    if (!stored) return null
    return rangeFromSearch('?' + stored, presets, '') || null
  } catch {
    return null
  }
}
