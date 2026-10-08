import { useState } from 'react'
import { s, hover } from '../../lib/dc'

/* 顶部 KPI 条 + 市场风向。指标的长口径说明只为讨论热度保留，并收进点击注释。 */
export default function Overview({ v }) {
  const [openNote, setOpenNote] = useState(null)

  return (
    <>
      <div style={s('display:flex;align-items:stretch;background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04);margin-bottom:14px;overflow:visible')}>
        {v.kpis.map((k) => {
          const noteOpen = openNote === k.label
          return (
            <div key={k.label} style={s('position:relative;flex:1;min-width:0;padding:13px 18px;border-right:1px solid var(--border-1)')}>
              <div style={s('display:flex;align-items:center;gap:7px;margin-bottom:8px')}>
                <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{k.label}</span>
                {k.note && (
                  <button
                    type="button"
                    aria-label={`查看${k.label}说明`}
                    aria-expanded={noteOpen}
                    onClick={() => setOpenNote(noteOpen ? null : k.label)}
                    style={s(`width:17px;height:17px;padding:0;border:1px solid ${noteOpen ? 'var(--csop-blue-500)' : 'var(--border-2)'};border-radius:9999px;background:${noteOpen ? 'var(--csop-blue-50)' : '#fff'};font:600 11px/1 var(--font-mono);color:${noteOpen ? 'var(--csop-blue-700)' : 'var(--ink-500)'};cursor:pointer`)}
                    className={hover('background:var(--csop-blue-50);border-color:var(--csop-blue-400)')}
                  >i</button>
                )}
              </div>
              <div style={s('display:flex;align-items:baseline;gap:9px')}>
                <span style={s('font:600 26px/1 var(--font-mono);letter-spacing:-0.01em;color:var(--ink-900)')}>{k.value}</span>
                <span title={k.deltaTitle} style={s(`font:600 13px/1.4 var(--font-mono);color:${k.dfg}`)}>{k.delta}</span>
              </div>
              {noteOpen && (
                <div role="note" style={s('position:absolute;z-index:40;left:18px;top:47px;width:310px;padding:11px 13px;border:1px solid var(--csop-blue-200);border-radius:7px;background:#fff;box-shadow:0 8px 24px rgba(14,42,82,0.16);font:400 12px/1.65 var(--font-cjk);color:var(--ink-700);text-wrap:pretty')}>
                  {k.note}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <section data-market-outlook style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px;overflow:hidden')}>
        <div style={s('display:flex;align-items:center;gap:10px;padding:13px 16px;border-bottom:1px solid var(--border-1)')}>
          <span style={s('font:600 18px/1.3 var(--font-cjk);color:var(--ink-900)')}>{v.marketOutlookTitle}</span>
          <span style={s('font:400 12px/1.4 var(--font-mono);color:var(--ink-400)')}>（{v.marketOutlookRange}）</span>
          <span style={s('padding:2px 8px;border-radius:9999px;background:var(--warning-100);font:500 11px/1.5 var(--font-cjk);color:var(--warning-700)')}>AI 摘要 · 未经人工验证</span>
          <button type="button" onClick={v.refreshMarketOutlook} style={s('margin-left:auto;display:inline-flex;align-items:center;gap:5px;padding:5px 10px;border:1px solid var(--border-2);border-radius:5px;background:#fff;font:500 13px/1.4 var(--font-cjk);color:var(--ink-800);cursor:pointer')} className={hover('background:var(--canvas-alt);border-color:var(--csop-blue-300)')}>
            <span aria-hidden="true">↻</span> 刷新
          </button>
        </div>

        {v.marketOutlookNa && (
          <div style={s('padding:18px;font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>数据暂不可用 — 市场行情摘要尚未生成。</div>
        )}
        {v.marketOutlookEmpty && (
          <div style={s('padding:18px;font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>{v.marketOutlookEmptyText}</div>
        )}
        {!v.marketOutlookNa && !v.marketOutlookEmpty && (
          <table style={s('width:100%;border-collapse:collapse;table-layout:fixed')}>
            <colgroup>
              {!v.marketOutlookProductScoped && <col style={{ width: '25%' }} />}
              <col style={{ width: v.marketOutlookProductScoped ? '20%' : '15%' }} />
              <col style={{ width: v.marketOutlookProductScoped ? '80%' : '60%' }} />
            </colgroup>
            <thead>
              <tr style={s('background:var(--canvas-alt)')}>
                {(v.marketOutlookProductScoped ? ['情绪', '总结'] : ['产品', '情绪', '总结']).map((label) => (
                  <th key={label} scope="col" style={s('padding:12px 16px;border-bottom:1px solid var(--border-1);text-align:left;font:600 13px/1.4 var(--font-cjk);color:var(--ink-900)')}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {v.marketOutlookRows.map((row) => (
                <tr key={row.code} style={s('border-bottom:1px solid var(--ink-100)')}>
                  {!v.marketOutlookProductScoped && (
                    <td style={s('padding:15px 16px;vertical-align:top;font:500 14px/1.55 var(--font-cjk);color:var(--ink-900);overflow-wrap:anywhere')}>
                      {row.name}<span style={s('display:block;margin-top:2px;font:500 12px/1.4 var(--font-mono);color:var(--ink-500)')}>（{row.code}）</span>
                    </td>
                  )}
                  <td style={s('padding:15px 16px;vertical-align:top')}>
                    <span style={s(`display:inline-flex;padding:2px 8px;border:1px solid ${row.sentimentFg};border-radius:4px;background:${row.sentimentBg};font:500 12px/1.5 var(--font-cjk);color:${row.sentimentFg};white-space:nowrap`)}>{row.sentiment}</span>
                  </td>
                  <td style={s('padding:15px 16px;vertical-align:top;font:400 14px/1.75 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{row.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}
