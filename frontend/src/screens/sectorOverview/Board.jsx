import { s, hover } from '../../lib/dc'
import ProductTable from './ProductTable'
import Heatmap from './Heatmap'

/* ETF 产品观测 — the card that holds the whole board: search, 板块 chips, 范围 segments and
   quick filters on top, then the ranked table beside the treemap. */
export default function Board({ v }) {
  return (
    <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:20px')}>
      <div style={s('padding:13px 20px 12px;border-bottom:1px solid var(--border-1)')}>
        <div style={s('display:flex;align-items:center;gap:12px;margin-bottom:11px')}>
          <span style={s('flex:none;font:600 18px/1.3 var(--font-cjk)')}>ETF 产品观测</span>
          <div style={s('position:relative;flex:none')}>
            <input value={v.q} onChange={v.onSearch} placeholder="搜索产品代码或名称" style={s(`width:230px;box-sizing:border-box;padding:7px 28px 7px 31px;border:1px solid ${v.qBc};border-radius:6px;font:400 14px/1.4 var(--font-cjk);color:var(--ink-900);background:#fff`)} />
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#909AAA" strokeWidth="1.8" strokeLinecap="round" style={s('position:absolute;left:9px;top:9px;pointer-events:none')}><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.6-3.6"></path></svg>
            {v.hasQ && (
              <div onClick={v.clearQ} style={s('position:absolute;right:6px;top:6px;width:19px;height:19px;border-radius:4px;display:flex;align-items:center;justify-content:center;font:400 13px/1 var(--font-cjk);color:var(--ink-500);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>✕</div>
            )}
          </div>
          <div style={s('display:flex;align-items:center;gap:5px;flex-wrap:wrap;min-width:0')}>
            {v.chips.map((c) => (
              <div key={c.name} onClick={c.go} style={s(`display:flex;align-items:center;gap:5px;padding:4px 9px;white-space:nowrap;border:1px solid ${c.bc};border-radius:9999px;background:${c.bg};font:${c.fw} 13px/1.4 var(--font-cjk);color:${c.fg};cursor:pointer;transition:all 120ms cubic-bezier(.4,0,.2,1)`)}>
                <span style={s(`width:7px;height:7px;border-radius:2px;background:${c.dot}`)}></span>{c.name}
              </div>
            ))}
          </div>
        </div>
        <div style={s('display:flex;align-items:center;gap:9px;flex-wrap:wrap')}>
          <span style={s('font:600 12px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>范围</span>
          <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
            {v.scopeOptions.map((m) => (
              <div key={m.label} onClick={m.go} style={s(`padding:5px 12px;font:${m.fw} 13px/1.4 var(--font-cjk);color:${m.fg};background:${m.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{m.label}</div>
            ))}
          </div>
          {v.toggles.map((t) => (
            <div key={t.label} onClick={t.go} style={s(`display:flex;align-items:center;gap:6px;padding:5px 11px;border:1px solid ${t.bc};border-radius:9999px;background:${t.bg};font:${t.fw} 13px/1.4 var(--font-cjk);color:${t.fg};cursor:pointer;transition:all 120ms cubic-bezier(.4,0,.2,1)`)}>
              <span style={s(`width:6px;height:6px;border-radius:9999px;background:${t.dot}`)}></span>{t.label}
              {t.hasN && (
                <span style={s('padding:0 6px;border-radius:9999px;background:var(--negative-100);font:600 11px/1.6 var(--font-mono);color:var(--negative-700)')}>{t.n}</span>
              )}
            </div>
          ))}
          <div style={s('margin-left:auto;display:flex;align-items:center;gap:8px')}>
            <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.visibleCount} 只在筛选内</span>
          </div>
        </div>
      </div>

      <div style={s('display:flex;align-items:stretch;border-top:1px solid var(--border-1)')}>
        <ProductTable v={v} />
        <Heatmap v={v} />
      </div>
    </div>
  )
}
