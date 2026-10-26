/* Display-only ETF mentions. Product attribution remains on post.mentioned/code. */

function fallbackTicker(code) {
  var raw = String(code == null ? '' : code);
  return /^\d+$/.test(raw) ? raw.padStart(5, '0') + '.HK' : raw;
}

export function mentionedEtfsOf(post) {
  if (Array.isArray(post && post.mentionedEtfs)) return post.mentionedEtfs;
  /* Compatibility with frozen/demo payloads while all environments roll out. */
  return Array.isArray(post && post.mentioned) ? post.mentioned.map(function (item) {
    return Object.assign({}, item, {
      ticker: item.ticker || fallbackTicker(item.code),
      inPool: true
    });
  }) : [];
}

export function primaryDisplayedEtf(post) {
  var items = mentionedEtfsOf(post);
  if (post && post.code) {
    for (var i = 0; i < items.length; i++) {
      if (String(items[i].code) === String(post.code)) return items[i];
    }
  }
  return items[0] || null;
}
