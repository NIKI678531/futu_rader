/* 后端 API 客户端 —— 同步垫片的底座（ADR-0005）。
 *
 * ## 为什么是「同步读 + 抛 Promise」
 *
 * 五个屏幕的 renderVals() 是同步的：`R.officialPosts(key)` 直接返回数组。把它改成返回
 * Promise，就得把五个屏幕全部重写成 async 渲染 —— 那是**改设计**，不是接 API，逐字比对
 * 当场全红。
 *
 * 所以 read() 保持同步语义：命中缓存就返回，未命中就 throw 一个 Promise。React 的
 * Suspense 天生认这个：它会挂起该子树、等 Promise resolve、再重渲染一次。屏幕代码
 * 一行不用改，也不用预先声明「这一屏需要哪些端点」—— 参数从屏幕 state 来（区间、
 * 选中的官号……），静态声明根本枚举不全。
 *
 * ## 三条硬约束
 *
 * 1. **不做任何默认值兜底。** 没有 `?? 0`、`|| 0`、`|| []`。后端的 null 必须原样到达
 *    渲染层去触发「暂不可用」；在这里抹平就是铁律 2 说的那句撒谎。
 * 2. **网络错误 ≠ 数据缺失。** 连不上／5xx 会 throw Error，由 ScreenBoundary 接住渲染
 *    **屏级**错误条；字段级的「暂不可用」是 200 响应里的 null，两者在 UI 上必须不一样。
 * 3. **缓存按 URL。** 同一 URL 只打一次网络，同参数同结果 —— 逐字比对要的确定性。
 *
 * ## 为什么错误要带 kind
 *
 * ScreenBoundary 是 React 错误边界，它接住的不只是本模块抛的东西 —— 屏幕渲染时
 * 任何一个 `x.map is not a function` 都会走到同一个 catch。原来那里只有一句
 * 「后端服务连不上」，于是一个纯前端的解引用崩溃会被报成后端故障，值班的人去查
 * 后端，那里什么事都没有。
 *
 * 所以本模块抛的 Error 一律带 `kind`，让边界能区分「谁坏了」：
 *
 * | kind | 什么时候 | 边界怎么说 |
 * |---|---|---|
 * | `network` | fetch 自己 reject（DNS、连接拒绝、CORS、断网） | 后端服务连不上 |
 * | `http`    | 有响应但非 2xx（404 路由不存在、5xx 崩了） | 后端服务连不上 |
 * | `envelope`| 200 但响应体不是 `{status, data}` 信封 | 后端服务连不上 |
 * | 无 kind   | 不是本模块抛的 —— 屏幕代码自己炸了 | 页面渲染失败 |
 *
 * `envelope` 归到传输侧是有意的：信封坏掉意味着前端拿到的根本不是本项目的 API
 * （典型是反代把 HTML 登录页当 200 返回），那时候页面上的数字一个都不能信，
 * 和连不上是同一类事故。
 */

const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8008/api/v1'

/** 取数层抛出的错误。`kind` 见模块头那张表；`url` 用来在错误条上指出是哪个端点。 */
export class ApiError extends Error {
  constructor(kind, url, message) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.url = url
  }
}

/** 这个错误是不是取数层抛的。不是的话，它来自屏幕渲染 —— 两者在 UI 上必须不一样。 */
export function isApiError(err) {
  return err instanceof ApiError
}

/** url → { state: 'pending' | 'ok' | 'error', promise, body, error } */
const cache = new Map()
const listeners = new Set()
let observedRevision

function notify() {
  queueMicrotask(() => { for (const listener of listeners) listener() })
}

