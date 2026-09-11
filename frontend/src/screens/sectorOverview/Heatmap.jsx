import { s, hover } from '../../lib/dc'

/* 热力图 — squarified treemap of the top 20 by 讨论热度 (area) coloured by 情绪净值 or
   评论量环比; products outside the current filter stay in place but go grey. The tail is
   collapsed into the dashed row underneath, which jumps to the full table. */
export default function Heatmap({ v }) {
  return (
    <div style={s('flex:0 0 32%;min-width:380px;max-width:640px;box-sizing:border-box;border-left:1px solid var(--border-1);display:flex;flex-direction:column')}>
      <div style={s('flex:none;display:flex;align-items:center;gap:10px;height:44px;padding:0 14px;border-bottom:1px solid var(--border-1)')}>
        <span style={s('flex:none;font:600 14px/1.3 var(--font-cjk);color:var(--ink-900)')}>热力图</span>
        <div style={s('flex:none;display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
          {v.heatModes.map((m) => (
            <div key={m.label} onClick={m.go} style={s(`padding:5px 11px;font:${m.fw} 13px/1.4 var(--font-cjk);color:${m.fg};background:${m.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{m.label}</div>
          ))}
        </div>
      </div>
      <div style={s('flex:none;display:flex;align-items:center;gap:8px;height:34px;padding:0 14px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1)')}>
        <span style={s('flex:none;font:600 13px/1.4 var(--font-cjk);color:var(--ink-800);white-space:nowrap')}>色阶 · {v.heatLegendTitle}</span>
        <div style={s('display:flex')}>
          {v.heatLegend.map((g) => (
            <span key={g.label} style={s(`padding:3px 7px;background:${g.bg};border-radius:${g.radius};font:600 11px/1.4 var(--font-mono);color:${g.fg}`)}>{g.label}</span>
          ))}
        </div>
      </div>
      <div style={s('flex:none;display:flex;align-items:center;gap:14px;height:28px;padding:0 14px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap;overflow:hidden')}>
        <span style={s('flex:none;display:flex;align-items:center;gap:6px')}><span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.06em;color:var(--ink-400)')}>面积</span>热度＝评论量＋0.3×点赞＋转发</span>
        <span style={s('flex:none;width:1px;height:12px;background:var(--border-2)')}></span>
        <span style={s('flex:1 1 auto;min-width:0;display:flex;align-items:center;gap:6px;overflow:hidden')}><span style={s('flex:none;font:600 11px/1.4 var(--font-cjk);letter-spacing:0.06em;color:var(--ink-400)')}>颜色</span><span style={s('min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap')}>{v.heatMetricNote}</span></span>
      </div>
      <div style={s('flex:none;padding:4px 14px 14px')}>
        <div ref={v.heatRef} style={s('position:relative;width:100%;height:440px')}>
          {v.tiles.map((t) => (
            <div key={t.key} onClick={t.go} onMouseEnter={t.hoverIn} onMouseLeave={t.hoverOut} style={s(`position:absolute;left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;box-sizing:border-box;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;overflow:hidden;background:${t.bg};box-shadow:${t.ring};border-radius:2px;cursor:pointer`)}>
              {t.risk && (
                <span title="需合规关注 · AI 识别待人工确认" style={s('position:absolute;top:4px;right:4px;width:7px;height:7px;border-radius:9999px;background:var(--negative-600);box-shadow:0 0 0 1.5px #fff')}></span>
              )}
              <span style={s(`font:${t.codeFont};color:${t.fg};white-space:nowrap`)}>{t.code}</span>
              <span style={s(`font:${t.valFont};color:${t.fg};white-space:nowrap`)}>{t.val}</span>
              <span style={s(`font:${t.subFont};color:${t.fg};opacity:0.82;white-space:nowrap`)}>{t.sub}</span>
            </div>
          ))}
        </div>
        <div onClick={v.tailGo} style={s('display:flex;align-items:center;gap:10px;width:100%;box-sizing:border-box;height:36px;margin-top:6px;padding:0 12px;border:1px dashed var(--border-2);border-radius:4px;background:var(--canvas-alt);cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)')} className={hover('background:var(--csop-blue-50)')}>
          <span style={s('flex:none;font:600 13px/1.4 var(--font-cjk);color:var(--ink-700)')}>其他 {v.heatTailN} 只</span>
          <span style={s('flex:1;min-width:0;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>合计讨论热度 {v.tailHeat} · 占全市场 {v.tailShare}</span>
          <span style={s('flex:none;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600)')}>看完整榜单 →</span>
        </div>
        {/* 面积＝热度，热度未知就画不出格子。但少画几个格子看不出来，于是这张图会
            冒充全市场的全貌 —— 所以缺了几只必须写在图边上。demo 下恒为 0，不渲染。 */}
        {v.heatNaShow && (
          <div title={v.heatNaWhy} style={s('display:flex;align-items:center;gap:8px;width:100%;box-sizing:border-box;height:32px;margin-top:6px;padding:0 12px;border:1px dashed var(--warning-600);border-radius:4px;background:var(--warning-100);font:400 12px/1.4 var(--font-cjk);color:var(--warning-700)')}>{v.heatNaText}</div>
        )}
      </div>
    </div>
  )
}
