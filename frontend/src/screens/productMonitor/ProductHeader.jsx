import { s, hover } from '../../lib/dc'
import KpiNote from './KpiNote'

function wholePercent(value) {
  const parsed = Number.parseFloat(value)
  return Number.isFinite(parsed) ? `${Math.round(parsed)}%` : value
}

function metric(v, label) {
  return v.kpis.find((item) => item.label === label) || { label, value: '—', delta: '', dfg: 'var(--ink-400)' }
}

/* Product identity and the four decision metrics share the screenshot-style hero. */
export default function ProductHeader({ v }) {
  const heat = metric(v, '讨论热度')
  const active = metric(v, '活跃账号数')
  const rank = metric(v, '全市场筛后评论量排名')
  const positive = wholePercent(v.posShare)
  const negative = wholePercent(v.negShare)

  return (
    <section data-page-summary data-product-summary style={s('background:#fff;border:1px solid var(--border-1);border-radius:var(--radius-card);box-shadow:0 1px 2px rgba(14,42,82,0.04);margin-bottom:10px;overflow:visible')}>
      <div style={s('display:grid;grid-template-columns:minmax(360px,30%) repeat(4,minmax(0,1fr));min-height:124px')}>
        <div role="button" aria-expanded={v.selOpen} tabIndex="0" onClick={v.toggleSel} title="点击切换产品" style={s('display:flex;flex-direction:column;justify-content:center;min-width:0;padding:17px 22px;border-right:1px solid var(--border-1);cursor:pointer')} className={hover('background:var(--canvas)')}>
          <div style={s('font:600 12px/1.2 var(--font-cjk);color:var(--csop-blue-600);margin-bottom:9px')}>市场 · 产品监控</div>
          <div style={s('font:600 24px/1.25 var(--font-cjk);letter-spacing:-0.015em;color:var(--csop-navy-900);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}><span style={s('font-family:var(--font-mono);margin-right:7px')}>{v.code}</span>{v.name}</div>
          <div style={s('margin-top:7px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{v.sectorName} · {v.struct} · {v.issuer} {v.ownLabel}</div>
        </div>

        <div style={s('display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:0;padding:14px;border-right:1px solid var(--border-1);text-align:center')}>
          <div style={s('display:flex;align-items:center;gap:6px;font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>讨论热度 <KpiNote k={heat} /></div>
          <div style={s('display:flex;align-items:baseline;gap:7px;margin-top:9px')}><span style={s('font:600 28px/1 var(--font-mono);color:var(--csop-navy-900)')}>{heat.value}</span><span title={heat.deltaTitle} style={s(`font:600 12px/1.4 var(--font-mono);color:${heat.dfg}`)}>{heat.delta}</span></div>
          <div style={s('margin-top:8px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>{v.benchLabel} · 热度指数</div>
        </div>
        <div style={s('display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:0;padding:14px;border-right:1px solid var(--border-1);text-align:center')}>
          <div style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>活跃账号数</div>
          <div style={s('display:flex;align-items:baseline;gap:7px;margin-top:9px')}><span style={s('font:600 28px/1 var(--font-mono);color:var(--csop-navy-900)')}>{active.value}</span><span style={s(`font:600 12px/1.4 var(--font-mono);color:${active.dfg}`)}>{active.delta}</span></div>
          <div style={s('margin-top:8px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>近 {v.rangeDays} 日参与讨论的去重账号</div>
        </div>
        <div style={s('display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:0;padding:14px;border-right:1px solid var(--border-1);text-align:center')}>
          <div style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>正面 / 负面态度</div>
          <div style={s('display:flex;align-items:baseline;justify-content:center;gap:30px;margin-top:9px;width:100%')}><span style={s('font:600 20px/1 var(--font-mono);color:var(--positive-700)')}>{positive}</span><span style={s('font:600 20px/1 var(--font-mono);color:var(--negative-600)')}>{negative}</span></div>
          <div style={s('display:flex;width:86%;height:5px;margin-top:11px;border-radius:3px;overflow:hidden;background:var(--ink-100)')}><span style={s(`width:${v.posBarPct}%;background:var(--positive-600)`)}></span><span style={s('flex:1;background:var(--negative-600)')}></span></div>
        </div>
        <div style={s('display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:0;padding:14px;text-align:center')}>
          <div style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>全市场筛后评论量排名</div>
          <div style={s('display:flex;align-items:baseline;gap:7px;margin-top:9px')}><span style={s('font:600 28px/1 var(--font-mono);color:var(--csop-navy-900)')}>{rank.value}</span><span style={s('font:600 12px/1.4 var(--font-mono);color:var(--ink-700)')}>{rank.delta}</span></div>
          <div style={s('margin-top:8px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>同一筛选口径</div>
        </div>
      </div>

      {v.selOpen && (
        <div style={s('border-top:1px solid var(--border-1);background:var(--canvas);padding:12px 18px 14px')}>
          <div style={s('display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px')}>
            <input value={v.q} onChange={v.onSearch} placeholder="搜索产品代码或名称" style={s('width:250px;padding:7px 11px;border:1px solid var(--border-2);border-radius:var(--radius-control);font:400 13px/1.4 var(--font-cjk);color:var(--ink-900);background:#fff')} />
            <div style={s('display:flex;align-items:center;gap:5px;flex-wrap:wrap')}>
              {v.secChips.map((c) => <div key={c.name} onClick={c.go} style={s(`display:flex;align-items:center;gap:5px;padding:4px 8px;white-space:nowrap;border:1px solid ${c.bc};border-radius:var(--radius-control);background:${c.bg};font:${c.fw} 12px/1.4 var(--font-cjk);color:${c.fg};cursor:pointer`)}><span style={s(`width:7px;height:7px;border-radius:2px;background:${c.dot}`)}></span>{c.name}</div>)}
            </div>
            <div style={s('display:flex;border:1px solid var(--border-2);border-radius:var(--radius-control);overflow:hidden;background:#fff')}>{v.newOptions.map((m) => <div key={m.label} onClick={m.go} style={s(`padding:5px 11px;font:${m.fw} 12px/1.4 var(--font-cjk);color:${m.fg};background:${m.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{m.label}</div>)}</div>
            <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.resultCount} 只产品</span>
          </div>
          <div style={s('max-height:260px;overflow-y:auto;border:1px solid var(--border-1);border-radius:var(--radius-control);background:#fff')}>
            {v.selList.map((p) => <div key={p.code} onClick={p.go} style={s(`display:flex;align-items:center;gap:12px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${p.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}><span style={s('flex:none;width:26px;font:600 12px/1.4 var(--font-mono);color:var(--ink-400);text-align:right')}>#{p.rank}</span><span style={s('flex:none;width:52px;font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{p.code}</span><span style={s('flex:1;min-width:0;font:400 13px/1.4 var(--font-cjk);color:var(--ink-700);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{p.name}</span><span style={s('flex:none;width:62px;text-align:right;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>{p.sector}</span></div>)}
            {v.selEmpty && <div style={s('padding:16px;font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容 — 当前搜索与筛选下没有匹配的产品。</div>}
          </div>
        </div>
      )}
    </section>
  )
}
