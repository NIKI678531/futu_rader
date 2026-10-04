const STEP_FIELDS = [
  ['rawPlatform', '筛选前平台', 'rawPlatformCount'],
  ['platform', '筛后平台', 'platformCount'],
  ['parsed', '已抓正文', 'parsedCount'],
  ['eligible', '规则入围', 'ruleEligibleCount'],
  ['completed', 'AI 完成', 'aiCompletedCount'],
  ['relevant', '相关评论', 'relevantCount'],
]

const knownRatio = (value) => typeof value === 'number' && Number.isFinite(value)
const incompleteRatio = (value) => !knownRatio(value) || value < 1

export function commentFunnelView(funnel) {
  if (funnel == null) {
    return {
      available: false,
      partial: true,
      dataLabel: '数据暂不可用',
      relevantText: '数据暂不可用',
      steps: [],
      sourceCoverageText: '数据暂不可用',
      analysisCoverageText: '数据暂不可用',
    }
  }

  const partial = incompleteRatio(funnel.sourceCoverage)
    || incompleteRatio(funnel.analysisCoverage)
    || funnel.pendingCount > 0
    || funnel.needsContextCount > 0
  const relevant = funnel.relevantCount
  // Older demo fixtures predate the parent-feed filter audit fields.  Keep
  // them renderable without inventing a second number: their only known
  // platform count is both the before and after value.
  const rawPlatformCount = funnel.rawPlatformCount ?? funnel.platformCount
  const percent = (value) => knownRatio(value)
    ? `${(Math.max(0, Math.min(1, value)) * 100).toFixed(1)}%`
    : '数据暂不可用'

  return {
    available: true,
    partial,
    dataLabel: partial ? '部分数据' : '已完成',
    relevantText: relevant == null
      ? '数据暂不可用'
      : `${partial ? '≥ ' : ''}${relevant.toLocaleString('en-US')}`,
    steps: STEP_FIELDS.map(([key, label, field]) => ({
      key,
      label,
      count: field === 'rawPlatformCount' ? rawPlatformCount : funnel[field],
    })),
    qualifyingFeedCount: funnel.qualifyingFeedCount,
    filterExcludedPlatformCount: funnel.filterExcludedPlatformCount
      ?? (knownRatio(rawPlatformCount) && knownRatio(funnel.platformCount)
        ? Math.max(0, rawPlatformCount - funnel.platformCount)
        : undefined),
    sourceCoverageText: percent(funnel.sourceCoverage),
    analysisCoverageText: percent(funnel.analysisCoverage),
  }
}
