import { useState } from 'react'
import { s, hover } from '../../lib/dc'

export default function KpiNote({ k }) {
  const [noteOpen, setNoteOpen] = useState(false)

  return (
    <span style={s('position:relative;display:inline-flex;align-items:center')}>
      {k.note && (
        <button
          type="button"
          aria-label={`查看${k.label}说明`}
          aria-expanded={noteOpen}
          onClick={() => setNoteOpen(!noteOpen)}
          style={s(`width:16px;height:16px;padding:0;border:1px solid ${noteOpen ? 'var(--csop-blue-500)' : 'var(--border-2)'};border-radius:var(--radius-pill);background:${noteOpen ? 'var(--csop-blue-50)' : '#fff'};font:600 10px/1 var(--font-mono);color:${noteOpen ? 'var(--csop-blue-700)' : 'var(--ink-500)'};cursor:pointer`)}
          className={hover('background:var(--csop-blue-50);border-color:var(--csop-blue-400)')}
        >i</button>
      )}
      {noteOpen && (
        <span role="note" style={s('position:absolute;z-index:40;left:24px;top:-10px;width:300px;padding:10px 12px;border:1px solid var(--csop-blue-200);border-radius:var(--radius-small);background:#fff;box-shadow:0 8px 24px rgba(14,42,82,0.16);font:400 12px/1.6 var(--font-cjk);color:var(--ink-700);text-align:left;text-wrap:pretty')}>
          {k.note}
        </span>
      )}
    </span>
  )
}
