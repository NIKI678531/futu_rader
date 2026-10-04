import { s, hover } from '../../lib/dc'

/* 产品话题情绪 — 不计入产品积极／消极态度的市场／宏观类讨论 */
export default function Topics({ v }) {
  return (
    <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px')}>
      <div style={s('display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 20px;border-bottom:1px solid var(--border-1)')}>
        <div style={s('display:flex;align-items:baseline;gap:11px')}>
          <span style={s('font:600 18px/1.3 var(--font-cjk)')}>产品话题情绪</span>
          <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>市场方向、指数涨跌与宏观事件等不计入产品积极／消极态度的讨论在此呈现</span>
          {v.partialData && <span style={s('padding:2px 8px;border-radius:9999px;background:var(--warning-100);font:600 12px/1.6 var(--font-cjk);color:var(--warning-700);white-space:nowrap')}>部分数据</span>}
          {v.topicsStale && <span title={v.staleTitle} style={s('padding:2px 8px;border-radius:9999px;background:var(--warning-100);font:600 12px/1.6 var(--font-cjk);color:var(--warning-700);white-space:nowrap')}>AI 生成 · 待更新</span>}
        </div>
      </div>
      {v.hasTopics && (
        <div style={s('padding:6px 20px 16px')}>
          {v.topics.map((t) => (
            <div key={t.title} onClick={t.go} style={s('display:flex;align-items:center;gap:18px;padding:13px 0;border-bottom:1px solid var(--ink-100);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>
              <div style={s('flex:1;min-width:0')}>
                <div style={s('font:600 14px/1.4 var(--font-cjk);color:var(--ink-900)')}>{t.title}</div>
                <div style={s('margin-top:5px;font:400 13px/1.6 var(--font-cjk);color:var(--ink-600);text-wrap:pretty')}>{t.summary}</div>
              </div>
              <div style={s('flex:none;width:130px;display:flex;gap:1px;height:26px;align-items:flex-end')}>
                {t.spark.map((b, i) => (
                  <div key={i} style={s(`flex:1;height:${b.h}px;background:var(--csop-blue-300);border-radius:1px`)}></div>
                ))}
              </div>
              <div style={s('flex:none;width:190px')}>
                <div style={s('display:flex;height:8px;border-radius:3px;overflow:hidden;background:var(--ink-100);margin-bottom:7px')}>
                  <div style={s(`width:${t.posPct}%;background:var(--positive-600)`)}></div>
                  <div style={s(`width:${t.negPct}%;background:var(--negative-600)`)}></div>
                  <div style={s(`width:${t.neuPct}%;background:var(--ink-300)`)}></div>
                </div>
                <div style={s('display:flex;align-items:center;gap:10px;font:500 12px/1.4 var(--font-mono)')}>
                  <span style={s('color:var(--positive-700)')}>积极 {t.pos}</span>
                  <span style={s('color:var(--negative-700)')}>消极 {t.neg}</span>
                  <span style={s('color:var(--ink-500)')}>中性 {t.neu}</span>
                </div>
              </div>
              <div style={s('flex:none;width:120px;text-align:right')}>
                <div style={s('display:flex;align-items:baseline;justify-content:flex-end;gap:8px')}>
                  <span style={s('font:600 18px/1 var(--font-mono);color:var(--ink-900)')}>{t.mentions}</span>
                  <span style={s(`font:500 13px/1.4 var(--font-mono);color:${t.dfg}`)}>{t.delta}</span>
                </div>
                <div style={s('margin-top:6px;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600)')}>证据 {t.evidence} 条 →</div>
              </div>
            </div>
          ))}
        </div>
      )}
      {v.noTopics && (
        <div style={s('padding:20px;font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容 — 区间内没有识别到该产品的话题讨论。</div>
      )}
      {v.topicsUnavailable && (
        <div style={s('padding:20px')}><div style={s('padding:16px;border:1px dashed var(--warning-600);border-radius:6px;background:var(--warning-100);font:400 14px/1.7 var(--font-cjk);color:var(--warning-700)')}>数据暂不可用 — 话题识别结果尚未生成或数据源未提供，本区域不展示推测内容。</div></div>
      )}
    </div>
  )
}
