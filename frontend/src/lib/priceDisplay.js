export function priceSourceLabel(candles) {
  if (!candles || candles.status !== 'ok' || typeof candles.source !== 'string') return ''
  return candles.source.trim()
}

export function priceSourceDisclosure(candles, dataProvider) {
  if (dataProvider !== 'sql') return '行情与舆情数值均为演示数据。'

  const source = priceSourceLabel(candles)
  if (!source) return '舆情来自原始记录；行情来源暂不可用。缺失行情不补零。'
  return `舆情来自原始记录；行情来源 ${source}，拆股调整、不含股息调整。缺失行情不补零。`
}
