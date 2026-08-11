import { apiAdminPost } from './base'

export const migrationApi = {
  migrationDryRun: (data) => apiAdminPost('/api/knowledge/migrations/rag-agent/dry-run', data),
  migrationImport: (data) => apiAdminPost('/api/knowledge/migrations/rag-agent/import', data)
}
