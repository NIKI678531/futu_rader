/* 给类组件屏幕注入 React 18 的 transition。
 *
 * ## 为什么需要它
 *
 * 屏幕切区间／切产品／开抽屉时，新的 read() 会在 render 中途抛 Promise。普通的 setState
 * 让 Suspense 退回 fallback —— 旧内容整屏消失，换成骨架屏；数据回来再整屏回来。设计源
 * 里那个 520ms 的假 loading 定时器（`loading: true` → 正文透明度 0.45 → 520ms 后恢复）
 * 就是在演这种「旧内容留着、变淡」的效果，但它与真实取数毫无关系：数据早到了它还在淡，
 * 数据晚到了它已经恢复。
 *
 * 把 setState 包进 `startTransition`，React 就会在数据回来之前**保留旧内容**、不退 fallback；
 * `isPending` 则是真实的「还在等」。于是屏幕里的 `loading`／`bodyOpacity` 改由它驱动，
 * 假定时器删掉。
 *
 * ## 为什么是 HOC 而不是改写成函数组件
 *
 * 五个屏都是设计源逐字移植的类组件，`useTransition` 是 hook。一个小的函数式外壳拿到
 * `[isPending, startTransition]` 再以 props 传进去，屏幕只改 go() 里那一行 setState 和
 * renderVals() 里 loading 的来源，其余一行不动。
 *
 * ## 受控输入不能进 transition
 *
 * 输入框每敲一个字都 setState；若这次 setState 走 transition，React 会在事件结束后把
 * DOM 值回退到旧的 prop，直到 transition 提交 —— 敲的字会「消失」再出现。所以 `split()`
 * 把 `q`／`pq` 这类输入键拆出来紧急提交，只有会触发新取数的键走 transition。 */
import { useTransition } from 'react'

export default function withTransition(Screen) {
  function WithTransition(props) {
    const [isPending, startTransition] = useTransition()
    return <Screen {...props} isPending={isPending} startTransition={startTransition} />
  }
  WithTransition.displayName = `withTransition(${Screen.displayName || Screen.name || 'Screen'})`
  return WithTransition
}

/* 把 patch 按键拆成两半：`urgentKeys` 里的紧急提交，其余进 transition。
   两半都可能为空对象；调用方按需各 setState 一次。 */
export function split(patch, urgentKeys) {
  const urgent = {}
  const deferred = {}
  for (const k of Object.keys(patch)) {
    if (urgentKeys.indexOf(k) >= 0) urgent[k] = patch[k]
    else deferred[k] = patch[k]
  }
  return { urgent, deferred, hasUrgent: Object.keys(urgent).length > 0 }
}
