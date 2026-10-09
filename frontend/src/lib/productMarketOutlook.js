/* Product pages display only the outlook for the product currently being viewed. */
export function productMarketOutlookRows(marketOutlook, code) {
  if (!Array.isArray(marketOutlook?.list)) return []
  return marketOutlook.list.filter((row) => row?.code === code)
}

const PRODUCT_ATTITUDE_STYLE = {
  positive: ['var(--positive-100)', 'var(--positive-700)'],
  neutral_positive: ['var(--positive-100)', 'var(--positive-700)'],
  neutral: ['var(--ink-100)', 'var(--ink-700)'],
  neutral_negative: ['var(--negative-100)', 'var(--negative-700)'],
  negative: ['var(--negative-100)', 'var(--negative-700)'],
}

/* Keep product attitude and market direction separate all the way to the view model. */
export function productMarketOutlookView(row) {
  const palette = PRODUCT_ATTITUDE_STYLE[row?.attitude] || ['var(--ink-100)', 'var(--ink-500)']
  const attitudeFallback = row?.attitudeStatus === 'low_sample'
    ? '样本不足'
    : (row?.status === 'empty' ? '暂无结论' : '暂不可用')
  return {
    code: row?.code,
    name: row?.name,
    attitude: row?.attitudeLabel || attitudeFallback,
    attitudeBg: palette[0],
    attitudeFg: palette[1],
    summary: row?.summary || (row?.status === 'empty'
      ? '暂无相关内容 — 区间内没有识别到该产品的市场方向讨论。'
      : '数据暂不可用 — 市场行情摘要尚未生成。'),
  }
}
