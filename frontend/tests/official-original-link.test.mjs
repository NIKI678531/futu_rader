import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('official post cards do not render an external original-post button', async () => {
  const source = await readFile(new URL('../src/screens/OfficialActivity.jsx', import.meta.url), 'utf8')

  assert.equal(source.includes('原帖 ↗'), false)
})
