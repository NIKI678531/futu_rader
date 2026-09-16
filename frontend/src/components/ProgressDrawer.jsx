/* 处理进度侧栏 —— 标注管线的终端风格只读视图。
 *
 * ## 为什么是侧栏而不是横幅
 *
 * 原来 Shell 底下常显一行 `analysisProgress.text`（「自家分析完成 12/61 …」）。一行字放不下
 * 队列、吞吐、ETA 与事件流，而这些正是值班的人想看的；把它们摊在页面上又会挤占本来就
 * 只有 1200px 宽度预算的五个屏。所以默认收起、点开才渲染：**关着的时候 DOM 里没有一行
 * 日志**，也没有一次 `/progress` 请求。
 *
 * ## 数据从哪儿来
 *
 * 两个活端点，直接 `fetchLive()`，**不走 read() 缓存**（api.js 的缓存按 URL 键，活数据进去
 * 就是永远读第一次的快照）：
 *
 *   打开时          GET /progress                        总览 ＋ 最近 200 条事件
 *   之后每 3 s      GET /progress/events?after=<id>      增量追加
 *   每 15 s         GET /progress                        总览刷新
 *
 * 页面隐藏时停、回到前台时立刻补一次（与 api.js `startLiveUpdates` 同一套 visibility 思路）；
 * 关闭时停并 abort 在途请求。demo provider 下 `/progress` 的 data 是 null —— 入口按钮本来
 * 只在 `R.DATA_PROVIDER === 'sql'` 时渲染，这里再兜一层「暂不可用」是为了 mock 与联调。
 *
 * ## 状态点
 *
 * 入口按钮旁那颗点在没打开过侧栏时读 `/meta.analysisProgress.status`（已缓存，不多打一次
 * 网络）；打开过之后读最近一次 `/progress` 快照。运行中蓝、完成绿、配置错误／租约丢失／
 * 源已变更红；两边都没有的时候灰 —— 不知道就是不知道，不猜一个颜色。
 *
 * ## 这里没有任何按钮能启停作业
 *
 * 工作台只读（CLAUDE.md）。侧栏能做的只有看、过滤、展开一条事件的 data。
 */
import { useEffect, useRef, useState } from 'react'
import { fetchLive, qs } from '../lib/api'
import R from '../data/radar'

/* ── 模块级 store：入口按钮（在 Shell 里，随屏幕重挂）与侧栏（在 App 根上，跨路由存活）
   共享开关与最近一次快照。用 React context 要把 Provider 塞进五个屏幕的 Shell 调用点，
   这里一个 Set 就够了。 */
const store = { open: false, snapshot: null, listeners: new Set() }
function emit() { for (const fn of store.listeners) fn() }
export function openProgress() { if (!store.open) { store.open = true; emit() } }
export function closeProgress() { if (store.open) { store.open = false; emit() } }
function useStore() {
  const [, force] = useState(0)
  useEffect(() => {
    const fn = () => force((x) => x + 1)
    store.listeners.add(fn)
    return () => { store.listeners.delete(fn) }
  }, [])
  return store
}

const STAGE_NAME = { L0: '规则与近重复', L1: '学生模型', L2: 'Luna', L3: '汇总', orchestrator: '编排' }
const STAGES = ['L0', 'L1', 'L2', 'L3', 'orchestrator']
const STATUS_NAME = {
  running: '运行中', complete: '已完成',
  configuration_error: '配置错误', lease_lost: '租约丢失', source_changed: '源已变更',
}
const ERROR_STATUSES = ['configuration_error', 'lease_lost', 'source_changed']

/* 终端配色。参考截图：深底、等宽、绿字；warn 黄、error 红。 */
const T = {
  bg: '#0b0f0a', panel: '#101610', border: '#1f2b1f',
  fg: '#8ff0a4', dim: '#5f9a6b', warn: '#e3b341', err: '#ff7b72', head: '#c9f7d2', blue: '#79b8ff',
}
const DOT = { running: '#2F80ED', complete: '#1F8A5B', error: '#C53030', unknown: '#9AA4B2' }

