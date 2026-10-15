import { s, hover } from '../../lib/dc'

function TopCard({ box, title, rows, empty }) {
  return (
    <div style={s(box)}>
      <div style={s('display:flex;align-items:baseline;gap:8px;margin-bottom:6px')}>
        <span style={s('font:500 12px/1.4 var(--font-cjk);color:var(--ink-600);white-space:nowrap')}>{title}</span>
      </div>
      <div style={s('display:flex;flex-direction:column;gap:3px')}>
        {rows.map((t) => (
          <div key={t.code} onClick={t.go} title={t.title} style={s('display:flex;align-items:center;gap:7px;height:22px;padding:0 5px;margin:0 -5px;border-radius:var(--radius-small);cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)')} className={hover('background:var(--csop-blue-50)')}>
            <span style={s('flex:none;width:12px;font:600 11px/1 var(--font-mono);color:var(--ink-400)')}>{t.rank}</span>
            <span style={s('flex:none;font:600 12px/1.4 var(--font-mono);color:var(--ink-900)')}>{t.code}</span>
            <span style={s('flex:1;min-width:0;font:400 12px/1.4 var(--font-cjk);color:var(--ink-700);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{t.name}</span>
            <span style={s('flex:none;font:600 12px/1.4 var(--font-mono);color:var(--positive-700)')}>{t.growth}</span>
            <span style={s('flex:none;font:400 11px/1.4 var(--font-mono);color:var(--ink-400);white-space:nowrap')}>筛后 {t.comments} · 相关 {t.relatedComments}</span>
          </div>
        ))}
      </div>
      {empty && <div style={s('font:400 12px/1.5 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>暂无相关内容 — 基准期样本不足，暂无可比产品</div>}
    </div>
  )
}

/* Page identity and the four market summaries share one compact strip. */
export default function Kpis({ v }) {
  const { k1, k2 } = v

  return (
    <section data-page-summary style={s('display:grid;grid-template-columns:minmax(330px,30%) 12.5% 14.5% 21.5% 21.5%;min-height:124px;align-items:stretch;background:#fff;border:1px solid var(--border-1);border-radius:var(--radius-card);box-shadow:0 1px 2px rgba(14,42,82,0.04);margin-bottom:10px;overflow:hidden')}>
      <div style={s('display:flex;flex-direction:column;justify-content:center;min-width:0;padding:18px 22px;border-right:1px solid var(--border-1)')}>
        <div style={s('font:600 12px/1.2 var(--font-cjk);color:var(--csop-blue-600);margin-bottom:10px')}>市场 · 板块总览</div>
        <div style={s('font:600 26px/1.2 var(--font-cjk);letter-spacing:-0.015em;color:var(--csop-navy-900);white-space:nowrap')}>全市场活跃 ETF 舆情分布</div>
      </div>
      <div style={s('display:flex;flex-direction:column;justify-content:center;padding:14px 20px;border-right:1px solid var(--border-1);min-width:0')}>
        <div style={s('font:500 12px/1.4 var(--font-cjk);color:var(--ink-600);margin-bottom:8px')}>CSOP产品讨论热度</div>
        <div style={s('display:flex;align-items:baseline;gap:8px')}><span style={s('font:600 27px/1 var(--font-mono);letter-spacing:-0.01em;color:var(--csop-navy-900)')}>{k1.value}</span><span style={s(`font:500 12px/1.4 var(--font-mono);color:${k1.dfg}`)}>{k1.delta}</span></div>
      </div>
      <div style={s('display:flex;flex-direction:column;justify-content:center;padding:14px 18px;border-right:1px solid var(--border-1);min-width:0')}>
        <div style={s('display:flex;align-items:baseline;gap:6px;margin-bottom:8px')}><span style={s('font:500 12px/1.4 var(--font-cjk);color:var(--ink-600)')}>舆情管理 · CSOP 自家产品</span>{k2.partialData && k2.coverageLabel && <span style={s('margin-left:auto;font:500 10px/1.5 var(--font-cjk);color:var(--warning-700);white-space:nowrap')}>{k2.coverageLabel}</span>}</div>
        <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:12px')}>
          <div><div style={s('font:600 24px/1 var(--font-mono);color:var(--negative-600)')}>{k2.neg}</div><div style={s('margin-top:5px;font:500 11px/1.4 var(--font-cjk);color:var(--negative-700)')}>负面舆情</div></div>
          <div><div style={s('font:600 24px/1 var(--font-mono);color:var(--positive-700)')}>{k2.pos}</div><div style={s('margin-top:5px;font:500 11px/1.4 var(--font-cjk);color:var(--positive-700)')}>正面好评</div></div>
        </div>
        <div title={k2.barTitle} style={s('display:flex;height:5px;border-radius:3px;overflow:hidden;background:var(--ink-100);margin-top:7px')}><span style={s(`width:${k2.negW}%;background:var(--negative-600)`)}></span><span style={s(`width:${k2.posW}%;background:var(--positive-600)`)}></span></div>
      </div>
      <TopCard box="display:flex;flex-direction:column;justify-content:center;padding:14px 16px;border-right:1px solid var(--border-1);min-width:0" title="近期热议飙升的CSOP产品" rows={v.topOwn} empty={v.topOwnEmpty} />
      <TopCard box="display:flex;flex-direction:column;justify-content:center;padding:14px 16px;min-width:0" title="近期热议提升的其他产品" rows={v.topPeer} empty={v.topPeerEmpty} />
    </section>
  )
}
