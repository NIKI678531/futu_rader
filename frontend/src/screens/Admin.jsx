import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AdminApiError,
  clearAdminToken,
  getAdminToken,
  loadAdminRuns,
  loadAdminSources,
  loadAdminTasks,
  runAdminTask,
  saveAdminToken,
} from '../lib/adminApi'
import '../styles/admin.css'

const STATUS_TEXT = {
  current: '链路已追平',
  waiting_for_sync: '等待同步',
  sync_failed: '同步失败',
  upstream_unavailable: '上游状态不可用',
  radar_unavailable: 'Radar 数据库不可用',
  succeeded: '成功', running: '运行中', queued: '排队中', failed: '失败',
  success: '成功', not_started: '尚未开始', unavailable: '不可用',
}

function text(value, fallback = '—') {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function dateTime(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('zh-HK', { hour12: false })
}

function StatusPill({ value }) {
  const normalized = value || 'unknown'
  return <span className={`admin-pill is-${normalized}`}>{STATUS_TEXT[normalized] || normalized}</span>
}

function Login({ onLogin, error }) {
  const [value, setValue] = useState('')
  return <main className="admin-login-shell">
    <form className="admin-login" onSubmit={event => { event.preventDefault(); if (value.trim()) onLogin(value) }}>
      <p className="admin-eyebrow">FUTU RADAR</p>
      <h1>数据链路管理</h1>
      <p>请输入独立管理令牌。令牌只保存在这个浏览器标签页，关闭后自动清除。</p>
      <label htmlFor="admin-token">管理令牌</label>
      <input id="admin-token" type="password" autoComplete="off" value={value}
        onChange={event => setValue(event.target.value)} autoFocus />
      {error && <div className="admin-alert is-error">{error}</div>}
      <button type="submit" disabled={!value.trim()}>进入管理页</button>
      <Link to="/official">返回业务页面</Link>
    </form>
  </main>
}

function Metric({ label, value }) {
  return <div className="admin-metric"><dt>{label}</dt><dd>{text(value)}</dd></div>
}

function PipelineCard({ step, title, status, children, error }) {
  return <section className="admin-card admin-pipeline-card">
    <header><span className="admin-step">{step}</span><h2>{title}</h2><StatusPill value={status} /></header>
    <dl>{children}</dl>
    {error && <div className="admin-inline-error">{error}</div>}
  </section>
}

export default function Admin() {
  const [token, setToken] = useState(getAdminToken())
  const [sources, setSources] = useState(null)
  const [tasks, setTasks] = useState([])
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(false)
  const [runningTask, setRunningTask] = useState(null)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const [sourceData, taskData, runData] = await Promise.all([
        loadAdminSources(), loadAdminTasks(), loadAdminRuns(),
      ])
      setSources(sourceData)
      setTasks(taskData.items || [])
      setRuns(runData.items || [])
      setError(runData.error || '')
    } catch (reason) {
      if (reason instanceof AdminApiError && reason.status === 401) {
        clearAdminToken()
        setToken('')
        setError('管理令牌无效，请重新输入')
      } else {
        setError(reason.message || '管理状态读取失败')
      }
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    refresh()
    const interval = window.setInterval(refresh, tasks.some(task => task.isRunning) ? 5000 : 30000)
    return () => window.clearInterval(interval)
  }, [refresh, tasks.some(task => task.isRunning)])

  const taskMap = useMemo(() => Object.fromEntries(tasks.map(task => [task.id, task])), [tasks])

  async function trigger(task) {
    setRunningTask(task)
    setError('')
    try {
      await runAdminTask(task)
      await refresh()
    } catch (reason) {
      const details = reason.blockedReasons?.length ? `：${reason.blockedReasons.join('；')}` : ''
      setError(`${reason.message || '任务提交失败'}${details}`)
    } finally {
      setRunningTask(null)
    }
  }

  if (!token) {
    return <Login error={error} onLogin={value => { saveAdminToken(value); setToken(value.trim()); setError('') }} />
  }

  const upstream = sources?.upstream || {}
  const upstreamRun = upstream.latestRun
  const radar = sources?.radar || {}
  const ai = sources?.ai || {}

  return <main className="admin-shell">
    <header className="admin-topbar">
      <div><p className="admin-eyebrow">FUTU RADAR / ADMIN</p><h1>数据链路管理</h1></div>
      <div className="admin-actions"><Link to="/official">查看业务页面</Link>
        <button className="is-secondary" onClick={refresh} disabled={loading}>{loading ? '刷新中…' : '刷新状态'}</button>
        <button className="is-quiet" onClick={() => { clearAdminToken(); setToken('') }}>退出</button></div>
    </header>

    {error && <div className="admin-alert is-error">{error}</div>}
    {sources && <div className={`admin-chain-banner is-${sources.chainState}`}>
      <strong>{STATUS_TEXT[sources.chainState] || sources.chainState}</strong>
      <span>{sources.waitingForSync ? '上游完整日期比 Radar 新，可以启动同步。' : '状态按上游回执、Radar checkpoint 与 AI 进度计算。'}</span>
      <small>检查时间：{dateTime(sources.generatedAt)}</small>
    </div>}

    <div className="admin-pipeline">
      <PipelineCard step="1" title="上游采集" status={upstreamRun?.status || (upstream.available ? 'not_started' : 'unavailable')} error={upstream.error || upstreamRun?.error}>
        <Metric label="最新完整日期" value={upstream.latestCompleteRun?.completeThrough} />
        <Metric label="最新采集运行 ID" value={upstreamRun?.runId} />
        <Metric label="可同步批次 ID" value={upstream.latestCompleteRun?.runId} />
        <Metric label="成功产品 / 配置产品" value={upstreamRun ? `${text(upstreamRun.succeededSymbolCount)} / ${text(upstreamRun.configuredSymbolCount)}` : null} />
        <Metric label="完成时间" value={dateTime(upstreamRun?.finishedAt)} />
      </PipelineCard>
      <PipelineCard step="2" title="Radar 同步" status={radar.latestSync?.status || (radar.available ? 'not_started' : 'unavailable')} error={radar.error || radar.latestSync?.error}>
        <Metric label="页面当前 anchor" value={radar.anchor} />
        <Metric label="源完整日期" value={radar.sourceCompleteThrough} />
        <Metric label="最近写入" value={radar.latestSync?.counts ? JSON.stringify(radar.latestSync.counts) : null} />
        <Metric label="Checkpoint" value={`${text(radar.checkpointCount, '0')} 条 · ${dateTime(radar.checkpointUpdatedAt)}`} />
      </PipelineCard>
      <PipelineCard step="3" title="AI 分析" status={ai.status || 'not_started'} error={ai.error}>
        <Metric label="评论路由" value={ai.routingReady ? '已就绪' : '未就绪'} />
        <Metric label="分析 anchor" value={ai.analysisAnchor} />
        <Metric label="完成产品" value={`${text(ai.completedProducts, '0')} / ${text(ai.totalProducts, '0')}`} />
        <Metric label="今日预算" value={ai.budget ? `${ai.budget.used} / ${ai.budget.limit} 次` : null} />
      </PipelineCard>
    </div>

    <section className="admin-card admin-tasks">
      <header><div><p className="admin-eyebrow">CONTROL PLANE</p><h2>任务中心</h2></div><p>按钮只调用 Airflow API；不会在网页进程执行长任务。</p></header>
      <div className="admin-task-grid">
        {['sync', 'analyze'].map(name => {
          const task = taskMap[name]
          if (!task) return <article className="admin-task" key={name}><h3>{name}</h3><p>状态加载中…</p></article>
          const busy = runningTask === name || task.isRunning
          return <article className="admin-task" key={name}>
            <div className="admin-task-title"><h3>{task.label}</h3><StatusPill value={task.lastRun?.status || 'not_started'} /></div>
            <p>{task.schedule}</p>
            <dl>
              <Metric label="DAG" value={task.dagId} />
              <Metric label="调度状态" value={task.isPaused === null ? '不可用' : task.isPaused ? '已暂停' : '已启用'} />
              <Metric label="上次运行" value={dateTime(task.lastRun?.startedAt || task.lastRun?.logicalDate)} />
              <Metric label="下次运行" value={dateTime(task.nextRunAt)} />
            </dl>
            {task.blockedReasons?.length > 0 && <ul className="admin-blockers">{task.blockedReasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
            <button onClick={() => trigger(name)} disabled={!task.canRun || busy}>
              {busy ? '任务已提交 / 运行中' : task.label}
            </button>
          </article>
        })}
      </div>
    </section>

    <section className="admin-card admin-history">
      <details open><summary>最近运行记录（{runs.length}）</summary>
        <div className="admin-table-wrap"><table><thead><tr><th>任务</th><th>状态</th><th>运行 ID</th><th>开始</th><th>结束</th><th>错误</th></tr></thead>
          <tbody>{runs.length ? runs.map(run => <tr key={`${run.task}-${run.dagRunId}`}>
            <td>{run.task === 'sync' ? '数据同步' : 'AI 分析'}</td><td><StatusPill value={run.status} /></td>
            <td className="admin-mono">{text(run.dagRunId)}</td><td>{dateTime(run.startedAt || run.logicalDate)}</td><td>{dateTime(run.finishedAt)}</td><td>{text(run.error)}</td>
          </tr>) : <tr><td colSpan="6" className="admin-empty">暂无运行记录，或 Airflow 当前不可用。</td></tr>}</tbody>
        </table></div>
      </details>
    </section>
  </main>
}

