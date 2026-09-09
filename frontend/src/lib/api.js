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
 */

/* 后端地址。开发默认 8008（demo provider）；接真实库时改指 8009（mysql provider，
   ADR-0001 双服务同镜像）。不写死在代码里。 */
const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8008/api/v1'

/** url → { state: 'pending' | 'ok' | 'error', promise, body, error } */
const cache = new Map()

function load(url) {
  const entry = { state: 'pending', promise: null, body: undefined, error: undefined }
  entry.promise = fetch(BASE + url, { headers: { Accept: 'application/json' } })
    .then(async (res) => {
      /* 数据缺失一律是 200 + status 枚举（PRD §3.6）。走到非 200 就说明是传输层的事：
         路由不存在、参数不合法、后端崩了 —— 那是屏级错误，不是「这个数暂时没有」。 */
      if (!res.ok) {
        let detail = ''
        try {
          const body = await res.json()
          detail = body?.error?.message || ''
        } catch {
          /* 错误响应不是 JSON（比如反代返回的 HTML 502），detail 留空即可 */
        }
        throw new Error(`${res.status} ${url}${detail ? ' —— ' + detail : ''}`)
      }
      return res.json()
    })
    .then((body) => {
      entry.state = 'ok'
      entry.body = body
    })
    .catch((err) => {
      entry.state = 'error'
      entry.error = err
    })
  cache.set(url, entry)
  return entry
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

/** 同一端点的 status 枚举（ok / empty / unavailable / low_sample / na）。 */
export function readStatus(url) {
  const entry = cache.get(url) || load(url)
  if (entry.state === 'pending') throw entry.promise
  if (entry.state === 'error') throw entry.error
  return entry.body.status
}

/** 清空缓存，用于「重试」：下一次 read() 会重新打网络。 */
export function clearCache() {
  cache.clear()
}

/** 拼查询串。值为 undefined 的键直接不出现，不会变成字符串 "undefined"。 */
export function qs(params) {
  const parts = []
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) parts.push(`${k}=${encodeURIComponent(v)}`)
  }
  return parts.length ? '?' + parts.join('&') : ''
}
