import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { loadAdminPipeline, runAdminAnalysis, runAdminFillAll } from '../lib/adminApi'
import '../styles/admin.css'

const RANGES = ['d1', 'd2', 'd7', 'd14', 'd30', 'mtd']
const STATUS_TEXT = {
  queued: '排队中', running: '运行中', succeeded: '成功', partial: '部分完成',
  failed: '失败', complete: '已完成', waiting: '待处理', not_started: '尚未开始',
  incomplete: '仍有缺失', ok: '正常', fail: '需处理', info: '说明',
}
const SOURCE_TEXT = { admin: '手动', schedule: '定时' }

function text(value, fallback = '—') {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function numberText(value) {
  return typeof value === 'number' ? value.toLocaleString('zh-HK') : '—'
}

function dateTime(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('zh-HK', { hour12: false })
}

function StatusPill({ value }) {
  const normalized = value || 'not_started'
  return <span className={`admin-pill is-${normalized}`}>{STATUS_TEXT[normalized] || normalized}</span>
}

function Metric({ label, value }) {
  return <div className="admin-metric"><dt>{label}</dt><dd>{text(value)}</dd></div>
}

function CodeList({ codes, total }) {
  if (!codes?.length) return null
  const more = Math.max(0, (total || codes.length) - codes.length)
  return <small className="admin-code-list">{codes.join('、')}{more ? ` 等 ${more + codes.length} 只` : ''}</small>
}

function scopeText(ownTopLimit) {
  return typeof ownTopLimit === 'number'
    ? `自家按区间评论量前 ${ownTopLimit} 只 + 全部竞品`
    : '全部有效产品（自家在前）'
}

export default function Admin() {
  const [selectedRange, setSelectedRange] = useState('d1')
  const [pipeline, setPipeline] = useState(null)
  const [loading, setLoading] = useState(false)
  const [submittingRange, setSubmittingRange] = useState(null)
  const [commentsExpanded, setCommentsExpanded] = useState(false)
  const [expandedCoverage, setExpandedCoverage] = useState(null)
  const [error, setError] = useState('')
  const requestSerial = useRef(0)

  const refresh = useCallback(async ({ quiet = false } = {}) => {
    const serial = ++requestSerial.current
    if (!quiet) setLoading(true)
    try {
      const next = await loadAdminPipeline(selectedRange)
      if (serial === requestSerial.current) {
        setPipeline(next)
        setError('')
      }
    } catch (reason) {
      if (serial === requestSerial.current) setError(reason.message || '数据库状态读取失败')
    } finally {
      if (!quiet && serial === requestSerial.current) setLoading(false)
    }
  }, [selectedRange])

  const hasActiveRun = pipeline?.ranges?.some(item => item.activeRun)
  useEffect(() => {
    let cancelled = false
    let timer
    async function poll(initial = false) {
      await refresh({ quiet: !initial })
      if (!cancelled) timer = window.setTimeout(() => poll(), hasActiveRun ? 5000 : 30000)
    }
    poll(true)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [refresh, hasActiveRun])

  async function submit(range, request) {
    setSubmittingRange(range)
    setError('')
    if (range !== 'all') setSelectedRange(range)
    try {
      await request()
      const serial = ++requestSerial.current
      const next = await loadAdminPipeline(range === 'all' ? selectedRange : range)
      if (serial === requestSerial.current) setPipeline(next)
    } catch (reason) {
      setError(reason.message || 'AI 分析任务提交失败')
    } finally {
      setLoading(false)
      setSubmittingRange(null)
    }
  }

  const trigger = range => submit(range, () => runAdminAnalysis(range))
  const fillAll = () => submit('all', () => runAdminFillAll())

  const layers = pipeline?.layers || {}
  const ranges = pipeline?.ranges || RANGES.map(key => ({ key, activeRun: null }))
  const activeByRange = Object.fromEntries(ranges.map(item => [item.key, item.activeRun]))
  const coverage = pipeline?.coverage || []
  const checks = pipeline?.checks || []
  const comments = [...(pipeline?.comments || [])].sort((a, b) => {
    const aCount = typeof a.pageCommentCount === 'number' ? a.pageCommentCount : -1
    const bCount = typeof b.pageCommentCount === 'number' ? b.pageCommentCount : -1
    return bCount - aCount || String(a.code).localeCompare(String(b.code))
  })
  const recentRuns = pipeline?.recentRuns || []
  const commentDisplayLimit = pipeline?.commentDisplayLimit || 10
  const visibleComments = commentsExpanded ? comments : comments.slice(0, commentDisplayLimit)
  const hiddenCommentCount = Math.max(0, comments.length - visibleComments.length)
  const progress = layers.progress || {}
  const workerOnline = pipeline?.worker?.online === true
  const workerReady = workerOnline && pipeline?.worker?.ready === true
  const activeRangeRun = activeByRange[selectedRange]
  const progressIsCurrent = !activeRangeRun || progress.status === 'running'
  const progressPercent = progressIsCurrent
    ? Math.max(0, Math.min(100, Number(progress.percent) || 0))
    : 0
  const completedProducts = progressIsCurrent ? (progress.completedProducts || 0) : 0
  const progressStatus = activeRangeRun?.status || progress.status || 'not_started'
  const missingTotal = coverage.reduce((sum, row) => sum + (row.missing || 0), 0)
  const staleTotal = coverage.reduce((sum, row) => sum + (row.stale || 0), 0)

  function runLabel(key) {
    const active = activeByRange[key]
    if (active) return `${key} · ${STATUS_TEXT[active.status] || active.status}`
    if (submittingRange === key) return '提交中…'
    if (!workerOnline) return `${key} · worker 离线`
    if (!workerReady) return `${key} · 配置未就绪`
    return `运行 ${key}`
  }

  return <main className="admin-shell">
    <header className="admin-topbar">
      <div><p className="admin-eyebrow">FUTU RADAR / ADMIN</p><h1>数据链路管理</h1></div>
      <div className="admin-actions">
        <button onClick={fillAll} disabled={!workerReady || submittingRange === 'all'}>
          {submittingRange === 'all' ? '提交中…' : '一键补齐全部'}
        </button>
        <Link to="/product">查看产品页</Link>
      </div>
    </header>

    {error && <div className="admin-alert is-error">{error}</div>}
    {pipeline && !workerOnline && <div className="admin-alert is-warning">
      AI worker 未运行；为避免任务永久排队，分析按钮已停用。请启动 worker 后刷新本页。
      {pipeline.worker?.lastSeenAt && <small>上次心跳：{dateTime(pipeline.worker.lastSeenAt)}</small>}
    </div>}
    {pipeline?.worker && workerOnline && !workerReady && <div className="admin-alert is-warning">
      <span>AI worker 尚未就绪：{pipeline.worker.error || '运行前检查未通过'}</span>
      <small>心跳：{dateTime(pipeline.worker.lastSeenAt)}</small>
    </div>}

    <section className="admin-card admin-checks">
      <header className="admin-section-head">
        <div><p className="admin-eyebrow">PIPELINE / CHECKS</p><h2>链路检查</h2></div>
        <p>每日 08:00 自动执行：同步最新数据 → 行情 → 六个区间的 Layer A 与 Layer B；也可以在下方手动补齐。</p>
      </header>
      <div className="admin-check-list">
        {checks.length ? checks.map(item => <div className={`admin-check is-${item.status}`} key={item.id} data-check={item.id}>
          <StatusPill value={item.status} />
          <div><strong>{item.label}</strong><small>{text(item.detail)}</small></div>
        </div>) : <p className="admin-empty">{loading ? '正在读取数据库…' : '暂无检查结果。'}</p>}
      </div>
    </section>

    <section className="admin-card admin-coverage">
      <header className="admin-section-head">
        <div><p className="admin-eyebrow">PAGES / COMPLETENESS</p><h2>页面数据完整度</h2></div>
        <div className="admin-section-side">
          <p>覆盖范围：{scopeText(pipeline?.ownTopLimit)}。待更新的结论照常显示在页面上并标「待更新」；缺失的才是页面上的空位。</p>
        </div>
      </header>
      <div className="admin-context-line">
        <span>数据 anchor：{text(pipeline?.anchor)}</span>
        <span>缺失 {numberText(missingTotal)} 个产品区间 · 待更新 {numberText(staleTotal)} 个</span>
        <span>上次每日任务：{dateTime(pipeline?.daily?.startedAt)}</span>
      </div>
      <div className="admin-table-wrap">
        <table className="admin-comment-table">
          <thead><tr>
            <th>区间</th><th>应生成产品</th><th>已完成</th><th>待更新</th><th>缺失</th><th>状态</th><th>按区间补齐</th>
          </tr></thead>
          <tbody>{coverage.length ? coverage.map(row => <tr key={row.key}>
            <td className="admin-mono">
              {row.key}
              {row.missingCodes?.length > 0 && <button
                className="is-secondary admin-inline-toggle"
                onClick={() => setExpandedCoverage(value => value === row.key ? null : row.key)}
                aria-expanded={expandedCoverage === row.key}
              >{expandedCoverage === row.key ? '收起' : '缺哪些'}</button>}
              {expandedCoverage === row.key && <CodeList codes={row.missingCodes} total={row.missing} />}
            </td>
            <td>{numberText(row.expected)}</td><td>{numberText(row.complete)}</td>
            <td>{numberText(row.stale)}</td><td>{numberText(row.missing)}</td>
            <td><StatusPill value={row.status} /></td>
            <td><button
              className="is-secondary"
              onClick={() => trigger(row.key)}
              disabled={!workerReady || Boolean(activeByRange[row.key]) || submittingRange === row.key}
            >{runLabel(row.key)}</button></td>
          </tr>) : <tr><td colSpan="7" className="admin-empty">{loading ? '正在读取数据库…' : '暂无完整度数据。'}</td></tr>}</tbody>
        </table>
      </div>
    </section>

    <section className="admin-card admin-comments">
      <header className="admin-section-head">
        <div><p className="admin-eyebrow">DATABASE / COMMENTS</p><h2>评论进入</h2></div>
        <div className="admin-section-side">
          <p>只统计父帖 tag 明确包含对应 ETF 的评论；刷新不会触发采集、同步或 AI。</p>
          {comments.length > commentDisplayLimit && <button
            className="is-secondary admin-fold-toggle"
            onClick={() => setCommentsExpanded(value => !value)}
            aria-expanded={commentsExpanded}
          >{commentsExpanded ? '收起，只看页面评论量前 10' : `展开其余 ${hiddenCommentCount} 只 ETF`}</button>}
        </div>
      </header>
      <div className="admin-toolbar">
        <div className="admin-range-tabs" aria-label="评论区间">
          {RANGES.map(range => <button
            key={range}
            className={selectedRange === range ? 'is-active' : 'is-secondary'}
            aria-pressed={selectedRange === range}
            onClick={() => { setSelectedRange(range); setCommentsExpanded(false) }}
          >{range}</button>)}
        </div>
        <button className="is-secondary" onClick={() => refresh()} disabled={loading}>
          {loading ? '刷新中…' : '刷新评论状态'}
        </button>
      </div>
      <div className="admin-context-line">
        <span>数据 anchor：{text(pipeline?.anchor)}</span>
        <span>按页面评论量从大到小排序 · 默认显示前 {Math.min(commentDisplayLimit, comments.length)} / {comments.length} 只</span>
        <span>读取时间：{dateTime(pipeline?.generatedAt)}</span>
      </div>
      <div className="admin-table-wrap">
        <table className="admin-comment-table">
          <thead><tr>
            <th>排名</th><th>ETF</th><th>筛选前平台</th><th>筛后平台</th><th>已抓正文</th>
            <th>规则入围</th><th>AI 完成</th><th>相关评论</th><th>页面评论量</th>
          </tr></thead>
          <tbody>{comments.length ? visibleComments.map(row => <tr key={row.code}>
            <td className="admin-mono">{row.rank}</td>
            <td><strong>{row.code}</strong><small>{text(row.name)}</small></td>
            <td>{numberText(row.rawPlatformCount)}</td><td>{numberText(row.platformCount)}</td>
            <td>{numberText(row.parsedCount)}</td><td>{numberText(row.ruleEligibleCount)}</td>
            <td>{numberText(row.aiCompletedCount)}</td><td>{numberText(row.relevantCount)}</td>
            <td><span className={`admin-page-count is-${row.pageCommentState}`}>{row.pageCommentLabel}</span></td>
          </tr>) : <tr><td colSpan="9" className="admin-empty">{loading ? '正在读取数据库…' : '当前没有有效 ETF 数据。'}</td></tr>}</tbody>
        </table>
      </div>
    </section>

    <section className="admin-card admin-ai">
      <header className="admin-section-head">
        <div><p className="admin-eyebrow">DATABASE → LAYER A → LAYER B</p><h2>AI 分析 · {selectedRange}</h2></div>
        <p>处理{scopeText(pipeline?.ownTopLimit)}；没有评论的产品不会产生模型请求，输入未变化的结果直接复用。</p>
      </header>
      <div className="admin-ai-summary">
        <Metric label="分析 anchor" value={layers.anchor} /><Metric label="当前区间" value={layers.range || selectedRange} />
        <Metric label="本轮产品" value={layers.productCount} /><Metric label="待重算产品" value={layers.dirtyPairs} />
      </div>

      <div className="admin-progress-panel" aria-label="AI 处理进度">
        <div className="admin-progress-head">
          <div><strong>本轮处理进度</strong><span>{completedProducts} / {progress.totalProducts || layers.productCount || 0} 只 ETF</span></div>
          <div><StatusPill value={progressStatus} /><strong>{progressPercent}%</strong></div>
        </div>
        <div className="admin-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progressPercent}>
          <span style={{ width: `${progressPercent}%` }} />
        </div>
        <div className="admin-progress-meta">
          <span>当前：{progress.currentCode || (activeRangeRun?.status === 'queued' ? '等待 worker 领取' : '—')}</span>
          <span>任务完成 {progress.completedJobs || 0} · 处理中/待处理 {progress.activeJobs || 0} · 失败 {progress.failedJobs || 0}</span>
          <span>更新：{dateTime(progress.updatedAt || activeRangeRun?.startedAt || activeRangeRun?.requestedAt)}</span>
        </div>
      </div>

      <div className="admin-layer-grid">
        <article>
          <h3>Layer A · 逐条理解</h3>
          <p>评论与帖子先完成产品、相关性、态度、主题及合规标注。</p>
          <div className="admin-layer-list">
            {layers.layerA?.map(item => {
              const counts = item.counts || {}
              return <div className="admin-layer-row" key={item.id} data-ai-layer={item.id}>
                <div><strong>{item.label}</strong><small>{item.description}</small></div>
                <div className="admin-layer-state"><StatusPill value={item.status} /><span>完成 {counts.done || 0} · 待处理 {(counts.pending || 0) + (counts.claimed || 0)} · 失败 {(counts.failed || 0) + (counts.dead || 0)}</span></div>
                {item.error && <div className="admin-inline-error">{item.error}</div>}
              </div>
            })}
          </div>
        </article>
        <article>
          <h3>Layer B · 页面文案</h3>
          <p>基于 Layer A 结果生成产品页需要的总结、标签、阶段与竞品原因。</p>
          <div className="admin-run-buttons" aria-label="AI 分析运行区间">
            {ranges.map(item => {
              const busy = !workerReady || Boolean(item.activeRun) || submittingRange === item.key
              return <button key={item.key} onClick={() => trigger(item.key)} disabled={busy}>
                {runLabel(item.key)}
              </button>
            })}
          </div>
          <div className="admin-layer-list">
            {layers.layerB?.map(item => <div className="admin-layer-row" key={item.id} data-ai-layer={item.id}>
              <div>
                <strong>{item.label}</strong><small>页面：{item.locations?.join('、')}</small>
                {item.missingPairs > 0 && <CodeList codes={item.missingCodes} total={item.missingPairs} />}
              </div>
              <div className="admin-layer-state">
                <StatusPill value={item.status} />
                <span>已生成 {item.producedPairs || 0} / {item.expectedPairs || 0} · 待更新 {item.stalePairs || 0} · 缺失 {item.missingPairs || 0}</span>
              </div>
            </div>)}
          </div>
        </article>
      </div>
    </section>

    <section className="admin-card admin-history">
      <header className="admin-section-head"><div><p className="admin-eyebrow">DATABASE QUEUE</p><h2>最近运行</h2></div><p>最近 20 次数据库任务（手动与每日定时）及执行摘要。</p></header>
      <div className="admin-table-wrap"><table><thead><tr>
        <th>区间</th><th>来源</th><th>状态</th><th>提交时间</th><th>完成产品</th><th>AI 请求</th>
        <th>标注写入</th><th>Layer B 写入</th><th>复用</th><th>仍缺</th><th>错误与原因</th>
      </tr></thead><tbody>{recentRuns.length ? recentRuns.map(run => {
        const summary = run.summary || {}
        const reasons = summary.reasons || []
        return <tr key={run.id}>
          <td className="admin-mono">{run.range}</td><td>{SOURCE_TEXT[run.requestedBy] || '手动'}</td>
          <td><StatusPill value={run.status} /></td><td>{dateTime(run.requestedAt)}</td>
          <td>{summary.productsCompleted == null || summary.productsTotal == null ? '—' : `${summary.productsCompleted} / ${summary.productsTotal}`}</td>
          <td>{numberText(summary.aiRequests)}</td><td>{numberText(summary.annotationsWritten)}</td>
          <td>{numberText(summary.synthesisWritten)}</td><td>{numberText(summary.reused)}</td>
          <td>{numberText(summary.missingPairs)}</td>
          <td>{numberText(summary.errors)}
            {run.error && <small className="admin-run-error">{run.error}</small>}
            {reasons.map(reason => <small className="admin-run-error is-reason" key={reason}>{reason}</small>)}
          </td>
        </tr>
      }) : <tr><td colSpan="11" className="admin-empty">暂无运行记录。</td></tr>}</tbody></table></div>
    </section>
  </main>
}
