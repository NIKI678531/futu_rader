import { useState } from 'react'
import { s, hover } from '../../lib/dc'
import ProductTable from './ProductTable'
import Heatmap from './Heatmap'

/* Compact ETF workspace toolbar followed immediately by the ranking and heatmap. */
export default function Board({ v }) {
  const [moreOpen, setMoreOpen] = useState(false)
  const primaryMarkets = v.chips.slice(0, 4)
  const moreMarkets = v.chips.slice(4)
  const moreActive = moreMarkets.some((c) => c.fw === 600)

  return (
    <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:var(--radius-card);box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:20px')}>
      <div data-etf-product-toolbar style={s('display:flex;align-items:center;gap:7px;min-height:54px;box-sizing:border-box;padding:8px 12px;border-bottom:1px solid var(--border-1);white-space:nowrap')}>
        <span style={s('flex:none;font:600 18px/1.3 var(--font-cjk);color:var(--csop-navy-900);margin-right:3px')}>ETF 产品观测</span>
        <div style={s('position:relative;flex:none')}>
          <input value={v.q} onChange={v.onSearch} placeholder="搜索代码 / 产品名称" style={s(`width:185px;height:34px;box-sizing:border-box;padding:6px 27px 6px 30px;border:1px solid ${v.qBc};border-radius:var(--radius-control);font:400 12px/1.4 var(--font-cjk);color:var(--ink-900);background:#fff`)} />
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#909AAA" strokeWidth="1.8" strokeLinecap="round" style={s('position:absolute;left:9px;top:10px;pointer-events:none')}><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.6-3.6"></path></svg>
          {v.hasQ && <div onClick={v.clearQ} style={s('position:absolute;right:6px;top:7px;width:19px;height:19px;border-radius:var(--radius-small);display:flex;align-items:center;justify-content:center;font:400 13px/1 var(--font-cjk);color:var(--ink-500);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>✕</div>}
        </div>
        <span style={s('flex:none;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>市场</span>
        <div style={s('display:flex;border:1px solid var(--border-2);border-radius:var(--radius-control);background:#fff')}>
          {primaryMarkets.map((c) => <div key={c.name} onClick={c.go} style={s(`padding:5px 9px;border-right:1px solid var(--border-1);background:${c.bg};font:${c.fw} 12px/1.4 var(--font-cjk);color:${c.fg};cursor:pointer`)}>{c.name}</div>)}
          {moreMarkets.length > 0 && (
            <div style={s('position:relative')} onMouseLeave={() => setMoreOpen(false)}>
              <div onClick={() => setMoreOpen(!moreOpen)} style={s(`padding:5px 9px;background:${moreActive ? 'var(--csop-blue-50)' : '#fff'};font:${moreActive ? 600 : 400} 12px/1.4 var(--font-cjk);color:${moreActive ? 'var(--csop-blue-700)' : 'var(--ink-600)'};cursor:pointer`)}>更多⌄</div>
              {moreOpen && <div style={s('position:absolute;right:0;top:30px;z-index:55;min-width:118px;padding:4px;background:#fff;border:1px solid var(--border-2);border-radius:var(--radius-control);box-shadow:0 8px 24px rgba(14,42,82,0.16)')}>{moreMarkets.map((c) => <div key={c.name} onClick={() => { c.go(); setMoreOpen(false) }} style={s(`padding:6px 9px;border-radius:var(--radius-small);background:${c.bg};font:${c.fw} 12px/1.4 var(--font-cjk);color:${c.fg};cursor:pointer`)}>{c.name}</div>)}</div>}
            </div>
          )}
        </div>
        <span style={s('flex:none;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400);margin-left:2px')}>范围</span>
        <div style={s('display:flex;border:1px solid var(--border-2);border-radius:var(--radius-control);overflow:hidden;background:#fff')}>
          {v.scopeOptions.map((m) => <div key={m.label} onClick={m.go} style={s(`padding:5px 9px;font:${m.fw} 12px/1.4 var(--font-cjk);color:${m.fg};background:${m.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{m.label.replace('仅看', '')}</div>)}
        </div>
        {v.toggles.map((t) => <div key={t.label} onClick={t.go} style={s(`display:flex;align-items:center;gap:4px;padding:5px 8px;border:1px solid ${t.bc};border-radius:var(--radius-control);background:${t.bg};font:${t.fw} 12px/1.4 var(--font-cjk);color:${t.fg};cursor:pointer;transition:all 120ms cubic-bezier(.4,0,.2,1)`)}>{t.label.includes('合规') ? '合规关注' : t.label.replace('仅看', '').replace('仅', '')}{t.hasN && <span style={s('font:600 11px/1.4 var(--font-mono);color:var(--negative-700)')}>{t.n}</span>}</div>)}
        <div style={s('display:flex;border:1px solid var(--border-2);border-radius:var(--radius-control);overflow:hidden;background:#fff;margin-left:2px')}>
          {v.viewOptions.map((m) => <div key={m.label} onClick={m.go} style={s(`padding:5px 8px;border-right:1px solid var(--border-1);background:${m.bg};font:${m.fw} 12px/1.4 var(--font-cjk);color:${m.fg};cursor:pointer`)}>{m.label}</div>)}
        </div>
        <span style={s('margin-left:auto;flex:none;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.visibleCount} 只在筛选内</span>
      </div>
      <div style={s('display:flex;align-items:stretch')}>
        {v.viewMode !== 'heat' && <ProductTable v={v} />}
        {v.viewMode !== 'list' && <Heatmap v={v} full={v.viewMode === 'heat'} />}
      </div>
    </div>
  )
}
