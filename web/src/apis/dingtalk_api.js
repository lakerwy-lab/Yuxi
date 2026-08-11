import { apiAdminGet, apiSuperAdminPost } from './base'

const buildQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  return query.toString()
}

export const dingtalkApi = {
  getDirectorySyncConfig: () => apiAdminGet('/api/dingtalk/sync-config'),
  getDirectorySyncStatus: (params = {}) => {
    const query = buildQuery(params)
    return apiAdminGet(`/api/dingtalk/sync-status${query ? `?${query}` : ''}`)
  },
  startDirectorySync: () => apiSuperAdminPost('/api/dingtalk/sync', {}),
  listDepartments: (params = {}) => {
    const query = buildQuery(params)
    return apiAdminGet(`/api/dingtalk/departments${query ? `?${query}` : ''}`)
  },
  listUsers: (params = {}) => {
    const query = buildQuery(params)
    return apiAdminGet(`/api/dingtalk/users${query ? `?${query}` : ''}`)
  }
}
