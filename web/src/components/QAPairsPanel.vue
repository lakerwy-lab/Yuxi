<template>
  <section class="qa-pairs-panel">
    <header class="qa-toolbar">
      <div>
        <h3>表单问答对</h3>
        <p>维护标准问题、相似问法与 Markdown 答案，保存后自动发布到当前知识库。</p>
      </div>
      <button
        v-if="!readonly"
        type="button"
        class="qa-button qa-button-primary"
        @click="openCreate"
      >
        <Plus :size="16" />
        新增问答对
      </button>
    </header>

    <div class="qa-filters">
      <a-input
        v-model:value="filters.query"
        allow-clear
        placeholder="搜索问题"
        class="qa-search"
        @press-enter="search"
      >
        <template #prefix><Search :size="16" /></template>
      </a-input>
      <a-select v-model:value="filters.status" class="qa-status-select" @change="search">
        <a-select-option value="">全部状态</a-select-option>
        <a-select-option value="published">已发布</a-select-option>
        <a-select-option value="pending">索引中</a-select-option>
        <a-select-option value="failed">索引失败</a-select-option>
        <a-select-option value="disabled">已停用</a-select-option>
      </a-select>
      <button type="button" class="qa-button" @click="search">
        <Search :size="16" />
        搜索
      </button>
    </div>

    <a-spin :spinning="loading">
      <div class="qa-table-wrap">
        <table class="qa-table">
          <thead>
            <tr>
              <th>问题</th>
              <th>状态</th>
              <th>更新时间</th>
              <th>更新人</th>
              <th v-if="!readonly">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <td>
                <div class="qa-question" :title="item.standard_question">
                  {{ item.standard_question }}
                </div>
                <div v-if="item.aliases?.length" class="qa-alias-count">
                  {{ item.aliases.length }} 个相似问法
                </div>
              </td>
              <td>
                <span class="qa-status" :class="`is-${statusInfo(item).tone}`">
                  <LoaderCircle
                    v-if="statusInfo(item).spinning"
                    :size="13"
                    class="qa-status-spinner"
                  />
                  {{ statusInfo(item).label }}
                </span>
                <div v-if="item.index_error" class="qa-index-error" :title="item.index_error">
                  {{ item.index_error }}
                </div>
              </td>
              <td>{{ formatTime(item.updated_at) }}</td>
              <td>{{ item.updated_by_name || item.updated_by || '-' }}</td>
              <td v-if="!readonly">
                <div class="qa-actions">
                  <button type="button" class="qa-link" @click="openEdit(item)">
                    <Pencil :size="15" />编辑
                  </button>
                  <button
                    v-if="item.enabled"
                    type="button"
                    class="qa-link"
                    @click="confirmDisable(item)"
                  >
                    <CirclePause :size="15" />停用
                  </button>
                  <button type="button" class="qa-link is-danger" @click="confirmDelete(item)">
                    <Trash2 :size="15" />删除
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <a-empty v-if="!loading && items.length === 0" description="暂无问答对" />
      </div>
    </a-spin>

    <footer v-if="total > 0" class="qa-pagination">
      <span>共 {{ total }} 条</span>
      <a-pagination
        v-model:current="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-size-options="['10', '20', '50']"
        show-size-changer
        size="small"
        @change="load"
        @show-size-change="changePageSize"
      />
    </footer>

    <a-modal
      v-model:open="modalOpen"
      :title="editingId ? '编辑问答对' : '新增问答对'"
      width="1080px"
      :mask-closable="false"
      :confirm-loading="saving"
      ok-text="保存并发布"
      cancel-text="取消"
      @ok="save"
    >
      <a-form layout="vertical" class="qa-form">
        <a-form-item label="标准问题" required>
          <a-input
            v-model:value="form.standard_question"
            :maxlength="500"
            placeholder="输入用户最常问的标准问题"
          />
        </a-form-item>

        <a-form-item label="相似问法">
          <div class="qa-aliases">
            <div v-for="(_, index) in form.aliases" :key="index" class="qa-alias-row">
              <a-input v-model:value="form.aliases[index]" placeholder="输入一种相似问法" />
              <button type="button" class="qa-icon-button" @click="removeAlias(index)">
                <X :size="16" />
              </button>
            </div>
            <button type="button" class="qa-link" @click="addAlias">
              <Plus :size="15" />添加相似问法
            </button>
          </div>
        </a-form-item>

        <a-form-item required>
          <div class="qa-answer-header">
            <div class="qa-answer-title"><span>*</span>标准答案 <small>Markdown</small></div>
            <span>使用工具栏图片按钮，或直接粘贴、拖入图片</span>
          </div>
          <MdEditor
            v-model="form.answer_markdown"
            class="qa-markdown-editor"
            :theme="themeStore.isDark ? 'dark' : 'light'"
            language="zh-CN"
            preview-theme="github"
            :max-length="20000"
            :show-code-row-number="true"
            placeholder="支持标题、列表、链接、表格和图片等 Markdown 格式"
            @on-upload-img="uploadAnswerImages"
          />
          <div class="qa-answer-hint">图片将上传至 MinIO；支持 PNG、JPG、GIF、WebP，单张不超过 5MB。</div>
        </a-form-item>
      </a-form>
    </a-modal>
  </section>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  CirclePause,
  LoaderCircle,
  Pencil,
  Plus,
  Search,
  Trash2,
  X
} from 'lucide-vue-next'
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import { qaPairApi } from '@/apis/qa_pair_api'
import { userApi } from '@/apis/user_api'
import { useThemeStore } from '@/stores/theme'

