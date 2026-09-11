/* 屏级边界：把「服务连不上」和「这个字段暂不可用」在 UI 上分成两件事（ADR-0005 / spec D6）。
 *
 * - **屏级错误条**（本组件）：网络不通、5xx、路由不存在。整屏数据都不可信，必须明确说
 *   服务连不上 —— 安静地显示一屏空数据会让市场团队误判「市场上没人讨论」。
 * - **字段级「暂不可用」**（不在这里）：200 响应里的 null，由各字段自己渲染。
 *
 * 同时承担 Suspense 的 fallback：api.js 的 read() 未命中时抛 Promise，React 挂起子树，
 * 数据到了再重渲染一次。屏幕代码因此可以保持同步。
 */
import React, { Suspense } from 'react'
import { clearCache } from '../lib/api'

const BAR = {
  display: 'flex',
  alignItems: 'center',
  gap: 12,
  margin: '24px',
  padding: '14px 18px',
  border: '1px solid #C0392B',
  borderRadius: 8,
  background: '#FDECEA',
  font: '500 14px/1.5 var(--font-cjk)',
  color: '#8B2B20',
}

const BTN = {
  flex: 'none',
  marginLeft: 'auto',
  padding: '6px 14px',
  border: '1px solid #C0392B',
  borderRadius: 6,
  background: '#fff',
  font: '500 13px/1.4 var(--font-cjk)',
  color: '#8B2B20',
  cursor: 'pointer',
}

/* 加载中不渲染任何文字：任何占位文案都会被逐字比对当成页面内容抓走。
   比对脚本等的是屏幕自己的 [data-screen-label]，这里保持空白最安全。 */
const LOADING = <div style={{ minHeight: '100vh', background: 'var(--canvas)' }} />

export default class ScreenBoundary extends React.Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  retry = () => {
    clearCache()
    this.setState({ error: null })
  }

  render() {
    const { error } = this.state
    if (error) {
      return (
        <div style={{ minHeight: '100vh', background: 'var(--canvas)' }}>
          {/* data-screen-error 是给 scripts/six-state.mjs 认这条错误条用的：它要断言
              「除了这条错误条，页面上不该再有别的内容」，得先能把错误条自己刨掉。 */}
          <div data-screen-error style={BAR}>
            <span>
              后端服务连不上，本页数据全部无法加载。这不是「数据暂不可用」——
              是接口没响应，页面上的数字一个都不能信。
            </span>
            <span
              style={{ flex: 'none', font: '400 12px/1.4 var(--font-mono)', opacity: 0.75 }}
            >
              {String(error && error.message ? error.message : error)}
            </span>
            <button type="button" style={BTN} onClick={this.retry}>
              重试
            </button>
          </div>
        </div>
      )
    }
    return <Suspense fallback={LOADING}>{this.props.children}</Suspense>
  }
}
