export const CUSTOM_PREFIX = 'custom:'
export const CUSTOM_SYNTHESIS_MESSAGE = '自选日期区间暂不提供 AI 生成总结'

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
    const days = validIsoDay(from) && validIsoDay(to)
      ? Math.round((Date.parse(to + 'T00:00:00Z') - Date.parse(from + 'T00:00:00Z')) / 86400000) + 1
      : 0
    if (validIsoDay(from) && validIsoDay(to) && from <= to
      && Number.isInteger(capability.maxDays)
      && days <= capability.maxDays
      && (!capability.earliestDate || from >= capability.earliestDate)
      && (!capability.latestDate || to <= capability.latestDate)) {
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
  const current = rangeFromSearch(window.location.search, presets)
  return hrefWithRange(href, current)
}

export function listenForRangeNavigation(presets, onRange) {
  const handle = () => onRange(rangeFromSearch(window.location.search, presets))
  window.addEventListener('popstate', handle)
  return () => window.removeEventListener('popstate', handle)
}
