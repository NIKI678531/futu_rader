import { s, hover } from '../lib/dc'
import DcLink from './DcLink'
import R from '../data/radar'
import { ProgressButton } from './ProgressDrawer'

const COLLECTION_LABEL = {
  fresh: '采集正常',
  stale: '采集可能滞后',
  unavailable: '采集状态暂不可用',
}

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
      data-collection-disclosure
      data-freshness={freshness || 'unavailable'}
      data-comment-coverage={data?.commentCoverage || 'unknown'}
      role="note"
      style={s('padding:5px 24px;background:var(--canvas-alt);font:400 12px/1.5 var(--font-cjk);color:var(--ink-600)')}
    >
      <span style={{ color: fg, fontWeight: 600 }}>{status}{complete}</span>
      <span> · {counts}。页面评论量采用平台总数；AI 仅基于已抓取的评论正文，{coverage}</span>
    </div>
  )
}

/* Rows 1 and 2 of the sticky header — brand, domain tabs, sub-nav, range/updated.
   Byte-identical in all four .dc.html screens, so it lives here once. The third row
   (the filter bar) differs per screen and is passed in as children. */
export default function Shell({ vals, children }) {
  const { navGroups = [], rangeText, updated } = vals
  const progress = vals.skeleton ? null : R.ANALYSIS_PROGRESS
  const isSql = !vals.skeleton && R.DATA_PROVIDER === 'sql'
  const collection = isSql ? R.DATA_COLLECTION : null

  return (
    <div style={s('position:sticky;top:0;z-index:40;box-shadow:0 1px 0 var(--border-2)')}>
      <div style={s('display:flex;align-items:center;height:54px;padding:0 24px;background:#fff;border-bottom:1px solid var(--border-1)')}>
        <DcLink
          href="sector-overview.dc.html"
          style={s('display:flex;align-items:center;gap:10px;flex:none;color:var(--csop-navy-900);text-decoration:none')}
          className={hover('text-decoration:none')}
        >
          <span style={s('font:600 22px/1.2 var(--font-cjk);letter-spacing:0.02em;white-space:nowrap')}>舆情雷达</span>
        </DcLink>
        <div style={s('width:1px;height:20px;background:var(--border-2);margin:0 22px 0 20px;flex:none')}></div>
        <div style={s('display:flex;align-items:center;gap:4px;padding:3px;border-radius:9px;background:var(--ink-100)')}>
          {navGroups.map((g) => (
            <DcLink
              key={g.name}
              href={g.href}
              style={s(`display:flex;align-items:center;padding:6px 16px;border-radius:7px;font:${g.fw} 14px/1.2 var(--font-cjk);color:${g.fg};background:${g.bg};box-shadow:${g.sh};text-decoration:none;white-space:nowrap;transition:all 120ms cubic-bezier(.4,0,.2,1)`)}
              className={hover('color:var(--csop-navy-900);text-decoration:none')}
            >
              {g.name}
            </DcLink>
          ))}
        </div>
        <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:18px;white-space:nowrap')}>
          <div style={s('flex:none;display:flex;align-items:baseline;gap:7px;white-space:nowrap')}>
            <span style={s('font:600 12px/1.4 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400)')}>数据范围</span>
            <span style={s('font:600 14px/1.4 var(--font-mono);color:var(--ink-800)')}>{rangeText}</span>
            <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>HKT 自然日</span>
          </div>
          <div style={s('width:1px;height:15px;background:var(--border-2)')}></div>
          <div style={s('flex:none;display:flex;align-items:baseline;gap:7px;white-space:nowrap')}>
            <span style={s('font:600 12px/1.4 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400)')}>最近更新</span>
            <span style={s('font:600 14px/1.4 var(--font-mono);color:var(--ink-800)')}>{updated}</span>
          </div>
          {isSql && <ProgressButton />}
        </div>
      </div>
      <div style={s('display:flex;align-items:stretch;height:42px;padding:0 24px;background:#fff;border-bottom:1px solid var(--border-1)')}>
        {navGroups.map((g) => (
          <div key={g.name} style={s(`display:${g.subDisplay};align-items:stretch;gap:4px`)}>
            {g.items.map((it) => (
              <DcLink
                key={it.name}
                href={it.href}
                style={s(`display:flex;align-items:center;padding:0 14px;font:${it.fw} 15px/1.2 var(--font-cjk);color:${it.fg};box-shadow:inset 0 -2px 0 ${it.bc};text-decoration:none;white-space:nowrap;transition:color 120ms cubic-bezier(.4,0,.2,1)`)}
                className={hover('color:var(--csop-blue-700);text-decoration:none')}
              >
                {it.name}
              </DcLink>
            ))}
          </div>
        ))}
      </div>
      {children}
      {isSql && <CollectionDisclosure data={collection} />}
      {progress && <div role="status" style={s('padding:5px 24px;background:var(--canvas-alt);font:400 12px/1.4 var(--font-cjk);color:var(--ink-600)')}>{progress.text}</div>}
    </div>
  )
}
