import { API_BASE_PATH } from '../config/runtime.js'

const API_BASE = API_BASE_PATH

export class AdminApiError extends Error {
  constructor(message, { status = 0, code = 'admin_error' } = {}) {
    super(message)
    this.name = 'AdminApiError'
    this.status = status
    this.code = code
  }
}

async function adminRequest(path, { method = 'GET', body } = {}) {
  const options = {
    method,
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
    },
  }
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(body)
  }
  const response = await fetch(`${API_BASE}${path}`, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const apiError = payload?.error
    throw new AdminApiError(apiError?.message || `请求失败（${response.status}）`, {
      status: response.status,
      code: apiError?.type || apiError?.code,
    })
  }
  return payload?.data ?? payload
}

export function loadAdminPipeline(range = 'd1') {
  return adminRequest(`/admin/pipeline?range=${encodeURIComponent(range)}`)
}

export function runAdminAnalysis(range) {
  return adminRequest('/admin/analysis-runs', {
    method: 'POST',
    body: { range },
  })
}