const props = defineProps({
  kbId: { type: String, required: true },
  readonly: { type: Boolean, default: false }
})

const themeStore = useThemeStore()
const loading = ref(false)
const saving = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filters = reactive({ query: '', status: '' })
const modalOpen = ref(false)
const editingId = ref(null)
const form = reactive({ standard_question: '', answer_markdown: '', aliases: [] })
let pollTimer = null

const resetPoll = () => {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = null
}

const load = async () => {
  if (!props.kbId) return
  loading.value = true
  resetPoll()
  try {
    const result = await qaPairApi.list(props.kbId, {
      query: filters.query.trim(),
      status: filters.status,
      page: page.value,
      page_size: pageSize.value
    })
    items.value = result.items || []
    total.value = Number(result.total || 0)
    if (items.value.some((item) => ['pending', 'queued', 'running'].includes(item.index_status))) {
      pollTimer = window.setTimeout(load, 3000)
    }
  } catch (error) {
    message.error(error.message || '问答对加载失败')
  } finally {
    loading.value = false
  }
}

const search = () => {
  page.value = 1
  load()
}

const changePageSize = () => {
  page.value = 1
  load()
}

const resetForm = () => {
  editingId.value = null
  form.standard_question = ''
  form.answer_markdown = ''
  form.aliases = []
}

const openCreate = () => {
  resetForm()
  modalOpen.value = true
}

const openEdit = (item) => {
  editingId.value = item.id
  form.standard_question = item.standard_question || ''
  form.answer_markdown = item.answer_markdown || ''
  form.aliases = [...(item.aliases || [])]
  modalOpen.value = true
}

const addAlias = () => {
  if (form.aliases.length < 50) form.aliases.push('')
}

const removeAlias = (index) => form.aliases.splice(index, 1)

const uploadAnswerImages = async (files, callback) => {
  const uploadedImages = []
  try {
    for (const file of files) {
      if (!file.type.startsWith('image/')) throw new Error(`${file.name} 不是图片文件`)
      if (file.size > 5 * 1024 * 1024) throw new Error(`${file.name} 超过 5MB`)

      const result = await userApi.uploadImage(file)
      const imageUrl = result.image_url || result.url
      if (!imageUrl) throw new Error(`${file.name} 上传后未返回图片地址`)
      uploadedImages.push({ url: imageUrl, alt: file.name, title: file.name })
    }
    callback(uploadedImages)
    message.success(`${uploadedImages.length} 张图片已插入答案`)
  } catch (error) {
    if (uploadedImages.length) callback(uploadedImages)
    message.error(error.message || '图片上传失败')
  }
}

const save = async () => {
  const standardQuestion = form.standard_question.trim()
  const answerMarkdown = form.answer_markdown.trim()
  if (!standardQuestion || !answerMarkdown) {
    message.warning('请填写标准问题和标准答案')
    return
  }

  saving.value = true
  try {
    const payload = {
      standard_question: standardQuestion,
      answer_markdown: answerMarkdown,
      aliases: form.aliases.map((item) => item.trim()).filter(Boolean)
    }
    if (editingId.value) await qaPairApi.update(editingId.value, payload)
    else await qaPairApi.create(props.kbId, payload)
    modalOpen.value = false
    message.success('问答对已保存，正在更新索引')
    await load()
  } catch (error) {
    message.error(error.message || '问答对保存失败')
  } finally {
    saving.value = false
  }
}

const confirmDisable = (item) => {
  Modal.confirm({
    title: '停用这个问答对？',
    content: '停用后将立即从知识库检索中移除。再次编辑并保存即可重新发布。',
    okText: '停用',
    cancelText: '取消',
    onOk: async () => {
      await qaPairApi.disable(item.id)
      message.success('问答对已停用')
      await load()
    }
  })
}