/** 状态点该是什么颜色。summary 优先；没有 summary 时看队列有没有活。 */
export function statusOf(summary, queue) {
  if (summary && ERROR_STATUSES.indexOf(summary.status) >= 0) return 'error'
  if (summary && summary.status === 'running') return 'running'
  if (summary && summary.status === 'complete') return 'complete'
  if (queue) {
    const q = [queue.student, queue.llm].filter(Boolean)
    if (q.some((x) => x.pending > 0 || x.claimed > 0)) return 'running'
    if (q.some((x) => x.dead > 0 || x.failed > 0)) return 'error'
    if (q.some((x) => x.done > 0)) return 'complete'
  }
  return 'unknown'
}

/* 短徽章位的缺失文案（PRD §3.6 逐字），侧栏里全是短位。 */
const na = (v) => (v == null ? '暂不可用' : String(v))

function etaText(sec) {
  if (sec == null) return '暂不可用'
  if (sec < 60) return Math.round(sec) + ' 秒'
  const min = sec / 60
  return (min >= 100 ? Math.round(min) : (Math.round(min * 10) / 10)) + ' 分钟'
}

function rateText(v) {
  return v == null ? '暂不可用' : (Math.round(v * 100) / 100).toFixed(2) + ' 条/秒'
}

/* `{status: 计数}` 一类字典摊成一行「pending 3 · done 10」。空字典是「暂无内容」——
   任务表建了但一条都没有；null 才是「暂不可用」。 */
function countsText(dict) {
  if (dict == null) return '暂不可用'
  const keys = Object.keys(dict)
  if (!keys.length) return '暂无内容'
  return keys.map((k) => k + ' ' + na(dict[k])).join(' · ')
}

function queueText(q) {
  if (q == null) return '暂不可用'
  const extra = []
  if (q.claimed > 0) extra.push('claimed ' + q.claimed)
  if (q.failed > 0) extra.push('failed ' + q.failed)
  if (q.dead > 0) extra.push('dead ' + q.dead)
  if (q.superseded > 0) extra.push('superseded ' + q.superseded)
  return 'pending ' + na(q.pending) + ' / done ' + na(q.done) + (extra.length ? '（' + extra.join(' · ') + '）' : '')
}

/* ── 入口按钮（放在 Shell 右上角「最近更新」右侧） ─────────────────────── */
export function ProgressButton() {
  const st = useStore()
  const meta = R.ANALYSIS_PROGRESS
  const snap = st.snapshot
  const state = snap ? statusOf(snap.summary, snap.queue) : statusOf(meta, null)
  const title = snap && snap.summary && snap.summary.text ? snap.summary.text
    : (meta && meta.text ? meta.text : '查看标注管线的处理进度')
  return (
    <button
      type="button"
      data-progress-button
      onClick={openProgress}
      title={title}
      aria-expanded={st.open}
      style={{
        display: 'flex', alignItems: 'center', gap: 7, padding: '4px 10px', border: '1px solid var(--border-2)',
        borderRadius: 6, background: '#fff', cursor: 'pointer', font: '500 12px/1.4 var(--font-cjk)', color: 'var(--ink-700)',
      }}
    >
      <span data-progress-dot={state} style={{ width: 8, height: 8, borderRadius: 9999, background: DOT[state], flex: 'none' }} />
      处理进度
    </button>
  )
}

/* ── 侧栏本体（App 根上渲染一次） ────────────────────────────────────────── */
export default function ProgressDrawer() {
  const st = useStore()
  if (!st.open) return null
  return <DrawerBody />
}

