import { useState } from 'react'
import { s, hover } from '../../lib/dc'

/* Kept here so the compact summary can reuse the existing KPI-note contract. */
export function KpiNote({ k }) {
  const [noteOpen, setNoteOpen] = useState(false)

  return (
    <span style={s('position:relative;display:inline-flex;align-items:center')}>
      {k.note && (
        <button
          type="button"
          aria-label={`查看${k.label}说明`}
          aria-expanded={noteOpen}
          onClick={() => setNoteOpen(!noteOpen)}
          style={s(`width:16px;height:16px;padding:0;border:1px solid ${noteOpen ? 'var(--csop-blue-500)' : 'var(--border-2)'};border-radius:var(--radius-pill);background:${noteOpen ? 'var(--csop-blue-50)' : '#fff'};font:600 10px/1 var(--font-mono);color:${noteOpen ? 'var(--csop-blue-700)' : 'var(--ink-500)'};cursor:pointer`)}
          className={hover('background:var(--csop-blue-50);border-color:var(--csop-blue-400)')}
        >i</button>
      )}
      {noteOpen && (
        <span role="note" style={s('position:absolute;z-index:40;left:24px;top:-10px;width:300px;padding:10px 12px;border:1px solid var(--csop-blue-200);border-radius:var(--radius-small);background:#fff;box-shadow:0 8px 24px rgba(14,42,82,0.16);font:400 12px/1.6 var(--font-cjk);color:var(--ink-700);text-align:left;text-wrap:pretty')}>
          {k.note}
        </span>
      )}
    </span>
  )
}

/* Market outlook follows the compact product summary strip. */
export default function Overview({ v }) {
  return (
    <section data-market-outlook style={s('background:#fff;border:1px solid var(--border-1);border-radius:var(--radius-card);box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px;overflow:hidden')}>
        <div style={s('display:flex;align-items:center;gap:10px;padding:13px 16px;border-bottom:1px solid var(--border-1)')}>
          <span style={s('font:600 18px/1.3 var(--font-cjk);color:var(--ink-900)')}>{v.marketOutlookTitle}</span>
          <span style={s('font:400 12px/1.4 var(--font-mono);color:var(--ink-400)')}>（{v.marketOutlookRange}）</span>
          <span style={s('padding:2px 8px;border-radius:var(--radius-pill);background:var(--warning-100);font:500 11px/1.5 var(--font-cjk);color:var(--warning-700)')}>AI 摘要 · 未经人工验证</span>
          <button type="button" onClick={v.refreshMarketOutlook} style={s('margin-left:auto;display:inline-flex;align-items:center;gap:5px;padding:5px 10px;border:1px solid var(--border-2);border-radius:var(--radius-control);background:#fff;font:500 13px/1.4 var(--font-cjk);color:var(--ink-800);cursor:pointer')} className={hover('background:var(--canvas-alt);border-color:var(--csop-blue-300)')}>
            <span aria-hidden="true">↻</span> 刷新
          </button>
        </div>

        {v.marketOutlookNa && (
          <div style={s('padding:18px;font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>{v.marketOutlookUnavailableText}</div>
        )}
        {v.marketOutlookEmpty && (
          <div style={s('padding:18px;font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>{v.marketOutlookEmptyText}</div>
        )}
        {!v.marketOutlookNa && !v.marketOutlookEmpty && (
          <table style={s('width:100%;border-collapse:collapse;table-layout:fixed')}>
            <colgroup>
              <col style={{ width: '20%' }} />
              <col style={{ width: '80%' }} />
            </colgroup>
            <thead>
              <tr style={s('background:var(--canvas-alt)')}>
                {['市场观点', '市场观点总结'].map((label) => (
                  <th key={label} scope="col" style={s('padding:12px 16px;border-bottom:1px solid var(--border-1);text-align:left;font:600 13px/1.4 var(--font-cjk);color:var(--ink-900)')}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {v.marketOutlookRows.map((row) => (
                <tr key={row.code} style={s('border-bottom:1px solid var(--ink-100)')}>
                  <td style={s('padding:15px 16px;vertical-align:top')}>
                    <span style={s(`display:inline-flex;padding:2px 8px;border:1px solid ${row.attitudeFg};border-radius:var(--radius-small);background:${row.attitudeBg};font:500 12px/1.5 var(--font-cjk);color:${row.attitudeFg};white-space:nowrap`)}>{row.attitude}</span>
                  </td>
                  <td style={s('padding:15px 16px;vertical-align:top;font:400 14px/1.75 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{row.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </section>
  )
}
