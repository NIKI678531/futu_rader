import { s } from '../lib/dc'

/* Shared account-page hero: page identity and the four headline metrics live in
   one compact strip so the actual workspace starts immediately underneath. */
export default function AccountSummaryStrip({ eyebrow, title, kpis }) {
  return (
    <section
      data-page-summary
      style={s('display:grid;grid-template-columns:minmax(330px,30%) repeat(4,minmax(0,1fr));min-height:122px;background:#fff;border:1px solid var(--border-1);border-radius:var(--radius-card);box-shadow:0 1px 2px rgba(14,42,82,0.04);overflow:hidden;margin-bottom:10px')}
    >
      <div style={s('display:flex;flex-direction:column;justify-content:center;min-width:0;padding:18px 22px;border-right:1px solid var(--border-1)')}>
        <div style={s('font:600 12px/1.2 var(--font-cjk);color:var(--csop-blue-600);margin-bottom:10px')}>{eyebrow}</div>
        <div style={s('font:600 26px/1.2 var(--font-cjk);letter-spacing:-0.015em;color:var(--csop-navy-900);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{title}</div>
      </div>
      {kpis.map((k) => (
        <div key={k.label} style={s('display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:0;padding:14px 16px;border-right:1px solid var(--border-1);text-align:center')}>
          <div title={k.label} style={s('max-width:100%;font:500 13px/1.4 var(--font-cjk);color:var(--ink-600);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{k.label}</div>
          <div style={s('display:flex;align-items:baseline;justify-content:center;gap:6px;margin-top:9px;max-width:100%')}>
            <span style={s(`font:600 28px/1 var(--font-mono);letter-spacing:-0.02em;color:${k.vfg || 'var(--ink-900)'};white-space:nowrap`)}>{k.value}</span>
            {k.unit && <span style={s('font:500 14px/1 var(--font-cjk);color:var(--ink-600)')}>{k.unit}</span>}
            {k.pct && <span style={s(`font:600 13px/1 var(--font-mono);color:${k.pfg || 'var(--ink-500)'}`)}>{k.pct}</span>}
          </div>
          <div title={k.sub} style={s('max-width:100%;margin-top:8px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{k.sub}</div>
        </div>
      ))}
    </section>
  )
}