export function subscribe(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function inflight() {
  const total = cache.size
  const pending = Array.from(cache.values()).filter(entry => entry.state === 'pending').length
  return { total, pending, done: total - pending }
}

function load(url, { signal, cached = true } = {}) {
  const entry = { state: 'pending', promise: null, body: undefined, error: undefined }
  entry.promise = fetch(BASE + url, { headers: { Accept: 'application/json' }, signal,
    cache: cached ? 'default' : 'no-store' })
    .catch((err) => {
      if (err?.name === 'AbortError') throw err
      /* fetch 只在传输层失败时 reject：断网、DNS、连接被拒、CORS 被拦。
         它**不会**因为 4xx/5xx reject —— 那是下面 res.ok 的事。 */
      throw new ApiError('network', url, `连不上后端 ${url} —— ${err && err.message ? err.message : err}`)
    })
    .then(async (res) => {
      /* 数据缺失一律是 200 + status 枚举（PRD §3.6）。走到非 200 就说明是传输层的事：
         路由不存在、参数不合法、后端崩了 —— 那是屏级错误，不是「这个数暂时没有」。 */
      if (!res.ok) {
        let detail = ''
        try {
          const body = await res.json()
          detail = body?.error?.message
          if (typeof detail !== 'string') detail = ''
        } catch {
          /* 错误响应不是 JSON（比如反代返回的 HTML 502），detail 留空即可 */
        }
        throw new ApiError('http', url, `${res.status} ${url}${detail ? ' —— ' + detail : ''}`)
      }
      return res.json().catch(() => {
        throw new ApiError('envelope', url, `${url} 返回 200 但响应体不是 JSON`)
      })
    })
    .then((body) => {
      /* 信封校验。少了这一步，`entry.body.data` 会在命中缓存的同步路径上抛
         TypeError —— 那条路径不在任何 Promise 里，错误边界收到的是一个光秃秃的
         「Cannot read properties of undefined」，看不出是后端返回了别的东西。 */
      if (!body || typeof body !== 'object' || typeof body.status !== 'string' || !('data' in body)) {
        throw new ApiError('envelope', url, `${url} 的响应体不是 {status, data} 信封`)
      }
      entry.state = 'ok'
      entry.body = body
      if (cached && url === '/meta' && observedRevision === undefined) observedRevision = body.data?.dataRevision
    })
    .catch((err) => {
      entry.state = 'error'
      entry.error = err
    })
    .finally(() => { if (cached) notify() })
  if (cached) {
    cache.set(url, entry)
    notify()
  }
  return entry
}

export async function fetchLive(url, signal) {
  const entry = load(url, { signal, cached: false })
  await entry.promise
  if (entry.state === 'error') throw entry.error
  return entry.body.data
}

/**
 * 同步取一个端点的 data。未命中抛 Promise（交给 Suspense），出错抛 Error（交给
 * ScreenBoundary）。返回值就是响应信封里的 data —— **原样**，null 也原样返回。
 */
export function read(url) {
  const entry = cache.get(url) || load(url)
  if (entry.state === 'pending') throw entry.promise
  if (entry.state === 'error') throw entry.error
  return entry.body.data
}

/* 这里曾经有一个 `readStatus(url)`，返回信封上的 status 枚举。它**一个调用方都没有**，
   删掉是有意的，不是清理顺手：

   `backend/core/envelope.py` 的 `status_of()` 完全由 data 推出来 —— null → unavailable、
   空容器 → empty、其余 → ok。而 22 个端点没有任何一个给 `respond()` 传显式 status
   （grep `respond(` 可复核），所以 `low_sample` 与 `na` 从不出现在信封上，它们活在
   data 里面（`competitorsFor`／`complianceFor`／`stagesFor` 的 `{status, list}`）。

   也就是说信封 status 对屏幕讲不出任何 `data == null` 讲不出的事。留着它，迟早有人
   拿它再判一遍「这个字段有没有」，于是同一件事有了两个来源，而两个来源迟早会不一致。

   哪天真有端点要在信封上发 low_sample，再把它加回来 —— 那时它才有话要说。 */

/** 清空缓存，用于「重试」：下一次 read() 会重新打网络。 */
export function clearCache() {
  cache.clear()
  notify()
}

export function startLiveUpdates(onChange) {
  let stopped = false
  let busy = false
  async function check() {
    if (stopped || busy || document.visibilityState === 'hidden') return
    if (Array.from(cache.values()).some(entry => entry.state === 'pending')) return
    busy = true
    try {
      const response = await fetch(BASE + '/version', { cache: 'no-store' })
      if (!response.ok) return
      const body = await response.json()
      const revision = body.data?.dataRevision
      if (!revision || revision === 'demo') return
      if (observedRevision === undefined) observedRevision = revision
      else if (revision !== observedRevision && !stopped) {
        observedRevision = revision
        clearCache()
        onChange()
      }
    } catch {
      // Keep the last successful view during a transient version check failure.
    } finally {
      busy = false
    }
  }
  const timer = window.setInterval(check, 30000)
  window.addEventListener('focus', check)
  document.addEventListener('visibilitychange', check)
  return () => {
    stopped = true
    window.clearInterval(timer)
    window.removeEventListener('focus', check)
    document.removeEventListener('visibilitychange', check)
  }
}

/** 拼查询串。值为 undefined 的键直接不出现，不会变成字符串 "undefined"。 */
export function qs(params) {
  const parts = []
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) parts.push(`${k}=${encodeURIComponent(v)}`)
  }
  return parts.length ? '?' + parts.join('&') : ''
}
