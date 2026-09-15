import { s, hover } from '../lib/dc'
import DcLink from './DcLink'
import R from '../data/radar'
import { ProgressButton } from './ProgressDrawer'

/* Rows 1 and 2 of the sticky header — brand, domain tabs, sub-nav, range/updated.
   Byte-identical in all four .dc.html screens, so it lives here once. The third row
   (the filter bar) differs per screen and is passed in as children.

   两处对设计源的有意补充，都只在 `R.DATA_PROVIDER === 'sql'` 时出现，demo 下逐字不变：
   - 「最近更新」右侧的「处理进度」入口按钮（见 ProgressDrawer.jsx）。它取代了原来
     Shell 底下那条常显的 `analysisProgress.text` 横幅：一行字放不下队列／吞吐／事件流，
     常显又挤占五个屏的高度预算。
   - `vals.skeleton` 为 true 时是 Suspense fallback 里的骨架态：导航照常可点，数据范围与
     最近更新两个值位画成灰条，**不读门面 R 的任何东西**（此时 /meta 可能还没回来，读了
     就会在 fallback 里再抛一次 Promise，页面退回整屏空白）。 */
export default function Shell({ vals, children }) {
  const { navGroups = [], rangeText, updated, skeleton } = vals
  const showProgress = !skeleton && R.DATA_PROVIDER === 'sql'
  const bar = (w) => <span aria-hidden style={s(`display:inline-block;width:${w}px;height:12px;border-radius:3px;background:var(--ink-100);vertical-align:middle`)}></span>

  return (
    <div style={s('position:sticky;top:0;z-index:40;box-shadow:0 1px 0 var(--border-2)')}>
      <div style={s('display:flex;align-items:center;height:54px;padding:0 24px;background:#fff;border-bottom:1px solid var(--border-1)')}>
        <DcLink
          href="sector-overview.dc.html"
          style={s('display:flex;align-items:center;gap:10px;flex:none;color:var(--csop-navy-900);text-decoration:none')}
          className={hover('text-decoration:none')}
        >
          <span style={s('font:600 22px/1.2 var(--font-cjk);letter-spacing:0.02em;white-space:nowrap')}>舆情雷达</span>
        </DcLink>
        <div style={s('width:1px;height:20px;background:var(--border-2);margin:0 22px 0 20px;flex:none')}></div>
        <div style={s('display:flex;align-items:center;gap:4px;padding:3px;border-radius:9px;background:var(--ink-100)')}>
          {navGroups.map((g) => (
            <DcLink
              key={g.name}
              href={g.href}
              style={s(`display:flex;align-items:center;padding:6px 16px;border-radius:7px;font:${g.fw} 14px/1.2 var(--font-cjk);color:${g.fg};background:${g.bg};box-shadow:${g.sh};text-decoration:none;white-space:nowrap;transition:all 120ms cubic-bezier(.4,0,.2,1)`)}
              className={hover('color:var(--csop-navy-900);text-decoration:none')}
            >
              {g.name}
            </DcLink>
          ))}
        </div>
        <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:18px;white-space:nowrap')}>
          <div style={s('flex:none;display:flex;align-items:baseline;gap:7px;white-space:nowrap')}>
            <span style={s('font:600 12px/1.4 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400)')}>数据范围</span>
            <span style={s('font:600 14px/1.4 var(--font-mono);color:var(--ink-800)')}>{skeleton ? bar(150) : rangeText}</span>
            <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>HKT 自然日</span>
          </div>
          <div style={s('width:1px;height:15px;background:var(--border-2)')}></div>
          <div style={s('flex:none;display:flex;align-items:baseline;gap:7px;white-space:nowrap')}>
            <span style={s('font:600 12px/1.4 var(--font-cjk);letter-spacing:0.14em;color:var(--ink-400)')}>最近更新</span>
            <span style={s('font:600 14px/1.4 var(--font-mono);color:var(--ink-800)')}>{skeleton ? bar(120) : updated}</span>
          </div>
          {showProgress && <ProgressButton />}
        </div>
      </div>
      <div style={s('display:flex;align-items:stretch;height:42px;padding:0 24px;background:#fff;border-bottom:1px solid var(--border-1)')}>
        {navGroups.map((g) => (
          <div key={g.name} style={s(`display:${g.subDisplay};align-items:stretch;gap:4px`)}>
            {g.items.map((it) => (
              <DcLink
                key={it.name}
                href={it.href}
                style={s(`display:flex;align-items:center;padding:0 14px;font:${it.fw} 15px/1.2 var(--font-cjk);color:${it.fg};box-shadow:inset 0 -2px 0 ${it.bc};text-decoration:none;white-space:nowrap;transition:color 120ms cubic-bezier(.4,0,.2,1)`)}
                className={hover('color:var(--csop-blue-700);text-decoration:none')}
              >
                {it.name}
              </DcLink>
            ))}
          </div>
        ))}
      </div>
      {children}
    </div>
  )
}
