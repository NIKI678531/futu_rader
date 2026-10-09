/* Presentation-only fallbacks for product-related KOL rows. Sorting stays in the API. */
export function productKolDisplay(row) {
  const postSummary = typeof row?.representativeSummary === 'string'
    ? row.representativeSummary.trim().replace(/\s+/g, ' ')
    : ''

  return {
    latestPost: row?.latestPostAt || '仅评论观点',
    postSummary: postSummary || '暂不可用',
  }
}
