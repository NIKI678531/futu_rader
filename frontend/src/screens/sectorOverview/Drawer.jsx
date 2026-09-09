import { s, hover } from '../../lib/dc'
import DcLink from '../../components/DcLink'

/* The positive and negative theme cards differ only in accent and hover tint. */
function ThemeCard({ t, accent, tint }) {
  return (
    <DcLink href={t.href} style={s(`display:block;border:1px solid var(--border-1);border-left:3px solid ${accent};border-radius:0 6px 6px 0;padding:10px 11px;margin-bottom:8px;background:#fff;text-decoration:none`)} className={hover(`background:${tint};text-decoration:none`)}>
      <div style={s('font:600 14px/1.45 var(--font-cjk);color:var(--ink-900);text-wrap:pretty')}>{t.title}</div>
      <div style={s('margin-top:5px;display:flex;align-items:center;gap:10px')}>
        <span style={s('font:600 13px/1.4 var(--font-mono);color:var(--ink-800)')}>{t.mentions} 条</span>
        <span style={s(`font:500 12px/1.4 var(--font-mono);color:${t.dfg}`)}>{t.delta}</span>
        <span style={s('margin-left:auto;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600)')}>证据 {t.evidence} →</span>
      </div>
    </DcLink>
  )
}

/* 快速详情抽屉 — opened by a table row, a treemap tile or a 前 3 entry. The panel itself
   is always mounted so the slide-in transform animates; only its contents are conditional.
   Every link in here hands off to 产品监控 with the code, range and anchor pre-set. */
