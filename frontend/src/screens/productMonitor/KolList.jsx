import { s, hover } from '../../lib/dc'
import DcLink from '../../components/DcLink'

/* 产品相关 KOL — 与 KOL 页面同源的相关原帖，并补充已确认的评论观点。 */
export default function KolList({ v }) {
  return (
    <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:14px')}>
      <div style={s('display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--border-1)')}>
        <span style={s('font:600 18px/1.3 var(--font-cjk)')}>产品相关 KOL</span>
        <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>与 KOL 页面同源的 {v.code} 相关帖子，并补充评论观点 · 按相关内容数降序</span>
        <DcLink href={v.kolActivityHref} style={s('margin-left:auto;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none;white-space:nowrap')} className={hover('text-decoration:underline')}>查看 KOL 页面相关帖子 →</DcLink>
        <span style={s('padding:3px 10px;border-radius:9999px;background:var(--csop-blue-50);font:500 12px/1.6 var(--font-cjk);color:var(--csop-blue-700);white-space:nowrap')}>已识别 KOL 范围：{v.kolScope}</span>
      </div>
      {v.kolHas && (
        <>
          <div style={s('display:flex;align-items:center;gap:8px;height:38px;padding:0 20px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1);font:600 13px/1.4 var(--font-cjk);color:var(--ink-700)')}>
            <span style={s('flex:none;width:210px')}>KOL</span>
            <span style={s('flex:none;width:92px;text-align:right')}>相关内容</span>
            <span style={s('flex:none;width:126px;padding-left:14px;box-sizing:border-box')}>最近提及</span>
            <span style={s('flex:none;width:92px')}>主要态度</span>
            <span style={s('flex:1;min-width:0')}>代表内容摘录</span>
            <span style={s('flex:none;width:96px;text-align:right')}>原文证据</span>
          </div>
          {v.kolRows.map((k) => (
            <div key={k.name} onClick={k.go} style={s('display:flex;align-items:center;gap:8px;min-height:56px;padding:9px 20px;border-bottom:1px solid var(--ink-100);cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)')} className={hover('background:var(--csop-blue-50)')}>
              <div style={s('flex:none;width:210px;min-width:0')}>
                <div style={s('display:flex;align-items:center;gap:7px')}>
                  <span style={s('min-width:0;font:600 14px/1.4 var(--font-cjk);color:var(--ink-900);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{k.name}</span>
                  <span style={s('flex:none;padding:0 7px;border-radius:3px;background:var(--csop-blue-50);font:600 11px/1.7 var(--font-cjk);color:var(--csop-blue-700)')}>{k.type}</span>
                  <DcLink href={k.detailHref} onClick={k.stop} style={s('flex:none;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none;white-space:nowrap')} className={hover('text-decoration:underline')}>详情 →</DcLink>
                </div>
                <div style={s('margin-top:2px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{k.tags}</div>
              </div>
              <span style={s('flex:none;width:92px;text-align:right;font:600 16px/1.3 var(--font-mono);color:var(--ink-900)')}>{k.count}</span>
              <span style={s('flex:none;width:126px;padding-left:14px;box-sizing:border-box;font:500 13px/1.4 var(--font-mono);color:var(--ink-600)')}>{k.last}</span>
              <span style={s('flex:none;width:92px')}><span style={s(`padding:2px 9px;border-radius:9999px;background:${k.abg};font:600 12px/1.6 var(--font-cjk);color:${k.afg}`)}>{k.att}</span></span>
              <span style={s('flex:1;min-width:0;font:400 13px/1.6 var(--font-cjk);color:var(--ink-700);overflow:hidden;text-overflow:ellipsis;white-space:nowrap')}>{k.excerpt}</span>
              <span style={s('flex:none;width:96px;text-align:right;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600)')}>证据 {k.evidence} 条 →</span>
            </div>
          ))}
          <div style={s('display:flex;align-items:flex-start;gap:14px;padding:11px 20px 13px')}>
            {v.kolMoreVisible && (
              <span onClick={v.kolToggle} style={s('flex:none;font:500 13px/1.6 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer;white-space:nowrap')}>{v.kolMoreLabel}</span>
            )}
            <span style={s('font:400 12px/1.6 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>KOL 身份来自已核验名单与账号映射，不按活跃度或粉丝数推断；相关原帖与 KOL 页面使用同一来源，模型确认的评论观点作为补充证据；同一条内容只计一次。有效态度少于 3 条不输出主要态度。</span>
          </div>
        </>
      )}
      {v.kolEmpty && (
        <div style={s('padding:20px')}><div style={s('padding:16px;border:1px dashed var(--border-2);border-radius:6px;background:var(--canvas);font:400 14px/1.7 var(--font-cjk);color:var(--ink-500)')}>暂无相关内容 — 当前区间未发现已识别 KOL 的相关帖子或评论观点。 <DcLink href={v.kolActivityHref} style={s('font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none;white-space:nowrap')} className={hover('text-decoration:underline')}>查看该产品的 KOL 相关帖子 →</DcLink></div></div>
      )}
      {v.kolUnavailable && (
        <div style={s('padding:20px')}><div style={s('padding:16px;border:1px dashed var(--warning-600);border-radius:6px;background:var(--warning-100);font:400 14px/1.7 var(--font-cjk);color:var(--warning-700)')}>数据暂不可用 — KOL 身份名单或账号映射尚未核验。</div></div>
      )}
    </div>
  )
}
