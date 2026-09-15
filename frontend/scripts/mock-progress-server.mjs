/* 处理进度侧栏与后端新增字段的本地 mock —— 只供开发与 progress-drawer-check 用，
 * **不在** `npm run dev` 的默认链路里。
 *
 * ## 为什么要一个 mock
 *
 * 侧栏读的 `/progress`／`/progress/events` 与新增字段（`heatUnknownPosts`、`stale`、
 * `evidenceCount`、`aiValidation=spot_check`）由另一位工程师在后端实现，且只有
 * `DATA_PROVIDER=sql` 接真库时才有值；本机没有真库，demo provider 下这些键根本不存在
 * （侧栏入口按钮也不渲染）。要在浏览器里看见并测到这些代码路径，只能有一份按契约形状
 * 造数的假后端。
 *
 * ## 它怎么工作
 *
 * 站在 demo 后端前面做一层透传代理：所有 `/api/v1/*` 原样转给 `--upstream`，只改三类东西
 *
 *   1. `/progress`、`/progress/events` 自己答，事件流每 `--tick` 毫秒长一条，总览的
 *      completed 随之推进 —— 增量追加、自动跟随、过滤都要有会动的数据才测得出来。
 *   2. `/meta` 的 `dataProvider` 改成 `sql`（侧栏入口的开关），并加 `aiValidation=spot_check`
 *      ＋ `aiValidationDetail`。
 *   3. 几个产品端点注入新增字段（见 INJECT），让「待更新」徽章、下限注记、证据条数这些
 *      渲染分支在页面上真的出现。
 *
 * 契约形状逐字照 PR-D 计划（另见 ProgressDrawer.jsx 模块头），后端落地时若形状有出入，
 * 改这里与 ProgressDrawer.jsx，别改测试断言迁就 mock。
 *
 * ## 用法
 *
 *   node scripts/mock-progress-server.mjs [--port 8021] [--upstream http://127.0.0.1:8017]
 *                                          [--tick 1000] [--na]
 *
 *   --na   吞吐与 ETA 下发 null（验证「暂不可用」那一支）
 *
 * 前端这边：VITE_API_BASE=http://127.0.0.1:8021/api/v1 npm run dev -- --port 5179
 */
import http from 'node:http'

const arg = (name, dflt) => {
  const i = process.argv.indexOf(name)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt
}
const PORT = Number(arg('--port', '8021'))
const UPSTREAM = arg('--upstream', 'http://127.0.0.1:8017')
const TICK = Number(arg('--tick', '1000'))
const NA = process.argv.includes('--na')

/* ── 事件流 ────────────────────────────────────────────────────────────── */

const CODES = ['3033', '3067', '2800', '3110', '7200', '3037']
const SCRIPT = [
  ['orchestrator', 'info', null, '开始一轮：scope=%s，anchor=2026-08-25'],
  ['L0', 'info', '%c', '规则预过滤：候选 128 条，近重复合并 9 条'],
  ['L1', 'info', '%c', '学生模型标注 119 条，needs_review 4 条'],
  ['L2', 'info', '%c', 'Luna 复核 4 条，改判 1 条'],
  ['L2', 'warn', '%c', '一次调用超时（12.4s），已重试'],
  ['L3', 'info', '%c', '汇总重新生成：hot_summary／summary／themes'],
  ['orchestrator', 'info', null, '产品 %c 完成，进入下一只'],
  ['L1', 'error', '%c', '模型返回非 JSON，任务标记 failed（第 2 次）'],
]

