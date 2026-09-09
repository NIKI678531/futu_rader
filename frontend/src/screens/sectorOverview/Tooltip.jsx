import { s } from '../../lib/dc'

/* One hover card shared by the table rows, the treemap tiles and the drawer's bucket bar;
   it is fixed to the viewport and never takes pointer events. */
export default function Tooltip({ v }) {
  return (
    <div style={s(`position:fixed;left:${v.tipX}px;top:${v.tipY}px;transform:translate(-50%,-100%);z-index:80;pointer-events:none;background:var(--csop-navy-900);color:#fff;border-radius:6px;padding:10px 12px;box-shadow:0 6px 20px rgba(14,42,82,0.28);min-width:186px`)}>
      <div style={s('font:600 13px/1.4 var(--font-mono);margin-bottom:8px;color:#fff')}>{v.tipTitle}</div>
      {v.tipRows.map((t) => (
        <div key={t.k} style={s('display:flex;align-items:center;justify-content:space-between;gap:18px;padding:2px 0')}>
          <span style={s('font:400 12px/1.5 var(--font-cjk);color:rgba(255,255,255,0.66)')}>{t.k}</span>
          <span style={s(`font:600 13px/1.5 var(--font-mono);color:${t.fg}`)}>{t.v}</span>
        </div>
      ))}
    </div>
  )
}
