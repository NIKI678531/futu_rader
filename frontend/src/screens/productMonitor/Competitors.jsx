import { s } from '../../lib/dc'
import { ThumbUp, ThumbDown } from './Attitude'

/* 关联竞品观点 — 每张卡片一只关联产品，正／负两栏各自可打开证据侧栏 */
export default function Competitors({ v }) {
  return (
    <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06)')}>
      <div style={s('display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 20px;border-bottom:1px solid var(--border-1)')}>
        <div style={s('display:flex;align-items:baseline;gap:11px')}>
          <span style={s('font:600 18px/1.3 var(--font-cjk)')}>关联竞品观点</span>
          <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.compScopeText} · 按当前区间评论量降序</span>
          {v.compsStale && <span title={v.staleTitle} style={s('padding:2px 8px;border-radius:9999px;background:var(--warning-100);font:600 12px/1.6 var(--font-cjk);color:var(--warning-700);white-space:nowrap')}>AI 生成 · 待更新</span>}
        </div>
        <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>共 {v.compCount} 只关联产品</span>
      </div>
      {v.hasComps && (
        <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:16px 20px 20px')}>
          {v.comps.map((c) => (
            <div key={c.code} style={s('border:1px solid var(--border-1);border-radius:6px;padding:14px 15px;background:#fff')}>
              <div style={s('display:flex;align-items:center;gap:9px;margin-bottom:8px')}>
                <span style={s('padding:2px 9px;border-radius:9999px;background:var(--ink-100);font:600 14px/1.5 var(--font-mono);color:var(--ink-800)')}>{c.code}</span>
                <span style={s('flex:1;min-width:0;font:600 14px/1.4 var(--font-cjk);color:var(--ink-900);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{c.name}</span>
                <span style={s(`padding:1px 8px;border-radius:9999px;background:${c.rbg};font:600 11px/1.8 var(--font-cjk);color:${c.rfg}`)}>{c.relation}</span>
              </div>
              <div style={s('display:flex;align-items:center;gap:14px;margin-bottom:9px')}>
                <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{c.issuer}</span>
                <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>评论量 <span style={s('font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{c.mentions}</span></span>
                <span style={s(`font:500 13px/1.4 var(--font-mono);color:${c.dfg}`)}>{c.delta}</span>
                <span onClick={c.open} style={s('margin-left:auto;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer')}>切换到该产品 →</span>
              </div>
              <div style={s('font:400 12px/1.6 var(--font-cjk);color:var(--ink-400);margin-bottom:10px;text-wrap:pretty')}>{c.reason}</div>
              <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:10px')}>
                <div onClick={c.goPos} style={s('background:var(--positive-50);border-radius:4px;padding:10px 11px;cursor:pointer')}>
                  <div style={s('display:flex;align-items:center;gap:6px;margin-bottom:6px;color:var(--positive-700)')}>
                    <ThumbUp size="12" />
                    <span style={s('font:600 12px/1.4 var(--font-cjk)')}>用户喜欢或选择它的原因</span>
                  </div>
                  <div style={s('font:400 12px/1.7 var(--font-cjk);color:var(--ink-700);text-wrap:pretty')}>{c.pos}</div>
                  <div style={s('margin-top:6px;font:500 11px/1.4 var(--font-cjk);color:var(--csop-blue-600)')}>证据 {c.posEv} 条 →</div>
                </div>
                <div onClick={c.goNeg} style={s('background:var(--negative-50);border-radius:4px;padding:10px 11px;cursor:pointer')}>
                  <div style={s('display:flex;align-items:center;gap:6px;margin-bottom:6px;color:var(--negative-700)')}>
                    <ThumbDown size="12" />
                    <span style={s('font:600 12px/1.4 var(--font-cjk)')}>用户质疑或放弃它的原因</span>
                  </div>
                  <div style={s('font:400 12px/1.7 var(--font-cjk);color:var(--ink-700);text-wrap:pretty')}>{c.neg}</div>
                  <div style={s('margin-top:6px;font:500 11px/1.4 var(--font-cjk);color:var(--csop-blue-600)')}>证据 {c.negEv} 条 →</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {v.noComps && (
        <div style={s('padding:20px')}>
          <div style={s('padding:16px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>{v.compsEmptyText}</div>
        </div>
      )}
    </div>
  )
}
