import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'


const source = async (name) => readFile(new URL(`../src/screens/${name}`, import.meta.url), 'utf8')


test('official publisher strength includes every issuer account', async () => {
  const text = await source('OfficialActivity.jsx')

  assert.doesNotMatch(text, /\.slice\(0,\s*8\)/)
  assert.match(text, />发行商官号发布强度<\/div>/)
  assert.match(text, />全部发行商官号 · 篇数 \/ 互动/)
})


test('KOL volume ranking exports the currently visible ranking as CSV', async () => {
  const text = await source('KolActivity.jsx')

  assert.match(text, /exportLeaderCsv\(list, summary, filename, rangeKey\)/)
  assert.match(text, /'排名', 'KOL', 'KOL类型'/)
  assert.match(text, /self\.exportLeaderCsv\(lRows,/)
  assert.match(text, />导出 CSV（\{v\.leaderCount\} 位）<\/div>/)
})
