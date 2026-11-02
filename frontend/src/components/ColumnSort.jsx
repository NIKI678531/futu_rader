import React from 'react'
import { s, hover } from '../lib/dc'

export default function ColumnSort({ label, sortKey, activeKey, direction, onSort, align = 'left' }) {
  const [open, setOpen] = React.useState(false)
  const active = sortKey === activeKey
  const choose = (nextDirection) => (event) => {
    event.stopPropagation()
    onSort(sortKey, nextDirection)
    setOpen(false)
  }

  return (
    <span onMouseLeave={() => setOpen(false)} style={s(`position:relative;display:inline-flex;width:100%;align-items:center;justify-content:${align === 'right' ? 'flex-end' : 'flex-start'};gap:5px`)}>
      <span>{label}</span>
      <button
        type="button"
        aria-label={`${label}排序`}
        aria-expanded={open}
        title={`${label}：选择升序或降序`}
        onClick={(event) => { event.stopPropagation(); setOpen(!open) }}
        style={s(`display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;padding:0;border:1px solid ${active ? 'var(--csop-blue-300)' : 'var(--border-2)'};border-radius:var(--radius-small);background:${active ? 'var(--csop-blue-50)' : '#fff'};font:600 11px/1 var(--font-mono);color:${active ? 'var(--csop-blue-700)' : 'var(--ink-400)'};cursor:pointer`)}
        className={hover('background:var(--csop-blue-100);color:var(--csop-blue-700)')}
      >
        {active ? (direction === 'asc' ? '↑' : '↓') : '⇅'}
      </button>
      {open && (
        <span role="menu" style={s(`position:absolute;${align === 'right' ? 'right:0' : 'left:0'};top:100%;z-index:12;min-width:82px;padding:4px;background:#fff;border:1px solid var(--border-2);border-radius:var(--radius-control);box-shadow:0 8px 20px rgba(14,42,82,0.16)`)}>
          <button type="button" role="menuitem" onClick={choose('asc')} style={s(`display:block;width:100%;padding:6px 8px;border:0;border-radius:var(--radius-small);background:${active && direction === 'asc' ? 'var(--csop-blue-50)' : '#fff'};font:500 12px/1.4 var(--font-cjk);color:var(--ink-700);text-align:left;white-space:nowrap;cursor:pointer`)}>升序 ↑</button>
          <button type="button" role="menuitem" onClick={choose('desc')} style={s(`display:block;width:100%;padding:6px 8px;border:0;border-radius:var(--radius-small);background:${active && direction === 'desc' ? 'var(--csop-blue-50)' : '#fff'};font:500 12px/1.4 var(--font-cjk);color:var(--ink-700);text-align:left;white-space:nowrap;cursor:pointer`)}>降序 ↓</button>
        </span>
      )}
    </span>
  )
}
