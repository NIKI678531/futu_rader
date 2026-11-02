import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { compareSortValues, stableSort } from '../src/lib/tableSort.js'

const screen = readFileSync(new URL('../src/screens/KolActivity.jsx', import.meta.url), 'utf8')

test('table sorting supports both directions while keeping unknown values last', () => {
  const rows = [{ value: null }, { value: 3 }, { value: 1 }]

  assert.deepEqual(stableSort(rows, (row) => row.value, 'asc').map((row) => row.value), [1, 3, null])
  assert.deepEqual(stableSort(rows, (row) => row.value, 'desc').map((row) => row.value), [3, 1, null])
})

test('table sorting handles Chinese text and compound displayed values deterministically', () => {
  const rows = [
    { id: 'b', label: '乙', mix: [2, 1] },
    { id: 'a', label: '甲', mix: [2, 3] },
    { id: 'c', label: '丙', mix: [1, 9] },
  ]

  assert.deepEqual(stableSort(rows, (row) => row.label, 'asc').map((row) => row.id), ['c', 'a', 'b'])
  assert.deepEqual(stableSort(rows, (row) => row.mix, 'desc').map((row) => row.id), ['a', 'b', 'c'])
  assert.equal(compareSortValues(null, 0, 'desc'), 1)
})

test('KOL tables expose sorting for every data column and the interaction snapshot time', () => {
  for (const key of ['kol', 'time', 'etf', 'type', 'summary', 'ownership', 'eng', 'views']) {
    assert.match(screen, new RegExp(`sortKey="${key}"`))
  }
  for (const key of ['kol', 'n', 'mix', 'views', 'comments', 'type']) {
    assert.match(screen, new RegExp(`sortKey="${key}"`))
  }
  assert.match(screen, /互动数据截至：/)
  assert.match(screen, /interactionUpdatedAt: stamp\(p0\.interactionUpdatedAt\)/)
})
