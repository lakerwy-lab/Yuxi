<script setup>
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { ArrowRight, DatabaseBackup, FileSearch } from 'lucide-vue-next'
import { migrationApi } from '@/apis/migration_api'

const sourcePath = ref('')
const targetKbId = ref('')
const loading = ref(false)
const dryRunResult = ref(null)
const importResult = ref(null)

const runDryRun = async () => {
  loading.value = true
  importResult.value = null
  try {
    dryRunResult.value = await migrationApi.migrationDryRun({
      source_path: sourcePath.value || undefined,
      target_kb_id: targetKbId.value || undefined
    })
  } catch (error) {
    message.error(error.message || '迁移预检失败')
  } finally {
    loading.value = false
  }
}

const runImport = async () => {
  if (!dryRunResult.value?.can_import || !dryRunResult.value.manifest || !targetKbId.value) return
  loading.value = true
  try {
    importResult.value = await migrationApi.migrationImport({
      target_kb_id: targetKbId.value,
      manifest: dryRunResult.value.manifest
    })
    message.success('知识库迁移导入已完成')
  } catch (error) {
    message.error(error.message || '迁移导入失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="migration-admin-view">
    <header class="migration-header">
      <div class="migration-icon"><DatabaseBackup :size="23" /></div>
      <div>
        <h2>数据迁移</h2>
        <p>将 rag-agent 的知识库快照导入当前系统。导入前必须先完成只读预检。</p>
      </div>
    </header>

    <section class="migration-card">
      <div class="migration-fields">
        <a-form layout="vertical">
          <a-form-item label="来源目录或 JSON 快照">
            <a-input v-model:value="sourcePath" placeholder="输入 rag-agent 迁移根目录下的来源路径" />
          </a-form-item>
          <a-form-item label="目标知识库 ID">
            <a-input v-model:value="targetKbId" placeholder="输入要导入的目标知识库 ID" />
          </a-form-item>
        </a-form>

        <div class="migration-actions">
          <button type="button" class="migration-button" :disabled="loading" @click="runDryRun">
            <FileSearch :size="16" />只读预检
          </button>
          <button
            type="button"
            class="migration-button is-primary"
            :disabled="loading || !dryRunResult?.can_import"
            @click="runImport"
          >
            执行导入<ArrowRight :size="16" />
          </button>
        </div>
      </div>

      <div class="migration-result">
        <a-empty v-if="!dryRunResult" description="完成预检后，这里会显示迁移检查结果" />
        <template v-else>
          <a-alert
            :type="dryRunResult.can_import ? 'success' : 'warning'"
            :message="dryRunResult.status"
            :description="(dryRunResult.reasons || []).join('；') || '预检通过，可以执行导入'"
            show-icon
          />
          <a-alert
            v-if="importResult"
            class="import-success"
            type="success"
            :message="`已导入 ${importResult.count || 0} 个文件，跳过 ${importResult.skipped?.length || 0} 个`"
            show-icon
          />
        </template>
      </div>
    </section>
  </main>
</template>

<style scoped lang="less">
.migration-admin-view { padding: 28px; }
.migration-header { display: flex; align-items: center; gap: 14px; margin-bottom: 22px; }
.migration-header h2 { margin: 0; color: var(--gray-1000); font-size: 24px; }
.migration-header p { margin: 4px 0 0; color: var(--gray-600); }
.migration-icon { display: grid; width: 46px; height: 46px; place-items: center; color: var(--main-700); background: var(--main-50); border-radius: 12px; }
.migration-card { display: grid; grid-template-columns: minmax(360px, 0.8fr) minmax(360px, 1.2fr); gap: 28px; padding: 24px; background: var(--gray-0); border: 1px solid var(--gray-150); border-radius: 14px; }
.migration-result { min-height: 220px; padding: 18px; background: var(--gray-25); border: 1px solid var(--gray-150); border-radius: 10px; }
.migration-actions { display: flex; gap: 10px; }
.migration-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; height: 36px; padding: 0 15px; color: var(--gray-800); background: var(--gray-0); border: 1px solid var(--gray-300); border-radius: 8px; cursor: pointer; }
.migration-button.is-primary { color: white; background: var(--main-700); border-color: var(--main-700); }
.migration-button:disabled { cursor: not-allowed; opacity: 0.5; }
.import-success { margin-top: 14px; }

@media (max-width: 900px) {
  .migration-admin-view { padding: 18px; }
  .migration-card { grid-template-columns: 1fr; }
}
</style>
