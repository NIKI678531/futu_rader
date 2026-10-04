import assert from 'node:assert/strict'
import test from 'node:test'

const values = new Map()
global.window = {
  sessionStorage: {
    getItem: key => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  },
}

const admin = await import('../src/lib/adminApi.js')

test('admin token is kept in sessionStorage and sent only as a header', async () => {
  admin.saveAdminToken('  short-lived-token  ')
  assert.equal(admin.getAdminToken(), 'short-lived-token')
  let request
  global.fetch = async (url, options) => {
    request = { url, options }
    return {
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', data: { items: [] } }),
    }
  }
  await admin.loadAdminTasks()
  assert.equal(request.options.headers['X-Admin-Token'], 'short-lived-token')
  assert.equal(request.url.includes('short-lived-token'), false)
  assert.equal(request.options.cache, 'no-store')
  admin.clearAdminToken()
  assert.equal(admin.getAdminToken(), '')
})

test('structured 409 keeps all gate reasons for the admin page', async () => {
  global.fetch = async () => ({
    ok: false,
    status: 409,
    json: async () => ({
      error: {
        message: '任务当前不能启动',
        blockedReasons: ['AI 数据治理尚未批准', '评论路由尚未就绪'],
      },
    }),
  })
  await assert.rejects(
    () => admin.runAdminTask('analyze'),
    error => error instanceof admin.AdminApiError
      && error.status === 409
      && error.blockedReasons.length === 2,
  )
})

