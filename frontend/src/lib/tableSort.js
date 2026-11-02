/* Shared table sorting rules: unknown values stay last in both directions. */

const COLLATOR = new Intl.Collator('zh-Hans-CN', {
  numeric: true,
  sensitivity: 'base',
})

function missing(value) {
  return value == null || value === ''
}

function comparePresent(a, b, direction) {
  if (Array.isArray(a) || Array.isArray(b)) {
    const left = Array.isArray(a) ? a : [a]
    const right = Array.isArray(b) ? b : [b]
    const length = Math.max(left.length, right.length)
    for (let index = 0; index < length; index++) {
      const result = compareSortValues(left[index], right[index], direction)
      if (result) return result
    }
    return 0
  }

  const leftNumber = typeof a === 'number' ? a : Number.NaN
  const rightNumber = typeof b === 'number' ? b : Number.NaN
  const result = Number.isFinite(leftNumber) && Number.isFinite(rightNumber)
    ? leftNumber - rightNumber
    : COLLATOR.compare(String(a), String(b))
  return direction === 'desc' ? -result : result
}

export function compareSortValues(a, b, direction = 'asc') {
  const leftMissing = missing(a)
  const rightMissing = missing(b)
  if (leftMissing || rightMissing) {
    if (leftMissing && rightMissing) return 0
    return leftMissing ? 1 : -1
  }
  return comparePresent(a, b, direction)
}

export function stableSort(rows, valueOf, direction = 'asc', tieBreaker) {
  return rows.map((row, index) => ({ row, index })).sort((a, b) => {
    return compareSortValues(valueOf(a.row), valueOf(b.row), direction)
      || (tieBreaker ? tieBreaker(a.row, b.row) : 0)
      || a.index - b.index
  }).map((item) => item.row)
}
