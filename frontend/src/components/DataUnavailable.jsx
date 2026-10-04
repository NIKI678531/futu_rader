import React from 'react'
import { s } from '../lib/dc'

/**
 * Whole-screen state for a successful API response whose filtered data is not
 * publishable yet.  This is deliberately separate from ScreenBoundary: a
 * 200/null response means the service is reachable, but readiness, source
 * tickers, or the active filter generation is incomplete.
 */
export default function DataUnavailable({ screenLabel }) {
  return (
    <div
      data-screen-label={screenLabel}
      data-screen-unavailable
      role="status"
      style={s('width:100%;min-width:1200px;min-height:100vh;box-sizing:border-box;background:var(--canvas);font-family:var(--font-cjk);color:var(--ink-900);font-size:14px;padding:24px')}
    >
      <div style={s('padding:18px 20px;border:1px dashed var(--warning-600);border-radius:8px;background:var(--warning-100);color:var(--warning-700);font:500 14px/1.7 var(--font-cjk)')}>
        <div style={s('font:600 18px/1.4 var(--font-cjk);margin-bottom:4px')}>数据暂不可用</div>
        <div>父帖筛选数据尚未完成回填或当前配置版本尚未就绪。完成校验并发布 readiness 后，本页会恢复显示。</div>
      </div>
    </div>
  )
}
