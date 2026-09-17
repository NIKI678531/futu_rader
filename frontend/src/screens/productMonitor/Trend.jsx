import { s } from '../../lib/dc'
import R from '../../data/radar'

/* 讨论 ／ 情绪双轨 + K 线叠加。
   画布本身由 index.jsx 的 drawTrend() 逐帧绘制；这里只负责 canvas 容器、
   DOM 轴、图例开关与 hover 浮层。ref 回调来自 renderVals()。 */
export default function Trend({ v }) {
  return (
    <div style={s('position:relative;background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px')}>
      <div style={s('display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 20px;border-bottom:1px solid var(--border-1)')}>
        <div style={s('display:flex;align-items:baseline;gap:11px;min-width:0')}>
          <span style={s('flex:none;white-space:nowrap;font:600 18px/1.3 var(--font-cjk)')}>{v.trendTitle}</span>
          <span style={s('min-width:0;font:400 13px/1.4 var(--font-cjk);color:var(--ink-500);text-wrap:pretty')}>实线为当前区间，虚线为基准区间 {v.benchText}；K 线仅显示当前区间</span>
          {v.pxUnavailable && (
            <span style={s('flex:none;padding:2px 9px;border-radius:9999px;background:var(--warning-100);font:600 12px/1.6 var(--font-cjk);color:var(--warning-700);white-space:nowrap')}>价格数据暂不可用</span>
          )}
        </div>
        <div style={s('flex:none;display:flex;align-items:center;gap:14px')}>
          {v.trendLegend.map((l) => (
            <div key={l.label} onClick={l.go} style={s(`flex:none;display:flex;align-items:center;gap:7px;cursor:pointer;opacity:${l.op};white-space:nowrap`)}>
              <span style={s(`flex:none;width:14px;height:3px;border-radius:2px;background:${l.color}`)}></span>
              <span style={s('white-space:nowrap;font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>{l.label}</span>
            </div>
          ))}
        </div>
      </div>
      {v.heatDisclosure && <div style={s('padding:10px 20px;font:400 12px/1.6 var(--font-cjk);color:var(--warning-700)')}>{v.heatDisclosure}</div>}
      <div ref={v.trendBoxRef} style={s('padding:14px 20px 10px')}>
        <div style={s(`position:relative;width:${v.trendW}px;height:340px`)}>
          <canvas ref={v.trendRef} onMouseMove={v.trendMove} onMouseLeave={v.trendLeave} style={s(`display:block;width:${v.trendW}px;height:340px`)}></canvas>
          <span style={s('position:absolute;left:8px;top:6px;font:500 13px/1.3 var(--font-cjk);color:var(--ink-600);white-space:nowrap')}>讨论 · 评论数 ／ 活跃账号数（左轴）</span>
          <span style={s(`position:absolute;left:${v.axRX}px;top:6px;font:500 12px/1.3 var(--font-cjk);color:var(--ink-500);white-space:nowrap`)}>互动数</span>
          <span style={s('position:absolute;left:8px;top:202px;font:500 13px/1.3 var(--font-cjk);color:var(--ink-600);white-space:nowrap')}>情绪 · 积极 ／ 消极内容数（左轴）</span>
          {v.pxOk && (
            <>
              <span style={s(`position:absolute;left:${v.axPX}px;top:6px;font:600 12px/1.3 var(--font-cjk);color:var(--ink-700);white-space:nowrap`)}>价格 {v.pxCurrency}</span>
              <span style={s(`position:absolute;left:${v.axPX}px;top:202px;font:600 12px/1.3 var(--font-cjk);color:var(--ink-700);white-space:nowrap`)}>价格 {v.pxCurrency}</span>
            </>
          )}
        </div>
        <div style={s(`display:flex;width:${v.trendW}px;box-sizing:border-box;padding:0 136px 0 64px`)}>
          {v.axisCells.map((a, i) => (
            <div key={i} style={s('flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;gap:4px')}>
              <span style={s(`width:1px;height:${a.tick}px;background:${a.tickC}`)}></span>
              <span style={s(`font:${a.fw} 13px/1.3 var(--font-mono);color:${a.fg};white-space:nowrap`)}>{a.v}</span>
            </div>
          ))}
        </div>
        {v.hasDayBands && (
          <div style={s(`display:flex;width:${v.trendW}px;box-sizing:border-box;padding:7px 136px 0 64px;margin-top:6px;border-top:1px solid var(--border-1)`)}>
            {v.dayBands.map((d, i) => (
              <div key={i} style={s(`flex:${d.flex};min-width:0;text-align:center;font:600 13px/1.3 var(--font-mono);color:var(--ink-700);white-space:nowrap;overflow:hidden`)}>{d.label}</div>
            ))}
          </div>
        )}
      </div>
      <div style={s('padding:0 20px 14px;font:400 13px/1.6 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>两条轨道共享同一时间轴与 hover：讨论轨道为评论数、活跃账号数（左轴）与互动数（右轴），情绪轨道为积极与消极内容数（左轴）；所选 ETF 的 OHLC K 线叠加在两条轨道底层，{v.pxKLabel}，刻度使用最右侧独立的价格轴，不与舆情单位共用标尺；当前舆情粒度{v.granLabel}，{v.pxNote}。同步观察所选 ETF 的价格波动、评论量与活跃账号变化；时间上的同步或先后关系不代表价格变化必然导致舆情变化。{R.DATA_PROVIDER === 'sql' ? '舆情来自原始记录；行情来源 FMP，拆股调整、不含股息调整。缺失行情不补零。' : '行情与舆情数值均为演示数据。'}</div>

      {v.hoverOpen && (
        <div style={s(`position:absolute;left:${v.hoverX}px;top:${v.hoverY}px;z-index:20;pointer-events:none;background:var(--csop-navy-900);color:#fff;border-radius:6px;padding:11px 13px;box-shadow:0 6px 20px rgba(14,42,82,0.28);width:336px;box-sizing:border-box`)}>
          <div style={s('font:600 13px/1.4 var(--font-mono);margin-bottom:9px')}>{v.hoverTitle}</div>
          {v.hoverRows.map((h) => (
            <div key={h.label} style={s('display:flex;align-items:center;gap:12px;padding:3px 0')}>
              <span style={s(`width:8px;height:8px;border-radius:2px;background:${h.color};flex:none`)}></span>
              <span style={s('flex:1;font:400 12px/1.5 var(--font-cjk);color:rgba(255,255,255,0.72)')}>{h.label}</span>
              <span style={s('font:600 13px/1.5 var(--font-mono);color:#fff')}>{h.cur}</span>
              <span style={s('width:40px;text-align:right;font:400 12px/1.5 var(--font-mono);color:rgba(255,255,255,0.6)')}>{h.base}</span>
              <span style={s(`width:78px;text-align:right;font:600 12px/1.5 var(--font-mono);color:${h.dfg}`)}>{h.delta}</span>
            </div>
          ))}
          <div style={s('margin-top:8px;padding-top:7px;border-top:1px solid rgba(255,255,255,0.16);font:400 11px/1.5 var(--font-cjk);color:rgba(255,255,255,0.55)')}>数值依次为当前 · 基准 · 差值（百分比）</div>
          {v.hoverPxOn && (
            <div style={s('margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.16)')}>
              <div style={s('display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:5px')}>
                <span style={s('font:600 12px/1.5 var(--font-cjk);color:rgba(255,255,255,0.85)')}>{v.hoverPxTitle}</span>
                <span style={s('font:400 11px/1.5 var(--font-cjk);color:rgba(255,255,255,0.55)')}>{v.hoverPxMeta}</span>
              </div>
              {v.hoverPxHas && (
                <div style={s('display:grid;grid-template-columns:repeat(4,1fr) 1.6fr;gap:6px')}>
                  {v.hoverPx.map((c) => (
                    <div key={c.k} style={s('display:flex;flex-direction:column;gap:1px')}>
                      <span style={s('font:400 11px/1.4 var(--font-cjk);color:rgba(255,255,255,0.6)')}>{c.k}</span>
                      <span style={s(`font:600 13px/1.4 var(--font-mono);color:${c.fg};white-space:nowrap`)}>{c.v}</span>
                    </div>
                  ))}
                </div>
              )}
              {v.hoverPxNone && (
                <div style={s('font:400 12px/1.5 var(--font-cjk);color:rgba(255,255,255,0.7)')}>{v.hoverPxNote}</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
