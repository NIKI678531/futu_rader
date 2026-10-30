import { s, hover } from '../lib/dc'
import DcLink from './DcLink'
import R, { prefetchScreen } from '../data/radar'
import { ProgressButton } from './ProgressDrawer'
import { useAdminEnabled } from '../lib/appRoutes'

const COLLECTION_LABEL = {
  fresh: '采集正常',
  stale: '采集可能滞后',
  unavailable: '采集状态暂不可用',
}

/* Kept in the DOM for text-contract and diagnostics checks, but deliberately hidden
   from every business page so it consumes no workspace height. */
function CollectionDisclosure({ data }) {
  const freshness = data?.freshness
  const status = COLLECTION_LABEL[freshness] || COLLECTION_LABEL.unavailable
  const complete = data?.sourceCompleteThrough
    ? ` · 完整至 ${data.sourceCompleteThrough}`
    : ''
  const hasCounts = data?.platformCommentCount != null && data?.parsedCommentCount != null
  const counts = hasCounts
    ? `平台评论量 ${data.platformCommentCount.toLocaleString('en-US')} 条 · 已抓取正文 ${data.parsedCommentCount.toLocaleString('en-US')} 条`
    : '评论覆盖统计暂不可用'
  const coverage = data?.commentCoverage === 'complete'
    ? '当前采集范围内正文覆盖完整。'
    : data?.commentCoverage === 'partial'
      ? '正文为部分覆盖。'
      : '正文覆盖情况未知，可能为部分覆盖。'
  const fg = freshness === 'fresh' ? 'var(--ink-600)' : 'var(--warning-700)'

  return (
    <div
      hidden
      data-collection-disclosure
      data-freshness={freshness || 'unavailable'}
      data-comment-coverage={data?.commentCoverage || 'unknown'}
      role="note"
      style={s('padding:5px 24px;background:var(--canvas-alt);font:400 12px/1.5 var(--font-cjk);color:var(--ink-600)')}
    >
      <span style={{ color: fg, fontWeight: 600 }}>{status}{complete}</span>
      <span> · {counts}。页面筛后评论量按带对应 ETF tag 的帖子一帖一条；AI 仅基于已抓取的评论正文，{coverage}</span>
    </div>
  )
}

/* Compact sticky header shared by all business screens. Domain and page tabs share
   the first row; each screen passes its own filter bar as the second row. */
