const RISK_LABELS = {
  abusive_attack: '辱骂攻击',
  regulatory_complaint: '监管举报',
  serious_allegation: '严重指控',
  compliance_concern: '合规质疑',
}

const ALLOWED_LABELS = new Set(Object.values(RISK_LABELS))

function allowedTags(item) {
  const keys = Array.isArray(item?.riskTags) ? item.riskTags : []
  const labels = Array.isArray(item?.riskLabels) ? item.riskLabels : []
  const tags = []

  keys.forEach((key, index) => {
    if (RISK_LABELS[key]) tags.push({ key, label: RISK_LABELS[key] })
    else if (ALLOWED_LABELS.has(labels[index])) tags.push({ key, label: labels[index] })
  })

  if (!keys.length) {
    labels.forEach((label) => {
      if (ALLOWED_LABELS.has(label)) tags.push({ key: label, label })
    })
  }

  return tags
}

/**
 * Frontend display boundary for the narrowed opinion-risk list.
 *
 * Only comment-level insults, regulatory complaints, serious allegations and
 * compliance concerns remain. Posts, market-movement discussion, uncertain
 * claims, mobilisation and unknown classifications are intentionally omitted.
 */
export function narrowOpinionRisk(result) {
  if (!result || !Array.isArray(result.list)) return result

  const list = result.list.flatMap((item) => {
    if (item?.sourceKind !== '评论' && item?.sourceKind !== 'comment') return []
    const tags = allowedTags(item)
    if (!tags.length) return []
    return [{
      ...item,
      riskTags: tags.map((tag) => tag.key),
      riskLabels: tags.map((tag) => tag.label),
    }]
  })

  return {
    ...result,
    status: result.status === 'ok' && list.length === 0 ? 'empty' : result.status,
    list,
  }
}

/**
 * Reconcile the sector overview's aggregate counts with the narrowed lists.
 * Products with a known zero need no detail request; positive legacy counts
 * are resolved through the existing per-product endpoint in parallel.
 */
export function opinionRiskCounts(pool, rangeKey, riskFor) {
  const counts = {}
  const pending = []

  pool.list.forEach((product) => {
    const legacyCount = pool.complianceCount[product.code]
    if (!(legacyCount > 0)) {
      counts[product.code] = legacyCount
      return
    }

    try {
      const result = riskFor(product.code, rangeKey)
      counts[product.code] = result.status === 'ok' || result.status === 'empty'
        ? result.list.length
        : null
    } catch (error) {
      if (typeof error?.then !== 'function') throw error
      pending.push(error)
    }
  })

  if (pending.length) throw Promise.all(pending)
  return counts
}

