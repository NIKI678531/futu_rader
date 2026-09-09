import { s } from '../../lib/dc'

/* 口径与数据状态 — the panel behind the header toggle: the shared status legend on top,
   the page's own methodology blocks underneath. */
export default function Notes({ v }) {
  return (
    <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);padding:20px;margin-bottom:20px')}>
      <div style={s('display:grid;grid-template-columns:repeat(3,1fr);gap:14px 26px;margin-bottom:18px')}>
        {v.statusLegend.map((l) => (
          <div key={l.k} style={s('display:flex;gap:11px;align-items:flex-start')}>
            <span style={s(`flex:none;min-width:30px;padding:3px 9px;border-radius:9999px;background:${l.bg};color:${l.fg};font:600 13px/1.4 var(--font-mono);text-align:center`)}>{l.k}</span>
            <span style={s('font:400 14px/1.65 var(--font-cjk);color:var(--ink-600);text-wrap:pretty')}>{l.v}</span>
          </div>
        ))}
      </div>
      <div style={s('padding-top:16px;border-top:1px solid var(--border-1);display:grid;grid-template-columns:repeat(2,1fr);gap:18px 28px')}>
        {v.notesBlocks.map((b) => (
          <div key={b.title}>
            <div style={s('font:600 14px/1.4 var(--font-cjk);margin-bottom:8px')}>{b.title}</div>
            <div style={s('font:400 14px/1.75 var(--font-cjk);color:var(--ink-600);text-wrap:pretty')}>{b.body}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
