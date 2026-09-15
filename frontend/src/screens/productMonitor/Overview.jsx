import { s, hover } from '../../lib/dc'

/* KPI 条 + 当前舆情总结（要点列表 + 口径侧栏） */
export default function Overview({ v }) {
  return (
    <>
      <div style={s('display:flex;align-items:stretch;background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04);margin-bottom:14px;overflow:hidden')}>
        {v.kpis.map((k) => (
          <div key={k.label} style={s('flex:1;padding:13px 18px;border-right:1px solid var(--border-1)')}>
            <div style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500);margin-bottom:8px')}>{k.label}</div>
            <div style={s('display:flex;align-items:baseline;gap:9px')}>
              <span style={s('font:600 26px/1 var(--font-mono);letter-spacing:-0.01em;color:var(--ink-900)')}>{k.value}</span>
              <span title={k.deltaTitle} style={s(`font:600 13px/1.4 var(--font-mono);color:${k.dfg}`)}>{k.delta}</span>
            </div>
            <div style={s('margin-top:7px;font:400 12px/1.5 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>{k.note}</div>
          </div>
        ))}
      </div>

      <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px;overflow:hidden')}>
        <div style={s('display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid var(--border-1)')}>
          <span style={s('font:600 18px/1.3 var(--font-cjk)')}>当前舆情总结</span>
          <span style={s(`padding:2px 9px;border-radius:9999px;background:${v.aiBg};font:600 12px/1.7 var(--font-cjk);color:${v.aiFg}`)}>{v.aiLabel}</span>
          <span style={s('margin-left:auto;font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.summaryCountText}</span>
        </div>
        <div style={s('display:grid;grid-template-columns:minmax(0,1fr) 300px')}>
          <div style={s('padding:18px 24px 20px;display:flex;flex-direction:column;gap:12px')}>
            {v.summaryNa && (
              <div style={s('padding:16px;border:1px dashed var(--warning-600);border-radius:6px;background:var(--warning-100);font:400 14px/1.7 var(--font-cjk);color:var(--warning-700)')}>数据暂不可用 — 舆情总结尚未生成或数据源未提供，本区域不展示推测内容。</div>
            )}
            {v.summaryPoints.map((p) => (
              <div key={p.n} style={s('display:flex;gap:14px;align-items:flex-start')}>
                <span style={s('flex:none;width:24px;height:24px;margin-top:2px;border-radius:9999px;background:var(--csop-blue-50);display:flex;align-items:center;justify-content:center;font:600 12px/1 var(--font-mono);color:var(--csop-blue-700)')}>{p.n}</span>
                <span style={s('font:400 15px/1.8 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{p.text}</span>
              </div>
            ))}
          </div>
          <div style={s('border-left:1px solid var(--border-1);background:var(--canvas);padding:18px 20px;display:flex;flex-direction:column;gap:12px')}>
            <div style={s('display:flex;justify-content:space-between;gap:12px')}><span style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>统计区间</span><span style={s('font:600 13px/1.6 var(--font-mono);color:var(--ink-800);text-align:right')}>{v.rangeText}</span></div>
            <div style={s('display:flex;justify-content:space-between;gap:12px')}><span style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>基准区间</span><span style={s('font:600 13px/1.6 var(--font-mono);color:var(--ink-800);text-align:right')}>{v.benchText}</span></div>
            <div style={s('display:flex;justify-content:space-between;gap:12px')}><span style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>有效样本</span>{v.sampleOk
              ? <span style={s('font:600 13px/1.6 var(--font-mono);color:var(--ink-800);text-align:right')}>{v.sampleN} 条</span>
              : <span style={s('font:600 13px/1.6 var(--font-mono);color:var(--ink-500);text-align:right')}>{v.sampleN}</span>}</div>
            <div style={s('display:flex;justify-content:space-between;gap:12px')}><span style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>更新时间</span><span style={s('font:600 13px/1.6 var(--font-mono);color:var(--ink-800);text-align:right')}>{v.updated}</span></div>
            {/* AI 结论的验证程度（ADR-0019 §4）。挨着「更新时间」，因为它和上面四行是
                同一类东西：这一块结论的出处与成色。 */}
            <div style={s('padding-top:12px;border-top:1px solid var(--border-1);font:400 12px/1.6 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>{v.aiValidationNote}</div>
            {v.hasSummaryEvidence && (
              <div onClick={v.openSummaryEvidence} style={s('margin-top:auto;display:flex;align-items:center;justify-content:center;gap:7px;padding:9px 14px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 14px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>查看原文证据 {v.summaryEvidence} 条 →</div>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
