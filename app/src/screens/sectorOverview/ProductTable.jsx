import { s, hover } from '../../lib/dc'

/* 产品榜单 — one table for every sort key; 序号 is the rank under the current sort.
   Rows share the treemap's hover card, but track the cursor horizontally. */
export default function ProductTable({ v }) {
  return (
    <div style={s('flex:1;min-width:0;box-sizing:border-box;display:flex;flex-direction:column')}>
      <div style={s('flex:none;display:flex;align-items:center;gap:10px;height:44px;padding:0 14px;border-bottom:1px solid var(--border-1)')}>
        <span style={s('flex:none;font:600 14px/1.3 var(--font-cjk);color:var(--ink-900)')}>产品榜单</span>
        <span style={s('flex:none;font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.boardNote}</span>
        {v.listAll && (
          <div onClick={v.listToggle} style={s('flex:none;display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:9999px;background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>完整榜单 {v.poolCount} 只<span style={s('font:400 12px/1 var(--font-cjk)')}>✕</span></div>
        )}
        <span style={s('margin-left:auto;min-width:0;text-align:right;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>热度＝评论量＋0.3×点赞＋转发 · 点表头排序</span>
      </div>
      <div style={s('flex:none;display:flex;align-items:center;gap:8px;height:40px;padding:0 14px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1)')}>
        <span style={s('flex:none;width:36px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-800)')}>序号</span>
        <span style={s('flex:none;width:56px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-800)')}>代码</span>
        <span style={s('flex:1;min-width:120px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-800);white-space:nowrap')}>产品名称</span>
        <div onClick={v.sortComments.go} style={s(`flex:none;width:64px;display:flex;align-items:center;justify-content:flex-end;gap:3px;cursor:pointer;font:${v.sortComments.fw} 14px/1.4 var(--font-cjk);color:${v.sortComments.fg}`)} className={hover('color:var(--csop-blue-600)')}>评论量<span style={s('font:600 9px/1 var(--font-mono)')}>{v.sortComments.caret}</span></div>
        <div onClick={v.sortGrowth.go} style={s(`flex:none;width:60px;display:flex;align-items:center;justify-content:flex-end;gap:3px;cursor:pointer;font:${v.sortGrowth.fw} 14px/1.4 var(--font-cjk);color:${v.sortGrowth.fg}`)} className={hover('color:var(--csop-blue-600)')}>环比<span style={s('font:600 9px/1 var(--font-mono)')}>{v.sortGrowth.caret}</span></div>
        <div onClick={v.sortHeat.go} style={s(`flex:none;width:72px;display:flex;align-items:center;justify-content:flex-end;gap:3px;cursor:pointer;font:${v.sortHeat.fw} 14px/1.4 var(--font-cjk);color:${v.sortHeat.fg}`)} className={hover('color:var(--csop-blue-600)')}>讨论热度<span style={s('font:600 9px/1 var(--font-mono)')}>{v.sortHeat.caret}</span></div>
        <div onClick={v.sortAtt.go} style={s(`flex:none;width:120px;display:flex;align-items:center;gap:3px;cursor:pointer;font:${v.sortAtt.fw} 14px/1.4 var(--font-cjk);color:${v.sortAtt.fg}`)} className={hover('color:var(--csop-blue-600)')}>正面 ／ 负面<span style={s('font:600 9px/1 var(--font-mono)')}>{v.sortAtt.caret}</span></div>
        <div onClick={v.sortNeg.go} style={s(`flex:none;width:48px;display:flex;align-items:center;justify-content:flex-end;gap:3px;cursor:pointer;font:${v.sortNeg.fw} 14px/1.4 var(--font-cjk);color:${v.sortNeg.fg}`)} className={hover('color:var(--csop-blue-600)')}>舆情<span style={s('font:600 9px/1 var(--font-mono)')}>{v.sortNeg.caret}</span></div>
        <span title={v.hotRule} style={s('flex:1.3;min-width:220px;display:flex;align-items:center;gap:6px;padding-left:10px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-800);white-space:nowrap')}>热议总结<span style={s('padding:0 6px;border-radius:9999px;background:var(--csop-blue-50);font:500 11px/1.6 var(--font-cjk);color:var(--csop-blue-700)')}>AI 生成</span></span>
      </div>
      <div style={s('flex:none;height:540px;overflow-y:auto')}>
        {v.rows.map((r) => (
          <div key={r.code} onClick={r.go} onMouseEnter={r.hoverIn} onMouseLeave={r.hoverOut} style={s(`display:flex;align-items:center;gap:8px;height:54px;box-sizing:border-box;padding:0 14px;background:${r.bg};border-bottom:1px solid var(--ink-100);cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
            <span style={s('flex:none;width:36px;font:500 13px/1.4 var(--font-mono);color:var(--ink-400)')}>{r.idx}</span>
            <span style={s(`flex:none;width:56px;font:600 14px/1.4 var(--font-mono);color:${r.codeFg}`)}>{r.code}</span>
            <span style={s('flex:1;min-width:120px;display:flex;align-items:center;gap:8px;overflow:hidden')}>
              <span title={r.name} style={s('flex:1;min-width:0;font:400 14px/1.4 var(--font-cjk);color:var(--ink-700);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{r.name}</span>
            </span>
            <span style={s('flex:none;width:64px;text-align:right;font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{r.comments}</span>
            <span style={s(`flex:none;width:60px;text-align:right;font:600 13px/1.4 var(--font-mono);color:${r.gfg}`)}>{r.growth}</span>
            <span style={s('flex:none;width:72px;text-align:right;font:500 13px/1.4 var(--font-mono);color:var(--ink-700)')}>{r.heat}</span>
            <div style={s('flex:none;width:120px;display:flex;align-items:center;gap:6px')}>
              {r.hasAttitude && (
                <>
                  <span style={s('flex:none;display:flex;width:56px;height:6px;border-radius:3px;overflow:hidden;background:var(--ink-100)')}>
                    <span style={s(`width:${r.posW}%;background:var(--positive-600)`)}></span>
                    <span style={s(`width:${r.negW}%;background:var(--negative-600)`)}></span>
                  </span>
                  <span style={s('flex:none;font:600 12px/1.4 var(--font-mono);color:var(--positive-700)')}>{r.pos}</span>
                  <span style={s('flex:none;font:600 12px/1.4 var(--font-mono);color:var(--negative-700)')}>{r.neg}</span>
                </>
              )}
              {r.lowSample && (
                <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>样本不足</span>
              )}
            </div>
            <span style={s('flex:none;width:48px;display:flex;justify-content:flex-end')}>
              {r.alert && (
                <span title="可归类为需关注问题的内容条数" style={s('padding:1px 7px;border-radius:9999px;background:var(--negative-100);font:600 12px/1.6 var(--font-mono);color:var(--negative-700)')}>{r.alertN}</span>
              )}
            </span>
            <span title={r.hotTitle} style={s(`flex:1.3;min-width:220px;padding-left:10px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;font:400 13px/18px var(--font-cjk);color:${r.hotFg};text-wrap:pretty`)}>{r.hot}</span>
          </div>
        ))}
        {v.listMore && (
          <div onClick={v.listToggle} style={s('display:flex;align-items:center;justify-content:center;height:38px;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);background:var(--canvas);border-bottom:1px solid var(--ink-100);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>{v.listMoreLabel}</div>
        )}
        {v.rowsEmpty && (
          <div style={s('padding:16px;font:400 14px/1.6 var(--font-cjk);color:var(--ink-500)')}>{v.rowsEmptyText}</div>
        )}
      </div>
    </div>
  )
}
