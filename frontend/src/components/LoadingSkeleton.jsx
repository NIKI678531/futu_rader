/* Suspense fallback：骨架屏 ＋ 顶部 2px 进度条。
 *
 * ## 为什么 fallback 里要有 Shell
 *
 * 原来的 fallback 是一块 100vh 的空白（ScreenBoundary 的 LOADING）。首屏十几个端点串行
 * 往返时，用户看到的是一秒多的白屏，分不清是在加载还是挂了；切页面时导航也一起消失，
 * 想换个页都得等。把 Shell 的静态部分（品牌、两级导航）画进 fallback，导航就一直在。
 *
 * 选「fallback 里复用 Shell」而不是「把 Shell 搬到边界之外」：后者要改五个屏的 render
 * （它们各自把 `<Shell vals={v}>` 和自己的 FilterBar 套在一起，`vals` 是 renderVals() 的
 * 产物），且 Shell 的 rangeText／updated 本来就是数据 —— 搬出去仍然要在没数据的时候画灰条。
 * 前者只碰 ScreenBoundary 一处，五个屏一行不动。
 *
 * 导航项由 `navGroups(domain, sub)` 静态算出（lib/view.js，纯函数，不触网）；域与页由
 * App 按路由传进来。Shell 收到 `skeleton: true` 时不读门面 R —— /meta 这时可能还没回来。
 *
 * ## 「正在加载 x / y 个数据块」
 *
 * 数字来自 api.js 的 inflight()（本轮在途统计），随 subscribe 刷新。这行字放在带
 * `data-loading-skeleton` 的容器里：逐字比对脚本（screen-diff）按这个属性把整块排除，
 * 六态脚本等的是屏幕自己的 [data-screen-label]，骨架消失后才开始抓文本。 */
import { useEffect, useState } from 'react'
import { inflight, subscribe } from '../lib/api'
import { navGroups } from '../lib/view'
import Shell from './Shell'

export function useInflight() {
  const [st, setSt] = useState(inflight)
  useEffect(() => subscribe(() => setSt(inflight())), [])
  return st
}

const BLOCK = (h, w) => ({
  height: h, width: w, borderRadius: 8, background: 'var(--ink-100)',
  animation: 'skeleton-pulse 1.2s ease-in-out infinite',
})

export default function LoadingSkeleton({ domain, sub }) {
  const { total, done } = useInflight()
  return (
    <div data-loading-skeleton style={{ minHeight: '100vh', minWidth: 1200, background: 'var(--canvas)' }}>
      <style>{'@keyframes skeleton-pulse{0%,100%{opacity:1}50%{opacity:.55}}'}</style>
      <Shell vals={{ navGroups: navGroups(domain, sub), skeleton: true }} />
      <div style={{ padding: '22px 24px 72px' }}>
        <div role="status" style={{ font: '400 13px/1.5 var(--font-cjk)', color: 'var(--ink-500)', marginBottom: 18 }}>
          正在加载 {done} / {total} 个数据块
        </div>
        <div style={{ display: 'flex', gap: 14, marginBottom: 18 }}>
          {[0, 1, 2, 3].map((i) => <div key={i} style={BLOCK(96, '25%')} />)}
        </div>
        <div style={{ ...BLOCK(260, '100%'), marginBottom: 18 }} />
        <div style={{ display: 'flex', gap: 14 }}>
          <div style={BLOCK(220, '50%')} />
          <div style={BLOCK(220, '50%')} />
        </div>
      </div>
    </div>
  )
}

/* 顶部 2px 细进度条，挂在 App 根上（Shell 之上，z-index 高于 sticky 头）。
   有在途请求时显示并按 done/total 推进；全部落地后先补满，再用 300ms 渐隐。
   没有一个字，所以对逐字比对透明。 */
export function LoadingBar() {
  const [bar, setBar] = useState({ show: false, pct: 0, fade: false })
  useEffect(() => {
    let timer = null
    const sync = () => {
      const f = inflight()
      if (f.pending > 0) {
        if (timer) { window.clearTimeout(timer); timer = null }
        setBar({ show: true, pct: Math.max(6, Math.round((f.done / f.total) * 100)), fade: false })
      } else {
        setBar((b) => (b.show ? { show: true, pct: 100, fade: true } : b))
        if (!timer) {
          timer = window.setTimeout(() => { timer = null; setBar({ show: false, pct: 0, fade: false }) }, 450)
        }
      }
    }
    sync()
    const off = subscribe(sync)
    return () => { off(); if (timer) window.clearTimeout(timer) }
  }, [])
  if (!bar.show) return null
  return (
    <div
      aria-hidden
      data-loading-bar
      style={{
        position: 'fixed', top: 0, left: 0, height: 2, zIndex: 70, pointerEvents: 'none',
        width: bar.pct + '%', background: 'var(--csop-blue-600)',
        opacity: bar.fade ? 0 : 1,
        transition: bar.fade ? 'width 150ms ease, opacity 300ms ease 150ms' : 'width 200ms ease',
      }}
    />
  )
}
