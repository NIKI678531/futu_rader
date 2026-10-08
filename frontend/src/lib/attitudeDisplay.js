export const ISSUER_OPINION_PREVIEW_LIMIT = 4
export const SECTOR_ATTITUDE_BAR_MIN_COMMENTS = 5

/* The API is already newest-first. Filtering must not reorder that sequence. */
export function issuerOpinionView(rows, expanded) {
  const allRows = Array.isArray(rows) ? rows : []
  const previewRows = allRows
    .filter((row) => row?.attitude === 'negative')
    .slice(0, ISSUER_OPINION_PREVIEW_LIMIT)
  const hiddenCount = allRows.length - previewRows.length

  return {
    rows: expanded ? allRows : previewRows,
    hasRows: allRows.length > 0,
    canToggle: hiddenCount > 0,
    expanded: Boolean(expanded),
    toggleLabel: expanded ? '收起' : `展开其余 ${hiddenCount} 条`,
  }
}

function count(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

export function sectorAttitudeBar(observation) {
  const comments = count(observation?.comments)
  const attitude = observation?.attitude

  if (attitude == null) {
    return {
      kind: 'unavailable',
      positiveWidth: '0.0',
      negativeWidth: '0.0',
    }
  }

  const positive = count(attitude.positive)
  const negative = count(attitude.negative)
  const recognized = positive + negative
  const widths = {
    positiveWidth: recognized ? (positive / recognized * 100).toFixed(1) : '0.0',
    negativeWidth: recognized ? (negative / recognized * 100).toFixed(1) : '0.0',
  }

  /* Five filtered comments control only whether the compact board may draw a bar.
     LOW_SAMPLE=10 still governs whether the product gets an overall conclusion. */
  if (comments >= SECTOR_ATTITUDE_BAR_MIN_COMMENTS && recognized > 0) {
    return { kind: 'colored', ...widths }
  }
  if (comments >= SECTOR_ATTITUDE_BAR_MIN_COMMENTS) {
    return { kind: 'gray', grayLabel: '未识别到积极或消极态度', ...widths }
  }
  return { kind: 'low_sample', ...widths }
}
