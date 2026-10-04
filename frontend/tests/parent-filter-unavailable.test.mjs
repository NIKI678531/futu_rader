import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('parent-filter 200/null renders an unavailable state before pool fields are read', () => {
  const sector = source('screens/sectorOverview/index.jsx')
  const product = source('screens/productMonitor/index.jsx')
  const unavailable = source('components/DataUnavailable.jsx')

  for (const screen of [sector, product]) {
    const render = screen.slice(screen.lastIndexOf('  render() {'))
    const guard = render.indexOf('if (R.pool(this.state.rangeKey) == null)')
    const derive = render.indexOf('const v = this.renderVals()')
    assert.ok(guard >= 0, 'screen must guard a null filtered pool')
    assert.ok(derive > guard, 'null guard must run before renderVals dereferences pool data')
    assert.match(render, /return <DataUnavailable screenLabel=/)
  }

  assert.match(unavailable, /data-screen-unavailable/)
  assert.match(unavailable, />数据暂不可用</)
  assert.doesNotMatch(unavailable, /页面渲染失败/)
})
