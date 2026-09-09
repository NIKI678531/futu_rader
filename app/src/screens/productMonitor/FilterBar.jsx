import { s, hover } from '../../lib/dc'

/* 第三行：预设区间 · 起止日期 · 产品搜索下拉 · 热力粒度 · 基准区间 */
export default function FilterBar({ v }) {
  return (
    <div style={s('display:flex;align-items:center;gap:10px 12px;flex-wrap:wrap;min-height:48px;padding:7px 24px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1)')}>
      <div style={s('flex:none;display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
        {v.presets.map((p) => (
          <div key={p.label} onClick={p.go} style={s(`padding:6px 14px;font:${p.fw} 14px/1.4 var(--font-cjk);color:${p.fg};background:${p.bg};cursor:pointer;border-right:1px solid var(--border-1);transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>{p.label}</div>
        ))}
      </div>
      <div style={s('flex:none;display:flex;align-items:center;gap:8px;padding:6px 12px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 14px/1.4 var(--font-mono);color:var(--ink-700);white-space:nowrap')}>
        <span>{v.rangeFrom}</span><span style={s('color:var(--ink-300)')}>→</span><span>{v.rangeTo}</span>
      </div>
      <div style={s('position:relative;flex:none')}>
        <input value={v.pq} onChange={v.onPq} placeholder="搜索产品代码或名称" style={s(`width:222px;box-sizing:border-box;padding:6px 26px 6px 30px;border:1px solid ${v.pqBc};border-radius:6px;font:400 14px/1.4 var(--font-cjk);color:var(--ink-900);background:#fff`)} />
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#909AAA" strokeWidth="1.8" strokeLinecap="round" style={s('position:absolute;left:9px;top:8px;pointer-events:none')}><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.6-3.6"></path></svg>
        {v.pqHas && (
          <div onClick={v.pqClear} style={s('position:absolute;right:5px;top:5px;width:19px;height:19px;border-radius:4px;display:flex;align-items:center;justify-content:center;font:400 13px/1 var(--font-cjk);color:var(--ink-500);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>✕</div>
        )}
        {v.pqOpen && (
          <div style={s('position:absolute;left:0;top:37px;width:352px;max-height:284px;overflow-y:auto;background:#fff;border:1px solid var(--border-2);border-radius:6px;box-shadow:0 8px 24px rgba(14,42,82,0.16);z-index:50')}>
            {v.pqList.map((p) => (
              <div key={p.code} onMouseDown={p.go} style={s(`display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${p.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                <span style={s('flex:none;width:44px;font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{p.code}</span>
                <span style={s('flex:1;min-width:0;font:400 13px/1.4 var(--font-cjk);color:var(--ink-700);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{p.name}</span>
                <span style={s('flex:none;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>{p.sector}</span>
                <span style={s('flex:none;width:42px;text-align:right;font:600 13px/1.4 var(--font-mono);color:var(--ink-700)')}>{p.mentions}</span>
              </div>
            ))}
            {v.pqEmpty && (
              <div style={s('padding:13px;font:400 13px/1.5 var(--font-cjk);color:var(--ink-500)')}>暂无匹配产品</div>
            )}
          </div>
        )}
      </div>
      <div style={s('flex:none;padding:5px 11px;border-radius:9999px;background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);white-space:nowrap')}>热力粒度 {v.granLabel}</div>
      <div style={s('flex:none;margin-left:auto;padding:5px 11px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 13px/1.4 var(--font-cjk);color:var(--ink-600);white-space:nowrap')}>{v.benchLabel} · 基准 {v.benchText}</div>
    </div>
  )
}
