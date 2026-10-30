import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('official post cards render verified original-post links conditionally', async () => {
  const source = await readFile(new URL('../src/screens/OfficialActivity.jsx', import.meta.url), 'utf8')

  assert.match(source, /url: p\.url \|\| null, hasUrl: !!p\.url/)
  assert.match(source, /c\.hasUrl/)
  assert.match(source, /href=\{c\.url\}/)
  assert.match(source, /原帖 ↗/)
})
