import { createContext, useContext } from 'react'

const AppBasePathContext = createContext('')
const AdminEnabledContext = createContext(true)

export function normalizeAppBasePath(basePath = '') {
  if (!basePath || basePath === '/') return ''
  return `/${basePath.replace(/^\/+|\/+$/g, '')}`
}

export function withAppBasePath(basePath, path = '/') {
  if (!path || !path.startsWith('/')) return path
  const base = normalizeAppBasePath(basePath)
  return path === '/' ? (base || '/') : `${base}${path}`
}

export function AppBasePathProvider({ basePath = '', adminEnabled = true, children }) {
  return (
    <AppBasePathContext.Provider value={normalizeAppBasePath(basePath)}>
      <AdminEnabledContext.Provider value={adminEnabled}>
        {children}
      </AdminEnabledContext.Provider>
    </AppBasePathContext.Provider>
  )
}

export function useAppBasePath() {
  return useContext(AppBasePathContext)
}

export function useAppPath(path) {
  return withAppBasePath(useAppBasePath(), path)
}

export function useAdminEnabled() {
  return useContext(AdminEnabledContext)
}