const confirmDelete = (item) => {
  Modal.confirm({
    title: '删除这个问答对？',
    content: `“${item.standard_question}”将从列表和知识库索引中删除。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      await qaPairApi.remove(item.id)
      message.success('问答对已删除')
      if (items.value.length === 1 && page.value > 1) page.value -= 1
      await load()
    }
  })
}

const statusInfo = (item) => {
  if (!item.enabled) return { label: '已停用', tone: 'muted' }
  if (item.index_status === 'failed') return { label: '索引失败', tone: 'danger' }
  if (['pending', 'queued', 'running'].includes(item.index_status)) {
    return { label: '索引中', tone: 'warning', spinning: true }
  }
  return { label: '已发布', tone: 'success' }
}

const formatTime = (value) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

watch(() => props.kbId, search)
onMounted(load)
onBeforeUnmount(resetPoll)
</script>

<style scoped lang="less">
.qa-pairs-panel {
  min-height: 460px;
  padding: 22px 24px;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 14px;
}

.qa-toolbar,
.qa-filters,
.qa-actions,
.qa-pagination,
.qa-link,
.qa-button,
.qa-preview-title {
  display: flex;
  align-items: center;
}

.qa-toolbar {
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;

  h3 { margin: 0; color: var(--gray-1000); font-size: 20px; font-weight: 650; }
  p { margin: 5px 0 0; color: var(--gray-600); font-size: 13px; }
}

.qa-button {
  justify-content: center;
  gap: 7px;
  height: 36px;
  padding: 0 15px;
  color: var(--gray-800);
  background: var(--gray-0);
  border: 1px solid var(--gray-300);
  border-radius: 8px;
  cursor: pointer;

  &:hover { border-color: var(--main-500); color: var(--main-700); }
  &.qa-button-primary { color: white; background: var(--main-700); border-color: var(--main-700); }
}

.qa-filters { gap: 12px; margin-bottom: 18px; }
.qa-search { max-width: 470px; }
.qa-status-select { width: 180px; }

.qa-table-wrap { overflow-x: auto; }
.qa-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;

  th,
  td { padding: 15px 16px; border-bottom: 1px solid var(--gray-150); text-align: left; vertical-align: middle; }
  th { color: var(--gray-800); background: var(--gray-25); font-weight: 600; }
  td { color: var(--gray-700); }
  th:first-child { width: 35%; }
  th:nth-child(2) { width: 13%; }
  th:nth-child(3) { width: 18%; }
  th:nth-child(4) { width: 14%; }
}

.qa-question { overflow: hidden; color: var(--gray-900); font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.qa-alias-count { margin-top: 4px; color: var(--gray-500); font-size: 12px; }
.qa-status { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 999px; font-size: 12px; }
.qa-status.is-success { color: var(--color-success-700); background: var(--color-success-50); }
.qa-status.is-warning { color: var(--color-warning-700); background: var(--color-warning-50); }
.qa-status.is-danger { color: var(--color-error-700); background: var(--color-error-50); }
.qa-status.is-muted { color: var(--gray-600); background: var(--gray-100); }
.qa-status-spinner { animation: qa-spin 1s linear infinite; }
.qa-index-error { max-width: 190px; margin-top: 5px; overflow: hidden; color: var(--color-error-500); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }

.qa-actions { flex-wrap: wrap; gap: 10px; }
.qa-link {
  gap: 4px;
  padding: 0;
  color: var(--main-700);
  background: none;
  border: 0;
  cursor: pointer;
  white-space: nowrap;

  &.is-danger { color: var(--color-error-500); }
}

.qa-pagination { justify-content: space-between; margin-top: 18px; color: var(--gray-600); font-size: 13px; }
.qa-form { padding-top: 8px; }
.qa-aliases { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
.qa-alias-row { display: flex; width: 100%; gap: 8px; }
.qa-icon-button { display: grid; width: 32px; flex: 0 0 32px; place-items: center; color: var(--gray-600); background: var(--gray-0); border: 1px solid var(--gray-200); border-radius: 7px; cursor: pointer; }
.qa-answer-header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.qa-answer-header > span { color: var(--gray-500); font-size: 12px; }
.qa-answer-title { color: var(--gray-800); font-size: 14px; }
.qa-answer-title span { margin-right: 5px; color: var(--color-error-500); }
.qa-answer-title small { margin-left: 5px; color: var(--gray-500); font-size: 12px; font-weight: 400; }
.qa-markdown-editor { height: 480px; overflow: hidden; border: 1px solid var(--gray-200); border-radius: 10px; }
.qa-answer-hint { margin-top: 7px; color: var(--gray-500); font-size: 12px; line-height: 1.5; }

:deep(.md-editor) { --md-color: var(--gray-900); --md-bk-color: var(--gray-0); --md-border-color: var(--gray-200); --md-scrollbar-bg-color: var(--gray-100); --md-scrollbar-thumb-color: var(--gray-400); }
:deep(.md-editor-toolbar-wrapper) { border-bottom-color: var(--gray-150); }

@keyframes qa-spin { to { transform: rotate(360deg); } }

@media (max-width: 900px) {
  .qa-toolbar { align-items: flex-start; flex-direction: column; }
  .qa-filters { align-items: stretch; flex-direction: column; }
  .qa-search { max-width: none; }
  .qa-status-select { width: 100%; }
  .qa-table { min-width: 820px; }
}
</style>
