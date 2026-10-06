import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')

test('design CSS is a module entry, not a cross-root PostCSS import', () => {
  const main = read('../src/main.jsx')
  const tokens = read('../src/styles/tokens.css')
  assert.match(
    main,
    /import '\.\.\/\.\.\/design\/_ds\/[^']+\/colors_and_type\.css'[\s\S]*import '\.\/styles\/tokens\.css'/,
  )
  assert.doesNotMatch(tokens, /@import\s+['"][^'"]*design\//)
})