let seq = 0
const events = []
let cursor = 0
let completed = 12
const pad = (n) => String(n).padStart(2, '0')
const stamp = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`

function push(at) {
  const [stage, level, codeTpl, msgTpl] = SCRIPT[cursor % SCRIPT.length]
  const code = CODES[Math.floor(cursor / SCRIPT.length) % CODES.length]
  cursor++
  const message = msgTpl.replace('%s', code + '·d7').replace('%c', code)
  const withData = stage === 'L1' || level !== 'info'
  const e = {
    id: ++seq, ts: stamp(at), level, stage,
    code: codeTpl ? code : null, scopeId: code + '|d7', runId: 'run-mock-1', message,
    data: withData ? { attempt: level === 'error' ? 2 : 1, n: 119 - (seq % 7) } : null,
  }
  events.push(e)
  if (events.length > 2000) events.shift()
  if (stage === 'orchestrator' && msgTpl.startsWith('产品') && completed < 61) completed++
}

/* 先造 40 条历史（时间往前推），之后每 tick 一条。 */
for (let i = 40; i > 0; i--) push(new Date(Date.now() - i * TICK))
setInterval(() => push(new Date()), TICK)

function summary() {
  return {
    completed, total: 61, status: completed >= 61 ? 'complete' : 'running',
    text: `自家分析 ${completed}/61 · ${completed >= 61 ? '已完成' : '处理中'}`,
    anchor: '2026-08-25', products: {},
  }
}

function progress() {
  const remaining = (61 - completed) * 119
  return {
    summary: summary(),
    queue: {
      student: { pending: Math.max(0, 61 - completed) * 3, claimed: 2, done: completed * 119, failed: 1, dead: 0, superseded: 4 },
      llm: { pending: Math.max(0, 61 - completed), claimed: 1, done: completed * 4, failed: 0, dead: 0, superseded: 0 },
    },
    tasks: {
      post_annotation: { pending: 37, claimed: 1, done: 812, failed: 2 },
      kol_comment_opinion: {},
    },
    synthesis: { dirtyProducts: Math.max(0, 61 - completed), productsWithOutputs: completed },
    throughput: NA ? { itemsPerSec5m: null, etaSeconds: null } : { itemsPerSec5m: 2.35, etaSeconds: remaining / 2.35 },
    events: events.slice(-200),
    latestEventId: events.length ? events[events.length - 1].id : null,
  }
}

function eventsAfter(after, limit) {
  const list = events.filter((e) => e.id > after).slice(0, limit)
  return { events: list, latestEventId: events.length ? events[events.length - 1].id : null }
}

/* ── 新增字段注入（缺键即按不存在处理，所以这里加的每个键都对应页面上的一支） ── */

const INJECT = {
  '/api/v1/meta': (d) => Object.assign(d, {
    dataProvider: 'sql',
    analysisProgress: summary(),
    aiValidation: 'spot_check',
    aiValidationDetail: {
      level: 'spot_check', n: 200, date: '2026-09-12',
      relevance_accuracy: 0.935, attitude_accuracy: 0.87, attitude_macro_f1: 0.81,
      by_system: { student: { attitude_accuracy: 0.85 }, luna: { attitude_accuracy: 0.93 } },
    },
  }),
  '/api/v1/pool': (d) => {
    if (d && Array.isArray(d.list)) {
      for (const o of d.list) if (o.code === '3033') o.heatUnknownPosts = 3
    }
    if (d && d.own) d.own.heatUnknownPosts = 5
    return d
  },
  '/api/v1/hot-summaries': (d) => { if (d && d['3033']) d['3033'].stale = true; return d },
  '/api/v1/products/3033/summary': (d) => (d ? Object.assign(d, { stale: true, evidenceCount: 7 }) : d),
  '/api/v1/products/3033/themes': (d) => (d ? Object.assign(d, { stale: true }) : d),
  '/api/v1/products/3033/benchmark': (d) => (d ? Object.assign(d, { heatUnknownPosts: { current: 3, base: 1 } }) : d),
  '/api/v1/products/3033/stages': (d) => (d ? Object.assign(d, { stale: true }) : d),
  '/api/v1/products/3033/competitors': (d) => (d ? Object.assign(d, { stale: true }) : d),
}

/* ── HTTP ─────────────────────────────────────────────────────────────── */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Accept, Content-Type',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
}

function send(res, status, body, extra) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...CORS, ...(extra || {}) })
  res.end(typeof body === 'string' ? body : JSON.stringify(body))
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') { res.writeHead(204, CORS); return res.end() }
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`)

  if (url.pathname === '/api/v1/progress') return send(res, 200, { status: 'ok', data: progress() })
  if (url.pathname === '/api/v1/progress/events') {
    const after = Number(url.searchParams.get('after') || 0)
    const limit = Math.max(1, Math.min(500, Number(url.searchParams.get('limit') || 200)))
    return send(res, 200, { status: 'ok', data: eventsAfter(after, limit) })
  }

  /* 其余透传。信封里 data 为 null 时不注入 —— null 是「整块暂不可用」，不能替它编内容。 */
  let up
  try {
    up = await fetch(UPSTREAM + url.pathname + url.search, { headers: { Accept: 'application/json' } })
  } catch (err) {
    return send(res, 502, { status: 'unavailable', data: null, error: { message: `upstream ${UPSTREAM} 连不上：${err.message}` } })
  }
  const text = await up.text()
  const patch = INJECT[url.pathname]
  if (!patch || !up.ok) return send(res, up.status, text)
  let body
  try { body = JSON.parse(text) } catch { return send(res, up.status, text) }
  if (body && body.data != null) body.data = patch(body.data)
  return send(res, up.status, body)
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`mock-progress-server :${PORT} → upstream ${UPSTREAM}（tick ${TICK}ms${NA ? '，吞吐/ETA 为 null' : ''}）`)
})
