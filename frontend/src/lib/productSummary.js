export const PRODUCT_SUMMARY_LIMIT = 5

export function productSummaryPoints(summary) {
  const structured = Array.isArray(summary?.points)
    ? summary.points.map((point) => String(point?.text || '').trim()).filter(Boolean)
    : []
  if (structured.length) return structured.slice(0, PRODUCT_SUMMARY_LIMIT)

  const points = []
  let buffer = ''
  for (const char of Array.from(String(summary?.text || ''))) {
    buffer += char
    if ('。；！'.includes(char)) {
      points.push(buffer.trim())
      buffer = ''
    }
  }
  if (buffer.trim()) points.push(buffer.trim())

  return points.reduce((merged, point) => {
    if (point.length < 14 && merged.length) merged[merged.length - 1] += point
    else merged.push(point)
    return merged
  }, []).slice(0, PRODUCT_SUMMARY_LIMIT)
}
