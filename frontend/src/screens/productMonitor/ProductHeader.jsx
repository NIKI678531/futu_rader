import { s, hover } from '../../lib/dc'

/* 产品身份卡 + 「切换产品」抽屉（搜索 · 板块 · 新品筛选 · 结果列表） */
export default function ProductHeader({ v }) {
  return (
    <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px')}>
      <div style={s('display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding:18px 20px')}>
        <div style={s('min-width:0')}>
          <div style={s('font:600 12px/1.2 var(--font-cjk);letter-spacing:0.18em;color:var(--csop-blue-600);margin-bottom:10px')}>市场 · 产品监控</div>
          <div style={s('display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px')}>
            <span style={s('padding:4px 12px;border-radius:9999px;background:var(--csop-navy-900);font:600 17px/1.4 var(--font-mono);color:#fff')}>{v.code}</span>
            <span style={s('font:600 22px/1.3 var(--font-cjk);letter-spacing:-0.01em')}>{v.name}</span>
          </div>
          <div style={s('display:flex;align-items:center;gap:7px;flex-wrap:wrap')}>
            <span style={s('padding:3px 9px;border-radius:4px;background:var(--canvas-alt);font:500 13px/1.5 var(--font-cjk);color:var(--ink-700)')}>{v.sectorName}</span>
            <span style={s('padding:3px 9px;border-radius:4px;background:var(--canvas-alt);font:500 13px/1.5 var(--font-cjk);color:var(--ink-700)')}>{v.struct}</span>
            <span style={s('padding:3px 9px;border-radius:4px;background:var(--canvas-alt);font:500 13px/1.5 var(--font-cjk);color:var(--ink-700)')}>{v.issuer}</span>
            <span style={s(`padding:3px 9px;border-radius:4px;background:${v.ownBg};font:600 13px/1.5 var(--font-cjk);color:${v.ownFg}`)}>{v.ownLabel}</span>
            <span style={s(`padding:3px 9px;border-radius:4px;background:${v.newBg};font:600 13px/1.5 var(--font-cjk);color:${v.newFg}`)}>{v.newLabel}</span>
            <span style={s('font:400 13px/1.5 var(--font-cjk);color:var(--ink-500)')}>上市 {v.listing}</span>
          </div>
        </div>
        <div onClick={v.toggleSel} style={s(`flex:none;display:flex;align-items:center;gap:9px;padding:9px 16px;border:1px solid ${v.selBc};border-radius:6px;background:${v.selBg};font:600 14px/1.4 var(--font-cjk);color:${v.selFg};cursor:pointer;transition:all 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>切换产品<span style={s('font:400 12px/1.4 var(--font-mono)')}>{v.selCaret}</span></div>
      </div>

      {v.selOpen && (
        <div style={s('border-top:1px solid var(--border-1);background:var(--canvas);padding:16px 20px 18px')}>
          <div style={s('display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px')}>
            <input value={v.q} onChange={v.onSearch} placeholder="搜索产品代码或名称" style={s('width:250px;padding:8px 12px;border:1px solid var(--border-2);border-radius:6px;font:400 14px/1.4 var(--font-cjk);color:var(--ink-900);background:#fff')} />
            <div style={s('display:flex;align-items:center;gap:6px;flex-wrap:wrap')}>
              <span style={s('font:600 12px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>板块</span>
              {v.secChips.map((c) => (
                <div key={c.name} onClick={c.go} style={s(`display:flex;align-items:center;gap:5px;padding:4px 9px;white-space:nowrap;border:1px solid ${c.bc};border-radius:9999px;background:${c.bg};font:${c.fw} 13px/1.4 var(--font-cjk);color:${c.fg};cursor:pointer`)}>
                  <span style={s(`width:7px;height:7px;border-radius:2px;background:${c.dot}`)}></span>{c.name}
                </div>
              ))}
            </div>
            <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
              {v.newOptions.map((m) => (
                <div key={m.label} onClick={m.go} style={s(`padding:6px 13px;font:${m.fw} 13px/1.4 var(--font-cjk);color:${m.fg};background:${m.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{m.label}</div>
              ))}
            </div>
            <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.resultCount} 只产品 · 新品＝上市未满 30 个自然日</span>
          </div>
          <div style={s('max-height:290px;overflow-y:auto;border:1px solid var(--border-1);border-radius:6px;background:#fff')}>
            {v.selList.map((p) => (
              <div key={p.code} onClick={p.go} style={s(`display:flex;align-items:center;gap:12px;padding:9px 13px;border-bottom:1px solid var(--ink-100);background:${p.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                <span style={s('flex:none;width:26px;font:600 12px/1.4 var(--font-mono);color:var(--ink-400);text-align:right')}>#{p.rank}</span>
                <span style={s('flex:none;width:52px;font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{p.code}</span>
                <span style={s('flex:1;min-width:0;font:400 14px/1.4 var(--font-cjk);color:var(--ink-700);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{p.name}</span>
                <span style={s(`flex:none;padding:1px 7px;border-radius:3px;background:${p.obg};font:600 11px/1.7 var(--font-cjk);color:${p.ofg}`)}>{p.own}</span>
                <span style={s(`flex:none;padding:1px 7px;border-radius:3px;background:${p.nbg};font:600 11px/1.7 var(--font-cjk);color:${p.nfg}`)}>{p.newTag}</span>
                <span style={s('flex:none;width:62px;text-align:right;font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{p.sector}</span>
                <span style={s('flex:none;width:56px;text-align:right;font:600 14px/1.4 var(--font-mono);color:var(--ink-800)')}>{p.mentions}</span>
              </div>
            ))}
            {v.selEmpty && (
              <div style={s('padding:18px;font:400 14px/1.6 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容 — 当前搜索与筛选下没有匹配的产品。</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
