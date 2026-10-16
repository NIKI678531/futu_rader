import { s } from '../../lib/dc'
import DatePresetTabs from '../../components/DatePresetTabs'

/* Header row 2 — date presets, resolved range, 热力粒度 / 基准 pills. */
export default function FilterBar({ v }) {
  return (
    <div data-shell-filter style={s('display:flex;align-items:center;gap:8px 10px;min-height:44px;box-sizing:border-box;padding:5px 16px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1);flex-wrap:wrap')}>
      <DatePresetTabs presets={v.presets} />
      <div style={s('display:flex;align-items:center;gap:8px;padding:6px 12px;border:1px solid var(--border-2);border-radius:var(--radius-control);background:#fff;font:500 14px/1.4 var(--font-mono);color:var(--ink-700);white-space:nowrap')}>
        <span>{v.rangeFrom}</span><span style={s('color:var(--ink-300)')}>→</span><span>{v.rangeTo}</span>
      </div>
      <div style={s('padding:5px 11px;border-radius:var(--radius-pill);background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>热力粒度 {v.granLabel}</div>
      <div style={s('padding:5px 11px;border-radius:var(--radius-pill);background:#fff;border:1px solid var(--border-2);font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>{v.benchLabel} · 基准 {v.benchText}</div>
    </div>
  )
}
