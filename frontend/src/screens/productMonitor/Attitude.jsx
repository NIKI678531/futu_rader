import { s, hover } from '../../lib/dc'

/* 赞／踩图标：设计源里在这张卡内出现四次，尺寸不同，路径相同 */
export function ThumbUp({ size }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={s('flex:none')}><path d="M7 10v12"></path><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"></path></svg>
  )
}
export function ThumbDown({ size }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={s('flex:none')}><path d="M17 14V2"></path><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"></path></svg>
  )
}

/* 观点主题卡 — 积极与消极两栏共用同一块标记，只有强调色与「占积极／占消极」不同 */
function ThemeCard({ t, accent, tint, shareLabel }) {
  return (
    <div onClick={t.go} style={s(`border:1px solid var(--border-1);border-left:3px solid ${accent};border-radius:0 6px 6px 0;padding:12px 14px;margin-bottom:9px;background:#fff;cursor:pointer`)} className={hover(`background:${tint}`)}>
      <div style={s('display:flex;align-items:baseline;gap:9px;margin-bottom:6px')}>
        <span style={s('font:600 14px/1.4 var(--font-cjk);color:var(--ink-900);text-wrap:pretty')}>{t.title}</span>
        <span style={s('margin-left:auto;flex:none;font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{t.mentions}</span>
        <span style={s(`flex:none;font:500 13px/1.4 var(--font-mono);color:${t.dfg}`)}>{t.delta}</span>
      </div>
      {t.hasMeta && (
        <div style={s('display:flex;align-items:center;gap:6px;margin-bottom:7px')}>
          <span style={s(`padding:2px 8px;border-radius:9999px;background:${t.sevBg};font:600 11px/1.7 var(--font-cjk);color:${t.sevFg}`)}>关注程度 {t.severity}</span>
          <span style={s(`padding:2px 8px;border-radius:9999px;background:${t.lifeBg};font:600 11px/1.7 var(--font-cjk);color:${t.lifeFg}`)}>{t.life}</span>
          <span style={s('font:400 11px/1.7 var(--font-mono);color:var(--ink-500)')}>{t.first} → {t.last}</span>
        </div>
      )}
      <div style={s('font:400 13px/1.65 var(--font-cjk);color:var(--ink-600);text-wrap:pretty;margin-bottom:9px')}>{t.summary}</div>
      <div style={s('display:flex;gap:2px;height:16px;margin-bottom:4px')}>
        {t.cells.map((b, i) => (
          <div key={i} title={b.title} style={s(`flex:1;min-width:3px;background:${b.bg};border-radius:2px`)}></div>
        ))}
      </div>
      <div style={s('display:flex;align-items:baseline;gap:8px;margin-bottom:8px;font:500 11px/1.4 var(--font-mono);color:var(--ink-400)')}>
        <span style={s('flex:none')}>{t.spanFrom}</span>
        <span style={s('flex:1;min-width:0;text-align:center;color:var(--ink-600);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{t.spanPeak}</span>
        <span style={s('flex:none')}>{t.spanTo}</span>
      </div>
      <div style={s('display:flex;align-items:center;gap:12px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>
        <span>{shareLabel} {t.share}</span>
        <span>AI 置信度 {t.confidence}</span>
        <span style={s('margin-left:auto;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600)')}>证据 {t.evidence} 条 →</span>
      </div>
    </div>
  )
}

/* 积极 ／ 消极整体态度 + 两栏观点排行 */
export default function Attitude({ v }) {
  return (
    <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px')}>
      <div style={s('display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 20px;border-bottom:1px solid var(--border-1)')}>
        <div style={s('display:flex;align-items:baseline;gap:11px')}>
          <span style={s('font:600 18px/1.3 var(--font-cjk)')}>积极 ／ 消极整体态度</span>
          <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>赞／踩为 AI 对内容中产品态度的分类，不是 Futu 平台的点赞／点踩行为</span>
          {/* 主题聚类顶层 `stale === true`（标注已更新、汇总待重新生成）才渲染；demo 下没有这个键。 */}
          {v.themesStale && <span title={v.staleTitle} style={s('padding:2px 8px;border-radius:9999px;background:var(--warning-100);font:600 12px/1.6 var(--font-cjk);color:var(--warning-700);white-space:nowrap')}>AI 生成 · 待更新</span>}
        </div>
        <span style={s(`font:600 14px/1.4 var(--font-cjk);color:${v.netFg}`)}>{v.netText}</span>
      </div>

      <div style={s('padding:18px 20px 4px')}>
        <div style={s('position:relative;display:grid;grid-template-columns:1fr 1fr;gap:18px')}>
          <div style={s('position:absolute;left:0;right:0;top:44px;height:30px;display:flex;border-radius:5px;overflow:hidden;background:var(--ink-100)')}>
            <div style={s(`width:${v.posBarPct}%;background:var(--positive-600);transition:width 320ms cubic-bezier(.4,0,.2,1);display:flex;align-items:center;justify-content:flex-end;padding-right:10px;box-sizing:border-box`)}><span style={s('font:600 12px/1 var(--font-mono);color:#fff;white-space:nowrap')}>{v.posShare}</span></div>
            <div style={s(`width:${v.negBarPct}%;background:var(--negative-600);transition:width 320ms cubic-bezier(.4,0,.2,1);display:flex;align-items:center;justify-content:flex-start;padding-left:10px;box-sizing:border-box`)}><span style={s('font:600 12px/1 var(--font-mono);color:#fff;white-space:nowrap')}>{v.negShare}</span></div>
          </div>
          <span style={s('position:absolute;left:50%;top:38px;height:42px;width:2px;background:var(--ink-900);transform:translateX(-1px);border-radius:1px')}></span>
          <div style={s('min-width:0;display:flex;flex-direction:column;gap:9px')}>
            <div style={s('display:flex;align-items:baseline;gap:10px')}>
              <span style={s('display:flex;align-items:center;gap:7px;color:var(--positive-700)')}>
                <ThumbUp size="16" />
                <span style={s('font:600 14px/1.4 var(--font-cjk)')}>积极 ／ 赞</span>
              </span>
              <span style={s('font:600 26px/1 var(--font-mono);color:var(--positive-700)')}>{v.posTotal}</span>
              <span style={s('font:500 14px/1.4 var(--font-mono);color:var(--ink-600)')}>{v.posShare}</span>
              <span style={s(`font:500 13px/1.4 var(--font-mono);color:${v.posDfg}`)}>{v.posDelta}</span>
            </div>
            <div style={s('height:30px')}></div>
            <div style={s('padding:11px 13px;border:1px solid var(--border-1);border-radius:6px;background:var(--positive-50)')}>
              <div style={s('font:600 12px/1.4 var(--font-cjk);color:var(--positive-700);margin-bottom:6px')}>AI 观点总结</div>
              <div style={s('font:400 13px/1.8 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{v.posLead}{v.posSummary}</div>
            </div>
          </div>
          <div style={s('min-width:0;display:flex;flex-direction:column;gap:9px')}>
            <div style={s('display:flex;align-items:baseline;gap:10px')}>
              <span style={s('display:flex;align-items:center;gap:7px;color:var(--negative-700)')}>
                <ThumbDown size="16" />
                <span style={s('font:600 14px/1.4 var(--font-cjk)')}>消极 ／ 踩</span>
              </span>
              <span style={s('font:600 26px/1 var(--font-mono);color:var(--negative-700)')}>{v.negTotal}</span>
              <span style={s('font:500 14px/1.4 var(--font-mono);color:var(--ink-600)')}>{v.negShare}</span>
              <span style={s(`font:500 13px/1.4 var(--font-mono);color:${v.negDfg}`)}>{v.negDelta}</span>
            </div>
            <div style={s('height:30px')}></div>
            <div style={s('padding:11px 13px;border:1px solid var(--border-1);border-radius:6px;background:var(--negative-50)')}>
              <div style={s('font:600 12px/1.4 var(--font-cjk);color:var(--negative-700);margin-bottom:6px')}>AI 观点总结</div>
              <div style={s('font:400 13px/1.8 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{v.negLead}{v.negSummary}</div>
            </div>
          </div>
        </div>
        <div style={s('margin-top:16px;padding:11px 14px;background:var(--canvas);border:1px solid var(--border-1);border-radius:6px;display:flex;align-items:center;gap:16px;flex-wrap:wrap')}>
          <span style={s('font:500 14px/1.4 var(--font-cjk);color:var(--ink-600)')}>中性内容 <span style={s('font:600 15px/1.4 var(--font-mono);color:var(--ink-900)')}>{v.neuTotal}</span> 条，占全部有效内容 <span style={s('font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{v.neuShare}</span></span>
          <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>中性内容不进入赞踩比；赞踩比分母为积极＋消极＝{v.validN} 条</span>
          {v.lowSample && (
            <span style={s('padding:3px 10px;border-radius:9999px;background:var(--warning-100);font:600 13px/1.5 var(--font-cjk);color:var(--warning-700)')}>样本不足 · 低于 {v.threshold} 条阈值，不输出倾向结论</span>
          )}
          {/* 与「样本不足」互斥：那一枚说的是「数过了，样本太少」，这一枚说的是
              「三态标注还没有」。徽章位用短文案「暂不可用」（PRD §3.6）。 */}
          {v.attNa && (
            <span style={s('padding:3px 10px;border-radius:9999px;background:var(--ink-100);font:600 13px/1.5 var(--font-cjk);color:var(--ink-600)')}>暂不可用 · 三态标注尚未生成</span>
          )}
        </div>
      </div>

      <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:18px;padding:18px 20px 20px')}>
        <div>
          <div style={s('display:flex;align-items:center;justify-content:space-between;margin-bottom:10px')}>
            <div style={s('display:flex;align-items:center;gap:7px;color:var(--positive-700)')}>
              <ThumbUp size="15" />
              <span style={s('font:600 14px/1.4 var(--font-cjk)')}>积极观点排行</span>
            </div>
            {v.posMoreVisible && (
              <div onClick={v.posToggle} style={s('font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer')}>{v.posMoreLabel}</div>
            )}
          </div>
          {v.posThemes.map((t) => (
            <ThemeCard key={t.title} t={t} accent="var(--positive-600)" tint="var(--positive-50)" shareLabel="占积极" />
          ))}
          {v.noPos && (
            <div style={s('padding:14px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.6 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容 — 区间内没有可归类的积极观点。</div>
          )}
          {v.posUnavailable && (
            <div style={s('padding:16px;border:1px dashed var(--warning-600);border-radius:6px;background:var(--warning-100);font:400 14px/1.7 var(--font-cjk);color:var(--warning-700)')}>数据暂不可用 — 积极观点聚类尚未生成或数据源未提供，本区域不展示推测内容。</div>
          )}
        </div>

        <div>
          <div style={s('display:flex;align-items:center;justify-content:space-between;margin-bottom:10px')}>
            <div style={s('display:flex;align-items:center;gap:7px;color:var(--negative-700)')}>
              <ThumbDown size="15" />
              <span style={s('font:600 14px/1.4 var(--font-cjk)')}>消极观点排行</span>
              <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>其中 {v.negActionable} 条需跟进</span>
            </div>
            {v.negMoreVisible && (
              <div onClick={v.negToggle} style={s('font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer')}>{v.negMoreLabel}</div>
            )}
          </div>
          {v.negThemes.map((t) => (
            <ThemeCard key={t.title} t={t} accent="var(--negative-600)" tint="var(--negative-50)" shareLabel="占消极" />
          ))}
          {v.noNeg && (
            <div style={s('padding:14px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.6 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容 — 区间内没有可归类的消极观点。</div>
          )}
          {v.negUnavailable && (
            <div style={s('padding:16px;border:1px dashed var(--warning-600);border-radius:6px;background:var(--warning-100);font:400 14px/1.7 var(--font-cjk);color:var(--warning-700)')}>数据暂不可用 — 消极观点聚类尚未生成或数据源未提供，本区域不展示推测内容。</div>
          )}
        </div>
      </div>
    </div>
  )
}
