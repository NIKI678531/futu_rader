import { useEffect, useState } from 'react'
import { s, hover } from '../lib/dc'
import { CUSTOM_ANALYSIS_MESSAGE, customRangeValidation, makeCustomRange, parseCustomRange, setRangeInUrl } from '../lib/customRange'

export default function DatePresetTabs({ presets }) {
  const customPreset = presets.find(preset => preset.custom)
  const activeCustom = customPreset && parseCustomRange(customPreset.rangeKey)
  const [open, setOpen] = useState(false)
  const [from, setFrom] = useState(activeCustom?.from || customPreset?.latestDate || '')
  const [to, setTo] = useState(activeCustom?.to || customPreset?.latestDate || '')
  useEffect(() => {
    setFrom(activeCustom?.from || customPreset?.latestDate || '')
    setTo(activeCustom?.to || customPreset?.latestDate || '')
  }, [activeCustom?.from, activeCustom?.to, customPreset?.latestDate])
  const { days, error: invalidReason } = customRangeValidation(from, to, customPreset)
  const invalid = Boolean(invalidReason)
  const availableText = customPreset?.earliestDate && customPreset?.latestDate
    ? `可选数据 ${customPreset.earliestDate} ～ ${customPreset.latestDate}`
    : `最新可选 ${customPreset?.latestDate || '暂不可用'}`
  const choose = (preset, key = preset.key) => {
    setRangeInUrl(key)
    preset.go(key)
    if (preset.custom) setOpen(false)
  }
  return (
    <div style={s('position:relative;flex:none')}>
      <div
        role="group"
        aria-label="日期区间"
        data-date-preset-tabs
        style={s('display:flex;border:1px solid var(--border-2);border-radius:var(--radius-control);overflow:hidden;background:#fff')}
      >
        {presets.map((preset) => (
          <button
            key={preset.label}
            type="button"
            aria-pressed={Boolean(preset.active)}
            data-date-preset-placeholder={preset.placeholder || undefined}
            data-screen-diff-new={preset.placeholder || preset.screenDiffNew ? 'today-range-placeholder' : undefined}
            onClick={() => preset.custom ? setOpen(!open) : choose(preset)}
            style={s(`display:block;appearance:none;margin:0;padding:5px 13px;border:0;border-right:1px solid var(--border-1);font:${preset.active ? 600 : 400} 14px/1.4 var(--font-cjk);color:${preset.active ? 'var(--csop-blue-700)' : 'var(--ink-600)'};background:${preset.active ? 'var(--csop-blue-50)' : '#fff'};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)}
            className={hover('background:var(--csop-blue-50)')}
          >
            {preset.label}
          </button>
        ))}
      </div>
      {activeCustom && (
        <div data-custom-analysis-note role="status" style={s('margin-top:4px;text-align:right;font:400 11px/1.4 var(--font-cjk);color:var(--csop-blue-700);white-space:nowrap')}>{CUSTOM_ANALYSIS_MESSAGE}</div>
      )}
      {open && customPreset && (
        <div data-custom-date-picker style={s('position:absolute;z-index:40;right:0;top:calc(100% + 8px);width:320px;padding:14px;background:#fff;border:1px solid var(--border-2);border-radius:var(--radius-card);box-shadow:0 10px 30px rgba(14,42,82,.16)')}>
          <div style={s('font:600 14px/1.4 var(--font-cjk);color:var(--ink-800);margin-bottom:10px')}>选择 HKT 自然日（含首尾）</div>
          <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:10px')}>
            <label style={s('font:400 12px/1.5 var(--font-cjk);color:var(--ink-500)')}>开始日<input aria-label="自选开始日" type="date" min={customPreset.earliestDate || undefined} max={customPreset.latestDate || undefined} value={from} onChange={event => setFrom(event.target.value)} style={s('display:block;width:100%;box-sizing:border-box;margin-top:4px;padding:6px;border:1px solid var(--border-2);border-radius:var(--radius-control)')} /></label>
            <label style={s('font:400 12px/1.5 var(--font-cjk);color:var(--ink-500)')}>结束日<input aria-label="自选结束日" type="date" min={customPreset.earliestDate || undefined} max={customPreset.latestDate || undefined} value={to} onChange={event => setTo(event.target.value)} style={s('display:block;width:100%;box-sizing:border-box;margin-top:4px;padding:6px;border:1px solid var(--border-2);border-radius:var(--radius-control)')} /></label>
          </div>
          <div style={s(`margin-top:8px;font:400 12px/1.5 var(--font-cjk);color:${invalid ? 'var(--negative-600)' : 'var(--ink-400)'}`)}>{invalid ? invalidReason : `${days} 个自然日 · ${availableText}`}</div>
          <div style={s('display:flex;justify-content:flex-end;gap:8px;margin-top:12px')}>
            <button type="button" onClick={() => setOpen(false)} style={s('padding:6px 12px;border:1px solid var(--border-2);border-radius:var(--radius-control);background:#fff;color:var(--ink-600);cursor:pointer')}>取消</button>
            <button type="button" disabled={invalid} onClick={() => choose(customPreset, makeCustomRange(from, to))} style={s(`padding:6px 12px;border:0;border-radius:var(--radius-control);background:${invalid ? 'var(--ink-200)' : 'var(--csop-blue-600)'};color:#fff;cursor:${invalid ? 'not-allowed' : 'pointer'}`)}>应用</button>
          </div>
        </div>
      )}
    </div>
  )
}
