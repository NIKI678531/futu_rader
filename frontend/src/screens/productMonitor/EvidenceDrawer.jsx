import { s, hover } from '../../lib/dc'

/* 原文证据侧栏。抽屉本体始终挂载，靠 transform 平移进出（与设计源一致），
   内容只在 panelOpen 时渲染；遮罩层单独条件渲染。 */
export default function EvidenceDrawer({ v }) {
  return (
    <>
      {v.panelOpen && (
        <div onClick={v.closePanel} style={s('position:fixed;inset:0;background:rgba(14,42,82,0.55);z-index:60')}></div>
      )}

      <div style={s(`position:fixed;top:0;right:0;width:576px;height:100vh;background:#fff;border-left:1px solid var(--border-2);box-shadow:-8px 0 28px rgba(14,42,82,0.14);z-index:61;transform:translateX(${v.panelX});transition:transform 320ms cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;overflow:hidden`)}>
        {v.panelOpen && (
          <div style={s('display:flex;flex-direction:column;height:100vh')}>
            <div style={s('flex:none;padding:17px 22px 15px;background:var(--canvas);border-bottom:1px solid var(--border-1)')}>
              <div style={s('display:flex;align-items:flex-start;justify-content:space-between;gap:14px')}>
                <div style={s('min-width:0')}>
                  <div style={s('font:600 12px/1.2 var(--font-cjk);letter-spacing:0.16em;color:var(--ink-400);margin-bottom:8px')}>原文证据</div>
                  <div style={s('font:600 16px/1.4 var(--font-cjk);text-wrap:pretty')}>{v.panelTitle}</div>
                  <div style={s('margin-top:8px;display:flex;flex-wrap:wrap;gap:6px')}>
                    <span style={s('padding:2px 9px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:600 12px/1.7 var(--font-mono);color:var(--ink-700)')}>{v.code}</span>
                    <span style={s('padding:2px 9px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 12px/1.7 var(--font-mono);color:var(--ink-600)')}>{v.rangeText}</span>
                    <span style={s('padding:2px 9px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 12px/1.7 var(--font-cjk);color:var(--ink-600)')}>{v.panelFilter}</span>
                    {v.panelCountOk
                      ? <span style={s('padding:2px 9px;border-radius:9999px;background:var(--csop-blue-50);font:600 12px/1.7 var(--font-cjk);color:var(--csop-blue-700)')}>{v.panelCount} 条结果</span>
                      : <span style={s('padding:2px 9px;border-radius:9999px;background:var(--ink-100);font:600 12px/1.7 var(--font-cjk);color:var(--ink-500)')}>{v.panelCount}</span>}
                  </div>
                </div>
                <div onClick={v.closePanel} style={s('flex:none;width:28px;height:28px;border-radius:6px;border:1px solid var(--border-2);background:#fff;display:flex;align-items:center;justify-content:center;font:400 15px/1 var(--font-cjk);color:var(--ink-500);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>✕</div>
              </div>
            </div>

            <div style={s('flex:1;min-height:0;overflow-y:auto;padding:16px 22px 24px')}>
              {v.panelHasExtra && (
                <div style={s('border:1px solid var(--border-1);border-radius:6px;background:var(--canvas);padding:13px 14px;margin-bottom:14px')}>
                  {v.panelExtra.map((e) => (
                    <div key={e.k} style={s('display:flex;gap:12px;padding:4px 0')}>
                      <span style={s('flex:none;width:76px;font:500 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>{e.k}</span>
                      <span style={s('flex:1;font:400 13px/1.6 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{e.v}</span>
                    </div>
                  ))}
                </div>
              )}

              {v.evidence.map((e) => (
                <div key={e.id} onClick={e.go} style={s(`border:1px solid ${e.bc};border-radius:6px;padding:13px 14px;margin-bottom:10px;background:${e.bg};cursor:pointer`)} className={hover('border-color:var(--csop-blue-400)')}>
                  <div style={s('display:flex;align-items:center;gap:9px;margin-bottom:8px;flex-wrap:wrap')}>
                    <span style={s('font:500 13px/1.4 var(--font-mono);color:var(--ink-600)')}>{e.time}</span>
                    <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-800)')}>{e.author}</span>
                    <span style={s(`padding:1px 7px;border-radius:3px;background:${e.tbg};font:600 11px/1.7 var(--font-cjk);color:${e.tfg}`)}>{e.type}</span>
                    {e.hasAtt && (
                      <span style={s(`padding:1px 7px;border-radius:3px;background:${e.abg};font:600 11px/1.7 var(--font-cjk);color:${e.afg}`)}>态度 · {e.att}</span>
                    )}
                  </div>
                  {e.hasRisk && (
                    <div style={s('display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-bottom:8px')}>
                      {e.riskTags.map((g) => (
                        <span key={g.label} style={s('padding:1px 8px;border-radius:9999px;background:var(--negative-50);border:1px solid var(--negative-100);font:600 11px/1.7 var(--font-cjk);color:var(--negative-700)')}>{g.label}</span>
                      ))}
                      <span style={s('margin-left:auto;padding:1px 8px;border-radius:9999px;background:var(--warning-100);font:600 11px/1.7 var(--font-cjk);color:var(--warning-700)')}>AI 识别 · 待人工确认</span>
                    </div>
                  )}
                  <div style={s('font:400 14px/1.75 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{e.excerpt}</div>
                  {e.hasRisk && (
                    <div style={s('margin-top:6px;font:400 12px/1.6 var(--font-cjk);color:var(--ink-500);text-wrap:pretty')}>AI 命中依据：{e.rationale}</div>
                  )}
                  <div style={s('margin-top:9px;display:flex;align-items:center;gap:14px;flex-wrap:wrap')}>
                    {e.codes.map((p) => (
                      <span key={p.code} style={s('padding:1px 7px;border-radius:9999px;background:var(--csop-blue-50);font:600 12px/1.7 var(--font-mono);color:var(--csop-blue-700)')}>{p.code}</span>
                    ))}
                    <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>评论 <span style={s('font:600 12px/1.4 var(--font-mono);color:var(--ink-800)')}>{e.comments}</span></span>
                    <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>互动 <span style={s('font:600 12px/1.4 var(--font-mono);color:var(--ink-800)')}>{e.inter}</span></span>
                    <a href={e.url} target="_blank" rel="noopener" onClick={e.stop} style={s('margin-left:auto;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none;white-space:nowrap')} className={hover('color:var(--csop-blue-800);text-decoration:underline')}>Futu 原文 ↗</a>
                    <span style={s('font:500 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>{e.expandLabel}</span>
                  </div>
                  {e.open && (
                    <div style={s('margin-top:12px;padding-top:12px;border-top:1px solid var(--border-1)')}>
                      <div style={s('font:600 12px/1.2 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400);margin-bottom:9px')}>单帖详情</div>
                      <div style={s('display:grid;grid-template-columns:repeat(2,1fr);gap:8px 14px;margin-bottom:11px')}>
                        {e.detail.map((d) => (
                          <div key={d.k} style={s('display:flex;gap:9px')}>
                            <span style={s('flex:none;width:68px;font:400 12px/1.6 var(--font-cjk);color:var(--ink-500)')}>{d.k}</span>
                            <span style={s('flex:1;font:500 12px/1.6 var(--font-mono);color:var(--ink-800)')}>{d.v}</span>
                          </div>
                        ))}
                      </div>
                      <div style={s('padding:10px 12px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>关系数据暂不可用 — 该帖的回复／转发关系尚未核验，不展示推测连线。</div>
                      <div style={s('margin-top:10px;font:400 12px/1.5 var(--font-cjk);color:var(--ink-400)')}>来源：{e.source} · 「Futu 原文」为演示链接，正式接入后指向真实帖子或评论</div>
                    </div>
                  )}
                </div>
              ))}

              {v.panelEmpty && (
                <div style={s('padding:16px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容 — 当前筛选范围内没有可展示的原文证据。</div>
              )}
              {v.panelUnavailable && (
                <div style={s('padding:16px;border:1px dashed var(--warning-600);border-radius:6px;background:var(--warning-100);font:400 14px/1.7 var(--font-cjk);color:var(--warning-700)')}>数据暂不可用 — 原文证据尚未生成或数据源未提供，本区域不展示推测内容。</div>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  )
}
