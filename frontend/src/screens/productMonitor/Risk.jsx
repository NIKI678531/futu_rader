import { s, hover } from '../../lib/dc'

/* 重点舆情（需合规关注）— 独立于消极观点排行的风险标签 */
export default function Risk({ v }) {
  return (
    <div data-testid="product-risk-section" style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px;overflow:hidden')}>
      {v.riskHas && (
        <div style={s('height:3px;background:var(--negative-600)')}></div>
      )}
      <div style={s('display:flex;align-items:center;gap:12px;padding:15px 20px;border-bottom:1px solid var(--border-1)')}>
        <span style={s('font:600 18px/1.3 var(--font-cjk)')}>重点舆情</span>
        <span style={s('padding:3px 10px;border-radius:9999px;background:var(--negative-100);font:600 12px/1.6 var(--font-cjk);color:var(--negative-700)')}>需合规关注</span>
        {v.riskHas && (
          <span data-testid="product-risk-total" style={s('font:600 18px/1.3 var(--font-mono);color:var(--negative-700)')}>{v.riskN}</span>
        )}
        <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>监管举报 · 严重指控 · 疑似未经证实指控 · 煽动扩散 · 合规质疑 · 独立于普通消极观点与负面产品问题</span>
        <span style={s('margin-left:auto;padding:3px 10px;border-radius:9999px;background:var(--warning-100);font:600 12px/1.6 var(--font-cjk);color:var(--warning-700);white-space:nowrap')}>AI 识别 · 待人工确认</span>
      </div>
      {v.riskHas && (
        <>
          <div id="product-risk-list" data-testid="product-risk-list" style={s('padding:14px 20px 6px;display:grid;grid-template-columns:1fr 1fr;gap:10px')}>
            {v.riskRows.map((r) => (
              <div key={r.id} data-testid="product-risk-item" onClick={r.go} style={s('border:1px solid var(--border-1);border-left:3px solid var(--negative-600);border-radius:0 6px 6px 0;padding:12px 14px;background:#fff;cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)')} className={hover('background:var(--negative-50)')}>
                <div style={s('display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-bottom:8px')}>
                  {r.tags.map((g) => (
                    <span key={g.label} style={s('padding:1px 8px;border-radius:9999px;background:var(--negative-50);border:1px solid var(--negative-100);font:600 11px/1.7 var(--font-cjk);color:var(--negative-700)')}>{g.label}</span>
                  ))}
                  <span style={s('margin-left:auto;font:500 12px/1.4 var(--font-mono);color:var(--ink-500)')}>{r.time}</span>
                </div>
                <div style={s('font:400 14px/1.75 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>“{r.excerpt}”</div>
                <div style={s('margin-top:7px;font:400 12px/1.6 var(--font-cjk);color:var(--ink-500);text-wrap:pretty')}><span style={s('font-weight:600;color:var(--ink-600)')}>AI 命中依据</span>　{r.rationale}</div>
                <div style={s('margin-top:9px;display:flex;align-items:center;gap:8px;flex-wrap:wrap')}>
                  <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-800)')}>{r.author}</span>
                  <span style={s(`padding:0 7px;border-radius:3px;background:${r.tbg};font:600 11px/1.7 var(--font-cjk);color:${r.tfg}`)}>{r.type}</span>
                  <span style={s('padding:0 7px;border-radius:9999px;background:var(--csop-blue-50);font:600 11px/1.7 var(--font-mono);color:var(--csop-blue-700)')}>{r.code}</span>
                  <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>来源：{r.source}</span>
                  <span style={s('margin-left:auto;padding:1px 8px;border-radius:9999px;background:var(--warning-100);font:600 11px/1.7 var(--font-cjk);color:var(--warning-700)')}>{r.status}</span>
                  <span style={s('font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600)')}>查看原文证据 →</span>
                </div>
              </div>
            ))}
          </div>
          <div style={s('display:flex;align-items:flex-start;gap:14px;padding:8px 20px 16px')}>
            {v.riskMoreVisible && (
              <button type="button" data-testid="product-risk-toggle" aria-expanded={v.riskExpanded} aria-controls="product-risk-list" onClick={v.riskToggle} style={s('flex:none;padding:0;border:0;background:transparent;font:500 13px/1.6 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer;white-space:nowrap')}>{v.riskMoreLabel}</button>
            )}
            <span style={s('font:400 12px/1.6 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>重点舆情是独立的风险标签，不等同于普通消极观点或可归类的负面产品问题；同一条评论可同时属于消极观点与重点舆情，并标明命中原因。系统只识别风险信号并提供原文，不判定言论真伪、是否违法或产品是否违规，不生成风险分数，不触发通知或处置流程。</span>
          </div>
        </>
      )}
      {v.riskEmpty && (
        <div style={s('padding:20px')}><div style={s('padding:16px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容 — 当前区间未识别到需合规关注的言论；普通消极观点与负面问题见上方排行。</div></div>
      )}
      {v.riskUnavailable && (
        <div style={s('padding:20px')}><div style={s('padding:16px;border:1px dashed var(--warning-600);border-radius:6px;background:var(--warning-100);font:400 14px/1.7 var(--font-cjk);color:var(--warning-700)')}>数据暂不可用 — 风险识别结果尚未核验或数据源未提供，本区域不展示推测内容。</div></div>
      )}
      {v.riskNa && (
        <div style={s('display:flex;align-items:center;gap:10px;padding:16px 20px')}>
          <span style={s('flex:none;min-width:30px;padding:3px 9px;border-radius:9999px;background:var(--ink-100);font:600 13px/1.4 var(--font-mono);color:var(--ink-700);text-align:center')}>—</span>
          <span style={s('font:400 14px/1.6 var(--font-cjk);color:var(--ink-500);text-wrap:pretty')}>同业产品不纳入重点舆情（需合规关注）识别范围，本区域不适用；该产品的消极观点与负面问题见上方排行。</span>
        </div>
      )}
    </div>
  )
}