function DrawerBody() {
  const [snapshot, setSnapshot] = useState(store.snapshot)
  const [events, setEvents] = useState([])
  const [error, setError] = useState(null)
  const [stage, setStage] = useState('all')
  const [code, setCode] = useState('')
  const [follow, setFollow] = useState(true)
  const [expanded, setExpanded] = useState({})
  const latestRef = useRef(null)
  const logRef = useRef(null)

  /* 轮询。全部句柄收在这个 effect 里，关闭（组件卸载）时一把清干净：定时器、visibility
     监听、在途请求。写成「停了就一个都不剩」是 real-data-check 里那条「关闭后无 /progress
     请求」断言的前提。 */
  useEffect(() => {
    let stopped = false
    let timerEvents = null
    let timerSummary = null
    const ctl = new AbortController()

    const merge = (list) => {
      if (!Array.isArray(list) || !list.length) return
      setEvents((prev) => {
        const seen = new Set(prev.map((e) => e.id))
        const add = list.filter((e) => e && !seen.has(e.id))
        if (!add.length) return prev
        const next = prev.concat(add)
        /* 只留最近 2000 条：侧栏可能开一整个下午，事件流不能无上限地长。 */
        return next.length > 2000 ? next.slice(next.length - 2000) : next
      })
      const maxId = list.reduce((m, e) => (e && e.id != null && (m == null || e.id > m) ? e.id : m), null)
      if (maxId != null && (latestRef.current == null || maxId > latestRef.current)) latestRef.current = maxId
    }

    const takeSnapshot = (data) => {
      const snap = data == null ? null : {
        summary: data.summary, queue: data.queue, tasks: data.tasks,
        synthesis: data.synthesis, throughput: data.throughput,
      }
      setSnapshot(snap)
      store.snapshot = snap
      if (data && data.latestEventId != null && (latestRef.current == null || data.latestEventId > latestRef.current)) {
        latestRef.current = data.latestEventId
      }
      emit()
    }

    const full = async () => {
      if (stopped) return
      try {
        const data = await fetchLive('/progress', ctl.signal)
        if (stopped) return
        takeSnapshot(data)
        merge(data && data.events)
        setError(null)
      } catch (err) {
        if (!stopped) setError(err)
      }
    }
    const incremental = async () => {
      if (stopped) return
      try {
        const after = latestRef.current == null ? 0 : latestRef.current
        const data = await fetchLive('/progress/events' + qs({ after, limit: 200 }), ctl.signal)
        if (stopped) return
        if (data) {
          merge(data.events)
          if (data.latestEventId != null && (latestRef.current == null || data.latestEventId > latestRef.current)) {
            latestRef.current = data.latestEventId
          }
        }
        setError(null)
      } catch (err) {
        if (!stopped) setError(err)
      }
    }

    const start = () => {
      if (timerEvents || stopped) return
      full()
      timerEvents = window.setInterval(incremental, 3000)
      timerSummary = window.setInterval(full, 15000)
    }
    const pause = () => {
      if (timerEvents) window.clearInterval(timerEvents)
      if (timerSummary) window.clearInterval(timerSummary)
      timerEvents = null
      timerSummary = null
    }
    const onVisibility = () => { if (document.visibilityState === 'hidden') pause(); else start() }
    const onKey = (e) => { if (e.key === 'Escape') closeProgress() }

    if (document.visibilityState !== 'hidden') start()
    document.addEventListener('visibilitychange', onVisibility)
    document.addEventListener('keydown', onKey)
    return () => {
      stopped = true
      pause()
      ctl.abort()
      document.removeEventListener('visibilitychange', onVisibility)
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  /* 自动跟随到底。用户往上翻了就停（follow=false），出现「回到最新」。 */
  useEffect(() => {
    const el = logRef.current
    if (el && follow) el.scrollTop = el.scrollHeight
  }, [events, follow, stage, code])

  const onScroll = () => {
    const el = logRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    if (atBottom !== follow) setFollow(atBottom)
  }

  const codeQ = code.trim()
  const shown = events.filter((e) => {
    if (stage !== 'all' && e.stage !== stage) return false
    if (codeQ) {
      const c = e.code == null ? '' : String(e.code)
      const sc = e.scopeId == null ? '' : String(e.scopeId)
      if (c.indexOf(codeQ) < 0 && sc.indexOf(codeQ) < 0) return false
    }
    return true
  })

  const summary = snapshot ? snapshot.summary : null
  const state = snapshot ? statusOf(snapshot.summary, snapshot.queue) : 'unknown'
  const mono = 'var(--font-mono), Consolas, monospace'

  const row = (k, v, fg) => (
    <div key={k} style={{ display: 'flex', gap: 10, font: `400 12px/1.7 ${mono}` }}>
      <span style={{ color: T.dim, flex: 'none', minWidth: 132 }}>{k}</span>
      <span style={{ color: fg || T.fg, wordBreak: 'break-all' }}>{v}</span>
    </div>
  )

  return (
    <div data-progress-drawer style={{ position: 'fixed', inset: 0, zIndex: 80, fontFamily: 'var(--font-cjk)' }}>
      <div
        data-progress-overlay
        onClick={closeProgress}
        style={{ position: 'absolute', inset: 0, background: 'rgba(8,12,20,0.45)' }}
      />
      <aside
        role="dialog"
        aria-label="处理进度"
        style={{
          position: 'absolute', top: 0, right: 0, bottom: 0, width: 520, maxWidth: '92vw',
          background: T.bg, color: T.fg, boxShadow: '-12px 0 40px rgba(0,0,0,0.45)',
          display: 'flex', flexDirection: 'column', animation: 'progress-drawer-in 180ms cubic-bezier(.4,0,.2,1)',
        }}
      >
        <style>{'@keyframes progress-drawer-in{from{transform:translateX(40px);opacity:0}to{transform:none;opacity:1}}'}</style>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: `1px solid ${T.border}` }}>
          <span style={{ width: 9, height: 9, borderRadius: 9999, background: DOT[state], flex: 'none' }} />
          <span style={{ font: `600 14px/1.3 ${mono}`, color: T.head }}>处理进度</span>
          <span style={{ font: `400 12px/1.3 ${mono}`, color: T.dim }}>
            {summary ? (STATUS_NAME[summary.status] || summary.status) : (snapshot ? '状态暂不可用' : '连接中…')}
          </span>
          <button
            type="button"
            onClick={closeProgress}
            aria-label="关闭"
            style={{ marginLeft: 'auto', border: `1px solid ${T.border}`, background: 'transparent', color: T.dim, borderRadius: 4, padding: '2px 8px', cursor: 'pointer', font: `500 12px/1.4 ${mono}` }}
          >
            关闭 Esc
          </button>
        </div>

        <section data-progress-overview style={{ padding: '12px 16px', borderBottom: `1px solid ${T.border}`, background: T.panel }}>
          <div style={{ font: `600 11px/1.4 ${mono}`, letterSpacing: '0.12em', color: T.dim, marginBottom: 6 }}>总览</div>
          {snapshot == null && error == null && row('状态', '正在读取 /progress …', T.dim)}
          {snapshot == null && error != null && row('状态', '暂不可用 —— ' + String(error.message || error), T.err)}
          {snapshot != null && [
            /* 主语跟 /meta.analysisProgress.scope 走：full_own 默认 61 只自家，`--all` 是 120 只全池；
               旧记录没有 scope ⇒ 自家。 */
            row(summary && summary.scope === 'all' ? '全池分析完成' : '自家分析完成', summary ? na(summary.completed) + ' / ' + na(summary.total) + (summary.anchor ? '　锚点 ' + summary.anchor : '') : '暂不可用'),
            summary && summary.text ? row('说明', summary.text) : null,
            row('L1 学生队列', queueText(snapshot.queue ? snapshot.queue.student : null)),
            row('L2 Luna 队列', queueText(snapshot.queue ? snapshot.queue.llm : null)),
            row('帖子标注任务', countsText(snapshot.tasks ? snapshot.tasks.post_annotation : null)),
            row('KOL 评论观点任务', countsText(snapshot.tasks ? snapshot.tasks.kol_comment_opinion : null)),
            row('待更新汇总产品', snapshot.synthesis ? na(snapshot.synthesis.dirtyProducts) + ' 只（已有产出 ' + na(snapshot.synthesis.productsWithOutputs) + ' 只）' : '暂不可用'),
            row('当前吞吐（5 分钟）', rateText(snapshot.throughput ? snapshot.throughput.itemsPerSec5m : null)),
            row('预计剩余', etaText(snapshot.throughput ? snapshot.throughput.etaSeconds : null)),
            error != null ? row('最近一次刷新', '失败 —— ' + String(error.message || error), T.err) : null,
          ]}
        </section>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 16px', borderBottom: `1px solid ${T.border}` }}>
          <span style={{ font: `600 11px/1.4 ${mono}`, letterSpacing: '0.12em', color: T.dim }}>实时日志</span>
          <select
            data-progress-stage
            value={stage}
            onChange={(e) => setStage(e.target.value)}
            aria-label="按阶段过滤"
            style={{ marginLeft: 'auto', background: T.panel, color: T.fg, border: `1px solid ${T.border}`, borderRadius: 4, font: `400 12px/1.4 ${mono}`, padding: '2px 6px' }}
          >
            <option value="all">全部阶段</option>
            {STAGES.map((k) => <option key={k} value={k}>{k} {STAGE_NAME[k]}</option>)}
          </select>
          <input
            data-progress-code
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="产品代码"
            aria-label="按产品代码过滤"
            style={{ width: 88, background: T.panel, color: T.fg, border: `1px solid ${T.border}`, borderRadius: 4, font: `400 12px/1.4 ${mono}`, padding: '2px 6px' }}
          />
          <span style={{ font: `400 11px/1.4 ${mono}`, color: T.dim }}>{shown.length} / {events.length}</span>
        </div>

        <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
          <div
            ref={logRef}
            onScroll={onScroll}
            data-progress-log
            style={{ position: 'absolute', inset: 0, overflow: 'auto', padding: '10px 16px 16px', font: `400 12px/1.65 ${mono}` }}
          >
            {shown.length === 0 && (
              <div style={{ color: T.dim }}>
                {events.length === 0 ? (error ? '事件流暂不可用' : '暂无事件') : '当前过滤条件下暂无事件'}
              </div>
            )}
            {shown.map((e) => {
              const fg = e.level === 'error' ? T.err : (e.level === 'warn' ? T.warn : T.fg)
              const hasData = e.data != null && (typeof e.data !== 'object' || Object.keys(e.data).length > 0)
              const open = !!expanded[e.id]
              const ts = typeof e.ts === 'string' && e.ts.length >= 19 ? e.ts.slice(11, 19) : na(e.ts)
              return (
                <div key={e.id} data-progress-event data-level={e.level} data-stage={e.stage} style={{ color: fg, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  <span title={e.ts} style={{ color: T.dim }}>[{ts}]</span>
                  {' '}<span style={{ color: e.level === 'info' ? T.blue : fg }}>[{STAGE_NAME[e.stage] || na(e.stage)}]</span>
                  {e.code != null ? ' [' + e.code + ']' : ''}
                  {' '}{e.message}
                  {hasData && (
                    <button
                      type="button"
                      onClick={() => setExpanded((m) => Object.assign({}, m, { [e.id]: !open }))}
                      style={{ marginLeft: 8, border: `1px solid ${T.border}`, background: 'transparent', color: T.dim, borderRadius: 3, padding: '0 5px', cursor: 'pointer', font: `400 11px/1.4 ${mono}` }}
                    >
                      {open ? '收起 data' : '展开 data'}
                    </button>
                  )}
                  {hasData && open && (
                    <pre style={{ margin: '4px 0 6px 18px', padding: '6px 8px', background: T.panel, border: `1px solid ${T.border}`, borderRadius: 4, color: T.head, font: `400 11px/1.5 ${mono}`, overflow: 'auto' }}>
                      {JSON.stringify(e.data, null, 2)}
                    </pre>
                  )}
                </div>
              )
            })}
          </div>
          {!follow && (
            <button
              type="button"
              data-progress-follow
              onClick={() => { setFollow(true); const el = logRef.current; if (el) el.scrollTop = el.scrollHeight }}
              style={{ position: 'absolute', right: 16, bottom: 12, border: `1px solid ${T.fg}`, background: T.bg, color: T.fg, borderRadius: 999, padding: '4px 12px', cursor: 'pointer', font: `500 12px/1.4 ${mono}`, boxShadow: '0 4px 14px rgba(0,0,0,0.4)' }}
            >
              回到最新 ↓
            </button>
          )}
        </div>
      </aside>
    </div>
  )
}
