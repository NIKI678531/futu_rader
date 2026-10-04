const BASE = import.meta.env?.VITE_API_BASE || 'http://localhost:8008/api/v1'
const STORAGE_KEY = 'futu-radar-admin-token'

export class AdminApiError extends Error {
  constructor(status, message, blockedReasons = []) {
    super(message)
    this.name = 'AdminApiError'
    this.status = status
    this.blockedReasons = blockedReasons
  }
}

export function getAdminToken() {
  return window.sessionStorage.getItem(STORAGE_KEY) || ''
}

export function saveAdminToken(token) {
  window.sessionStorage.setItem(STORAGE_KEY, token.trim())
}

export function clearAdminToken() {
  window.sessionStorage.removeItem(STORAGE_KEY)
}

export async function adminRequest(path, { method = 'GET', token = getAdminToken() } = {}) {
  const response = await fetch(BASE + path, {
    method,
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-Admin-Token': token,
    },
  })
  let body
  try {
    body = await response.json()
  } catch {
    throw new AdminApiError(response.status, '管理接口返回了无法识别的响应')
  }
  if (!response.ok) {
    throw new AdminApiError(
      response.status,
      body?.error?.message || `管理接口请求失败（${response.status}）`,
      body?.error?.blockedReasons || [],
    )
  }
  if (!body || !('data' in body)) {
    throw new AdminApiError(response.status, '管理接口响应缺少 data')
  }
  return body.data
}

export const loadAdminSources = () => adminRequest('/admin/data-sources')
export const loadAdminTasks = () => adminRequest('/admin/tasks')
export const loadAdminRuns = () => adminRequest('/admin/job-runs?limit=50')
export const runAdminTask = task => adminRequest(`/admin/tasks/${task}/run`, { method: 'POST' })