export default function Drawer({ v }) {
  const sel = v.sel

  return (
    <>
      {v.drawerOpen && (
        <div onClick={v.closeDrawer} style={s('position:fixed;inset:0;background:rgba(14,42,82,0.55);z-index:60')}></div>
      )}

      <div style={s(`position:fixed;top:0;right:0;width:544px;height:100vh;background:#fff;border-left:1px solid var(--border-2);box-shadow:-8px 0 28px rgba(14,42,82,0.14);z-index:61;transform:translateX(${v.drawerX});transition:transform 320ms cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;overflow:hidden`)}>
        {v.drawerOpen && sel && (
          <div style={s('display:flex;flex-direction:column;height:100vh')}>

            <div style={s('flex:none;padding:18px 22px 16px;background:var(--canvas);border-bottom:1px solid var(--border-1)')}>
              <div style={s('display:flex;align-items:flex-start;justify-content:space-between;gap:14px')}>
                <div style={s('min-width:0')}>
                  <div style={s('display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap')}>
                    <span style={s('padding:3px 10px;border-radius:9999px;background:var(--csop-navy-900);font:600 14px/1.5 var(--font-mono);color:#fff')}>{sel.code}</span>
                    <span style={s('padding:3px 9px;border-radius:4px;background:#fff;border:1px solid var(--border-2);font:500 12px/1.6 var(--font-cjk);color:var(--ink-600)')}>{sel.sector}</span>
                    <span style={s('padding:3px 9px;border-radius:4px;background:#fff;border:1px solid var(--border-2);font:500 12px/1.6 var(--font-cjk);color:var(--ink-600)')}>{sel.struct}</span>
                    <span style={s(`padding:3px 9px;border-radius:4px;background:${sel.obg};font:600 12px/1.6 var(--font-cjk);color:${sel.ofg}`)}>{sel.own}</span>
                    {sel.isNew && (
                      <span style={s('padding:3px 9px;border-radius:4px;background:var(--warning-100);font:600 12px/1.6 var(--font-cjk);color:var(--warning-700)')}>新品</span>
                    )}
                  </div>
                  <div style={s('font:600 17px/1.4 var(--font-cjk);text-wrap:pretty')}>{sel.name}</div>
                  <div style={s('margin-top:5px;font:400 13px/1.5 var(--font-cjk);color:var(--ink-500)')}>发行商 {sel.issuer} · 上市 {sel.listing}</div>
                </div>
                <div onClick={v.closeDrawer} style={s('flex:none;width:28px;height:28px;border-radius:6px;border:1px solid var(--border-2);background:#fff;display:flex;align-items:center;justify-content:center;font:400 15px/1 var(--font-cjk);color:var(--ink-500);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>✕</div>
              </div>
            </div>

            <div ref={v.drawerBodyRef} style={s('flex:1;min-height:0;overflow-y:auto;padding:18px 22px 24px')}>

              <div style={s('font:600 13px/1.2 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400);margin-bottom:10px')}>当前舆情总结</div>
              <div style={s('border:1px solid var(--border-1);border-radius:6px;background:var(--canvas);padding:14px 15px;margin-bottom:20px')}>
                <div style={s('font:400 14px/1.8 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{sel.summary}</div>
                <div style={s('margin-top:11px;padding-top:10px;border-top:1px solid var(--border-1);display:flex;flex-wrap:wrap;gap:6px')}>
                  <span style={s('padding:2px 8px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 12px/1.6 var(--font-mono);color:var(--ink-600)')}>{sel.rangeText}</span>
                  <span style={s('padding:2px 8px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 12px/1.6 var(--font-mono);color:var(--ink-600)')}>更新 {v.updated}</span>
                  <span style={s('padding:2px 8px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 12px/1.6 var(--font-cjk);color:var(--ink-600)')}>有效样本 {sel.sample} 条</span>
                  <span style={s('padding:2px 8px;border-radius:9999px;background:var(--warning-100);font:600 12px/1.6 var(--font-cjk);color:var(--warning-700)')}>AI 生成 · 可追溯原文</span>
                </div>
              </div>

              <div style={s('display:flex;align-items:center;gap:10px;margin-bottom:10px')}>
                <span style={s('font:600 13px/1.2 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400)')}>分时段讨论热度</span>
                <span style={s('padding:2px 8px;border-radius:9999px;background:var(--csop-blue-50);font:500 12px/1.6 var(--font-cjk);color:var(--csop-blue-700)')}>粒度 {v.granLabel}</span>
                <div style={s('margin-left:auto;display:flex;align-items:center;gap:5px')}>
                  <span style={s('font:500 11px/1.4 var(--font-mono);color:var(--ink-400)')}>低</span>
                  <div style={s('display:flex')}>
                    {v.bucketLegend.map((g, i) => (
                      <span key={i} style={s(`width:13px;height:9px;background:${g.bg}`)}></span>
                    ))}
                  </div>
                  <span style={s('font:500 11px/1.4 var(--font-mono);color:var(--ink-400)')}>高</span>
                </div>
              </div>
              <div style={s('border:1px solid var(--border-1);border-radius:6px;background:#fff;padding:13px 14px 12px;margin-bottom:20px')}>
                <div onMouseMove={sel.cellMove} onMouseLeave={sel.cellLeave} style={s('display:flex;gap:2px;height:86px;cursor:pointer')}>
                  {sel.cells.map((c, i) => (
                    <div key={i} onClick={c.pick} style={s(`flex:1;min-width:3px;background:${c.bg};box-shadow:${c.ring};border-radius:2px`)}></div>
                  ))}
                </div>
                <div style={s('display:flex;gap:2px;margin-top:7px')}>
                  {sel.axis.map((a, i) => (
                    <div key={i} style={s('flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;gap:4px')}>
                      <span style={s(`width:1px;height:${a.tick}px;background:${a.tickC}`)}></span>
                      <span style={s(`font:${a.fw} 13px/1.3 var(--font-mono);color:${a.fg};white-space:nowrap`)}>{a.v}</span>
                    </div>
                  ))}
                </div>
                {sel.hasDayBands && (
                  <div style={s('display:flex;gap:2px;margin-top:7px;padding-top:7px;border-top:1px solid var(--border-1)')}>
                    {sel.dayBands.map((d, i) => (
                      <div key={i} style={s(`flex:${d.flex};min-width:0;text-align:center;font:600 13px/1.3 var(--font-mono);color:var(--ink-700);white-space:nowrap;overflow:hidden`)}>{d.label}</div>
                    ))}
                  </div>
                )}
                <div style={s('margin-top:11px;padding-top:10px;border-top:1px solid var(--border-1)')}>
                  <div style={s('display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:9px')}>
                    <span style={s(`padding:3px 11px;border-radius:9999px;background:${sel.readBg};font:600 13px/1.5 var(--font-mono);color:#fff;white-space:nowrap`)}>{sel.readTime}</span>
                    <span style={s('font:400 12px/1.5 var(--font-cjk);color:var(--ink-500)')}>{sel.readHint}</span>
                    {sel.hasPick && (
                      <span onClick={sel.clearPick} style={s('font:500 12px/1.5 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer')}>回到峰值时段</span>
                    )}
                  </div>
                  <div style={s('display:flex;gap:20px;flex-wrap:wrap')}>
                    {sel.readRows.map((r) => (
                      <span key={r.k} style={s('font:400 12px/1.5 var(--font-cjk);color:var(--ink-500)')}>{r.k} <span style={s(`font:600 14px/1.3 var(--font-mono);color:${r.fg}`)}>{r.v}</span></span>
                    ))}
                  </div>
                </div>
              </div>

              <div style={s('font:600 13px/1.2 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400);margin-bottom:10px')}>积极 ／ 消极观点</div>
              <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px')}>
                <div>
                  <div style={s('display:flex;align-items:center;gap:6px;margin-bottom:8px;color:var(--positive-700)')}>
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M7 10v12"></path><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"></path></svg>
                    <span style={s('font:600 14px/1.4 var(--font-cjk)')}>积极观点</span>
                    <span style={s('font:600 14px/1.4 var(--font-mono)')}>{sel.posTotal}</span>
                  </div>
                  {sel.posThemes.map((t) => (
                    <ThemeCard key={t.id} t={t} accent="var(--positive-600)" tint="var(--positive-50)" />
                  ))}
                  {sel.noPos && (
                    <div style={s('padding:12px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容</div>
                  )}
                </div>
                <div>
                  <div style={s('display:flex;align-items:center;gap:6px;margin-bottom:8px;color:var(--negative-700)')}>
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M17 14V2"></path><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"></path></svg>
                    <span style={s('font:600 14px/1.4 var(--font-cjk)')}>消极观点</span>
                    <span style={s('font:600 14px/1.4 var(--font-mono)')}>{sel.negTotal}</span>
                  </div>
                  {sel.negThemes.map((t) => (
                    <ThemeCard key={t.id} t={t} accent="var(--negative-600)" tint="var(--negative-50)" />
                  ))}
                  {sel.noNeg && (
                    <div style={s('padding:12px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容</div>
                  )}
                </div>
              </div>

              <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px')}>
                <div style={s('border:1px solid var(--border-1);border-radius:6px;padding:13px 14px')}>
                  <div style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500);margin-bottom:8px')}>讨论热度</div>
                  <div style={s('display:flex;align-items:baseline;gap:9px')}>
                    <span style={s('font:600 24px/1 var(--font-mono);color:var(--ink-900)')}>{sel.heat}</span>
                    <span style={s(`font:500 13px/1.4 var(--font-mono);color:${sel.heatDfg}`)}>{sel.heatDelta}</span>
                  </div>
                  <div style={s('margin-top:8px;font:400 12px/1.6 var(--font-cjk);color:var(--ink-500)')}>评论 {sel.comments} · 点赞 {sel.likes} · 转发 {sel.shares}<br />全市场评论量排名 第 {sel.rank} ／ {sel.rankTotal}</div>
                </div>
                <div style={s('border:1px solid var(--border-1);border-radius:6px;padding:13px 14px')}>
                  <div style={s('display:flex;align-items:center;justify-content:space-between;margin-bottom:8px')}>
                    <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>整体态度</span>
                    <span style={s(`font:500 12px/1.4 var(--font-cjk);color:${sel.netFg}`)}>{sel.net}</span>
                  </div>
                  <div style={s('display:flex;height:22px;border-radius:4px;overflow:hidden;background:var(--ink-100);margin-bottom:8px')}>
                    <div style={s(`width:${sel.posPct}%;background:var(--positive-600)`)}></div>
                    <div style={s(`width:${sel.negPct}%;background:var(--negative-600)`)}></div>
                  </div>
                  <div style={s('display:flex;align-items:center;gap:12px;font:600 13px/1.4 var(--font-mono)')}>
                    <span style={s('color:var(--positive-700)')}>赞 {sel.posTotal}</span>
                    <span style={s('color:var(--negative-700)')}>踩 {sel.negTotal}</span>
                    <span style={s('color:var(--ink-500);font-weight:500')}>中性 {sel.neuTotal}（{sel.neuShare}）</span>
                  </div>
                </div>
              </div>

              <div ref={v.riskRef} style={s('display:flex;align-items:center;gap:8px;margin-bottom:10px')}>
                <span style={s('font:600 13px/1.2 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400)')}>重点舆情</span>
                <span style={s('padding:2px 8px;border-radius:9999px;background:var(--negative-100);font:600 12px/1.5 var(--font-cjk);color:var(--negative-700)')}>需合规关注</span>
                {sel.riskOk && (
                  <span style={s('font:600 14px/1.2 var(--font-mono);color:var(--negative-700)')}>{sel.riskN}</span>
                )}
                <span style={s('margin-left:auto;padding:2px 8px;border-radius:9999px;background:var(--warning-100);font:600 11px/1.6 var(--font-cjk);color:var(--warning-700);white-space:nowrap')}>AI 识别 · 待人工确认</span>
              </div>
              {sel.riskOk && (
                <div style={s('margin-bottom:20px')}>
                  {sel.riskRows.map((r) => (
                    <DcLink key={r.id} href={r.href} style={s('display:block;border:1px solid var(--border-1);border-left:3px solid var(--negative-600);border-radius:0 6px 6px 0;padding:11px 13px;margin-bottom:8px;background:#fff;text-decoration:none;color:inherit')} className={hover('background:var(--negative-50);text-decoration:none')}>
                      <div style={s('display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-bottom:7px')}>
                        {r.tags.map((g) => (
                          <span key={g.label} style={s('padding:1px 8px;border-radius:9999px;background:var(--negative-50);border:1px solid var(--negative-100);font:600 11px/1.7 var(--font-cjk);color:var(--negative-700)')}>{g.label}</span>
                        ))}
                        <span style={s('margin-left:auto;font:500 12px/1.4 var(--font-mono);color:var(--ink-500);white-space:nowrap')}>{r.time}</span>
                      </div>
                      <div style={s('font:400 14px/1.7 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>“{r.excerpt}”</div>
                      <div style={s('margin-top:6px;font:400 12px/1.6 var(--font-cjk);color:var(--ink-500);text-wrap:pretty')}><span style={s('font-weight:600;color:var(--ink-600)')}>AI 命中依据</span>　{r.why}</div>
                      <div style={s('margin-top:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap')}>
                        <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-800)')}>{r.author}</span>
                        <span style={s(`padding:0 7px;border-radius:3px;background:${r.tbg};font:600 11px/1.7 var(--font-cjk);color:${r.tfg}`)}>{r.type}</span>
                        <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>来源：{r.source}</span>
                        <span style={s('margin-left:auto;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600)')}>查看原文证据 →</span>
                      </div>
                    </DcLink>
                  ))}
                  <div style={s('display:flex;align-items:center;gap:12px;flex-wrap:wrap')}>
                    <DcLink href={sel.riskHref} style={s('display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>查看全部原文证据 →</DcLink>
                    {sel.riskHasMore && (
                      <span style={s('font:400 12px/1.5 var(--font-cjk);color:var(--ink-500)')}>{sel.riskMore}</span>
                    )}
                    <span style={s('font:400 12px/1.5 var(--font-cjk);color:var(--ink-400)')}>只识别风险信号并保留原文，不判定言论真伪或产品是否违规</span>
                  </div>
                </div>
              )}
              {sel.riskEmpty && (
                <div style={s('padding:14px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.6 var(--font-cjk);color:var(--ink-500);margin-bottom:20px')}>暂无相关内容 — 区间内未识别到需合规关注的言论；普通消极观点与负面问题见相邻模块。</div>
              )}
              {sel.riskUnavailable && (
                <div style={s('padding:14px;border:1px dashed var(--warning-600);border-radius:6px;background:var(--warning-100);font:400 14px/1.6 var(--font-cjk);color:var(--warning-700);margin-bottom:20px')}>数据暂不可用 — 风险识别结果尚未核验或数据源未提供，不展示推测内容。</div>
              )}
              {sel.riskNa && (
                <div style={s('display:flex;align-items:center;gap:10px;padding:12px 14px;border:1px solid var(--border-1);border-radius:6px;background:#fff;margin-bottom:20px')}>
                  <span style={s('flex:none;min-width:26px;padding:2px 8px;border-radius:9999px;background:var(--ink-100);font:600 13px/1.4 var(--font-mono);color:var(--ink-700);text-align:center')}>—</span>
                  <span style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-500);text-wrap:pretty')}>同业产品不纳入重点舆情（需合规关注）识别范围，字段不适用。</span>
                </div>
              )}

              <div style={s('font:600 13px/1.2 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400);margin-bottom:10px')}>负面舆情摘要</div>
              {sel.hasNegCats && (
                <div style={s('margin-bottom:20px')}>
                  {sel.negCats.map((c) => (
                    <div key={c.label} style={s('border:1px solid var(--border-1);border-radius:6px;padding:11px 13px;margin-bottom:8px;background:#fff')}>
                      <div style={s('display:flex;align-items:center;gap:8px;margin-bottom:6px')}>
                        <span style={s('font:600 14px/1.4 var(--font-cjk);color:var(--ink-900)')}>{c.label}</span>
                        <span style={s(`padding:1px 7px;border-radius:9999px;background:${c.lbg};font:600 11px/1.7 var(--font-cjk);color:${c.lfg}`)}>{c.life}</span>
                        <span style={s('margin-left:auto;font:600 13px/1.4 var(--font-mono);color:var(--negative-700)')}>{c.mentions} 条 · {c.share}</span>
                      </div>
                      <div style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-600);text-wrap:pretty')}>{c.summary}</div>
                    </div>
                  ))}
                </div>
              )}
              {sel.noNegCats && (
                <div style={s('padding:14px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.6 var(--font-cjk);color:var(--ink-500);margin-bottom:20px')}>暂无相关内容 — 区间内没有可归类为需关注负面舆情的内容。</div>
              )}

              <div style={s('font:600 13px/1.2 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400);margin-bottom:10px')}>关联竞品观点 · 前 3</div>
              {sel.hasComps && (
                <div>
                  {sel.comps.map((c) => (
                    <div key={c.code} style={s('border:1px solid var(--border-1);border-radius:6px;padding:12px 13px;margin-bottom:8px;background:#fff')}>
                      <div style={s('display:flex;align-items:center;gap:8px;margin-bottom:8px')}>
                        <span style={s('padding:2px 8px;border-radius:9999px;background:var(--ink-100);font:600 13px/1.5 var(--font-mono);color:var(--ink-700)')}>{c.code}</span>
                        <span style={s('flex:1;min-width:0;font:500 14px/1.4 var(--font-cjk);color:var(--ink-900);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{c.name}</span>
                        <span style={s(`padding:1px 7px;border-radius:9999px;background:${c.rbg};font:600 11px/1.7 var(--font-cjk);color:${c.rfg}`)}>{c.relation}</span>
                      </div>
                      <div style={s('display:flex;align-items:center;gap:12px;margin-bottom:8px;font:500 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>
                        <span>{c.issuer}</span>
                        <span>评论量 <span style={s('font:600 13px/1.4 var(--font-mono);color:var(--ink-800)')}>{c.mentions}</span></span>
                        <span style={s(`font:500 12px/1.4 var(--font-mono);color:${c.dfg}`)}>{c.delta}</span>
                      </div>
                      <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:9px')}>
                        <div style={s('background:var(--positive-50);border-radius:4px;padding:8px 9px')}>
                          <div style={s('font:600 12px/1.4 var(--font-cjk);color:var(--positive-700);margin-bottom:4px')}>用户认可</div>
                          <div style={s('font:400 12px/1.6 var(--font-cjk);color:var(--ink-700);text-wrap:pretty')}>{c.pos}</div>
                        </div>
                        <div style={s('background:var(--negative-50);border-radius:4px;padding:8px 9px')}>
                          <div style={s('font:600 12px/1.4 var(--font-cjk);color:var(--negative-700);margin-bottom:4px')}>用户质疑</div>
                          <div style={s('font:400 12px/1.6 var(--font-cjk);color:var(--ink-700);text-wrap:pretty')}>{c.neg}</div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {sel.noComps && (
                <div style={s('padding:14px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.6 var(--font-cjk);color:var(--ink-500)')}>{sel.compsEmptyText}</div>
              )}
            </div>

            <div style={s('flex:none;padding:14px 22px;border-top:1px solid var(--border-1);background:var(--canvas)')}>
              <DcLink href={sel.monitorHref} style={s('display:flex;align-items:center;justify-content:center;padding:11px;border-radius:6px;background:var(--csop-blue-600);color:#fff;font:600 14px/1.4 var(--font-cjk);text-decoration:none;transition:background 120ms cubic-bezier(.4,0,.2,1)')} className={hover('background:var(--csop-blue-800);text-decoration:none')}>进入完整产品监控</DcLink>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
