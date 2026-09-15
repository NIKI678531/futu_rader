/* 屏级边界：把「服务连不上」和「这个字段暂不可用」在 UI 上分成两件事（ADR-0005 / spec D6）。
 *
 * - **屏级错误条**（本组件）：网络不通、5xx、路由不存在。整屏数据都不可信，必须明确说
 *   服务连不上 —— 安静地显示一屏空数据会让市场团队误判「市场上没人讨论」。
 * - **字段级「暂不可用」**（不在这里）：200 响应里的 null，由各字段自己渲染。
 *
 * 同时承担 Suspense 的 fallback：api.js 的 read() 未命中时抛 Promise，React 挂起子树，
 * 数据到了再重渲染一次。屏幕代码因此可以保持同步。
 *
 * ## 为什么要分两种错误条
 *
 * React 错误边界接住的不只是取数层抛的东西：屏幕渲染里任何一句
 * `topics.map is not a function` 都落进同一个 catch。这里原来只有一句「后端服务连不上」,
 * 于是一个纯前端的解引用崩溃会被报成后端故障 —— 值班的人去查后端，而后端好端端的，
 * 直到有人想起来打开浏览器控制台。诊断时间全花在错误的系统上。
 *
 * 所以按 `api.js` 给的 `kind` 分支：取数层抛的（network/http/envelope）说「后端连不上」，
 * 其余一律说「页面渲染失败」，并把 message 与组件栈摆在页面上 —— 不指望值班的人先去开
 * DevTools 才知道该找谁。
 *
 * **渲染失败这一支没有「重试」按钮。** 后端返回的是确定的 200，再取一次还是同一份
 * payload，重渲染必然在同一行再炸一次；给一个点了不会好的按钮，只会让人点上五遍才
 * 开始怀疑别的地方。这一类只能改代码。
 */
import React, { Suspense } from 'react'
import { clearCache, isApiError } from '../lib/api'
import LoadingSkeleton from './LoadingSkeleton'

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

/* 加载中的 fallback 是骨架屏（LoadingSkeleton）而不是空白：Shell 的导航照常可见，正文
   几块灰条加一行「正在加载 x / y 个数据块」。文字全在 [data-loading-skeleton] 容器里，
   逐字比对脚本按这个属性排除；六态脚本等的是屏幕自己的 [data-screen-label]。
   `nav` 由 App 按路由传入（{domain, sub}），骨架用它静态算出导航高亮。 */

const DETAIL = { flex: 'none', font: '400 12px/1.4 var(--font-mono)', opacity: 0.75 }

/* 组件栈占一整行：它比 message 长得多，塞进 flex 行里会把说明文字挤成一竖条。 */
const STACK = {
  margin: '0 24px 24px',
  padding: '10px 14px',
  border: '1px solid #E3C9C4',
  borderRadius: 6,
  background: '#fff',
  font: '400 11px/1.6 var(--font-mono)',
  color: '#6B3A32',
  whiteSpace: 'pre-wrap',
  maxHeight: 240,
  overflow: 'auto',
}

function describe(error) {
  if (error && error.message) return String(error.message)
  return String(error)
}

export default class ScreenBoundary extends React.Component {
  state = { error: null, stack: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  /* 组件栈只在 componentDidCatch 里拿得到，getDerivedStateFromError 没有第二个参数。
     渲染崩溃时它是唯一能指出「哪个屏、哪个组件」的东西，比 message 有用得多。 */
  componentDidCatch(error, info) {
    if (!isApiError(error)) {
      /* 控制台那份留着：页面上只放得下前几层，完整栈还是控制台好读。 */
      console.error('[ScreenBoundary] 屏幕渲染崩溃，这不是后端故障：', error, info)
      this.setState({ stack: info && info.componentStack ? info.componentStack : null })
    }
  }

  retry = () => {
    clearCache()
    this.setState({ error: null, stack: null })
  }

  render() {
    const { error, stack } = this.state
    if (!error) {
      const nav = this.props.nav || {}
      return (
        <Suspense fallback={<LoadingSkeleton domain={nav.domain} sub={nav.sub} />}>
          {this.props.children}
        </Suspense>
      )
    }

    /* 取数层抛的才是「后端连不上」。别的都是屏幕自己炸了 —— 报错方向必须不一样。 */
    if (isApiError(error)) {
      return (
        <div style={{ minHeight: '100vh', background: 'var(--canvas)' }}>
          {/* data-screen-error 是给 scripts/six-state.mjs 认这条错误条用的：它要断言
              「除了这条错误条，页面上不该再有别的内容」，得先能把错误条自己刨掉。 */}
          <div data-screen-error style={BAR}>
            <span>
              后端服务连不上，本页数据全部无法加载。这不是「数据暂不可用」——
              是接口没响应，页面上的数字一个都不能信。
            </span>
            <span style={DETAIL}>{describe(error)}</span>
            <button type="button" style={BTN} onClick={this.retry}>
              重试
            </button>
          </div>
        </div>
      )
    }

    /* data-screen-crash 与 data-screen-error 是两个属性，不是一个。六态脚本断言的是
       「断网时出现 data-screen-error」；要是渲染崩溃也挂同一个属性，一次前端崩溃就能
       把那条断言变绿 —— 红线会替一个真 bug 作证。 */
    return (
      <div style={{ minHeight: '100vh', background: 'var(--canvas)' }}>
        <div data-screen-crash style={BAR}>
          <span>
            页面渲染失败，本页内容无法显示。接口是通的 —— 后端返回了数据，是前端在渲染
            这份数据时崩了。请把下面这段连同当前地址反馈给开发；重试没有用，同一份数据会
            在同一行再崩一次。
          </span>
          <span style={DETAIL}>{describe(error)}</span>
        </div>
        {stack ? <pre style={STACK}>{stack.trim()}</pre> : null}
      </div>
    )
  }
}
