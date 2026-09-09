/* Shim for the two things `.dc.html` gives you that plain JSX does not:
   `style="a:b;c:d"` declaration strings, and `style-hover` / `style-focus`.

   Keeping both as strings means a ported screen stays diffable line-for-line
   against its `design/*.dc.html` source — the markup is copied, not retyped. */

/* ── style strings → React style objects ─────────────────────────────── */

const styleCache = new Map()

/* Split on `;` / `:` at paren depth 0 only, so `rgba(1,2,3)`, `linear-gradient(…)`
   and `url(…)` survive intact. */
function splitTop(str, sep) {
  const out = []
  let depth = 0
  let start = 0
  for (let i = 0; i < str.length; i++) {
    const c = str[i]
    if (c === '(') depth++
    else if (c === ')') depth--
    else if (c === sep && depth === 0) {
      out.push(str.slice(start, i))
      start = i + 1
    }
  }
  out.push(str.slice(start))
  return out
}

function camel(prop) {
  return prop.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
}

/** Parse a CSS declaration string into a React style object. Memoized. */
export function s(css) {
  if (!css) return undefined
  const hit = styleCache.get(css)
  if (hit) return hit

  const out = {}
  for (const decl of splitTop(css, ';')) {
    const trimmed = decl.trim()
    if (!trimmed) continue
    const idx = splitTop(trimmed, ':')[0].length
    if (idx >= trimmed.length) continue
    const prop = trimmed.slice(0, idx).trim()
    const value = trimmed.slice(idx + 1).trim()
    if (!prop || !value) continue
    // React passes `--*` keys through to setProperty untouched.
    out[prop.startsWith('--') ? prop : camel(prop)] = value
  }

  styleCache.set(css, out)
  return out
}

/* ── style-hover / style-focus → real CSS rules ──────────────────────── */

let sheet = null
const ruleCache = new Map()

function styleSheet() {
  if (!sheet) {
    const el = document.createElement('style')
    el.setAttribute('data-dc-states', '')
    document.head.appendChild(el)
    sheet = el.sheet
  }
  return sheet
}

function hash(str) {
  let h = 5381
  for (let i = 0; i < str.length; i++) h = ((h << 5) + h + str.charCodeAt(i)) | 0
  return (h >>> 0).toString(36)
}

/* Real `:hover` / `:focus` rules rather than React state: the tables here run to
   hundreds of rows and chips, and a state-per-element hover would re-render the
   screen on every pointer move. `!important` matches the dc runtime, which layers
   the state style over the element's own inline style. */
function stateClass(pseudo, css) {
  if (!css) return undefined
  const key = pseudo + '|' + css
  const hit = ruleCache.get(key)
  if (hit) return hit

  const cls = `dc-${pseudo}-${hash(css)}`
  const body = splitTop(css, ';')
    .map((d) => d.trim())
    .filter(Boolean)
    .map((d) => `${d} !important`)
    .join(';')

  try {
    const sh = styleSheet()
    sh.insertRule(`.${cls}:${pseudo}{${body}}`, sh.cssRules.length)
  } catch {
    /* A malformed declaration should degrade to "no hover effect", not a crash. */
  }

  ruleCache.set(key, cls)
  return cls
}

/** Class name applying `css` on `:hover`. Mirrors `style-hover` in a .dc.html. */
export const hover = (css) => stateClass('hover', css)

/** Class name applying `css` on `:focus`. Mirrors `style-focus` in a .dc.html. */
export const focus = (css) => stateClass('focus', css)

/** Join class names, dropping the undefined ones. */
export const cx = (...names) => names.filter(Boolean).join(' ') || undefined
