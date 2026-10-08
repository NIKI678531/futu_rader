import { s, hover } from '../lib/dc'

export default function DatePresetTabs({ presets }) {
  return (
    <div
      role="group"
      aria-label="日期区间"
      data-date-preset-tabs
      style={s('flex:none;display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}
    >
      {presets.map((preset) => (
        <button
          key={preset.label}
          type="button"
          aria-pressed={Boolean(preset.active)}
          data-date-preset-placeholder={preset.placeholder || undefined}
          data-screen-diff-new={preset.placeholder || preset.screenDiffNew ? 'today-range-placeholder' : undefined}
          onClick={preset.go}
          style={s(`display:block;appearance:none;margin:0;padding:6px 14px;border:0;border-right:1px solid var(--border-1);font:${preset.active ? 600 : 400} 14px/1.4 var(--font-cjk);color:${preset.active ? 'var(--csop-blue-700)' : 'var(--ink-600)'};background:${preset.active ? 'var(--csop-blue-50)' : '#fff'};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)}
          className={hover('background:var(--csop-blue-50)')}
        >
          {preset.label}
        </button>
      ))}
    </div>
  )
}
