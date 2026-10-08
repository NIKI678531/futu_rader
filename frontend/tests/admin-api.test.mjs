import assert from 'node:assert/strict'
import test from 'node:test'

const admin = await import('../src/lib/adminApi.js')

test('pipeline reads one range without browser credentials', async () => {
  let request
  global.fetch = async (url, options) => {
    request = { url, options }
    return { ok: true, status: 200, json: async () => ({ status: 'ok', data: { comments: [] } }) }
  }
  await admin.loadAdminPipeline('d14')
  assert.equal(request.url, '/api/v1/admin/pipeline?range=d14')
  assert.equal(request.options.cache, 'no-store')
  assert.equal('X-Admin-Token' in request.options.headers, false)
})

test('pipeline can request the current-day range', async () => {
  let request
  global.fetch = async (url, options) => {
    request = { url, options }
    return { ok: true, status: 200, json: async () => ({ status: 'ok', data: { comments: [] } }) }
  }
  await admin.loadAdminPipeline('d0')
  assert.equal(request.url, '/api/v1/admin/pipeline?range=d0')
})

test('analysis submission sends only the selected range', async () => {
  let request
  global.fetch = async (url, options) => {
    request = { url, options }
    return { ok: true, status: 202, json: async () => ({ status: 'ok', data: { run: { status: 'queued' } } }) }
  }
  await admin.runAdminAnalysis('d7')
  assert.equal(request.url, '/api/v1/admin/analysis-runs')
  assert.equal(request.options.method, 'POST')
  assert.deepEqual(JSON.parse(request.options.body), { range: 'd7' })
})

test('analysis submission preserves the current-day range key', async () => {
  let request
  global.fetch = async (url, options) => {
    request = { url, options }
    return { ok: true, status: 202, json: async () => ({ status: 'ok', data: { run: { status: 'queued' } } }) }
  }
  await admin.runAdminAnalysis('d0')
  assert.deepEqual(JSON.parse(request.options.body), { range: 'd0' })
})

test('structured API errors are exposed to the page', async () => {
  global.fetch = async () => ({
    ok: false,
    status: 503,
    json: async () => ({ error: { type: 'dependency_unavailable', message: 'Radar 数据库暂时不可用' } }),
  })
  await assert.rejects(
    () => admin.loadAdminPipeline('d1'),
    error => error instanceof admin.AdminApiError
      && error.status === 503
      && error.code === 'dependency_unavailable',
  )
})
