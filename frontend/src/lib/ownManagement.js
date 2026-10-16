function count(value) {
  return value == null ? '数据暂不可用' : value.toLocaleString('en-US')
}

function percentage(value, total) {
  if (value == null || total == null) return '数据暂不可用'
  if (total === 0) return '0%'
  return `${(value / total * 100).toFixed(0)}%`
}

/**
 * Presentation-only mapping for the CSOP management headline.
 *
 * Exact totals stay exact. While the AI run is incomplete, the API supplies
 * separately named lower bounds and product coverage so the page can show the
 * work already completed without treating unanalysed products as zero.
 */
export function ownManagementView(own) {
  const coverage = own.managementCoverage == null ? {} : own.managementCoverage
  const total = coverage.totalProducts == null ? own.count : coverage.totalProducts
  const negativeProducts = coverage.negativeProducts
  const positiveProducts = coverage.positiveProducts
  const negExact = own.neg != null
  const posExact = own.pos != null
  const negHasLowerBound = !negExact && negativeProducts > 0 && own.negLowerBound != null
  const posHasLowerBound = !posExact && positiveProducts > 0 && own.posLowerBound != null
  const completed = negativeProducts == null || positiveProducts == null
    ? null
    : Math.min(negativeProducts, positiveProducts)
  const partial = coverage.complete === false && (negHasLowerBound || posHasLowerBound)
  const negValue = negExact ? own.neg : (negHasLowerBound ? own.negLowerBound : null)
  const posValue = posExact ? own.pos : (posHasLowerBound ? own.posLowerBound : null)
  const valueTotal = negValue == null || posValue == null ? null : negValue + posValue
  const negPercentage = percentage(negValue, valueTotal)
  const posPercentage = percentage(posValue, valueTotal)

  return {
    neg: negExact ? count(own.neg) : (negHasLowerBound ? `≥ ${count(own.negLowerBound)}` : count(null)),
    pos: posExact ? count(own.pos) : (posHasLowerBound ? `≥ ${count(own.posLowerBound)}` : count(null)),
    negPercentage,
    posPercentage,
    negDelta: negExact ? own.dNeg.short : (negHasLowerBound ? '待补齐' : '暂不可用'),
    posDelta: posExact ? own.dPos.short : (posHasLowerBound ? '待补齐' : '暂不可用'),
    negDir: negExact ? own.dNeg.dir : 0,
    posDir: posExact ? own.dPos.dir : 0,
    negWidth: valueTotal ? (negValue / valueTotal * 100).toFixed(1) : '0',
    posWidth: valueTotal ? (posValue / valueTotal * 100).toFixed(1) : '0',
    partial,
    coverageLabel: partial ? `AI ${completed}/${total}` : '',
    barTitle: partial
      ? `当前已分析范围：负面 ${negPercentage}，正面 ${posPercentage} · AI 覆盖 ${completed}/${total} 只产品`
      : `负面 ${negPercentage} ： 正面 ${posPercentage}`,
  }
}
