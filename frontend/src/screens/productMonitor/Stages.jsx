import React from 'react'
import { s, hover } from '../../lib/dc'

/* 热度变化与阶段观点 — 热度折线（SVG）＋ 阶段带 ＋ 可展开的阶段表 */
export default function Stages({ v }) {
  return (
    <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px')}>
      <div style={s('display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 20px;border-bottom:1px solid var(--border-1)')}>
        <div style={s('display:flex;align-items:baseline;gap:11px;min-width:0')}>
          <span style={s('flex:none;white-space:nowrap;font:600 18px/1.3 var(--font-cjk)')}>热度变化与阶段观点</span>
          <span style={s('min-width:0;font:400 13px/1.4 var(--font-cjk);color:var(--ink-500);text-wrap:pretty')}>讨论热度随时间变化 · AI 按时段归纳主流观点（{v.stageGranLabel}）· 与上方趋势面板同一时间轴</span>
        </div>
        <div style={s('flex:none;display:flex;align-items:center;gap:8px')}>
          <span style={s('padding:3px 10px;border-radius:9999px;background:var(--warning-100);font:600 12px/1.6 var(--font-cjk);color:var(--warning-700);white-space:nowrap')}>AI 生成 · 可追溯原文</span>
          <span style={s('padding:3px 10px;border-radius:9999px;background:var(--csop-blue-50);font:500 12px/1.6 var(--font-cjk);color:var(--csop-blue-700);white-space:nowrap')}>折线粒度 {v.heatGranLabel}</span>
        </div>
      </div>
      {v.stageUnavailable && (
        <div style={s('margin:16px 20px;padding:14px 16px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>数据暂不可用 — 阶段观点尚未生成，热度序列与分时段观点会在下一批次采集后输出。</div>
      )}
      {v.stageEmpty && (
        <div style={s('margin:16px 20px;padding:14px 16px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容 — 当前日期范围内该 ETF 没有讨论，不输出阶段观点。</div>
      )}
      {v.stageOk && (
        <>
          <div style={s('padding:14px 20px 6px')}>
            <div style={s(`position:relative;width:${v.trendW}px;height:150px`)}>
              {v.stageBands.map((b) => (
                <div key={b.n} onClick={b.go} onMouseEnter={b.in} onMouseLeave={b.out} title={b.title} style={s(`position:absolute;left:${b.x}px;top:0;width:${b.w}px;height:150px;box-sizing:border-box;background:${b.bg};opacity:${b.op};border-left:1px dashed var(--border-2);box-shadow:${b.ring};cursor:pointer;transition:opacity 120ms cubic-bezier(.4,0,.2,1)`)}>
                  <span style={s(`position:absolute;left:5px;top:4px;padding:0 6px;border-radius:9999px;background:${b.tagBg};font:600 11px/1.6 var(--font-mono);color:${b.fg}`)}>{b.n}</span>
                </div>
              ))}
              {v.heatGrid.map((g, i) => (
                <React.Fragment key={i}>
                  <div style={s(`position:absolute;left:64px;width:${v.heatGridW}px;top:${g.y}px;height:1px;background:var(--border-1);pointer-events:none`)}></div>
                  <span style={s(`position:absolute;left:0;width:55px;top:${g.ly}px;text-align:right;font:500 10px/1 var(--font-mono);color:var(--ink-400);pointer-events:none`)}>{g.v}</span>
                </React.Fragment>
              ))}
              <svg width={v.trendW} height="150" style={s('position:absolute;left:0;top:0;pointer-events:none')}>
                <path d={v.heatArea} fill="rgba(35,97,173,0.08)" stroke="none"></path>
                <path d={v.heatPath} fill="none" stroke="#2361AD" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"></path>
              </svg>
              {v.heatLowDots.map((d, i) => (
                <span key={i} title={d.tip} style={s(`position:absolute;left:${d.x}px;top:136px;width:6px;height:6px;margin-left:-3px;border-radius:9999px;background:var(--ink-300)`)}></span>
              ))}
              {v.heatCols.map((c, i) => (
                <div key={i} onMouseEnter={c.in} onMouseLeave={c.out} style={s(`position:absolute;left:${c.x}px;top:0;width:${c.w}px;height:150px`)}></div>
              ))}
              {v.heatHoverOn && (
                <>
                  <div style={s(`position:absolute;left:${v.heatHoverX}px;top:8px;width:1px;height:130px;background:var(--csop-navy-900);pointer-events:none`)}></div>
                  <span style={s(`position:absolute;left:${v.heatHoverX}px;top:${v.heatHoverY}px;width:9px;height:9px;margin:-4px 0 0 -4px;border-radius:9999px;background:#fff;border:2px solid var(--csop-blue-600);box-sizing:border-box;pointer-events:none`)}></span>
                  <div style={s(`position:absolute;left:${v.heatTipX}px;top:6px;z-index:5;width:250px;box-sizing:border-box;padding:10px 12px;border-radius:6px;background:var(--csop-navy-900);color:#fff;box-shadow:0 6px 20px rgba(14,42,82,0.28);pointer-events:none`)}>
                    <div style={s('font:600 13px/1.4 var(--font-mono);margin-bottom:6px')}>{v.heatTipTitle}</div>
                    {v.heatTipRows.map((h) => (
                      <div key={h.k} style={s('display:flex;align-items:center;justify-content:space-between;gap:10px;padding:2px 0')}>
                        <span style={s('font:400 12px/1.5 var(--font-cjk);color:rgba(255,255,255,0.72)')}>{h.k}</span>
                        <span style={s('font:600 12px/1.5 var(--font-mono);color:#fff;text-align:right')}>{h.v}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
              <span style={s('position:absolute;left:8px;top:-2px;font:500 13px/1.3 var(--font-cjk);color:var(--ink-600);white-space:nowrap;pointer-events:none')}>讨论热度（左轴）</span>
            </div>
            <div style={s(`position:relative;width:${v.trendW}px;height:20px`)}>
              {v.heatAxis.map((a, i) => (
                <span key={i} style={s(`position:absolute;left:${a.x}px;top:2px;transform:translateX(-50%);font:500 13px/1.3 var(--font-mono);color:var(--ink-600);white-space:nowrap`)}>{a.label}</span>
              ))}
            </div>
            {v.hasHeatDays && (
              <div style={s(`position:relative;width:${v.trendW}px;height:24px;margin-top:6px;border-top:1px solid var(--border-1)`)}>
                {v.heatDays.map((d, i) => (
                  <span key={i} style={s(`position:absolute;left:${d.x}px;width:${d.w}px;top:6px;text-align:center;font:600 13px/1.3 var(--font-mono);color:var(--ink-700);white-space:nowrap;overflow:hidden`)}>{d.label}</span>
                ))}
              </div>
            )}
          </div>
          <div style={s('border-top:1px solid var(--border-1)')}>
            <div style={s('display:flex;align-items:center;gap:14px;padding:8px 20px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1);font:600 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>
              <span style={s('flex:none;width:26px')}>阶段</span>
              <span style={s('flex:none;width:148px')}>起止</span>
              <span style={s('flex:none;width:96px')}>时段</span>
              <span style={s('flex:none;width:176px')}>观点分类 · 情绪</span>
              <span style={s('flex:1;min-width:0')}>一句话总结</span>
              <span style={s('flex:none;width:72px;text-align:right')}>样本</span>
              <span style={s('flex:none;width:120px;text-align:right')}>证据</span>
              <span style={s('flex:none;width:14px')}></span>
            </div>
            {v.stageRows.map((st) => (
              <React.Fragment key={st.n}>
                <div onClick={st.toggle} onMouseEnter={st.in} onMouseLeave={st.out} style={s(`display:flex;align-items:center;gap:14px;padding:11px 20px;border-bottom:1px solid var(--ink-100);background:${st.rowBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)}>
                  <span style={s(`flex:none;width:26px;height:22px;display:flex;align-items:center;justify-content:center;border-radius:9999px;background:${st.catBg};font:600 12px/1 var(--font-mono);color:${st.catFg}`)}>{st.n}</span>
                  <span style={s('flex:none;width:148px;font:600 13px/1.4 var(--font-mono);color:var(--ink-900);white-space:nowrap')}>{st.label}</span>
                  <span style={s('flex:none;width:96px;font:400 12px/1.4 var(--font-mono);color:var(--ink-400);white-space:nowrap')}>{st.sub}</span>
                  <span style={s('flex:none;width:176px;display:flex;align-items:center;gap:6px')}>
                    <span style={s(`padding:2px 9px;border-radius:4px;background:${st.catBg};font:600 12px/1.6 var(--font-cjk);color:${st.catFg};white-space:nowrap`)}>{st.cat}</span>
                    {st.hasSent && (
                      <span style={s(`padding:2px 8px;border-radius:9999px;background:${st.sbg};font:600 12px/1.5 var(--font-cjk);color:${st.sfg};white-space:nowrap`)}>{st.sent}</span>
                    )}
                  </span>
                  <span style={s(`flex:1;min-width:0;font:400 14px/1.5 var(--font-cjk);color:${st.sumFg};text-wrap:pretty`)}>{st.summary}</span>
                  <span style={s('flex:none;width:72px;text-align:right;font:500 13px/1.4 var(--font-mono);color:var(--ink-500);white-space:nowrap')}>{st.sample}</span>
                  <span style={s('flex:none;width:120px;display:flex;justify-content:flex-end')}>
                    {st.hasEv && (
                      <span onClick={st.openEvidence} style={s('display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600);white-space:nowrap;cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>证据 {st.evidence} →</span>
                    )}
                    {st.noEv && (
                      <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>—</span>
                    )}
                  </span>
                  <span style={s('flex:none;width:14px;font:400 9px/1 var(--font-cjk);color:var(--ink-400);text-align:right')}>{st.caret}</span>
                </div>
                {st.open && (
                  <div style={s('padding:8px 20px 12px 60px;background:var(--canvas);border-bottom:1px solid var(--ink-100)')}>
                    <div style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400);margin-bottom:4px')}>{st.unitNote}</div>
                    {st.digests.map((d, i) => (
                      <div key={i} style={s('display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px dashed var(--border-1)')}>
                        <span style={s('flex:none;width:112px;font:600 12px/1.4 var(--font-mono);color:var(--ink-800);white-space:nowrap')}>{d.label}</span>
                        <span style={s('flex:none;width:96px;font:400 12px/1.4 var(--font-mono);color:var(--ink-400);white-space:nowrap')}>{d.sub}</span>
                        <span style={s(`flex:none;width:64px;padding:1px 8px;box-sizing:border-box;border-radius:9999px;background:${d.tbg};font:600 11px/1.6 var(--font-cjk);color:${d.tfg};text-align:center;white-space:nowrap`)}>{d.tone}</span>
                        <span style={s(`flex:1;min-width:0;font:400 13px/1.5 var(--font-cjk);color:${d.dfg}`)}>{d.digest}</span>
                        <span style={s('flex:none;font:400 12px/1.4 var(--font-mono);color:var(--ink-400);white-space:nowrap')}>{d.meta}</span>
                      </div>
                    ))}
                  </div>
                )}
              </React.Fragment>
            ))}
          </div>
          <div style={s('padding:12px 20px 14px;font:400 13px/1.6 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>{v.stageRule} 热度口径与全站一致（{v.heatFormulaText}）；点阶段行展开逐时段摘要，「证据 →」打开该阶段区间的原文侧栏，每条可跳转 Futu 原文。阶段观点为演示数据。</div>
        </>
      )}
    </div>
  )
}
