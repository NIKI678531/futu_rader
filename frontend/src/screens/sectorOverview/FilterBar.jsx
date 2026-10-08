import { s, hover } from '../../lib/dc'
import DatePresetTabs from '../../components/DatePresetTabs'

/* Header row 2 — date presets, resolved range, 热力粒度 / 基准 pills, 口径与数据状态 toggle. */
export default function FilterBar({ v }) {
  return (
    <div data-shell-filter style={s('display:flex;align-items:center;gap:8px 12px;min-height:48px;box-sizing:border-box;padding:6px 24px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1);flex-wrap:wrap')}>
      <DatePresetTabs presets={v.presets} />
      <div style={s('display:flex;align-items:center;gap:8px;padding:6px 12px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 14px/1.4 var(--font-mono);color:var(--ink-700);white-space:nowrap')}>
        <span>{v.rangeFrom}</span><span style={s('color:var(--ink-300)')}>→</span><span>{v.rangeTo}</span>
      </div>
      <div style={s('padding:5px 11px;border-radius:9999px;background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>热力粒度 {v.granLabel}</div>
      <div style={s('padding:5px 11px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>{v.benchLabel} · 基准 {v.benchText}</div>
      <div onClick={v.toggleNotes} style={s(`margin-left:auto;flex:none;display:flex;align-items:center;gap:8px;padding:6px 14px;border:1px solid ${v.notesBc};border-radius:9999px;background:${v.notesBg};font:500 14px/1.4 var(--font-cjk);color:${v.notesFg};cursor:pointer;transition:all 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>口径与数据状态<span style={s('font:400 12px/1.4 var(--font-mono)')}>{v.notesCaret}</span></div>
    </div>
  )
}