export default function Shell({ vals, children }) {
  const adminEnabled = useAdminEnabled()
  const { navGroups = [], rangeText, updated } = vals
  const progress = vals.skeleton ? null : R.ANALYSIS_PROGRESS
  const isSql = !vals.skeleton && R.DATA_PROVIDER === 'sql'
  const collection = isSql ? R.DATA_COLLECTION : null
  const activeGroup = navGroups.find((group) => group.subDisplay === 'flex')
  const pageItems = activeGroup?.items || []
  const pageContext = vals.pageContext || null
  const clock = pageContext?.commentClock
  const clockStatus = clock?.status || 'unavailable'
  const clockValue = clockStatus === 'empty'
    ? '区间内无评论'
    : clock?.postedThrough
      ? compactHkt(clock.postedThrough)
      : '暂不可用'
  const coverageTitle = clockStatus === 'partial'
    ? `部分评论缺少实际发帖时间：有时间 ${clock.timestamped} / 已入库 ${clock.stored}`
    : clockStatus === 'unavailable'
      ? '评论实际发帖时间未提供；未使用父帖时间或刷新时间替代'
      : '当前页面数据范围内，已入库评论的最大实际发帖时间'
  const refreshAvailable = Boolean(updated && !String(updated).includes('暂不可用'))
  const refreshValue = refreshAvailable ? `${compactHkt(updated)} HKT` : (updated || '暂不可用')
  const warm = (href) => {
    const target = String(href || '')
    const targetAccount = /official|kol/.test(target)
    const currentAccount = /\/(?:official|kol)(?:\/|$)/.test(window.location.pathname)
    const rangeKey = targetAccount === currentAccount ? (vals.rangeKey || R.DEFAULT_RANGE) : R.DEFAULT_RANGE
    if (/official/.test(target)) prefetchScreen('official', { rangeKey })
    else if (/kol/.test(target)) prefetchScreen('kol', { rangeKey })
    else if (/sector/.test(target)) prefetchScreen('sector', { rangeKey })
  }

  return (
    <div style={s('position:sticky;top:0;z-index:40;box-shadow:0 1px 0 var(--border-2)')}>
      <div data-shell-header style={s('display:flex;align-items:center;height:48px;box-sizing:border-box;padding:0 16px;background:#fff;border-bottom:1px solid var(--border-1)')}>
        <DcLink
          href="sector-overview.dc.html"
          style={s('display:flex;align-items:center;gap:10px;flex:none;color:var(--csop-navy-900);text-decoration:none')}
          className={hover('text-decoration:none')}
        >
          <span style={s('font:600 21px/1.2 var(--font-cjk);letter-spacing:0.01em;white-space:nowrap')}>舆情雷达</span>
        </DcLink>
        <div style={s('width:1px;height:20px;background:var(--border-2);margin:0 12px 0 14px;flex:none')}></div>
        <nav aria-label="业务域" style={s('display:flex;align-items:center;gap:4px;padding:3px;border-radius:var(--radius-control);background:var(--ink-100);flex:none')}>
          {navGroups.map((g) => (
            <DcLink
              key={g.name}
              href={g.href}
              onMouseEnter={() => warm(g.href)}
              style={s(`display:flex;align-items:center;padding:5px 12px;border-radius:var(--radius-small);font:${g.fw} 14px/1.2 var(--font-cjk);color:${g.fg};background:${g.bg};box-shadow:${g.sh};text-decoration:none;white-space:nowrap;transition:all 120ms cubic-bezier(.4,0,.2,1)`)}
              className={hover('color:var(--csop-navy-900);text-decoration:none')}
            >
              {g.name}
            </DcLink>
          ))}
        </nav>
        {pageItems.length > 0 && (
          <>
            <div style={s('width:1px;height:20px;background:var(--border-2);margin:0 10px;flex:none')}></div>
            <nav aria-label="页面" style={s('align-self:stretch;display:flex;align-items:stretch;gap:2px;flex:none')}>
              {pageItems.map((it) => (
                <DcLink
                  key={it.name}
                  href={it.href}
                  onMouseEnter={() => warm(it.href)}
                  style={s(`display:flex;align-items:center;padding:0 10px;font:${it.fw} 14px/1.2 var(--font-cjk);color:${it.fg};box-shadow:inset 0 -2px 0 ${it.bc};text-decoration:none;white-space:nowrap;transition:color 120ms cubic-bezier(.4,0,.2,1)`)}
                  className={hover('color:var(--csop-blue-700);text-decoration:none')}
                >
                  {it.name}
                </DcLink>
              ))}
            </nav>
          </>
        )}
        <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:8px;white-space:nowrap')}>
          <div style={s('flex:none;display:flex;align-items:baseline;gap:5px;white-space:nowrap')}>
            <span style={s('font:600 12px/1.4 var(--font-mono);color:var(--ink-800)')}>{rangeText}</span>
            <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>HKT 自然日</span>
          </div>
          <div style={s('width:1px;height:15px;background:var(--border-2)')}></div>
          <div data-page-refresh-clock style={s('flex:none;display:flex;align-items:baseline;gap:5px;white-space:nowrap')}>
            <span data-screen-diff-new="page-refresh-label" style={s('font:500 11px/1.4 var(--font-cjk);color:var(--ink-500)')}>刷新时间</span>
            <span style={s('font:600 12px/1.4 var(--font-mono);color:var(--ink-800)')}>{refreshValue}</span>
          </div>
          <div data-page-comment-clock data-screen-diff-new="page-comment-clock" title={coverageTitle} style={s(`flex:none;display:flex;align-items:center;gap:5px;height:30px;box-sizing:border-box;padding:0 9px;border:1px solid ${clockStatus === 'partial' ? 'var(--warning-600)' : 'var(--border-2)'};border-radius:var(--radius-control);background:${clockStatus === 'partial' ? 'var(--warning-100)' : '#fff'};white-space:nowrap`)}>
            <span style={s('font:500 11px/1.4 var(--font-cjk);color:var(--ink-500)')}>页面数据截至</span>
            <span style={s('font:600 12px/1.4 var(--font-mono);color:var(--ink-800)')}>{clockValue}</span>
            {clock?.postedThrough && <span style={s('font:400 10px/1.4 var(--font-cjk);color:var(--ink-400)')}>HKT</span>}
          </div>
          {isSql && <ProgressButton />}
          {isSql && adminEnabled && (
            <DcLink
              href="/admin"
              title="查看评论进入与 AI 分析链路"
              style={s('display:inline-flex;align-items:center;height:30px;padding:0 11px;border:1px solid var(--border-2);border-radius:var(--radius-control);background:#fff;font:600 12px/1.2 var(--font-cjk);color:var(--ink-700);text-decoration:none')}
              className={hover('background:var(--canvas-alt);color:var(--csop-blue-700);text-decoration:none')}
            >
              数据管理
            </DcLink>
          )}
        </div>
      </div>
      {children}
      {isSql && <CollectionDisclosure data={collection} />}
      {progress && <div hidden role="status" style={s('padding:5px 24px;background:var(--canvas-alt);font:400 12px/1.4 var(--font-cjk);color:var(--ink-600)')}>{progress.text}</div>}
    </div>
  )
}

function compactHkt(value) {
  const text = String(value || '').replace('T', ' ').replace(/(?:\+08:00| HKT|Z)$/, '')
  return text.length >= 16 ? text.slice(0, 16) : text
}
