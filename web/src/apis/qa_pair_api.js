import { apiAdminDelete, apiAdminGet, apiAdminPost, apiAdminPut } from './base'

const buildQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  return query.toString()
}

export const qaPairApi = {
  list: (kbId, params = {}) => {
    const query = buildQuery({ kb_id: kbId, ...params })
    return apiAdminGet(`/api/qa-pairs?${query}`)
  },
  create: (kbId, data) => apiAdminPost('/api/qa-pairs', { kb_id: kbId, ...data }),
  update: (id, data) => apiAdminPut(`/api/qa-pairs/${id}`, data),
  disable: (id) => apiAdminPost(`/api/qa-pairs/${id}/disable`, {}),
  remove: (id) => apiAdminDelete(`/api/qa-pairs/${id}`),
  statistics: (kbId) => apiAdminGet(`/api/qa-pairs/statistics?kb_id=${encodeURIComponent(kbId)}`)
}
