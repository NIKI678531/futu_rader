/* The design files navigate by filename (`kol-detail.dc.html?kol=…&range=d7`).
   radar-data.js builds those hrefs, so rather than fork the data layer we translate
   them to router paths at render time. Query params keep their names. */

export const PATH_BY_FILE = {
  'sector-overview.dc.html': '/sector',
  'product-monitor.dc.html': '/product',
  'kol-activity.dc.html': '/kol',
  'kol-detail.dc.html': '/kol/detail',
  'official-activity.dc.html': '/official',
}

export const FILE_BY_PATH = Object.fromEntries(
  Object.entries(PATH_BY_FILE).map(([file, path]) => [path, file]),
)

export function isExternal(href) {
  return /^(https?:)?\/\//.test(href || '') || (href || '').startsWith('mailto:')
}

/** `product-monitor.dc.html?code=3033` → `/product?code=3033`. */
export function hrefToPath(href) {
  if (!href || isExternal(href)) return href
  const [file, query] = href.split('?')
  const path = PATH_BY_FILE[file]
  if (!path) return href
  return query ? `${path}?${query}` : path
}
